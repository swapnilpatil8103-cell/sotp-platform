"""Unit tests for backend.services.sec_client -- CIK lookup logic against a mocked
ticker file (no real network access). Also a real-network integration test,
marked so it can be skipped in CI.
"""

import os

import pytest

from backend.services.sec_client import SECClient, SECNotFoundError


FAKE_TICKERS_JSON = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}


@pytest.fixture
def sec_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Suite test@example.com")
    from backend.services.cache import FileCache

    client = SECClient(cache=FileCache(cache_dir=tmp_path))
    return client


def test_get_cik_resolves_known_ticker(sec_client, monkeypatch):
    monkeypatch.setattr(sec_client, "_fetch_json", lambda *a, **k: FAKE_TICKERS_JSON)
    cik = sec_client.get_cik("AAPL")
    assert cik == "0000320193"


def test_get_cik_is_case_insensitive(sec_client, monkeypatch):
    monkeypatch.setattr(sec_client, "_fetch_json", lambda *a, **k: FAKE_TICKERS_JSON)
    cik = sec_client.get_cik("aapl")
    assert cik == "0000320193"


def test_get_cik_raises_not_found_for_unknown_ticker(sec_client, monkeypatch):
    monkeypatch.setattr(sec_client, "_fetch_json", lambda *a, **k: FAKE_TICKERS_JSON)
    with pytest.raises(SECNotFoundError):
        sec_client.get_cik("NOTAREALTICKERXYZ")


def test_client_requires_sec_user_agent(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    from backend.services.cache import FileCache
    from backend.services.sec_client import SECError

    with pytest.raises(SECError):
        SECClient(cache=FileCache(cache_dir=tmp_path))


@pytest.mark.integration
def test_real_sec_lookup_for_aapl(tmp_path):
    """Hits real SEC EDGAR. Requires SEC_USER_AGENT to be set in the environment."""
    if not os.environ.get("SEC_USER_AGENT"):
        pytest.skip("SEC_USER_AGENT not set; skipping live SEC integration test")

    from backend.services.cache import FileCache

    client = SECClient(cache=FileCache(cache_dir=tmp_path))
    cik = client.get_cik("AAPL")
    assert cik == "0000320193"

    submissions = client.get_submissions(cik)
    assert submissions.get("name", "").lower().startswith("apple")

    filings = client.get_latest_filings(cik, form_types=("10-K",))
    assert len(filings) == 1
    assert filings[0]["form"] == "10-K"
    assert filings[0]["accession_number"]
