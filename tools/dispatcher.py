"""
tools/dispatcher.py

Maps tool names to their Python implementations and handles
argument parsing for LLM-friendly loose inputs (e.g. "50M", "true").
Terminal tools (ask_user, give_recommendation) are NOT dispatched here —
the agent loop intercepts them before calling execute_tool.
"""

import json
import logging
import re
from typing import Any, Optional

from tools.reasoning import think, plan, update_state
from tools.domain import web_search, evaluate_index_viability, compare_indexes, use_case_search, get_default_parameters, get_index_queries
from tools.performance_tools import find_baseline_configuration

logger = logging.getLogger(__name__)


def execute_tool(
    tool_name: str,
    args: dict,
    session_state: Optional[dict] = None,
    gemini_client=None,
    gemini_model: str = None,
) -> Any:
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
            # Returns (text, source_urls) tuple — caller handles the split
            return web_search(
                args.get("query", ""),
                gemini_client=gemini_client,
                gemini_model=gemini_model,
            )

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

        elif tool_name == "get_default_parameters":
            return get_default_parameters(
                index_type=args.get("index_type", "HVI"),
                vector_count=int(args.get("vector_count", 0)),
            )

        elif tool_name == "get_index_queries":
            return get_index_queries(
                index_type=args.get("index_type", "HVI"),
            )

        elif tool_name == "find_baseline_configuration":
            # Parse scale — the LLM may pass "700M", "1B", or a plain integer
            scale_raw = str(args.get("target_scale", "0")).upper()
            scale_mult = (
                1_000_000_000 if "B" in scale_raw
                else 1_000_000 if "M" in scale_raw
                else 1_000 if "K" in scale_raw
                else 1
            )
            scale_clean = re.sub(r"[^\d.]", "", scale_raw)
            target_scale = int(float(scale_clean) * scale_mult) if scale_clean else 0

            return find_baseline_configuration(
                solution=str(args.get("solution", "BHIVE")).upper(),
                target_scale=target_scale,
                target_dimension=int(args.get("target_dimension", 0)),
                target_recall=float(args.get("target_recall", 0.0)),
                target_qps=float(args.get("target_qps", 0.0)),
                target_latency=float(args.get("target_latency", 0.0)),
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
