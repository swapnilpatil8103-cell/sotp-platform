"""Unit tests for OllamaAdapter, using mocked HTTP responses -- no real
Ollama service required. See test_ollama_integration for a real-service
smoke test."""

from __future__ import annotations

import httpx
import pytest

from backend.ai.errors import AIResponseParseError, AIUnavailableError
from backend.ai.ollama_adapter import OllamaAdapter


def _adapter(monkeypatch, transport_handler):
    adapter = OllamaAdapter(base_url="http://localhost:11434", model="llama3.1:8b")

    def fake_post(url, json=None, timeout=None):
        request = httpx.Request("POST", url, json=json)
        response = transport_handler(request)
        response.request = request
        return response

    monkeypatch.setattr("backend.ai.ollama_adapter.httpx.post", fake_post)
    return adapter


def test_generate_text_success(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "hello there"}})

    adapter = _adapter(monkeypatch, handler)
    assert adapter.generate_text("hi") == "hello there"


def test_generate_text_connection_refused(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        raise httpx.ConnectError("connection refused", request=httpx.Request("POST", url))

    adapter = OllamaAdapter(base_url="http://localhost:11434", model="llama3.1:8b")
    monkeypatch.setattr("backend.ai.ollama_adapter.httpx.post", fake_post)

    with pytest.raises(AIUnavailableError, match="Ollama"):
        adapter.generate_text("hi")


def test_generate_text_model_not_found_404(monkeypatch):
    def handler(request):
        return httpx.Response(404, json={"error": 'model "llama3.1:8b" not found, try pulling it first'})

    adapter = _adapter(monkeypatch, handler)
    with pytest.raises(AIUnavailableError, match="pull"):
        adapter.generate_text("hi")


def test_generate_text_model_not_found_200_with_error_field(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"error": 'model "llama3.1:8b" not found'})

    adapter = _adapter(monkeypatch, handler)
    with pytest.raises(AIUnavailableError, match="pull"):
        adapter.generate_text("hi")


def test_generate_text_malformed_response(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"unexpected": "shape"})

    adapter = _adapter(monkeypatch, handler)
    with pytest.raises(AIResponseParseError):
        adapter.generate_text("hi")


def test_generate_text_invalid_json(monkeypatch):
    def handler(request):
        return httpx.Response(200, content=b"not json")

    adapter = _adapter(monkeypatch, handler)
    with pytest.raises(AIResponseParseError):
        adapter.generate_text("hi")


def test_generate_text_timeout(monkeypatch):
    def fake_post(url, json=None, timeout=None):
        raise httpx.TimeoutException("timed out", request=httpx.Request("POST", url))

    adapter = OllamaAdapter(base_url="http://localhost:11434", model="llama3.1:8b")
    monkeypatch.setattr("backend.ai.ollama_adapter.httpx.post", fake_post)

    with pytest.raises(AIUnavailableError, match="timed out"):
        adapter.generate_text("hi")


def test_recommend_assumption_parses_number(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "8.5% because of comps."}})

    adapter = _adapter(monkeypatch, handler)
    rec = adapter.recommend_assumption({"assumption_key": "wacc"})
    assert rec.assumption_key == "wacc"
    assert rec.recommended_value == 8.5
    assert rec.model == "llama3.1:8b"


def test_recommend_assumption_unparseable_raises(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "no numbers here"}})

    adapter = _adapter(monkeypatch, handler)
    with pytest.raises(AIResponseParseError):
        adapter.recommend_assumption({"assumption_key": "wacc"})


def test_draft_narrative_returns_text(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"message": {"content": "narrative text"}})

    adapter = _adapter(monkeypatch, handler)
    assert adapter.draft_narrative({"foo": "bar"}) == "narrative text"


def test_defaults_from_env(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://example-host:1234")
    monkeypatch.setenv("OLLAMA_MODEL", "custom-model")
    adapter = OllamaAdapter()
    assert adapter.base_url == "http://example-host:1234"
    assert adapter.model_name == "custom-model"


def test_defaults_without_env(monkeypatch):
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    adapter = OllamaAdapter()
    assert adapter.base_url == "http://localhost:11434"
    assert adapter.model_name == "llama3.1:8b"
