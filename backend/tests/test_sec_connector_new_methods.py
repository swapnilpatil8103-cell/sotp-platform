"""Unit + integration tests for the newer SECConnector capabilities added
during the SECClient -> SECConnector consolidation:

- resolve_ticker vs get_company_cik (raw vs. zero-padded CIK)
- get_company_concept (new)
- get_filing_document (new)
- get_filing_xbrl (absorbed from backend.data.xbrl_instance's fetch+parse)

Unit tests here use mocked HTTP (no real network access, via monkeypatching
`_fetch_json`/`_request`/`httpx.get`). The `@pytest.mark.integration` tests
hit real SEC EDGAR for AAPL and are skipped unless SEC_USER_AGENT is set.
"""

from __future__ import annotations

import base64
import os

import httpx
import pytest

from backend.data.xbrl_instance import parse_inline_xbrl
from backend.services.cache import FileCache
from backend.services.sec_client import (
    ARCHIVES_URL_TMPL,
    COMPANY_CONCEPT_URL_TMPL,
    SECConnector,
    SECNotFoundError,
    SECUnavailableError,
)
from backend.tests.fixtures.segment_instance_sample import SAMPLE_INLINE_XBRL

FAKE_TICKERS_JSON = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
}

FAKE_COMPANY_CONCEPT_JSON = {
    "cik": 320193,
    "taxonomy": "us-gaap",
    "tag": "Revenues",
    "units": {"USD": [{"val": 1000, "fy": 2023, "fp": "FY", "end": "2023-09-30"}]},
}


@pytest.fixture
def connector(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Suite test@example.com")
    return SECConnector(cache=FileCache(cache_dir=tmp_path))


# ---------------------------------------------------------- resolve_ticker

def test_resolve_ticker_returns_raw_unpadded_cik(connector, monkeypatch):
    monkeypatch.setattr(connector, "_fetch_json", lambda *a, **k: FAKE_TICKERS_JSON)
    assert connector.resolve_ticker("AAPL") == "320193"


def test_get_company_cik_returns_zero_padded_cik(connector, monkeypatch):
    monkeypatch.setattr(connector, "_fetch_json", lambda *a, **k: FAKE_TICKERS_JSON)
    assert connector.get_company_cik("AAPL") == "0000320193"


def test_get_company_cik_builds_on_resolve_ticker(connector, monkeypatch):
    monkeypatch.setattr(connector, "_fetch_json", lambda *a, **k: FAKE_TICKERS_JSON)
    assert connector.get_company_cik("aapl") == connector.resolve_ticker("aapl").zfill(10)


def test_resolve_ticker_raises_not_found(connector, monkeypatch):
    monkeypatch.setattr(connector, "_fetch_json", lambda *a, **k: FAKE_TICKERS_JSON)
    with pytest.raises(SECNotFoundError):
        connector.resolve_ticker("NOTAREALTICKERXYZ")


# ------------------------------------------------------ get_company_concept

def test_get_company_concept_constructs_url_and_returns_payload(connector, monkeypatch):
    captured = {}

    def fake_fetch(url, **kwargs):
        captured["url"] = url
        return FAKE_COMPANY_CONCEPT_JSON

    monkeypatch.setattr(connector, "_fetch_json", fake_fetch)
    result = connector.get_company_concept("0000320193", "Revenues")
    assert captured["url"] == COMPANY_CONCEPT_URL_TMPL.format(cik10="0000320193", taxonomy="us-gaap", tag="Revenues")
    assert result == FAKE_COMPANY_CONCEPT_JSON


def test_get_company_concept_raises_not_found_on_404(connector, monkeypatch):
    class FakeResp:
        status_code = 404

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())
    with pytest.raises(SECNotFoundError):
        connector.get_company_concept("0000320193", "NotARealConcept")


# ------------------------------------------------------- get_filing_document

def test_get_filing_document_constructs_url_and_returns_bytes(connector, monkeypatch):
    captured = {}

    class FakeResp:
        status_code = 200
        content = b"<html>fake filing</html>"

    def fake_get(url, **kwargs):
        captured["url"] = url
        return FakeResp()

    monkeypatch.setattr(httpx, "get", fake_get)
    result = connector.get_filing_document("0000320193", "0000320193-23-000106", "aapl-20230930.htm")
    assert captured["url"] == ARCHIVES_URL_TMPL.format(
        cik_int=320193, accession_nodash="000032019323000106", filename="aapl-20230930.htm"
    )
    assert result == b"<html>fake filing</html>"


