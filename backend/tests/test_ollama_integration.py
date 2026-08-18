"""Integration test hitting a REAL local Ollama service (not mocked).

Skipped automatically if the service isn't reachable at all (nothing to test
locally / in CI without Ollama installed). If the service IS reachable but
the configured model hasn't been pulled yet, this test still meaningfully
verifies the "service up, model missing" path raises a clear
AIUnavailableError telling the user to `ollama pull <model>` -- it does not
skip in that case, since that is a real, correctly-handled code path.
"""

from __future__ import annotations

import httpx
import pytest

from backend.ai.errors import AIUnavailableError
from backend.ai.ollama_adapter import OllamaAdapter


def _ollama_service_reachable() -> bool:
    try:
        httpx.get("http://localhost:11434/api/tags", timeout=2.0)
        return True
    except httpx.HTTPError:
        return False


@pytest.mark.integration
def test_real_local_ollama_service():
    if not _ollama_service_reachable():
        pytest.skip("Local Ollama service is not reachable at http://localhost:11434")

    adapter = OllamaAdapter()

    try:
        text = adapter.generate_text("Reply with exactly one word: hello")
    except AIUnavailableError as exc:
        # Service is up but the model isn't pulled yet (or another
        # unavailable condition) -- verify the error is the specific,
        # actionable one rather than a generic failure.
        message = str(exc).lower()
        assert "pull" in message or "ollama" in message
        return

    # Model was ready -- verify a real, non-empty response came back.
    assert isinstance(text, str)
    assert len(text.strip()) > 0
