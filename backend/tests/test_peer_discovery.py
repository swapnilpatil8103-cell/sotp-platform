"""Unit tests for backend.data.peer_discovery -- fixture-based frame/submissions
data, no real network access.
"""

from __future__ import annotations

from backend.data.peer_discovery import discover_peer_candidates
from backend.services.cache import FileCache
from backend.services.sec_client import SECConnector

TARGET_CIK = "0000000001"  # SIC 7372 (software)
SOFTWARE_PEER_CIK = "0000000002"  # SIC 7372 (software) -- should match
BANK_CIK = "0000000003"  # SIC 6022 (bank) -- should be filtered out
OTHER_SOFTWARE_CIK = "0000000004"  # SIC 7372 -- should match, but no financials available

FAKE_FRAME = {
    "data": [
        {"cik": 1, "entityName": "TARGET CO", "val": 1_000_000_000},
        {"cik": 2, "entityName": "SOFTWARE PEER CO", "val": 1_050_000_000},
        {"cik": 3, "entityName": "BANK CO", "val": 1_100_000_000},
        {"cik": 4, "entityName": "SOFTWARE PEER NO FACTS", "val": 900_000_000},
    ]
}

SUBMISSIONS_BY_CIK = {
    TARGET_CIK: {"name": "Target Co", "sic": "7372", "sicDescription": "Prepackaged Software"},
    SOFTWARE_PEER_CIK: {"name": "Software Peer Co", "sic": "7372", "sicDescription": "Prepackaged Software"},
    BANK_CIK: {"name": "Bank Co", "sic": "6022", "sicDescription": "State Commercial Banks"},
    OTHER_SOFTWARE_CIK: {"name": "Software Peer No Facts", "sic": "7372", "sicDescription": "Prepackaged Software"},
}

COMPANY_FACTS_BY_CIK = {
    SOFTWARE_PEER_CIK: {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"val": 1_050_000_000, "fy": 2023, "fp": "FY", "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-01", "accn": "0000000002-24-000001"}
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {"val": 100_000_000, "fy": 2023, "fp": "FY", "start": "2023-01-01", "end": "2023-12-31", "filed": "2024-02-01", "accn": "0000000002-24-000001"}
                        ]
                    }
                },
            }
        }
    },
    OTHER_SOFTWARE_CIK: {"facts": {"us-gaap": {}}},  # no financial concepts at all -> everything MISSING
}


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Suite test@example.com")
    client = SECConnector(cache=FileCache(cache_dir=tmp_path))

    monkeypatch.setattr(client, "get_frame", lambda *a, **k: FAKE_FRAME)
    monkeypatch.setattr(client, "get_submissions", lambda cik10: SUBMISSIONS_BY_CIK[cik10])
    monkeypatch.setattr(client, "get_company_facts", lambda cik10: COMPANY_FACTS_BY_CIK.get(cik10, {"facts": {}}))
    monkeypatch.setattr(client, "_load_ticker_map", lambda: {"SOFT": SOFTWARE_PEER_CIK, "BANK": BANK_CIK})
    return client


def test_discover_peer_candidates_filters_to_same_classification(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    result = discover_peer_candidates(client, target_cik10=TARGET_CIK, fiscal_year=2023, quarter=4)

    assert result.target_classification.category == "Software"
    candidate_ciks = {c.cik10 for c in result.candidates}
    assert SOFTWARE_PEER_CIK in candidate_ciks
    assert OTHER_SOFTWARE_CIK in candidate_ciks
    assert BANK_CIK not in candidate_ciks  # different SIC bucket -- filtered out


def test_discover_peer_candidates_reports_real_financials(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    result = discover_peer_candidates(client, target_cik10=TARGET_CIK, fiscal_year=2023, quarter=4)

    peer = next(c for c in result.candidates if c.cik10 == SOFTWARE_PEER_CIK)
    revenue_fact = next(f for f in peer.financials if f.concept == "revenue")
    assert revenue_fact.data_status == "REPORTED"
    assert revenue_fact.value == 1_050_000_000
    net_income_fact = next(f for f in peer.financials if f.concept == "net_income")
    assert net_income_fact.data_status == "REPORTED"


def test_discover_peer_candidates_marks_missing_financials_honestly(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    result = discover_peer_candidates(client, target_cik10=TARGET_CIK, fiscal_year=2023, quarter=4)

    peer = next(c for c in result.candidates if c.cik10 == OTHER_SOFTWARE_CIK)
    assert all(f.data_status == "MISSING" for f in peer.financials)
    assert all(f.value is None for f in peer.financials)


def test_discover_peer_candidates_excludes_target_itself(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    result = discover_peer_candidates(client, target_cik10=TARGET_CIK, fiscal_year=2023, quarter=4)

    assert all(c.cik10 != TARGET_CIK for c in result.candidates)


def test_discover_peer_candidates_ticker_lookup(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    result = discover_peer_candidates(client, target_cik10=TARGET_CIK, fiscal_year=2023, quarter=4)

    peer = next(c for c in result.candidates if c.cik10 == SOFTWARE_PEER_CIK)
    assert peer.ticker == "SOFT"
