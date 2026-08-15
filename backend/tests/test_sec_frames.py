"""Unit tests for SECConnector.get_frame -- URL construction, caching, typed
errors -- using mocked HTTP (no real network access). Also a real-network
integration test, marked so it can be skipped in CI.
"""

from __future__ import annotations

import os

import httpx
import pytest

from backend.services.cache import FileCache
from backend.services.sec_client import (
    FRAMES_URL_TMPL,
    SECConnector,
    SECNotFoundError,
    SECUnavailableError,
)

FAKE_FRAME_JSON = {
    "tag": "Revenues",
    "ccp": "CY2023Q4",
    "uom": "USD",
    "data": [
        {"cik": 320193, "entityName": "APPLE INC", "val": 100000000, "end": "2023-12-31"},
        {"cik": 789019, "entityName": "MICROSOFT CORP", "val": 90000000, "end": "2023-12-31"},
    ],
}


@pytest.fixture
def sec_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Suite test@example.com")
    return SECConnector(cache=FileCache(cache_dir=tmp_path))


def test_frame_period_instant_quarter():
    assert SECConnector.frame_period(2023, 4, instant=True) == "CY2023Q4I"


def test_frame_period_duration_quarter():
    assert SECConnector.frame_period(2023, 4, instant=False) == "CY2023Q4"


def test_frame_period_duration_full_year():
    assert SECConnector.frame_period(2023, None, instant=False) == "CY2023"


def test_frame_period_full_year_instant_raises():
    with pytest.raises(ValueError):
        SECConnector.frame_period(2023, None, instant=True)


def test_frame_period_invalid_quarter_raises():
    with pytest.raises(ValueError):
        SECConnector.frame_period(2023, 5, instant=False)


def test_get_frame_constructs_duration_quarter_url(sec_client, monkeypatch):
    captured = {}

    def fake_fetch(url, **kwargs):
        captured["url"] = url
        return FAKE_FRAME_JSON

    monkeypatch.setattr(sec_client, "_fetch_json", fake_fetch)
    result = sec_client.get_frame("Revenues", 2023, 4, instant=False)
    assert captured["url"] == FRAMES_URL_TMPL.format(
        taxonomy="us-gaap", tag="Revenues", unit="USD", period="CY2023Q4"
    )
    assert result == FAKE_FRAME_JSON


def test_get_frame_constructs_instant_url(sec_client, monkeypatch):
    captured = {}

    def fake_fetch(url, **kwargs):
        captured["url"] = url
        return FAKE_FRAME_JSON

    monkeypatch.setattr(sec_client, "_fetch_json", fake_fetch)
    sec_client.get_frame("Assets", 2023, 4, instant=True)
    assert captured["url"] == FRAMES_URL_TMPL.format(
        taxonomy="us-gaap", tag="Assets", unit="USD", period="CY2023Q4I"
    )


def test_get_frame_constructs_full_year_url(sec_client, monkeypatch):
    captured = {}

    def fake_fetch(url, **kwargs):
        captured["url"] = url
        return FAKE_FRAME_JSON

    monkeypatch.setattr(sec_client, "_fetch_json", fake_fetch)
    sec_client.get_frame("Revenues", 2023, None, instant=False)
    assert captured["url"] == FRAMES_URL_TMPL.format(
        taxonomy="us-gaap", tag="Revenues", unit="USD", period="CY2023"
    )


def test_get_frame_uses_cache(sec_client, monkeypatch):
    url = FRAMES_URL_TMPL.format(taxonomy="us-gaap", tag="Revenues", unit="USD", period="CY2023Q4")
    sec_client._cache.set(url, FAKE_FRAME_JSON)

    def fail_if_called(*a, **k):
        raise AssertionError("should not hit the network when cache has a fresh entry")

    monkeypatch.setattr(httpx, "get", fail_if_called)
    result = sec_client.get_frame("Revenues", 2023, 4, instant=False)
    assert result == FAKE_FRAME_JSON


def test_get_frame_raises_not_found_on_404(sec_client, monkeypatch):
    class FakeResp:
        status_code = 404

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())
    with pytest.raises(SECNotFoundError):
        sec_client.get_frame("NotARealConcept", 2023, 4, instant=False)


def test_get_frame_raises_unavailable_on_5xx(sec_client, monkeypatch):
    class FakeResp:
        status_code = 503

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())
    with pytest.raises(SECUnavailableError):
        sec_client.get_frame("Revenues", 2023, 4, instant=False)


@pytest.mark.integration
def test_real_sec_frames_revenues_recent_quarter(tmp_path):
    """Hits the real SEC frames API for Revenues, a recent completed quarter.
    Requires SEC_USER_AGENT to be set in the environment."""
    if not os.environ.get("SEC_USER_AGENT"):
        pytest.skip("SEC_USER_AGENT not set; skipping live SEC integration test")

    client = SECConnector(cache=FileCache(cache_dir=tmp_path))
    frame = client.get_frame("Revenues", 2024, 2, instant=False)
    data = frame.get("data", [])
    assert len(data) > 500  # thousands of filers typically report Revenues each quarter

    aapl_cik = int(client.get_company_cik("AAPL"))
    matches = [d for d in data if d.get("cik") == aapl_cik]
    # AAPL may or may not use the plain "Revenues" tag for a given quarter
    # (ASC 606 tag variants exist) -- record whichever way it goes rather than
    # asserting a specific outcome, since this test's job is to prove the
    # frames endpoint returns real, sizeable data.
    print(f"Revenues CY2024Q2 frame: {len(data)} companies; AAPL present: {bool(matches)}")
