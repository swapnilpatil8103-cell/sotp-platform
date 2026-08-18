"""Ollama-backed implementation of the AIAdapter interface.

Calls a local Ollama service (default http://localhost:11434) over its REST
API (POST /api/chat, non-streaming). No API key is required -- Ollama is
local and unauthenticated. If the service is unreachable (connection
refused) or the configured model has not been pulled, this adapter raises
AIUnavailableError with a clear, actionable message; it never crashes the
caller and never returns fabricated content. Callers (backend/ai/tasks/*)
are responsible for turning an AIUnavailableError into an explicit ABSTAIN
result, exactly as with GeminiAdapter.

Exercised in tests via mocked HTTP responses (backend/tests/test_ollama_adapter.py)
and, where a real local Ollama service is available, via an
@pytest.mark.integration test.
"""

from __future__ import annotations

import os
import re
from typing import Any, Optional

import httpx

from backend.ai.adapter import AIAdapter, AIRecommendation
from backend.ai.errors import AIResponseParseError, AIUnavailableError

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1:8b"

# Local inference can be considerably slower than a hosted API, especially on
# first load / CPU-only hardware -- give it much more headroom than Gemini's
# typical short timeout.
DEFAULT_TIMEOUT_SECONDS = 120.0


class OllamaAdapter(AIAdapter):
    """Real Ollama adapter, calling the local /api/chat endpoint."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = (
            base_url if base_url is not None else os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self.model_name = model if model is not None else os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        self.timeout = timeout

    def generate_text(self, prompt: str) -> str:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }

        try:
            response = httpx.post(url, json=payload, timeout=self.timeout)
        except httpx.ConnectError as exc:
            raise AIUnavailableError(
                f"Could not reach Ollama at {self.base_url}. Is the Ollama app "
                f"running? ({exc})"
            ) from exc
        except httpx.TimeoutException as exc:
            raise AIUnavailableError(
                f"Ollama call timed out after {self.timeout}s calling {url}: {exc}"
            ) from exc
        except httpx.HTTPError as exc:  # pragma: no cover - generic network failure
            raise AIUnavailableError(f"Ollama call failed: {exc}") from exc

        if response.status_code == 404:
            raise AIUnavailableError(
                f"Ollama model '{self.model_name}' is not pulled. Run "
                f"`ollama pull {self.model_name}` and try again."
            )

        if response.status_code != 200:
            body = response.text
            if "model" in body.lower() and ("not found" in body.lower() or "not exist" in body.lower()):
                raise AIUnavailableError(
                    f"Ollama model '{self.model_name}' is not pulled. Run "
                    f"`ollama pull {self.model_name}` and try again."
                )
            raise AIUnavailableError(
                f"Ollama call failed with status {response.status_code}: {body}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise AIResponseParseError(f"Ollama response was not valid JSON: {exc}") from exc

        # Some Ollama error responses are HTTP 200 with an "error" field
        # (older/edge-case behavior) rather than a non-200 status.
        if isinstance(data, dict) and data.get("error"):
            error_text = str(data["error"])
            if "not found" in error_text.lower() or "not exist" in error_text.lower():
                raise AIUnavailableError(
                    f"Ollama model '{self.model_name}' is not pulled. Run "
                    f"`ollama pull {self.model_name}` and try again."
                )
            raise AIUnavailableError(f"Ollama call failed: {error_text}")

        message = data.get("message") if isinstance(data, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        if not text:
            raise AIResponseParseError(f"Ollama response contained no message content: {data!r}")
        return text

    def recommend_assumption(self, context: dict[str, Any]) -> AIRecommendation:
        # Mirrors GeminiAdapter.recommend_assumption exactly: thin fallback
        # kept for AIAdapter interface compatibility; the structured tasks
        # in backend/ai/tasks/assumption_recommendation.py are the supported
        # path and do their own prompt construction + validation.
        assumption_key = context.get("assumption_key", "unknown")
        prompt = (
            "You are a financial analyst assistant. Based ONLY on the following "
            f"structured context, propose a single numeric value for '{assumption_key}'. "
            "Reply with just the number followed by a one-sentence rationale.\n\n"
            f"Context: {context}"
        )
        text = self.generate_text(prompt)
        match = re.search(r"-?\d+(\.\d+)?", text)
        if not match:
            raise AIResponseParseError(f"Could not parse a numeric value from Ollama response: {text!r}")
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
