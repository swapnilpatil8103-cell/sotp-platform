"""Adapter selection factory.

Callers that previously hardcoded `GeminiAdapter()` should call
`get_ai_adapter()` instead, so the AI provider is swappable via the
`AI_PROVIDER` env var without touching call sites. Default is "gemini" to
preserve existing behavior for anyone who hasn't set the var.
"""

from __future__ import annotations

import os

from backend.ai.adapter import AIAdapter

SUPPORTED_PROVIDERS = ("gemini", "ollama")


def get_ai_adapter(provider: str | None = None) -> AIAdapter:
    """Return the configured AIAdapter implementation.

    provider: explicit override; if None, reads the AI_PROVIDER env var
    (default "gemini"). Raises ValueError for an unrecognized provider name
    rather than silently falling back, so misconfiguration is loud.
    """
    resolved = (provider if provider is not None else os.environ.get("AI_PROVIDER", "gemini")).strip().lower()

    if resolved == "gemini":
        from backend.ai.gemini_adapter import GeminiAdapter

        return GeminiAdapter()
    if resolved == "ollama":
        from backend.ai.ollama_adapter import OllamaAdapter

        return OllamaAdapter()

    raise ValueError(
        f"Unknown AI_PROVIDER {resolved!r}; supported providers are {SUPPORTED_PROVIDERS}."
    )
