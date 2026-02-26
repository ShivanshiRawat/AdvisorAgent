"""
Chainlit UI for the Couchbase Vector Index Advisor.

Key behaviours:
- Questions are asked ONE AT A TIME with inline button options
- "✏️ Other / Type here" opens an inline text input — no search box
- Agent reasoning is shown as collapsible cl.Step dropdowns
- Every tool call shows WHAT ran, WHY, and WHAT it returned
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Chainlit context workaround (2.x)
# ---------------------------------------------------------------------------
try:
    from chainlit.context import local_steps
    try:
        local_steps.get()
    except LookupError:
        local_steps.set(None)
except Exception:
    pass

import json
from typing import Any, Dict, List

import chainlit as cl

import config  # loads .env
from agent import run_turn


# ---------------------------------------------------------------------------
# Tool → human-readable label & emoji
# ---------------------------------------------------------------------------

TOOL_META = {
    "think":                 ("💭 Agent Thinking",              "reasoning"),
    "plan":                  ("📋 Execution Plan",              "planning"),
    "update_state":          ("📝 Updating Understanding",      "memory"),
    "evaluate_index_viability": ("🧮 Viability Check",         "compute"),
    "compare_indexes":       ("⚖️ Comparing Index Options",     "analysis"),
    "estimate_resources":    ("💾 Estimating Resource Footprint","compute"),
    "google_search":         ("🔍 Google Search",               "search"),
    "ask_user":              ("❓ Asking Clarifying Question",  "terminal"),
    "give_recommendation":   ("✅ Delivering Recommendation",   "terminal"),
}


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("session", {})
    await cl.Message(
        content=(
            "## Couchbase Vector Index Advisor\n\n"
            "Describe your use case — what you're building, the data you have, "
            "how users search, your scale, and any performance requirements.\n\n"
            "I'll reason through the best index architecture, use Google Search "
            "when I need external facts, and ask follow-up questions one at a time "
            "only when something critical is missing."
        )
    ).send()


# ---------------------------------------------------------------------------
# Main message handler
# ---------------------------------------------------------------------------

@cl.on_message
async def on_message(message: cl.Message):
    await _handle(message.content)


async def _handle(user_text: str):
    """Run agent turn and render all output."""
    import asyncio

    session = cl.user_session.get("session") or {}

    loading = cl.Message(content="Analysing your use case...")
    await loading.send()

    # Run the synchronous agent turn in a separate background thread
    # This prevents the blocking API calls from hanging the Chainlit UI
    response = await asyncio.to_thread(run_turn, user_text, session)

    await loading.remove()

    cl.user_session.set("session", session)

    # Render reasoning trace (collapsible steps)
    steps = response.get("steps", [])
    if steps:
        await _render_trace(steps)

    # Route to output handler
    resp_type = response.get("type")
    payload = response.get("payload", {})

    if resp_type == "recommendation":
        await _show_recommendation(payload)
    elif resp_type == "clarification":
        await _ask_questions(payload, session)
    elif resp_type == "text":
        await cl.Message(content=payload.get("message", "")).send()
    else:
        await cl.Message(
            content=f"⚠️ {payload.get('message', 'Something went wrong.')}"
        ).send()


# ---------------------------------------------------------------------------
# Reasoning trace — collapsible cl.Step per tool call
# ---------------------------------------------------------------------------

async def _render_trace(steps: List[Dict[str, Any]]):
    """Render every tool call as a collapsible Chainlit Step.

    Each step shows:
      - Tool name (human-readable)
      - Agent's thought / reason for calling it
      - What the tool returned (result)
    """
    seen = set()

    for step_data in steps:
        tool = step_data.get("tool", "")
        thought = (step_data.get("content") or "").strip()
        args = step_data.get("args", {})
        result = (step_data.get("result") or "").strip()

        # Skip terminal tools — they render their own output below
        if tool in ("ask_user", "give_recommendation"):
            continue

        label, _ = TOOL_META.get(tool, (f"🔧 {tool}", "tool"))

        # Deduplicate identical thoughts (can happen when model emits multiple calls)
        if thought and thought in seen:
            thought = ""
        elif thought:
            seen.add(thought)

        # Build step content
        parts = []

        if tool == "think":
            content = args.get("reasoning", thought) or thought
            parts.append(content)

        elif tool == "plan":
            plan_steps = args.get("steps", [])
            if plan_steps:
                lines = [
                    f"{i}. **{s.get('step', '')}**"
                    + (f"\n   - Tool: `{s['tool']}`" if s.get("tool") else "")
                    + (f"\n   - Why: {s['why']}" if s.get("why") else "")
                    for i, s in enumerate(plan_steps, 1)
                ]
                parts.append("\n".join(lines))
            elif thought:
                parts.append(thought)

        elif tool == "update_state":
            confirmed = args.get("confirmed_facts", {})
            gaps_resolved = args.get("resolved_gaps", [])
            gaps_open = args.get("open_gaps", [])
            summary = args.get("narrative_summary", "")
            lines = []
            if confirmed:
                lines.append("**Confirmed facts:**")
                for k, v in confirmed.items():
                    lines.append(f"  - {k}: {v}")
            if gaps_resolved:
                lines.append("**Resolved gaps:** " + ", ".join(gaps_resolved))
            if gaps_open:
                lines.append("**Still open:** " + ", ".join(gaps_open))
            if summary:
                lines.append(f"**Summary:** {summary}")
            parts.append("\n".join(lines) if lines else thought or "State updated.")

        elif tool == "google_search":
            sources = args.get("sources", [])
            if sources:
                parts.append("**Sources consulted:**\n" + "\n".join(f"- {s}" for s in sources))
            else:
                parts.append(thought or "Performed Google Search.")

        else:
            # Generic tool: show thought + result
            if thought:
                parts.append(f"**Reason:** {thought}")
            if result:
                try:
                    parsed = json.loads(result)
                    pretty = json.dumps(parsed, indent=2)
                    parts.append(f"**Result:**\n```json\n{pretty}\n```")
                except Exception:
                    parts.append(f"**Result:**\n{result}")

        output = "\n\n".join(p for p in parts if p)
        if not output:
            output = "Tool executed."

        async with cl.Step(name=label, type="tool") as step:
            step.output = output


# ---------------------------------------------------------------------------
# Questions — one at a time, inline options + inline free-text
# ---------------------------------------------------------------------------

async def _ask_questions(payload: Dict[str, Any], session: Dict[str, Any]):
    """Present clarifying questions sequentially.

    For each question:
    1. Show the question text, anchor, and why-asking context
    2. Show button options (up to 4) + an "✏️ Other / Type here" button
    3. Wait for the user's click
    4. If they click "Type here", open an inline AskUserMessage (not a search box)
    5. Capture the answer, then move to the next question
    6. After all questions, feed all answers back to the agent
    """
    context_msg = payload.get("message", "")
    if context_msg:
        await cl.Message(content=context_msg).send()

    questions = payload.get("questions", [])
    if not questions:
        await cl.Message(content="Please type your response below.").send()
        return

    collected: List[str] = []

    for q_item in questions:
        question_text = q_item.get("question", "")
        anchor = q_item.get("anchor", "")
        why_asking = q_item.get("why_asking", "")
        options = q_item.get("options") or []

        # --- Question header ---
        header_parts = [f"### {question_text}"]
        if anchor:
            header_parts.append(f"> {anchor}")
        if why_asking:
            header_parts.append(f"*Why this matters: {why_asking}*")

        await cl.Message(content="\n\n".join(header_parts)).send()

        if not options:
            # Fallback: no options provided, go straight to free text
            reply = await cl.AskUserMessage(
                content="Type your answer:",
                timeout=3600,
                raise_on_timeout=False,
            ).send()
            if reply:
                answer = reply.get("output") or reply.get("content") or ""
                if answer:
                    collected.append(f"Q: {question_text}\nA: {answer}")
            continue

        # --- Build action buttons ---
        actions = []
        for opt in options[:4]:
            label = opt.get("label") or opt.get("id") or "?"
            safe_name = "opt_" + "".join(c for c in label[:24] if c.isalnum() or c in ("_", "-"))
            actions.append(
                cl.Action(
                    name=safe_name,
                    payload={"value": label},
                    label=label,
                )
            )

        # Always add "Type here" as the last button
        actions.append(
            cl.Action(
                name="opt_type_here",
                payload={"value": "__type__"},
                label="✏️ Other / Type here",
            )
        )

        # --- Show buttons and wait ---
        res = await cl.AskActionMessage(
            content="Select an option, or type your own answer:",
            actions=actions,
            timeout=3600,
            raise_on_timeout=False,
        ).send()

        if not res:
            # Timeout or dismissal — skip this question
            continue

        chosen = res.get("payload", {}).get("value", "")

        if chosen == "__type__":
            # Open inline text input — renders in the chat thread, not a search box
            free = await cl.AskUserMessage(
                content=f"Your answer to: *{question_text}*",
                timeout=3600,
                raise_on_timeout=False,
            ).send()
            if free:
                answer = free.get("output") or free.get("content") or ""
                if answer:
                    collected.append(f"Q: {question_text}\nA: {answer}")
        else:
            collected.append(f"Q: {question_text}\nA: {chosen}")

    # Feed all answers back to the agent in one turn
    if collected:
        combined = "\n\n".join(collected)
        await _handle(combined)
    else:
        await cl.Message(content="No answers received. Feel free to type your response below.").send()


# ---------------------------------------------------------------------------
# Recommendation display
# ---------------------------------------------------------------------------

async def _show_recommendation(payload: Dict[str, Any]):
    parts = []

    if payload.get("summary"):
        parts.append(f"## Recommendation\n\n{payload['summary']}\n")

    for rec in payload.get("query_pattern_recommendations", []):
        qp = rec.get("query_pattern", "Pattern")
        idx = rec.get("recommended_index", "?")
        parts.append(f"### {qp} → **{idx}**\n")

        if rec.get("reasoning"):
            parts.append(f"**Why this index:**\n{rec['reasoning']}\n")

        elim = rec.get("eliminated_alternatives", {})
        if elim:
            parts.append("**Alternatives considered and eliminated:**")
            for k, v in elim.items():
                parts.append(f"- ~~{k}~~: {v}")
            parts.append("")

        for c in rec.get("caveats", []):
            parts.append(f"> ⚠️ Caveat: {c}")
        parts.append("")

    arch = payload.get("architecture_summary", {})
    if arch:
        parts.append("---\n### Architecture Summary")
        if arch.get("total_indexes"):
            parts.append(f"- **Total indexes:** {arch['total_indexes']}")
        if arch.get("index_types_used"):
            parts.append(f"- **Types used:** {', '.join(arch['index_types_used'])}")
        if arch.get("shared_indexes"):
            parts.append(f"- **Shared indexes:** {arch['shared_indexes']}")
        if arch.get("operational_notes"):
            parts.append(f"- **Operational notes:** {arch['operational_notes']}")

    await cl.Message(content="\n".join(parts)).send()
