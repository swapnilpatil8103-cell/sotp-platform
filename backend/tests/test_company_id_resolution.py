"""Tests for ticker->company_id resolution (closes Phase 10 audit gap #2).

GET /companies/{ticker} now persists/looks up the Company row and returns
its internal company_id; GET /companies/{ticker}/id is a thin wrapper for
callers that only need the id.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import deps
from backend.api.main import app

client = TestClient(app)


class _FakeSECConnector:
    def get_company_cik(self, ticker):
        return "0000320193"

    def get_submissions(self, cik10):
        return {
            "name": "Apple Inc.",
            "sic": "3571",
            "sicDescription": "Electronic Computers",
            "exchanges": ["Nasdaq"],
            "fiscalYearEnd": "0930",
        }


def test_get_company_returns_company_id():
    app.dependency_overrides[deps.get_sec_client] = lambda: _FakeSECConnector()
    try:
        resp = client.get("/companies/AAPL")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "AAPL"
    assert isinstance(body["company_id"], int)


def test_get_company_id_endpoint_resolves_and_persists():
    app.dependency_overrides[deps.get_sec_client] = lambda: _FakeSECConnector()
    try:
        resp1 = client.get("/companies/AAPL/id")
        resp2 = client.get("/companies/AAPL/id")
    finally:
        app.dependency_overrides.clear()

    assert resp1.status_code == 200
    assert resp2.status_code == 200
    # Same ticker resolves to the same persisted company_id both times.
    assert resp1.json()["company_id"] == resp2.json()["company_id"]
    assert resp1.json()["ticker"] == "AAPL"
