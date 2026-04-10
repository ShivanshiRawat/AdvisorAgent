"""
agent/llm_loop.py

Provider-agnostic ReAct reasoning loop.
Delegates all LLM communication to the BaseLLMProvider interface,
keeping this module free of any SDK-specific imports.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import config
from agent.providers.base import BaseLLMProvider, LLMResponse, ToolResult
from agent.session import _load_agent_md
from prompts import get_system_prompt
from tools import execute_tool

logger = logging.getLogger(__name__)

TERMINAL_TOOLS = frozenset({"ask_user", "give_recommendation", "give_performance_profile"})

_TERMINAL_TYPE_MAP = {
    "ask_user": "clarification",
    "give_recommendation": "recommendation",
    "give_performance_profile": "performance_profile",
}


def _build_system_prompt() -> str:
    agent_md = _load_agent_md()
    system_prompt = get_system_prompt()
    return f"{system_prompt}\n\n### EXPERT KNOWLEDGE BASE (GROUND TRUTH)\n{agent_md}"


def _build_recovery_context(session: Dict[str, Any]) -> dict:
    understanding = session.get("state", {})
    return {
        "confirmed_facts": understanding.get("confirmed_facts", {}),
        "query_patterns":  understanding.get("query_patterns", []),
        "reasoning_so_far": understanding.get("reasoning_so_far", ""),
        "open_gaps":       understanding.get("open_gaps", []),
        "resolved_gaps":   understanding.get("resolved_gaps", []),
    }


def _build_fallback_text(session: Dict[str, Any]) -> str:
    reasoning = session.get("state", {}).get("reasoning_so_far", "").strip()
    text = (
        "I'm sorry -- the internal model was not able to complete its reasoning.\n\n"
    )
    if reasoning:
        text += f"**Here is my reasoning up to this point:**\n{reasoning}\n\n"
    text += "Please **reload the chat** to continue. Sorry for the inconvenience."
    return text


def run_llm_turn(
    provider: BaseLLMProvider,
    session: Dict[str, Any],
    effective_message: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Execute one full ReAct loop. Returns (terminal_payload, ephemeral_trace)."""

    full_system = _build_system_prompt()
    state_json = json.dumps(session.get("state", {}), indent=2, default=str)

    prior_history = session["history"][:-1] if len(session["history"]) > 1 else []
    chat = provider.start_chat(full_system, [], prior_history)

    user_latest = (
        effective_message
        if effective_message is not None
        else session["history"][-1]["content"]
    )
    first_prompt = (
        f"### Current Understanding State\n```json\n{state_json}\n```\n\n"
        f"User: {user_latest}"
    )

    ephemeral_trace: List[Dict[str, Any]] = []
    exception_count = 0
    resp = None  # type: Optional[LLMResponse]

    for loop_i in range(config.MAX_LOOPS):
        try:
            # ---- send message -----------------------------------------
            if loop_i == 0:
                resp = provider.send_message(chat, first_prompt)
            if resp is None:
                break

            # ---- empty / error response handling ----------------------
            if resp.finish_reason == "error":
                logger.error("Empty/malformed response on loop %d. Retrying…", loop_i)
                recovery_prompt = (
                    "Your previous response was empty or malformed.\n"
                    "Here is the understanding so far:\n\n"
                    f"```json\n{json.dumps(_build_recovery_context(session), indent=2, default=str)}\n```\n\n"
                    "If you are presenting a tool result (e.g. benchmark baseline), respond with plain text. "
                    "Otherwise call `ask_user` if information is missing, or `give_recommendation` only if "
                    "you are delivering an initial index recommendation for the first time."
                )
                try:
                    retry = provider.send_message(chat, recovery_prompt)
                    if retry.finish_reason != "error":
                        resp = retry
                        continue
                except Exception as retry_e:
                    logger.error("Recovery retry failed: %s", retry_e)

                fallback = _build_fallback_text(session)
                session["history"].append({"role": "model", "content": fallback})
                return (
                    {"type": "text", "payload": {"message": fallback}},
                    ephemeral_trace,
                )

            # ---- pure text (no tool calls) ----------------------------
            if resp.finish_reason in ("stop", "empty") and not resp.tool_calls:
                text = resp.text or ""
                if not text.strip():
                    logger.warning("Empty text on loop %d, nudging model.", loop_i)
                    resp = provider.send_message(
                        chat,
                        "Please call `ask_user` or `give_recommendation` now.",
                    )
                    continue
                session["history"].append({"role": "model", "content": text})
                return (
                    {"type": "text", "payload": {"message": text}},
                    ephemeral_trace,
                )

            # ---- process tool calls -----------------------------------
            thought = resp.text or ""
            tool_results: List[ToolResult] = []
            terminal_found = False
            terminal_payload = None  # type: Optional[Dict[str, Any]]

            for tc in resp.tool_calls:
                if tc.name in TERMINAL_TOOLS:
                    terminal_found = True
                    terminal_payload = {
                        "type": _TERMINAL_TYPE_MAP[tc.name],
                        "args": tc.args,
                    }
                    ephemeral_trace.append({
                        "tool": tc.name,
                        "args": tc.args,
                        "content": thought,
                        "result": f"{tc.name} delivered.",
                    })
                    tool_results.append(ToolResult(
                        call_id=tc.call_id,
                        name=tc.name,
                        response={"result": f"{tc.name} delivered."},
                    ))
                else:
                    result = execute_tool(
                        tc.name,
                        tc.args,
                        session_state=session.get("state"),
                        provider=provider,
                    )

                    source_urls: list = []
                    if tc.name == "web_search" and isinstance(result, tuple):
                        result, source_urls = result

                    result_str = (
                        json.dumps(result, default=str)
                        if isinstance(result, dict)
                        else str(result)
                    )
                    trace_entry: Dict[str, Any] = {
                        "tool": tc.name,
                        "args": tc.args,
                        "content": thought,
                        "result": result_str,
                    }
                    if source_urls:
                        trace_entry["source_urls"] = source_urls
                    ephemeral_trace.append(trace_entry)

                    response_payload = (
                        result if isinstance(result, dict) else {"result": result_str}
                    )
                    tool_results.append(ToolResult(
                        call_id=tc.call_id,
                        name=tc.name,
                        response=response_payload,
                    ))

            if terminal_found:
                return terminal_payload, ephemeral_trace

            resp = provider.send_tool_results(chat, tool_results)

        except Exception as e:
            logger.error("LLM loop error (iteration %d): %s", loop_i, e, exc_info=True)
            exception_count += 1

            if exception_count <= config.MAX_RETRIES:
                logger.warning(
                    "Exception #%d/%d. Retrying with recovery context…",
                    exception_count, config.MAX_RETRIES,
                )
                recovery_prompt = (
                    "There was a temporary connection issue.\n"
                    "Here is the full understanding so far:\n\n"
                    f"```json\n{json.dumps(_build_recovery_context(session), indent=2, default=str)}\n```\n\n"
                    "Pick up from this point. If you are presenting a tool result (e.g. benchmark baseline), "
                    "respond with plain text. Otherwise call `ask_user` if information is missing, or "
                    "`give_recommendation` only if delivering an initial index recommendation for the first time."
                )
                try:
                    resp = provider.send_message(chat, recovery_prompt)
                    continue
                except Exception as retry_e:
                    logger.error("Retry %d also failed: %s", exception_count, retry_e)
                    continue

            logger.error("All exception retries exhausted.")
            fallback = _build_fallback_text(session)
            session["history"].append({"role": "model", "content": fallback})
            return (
                {"type": "text", "payload": {"message": fallback}},
                ephemeral_trace,
            )

    # Loop limit reached
    logger.error("Reasoning loop limit reached.")
    fallback = _build_fallback_text(session)
    session["history"].append({"role": "model", "content": fallback})
    return (
        {"type": "text", "payload": {"message": fallback}},
        ephemeral_trace,
    )
