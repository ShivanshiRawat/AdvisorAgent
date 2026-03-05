"""
tools/domain.py

Domain tools — compute index-choice decisions.
These tools perform deterministic business logic and return structured results.
"""

import logging
import re
from pathlib import Path
from typing import Optional

try:
    from ddgs import DDGS
    _DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        _DDGS_AVAILABLE = True
    except ImportError:
        _DDGS_AVAILABLE = False

from similarity import find_similar_cases

logger = logging.getLogger(__name__)

# Path to the use case library, relative to the project root
_USE_CASES_PATH = str(Path(__file__).parent.parent / "data" / "use_cases.json")


def web_search(query: str) -> str:
    """Search the web for Couchbase parameter limits, release notes, or other factual lookups."""
    if not _DDGS_AVAILABLE:
        return "Web search unavailable."
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(f"Couchbase {query}", max_results=3))
            if not results:
                results = list(ddgs.text(query, max_results=3))
            if not results:
                return "No results found."
            return "\n\n".join(
                f"**{r.get('title','')}**\n{r.get('body','')}" for r in results
            )
    except Exception as e:
        return f"Search error: {str(e)}"


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
