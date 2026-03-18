"""
Chainlit UI for the Couchbase Vector Index Advisor.

Key behaviours:
- Agent reasoning is shown as collapsible cl.Step dropdowns
- Clarifying questions are asked ONE AT A TIME with inline button options
- "✏️ Other / Type here" opens an inline text input
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


import chainlit as cl

import config  # loads .env
from agent import run_turn
from storage import save_turn


# ---------------------------------------------------------------------------
# Tool → human-readable label
# ---------------------------------------------------------------------------

TOOL_META = {
    "think":                    ("Agent Thinking",               "reasoning"),
    "plan":                     ("Execution Plan",               "planning"),
    "update_state":             ("Updating Understanding",       "memory"),
    "use_case_search":          ("Use Case Library Search",      "search"),
    "evaluate_index_viability": ("Viability Check",              "compute"),
    "compare_indexes":          ("Comparing Index Options",      "analysis"),
    "get_default_parameters":   ("Calculating Parameters",       "compute"),
    "web_search":               ("Web Search",                   "search"),
    "ask_user":                 ("Asking Clarifying Question",   "terminal"),
    "give_recommendation":      ("Delivering Recommendation",    "terminal"),
    "give_performance_profile": ("Delivering Perf Profile",      "terminal"),
}


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

@cl.on_chat_start
async def on_chat_start():
    import asyncio
    cl.user_session.set("session", {})

    # Optimized delay for AWS reliability (websocket readiness)

    await cl.Message(
        content=(
            "### **Welcome to the Couchbase Vector Advisor**\n\n"
            "I am here to help you find the most efficient and cost-effective way to build search into your application. "
            "Whether you are just starting out, preparing for massive growth, or simply have questions about Couchbase "
            "vector indexes, I can guide you to the right setup for your needs.\n\n"
            "--- \n\n"
            "**How to get started:**\n"
            "Simply describe your project or paste your use case below. Tell me a bit about what you are building, "
            "the amount of data you expect, and your specific goals for speed or accuracy.\n\n"
            "**From our conversation, I will:**\n"
            "* **Recommend the best index** for your specific scenario.\n"
            "* **Identify the simplest path** based on your current setup.\n"
            "* **Provide expert answers** to any questions regarding index architecture.\n\n"
            "Please share your use case or ask a question to begin!"
        )
    ).send()

    session_id = cl.context.session.id
    await cl.Message(
        content=(
            f"🔖 **Session ID** — share this if you report an issue:\n"
            f"```\n{session_id}\n```"
        ),
        author="System",
    ).send()



# ---------------------------------------------------------------------------
# Main message handler
# ---------------------------------------------------------------------------

@cl.on_message
async def on_message(message: cl.Message):
    await _handle(message.content)


async def _handle(user_text: str):
    """Run one agent turn and render the output."""
    import asyncio

    session = cl.user_session.get("session") or {}

    # Show a plain chat-bubble loading indicator — no step/tool chrome
    loading_msg = cl.Message(content="Analysing...")
    await loading_msg.send()

    # Run the blocking agent call in a background thread to keep the UI responsive
    response = await asyncio.to_thread(run_turn, user_text, session)

    # Fire the removal in the background — don't block rendering on a server round-trip
    asyncio.ensure_future(loading_msg.remove())

    cl.user_session.set("session", session)

    # Persist the turn to Couchbase (fire-and-forget, silent on failure)
    try:
        session_id = cl.context.session.id
        await asyncio.to_thread(
            save_turn,
            session_id=session_id,
            user_message=user_text,
            response_type=response.get("type", "unknown"),
            response_payload=response.get("payload", {}),
            reasoning_trace=response.get("steps", []),
            state_snapshot=dict(session.get("state", {})),
        )
    except Exception as _storage_err:
        logger.error("Storage hook failed: %s", _storage_err)

    # Render the reasoning trace
    if response.get("steps"):
        await _render_trace(response["steps"])

    # Route to the right output handler
    resp_type = response.get("type")
    payload = response.get("payload", {})

    if resp_type == "recommendation":
        await _show_recommendation(payload)
    elif resp_type == "performance_profile":
        await _show_performance_profile(payload)
    elif resp_type == "clarification":
        await _ask_questions(payload, session)
    elif resp_type == "text":
        await cl.Message(content=payload.get("message", "")).send()
    else:
        await cl.Message(
            content=f"⚠️ {payload.get('message', 'Something went wrong.')}"
        ).send()


# ---------------------------------------------------------------------------
# Reasoning trace — one collapsible cl.Step per tool call
# ---------------------------------------------------------------------------

async def _render_trace(steps: List[Dict[str, Any]]):
    """Render each tool call as a collapsible step in the UI."""
    seen = set()

    for step_data in steps:
        tool = step_data.get("tool", "")
        thought = (step_data.get("content") or "").strip()
        args = step_data.get("args", {})
        result = (step_data.get("result") or "").strip()

        # Terminal tools render their own output below — skip them here
        if tool in ("ask_user", "give_recommendation", "give_performance_profile"):
            continue

        label, _ = TOOL_META.get(tool, (f"{tool}", "tool"))

        # Deduplicate identical thoughts
        if thought and thought in seen:
            thought = ""
        elif thought:
            seen.add(thought)

        parts = []

        if tool == "think":
            parts.append(args.get("reasoning", thought) or thought)

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
            lines = []
            confirmed = args.get("confirmed_facts", {})
            if confirmed:
                lines.append("**Confirmed facts:**")
                for k, v in confirmed.items():
                    lines.append(f"  - {k}: {v}")
            gaps_resolved = args.get("resolved_gaps", [])
            if gaps_resolved:
                lines.append("**Resolved gaps:** " + ", ".join(gaps_resolved))
            gaps_open = args.get("open_gaps", [])
            if gaps_open:
                lines.append("**Still open:** " + ", ".join(gaps_open))
            summary = args.get("narrative_summary", "")
            if summary:
                lines.append(f"**Summary:** {summary}")
            parts.append("\n".join(lines) if lines else thought or "State updated.")

        elif tool == "web_search":
            query = args.get("query", "")
            search_parts = []
            if query:
                search_parts.append(f"**Query:** `{query}`")
            if thought:
                search_parts.append(thought)

            # Show source URLs if available
            source_urls = step_data.get("source_urls", [])
            if source_urls:
                links = "\n".join(
                    f"  {i}. [{s.get('title', s['url'])}]({s['url']})"
                    for i, s in enumerate(source_urls, 1)
                )
                search_parts.append(f"**Sources consulted:**\n{links}")

            parts.extend(search_parts)

        else:
            # Generic: show reason + result
            if thought:
                parts.append(f"**Reason:** {thought}")
            if result:
                try:
                    parsed = json.loads(result)
                    pretty = json.dumps(parsed, indent=2)
                    parts.append(f"**Result:**\n```json\n{pretty}\n```")
                except Exception:
                    parts.append(f"**Result:**\n{result}")

        output = "\n\n".join(p for p in parts if p) or "Tool executed."

        async with cl.Step(name=label, type="tool") as step:
            step.output = output


# ---------------------------------------------------------------------------
# Clarifying questions — one at a time, with inline options + free text
# ---------------------------------------------------------------------------

async def _ask_questions(payload: Dict[str, Any], session: Dict[str, Any]):
    """Present each clarifying question with button options.
    After all answers are collected, feed them back to the agent in one turn.
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

        # Show the question header
        header_parts = [f"### {question_text}"]
        if anchor:
            header_parts.append(f"> {anchor}")
        if why_asking:
            header_parts.append(f"*Why this matters: {why_asking}*")
        await cl.Message(content="\n\n".join(header_parts)).send()

        if not options:
            # No options — go straight to free text
            reply = await cl.AskUserMessage(
                content="Type your answer:", timeout=3600, raise_on_timeout=False
            ).send()
            if reply:
                answer = reply.get("output") or reply.get("content") or ""
                if answer:
                    collected.append(f"Q: {question_text}\nA: {answer}")
            continue

        # Build button options — filter out any "Other/type" options the LLM added,
        # since the UI always appends exactly ONE ✏️ fallback button automatically.
        _other_keywords = ("other", "type here", "specify", "something else", "fill in", "custom")
        filtered_options = [
            opt for opt in options
            if not any(kw in (opt.get("label") or "").lower() for kw in _other_keywords)
        ]

        actions = []
        for opt in filtered_options[:4]:
            label = opt.get("label") or opt.get("id") or "?"
            safe_name = "opt_" + "".join(c for c in label[:24] if c.isalnum() or c in ("_", "-"))
            actions.append(cl.Action(name=safe_name, payload={"value": label}, label=label))

        # Always add a free-text fallback button
        actions.append(
            cl.Action(name="opt_type_here", payload={"value": "__type__"}, label="✏️ Other / Type here")
        )

        res = await cl.AskActionMessage(
            content="Select an option, or type your own answer:",
            actions=actions,
            timeout=3600,
            raise_on_timeout=False,
        ).send()

        if not res:
            continue  # Timeout — skip this question

        chosen = res.get("payload", {}).get("value", "")

        if chosen == "__type__":
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

    if collected:
        await _handle("\n\n".join(collected))
    else:
        await cl.Message(content="No answers received. Feel free to type below.").send()


