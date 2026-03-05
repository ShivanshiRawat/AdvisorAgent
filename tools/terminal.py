"""
tools/terminal.py

Terminal tools — signal the end of a reasoning turn.
These functions define the schema only; actual rendering is handled by app.py.
The agent loop intercepts these calls and exits instead of running the functions.
"""

from typing import List, TypedDict


class Option(TypedDict):
    id: str
    label: str


class Question(TypedDict):
    question: str
    anchor: str
    why_asking: str
    options: List[Option]


def ask_user(message: str, questions: List[Question]) -> str:
    """TERMINAL TOOL. Ask clarifying questions when critical information is
    missing. Every question MUST have 3–4 concrete options."""
    return "Questions presented to user."


def give_recommendation(
    summary: str,
    query_pattern_recommendations: list,
    architecture_summary: dict,
) -> str:
    """TERMINAL TOOL. Deliver the final index recommendation. Only call after
    evaluate_index_viability has returned a report."""
    return "Recommendation delivered to user."
