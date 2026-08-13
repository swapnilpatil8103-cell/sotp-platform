"""Unit test for the minimal POST /valuation/sotp endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api.main import app

client = TestClient(app)


def test_post_valuation_sotp_computes_result():
    payload = {
        "segments": [
            {"name": "Alpha", "enterprise_value": 1000.0, "ownership_pct": 1.0},
            {"name": "Beta", "enterprise_value": 500.0, "ownership_pct": 0.6},
        ],
        "cash_and_equivalents": 100.0,
        "marketable_securities": 50.0,
        "other_investments": 0.0,
        "total_debt": 400.0,
        "minority_interest": 20.0,
        "corporate_liabilities": 10.0,
        "corporate_overhead_annual": 30.0,
        "overhead_capitalization_multiple": 5.0,
        "corporate_overhead_treatment": "direct_deduction",
        "diluted_shares_outstanding": 100.0,
        "current_share_price": 9.0,
    }
    resp = client.post("/valuation/sotp", json=payload)
    assert resp.status_code == 200
    body = resp.json()
    assert body["equity_value"] == 1020.0
    assert body["implied_price_per_share"] == 10.2
