"""
agent/providers/openai_provider.py

LLM provider implementation for OpenAI-compatible APIs
(OpenAI, Azure OpenAI, or any API that follows the Chat Completions spec).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

import config
from .base import BaseLLMProvider, LLMResponse, ToolCall, ToolResult

logger = logging.getLogger(__name__)


class OpenAIProvider(BaseLLMProvider):

    def __init__(self) -> None:
        from openai import OpenAI
        self._client = OpenAI(api_key=config.LLM_API_KEY)
        self._openai_tools = self._build_tools()

    # ------------------------------------------------------------------
    # Tool schema (already in OpenAI format — just wrap with "type")
    # ------------------------------------------------------------------

    @staticmethod
    def _build_tools() -> List[Dict]:
        from tools import ALL_TOOL_SCHEMAS
        return [
            {"type": "function", "function": s["function"]}
            for s in ALL_TOOL_SCHEMAS
        ]

    # ------------------------------------------------------------------
    # BaseLLMProvider interface
    # ------------------------------------------------------------------

    def start_chat(
        self,
        system_prompt: str,
        tools: List[Dict],
        history: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        for msg in history:
            role = "user" if msg["role"] == "user" else "assistant"
            content = msg.get("content") or ""
            if content:
                messages.append({"role": role, "content": content})

        return {
            "messages": messages,
            "model": config.MODEL,
            "tools": self._openai_tools,
            "temperature": config.TEMPERATURE,
        }

    def send_message(self, chat: Dict, message: str) -> LLMResponse:
        chat["messages"].append({"role": "user", "content": message})
        return self._complete(chat)

    def send_tool_results(
        self, chat: Dict, results: List[ToolResult],
    ) -> LLMResponse:
        for r in results:
            content = (
                json.dumps(r.response, default=str)
                if isinstance(r.response, dict) else str(r.response)
            )
            call_id = r.call_id or f"call_{r.name}"
            chat["messages"].append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": content,
            })
        return self._complete(chat)

    def web_search(self, query: str) -> Tuple[str, list]:
        """Fallback web search using the model's own knowledge.

        For providers that lack built-in grounding (Google Search), we ask
        the model directly with a focused prompt.  The quality depends on
        the model's training-data cut-off.
        """
        try:
            search_query = (
                f"Couchbase {query}" if "couchbase" not in query.lower() else query
            )
            resp = self._client.chat.completions.create(
                model=config.MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a technical search assistant. Answer the "
                            "following query with factual, concise information. "
                            "If you are not sure, say so."
                        ),
                    },
                    {"role": "user", "content": search_query},
                ],
                temperature=0.1,
            )
            text = resp.choices[0].message.content or "No results found."
            return text, []
        except Exception as e:
            logger.error("OpenAI web search fallback error: %s", e)
            return f"Search error: {e}", []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _complete(self, chat: Dict) -> LLMResponse:
        resp = self._client.chat.completions.create(
            model=chat["model"],
            messages=chat["messages"],
            tools=chat["tools"],
            temperature=chat["temperature"],
        )
        choice = resp.choices[0]
        msg = choice.message

        # Append assistant message to the running history so follow-up
        # calls (tool results, etc.) have the full context.
        dumped = msg.model_dump(exclude_none=True)
        if "tool_calls" in dumped and not dumped["tool_calls"]:
            del dumped["tool_calls"]
        chat["messages"].append(dumped)

        return self._normalise(choice)

    @staticmethod
    def _normalise(choice: Any) -> LLMResponse:
        msg = choice.message
        finish = choice.finish_reason or "stop"

        if finish == "tool_calls" or (msg.tool_calls and len(msg.tool_calls) > 0):
            calls = []
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                calls.append(ToolCall(
                    name=tc.function.name,
                    args=args,
                    call_id=tc.id or "",
                ))
            return LLMResponse(
                text=msg.content or None,
                tool_calls=calls,
                finish_reason="tool_calls",
            )

        return LLMResponse(
            text=msg.content or None,
            finish_reason="stop" if (msg.content or "").strip() else "empty",
        )
