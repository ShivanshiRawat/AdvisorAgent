"""
Tool definitions and implementations for the VIA agent.
Each tool serves a specific purpose in the SE reasoning loop.

Google Search is handled natively by Gemini grounding — no web_search tool needed here.
"""

import json
import logging
import math
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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# REASONING TOOLS — Help the agent think, not just act
# ---------------------------------------------------------------------------

def think(reasoning: str) -> str:
    """Free-form scratchpad for the agent to reason out loud.

    The agent uses this to articulate what it knows, what it suspects,
    what trade-offs it's weighing, and what it's uncertain about —
    like an SE whiteboarding.
    """
    return "Thinking recorded. Continue your analysis."


def plan(steps: list) -> str:
    """Agent outlines what it will do next and why.

    Called at the start of a new user request to structure the approach.
    """
    return "Plan recorded. Proceed with execution."


def update_state(session_state: dict, updates: dict) -> str:
    """Merge new facts into the agent's persistent understanding state.

    Accepts partial updates — only provided keys are merged.
    """
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
# DOMAIN TOOLS — Help the agent compute, validate, and compare
# ---------------------------------------------------------------------------

def web_search(query: str) -> str:
    """Search the web for Couchbase vector index facts or embedding model specifications.

    Use this when you need to verify:
    - A specific embedding model's output dimensions (e.g. 'all-MiniLM-L6-v2 dimensions')
    - A Couchbase parameter limit you are unsure about
    - Recent Couchbase documentation on a feature
    NEVER use this for architectural decisions — those come from your knowledge base.
    """
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
    requires_keyword_search: bool
) -> str:
    """Evaluate index viability based on physical scale and selectivity rules.
    This provides deterministic math evaluation of AGENT.md ground truth.
    """
    report = ["--- DETERMINISTIC VIABILITY REPORT ---"]
    report.append(
        f"Input: {projected_vector_count:,} vectors, "
        f"{filter_selectivity_pct}% data remaining after filter, "
        f"keyword search required: {requires_keyword_search}"
    )

    # 1. Scale & Keyword Search Rules (The 100M Ceiling)
    if requires_keyword_search:
        if projected_vector_count > 100_000_000:
            report.append("❌ Search Vector Index (FTS): ELIMINATED. Projected scale > 100M exceeds the memory-mapping ceiling.")
            report.append("✅ Hybrid Architecture (HVI + FTS): REQUIRED. HVI handles billion-scale vectors; FTS handles keyword search.")
        else:
            report.append("✅ Search Vector Index (FTS): SUPPORTED. Scale is safely under the 100M ceiling.")
            report.append("⚠️  Hybrid Architecture (HVI + FTS): OVERKILL at this scale. Unified FTS is simpler and sufficient.")
    else:
        report.append("ℹ️  Search Vector Index / Hybrid: NOT APPLICABLE (no keyword search required).")

    # 2. Selectivity Rules
    if filter_selectivity_pct < 20:
        report.append(
            f"✅ Composite Vector Index (CVI): VIABLE. Filter prunes to {filter_selectivity_pct}% of corpus — "
            f"GSI eliminates {100 - filter_selectivity_pct:.0f}% before the ANN step. "
            f"CAVEAT: Full index must fit in cluster RAM (e.g., a 600GB index distributes easily across multiple index nodes)."
        )
        report.append(
            "⚠️  Hyperscale Vector Index (HVI): SUBOPTIMAL for this selectivity — "
            "it would scan the full graph when the filter could drastically shrink the search space."
        )
    else:
        report.append(
            f"❌ Composite Vector Index (CVI): ELIMINATED. Filter retains {filter_selectivity_pct}% of corpus — "
            f"the GSI pre-filter provides minimal reduction, causing massive memory pressure for little gain."
        )
        report.append(
            "✅ Hyperscale Vector Index (HVI): SUPPORTED. Designed for broad, low-selectivity searches "
            "across large datasets with 2% DGM disk-centric storage."
        )

    report.append("\nINSTRUCTION: Use these exact conclusions in your final give_recommendation call.")
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
                f"BUT: verify the full index fits in RAM before committing to CVI."
            )
        else:
            analysis["analysis"]["filter_verdict"] = (
                f"Filter retains {filter_selectivity_pct}% of corpus — moderately selective. "
                f"Composite's GSI scan cost is not justified by the ANN reduction at this selectivity."
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


def estimate_resources(
    vector_count: int,
    dimension: int,
    quantization: str = "None",
    persist_full_vector: bool = True,
) -> dict:
    """Estimate memory and disk footprint for a vector index.

    CRITICAL: Use this to verify a CVI or FTS index will fit in RAM before recommending it.
    Also use it to justify quantization decisions.
    """
    bytes_per_vector = {
        "None":    dimension * 4,         # 32-bit floats
        "SQ8":     dimension * 1,          # 8-bit ints
        "SQ4":     max(1, dimension // 2), # 4-bit
        "PQ128X8": 128,
        "PQ64X8":  64,
        "PQ32X8":  32,
    }

    bpv = bytes_per_vector.get(quantization, dimension * 4)
    index_bytes = vector_count * bpv
    full_vector_bytes = vector_count * dimension * 4 if persist_full_vector else 0
    total_bytes = index_bytes + full_vector_bytes

    def fmt_gb(b: int) -> float:
        return round(b / (1024 ** 3), 2)

    return {
        "vector_count": f"{vector_count:,}",
        "dimension": dimension,
        "quantization": quantization,
        "index_size_gb": fmt_gb(index_bytes),
        "full_vector_storage_gb": fmt_gb(full_vector_bytes) if persist_full_vector else "N/A (disabled)",
        "total_estimated_gb": fmt_gb(total_bytes),
        "persist_full_vector": persist_full_vector,
        "note": (
            "Raw vector storage estimate. Add ~20–30% overhead for metadata, "
            "centroid structures, and operational headroom."
        ),
    }


# ---------------------------------------------------------------------------
# TERMINAL TOOLS — Handled by the agent loop, not executed here
# ---------------------------------------------------------------------------

class Option(TypedDict):
    id: str
    label: str


class Question(TypedDict):
    question: str
    anchor: str
    why_asking: str
    options: List[Option]
    allow_free_form: Optional[bool]
    free_form_label: Optional[str]


def ask_user(message: str, questions: List[Question]) -> str:
    """
    TERMINAL TOOL. Call this when you identify gaps that prevent you from
    confidently recommending an index. Explain WHY you need to know —
    the user should understand what changes based on their answer.
    """
    return "Questions presented to user."


class QueryPatternRecommendation(TypedDict):
    query_pattern: str
    recommended_index: str
    reasoning: str
    eliminated_alternatives: Dict[str, str]
    caveats: Optional[List[str]]


class ArchitectureSummary(TypedDict):
    total_indexes: int
    index_types_used: List[str]
    shared_indexes: str
    operational_notes: str


def give_recommendation(
    summary: str,
    query_pattern_recommendations: List[QueryPatternRecommendation],
    architecture_summary: ArchitectureSummary
) -> str:
    """
    TERMINAL TOOL. Call this when you are confident in your reasoning
    and have validated your configuration. Provides the final output
    with full physical reasoning and eliminated alternatives.
    """
    return "Recommendation delivered to user."


# ---------------------------------------------------------------------------
# TOOL SCHEMAS — OpenAI / Gemini function calling format
# ---------------------------------------------------------------------------

ALL_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "Your scratchpad. Use this to reason out loud — dump what you know, "
                "what you suspect, what trade-offs you're weighing, and what you're "
                "uncertain about. Like an SE whiteboarding. Use liberally before making decisions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reasoning": {
                        "type": "string",
                        "description": (
                            "Your current reasoning in natural language — what you know, "
                            "what you don't know, what tradeoffs you're considering, "
                            "what your current hypothesis is."
                        ),
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
            "description": (
                "Outline what you'll do next and why. Call this at the start of a new "
                "user request to structure your approach."
            ),
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
                "Record confirmed facts, query patterns, open gaps, or reasoning into your "
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
                "Search the web for specific factual lookups: embedding model dimensions, "
                "Couchbase parameter limits, or recent documentation. "
                "NEVER use for architectural decisions — use your knowledge base for those."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (e.g. 'all-MiniLM-L6-v2 embedding dimensions')",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "evaluate_index_viability",
            "description": (
                "CRITICAL: Call this before give_recommendation. "
                "Evaluates the physical limits of vector counts and selectivities "
                "against Couchbase boundaries (e.g., the 100M FTS limit, selectivity thresholds)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "projected_vector_count": {
                        "type": "string",
                        "description": "The 3-YEAR PROJECTED vector count (e.g., '50000000' or '50M').",
                    },
                    "filter_selectivity_pct": {
                        "type": "string",
                        "description": (
                            "Percentage of data REMAINING after filters (e.g., '15' means 15% remains). "
                            "Use '100' if no filters are applied."
                        ),
                    },
                    "requires_keyword_search": {
                        "type": "string",
                        "description": "'true' or 'false'. Does the user need typos, fuzzy matching, or lexical BM25 search?",
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
                "Reasons about scale, filter behaviour, and migration risk for the specific situation."
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
                        "description": "Percentage of corpus REMAINING after filter (e.g. 2 means 2% remains)",
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
            "name": "estimate_resources",
            "description": (
                "CRITICAL: Estimate memory and disk footprint for a vector index. "
                "Use this to prove a CVI or FTS index fits in RAM before offering it. "
                "Also use when deciding quantization level."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vector_count": {"type": "integer"},
                    "dimension": {"type": "integer"},
                    "quantization": {
                        "type": "string",
                        "enum": ["SQ8", "SQ4", "PQ128X8", "PQ64X8", "PQ32X8", "None"],
                    },
                    "persist_full_vector": {"type": "boolean"},
                },
                "required": ["vector_count", "dimension", "quantization", "persist_full_vector"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "TERMINAL TOOL. Call this when you identify gaps that prevent you from "
                "confidently recommending an index. Explain WHY you need to know — "
                "the user should understand what changes based on their answer. "
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
                                    "description": "Reference the user's words, e.g., 'You mentioned category browsing...'",
                                },
                                "why_asking": {
                                    "type": "string",
                                    "description": "Explain physically/causally why this changes the recommendation.",
                                },
                                "options": {
                                    "type": "array",
                                    "description": (
                                        "MANDATORY: Provide 3–4 concrete options. "
                                        "An 'Other / Type here' option is added automatically by the UI."
                                    ),
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "id": {"type": "string"},
                                            "label": {"type": "string"},
                                        },
                                        "required": ["id", "label"],
                                    },
                                },
                                "allow_free_form": {"type": "boolean"},
                                "free_form_label": {"type": "string"},
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
                "TERMINAL TOOL. Call this ONLY after evaluate_index_viability has returned a report "
                "AND all required metrics (Scale, Growth, Selectivity, Dimension, Metric, Search Type) "
                "have been confirmed via ask_user. DO NOT GUESS OR INFER."
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
                                    "description": "Physical reasoning: I/O cost, scale, filter mechanics.",
                                },
                                "eliminated_alternatives": {
                                    "type": "object",
                                    "additionalProperties": {
                                        "type": "string",
                                        "description": "Why it was eliminated — physical reasoning.",
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
                "required": [
                    "summary",
                    "query_pattern_recommendations",
                    "architecture_summary",
                ],
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
    import re

    try:
        if tool_name == "think":
            return think(args.get("reasoning", ""))

        elif tool_name == "plan":
            return plan(args.get("steps", []))

        elif tool_name == "update_state":
            if session_state is not None:
                return update_state(session_state, args)
            return "Error: No session state available for update_state."

        elif tool_name == "web_search":
            return web_search(args.get("query", ""))

        elif tool_name == "evaluate_index_viability":
            # Robust parsing — handle loose LLM string outputs like "50M", "15-20%", "true"
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

        elif tool_name == "estimate_resources":
            return estimate_resources(
                vector_count=int(args.get("vector_count", 0)),
                dimension=int(args.get("dimension", 768)),
                quantization=args.get("quantization", "None"),
                persist_full_vector=bool(args.get("persist_full_vector", True)),
            )

        else:
            return f"Tool '{tool_name}' not found or is a terminal tool handled by the agent loop."

    except Exception as e:
        logger.error(f"Tool execution error [{tool_name}]: {e}")
        return json.dumps({
            "status": "error",
            "error": str(e),
            "suggestion": "Try with different parameters or call think() to reassess.",
        })
