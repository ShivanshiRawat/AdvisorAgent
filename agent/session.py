"""
agent/session.py

Session lifecycle helpers:
- Initialise a fresh session dict.
- Compress long histories into the narrative_summary to stay within token limits.
- Load the expert knowledge base from AGENT.md.

This module is provider-agnostic; history conversion to provider-specific
types is handled inside each provider implementation.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)

_AGENT_MD_PATH = Path(__file__).parent.parent / "AGENT.md"


def _load_agent_md() -> str:
    """Load the expert knowledge base from AGENT.md."""
    try:
        if _AGENT_MD_PATH.exists():
            return _AGENT_MD_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.error("Failed to load AGENT.md: %s", e)
    return ""


def _init_session(session: Dict[str, Any]) -> None:
    """Populate a new session with default keys if they don't already exist."""
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


def _compress_history(session: Dict[str, Any]) -> None:
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
