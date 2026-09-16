"""Unit tests for backend/services/macro_data_client.py -- mocked HTTP only,
no real network calls (real-network verification against London Strategic
Edge's live API was done manually during development, not in CI)."""

from __future__ import annotations

import pytest

from backend.services.macro_data_client import (
    MacroDataClient,
    MacroDataNotFoundError,
    MacroDataUnavailableError,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_missing_api_key_raises_unavailable(monkeypatch):
    monkeypatch.delenv("LSE_API_KEY", raising=False)
    client = MacroDataClient(api_key="")
    with pytest.raises(MacroDataUnavailableError):
        client.get_risk_free_rate_suggestion("US10Y")


def test_successful_suggestion_parses_latest_row(monkeypatch, tmp_path):
    client = MacroDataClient(api_key="test-key", base_url="https://fake.example")
    client.cache.cache_dir = tmp_path

    def fake_get(url, params, headers, timeout):
        assert headers["x-api-key"] == "test-key"
        assert params == {"symbol": "US10Y", "order": "desc", "limit": 1}
        return _FakeResponse(200, [{"symbol": "US10Y", "date": "2026-09-10", "close": 4.919}])

    monkeypatch.setattr("backend.services.macro_data_client.httpx.get", fake_get)

    result = client.get_risk_free_rate_suggestion("US10Y")
    assert result.symbol == "US10Y"
    assert result.rate_pct == pytest.approx(0.04919)
    assert result.as_of_date == "2026-09-10"
    assert result.is_suggestion_not_authoritative is True


def test_401_raises_unavailable_not_crash(monkeypatch, tmp_path):
    client = MacroDataClient(api_key="bad-key", base_url="https://fake.example")
    client.cache.cache_dir = tmp_path
    monkeypatch.setattr(
        "backend.services.macro_data_client.httpx.get",
        lambda *a, **kw: _FakeResponse(401, {"error": "Unauthorized"}),
    )
    with pytest.raises(MacroDataUnavailableError):
        client.get_risk_free_rate_suggestion("US10Y")


def test_empty_rows_raises_not_found(monkeypatch, tmp_path):
    client = MacroDataClient(api_key="test-key", base_url="https://fake.example")
    client.cache.cache_dir = tmp_path
    monkeypatch.setattr("backend.services.macro_data_client.httpx.get", lambda *a, **kw: _FakeResponse(200, []))
    with pytest.raises(MacroDataNotFoundError):
        client.get_risk_free_rate_suggestion("US10Y")


def test_result_caches_and_avoids_second_network_call(monkeypatch, tmp_path):
    client = MacroDataClient(api_key="test-key", base_url="https://fake.example")
    client.cache.cache_dir = tmp_path
    calls = {"n": 0}

    def fake_get(*a, **kw):
        calls["n"] += 1
        return _FakeResponse(200, [{"symbol": "US10Y", "date": "2026-09-10", "close": 4.919}])

    monkeypatch.setattr("backend.services.macro_data_client.httpx.get", fake_get)

    client.get_risk_free_rate_suggestion("US10Y")
    client.get_risk_free_rate_suggestion("US10Y")
    assert calls["n"] == 1
