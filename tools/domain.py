"""
tools/domain.py

Domain tools — compute index-choice decisions.
These tools perform deterministic business logic and return structured results.
"""

import logging
import re
from pathlib import Path
from typing import Optional, Tuple

from similarity import find_similar_cases

logger = logging.getLogger(__name__)

# Path to the use case library, relative to the project root
_USE_CASES_PATH = str(Path(__file__).parent.parent / "data" / "use_cases.json")


def web_search(query: str, provider=None) -> Tuple[str, list]:
    """Search the web via the active LLM provider.

    Returns a tuple of (text_result, source_urls).
    The concrete implementation (e.g. Gemini Google Search grounding,
    OpenAI knowledge fallback) lives inside each provider.
    """
    if provider is None:
        return "Web search unavailable: no LLM provider.", []
    return provider.web_search(query)



def evaluate_index_viability(
    current_vector_count: int,
    filter_selectivity_pct: float,
    requires_keyword_search: bool,
    projected_vector_count: int = 0,
) -> str:
    """Evaluate which index types are viable based on scale, selectivity, and
    keyword search requirements. Returns a deterministic verdict report.

    CRITICAL: Routing is driven by current_vector_count ONLY.
    projected_vector_count (if provided) generates a forward-looking NOTE, never a routing change.

    Guard: If current_vector_count seems too large and projected_vector_count is small or zero,
    assume they were swapped and swap them back.
    """
    # Safety guard: if current is much larger than projected, they may be swapped
    if (projected_vector_count > 0 and current_vector_count > 500_000_000 and 
        projected_vector_count < current_vector_count / 2):
        # Likely swapped — correct it
        current_vector_count, projected_vector_count = projected_vector_count, current_vector_count

    report = ["--- VIABILITY REPORT ---"]
    report.append(
        f"Input: {current_vector_count:,} vectors (CURRENT), "
        + (f"{projected_vector_count:,} vectors (3-year projection), " if projected_vector_count else "")
        + f"{filter_selectivity_pct}% data remaining after filter, "
        f"keyword search required: {requires_keyword_search}"
    )

    # Scale + keyword search rule — driven by CURRENT scale only
    if requires_keyword_search:
        if current_vector_count > 100_000_000:
            report.append("❌ Search Vector Index (FTS): ELIMINATED — current scale exceeds the 100M memory-mapping ceiling.")
            report.append("✅ Hybrid Architecture (HVI + FTS): REQUIRED — HVI handles vectors at scale, FTS handles keywords.")
        else:
            report.append("✅ Search Vector Index (FTS): VIABLE — current scale is safely under the 100M ceiling.")
            report.append("⚠️  Hybrid Architecture (HVI + FTS): OVERKILL at this scale — unified FTS is simpler.")
            # Add growth note if projected scale crosses the threshold
            if projected_vector_count and projected_vector_count > 100_000_000:
                report.append(
                    f"📌 FORWARD-LOOKING NOTE: At {projected_vector_count:,} vectors (your projection), "
                    "FTS would exceed its 100M ceiling. This is a future consideration only — "
                    "FTS is optimal today. When/if you approach that threshold in 2–3 years, "
                    "migrate to HVI (or Hybrid HVI+FTS for keyword search). This is a service-boundary change, "
                    "so allow lead time. No action needed now."
                )
    else:
        report.append("ℹ️  Search Vector Index / Hybrid: NOT APPLICABLE (no keyword search required).")

    # Selectivity rule — also driven by current scale
    if filter_selectivity_pct < 20:
        recommendation = (
            f"✅ Composite Vector Index (CVI): RECOMMENDED — filter prunes to {filter_selectivity_pct}% of corpus. "
            f"GSI eliminates {100 - filter_selectivity_pct:.0f}% before ANN. "
        )
        if current_vector_count >= 1_000_000_000:
            recommendation += "⚠️  CAVEAT: At billion-scale, ensure the full index fits in RAM or consider HVI."
        report.append(recommendation)
        report.append(
            "⚠️  Hyperscale Vector Index (HVI): SUBOPTIMAL at this selectivity — "
            "it scans the full graph when the filter could dramatically shrink the search space."
        )
        # CVI → HVI growth note
        if projected_vector_count and projected_vector_count >= 1_000_000_000 and current_vector_count < 1_000_000_000:
            report.append(
                f"📌 FORWARD-LOOKING NOTE: At {projected_vector_count:,} vectors (your projection), "
                "CVI's RAM cost may become prohibitive — this is a future consideration only. "
                "CVI is optimal today for your selectivity. When/if you approach billion-scale in 2–3 years, "
                "consider migrating to HVI (same Index Service, similar DDL, same query syntax). "
                "No action needed now."
            )
    else:
        report.append(
            f"❌ Composite Vector Index (CVI): ELIMINATED — filter retains {filter_selectivity_pct}% of corpus. "
            "The GSI pre-filter provides minimal reduction, wasting RAM for little gain."
        )
        report.append(
            "✅ Hyperscale Vector Index (HVI): VIABLE — designed for broad, low-selectivity searches "
            "at massive scale with a 2% DGM disk-centric model."
        )
    return "\n".join(report)


