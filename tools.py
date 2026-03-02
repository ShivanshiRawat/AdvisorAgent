"""
Tool definitions and implementations for the VIA agent.

Tools are grouped into three categories:
  1. Reasoning tools  — help the agent think and track state
  2. Domain tools     — compute index-choice decisions
  3. Terminal tools   — end the turn (ask_user, give_recommendation)
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, TypedDict

try:
    from ddgs import DDGS
    _DDGS_AVAILABLE = True
except ImportError:
    try:
        from duckduckgo_search import DDGS
        _DDGS_AVAILABLE = True
    except ImportError:
        _DDGS_AVAILABLE = False

from pathlib import Path
from use_case_similarity import find_similar_cases

logger = logging.getLogger(__name__)

# Path to the use case library, relative to this file
_USE_CASES_PATH = str(Path(__file__).parent / "use_cases.json")


# ---------------------------------------------------------------------------
# REASONING TOOLS
# ---------------------------------------------------------------------------

def think(reasoning: str) -> str:
    """Free-form scratchpad. The agent uses this to articulate what it knows,
    what trade-offs it's weighing, and what it's uncertain about."""
    return "Thinking recorded. Continue your analysis."


def plan(steps: list) -> str:
    """Agent outlines its approach at the start of a new request."""
    return "Plan recorded. Proceed with execution."


def update_state(session_state: dict, updates: dict) -> str:
    """Merge new facts into the agent's persistent session state."""
    for key in ["confirmed_facts", "resolved_gaps"]:
        if key in updates:
            if isinstance(session_state.get(key), dict):
                session_state[key].update(updates[key])
            elif isinstance(session_state.get(key), list):
                existing = session_state.get(key, [])
                for item in updates[key]:
                    if item not in existing:
                        existing.append(item)
                session_state[key] = existing
            else:
                session_state[key] = updates[key]

    for key in ["query_patterns", "open_gaps"]:
        if key in updates:
            existing = session_state.get(key, [])
            if isinstance(existing, list):
                for item in updates[key]:
                    if item not in existing:
                        existing.append(item)
                session_state[key] = existing
            else:
                session_state[key] = updates[key]

    if "narrative_summary" in updates:
        session_state["narrative_summary"] = updates["narrative_summary"]
    if "reasoning_so_far" in updates:
        session_state["reasoning_so_far"] = updates["reasoning_so_far"]

    return "State updated successfully."


# ---------------------------------------------------------------------------
# DOMAIN TOOLS
# ---------------------------------------------------------------------------

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

    report.append("\nINSTRUCTION: Use these conclusions in your give_recommendation call.")
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


# ---------------------------------------------------------------------------
# TERMINAL TOOLS (signal only — rendered by the UI layer)
# ---------------------------------------------------------------------------

class Option(TypedDict):
    id: str
    label: str


class Question(TypedDict):
    question: str
    anchor: str
    why_asking: str
    options: List[Option]


def ask_user(message: str, questions: List[Question]) -> str:
    """TERMINAL TOOL. Ask clarifying questions when critical information is
    missing. Every question MUST have 3–4 concrete options."""
    return "Questions presented to user."


def give_recommendation(
    summary: str,
    query_pattern_recommendations: list,
    architecture_summary: dict,
) -> str:
    """TERMINAL TOOL. Deliver the final index recommendation. Only call after
    evaluate_index_viability has returned a report."""
    return "Recommendation delivered to user."


# ---------------------------------------------------------------------------
# TOOL SCHEMAS (Gemini function-calling format)
# ---------------------------------------------------------------------------

