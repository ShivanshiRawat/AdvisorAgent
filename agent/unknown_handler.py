"""
agent/unknown.py

Detects when a user signals they don't know an answer, and builds
a system-note injection that directs the LLM to accept the unknown
and proceed with conservative assumptions rather than re-asking.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Known uncertainty phrases (normalised lowercase)
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
    # Also catch short sentences that contain a known phrase
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