def test_get_filing_document_uses_cache(connector):
    url = ARCHIVES_URL_TMPL.format(cik_int=320193, accession_nodash="000032019323000106", filename="doc.htm")
    connector._cache.set(f"doc:{url}", base64.b64encode(b"cached body").decode("ascii"))

    def fail_if_called(*a, **k):
        raise AssertionError("should not hit the network when cache has a fresh entry")

    import httpx as httpx_mod

    orig_get = httpx_mod.get
    httpx_mod.get = fail_if_called
    try:
        result = connector.get_filing_document("0000320193", "0000320193-23-000106", "doc.htm")
    finally:
        httpx_mod.get = orig_get
    assert result == b"cached body"


def test_get_filing_document_raises_unavailable_on_5xx(connector, monkeypatch):
    class FakeResp:
        status_code = 503

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())
    with pytest.raises(SECUnavailableError):
        connector.get_filing_document("0000320193", "0000320193-23-000106", "doc.htm")


# ------------------------------------------------------------ get_filing_xbrl

def test_get_filing_xbrl_matches_old_fetch_plus_parse_entry_point(connector, monkeypatch):
    """get_filing_xbrl must produce identical parsed output to the old
    xbrl_instance.fetch_inline_xbrl_document + parse_inline_xbrl combo for the
    same document bytes."""

    class FakeResp:
        status_code = 200
        content = SAMPLE_INLINE_XBRL

    monkeypatch.setattr(httpx, "get", lambda *a, **k: FakeResp())

    contexts, facts = connector.get_filing_xbrl("https://www.sec.gov/Archives/edgar/data/1/fake/doc.htm")

    expected_contexts, expected_facts = parse_inline_xbrl(SAMPLE_INLINE_XBRL)

    assert set(contexts.keys()) == set(expected_contexts.keys())
    for ctx_id in contexts:
        assert contexts[ctx_id] == expected_contexts[ctx_id]

    assert len(facts) == len(expected_facts)
    for f, ef in zip(facts, expected_facts):
        assert f == ef


def test_get_filing_xbrl_uses_cache(connector):
    url = "https://www.sec.gov/Archives/edgar/data/1/fake/doc.htm"
    connector._cache.set(f"doc:{url}", base64.b64encode(SAMPLE_INLINE_XBRL).decode("ascii"))

    def fail_if_called(*a, **k):
        raise AssertionError("should not hit the network when cache has a fresh entry")

    import httpx as httpx_mod

    orig_get = httpx_mod.get
    httpx_mod.get = fail_if_called
    try:
        contexts, facts = connector.get_filing_xbrl(url)
    finally:
        httpx_mod.get = orig_get
    assert contexts
    assert facts


# --------------------------------------------------------------- integration

@pytest.mark.integration
def test_real_get_company_concept_for_aapl(tmp_path):
    """Hits real SEC EDGAR. Requires SEC_USER_AGENT to be set in the environment."""
    if not os.environ.get("SEC_USER_AGENT"):
        pytest.skip("SEC_USER_AGENT not set; skipping live SEC integration test")

    client = SECConnector(cache=FileCache(cache_dir=tmp_path))
    cik10 = client.get_company_cik("AAPL")

    concept = client.get_company_concept(cik10, "Assets")
    assert concept.get("tag") == "Assets"
    usd_values = concept.get("units", {}).get("USD", [])
    assert len(usd_values) > 0
    assert any(v.get("val") for v in usd_values)


@pytest.mark.integration
def test_real_get_filing_document_for_aapl(tmp_path):
    """Hits real SEC EDGAR. Requires SEC_USER_AGENT to be set in the environment."""
    if not os.environ.get("SEC_USER_AGENT"):
        pytest.skip("SEC_USER_AGENT not set; skipping live SEC integration test")

    client = SECConnector(cache=FileCache(cache_dir=tmp_path))
    cik10 = client.get_company_cik("AAPL")
    filings = client.get_latest_filings(cik10, form_types=("10-K",))
    assert filings
    filing = filings[0]

    document = client.get_filing_document(cik10, filing["accession_number"], filing["primary_document"])
    assert isinstance(document, bytes)
    assert len(document) > 1000
    # Primary 10-K document should be HTML.
    assert b"<html" in document.lower() or b"<HTML" in document
