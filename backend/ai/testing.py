"""Test double for AIAdapter.

FakeAIAdapter implements the same interface as GeminiAdapter with
deterministic, canned responses so unit tests never depend on a live Gemini
API key or network access. Use it wherever tests need an AIAdapter.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from backend.ai.adapter import AIAdapter, AIRecommendation
from backend.ai.errors import AIRateLimitError, AIUnavailableError


class FakeAIAdapter(AIAdapter):
    """Deterministic AIAdapter test double.

    - ``fixed_text``: if set, ``generate_text`` always returns this string
      (or the result of calling it, if a callable was supplied).
    - ``raise_unavailable`` / ``raise_rate_limit``: simulate degraded-mode
      paths without needing a real failure.
    """

    def __init__(
        self,
        fixed_text: str | Callable[[str], str] = "",
        raise_unavailable: bool = False,
        raise_rate_limit: bool = False,
    ) -> None:
        self.fixed_text = fixed_text
        self.raise_unavailable = raise_unavailable
        self.raise_rate_limit = raise_rate_limit
        self.calls: list[str] = []  # prompts seen, for assertions in tests

    def generate_text(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self.raise_unavailable:
            raise AIUnavailableError("FakeAIAdapter configured to simulate unavailability.")
        if self.raise_rate_limit:
            raise AIRateLimitError("FakeAIAdapter configured to simulate a rate limit.")
        if callable(self.fixed_text):
            return self.fixed_text(prompt)
        return self.fixed_text

    def recommend_assumption(self, context: dict[str, Any]) -> AIRecommendation:
        text = self.generate_text(str(context))
        return AIRecommendation(
            assumption_key=context.get("assumption_key", "unknown"),
            recommended_value=0.0,
            rationale=text or "fake rationale",
            confidence=0.5,
            model="fake-model",
        )

    def draft_narrative(self, context: dict[str, Any]) -> str:
        return self.generate_text(str(context))
