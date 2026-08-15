"""Unit tests for backend.services.sec_client -- CIK lookup logic against a mocked
ticker file (no real network access). Also a real-network integration test,
marked so it can be skipped in CI.
"""

import os

import pytest

from backend.services.sec_client import SECConnector, SECNotFoundError


FAKE_TICKERS_JSON = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
}


@pytest.fixture
def sec_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Suite test@example.com")
    from backend.services.cache import FileCache

    client = SECConnector(cache=FileCache(cache_dir=tmp_path))
    return client


def test_get_cik_resolves_known_ticker(sec_client, monkeypatch):
    monkeypatch.setattr(sec_client, "_fetch_json", lambda *a, **k: FAKE_TICKERS_JSON)
    cik = sec_client.get_company_cik("AAPL")
    assert cik == "0000320193"


def test_get_cik_is_case_insensitive(sec_client, monkeypatch):
    monkeypatch.setattr(sec_client, "_fetch_json", lambda *a, **k: FAKE_TICKERS_JSON)
    cik = sec_client.get_company_cik("aapl")
    assert cik == "0000320193"


def test_get_cik_raises_not_found_for_unknown_ticker(sec_client, monkeypatch):
    monkeypatch.setattr(sec_client, "_fetch_json", lambda *a, **k: FAKE_TICKERS_JSON)
    with pytest.raises(SECNotFoundError):
        sec_client.get_company_cik("NOTAREALTICKERXYZ")


def test_client_requires_sec_user_agent(monkeypatch, tmp_path):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    from backend.services.cache import FileCache
    from backend.services.sec_client import SECError

    with pytest.raises(SECError):
        SECConnector(cache=FileCache(cache_dir=tmp_path))


FAKE_SUBMISSIONS_JSON = {
    "name": "Apple Inc.",
    "filings": {
        "recent": {
            "form": ["10-K", "10-Q", "10-Q"],
            "accessionNumber": ["0000320193-23-000106", "0000320193-23-000077", "0000320193-23-000050"],
            "filingDate": ["2023-11-03", "2023-08-04", "2023-05-05"],
            "reportDate": ["2023-09-30", "2023-07-01", "2023-04-01"],
            "primaryDocument": ["aapl-20230930.htm", "aapl-20230701.htm", "aapl-20230401.htm"],
        }
    },
}


def test_get_filing_metadata_finds_filing_by_accession_number(sec_client, monkeypatch):
    monkeypatch.setattr(sec_client, "get_submissions", lambda cik10: FAKE_SUBMISSIONS_JSON)
    meta = sec_client.get_filing_metadata("0000320193", "0000320193-23-000106")
    assert meta["form"] == "10-K"
    assert meta["accession_number"] == "0000320193-23-000106"
    assert meta["filing_date"] == "2023-11-03"
    assert meta["primary_document"] == "aapl-20230930.htm"
    assert meta["source_url"].endswith("000032019323000106/aapl-20230930.htm")


def test_get_filing_metadata_matches_accession_regardless_of_dashes(sec_client, monkeypatch):
    monkeypatch.setattr(sec_client, "get_submissions", lambda cik10: FAKE_SUBMISSIONS_JSON)
    meta = sec_client.get_filing_metadata("0000320193", "000032019323000106")
    assert meta["accession_number"] == "0000320193-23-000106"


def test_get_filing_metadata_raises_not_found_for_unknown_accession(sec_client, monkeypatch):
    monkeypatch.setattr(sec_client, "get_submissions", lambda cik10: FAKE_SUBMISSIONS_JSON)
    with pytest.raises(SECNotFoundError):
        sec_client.get_filing_metadata("0000320193", "9999999999-99-999999")


def test_get_latest_filings_consistent_with_get_filing_metadata(sec_client, monkeypatch):
    monkeypatch.setattr(sec_client, "get_submissions", lambda cik10: FAKE_SUBMISSIONS_JSON)
    latest = sec_client.get_latest_filings("0000320193", form_types=("10-K",))
    assert len(latest) == 1
    by_accession = sec_client.get_filing_metadata("0000320193", latest[0]["accession_number"])
    assert latest[0] == by_accession


@pytest.mark.integration
def test_real_sec_lookup_for_aapl(tmp_path):
    """Hits real SEC EDGAR. Requires SEC_USER_AGENT to be set in the environment."""
    if not os.environ.get("SEC_USER_AGENT"):
        pytest.skip("SEC_USER_AGENT not set; skipping live SEC integration test")

    from backend.services.cache import FileCache

    client = SECConnector(cache=FileCache(cache_dir=tmp_path))
    cik = client.get_company_cik("AAPL")
    assert cik == "0000320193"

    submissions = client.get_submissions(cik)
    assert submissions.get("name", "").lower().startswith("apple")

    filings = client.get_latest_filings(cik, form_types=("10-K",))
    assert len(filings) == 1
    assert filings[0]["form"] == "10-K"
    assert filings[0]["accession_number"]