def compare_indexes(
    option_a_type: str,
    option_b_type: str,
    vector_count: int,
    has_hard_filter: bool,
    filter_selectivity_pct: Optional[float] = None,
    latency_target_ms: Optional[int] = None,
) -> dict:
    """Side-by-side tradeoff analysis of two index strategies."""
    analysis = {
        "option_a": option_a_type,
        "option_b": option_b_type,
        "vector_count": f"{vector_count:,}",
        "analysis": {},
    }

    a_lower = option_a_type.lower()
    b_lower = option_b_type.lower()

    scale_notes = {}
    for idx_type in [a_lower, b_lower]:
        if idx_type == "search":
            if vector_count > 100_000_000:
                scale_notes[idx_type] = "EXCEEDS scale limit (<100M). Not viable."
            elif vector_count > 50_000_000:
                scale_notes[idx_type] = "Approaching scale limit. Monitor growth trajectory."
            else:
                scale_notes[idx_type] = "Within scale limits."
        elif idx_type in ("hyperscale", "composite"):
            scale_notes[idx_type] = "Designed for this scale (supports 100M–1B+)."
    analysis["analysis"]["scale"] = scale_notes

    if has_hard_filter and filter_selectivity_pct is not None:
        if filter_selectivity_pct < 20:
            analysis["analysis"]["filter_verdict"] = (
                f"Filter is {filter_selectivity_pct}% selective — only {filter_selectivity_pct}% of the corpus "
                f"is eligible for vector search, with the remaining {100 - filter_selectivity_pct:.0f}% filtered out. "
                f"Composite physically benefits: GSI reduces ANN scope by {100 - filter_selectivity_pct:.0f}%. "
                f"BUT: verify the full index fits in RAM before committing."
            )
        else:
            analysis["analysis"]["filter_verdict"] = (
                f"Filter is {filter_selectivity_pct}% selective — {filter_selectivity_pct}% of the corpus "
                f"is eligible for vector search, with the remaining {100 - filter_selectivity_pct:.0f}% filtered out. "
                f"Composite's GSI pre-filter provides minimal reduction at this selectivity, wasting RAM for little gain."
            )
    elif not has_hard_filter:
        analysis["analysis"]["filter_verdict"] = (
            "No hard filter. Composite's GSI would run without consistent benefit. "
            "Hyperscale handles optional/post-filters with lower overhead."
        )

    migration_types = {a_lower, b_lower}
    if "search" in migration_types and migration_types & {"hyperscale", "composite"}:
        analysis["analysis"]["migration_risk"] = (
            "Switching between Search and Hyperscale/Composite is a major re-architecture "
            "(API change from FTS syntax to SQL++). Factor growth trajectory carefully."
        )
    elif migration_types == {"hyperscale", "composite"}:
        analysis["analysis"]["migration_risk"] = (
            "Switching between Hyperscale and Composite is relatively low-risk — "
            "same vector payload format, both IVF-based. Main difference is filter pre-processing."
        )

    return analysis


def use_case_search(
    search_type: str,
    filter_selectivity: float,
    scale_category: str,
    latency_ms: int,
    scale_change: bool = False,
) -> list:
    """Search the use case library for stored patterns similar to the user's signals."""
    keyword_required = search_type in ("hybrid_keyword_vector", "filtered_hybrid")
    user_signals = {
        "search_type":        search_type,
        "filter_selectivity": filter_selectivity,
        "scale_category":     scale_category,
        "keyword_required":   keyword_required,
        "latency_ms":         latency_ms,
        "scale_change":       scale_change,
    }
    return find_similar_cases(user_signals, _USE_CASES_PATH, top_n=3)