ALL_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "Your scratchpad. Use before making decisions — dump what you know, "
                "what's missing, and what tradeoffs you're weighing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": "Your current reasoning in natural language.",
                    },
                },
                "required": ["reasoning"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plan",
            "description": "Outline your approach at the start of a new user request.",
            "parameters": {
                "type": "object",
                "properties": {
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "step": {"type": "string"},
                                "tool": {"type": "string", "description": "Tool to use"},
                                "why": {"type": "string", "description": "Why this step matters"},
                            },
                        },
                        "description": "Ordered list of steps you plan to take.",
                    },
                },
                "required": ["steps"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_state",
            "description": (
                "Record confirmed facts, query patterns, open gaps, or reasoning into "
                "persistent memory. Call this whenever you learn something new from the user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "confirmed_facts": {
                        "type": "object",
                        "description": "Key-value pairs of confirmed facts (e.g. {'vector_count': 40000000})",
                    },
                    "query_patterns": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "List of identified query patterns",
                    },
                    "open_gaps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Things you still need to know",
                    },
                    "resolved_gaps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Previously open gaps that are now resolved",
                    },
                    "narrative_summary": {
                        "type": "string",
                        "description": "Brief summary of what you understand so far",
                    },
                    "reasoning_so_far": {
                        "type": "string",
                        "description": "Your current reasoning and hypothesis",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for specific factual lookups: Couchbase parameter limits, "
                "release notes, or other verifiable facts. "
                "Do NOT use this for architectural decisions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. 'Couchbase FTS 100M vector limit')",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "use_case_search",
            "description": (
                "Search the use case library for stored patterns similar to the user's confirmed signals. "
                "Returns up to 3 matches with similarity scores, recommended indexes, and reasoning. "
                "MANDATORY: You must call this tool at least once before give_recommendation. "
                "Call this in TWO situations:\n"
                "1. EARLY — once you know search_type and scale_category — to find precedents that guide follow-up questions.\n"
                "2. LATE — after all signals are confirmed — to cross-validate your reasoning before give_recommendation.\n"
                "Use the results to understand the underlying thinking, but do NOT treat them as ground truth. "
                "Make your own decision using your intelligence. If your recommendation differs from a strong match, explain why. "
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "search_type": {
                        "type": "string",
                        "enum": ["pure_vector", "filtered_vector", "hybrid_keyword_vector", "filtered_hybrid"],
                        "description": "The fundamental query pattern.",
                    },
                    "filter_selectivity": {
                        "type": "number",
                        "description": "Fraction of data remaining after metadata filter (0.0–1.0). Use 1.0 if no filter.",
                    },
                    "scale_category": {
                        "type": "string",
                        "enum": ["small", "medium", "large", "massive", "billion_plus"],
                        "description": "Dataset size tier: small=<1M, medium=1M-50M, large=50M-100M, massive=100M-1B, billion_plus=>1B.",
                    },
                    "latency_ms": {
                        "type": "integer",
                        "description": "Latency SLA in milliseconds.",
                    },
                    "scale_change": {
                        "type": "boolean",
                        "description": "True if dataset is projected to grow from <100M to >100M vectors.",
                    },
                },
                "required": ["search_type", "filter_selectivity", "scale_category", "latency_ms", "scale_change"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_index_viability",
            "description": (
                "MANDATORY before give_recommendation. Evaluates which index types are "
                "viable given scale, filter selectivity, and keyword search requirements."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "projected_vector_count": {
                        "type": "string",
                        "description": "3-year projected vector count (e.g. '50000000' or '50M').",
                    },
                    "filter_selectivity_pct": {
                        "type": "string",
                        "description": (
                            "Percentage of data REMAINING after filters (e.g. '15' means 15% remains). "
                            "Use '100' if no filters are applied."
                        ),
                    },
                    "requires_keyword_search": {
                        "type": "string",
                        "description": "'true' or 'false'. Does the user need fuzzy matching or BM25 keyword search?",
                    },
                },
                "required": ["projected_vector_count", "filter_selectivity_pct", "requires_keyword_search"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_indexes",
            "description": (
                "Side-by-side tradeoff analysis of two index strategies. "
                "Use when two options are genuinely close to explain the difference clearly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "option_a_type": {
                        "type": "string",
                        "enum": ["Hyperscale", "Composite", "Search", "Hybrid"],
                    },
                    "option_b_type": {
                        "type": "string",
                        "enum": ["Hyperscale", "Composite", "Search", "Hybrid"],
                    },
                    "vector_count": {"type": "integer"},
                    "has_hard_filter": {
                        "type": "boolean",
                        "description": "Is there a filter that is ALWAYS applied?",
                    },
                    "filter_selectivity_pct": {
                        "type": "number",
                        "description": "Percentage of corpus REMAINING after filter",
                    },
                    "latency_target_ms": {
                        "type": "integer",
                        "description": "Optional p95 latency target in ms",
                    },
                },
                "required": ["option_a_type", "option_b_type", "vector_count", "has_hard_filter"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "TERMINAL TOOL. Ask clarifying questions when critical information is missing. "
                "Every question MUST have 3–4 concrete options."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Context sentence to preface the questions.",
                    },
                    "questions": {
                        "type": "array",
                        "description": "List of questions to ask the user.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string"},
                                "anchor": {
                                    "type": "string",
                                    "description": "Reference the user's words, e.g. 'You mentioned category browsing...'",
                                },
                                "why_asking": {
                                    "type": "string",
                                    "description": "Why this answer changes the recommendation.",
                                },
                                "options": {
                                    "type": "array",
                                    "description": "3–4 concrete options. 'Other / Type here' is added automatically.",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "label": {"type": "string"},
                                        },
                                        "required": ["id", "label"],
                                    },
                                },
                            },
                            "required": ["question", "anchor", "why_asking", "options"],
                        },
                    },
                },
                "required": ["message", "questions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "give_recommendation",
            "description": (
                "TERMINAL TOOL. Deliver the final index recommendation. "
                "Only call after evaluate_index_viability has returned a verdict."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "query_pattern_recommendations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "query_pattern": {"type": "string"},
                                "recommended_index": {"type": "string"},
                                "reasoning": {
                                    "type": "string",
                                    "description": "Physical reasoning: scale, filter mechanics, RAM constraints.",
                                },
                                "eliminated_alternatives": {
                                    "type": "object",
                                    "additionalProperties": {
                                        "type": "string",
                                        "description": "Why this index was eliminated.",
                                    },
                                },
                                "caveats": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "What would change this recommendation.",
                                },
                            },
                            "required": [
                                "query_pattern",
                                "recommended_index",
                                "reasoning",
                                "eliminated_alternatives",
                            ],
                        },
                    },
                    "architecture_summary": {
                        "type": "object",
                        "properties": {
                            "total_indexes": {"type": "integer"},
                            "index_types_used": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "shared_indexes": {"type": "string"},
                            "operational_notes": {"type": "string"},
                        },
                    },
                },
                "required": ["summary", "query_pattern_recommendations", "architecture_summary"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# TOOL DISPATCHER
# ---------------------------------------------------------------------------

def execute_tool(tool_name: str, args: dict, session_state: Optional[dict] = None) -> Any:
    """Execute a non-terminal tool and return its result.
    Terminal tools (ask_user, give_recommendation) are handled by the agent loop.
    """
    try:
        if tool_name == "think":
            return think(args.get("reasoning", ""))

        elif tool_name == "plan":
            return plan(args.get("steps", []))

        elif tool_name == "update_state":
            if session_state is not None:
                return update_state(session_state, args)
            return "Error: No session state available."

        elif tool_name == "web_search":
            return web_search(args.get("query", ""))

        elif tool_name == "use_case_search":
            return use_case_search(
                search_type=args.get("search_type", "filtered_vector"),
                filter_selectivity=float(args.get("filter_selectivity", 1.0)),
                scale_category=args.get("scale_category", "medium"),
                latency_ms=int(args.get("latency_ms", 100)),
                scale_change=bool(args.get("scale_change", False)),
            )

        elif tool_name == "evaluate_index_viability":
            # Parse loose LLM string outputs like "50M", "15-20%", "true"
            vc_raw = str(args.get("projected_vector_count", "0")).upper()
            vc_multiplier = 1_000_000 if "M" in vc_raw else (1_000 if "K" in vc_raw else 1)
            vc_clean = re.sub(r"[^\d.]", "", vc_raw)
            try:
                vc = int(float(vc_clean) * vc_multiplier) if vc_clean else 0
            except Exception:
                vc = 0

            sel_raw = str(args.get("filter_selectivity_pct", "100.0"))
            sel_matches = re.findall(r"\d+\.?\d*", sel_raw)
            try:
                if len(sel_matches) > 1:
                    sel = sum(float(x) for x in sel_matches) / len(sel_matches)
                elif len(sel_matches) == 1:
                    sel = float(sel_matches[0])
                else:
                    sel = 100.0
            except Exception:
                sel = 100.0

            kw_raw = str(args.get("requires_keyword_search", "false")).lower().strip()
            kw = kw_raw in ("true", "yes", "y", "1")

            return evaluate_index_viability(vc, sel, kw)

        elif tool_name == "compare_indexes":
            return compare_indexes(
                option_a_type=args.get("option_a_type", ""),
                option_b_type=args.get("option_b_type", ""),
                vector_count=int(args.get("vector_count", 0)),
                has_hard_filter=bool(args.get("has_hard_filter", False)),
                filter_selectivity_pct=args.get("filter_selectivity_pct"),
                latency_target_ms=args.get("latency_target_ms"),
            )

        else:
            return f"Unknown tool: '{tool_name}'."

    except Exception as e:
        logger.error(f"Tool execution error [{tool_name}]: {e}")
        return json.dumps({
            "status": "error",
            "error": str(e),
            "suggestion": "Try with different parameters or call think() to reassess.",
        })
