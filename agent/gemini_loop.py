"""
agent/gemini_loop.py

Gemini-specific implementation of the ReAct reasoning loop.
Contains all google-genai SDK usage: client singleton, schema conversion,
and the inner _run_gemini_turn loop that sends messages and processes
tool call responses until a terminal tool (ask_user / give_recommendation)
is encountered.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

import config
from google import genai
from google.genai import types

from agent.session import _load_agent_md, _to_gemini_history
from prompts import get_system_prompt
from tools import execute_tool, ALL_TOOL_SCHEMAS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------

_client: genai.Client = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


# ---------------------------------------------------------------------------
# Schema conversion — OpenAI JSON Schema dict → Gemini types.Schema
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "string":  "STRING",
    "integer": "INTEGER",
    "number":  "NUMBER",
    "boolean": "BOOLEAN",
    "object":  "OBJECT",
    "array":   "ARRAY",
}


def _convert_schema(schema: dict) -> types.Schema:
    """Recursively convert an OpenAI-style JSON Schema dict to types.Schema."""
    if not schema:
        return types.Schema(type="OBJECT")

    gemini_type = _TYPE_MAP.get(schema.get("type", "string"), "STRING")

    properties = None
    if "properties" in schema:
        properties = {k: _convert_schema(v) for k, v in schema["properties"].items()}

    items = _convert_schema(schema["items"]) if "items" in schema else None

    return types.Schema(
        type=gemini_type,
        description=schema.get("description"),
        properties=properties,
        required=schema.get("required"),
        items=items,
        enum=schema.get("enum"),
    )


def _build_gemini_tools() -> List[types.Tool]:
    """Convert ALL_TOOL_SCHEMAS (OpenAI format) → a single Gemini Tool."""
    declarations = []
    for schema in ALL_TOOL_SCHEMAS:
        fn = schema["function"]
        params_dict = fn.get("parameters", {"type": "object", "properties": {}})
        declarations.append(
            types.FunctionDeclaration(
                name=fn["name"],
                description=fn["description"],
                parameters=_convert_schema(params_dict),
            )
        )
    return [types.Tool(function_declarations=declarations)]


_GEMINI_TOOLS: List[types.Tool] = None  # built once lazily


def _get_tools() -> List[types.Tool]:
    global _GEMINI_TOOLS
    if _GEMINI_TOOLS is None:
        _GEMINI_TOOLS = _build_gemini_tools()
    return _GEMINI_TOOLS


# ---------------------------------------------------------------------------
# Gemini-native ReAct loop
# ---------------------------------------------------------------------------

def _run_gemini_turn(
    session: Dict[str, Any],
    effective_message: str = None,
) -> Dict[str, Any]:
    agent_md = _load_agent_md()
    system_prompt = get_system_prompt()
    state_json = json.dumps(session.get("state", {}), indent=2, default=str)

    full_system = (
        f"{system_prompt}\n\n"
        f"### EXPERT KNOWLEDGE BASE (GROUND TRUTH)\n{agent_md}"
    )

    client = _get_client()

    gen_config = types.GenerateContentConfig(
        system_instruction=full_system,
        tools=_get_tools(),
        temperature=config.TEMPERATURE,
        thinking_config=types.ThinkingConfig(thinking_budget=config.THINKING_BUDGET),
    )

    prior_history = _to_gemini_history(session["history"][:-1]) if len(session["history"]) > 1 else []

    chat = client.chats.create(
        model=config.MODEL,
        config=gen_config,
        history=prior_history,
    )

    # Use the (possibly annotated) effective message, falling back to raw history content
    user_latest = effective_message if effective_message is not None else session["history"][-1]["content"]
    first_prompt = (
        f"### Current Understanding State\n```json\n{state_json}\n```\n\n"
        f"User: {user_latest}"
    )

    ephemeral_trace: List[Dict[str, Any]] = []
    MAX_LOOPS = config.MAX_LOOPS
    MAX_EXCEPTION_RETRIES = config.MAX_RETRIES
    exception_count = 0
    resp = None

    for loop_i in range(MAX_LOOPS):
        try:
            if loop_i == 0:
                resp = chat.send_message(first_prompt)

            candidate = resp.candidates[0] if resp and resp.candidates else None

            # --- Empty / blocked / malformed response handling ---
            # MALFORMED_FUNCTION_CALL can arrive with parts present but broken,
            # so we must check finish_reason in addition to parts being empty.
            finish_reason = (
                str(getattr(candidate, "finish_reason", "")).upper()
                if candidate else "NO_CANDIDATE"
            )
            is_bad = (
                not candidate
                or not candidate.content
                or not candidate.content.parts
                or "MALFORMED" in finish_reason
                or "RECITATION" in finish_reason
            )

            if is_bad:
                logger.error(
                    f"Empty/malformed API response on loop {loop_i}. "
                    f"Finish reason: {finish_reason}. Retrying with understanding state..."
                )
                # Build a concise recovery prompt using ONLY the structured
                # understanding state — not raw history, not the full session.
                understanding = session.get("state", {})
                recovery_context = {
                    "confirmed_facts": understanding.get("confirmed_facts", {}),
                    "query_patterns": understanding.get("query_patterns", []),
                    "reasoning_so_far": understanding.get("reasoning_so_far", ""),
                    "open_gaps": understanding.get("open_gaps", []),
                    "resolved_gaps": understanding.get("resolved_gaps", []),
                }
                recovery_prompt = (
                    "Your previous response was empty or malformed.\n"
                    "Here is the understanding you have built so far about the user's use case:\n\n"
                    f"```json\n{json.dumps(recovery_context, indent=2, default=str)}\n```\n\n"
                    "Based on this, immediately call:\n"
                    "- `ask_user` if there are unresolved gaps you still need answered\n"
                    "- `give_recommendation` if confirmed_facts are sufficient for a decision\n"
                    "Do NOT respond with plain text. Use one of those two tools."
                )
                try:
                    retry_resp = chat.send_message(recovery_prompt)
                    retry_candidate = (
                        retry_resp.candidates[0]
                        if retry_resp and retry_resp.candidates
                        else None
                    )
                    retry_finish = (
                        str(getattr(retry_candidate, "finish_reason", "")).upper()
                        if retry_candidate else ""
                    )
                    retry_ok = (
                        retry_candidate
                        and retry_candidate.content
                        and retry_candidate.content.parts
                        and "MALFORMED" not in retry_finish
                    )
                    if retry_ok:
                        resp = retry_resp
                        continue
                    logger.error(f"Retry also malformed/empty. Finish reason: {retry_finish}")
                except Exception as retry_e:
                    logger.error(f"Retry send failed: {retry_e}")

                # Final fallback — return reasoning so far with a friendly apology.
                # The user should never see the raw error.
                reasoning = understanding.get("reasoning_so_far", "").strip()
                fallback_text = (
                    "I'm sorry — the internal model is receiving too many simultaneous calls "
                    "and wasn't able to complete its reasoning.\n\n"
                )
                if reasoning:
                    fallback_text += f"**Here is my reasoning up to this point:**\n{reasoning}\n\n"
                fallback_text += (
                    "Please **reload the chat** to start. "
                    "Sorry for the inconvenience."
                )
                session["history"].append({"role": "model", "content": fallback_text})
                return (
                    {"type": "text", "payload": {"message": fallback_text}},
                    ephemeral_trace,
                )

            parts = candidate.content.parts
            function_parts = [p for p in parts if getattr(p, "function_call", None)]

            # --- Pure text response ---
            if not function_parts:
                text = resp.text or ""
                if not text.strip():
                    logger.warning(f"Empty text response on loop {loop_i}. Treating as loop continuation.")
                    # Don't expose this — just skip and let the loop continue or hit MAX_LOOPS
                    resp = chat.send_message(
                        "Please call `ask_user` or `give_recommendation` now based on what you know."
                    )
                    continue
                session["history"].append({"role": "model", "content": text})
                return (
                    {"type": "text", "payload": {"message": text}},
                    ephemeral_trace,
                )

            # --- Extract agent thought (text before function calls) ---
            thought = ""
            for p in parts:
                if not getattr(p, "function_call", None):
                    txt = getattr(p, "text", "") or ""
                    if txt.strip():
                        thought = txt.strip()
                        break

            # --- Process tool calls ---
            tool_responses = []
            terminal_found = False
            terminal_payload = None

            for part in function_parts:
                fn = part.function_call
                tool_name = fn.name
                args = {k: v for k, v in fn.args.items()}

                if tool_name == "ask_user":
                    terminal_found = True
                    terminal_payload = {"type": "clarification", "args": args}
                    ephemeral_trace.append({
                        "tool": "ask_user",
                        "args": args,
                        "content": thought,
                        "result": "Questions sent to user.",
                    })
                    tool_responses.append(
                        types.Part.from_function_response(
                            name=tool_name,
                            response={"result": "Questions sent to user."},
                        )
                    )

                elif tool_name == "give_recommendation":
                    terminal_found = True
                    terminal_payload = {"type": "recommendation", "args": args}
                    ephemeral_trace.append({
                        "tool": "give_recommendation",
                        "args": args,
                        "content": thought,
                        "result": "Recommendation delivered.",
                    })
                    tool_responses.append(
                        types.Part.from_function_response(
                            name=tool_name,
                            response={"result": "Recommendation delivered to user."},
                        )
                    )

                elif tool_name == "give_performance_profile":
                    terminal_found = True
                    terminal_payload = {"type": "performance_profile", "args": args}
                    ephemeral_trace.append({
                        "tool": "give_performance_profile",
                        "args": args,
                        "content": thought,
                        "result": "Performance profile delivered.",
                    })
                    tool_responses.append(
                        types.Part.from_function_response(
                            name=tool_name,
                            response={"result": "Performance profile delivered to user."},
                        )
                    )

                else:
                    result = execute_tool(
                        tool_name,
                        args,
                        session_state=session.get("state"),
                        gemini_client=client,
                        gemini_model=config.MODEL,
                    )

                    # web_search returns (text, source_urls) tuple
                    source_urls = []
                    if tool_name == "web_search" and isinstance(result, tuple):
                        result, source_urls = result

                    result_str = (
                        json.dumps(result, default=str)
                        if isinstance(result, dict)
                        else str(result)
                    )

                    trace_entry = {
                        "tool": tool_name,
                        "args": args,
                        "content": thought,
                        "result": result_str,
                    }
                    if source_urls:
                        trace_entry["source_urls"] = source_urls

                    ephemeral_trace.append(trace_entry)
                    response_payload = (
                        result if isinstance(result, dict) else {"result": result_str}
                    )
                    tool_responses.append(
                        types.Part.from_function_response(
                            name=tool_name,
                            response=response_payload,
                        )
                    )

            if terminal_found:
                return terminal_payload, ephemeral_trace

            resp = chat.send_message(tool_responses)

        except Exception as e:
            logger.error(f"Gemini loop error (iteration {loop_i}): {e}", exc_info=True)
            exception_count += 1

            if exception_count <= MAX_EXCEPTION_RETRIES:
                logger.warning(
                    f"Exception #{exception_count}. Retrying on same chat with understanding state "
                    f"(attempt {exception_count}/{MAX_EXCEPTION_RETRIES})..."
                )
                understanding = session.get("state", {})
                recovery_context = {
                    "confirmed_facts":  understanding.get("confirmed_facts", {}),
                    "query_patterns":   understanding.get("query_patterns", []),
                    "reasoning_so_far": understanding.get("reasoning_so_far", ""),
                    "open_gaps":        understanding.get("open_gaps", []),
                    "resolved_gaps":    understanding.get("resolved_gaps", []),
                }
                recovery_prompt = (
                    "There was a temporary connection issue.\n"
                    "Here is the full understanding of the user's use case built so far:\n\n"
                    f"```json\n{json.dumps(recovery_context, indent=2, default=str)}\n```\n\n"
                    "Pick up exactly from this point. Immediately call:\n"
                    "- `ask_user` if there are open gaps you need to resolve\n"
                    "- `give_recommendation` if confirmed_facts are sufficient\n"
                    "Do NOT produce plain text. Use one of those two tools."
                )
                try:
                    resp = chat.send_message(recovery_prompt)
                    continue  # back to top of for-loop on the SAME chat
                except Exception as retry_e:
                    logger.error(f"Retry attempt {exception_count} also failed: {retry_e}")
                    continue  # let exception_count accumulate, try again next iteration

            # All retries exhausted — show the friendly fallback
            logger.error("All exception retries exhausted. Showing fallback.")
            reasoning = session.get("state", {}).get("reasoning_so_far", "").strip()
            fallback_text = (
                "I'm sorry — the internal model is receiving too many simultaneous calls "
                "and wasn't able to complete its reasoning.\n\n"
            )
            if reasoning:
                fallback_text += f"**Here is my reasoning up to this point:**\n{reasoning}\n\n"
            fallback_text += (
                "Please **reload the chat** to start. "
                "Sorry for the inconvenience."
            )
            session["history"].append({"role": "model", "content": fallback_text})
            return (
                {"type": "text", "payload": {"message": fallback_text}},
                ephemeral_trace,
            )

    # Loop limit reached
    logger.error("Reasoning loop limit reached.")
    reasoning = session.get("state", {}).get("reasoning_so_far", "").strip()
    fallback_text = (
        "I'm sorry — the internal model is receiving too many simultaneous calls "
        "and wasn't able to complete its reasoning.\n\n"
    )
    if reasoning:
        fallback_text += f"**Here is my reasoning up to this point:**\n{reasoning}\n\n"
    fallback_text += (
        "Please **reload the chat** to continue from where we left off. "
        "Sorry for the inconvenience."
    )
    session["history"].append({"role": "model", "content": fallback_text})
    return (
        {"type": "text", "payload": {"message": fallback_text}},
        ephemeral_trace,
    )
