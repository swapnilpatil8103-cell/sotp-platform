"""Unit + integration tests for SECConnector's insider-ownership (Form 3/4/5)
and 13F institutional-holdings methods.

Unit tests mock HTTP (no real network access). The @pytest.mark.integration
tests hit real SEC EDGAR (AAPL Form 4s, Berkshire Hathaway 13F-HR) and are
skipped unless SEC_USER_AGENT is set.
"""

from __future__ import annotations

import os

import httpx
import pytest

from backend.services.cache import FileCache
from backend.services.sec_client import SECConnector, SECNotFoundError
from backend.tests.fixtures.ownership_samples import (
    SAMPLE_13F_COVER_PAGE_XML,
    SAMPLE_13F_INFO_TABLE_XML,
    SAMPLE_FORM4_XML,
)

FAKE_SUBMISSIONS_WITH_FORM4 = {
    "filings": {
        "recent": {
            "form": ["10-K", "4", "4", "3"],
            "accessionNumber": [
                "0000320193-26-000001",
                "0001140361-26-032884",
                "0001140361-26-025622",
                "0001140361-25-000111",
            ],
            "filingDate": ["2026-01-01", "2026-08-13", "2026-06-17", "2025-01-05"],
            "reportDate": ["2025-12-31", "2026-08-11", "2026-06-15", "2025-01-01"],
            "primaryDocument": [
                "aapl-20251231.htm",
                "xslF345X06/form4.xml",
                "xslF345X06/form4.xml",
                "xslF345X06/form3.xml",
            ],
        }
    }
}

FAKE_13F_SUBMISSIONS = {
    "filings": {
        "recent": {
            "form": ["13F-HR"],
            "accessionNumber": ["0001193125-26-352200"],
            "filingDate": ["2026-08-14"],
            "reportDate": ["2026-06-30"],
            "primaryDocument": ["xslForm13F_X02/primary_doc.xml"],
        }
    }
}

FAKE_13F_INDEX_JSON = {
    "directory": {
        "item": [
            {"name": "0001193125-26-352200-index.html"},
            {"name": "0001193125-26-352200.txt"},
            {"name": "56757.xml"},
            {"name": "primary_doc.xml"},
        ]
    }
}


