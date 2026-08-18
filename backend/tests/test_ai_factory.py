"""Unit tests for backend/ai/factory.py's provider-selection logic."""

from __future__ import annotations

import pytest

from backend.ai.factory import get_ai_adapter
from backend.ai.gemini_adapter import GeminiAdapter
from backend.ai.ollama_adapter import OllamaAdapter


def test_default_provider_is_gemini(monkeypatch):
    monkeypatch.delenv("AI_PROVIDER", raising=False)
    adapter = get_ai_adapter()
    assert isinstance(adapter, GeminiAdapter)


def test_env_var_selects_gemini(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    adapter = get_ai_adapter()
    assert isinstance(adapter, GeminiAdapter)


def test_env_var_selects_ollama(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "ollama")
    adapter = get_ai_adapter()
    assert isinstance(adapter, OllamaAdapter)


def test_env_var_case_insensitive(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "OLLAMA")
    adapter = get_ai_adapter()
    assert isinstance(adapter, OllamaAdapter)


def test_explicit_argument_overrides_env(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    adapter = get_ai_adapter(provider="ollama")
    assert isinstance(adapter, OllamaAdapter)


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "bogus")
    with pytest.raises(ValueError, match="Unknown AI_PROVIDER"):
        get_ai_adapter()
