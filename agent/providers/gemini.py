"""
agent/providers/gemini.py

LLM provider implementation for Google Gemini (google-genai SDK).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

import config
from .base import BaseLLMProvider, LLMResponse, ToolCall, ToolResult

logger = logging.getLogger(__name__)

_TYPE_MAP = {
    "string":  "STRING",
    "integer": "INTEGER",
    "number":  "NUMBER",
    "boolean": "BOOLEAN",
    "object":  "OBJECT",
    "array":   "ARRAY",
}


class GeminiProvider(BaseLLMProvider):

    def __init__(self) -> None:
        from google import genai
        self._genai = genai
        self._types = genai.types
        self._client = genai.Client(api_key=config.LLM_API_KEY)
        self._tools = self._build_tools()

    # ------------------------------------------------------------------
    # Schema conversion  (OpenAI JSON-Schema → Gemini types.Schema)
    # ------------------------------------------------------------------

    def _convert_schema(self, schema: dict) -> Any:
        if not schema:
            return self._types.Schema(type="OBJECT")
        gemini_type = _TYPE_MAP.get(schema.get("type", "string"), "STRING")
        properties = (
            {k: self._convert_schema(v) for k, v in schema["properties"].items()}
            if "properties" in schema else None
        )
        items = (
            self._convert_schema(schema["items"]) if "items" in schema else None
        )
        return self._types.Schema(
            type=gemini_type,
            description=schema.get("description"),
            properties=properties,
            required=schema.get("required"),
            items=items,
            enum=schema.get("enum"),
        )

    def _build_tools(self) -> list:
        from tools import ALL_TOOL_SCHEMAS
        declarations = []
        for schema in ALL_TOOL_SCHEMAS:
            fn = schema["function"]
            params = fn.get("parameters", {"type": "object", "properties": {}})
            declarations.append(
                self._types.FunctionDeclaration(
                    name=fn["name"],
                    description=fn["description"],
                    parameters=self._convert_schema(params),
                )
            )
        return [self._types.Tool(function_declarations=declarations)]

    # ------------------------------------------------------------------
    # History conversion  (internal dicts → Gemini Content objects)
    # ------------------------------------------------------------------

    def _to_history(self, history: List[Dict[str, str]]) -> list:
        result = []
        for msg in history:
            role = "user" if msg["role"] == "user" else "model"
            content = msg.get("content") or ""
            if content:
                result.append(
                    self._types.Content(
                        role=role,
                        parts=[self._types.Part(text=content)],
                    )
                )
        return result

    # ------------------------------------------------------------------
    # BaseLLMProvider interface
    # ------------------------------------------------------------------

    def start_chat(
        self,
        system_prompt: str,
        tools: List[Dict],
        history: List[Dict[str, str]],
    ) -> Any:
        gen_config = self._types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=self._tools,
            temperature=config.TEMPERATURE,
            thinking_config=self._types.ThinkingConfig(
                thinking_budget=config.THINKING_BUDGET,
            ),
        )
        return self._client.chats.create(
            model=config.MODEL,
            config=gen_config,
            history=self._to_history(history),
        )

    def send_message(self, chat: Any, message: str) -> LLMResponse:
        resp = chat.send_message(message)
        return self._normalise(resp)

    def send_tool_results(
        self, chat: Any, results: List[ToolResult],
    ) -> LLMResponse:
        parts = [
            self._types.Part.from_function_response(
                name=r.name,
                response=r.response if isinstance(r.response, dict) else {"result": str(r.response)},
            )
            for r in results
        ]
        resp = chat.send_message(parts)
        return self._normalise(resp)

    def web_search(self, query: str) -> Tuple[str, list]:
        try:
            grounding_tool = self._types.Tool(
                google_search=self._types.GoogleSearch(),
            )
            search_query = (
                f"Couchbase {query}" if "couchbase" not in query.lower() else query
            )
            response = self._client.models.generate_content(
                model=config.MODEL,
                contents=search_query,
                config=self._types.GenerateContentConfig(
                    tools=[grounding_tool],
                    temperature=0.1,
                ),
            )
            text_result = response.text or "No results found."
            source_urls: list = []
            try:
                meta = response.candidates[0].grounding_metadata
                if meta and meta.grounding_chunks:
                    for chunk in meta.grounding_chunks:
                        if chunk.web and chunk.web.uri:
                            source_urls.append({
                                "url": chunk.web.uri,
                                "title": chunk.web.title or chunk.web.uri,
                            })
            except Exception as e:
                logger.warning("Could not extract grounding metadata: %s", e)
            return text_result, source_urls
        except Exception as e:
            logger.error("Google Search grounding error: %s", e)
            return f"Search error: {e}", []

    # ------------------------------------------------------------------
    # Response normalisation
    # ------------------------------------------------------------------

    def _normalise(self, resp: Any) -> LLMResponse:
        candidate = (
            resp.candidates[0] if resp and resp.candidates else None
        )
        finish_reason = (
            str(getattr(candidate, "finish_reason", "")).upper()
            if candidate else "NO_CANDIDATE"
        )

        is_bad = (
            not candidate
            or not candidate.content
            or not candidate.content.parts
            or "MALFORMED" in finish_reason
            or "RECITATION" in finish_reason
        )
        if is_bad:
            return LLMResponse(finish_reason="error")

        parts = candidate.content.parts
        fn_parts = [p for p in parts if getattr(p, "function_call", None)]

        # Extract thought text (text before/around function calls)
        thought = ""
        for p in parts:
            if not getattr(p, "function_call", None):
                txt = getattr(p, "text", "") or ""
                if txt.strip():
                    thought = txt.strip()
                    break

        if not fn_parts:
            text = resp.text or ""
            return LLMResponse(
                text=text if text.strip() else None,
                finish_reason="stop" if text.strip() else "empty",
            )

        tool_calls = []
        for p in fn_parts:
            fn = p.function_call
            tool_calls.append(ToolCall(
                name=fn.name,
                args={k: v for k, v in fn.args.items()},
            ))

        return LLMResponse(
            text=thought or None,
            tool_calls=tool_calls,
            finish_reason="tool_calls",
        )