@pytest.fixture
def connector(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Suite test@example.com")
    return SECConnector(cache=FileCache(cache_dir=tmp_path))


# ------------------------------------------------------- get_insider_filings

def test_get_insider_filings_filters_to_form_3_4_5(connector, monkeypatch):
    monkeypatch.setattr(connector, "get_submissions", lambda cik10: FAKE_SUBMISSIONS_WITH_FORM4)
    filings = connector.get_insider_filings("0000320193")
    assert len(filings) == 3
    assert all(f["form"] in ("3", "4", "5") for f in filings)
    assert filings[0]["accession_number"] == "0001140361-26-032884"


def test_get_insider_filings_default_form_types(connector, monkeypatch):
    monkeypatch.setattr(connector, "get_submissions", lambda cik10: FAKE_SUBMISSIONS_WITH_FORM4)
    filings = connector.get_insider_filings("0000320193", form_types=("4",))
    assert len(filings) == 2


# ---------------------------------------------------- _raw_document_filename

def test_raw_document_filename_strips_xsl_prefix(connector):
    assert connector._raw_document_filename("xslF345X06/form4.xml") == "form4.xml"
    assert connector._raw_document_filename("xslForm13F_X02/primary_doc.xml") == "primary_doc.xml"
    assert connector._raw_document_filename("aapl-20251231.htm") == "aapl-20251231.htm"


# ---------------------------------------------------- get_ownership_document

def test_get_ownership_document_fetches_and_parses_real_shape(connector, monkeypatch):
    monkeypatch.setattr(connector, "get_submissions", lambda cik10: FAKE_SUBMISSIONS_WITH_FORM4)

    class FakeResp:
        status_code = 200
        content = SAMPLE_FORM4_XML

    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        return FakeResp()

    monkeypatch.setattr(httpx, "get", fake_get)

    doc = connector.get_ownership_document("0000320193", "0001140361-26-032884")

    assert captured["url"].endswith("/000114036126032884/form4.xml")
    assert doc.issuer_name == "Apple Inc."
    assert doc.reporting_owner_name == "Newstead Jennifer"
    assert len(doc.transactions) == 1
    assert doc.transactions[0].transaction_shares == 1439.0


# ---------------------------------------------------------- get_13f_holdings

def test_get_13f_holdings_uses_index_json_to_find_info_table(connector, monkeypatch):
    def fake_get_submissions(cik10):
        return FAKE_13F_SUBMISSIONS

    monkeypatch.setattr(connector, "get_submissions", fake_get_submissions)

    responses = {
        "primary_doc.xml": SAMPLE_13F_COVER_PAGE_XML,
        "56757.xml": SAMPLE_13F_INFO_TABLE_XML,
    }

    def fake_fetch_document(url, **kwargs):
        for filename, content in responses.items():
            if url.endswith(filename):
                return content
        raise AssertionError(f"unexpected url {url}")

    def fake_fetch_json(url, **kwargs):
        assert url.endswith("/index.json")
        return FAKE_13F_INDEX_JSON

    monkeypatch.setattr(connector, "_fetch_document", fake_fetch_document)
    monkeypatch.setattr(connector, "_fetch_json", fake_fetch_json)

    cover_page, holdings = connector.get_13f_holdings("0001067983", "0001193125-26-352200")

    assert cover_page.filing_manager_name == "Berkshire Hathaway Inc"
    assert cover_page.period_of_report == "06-30-2026"
    assert len(holdings) == 2
    assert holdings[0].name_of_issuer == "ALLY FINL INC"


def test_get_13f_holdings_raises_not_found_if_no_info_table(connector, monkeypatch):
    monkeypatch.setattr(connector, "get_submissions", lambda cik10: FAKE_13F_SUBMISSIONS)
    monkeypatch.setattr(connector, "_fetch_document", lambda url, **k: SAMPLE_13F_COVER_PAGE_XML)
    monkeypatch.setattr(
        connector,
        "_fetch_json",
        lambda url, **k: {"directory": {"item": [{"name": "primary_doc.xml"}]}},
    )
    with pytest.raises(SECNotFoundError):
        connector.get_13f_holdings("0001067983", "0001193125-26-352200")


# --------------------------------------------------------------- integration

@pytest.mark.integration
def test_real_get_insider_filings_and_ownership_document_for_aapl(tmp_path):
    if not os.environ.get("SEC_USER_AGENT"):
        pytest.skip("SEC_USER_AGENT not set; skipping live SEC integration test")

    client = SECConnector(cache=FileCache(cache_dir=tmp_path))
    cik10 = client.get_company_cik("AAPL")

    filings = client.get_insider_filings(cik10)
    assert len(filings) > 0
    assert all(f["form"] in ("3", "4", "5") for f in filings)

    doc = client.get_ownership_document(cik10, filings[0]["accession_number"])
    assert doc.issuer_cik == cik10
    assert doc.issuer_name
    assert doc.reporting_owner_name
    assert len(doc.transactions) >= 1


@pytest.mark.integration
def test_real_get_13f_holdings_for_berkshire(tmp_path):
    if not os.environ.get("SEC_USER_AGENT"):
        pytest.skip("SEC_USER_AGENT not set; skipping live SEC integration test")

    client = SECConnector(cache=FileCache(cache_dir=tmp_path))
    filer_cik10 = "0001067983"  # Berkshire Hathaway Inc, real 13F filer CIK

    latest = client.get_latest_filings(filer_cik10, form_types=("13F-HR",))
    assert latest
    accession_number = latest[0]["accession_number"]

    cover_page, holdings = client.get_13f_holdings(filer_cik10, accession_number)
    assert cover_page.filing_manager_name
    assert len(holdings) > 10
    assert all(h.cusip for h in holdings[:5])
