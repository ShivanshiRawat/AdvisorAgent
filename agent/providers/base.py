"""
agent/providers/base.py

Abstract base class and shared data types for LLM providers.
Every provider (Gemini, OpenAI, etc.) implements this interface so the
reasoning loop remains provider-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ToolCall:
    """A single tool/function call requested by the model."""
    name: str
    args: Dict[str, Any]
    call_id: str = ""


@dataclass
class ToolResult:
    """Result of executing a tool, sent back to the model."""
    call_id: str
    name: str
    response: Any


@dataclass
class LLMResponse:
    """Normalised response from any LLM provider."""
    text: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"


class BaseLLMProvider(ABC):
    """Interface that every LLM provider must implement."""

    @abstractmethod
    def start_chat(
        self,
        system_prompt: str,
        tools: List[Dict],
        history: List[Dict[str, str]],
    ) -> Any:
        """Initialise a chat session and return a provider-specific handle."""

    @abstractmethod
    def send_message(self, chat: Any, message: str) -> LLMResponse:
        """Send a plain-text user message and return a normalised response."""

    @abstractmethod
    def send_tool_results(
        self, chat: Any, results: List[ToolResult],
    ) -> LLMResponse:
        """Return tool execution results to the model."""

    @abstractmethod
    def web_search(self, query: str) -> Tuple[str, list]:
        """Provider-specific web search. Returns (text_result, source_urls)."""
