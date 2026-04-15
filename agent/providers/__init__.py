"""
agent/providers package.

Exposes a singleton factory that returns the configured LLM provider.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import BaseLLMProvider

_provider: "BaseLLMProvider | None" = None


def get_provider() -> "BaseLLMProvider":
    """Return the singleton LLM provider based on config.LLM_PROVIDER."""
    global _provider
    if _provider is not None:
        return _provider

    import config

    name = config.LLM_PROVIDER.lower()
    if name == "gemini":
        from .gemini import GeminiProvider
        _provider = GeminiProvider()
    elif name == "openai":
        from .openai_provider import OpenAIProvider
        _provider = OpenAIProvider()
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER '{config.LLM_PROVIDER}'. "
            "Supported: gemini, openai"
        )
    return _provider
