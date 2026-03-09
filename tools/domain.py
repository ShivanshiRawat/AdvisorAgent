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


def web_search(query: str, gemini_client=None, gemini_model: str = None) -> Tuple[str, list]:
    """
    Search the web using Gemini's built-in Google Search grounding.
    Returns a tuple of (text_result, source_urls).

    NOTE: Gemini does not allow google_search and custom FunctionDeclarations
    in the same API call. This function makes a separate, dedicated Gemini
    call with only the google_search tool enabled.
    """
    if gemini_client is None:
        return "Web search unavailable: no Gemini client provided.", []

    try:
        from google.genai import types as genai_types

        grounding_tool = genai_types.Tool(google_search=genai_types.GoogleSearch())

        search_query = f"Couchbase {query}" if "couchbase" not in query.lower() else query

        response = gemini_client.models.generate_content(
            model=gemini_model or "gemini-2.5-flash",
            contents=search_query,
            config=genai_types.GenerateContentConfig(
                tools=[grounding_tool],
                temperature=0.1,
            ),
        )

        text_result = response.text or "No results found."

        # Extract source URLs from grounding metadata
        source_urls = []
        try:
            grounding_meta = response.candidates[0].grounding_metadata
            if grounding_meta and grounding_meta.grounding_chunks:
                for chunk in grounding_meta.grounding_chunks:
                    if chunk.web and chunk.web.uri:
                        source_urls.append({
                            "url": chunk.web.uri,
                            "title": chunk.web.title or chunk.web.uri,
                        })
        except Exception as meta_err:
            logger.warning(f"Could not extract grounding metadata: {meta_err}")

        return text_result, source_urls

    except Exception as e:
        logger.error(f"Google Search grounding error: {e}")
        return f"Search error: {str(e)}", []



