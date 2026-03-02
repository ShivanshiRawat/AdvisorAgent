"""
VIA Agent — Gemini-native ReAct loop using the google-genai SDK.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, List

from google import genai
from google.genai import types

import config
from knowledge import get_system_prompt
from tools import execute_tool, ALL_TOOL_SCHEMAS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Client (module-level singleton)
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
# History helpers
# ---------------------------------------------------------------------------

def _to_gemini_history(history: List[Dict[str, Any]]) -> List[types.Content]:
    result = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        content = msg.get("content") or ""
        if content:
            result.append(
                types.Content(
                    role=role,
                    parts=[types.Part(text=content)],
                )
            )
    return result


def _load_agent_md() -> str:
    path = Path(__file__).parent / "AGENT.md"
    try:
        if path.exists():
            return path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Failed to load AGENT.md: {e}")
    return ""


# ---------------------------------------------------------------------------
# Session initialisation
# ---------------------------------------------------------------------------

def _init_session(session: Dict[str, Any]):
    if "history" not in session:
        session["history"] = []
    if "state" not in session:
        session["state"] = {
            "narrative_summary": "",
            "confirmed_facts": {},
            "query_patterns": [],
            "open_gaps": [],
            "resolved_gaps": [],
            "reasoning_so_far": "",
        }


def _compress_history(session: Dict[str, Any]):
    """Keep the last 10 turns. Compress older ones into narrative_summary."""
    MAX_HISTORY = 12
    history = session["history"]
    if len(history) <= MAX_HISTORY:
        return

    to_compress = history[:-10]
    summaries = []
    for msg in to_compress:
        role = msg.get("role", "")
        content = (msg.get("content") or "")[:300]
        if content:
            prefix = "User" if role == "user" else "Agent"
            summaries.append(f"{prefix}: {content}")

    if summaries:
        existing = session["state"].get("narrative_summary", "")
        addition = " | ".join(summaries)
        session["state"]["narrative_summary"] = (
            (existing + " | " + addition) if existing else addition
        )

    session["history"] = history[:2] + history[-10:]


# ---------------------------------------------------------------------------
# "I don't know" detection
# ---------------------------------------------------------------------------

_UNKNOWN_PHRASES = {
    # Direct negations
    "i don't know", "i dont know", "don't know", "dont know",
    "not sure", "unsure", "no idea", "no clue", "idk",
    "i'm not sure", "im not sure", "i am not sure",
    "not known", "unknown", "i have no idea",
    "i can't say", "i cannot say", "can't say",
    "i don't have that info", "i don't have that information",
    "not provided", "n/a", "na", "skip",
    # Naturalistic uncertainty (commonly missed)
    "hard to say", "hard to tell", "difficult to say",
    "not certain", "i'm not certain", "im not certain",
    "can't be sure", "cannot be sure",
    "haven't tracked", "don't track", "we don't track",
    "no visibility", "no data on that", "no metrics",
    "roughly", "approximately", 
    "depends", "it depends", "varies", "it varies",
    "not sure about that", "unsure about that",
    "maybe", "probably", "possibly", 
    "i'll have to check", "need to check", "not off the top of my head",
    "don't have that number", "don't have that figure",
    "approximate", "ballpark", "rough estimate",
}

def _is_unknown_response(text: str) -> bool:
    """Return True if the user is signalling they don't know the answer."""
    normalised = text.strip().lower()
    if normalised in _UNKNOWN_PHRASES:
        return True
    # Also catch short sentences that are substrings
    for phrase in _UNKNOWN_PHRASES:
        if phrase in normalised and len(normalised) < 80:
            return True
    return False


def _build_unknown_note(user_message: str) -> str:
    """Produce an injection note for the agent when user says they don't know."""
    return (
        f"[SYSTEM NOTE] The user replied: \"{user_message.strip()}\"\n"
        "This means they do not have this information. "
        "You MUST accept this as a confirmed unknown, record it in update_state "
        "(add the gap to resolved_gaps with value 'unknown'), and move forward. "
        "Do NOT ask the same question again. "
        "Proceed to give a recommendation based on everything you know so far, "
        "using the most conservative/safe assumption for any unknowns."
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_turn(user_message: str, session: Dict[str, Any]) -> Dict[str, Any]:
    """Run one ReAct turn and return a typed response dict."""
    _init_session(session)
    _compress_history(session)

    # Detect "I don't know" responses early and inject a directive
    if _is_unknown_response(user_message):
        effective_message = _build_unknown_note(user_message)
    else:
        effective_message = user_message

    session["history"].append({"role": "user", "content": user_message})
    return _run_gemini_turn(session, effective_message)


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

                # Retry once with an explicit directive
                try:
                    retry_resp = chat.send_message(
                        "Your last response was empty. Based on the Understanding State you already have, "
                        "immediately call give_recommendation or ask_user — whichever is appropriate. "
                        "Do not produce an empty response."
                    )
                    retry_candidate = retry_resp.candidates[0] if retry_resp and retry_resp.candidates else None
                    if retry_candidate and retry_candidate.content and retry_candidate.content.parts:
                        resp = retry_resp
                        continue  # re-enter loop with the retry response
                except Exception as retry_e:
                    logger.error(f"Retry also failed: {retry_e}")

                # Both retries failed — return an error
                return {
                    "type": "error",
                    "payload": {"message": "The AI service returned an empty response. Please try again."},
                    "steps": ephemeral_trace,
                }

            parts = candidate.content.parts
            function_parts = [p for p in parts if getattr(p, "function_call", None)]

            # --- Pure text response ---
            if not function_parts:
                text = resp.text or ""
                if not text.strip():
                    logger.warning(f"Empty text response on loop {loop_i}.")
                    return {
                        "type": "error",
                        "payload": {"message": "The AI service returned an empty response. Please try again."},
                        "steps": ephemeral_trace,
                    }
                session["history"].append({"role": "model", "content": text})
                return {
                    "type": "text",
                    "payload": {"message": text},
                    "steps": ephemeral_trace,
                }

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
                _record_terminal(session, terminal_payload)
                return {
                    "type": terminal_payload["type"],
                    "payload": terminal_payload["args"],
                    "steps": ephemeral_trace,
                }

            resp = chat.send_message(tool_responses)

        except Exception as e:
            logger.error(f"Gemini loop error (iteration {loop_i}): {e}", exc_info=True)
            return {
                "type": "error",
                "payload": {"message": f"Agent error: {str(e)}"},
                "steps": ephemeral_trace,
            }

    return {
        "type": "error",
        "payload": {"message": "Reasoning loop limit reached. Please rephrase your question."},
        "steps": ephemeral_trace,
    }


def _record_terminal(session: Dict[str, Any], terminal_payload: Dict[str, Any]):
    if terminal_payload["type"] == "clarification":
        questions = terminal_payload["args"].get("questions", [])
        qs = [q.get("question", "") for q in questions]
        session["history"].append({
            "role": "model",
            "content": f"I asked: {'; '.join(qs)}",
        })
    else:
        recs = terminal_payload["args"].get("query_pattern_recommendations", [])
        lines = [
            f"{r.get('query_pattern')} → {r.get('recommended_index')}"
            for r in recs
        ]
        session["history"].append({
            "role": "model",
            "content": f"I recommended: {', '.join(lines)}",
        })
