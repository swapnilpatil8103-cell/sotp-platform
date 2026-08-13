"""Gemini-backed implementation of the AIAdapter interface.

Reads GEMINI_API_KEY from the environment. If the key is missing, or any SDK
call fails, this adapter raises the typed errors in backend/ai/errors.py --
it never crashes the caller and never returns fabricated content. Callers
(backend/ai/tasks/*) are responsible for turning an AIUnavailableError into
an explicit ABSTAIN result.

No real network call has been exercised against this adapter in this
environment (no GEMINI_API_KEY configured) -- it is wired against the real
google-generativeai SDK and exercised in tests via backend/ai/testing.py's
FakeAIAdapter, which implements the same interface.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from backend.ai.adapter import AIAdapter, AIRecommendation
from backend.ai.errors import AIRateLimitError, AIResponseParseError, AIUnavailableError

DEFAULT_MODEL = "gemini-1.5-pro"


class GeminiAdapter(AIAdapter):
    """Real Gemini adapter. Lazily imports/initializes the SDK so importing
    this module never fails even if google-generativeai isn't installed or
    no key is configured -- failure is deferred to call time and surfaced as
    AIUnavailableError."""

    def __init__(self, api_key: Optional[str] = None, model: str = DEFAULT_MODEL) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("GEMINI_API_KEY", "")
        self.model_name = model
        self._model = None  # lazily constructed on first real call

    def _ensure_model(self):
        if not self.api_key:
            raise AIUnavailableError(
                "GEMINI_API_KEY is not configured. AI features are unavailable; "
                "set GEMINI_API_KEY to enable them."
            )
        if self._model is not None:
            return self._model
        try:
            import google.generativeai as genai
        except ImportError as exc:  # pragma: no cover - depends on env install
            raise AIUnavailableError(f"google-generativeai SDK is not installed: {exc}") from exc

        try:
            genai.configure(api_key=self.api_key)
            self._model = genai.GenerativeModel(self.model_name)
        except Exception as exc:  # pragma: no cover - depends on live SDK
            raise AIUnavailableError(f"Failed to initialize Gemini client: {exc}") from exc
        return self._model

    def generate_text(self, prompt: str) -> str:
        model = self._ensure_model()
        try:
            response = model.generate_content(prompt)
        except Exception as exc:  # pragma: no cover - depends on live SDK
            message = str(exc).lower()
            if "rate" in message or "quota" in message or "429" in message:
                raise AIRateLimitError(f"Gemini rate limit/quota exceeded: {exc}") from exc
            raise AIUnavailableError(f"Gemini call failed: {exc}") from exc

        text = getattr(response, "text", None)
        if not text:
            raise AIResponseParseError("Gemini response contained no text.")
        return text

    def recommend_assumption(self, context: dict[str, Any]) -> AIRecommendation:
        # Thin wrapper retained for AIAdapter interface compatibility; the
        # structured tasks in backend/ai/tasks/assumption_recommendation.py
        # are the supported path and do their own prompt construction +
        # validation. This method is a minimal, best-effort fallback.
        assumption_key = context.get("assumption_key", "unknown")
        prompt = (
            "You are a financial analyst assistant. Based ONLY on the following "
            f"structured context, propose a single numeric value for '{assumption_key}'. "
            "Reply with just the number followed by a one-sentence rationale.\n\n"
            f"Context: {context}"
        )
        text = self.generate_text(prompt)
        # Best-effort parse of a leading number; real structured extraction
        # happens in backend/ai/tasks/assumption_recommendation.py.
        import re

        match = re.search(r"-?\d+(\.\d+)?", text)
        if not match:
            raise AIResponseParseError(f"Could not parse a numeric value from Gemini response: {text!r}")
        return AIRecommendation(
            assumption_key=assumption_key,
            recommended_value=float(match.group(0)),
            rationale=text.strip(),
            confidence=None,
            model=self.model_name,
        )

    def draft_narrative(self, context: dict[str, Any]) -> str:
        prompt = (
            "You are a financial analyst assistant. Using ONLY the structured "
            "data below (do not invent any facts or numbers not present), "
            f"write a short narrative summary.\n\nData: {context}"
        )
        return self.generate_text(prompt)