def get_index_queries(index_type: str) -> dict:
    """Return DDL (CREATE INDEX) and DML (SELECT) query templates for the given index type.

    Templates use <placeholder> notation for ALL values — both user-specific
    fields (bucket, scope, collection, field names) and tunable parameters
    (dimension, similarity, nlist, etc.).

    The LLM is responsible for substituting known values from baseline
    configuration results, default parameters, or conversation context.

    Supported index_type values: "HVI", "CVI", "FTS"
    """
    index_type = index_type.strip().upper()

    if index_type == "HVI":
        return {
            "index_type": "Hyperscale Vector Index (HVI)",
            "ddl": """\
CREATE VECTOR INDEX `<your_index_name>`
ON `<your_bucket>`.`<your_scope>`.`<your_collection>`(`<your_vector_field>` VECTOR)
-- Optional: Fields to co-locate in the index for covering queries
INCLUDE (`<scalar_field_1>`, `<scalar_field_2>`)
-- Optional: Partitioning for high-scale distribution
PARTITION BY HASH(meta().id)
WITH {
  "dimension": <integer_dimensions>,
  "similarity": "<COSINE | DOT | L2 | L2_SQUARED>",
  "description": "IVF<nlist>,<SQ4 | SQ6 | SQ8 | PQ<subquantizers>x<bits>>",
  "train_list": <integer_sample_size>,
  "persist_full_vector": <true | false>,
  "scan_nprobes": <integer_cells_to_scan>,
  "num_replica": <integer_replicas>,
  "defer_build": false
};""",
            "dml": """\
SELECT
    `<your_scalar_fields>`,
    APPROX_VECTOR_DISTANCE(
        `<your_vector_field>`,
        <your_search_vector>,
        "<similarity_metric>",
        <nProbes>,        -- optional: number of centroids to scan (default 1)
        <true | false>    -- optional: enable reranking (requires persist_full_vector=true)
    ) AS score
FROM `<your_bucket>`.`<your_scope>`.`<your_collection>`
WHERE `<scalar_field>` = <filter_value>  -- Only if field is in INCLUDE
ORDER BY APPROX_VECTOR_DISTANCE(
    `<your_vector_field>`,
    <your_search_vector>,
    "<similarity_metric>",
    <nProbes>,
    <true | false>
)
LIMIT <top_k>;""",
            "user_must_fill": [
                "<your_index_name>", "<your_bucket>", "<your_scope>",
                "<your_collection>", "<your_vector_field>", "<scalar_field_1>",
                "<scalar_field_2>", "<your_scalar_fields>", "<your_search_vector>",
                "<filter_value>"
            ],
            "tool_can_fill": [
                "<integer_dimensions>", "<COSINE | DOT | L2 | L2_SQUARED>",
                "IVF<nlist>,<SQ4|SQ6|SQ8|PQNxB>", "<integer_sample_size>",
                "<true | false> (persist_full_vector)", "<integer_cells_to_scan>",
                "<integer_replicas>", "<similarity_metric>", "<nProbes>", "<top_k>"
            ],
            "notes": (
                "HVI is disk-centric (DiskANN/Vamana). PARTITION BY is optional "
                "but recommended for horizontal scaling. "
                "persist_full_vector=true enables reranking (better recall, more latency). "
                "scan_nprobes controls recall/latency trade-off at query time. "
                "description format: IVF<nlist>,<quantization> where quantization is "
                "SQ4, SQ6, SQ8, or PQ<subquantizers>x<bits> (e.g. IVF256,SQ8 or IVF512,PQ32x8). "
                "Omit nlist for auto (vectors/1000). "
                "nProbes and reranking are optional 4th/5th args to APPROX_VECTOR_DISTANCE."
            ),
        }

    elif index_type == "CVI":
        return {
            "index_type": "Composite Vector Index (CVI)",
            "ddl": """\
CREATE INDEX `<your_index_name>`
ON `<your_bucket>`.`<your_scope>`.`<your_collection>`(
    `<your_scalar_field_1>`,           -- leading filter field (most selective first)
    `<your_scalar_field_2>`,           -- additional filter fields as needed
    `<your_vector_field>` VECTOR
)
-- Optional: partition on the most selective scalar field
PARTITION BY HASH(`<your_scalar_field_1>`)
WITH {
  "dimension":   <integer_dimensions>,
  "similarity":  "<COSINE | DOT | L2 | L2_SQUARED>",
  "description": "IVF<nlist>,<SQ4 | SQ6 | SQ8 | PQ<subquantizers>x<bits>>",
  "train_list":  <integer_sample_size>,
  "num_replica": <integer_replicas>,
  "defer_build": false
};""",
            "dml": """\
SELECT
    meta().id,
    `<your_scalar_fields>`,
    APPROX_VECTOR_DISTANCE(
        `<your_vector_field>`,
        <your_search_vector>,
        "<COSINE | DOT | L2 | L2_SQUARED>"
    ) AS distance
FROM `<your_bucket>`.`<your_scope>`.`<your_collection>`
WHERE `<your_scalar_field_1>` = <value>
  AND `<your_scalar_field_2>` > <value>   -- match the leading index key order
ORDER BY APPROX_VECTOR_DISTANCE(
    `<your_vector_field>`,
    <your_search_vector>,
    "<COSINE | DOT | L2 | L2_SQUARED>"
)
LIMIT <top_k>;""",
            "user_must_fill": [
                "<your_index_name>", "<your_bucket>", "<your_scope>",
                "<your_collection>", "<your_vector_field>",
                "<your_scalar_field_1>", "<your_scalar_field_2>",
                "<your_scalar_fields>", "<your_search_vector>", "<value>"
            ],
            "tool_can_fill": [
                "<integer_dimensions>", "<COSINE | DOT | L2 | L2_SQUARED>",
                "IVF<nlist>,<SQ4|SQ6|SQ8|PQNxB>", "<integer_sample_size>",
                "<integer_replicas>", "<top_k>"
            ],
            "notes": (
                "CVI uses GSI Filter-First logic: scalar WHERE conditions are applied at "
                "the index level before the ANN step. For maximum benefit the filter must "
                "be <20% selective. Order scalar fields with the most selective first. "
                "The full index must fit in RAM. "
                "description format: IVF<nlist>,<quantization> where quantization is "
                "SQ4, SQ6, SQ8, or PQ<subquantizers>x<bits> (e.g. IVF4096,SQ8 or IVF,PQ32x8). "
                "Omit nlist for auto (vectors/1000)."
            ),
        }

    elif index_type == "FTS":
        return {
            "index_type": "Search Vector Index (FTS)",
            "ddl": (
                "Search Vector Indexes are created via the Couchbase UI "
                "(Search Service → Create Index) or the REST API — not via SQL++.\n"
                "Key settings to configure in the UI:\n"
                "  • Index name\n"
                "  • Bucket / Scope / Collection\n"
                "  • Vector field name and dimension\n"
                "  • Similarity metric (COSINE | DOT | L2)\n"
                "  • Optimized For: latency | recall | memory-efficient\n"
                "  • Scoring Model: BM25 (recommended) | TF-IDF\n"
                "  • Number of replicas"
            ),
            "dml": """\
-- Hybrid keyword + vector search via FTS
SELECT meta().id, `<your_scalar_fields>`, score() AS relevance_score
FROM `<your_bucket>`.`<your_scope>`.`<your_collection>`
WHERE SEARCH(`<your_collection>`, {
    "query": {
        "match": "<your_keyword_query>",
        "field":  "<your_text_field>"
    },
    "knn": [{
        "field":  "<your_vector_field>",
        "vector": <your_search_vector>,
        "k":      <top_k>
    }]
})
LIMIT <top_k>;""",
            "user_must_fill": [
                "<your_bucket>", "<your_scope>", "<your_collection>",
                "<your_vector_field>", "<your_text_field>",
                "<your_scalar_fields>", "<your_search_vector>",
                "<your_keyword_query>"
            ],
            "tool_can_fill": ["<top_k>"],
            "notes": (
                "FTS indexes are NOT created via SQL++. Use the Couchbase Web Console. "
                "The SELECT query uses the SEARCH() function for hybrid keyword + vector queries. "
                "FTS is limited to ~100M vectors due to memory-mapping constraints."
            ),
        }

    else:
        return {
            "error": f"Unknown index_type '{index_type}'. Valid values: HVI, CVI, FTS."
        }


