"""Integration test: real AAPL multi-year revenue history + CAGR, against
live SEC EDGAR (no mocks). Skipped by default; run explicitly with:

    pytest backend/tests/test_historical_analysis_integration.py -m integration -q
"""

from __future__ import annotations

import os

import pytest

from backend.data.historical_analysis import build_historical_trends
from backend.services.cache import FileCache
from backend.services.sec_client import SECConnector


@pytest.mark.integration
def test_aapl_real_multi_year_revenue_history_and_cagr(tmp_path):
    os.environ.setdefault("SEC_USER_AGENT", "SOTP Intelligence Test Suite test@example.com")
    client = SECConnector(cache=FileCache(cache_dir=tmp_path))

    cik10 = client.get_company_cik("AAPL")
    company_facts = client.get_company_facts(cik10)

    result = build_historical_trends(company_id=1, ticker="AAPL", company_facts=company_facts, num_years=6)

    # Real multi-year REPORTED revenue history should exist and span several years.
    revenue_series = result["series"]["revenue"]
    reported = [y for y in revenue_series if y["data_status"] == "REPORTED"]
    assert len(reported) >= 2, f"Expected >= 2 REPORTED revenue years for AAPL, got: {revenue_series}"

    print("AAPL REPORTED revenue series:", [(y["fiscal_year"], y["value"]) for y in reported])
    print("AAPL revenue CAGR result:", result["revenue_cagr"])
    print("AAPL operating margin trend:", result["operating_margin_trend"])
    print("AAPL suggested forward revenue:", result["suggested_forward_revenue"])

    cagr = result["revenue_cagr"]
    assert cagr["insufficient_history"] is False
    assert cagr["cagr_pct"] is not None
    # Sanity bound -- AAPL's multi-year revenue CAGR should be a plausible
    # double-digit-or-less percentage, not some fabricated/absurd number.
    assert -50.0 < cagr["cagr_pct"] < 100.0

    assert result["suggested_forward_revenue"]["label"] == "SUGGESTED"
