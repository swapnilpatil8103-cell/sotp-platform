import pytest

from backend.ai.errors import AIRateLimitError, AIUnavailableError
from backend.ai.gemini_adapter import GeminiAdapter
from backend.ai.testing import FakeAIAdapter


def test_gemini_adapter_unavailable_without_api_key():
    adapter = GeminiAdapter(api_key="")
    with pytest.raises(AIUnavailableError):
        adapter.generate_text("hello")


def test_fake_adapter_returns_fixed_text():
    adapter = FakeAIAdapter(fixed_text="hello world")
    assert adapter.generate_text("prompt") == "hello world"
    assert adapter.calls == ["prompt"]


def test_fake_adapter_simulates_unavailable():
    adapter = FakeAIAdapter(raise_unavailable=True)
    with pytest.raises(AIUnavailableError):
        adapter.generate_text("prompt")


def test_fake_adapter_simulates_rate_limit():
    adapter = FakeAIAdapter(raise_rate_limit=True)
    with pytest.raises(AIRateLimitError):
        adapter.generate_text("prompt")


def test_fake_adapter_recommend_assumption_and_draft_narrative():
    adapter = FakeAIAdapter(fixed_text="some text")
    rec = adapter.recommend_assumption({"assumption_key": "wacc"})
    assert rec.assumption_key == "wacc"
    narrative = adapter.draft_narrative({"foo": "bar"})
    assert narrative == "some text"