def get_default_parameters(index_type: str, vector_count: int) -> dict:
    """Calculate and return default parameter values for the recommended index.
    
    Based on the dataset scale (vector_count), this function calculates optimal
    default values for nlist and train_list, and returns structured parameters
    for both index time and query time.
    """
    index_type = index_type.strip().upper()

    if index_type in ("HVI", "CVI"):
        nlist = max(1, vector_count // 1000)
        
        if vector_count < 10000:
            train_list = vector_count
        else:
            train_list = min(1000000, max(vector_count // 10, 10 * nlist))

        params = {
            "index_time_parameters": {
                "dimension": "must match embedding model output",
                "similarity": "L2_SQUARED",
                "quantization": "SQ8",
                "nlist": nlist,
                "train_list": train_list,
                "num_replica": 0,
                "persist_full_vector": True
            },
            "query_time_parameters": {
                "nProbe": 1,
                "reranking": False,
                "limit": 100,
                "similarity_query_override": "uses index default"
            }
        }
        
        if index_type == "HVI":
            params["query_time_parameters"]["topNScan"] = "depends on query limit range (typically 40–300)"
            
        return params

    elif index_type == "FTS":
        return {
            "message": "For Search Vector Index (FTS), please continue with the Couchbase UI for parameter configuration."
        }
    
    else:
        return {
            "error": f"Unknown index_type '{index_type}'. Valid values: HVI, CVI, FTS."
        }