def evaluate_index_viability(
    projected_vector_count: int,
    filter_selectivity_pct: float,
    requires_keyword_search: bool,
) -> str:
    """Evaluate which index types are viable based on scale, selectivity, and
    keyword search requirements. Returns a deterministic verdict report."""
    report = ["--- VIABILITY REPORT ---"]
    report.append(
        f"Input: {projected_vector_count:,} vectors (3-year projection), "
        f"{filter_selectivity_pct}% data remaining after filter, "
        f"keyword search required: {requires_keyword_search}"
    )

    # Scale + keyword search rule
    if requires_keyword_search:
        if projected_vector_count > 100_000_000:
            report.append("❌ Search Vector Index (FTS): ELIMINATED — scale exceeds the 100M memory-mapping ceiling.")
            report.append("✅ Hybrid Architecture (HVI + FTS): REQUIRED — HVI handles vectors at scale, FTS handles keywords.")
        else:
            report.append("✅ Search Vector Index (FTS): VIABLE — scale is safely under the 100M ceiling.")
            report.append("⚠️  Hybrid Architecture (HVI + FTS): OVERKILL at this scale — unified FTS is simpler.")
    else:
        report.append("ℹ️  Search Vector Index / Hybrid: NOT APPLICABLE (no keyword search required).")

    # Selectivity rule
    if filter_selectivity_pct < 20:
        recommendation = (
            f"✅ Composite Vector Index (CVI): RECOMMENDED — filter prunes to {filter_selectivity_pct}% of corpus. "
            f"GSI eliminates {100 - filter_selectivity_pct:.0f}% before ANN. "
        )
        if projected_vector_count >= 1_000_000_000:
            recommendation += "⚠️  CAVEAT: At billion-scale, ensure the full index fits in RAM or consider HVI."
        report.append(recommendation)
        report.append(
            "⚠️  Hyperscale Vector Index (HVI): SUBOPTIMAL at this selectivity — "
            "it scans the full graph when the filter could dramatically shrink the search space."
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

    Templates use <placeholder> notation for fields the user must substitute.
    The LLM should present these queries and offer to personalise them once the
    user shares their data model (bucket / scope / collection names, field names,
    vector dimensions, and similarity metric).

    Supported index_type values: "HVI", "CVI", "FTS", "Hybrid"
    """
    index_type = index_type.strip().upper()

    if index_type == "HVI":
        return {
            "index_type": "Hyperscale Vector Index (HVI)",
            "ddl": """\
CREATE VECTOR INDEX `index_name` 
ON `bucket`.`scope`.`collection`(`vector_field` VECTOR)
-- Optional: Fields to be co-located in the index for covering queries
INCLUDE (`scalar_field_1`, `scalar_field_2`, `metadata_field`)
-- Optional: Partitioning for high-scale distribution
PARTITION BY HASH(meta().id) 
WITH {
  "dimension": <integer_dimensions>,
  "similarity": "<COSINE | DOT | L2 | L2_SQUARED>",
  "description": "<IVF_centroids,SQ_bits | IVF_centroids,PQ_subquantizers>",
  "train_list": <integer_sample_size>,
  "persist_full_vector": <true | false>,
  "scan_nprobes": <integer_cells_to_scan>,
  "num_replica": <integer_replicas>,
  "defer_build": <true | false>
};""",
            "dml": """\
SELECT 
    `scalar_field_1`, 
    `metadata_field`,
    APPROX_VECTOR_DISTANCE(`vector_field`, <search_vector>, "<similarity_metric>") AS score
FROM `bucket`.`scope`.`collection`
WHERE `scalar_field_1` = <filter_value> -- Only if field is in INCLUDE
ORDER BY APPROX_VECTOR_DISTANCE(`vector_field`, <search_vector>, "<similarity_metric>")
LIMIT <top_k>;""",
            "notes": (
                "HVI is disk-centric (DiskANN/Vamana). The PARTITION BY clause is optional "
                "but recommended for horizontal scaling across nodes. "
                "Set persist_full_vector=true only if you want reranking (improves recall at latency cost). "
                "nprobes controls the recall/latency trade-off at query time — higher = better recall, more latency."
            ),
        }

    elif index_type == "CVI":
        return {
            "index_type": "Composite Vector Index (CVI)",
            "ddl": """\
CREATE INDEX `<index_name>`
ON `<bucket>`.`<scope>`.`<collection>`(
    `<scalar_field_1>`,           -- leading filter field (most selective first)
    `<scalar_field_2>`,           -- additional filter fields as needed
    `<vector_field>` VECTOR
)
-- Optional: partition on the most selective scalar field
PARTITION BY HASH(`<scalar_field_1>`)
WITH {
  "dimension":   <integer — must match your embedding model output, e.g. 1536>,
  "similarity":  "<COSINE | DOT | L2 | L2_SQUARED>",
  "description": "<IVF_<centroids>,SQ8>",
  "train_list":  <integer — sample size for quantisation training, e.g. 100000>,
  "num_replica": <integer — index replicas for HA, e.g. 1>,
  "defer_build": <true | false>
};""",
            "dml": """\
SELECT
    meta().id,
    `<returned_scalar_fields>`,
    APPROX_VECTOR_DISTANCE(
        `<vector_field>`,
        <search_vector_array>,
        "<COSINE | DOT | L2 | L2_SQUARED>"
    ) AS distance
FROM `<bucket>`.`<scope>`.`<collection>`
WHERE `<scalar_field_1>` = <value>
  AND `<scalar_field_2>` > <value>   -- match the leading index key order
ORDER BY APPROX_VECTOR_DISTANCE(
    `<vector_field>`,
    <search_vector_array>,
    "<COSINE | DOT | L2 | L2_SQUARED>"
)
LIMIT <top_k>;""",
            "notes": (
                "CVI uses GSI Filter-First logic: scalar WHERE conditions are applied at the index "
                "level before the ANN step. For maximum benefit the filter must be <20% selective "
                "(i.e., less than 20% of the corpus is eligible for vector search). "
                "Order scalar fields in the CREATE INDEX key list with the most selective field first. "
                "The full index must fit in RAM — monitor memory usage as the corpus grows."
            ),
        }

    elif index_type == "FTS":
        return {
            "index_type": "Search Vector Index (FTS)",
            "ddl": (
                "Search Vector Indexes are created via the Couchbase UI (Search Service → Create Index) "
                "or the REST API — not via SQL++. Key settings to configure in the UI:\n"
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
SELECT meta().id, `<scalar_fields>`, score() AS relevance_score
FROM `<bucket>`.`<scope>`.`<collection>`
WHERE SEARCH(`<collection>`, {
    "query": {
        "match": "<keyword_query_string>",
        "field":  "<text_field>"
    },
    "knn": [{
        "field":  "<vector_field>",
        "vector": <search_vector_array>,
        "k":      <top_k>
    }]
})
LIMIT <top_k>;""",
            "notes": (
                "FTS Search Vector Index is strictly limited to ~100M vectors due to memory-mapping. "
                "Use the SEARCH() function in SQL++ to issue hybrid (keyword + vector) queries. "
                "For pure vector search without keyword scoring, the APPROX_VECTOR_DISTANCE "
                "on a standard index is more efficient."
            ),
        }

    else:
        return {
            "error": f"Unknown index_type '{index_type}'. Valid values: HVI, CVI, FTS."
        }
