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
        temperature=0.3,
        thinking_config=types.ThinkingConfig(thinking_budget=0),
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
    MAX_LOOPS = 12
    resp = None

    for loop_i in range(MAX_LOOPS):
        try:
            if loop_i == 0:
                resp = chat.send_message(first_prompt)

            candidate = resp.candidates[0] if resp and resp.candidates else None

            # --- Empty / blocked response handling ---
            if not candidate or not candidate.content or not candidate.content.parts:
                reason = getattr(candidate, "finish_reason", "UNKNOWN") if candidate else "NO_CANDIDATE"
                logger.error(f"Empty API response on loop {loop_i}. Finish reason: {reason}. Retrying...")

                try:
                    retry_resp = chat.send_message(
                        "Your last response was empty. Based on the Understanding State you already have, "
                        "immediately call give_recommendation or ask_user — whichever is appropriate. "
                        "Do not produce an empty response."
                    )
                    retry_candidate = retry_resp.candidates[0] if retry_resp and retry_resp.candidates else None
                    if retry_candidate and retry_candidate.content and retry_candidate.content.parts:
                        resp = retry_resp
                        continue
                except Exception as retry_e:
                    logger.error(f"Retry also failed: {retry_e}")

                return (
                    {"type": "error", "payload": {"message": "The AI service returned an empty response. Please try again."}},
                    ephemeral_trace,
                )

            parts = candidate.content.parts
            function_parts = [p for p in parts if getattr(p, "function_call", None)]

            # --- Pure text response ---
            if not function_parts:
                text = resp.text or ""
                if not text.strip():
                    logger.warning(f"Empty text response on loop {loop_i}.")
                    return (
                        {"type": "error", "payload": {"message": "The AI service returned an empty response. Please try again."}},
                        ephemeral_trace,
                    )
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

                else:
                    result = execute_tool(tool_name, args, session_state=session.get("state"))
                    result_str = (
                        json.dumps(result, default=str)
                        if isinstance(result, dict)
                        else str(result)
                    )
                    ephemeral_trace.append({
                        "tool": tool_name,
                        "args": args,
                        "content": thought,
                        "result": result_str,
                    })
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
            return (
                {"type": "error", "payload": {"message": f"Agent error: {str(e)}"}},
                ephemeral_trace,
            )

    return (
        {"type": "error", "payload": {"message": "Reasoning loop limit reached. Please rephrase your question."}},
        ephemeral_trace,
    )
