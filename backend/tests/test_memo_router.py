"""Tests for GET /memo/{ticker} (closes Phase 10 audit gap #1)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import deps
from backend.api.main import app
from backend.ai.testing import FakeAIAdapter
from backend.api.routers import memo as memo_router
from backend.db import get_engine
from backend.models.company import Company
from backend.models.enums import ValuationMethod
from backend.models.valuation_run import ValuationRun
from sqlmodel import Session

client = TestClient(app)


class _FakeSECClient:
    def get_cik(self, ticker):
        return "0000320193"


def _seed_company_with_run(ticker="AAPL", cik="0000320193"):
    with Session(get_engine()) as session:
        company = Company(ticker=ticker, cik=cik, name="Apple Inc.", sector="Technology")
        session.add(company)
        session.commit()
        session.refresh(company)

        run = ValuationRun(
            company_id=company.id,
            method=ValuationMethod.SOTP,
            version=1,
            inputs={"segments": [{"name": "iPhone", "enterprise_value": 1000.0}]},
            outputs={"equity_value": 3000000.0, "implied_price_per_share": 150.0},
            created_by="analyst:test",
        )
        session.add(run)
        session.commit()
        return company.id


def test_memo_endpoint_generates_sections_from_real_data():
    company_id = _seed_company_with_run()

    app.dependency_overrides[deps.get_sec_client] = lambda: _FakeSECClient()
    app.dependency_overrides[memo_router._get_adapter] = lambda: FakeAIAdapter(
        fixed_text="Equity value stands at 3000000.0, or 150.0 per share."
    )
    try:
        resp = client.get("/memo/AAPL")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["company_id"] == company_id
    assert body["insufficient_data"] is False
    assert len(body["sections"]) == len(memo_router.MEMO_SECTIONS)
    assert "valuation_runs" in body["data_snapshot"]
    assert body["data_snapshot"]["valuation_runs"]["SOTP"]["outputs"]["equity_value"] == 3000000.0
    # At least one section should have produced real, validated memo text.
    assert any(s["memo_text"] and not s["abstained"] for s in body["sections"])


def test_memo_endpoint_404_when_no_valuation_run_exists():
    app.dependency_overrides[deps.get_sec_client] = lambda: _FakeSECClient()
    app.dependency_overrides[memo_router._get_adapter] = lambda: FakeAIAdapter(fixed_text="whatever")
    try:
        resp = client.get("/memo/AAPL")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404
    assert "Insufficient data" in resp.json()["detail"]
