"""
agent/core.py

Public API for the VIA agent.
run_turn is the single entry point called by app.py.
It orchestrates session initialisation, unknown-response detection,
history management, and delegates to the Gemini loop.
"""

from __future__ import annotations

from typing import Any, Dict

from agent.session import _init_session, _compress_history
from agent.unknown_handler import _is_unknown_response, _build_unknown_note
from agent.gemini_loop import _run_gemini_turn


def run_turn(user_message: str, session: Dict[str, Any]) -> Dict[str, Any]:
    """Run one ReAct turn and return a typed response dict.

    Returns a dict with keys:
      - type: "recommendation" | "clarification" | "text" | "error"
      - payload: type-specific data dict
      - steps: list of ephemeral trace entries for UI rendering
    """
    _init_session(session)
    _compress_history(session)

    # Detect "I don't know" responses and inject a directive for the LLM
    if _is_unknown_response(user_message):
        effective_message = _build_unknown_note(user_message)
    else:
        effective_message = user_message

    session["history"].append({"role": "user", "content": user_message})

    terminal_payload, ephemeral_trace = _run_gemini_turn(session, effective_message)

    _record_terminal(session, terminal_payload)

    return {
        "type":    terminal_payload["type"],
        "payload": terminal_payload.get("args", terminal_payload.get("payload", {})),
        "steps":   ephemeral_trace,
    }


def _record_terminal(session: Dict[str, Any], terminal_payload: Dict[str, Any]) -> None:
    """Append a compact summary of the terminal tool call to conversation history."""
    if terminal_payload["type"] == "clarification":
        questions = terminal_payload.get("args", {}).get("questions", [])
        qs = [q.get("question", "") for q in questions]
        session["history"].append({
            "role": "model",
            "content": f"I asked: {'; '.join(qs)}",
        })
    elif terminal_payload["type"] == "recommendation":
        recs = terminal_payload.get("args", {}).get("query_pattern_recommendations", [])
        lines = [
            f"{r.get('query_pattern')} → {r.get('recommended_index')}"
            for r in recs
        ]
        session["history"].append({
            "role": "model",
            "content": f"I recommended: {', '.join(lines)}",
        })
    elif terminal_payload["type"] == "performance_profile":
        metrics = terminal_payload.get("args", {}).get("metrics", [])
        summary = ", ".join(
            f"{m.get('metric')} ({m.get('priority')}, bin: {m.get('bin')}, target: {m.get('target_range')})"
            for m in metrics
        )
        session["history"].append({
            "role": "model",
            "content": f"I presented a performance profile: {summary}",
        })
    # "error" and "text" types don't need history entries — the loop already handles those
