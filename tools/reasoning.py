"""
tools/reasoning.py

Reasoning tools — help the agent think and track state.
These tools have no side effects; they capture structured information
as arguments which the agent loop persists to session state.
"""


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