# ---------------------------------------------------------------------------
# Recommendation display
# ---------------------------------------------------------------------------

async def _show_recommendation(payload: Dict[str, Any]):
    """Format and display the final index recommendation."""
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

    next_steps = payload.get("next_steps", [])
    if next_steps:
        parts.append("\n---\n### 💡 What I can help with next")
        for i, step in enumerate(next_steps, 1):
            parts.append(f"{i}. {step}")

    await cl.Message(content="\n".join(parts)).send()


async def _show_performance_profile(payload: Dict[str, Any]):
    """Render the performance requirements profile as a formatted card."""
    _PRIORITY_BADGE = {"primary": "🥇 Primary", "secondary": "🥈 Secondary", "tertiary": "🥉 Tertiary"}

    parts = ["## Performance Requirements Profile\n"]

    domain_note = payload.get("domain_inference", "")
    if domain_note:
        parts.append(f"> {domain_note}\n")

    metrics = payload.get("metrics", [])
    if metrics:
        parts.append("| Priority | Metric | Bin | Target | Rationale |")
        parts.append("|---|---|---|---|---|")
        for m in metrics:
            badge   = _PRIORITY_BADGE.get(m.get("priority", ""), m.get("priority", ""))
            metric  = m.get("metric", "")
            bin_val = m.get("bin", "Unknown")
            target  = m.get("target_range", "TBD")
            reason  = m.get("rationale", "")
            parts.append(f"| {badge} | **{metric}** | `{bin_val}` | `{target}` | {reason} |")

    trade_off = payload.get("trade_off_note", "")
    if trade_off:
        parts.append(f"\n---\n> ⚖️ **Key trade-off:** {trade_off}")

    await cl.Message(content="\n".join(parts)).send()
