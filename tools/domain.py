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
                f"Filter prunes to {filter_selectivity_pct}% of corpus — highly selective. "
                f"Composite physically benefits: GSI reduces ANN scope by {100 - filter_selectivity_pct:.0f}%. "
                f"BUT: verify the full index fits in RAM before committing."
            )
        else:
            analysis["analysis"]["filter_verdict"] = (
                f"Filter retains {filter_selectivity_pct}% of corpus — low selectivity. "
                f"Composite's GSI scan cost is not justified by the ANN reduction."
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
