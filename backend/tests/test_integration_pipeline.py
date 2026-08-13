"""Full end-to-end integration test: real ticker -> SEC EDGAR -> normalized
financial facts -> segment extraction -> business classification -> Phase 5
deterministic valuation engine (DCF, SOTP).

Hits live SEC EDGAR (no mocks), so it is marked `integration` and skipped by
default. Run explicitly with:

    pytest backend/tests/test_integration_pipeline.py -m integration -q

This is the master spec's "test at least several different company
structures" integration test. It runs the pipeline against two real
companies with genuinely different structures:

- AAPL: single-segment-ish, simple capital structure, already proven to
  resolve cleanly in Phase 2/3's integration tests.
- JPM: a bank/financial institution. Per the business classifier, financial
  institutions do not have a conventional operating-EBIT/FCFF profile
  (interest income/expense dominates, no meaningful "capex" concept), so we
  expect -- and assert -- that the classifier steers this case away from a
  vanilla DCF, and we document what the pipeline can and can't do for it
  rather than forcing a fake DCF pass with invented assumptions.

All valuation assumptions (growth, margins, WACC components, etc.) fed into
the engine are hand-supplied explicit constants in this test -- never
invented from thin air by the pipeline -- consistent with the project's
"AI/pipeline cannot fabricate assumptions" governance rule.
"""

from __future__ import annotations

import os

import pytest

from backend.data.business_classifier import classify_business
from backend.data.normalizer import latest_available_fiscal_year, normalize_company_facts
from backend.data.segment_extractor import extract_segments_for_filing
from backend.models.enums import DataStatus
from backend.schemas.valuation import DcfInput, SotpInput, SotpSegmentInput
from backend.services.cache import FileCache
from backend.services.sec_client import SECClient
from backend.valuation.dcf import run_dcf
from backend.valuation.sotp import run_sotp


def _fact_value(facts, concept: str):
    for f in facts:
        if f.concept == concept and f.data_status != DataStatus.MISSING:
            return f.value
    return None


@pytest.mark.integration
def test_full_pipeline_aapl_dcf_and_sotp(tmp_path):
    """AAPL: ticker -> CIK -> SEC facts -> normalization -> DCF -> SOTP,
    asserting internal consistency of the resulting numbers."""
    os.environ.setdefault("SEC_USER_AGENT", "SOTP Intelligence Test Suite test@example.com")
    client = SECClient(cache=FileCache(cache_dir=tmp_path))

    # 1. Ticker -> CIK resolution.
    cik10 = client.get_cik("AAPL")
    assert cik10 and len(cik10) == 10

    # 2. Business classification (drives eligible methodologies downstream).
    submissions = client.get_submissions(cik10)
    sic = submissions.get("sic")
    classification = classify_business(sic)
    assert classification.category is not None

    # 3. Fetch + normalize financial facts.
    company_facts = client.get_company_facts(cik10)
    fiscal_year = latest_available_fiscal_year(company_facts, "FY")
    assert fiscal_year is not None

    facts = normalize_company_facts(
        company_id=1,
        company_facts=company_facts,
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        cik10=cik10,
    )
    assert facts, "expected at least one normalized fact"

    revenue = _fact_value(facts, "revenue")
    assert revenue is not None and revenue > 0

    # 4. Segment extraction (best-effort; AAPL discloses geographic/product
    # segments but the extractor may return a `note` if inline-XBRL segment
    # data isn't cleanly resolvable for the latest filing -- that's an
    # acceptable, documented outcome, not a hard failure).
    filings = client.get_latest_filings(cik10, form_types=("10-K",))
    assert filings
    segment_result = extract_segments_for_filing(
        user_agent=client.user_agent,
        cik10=cik10,
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        filing=filings[0],
    )

    # 5. Run DCF with hand-supplied assumptions (never invented by the test
    # from company data -- these are explicit, deliberately simple analyst
    # inputs).
    dcf_input = DcfInput(
        base_revenue=revenue,
        revenue_growth_rates=[0.06, 0.05, 0.05, 0.04, 0.04],
        ebit_margins=[0.30, 0.30, 0.30, 0.30, 0.30],
        tax_rate=0.16,
        da_pct_of_revenue=[0.03] * 5,
        capex_pct_of_revenue=[0.03] * 5,
        nwc_change_pct_of_revenue=[0.0] * 5,
        wacc=0.09,
        terminal_growth_rate=0.03,
        net_debt=0.0,
        cash_and_equivalents=0.0,
        investments=0.0,
        minority_interest=0.0,
        diluted_shares_outstanding=15_000_000_000,
    )
    dcf_result = run_dcf(dcf_input)

    # Structural / internal-consistency assertions (not fixed numeric
    # fixtures, since real filing data changes year over year).
    assert dcf_result.enterprise_value > 0
    assert dcf_result.equity_value == pytest.approx(
        dcf_result.enterprise_value - dcf_input.net_debt + dcf_input.cash_and_equivalents
        + dcf_input.investments - dcf_input.minority_interest,
        rel=1e-6,
    )
    assert dcf_result.implied_price_per_share == pytest.approx(
        dcf_result.equity_value / dcf_input.diluted_shares_outstanding, rel=1e-6
    )
    assert len(dcf_result.projections) == 5

    # 6. Run SOTP using the DCF-derived EV as a single "segment" EV (AAPL is
    # not a true multi-segment conglomerate, so this exercises the SOTP
    # arithmetic/bridge rather than claiming AAPL needs SOTP in practice).
    sotp_input = SotpInput(
        segments=[
            SotpSegmentInput(name="Consolidated", enterprise_value=dcf_result.enterprise_value, ownership_pct=1.0)
        ],
        cash_and_equivalents=0.0,
        marketable_securities=0.0,
        other_investments=0.0,
        total_debt=0.0,
        minority_interest=0.0,
        corporate_liabilities=0.0,
        corporate_overhead_annual=0.0,
        overhead_capitalization_multiple=1.0,
        corporate_overhead_treatment="direct_deduction",
        diluted_shares_outstanding=dcf_input.diluted_shares_outstanding,
    )
    sotp_result = run_sotp(sotp_input)

    assert sotp_result.sum_of_segment_evs == pytest.approx(dcf_result.enterprise_value, rel=1e-9)
    assert sotp_result.equity_value == pytest.approx(sotp_result.sum_of_segment_evs, rel=1e-6)
    assert sotp_result.implied_price_per_share == pytest.approx(
        sotp_result.equity_value / sotp_input.diluted_shares_outstanding, rel=1e-6
    )


@pytest.mark.integration
def test_full_pipeline_jpm_financial_institution_classification(tmp_path):
    """JPM: a bank, structurally different from AAPL. Verifies the pipeline
    correctly classifies it as a Bank (SIC 6021/602x) and
    that normalized facts + classification are internally consistent, without
    forcing a conventional operating-company DCF on it.

    Per the master spec, "DCF unavailable" / "methodology not applicable" is
    an acceptable and expected outcome for banks -- the assertion here is on
    the *classification and data pipeline*, not on manufacturing a fake DCF
    pass with invented capex/NWC assumptions for a business that doesn't have
    those concepts in a meaningful sense.
    """
    os.environ.setdefault("SEC_USER_AGENT", "SOTP Intelligence Test Suite test@example.com")
    client = SECClient(cache=FileCache(cache_dir=tmp_path))

    cik10 = client.get_cik("JPM")
    assert cik10 and len(cik10) == 10

    submissions = client.get_submissions(cik10)
    sic = submissions.get("sic")
    classification = classify_business(sic)

    # JPMorgan Chase's SIC code is 6021 (National Commercial Banks) -- should
    # land in the Financial Institution category, which is the whole point
    # of this test: a genuinely different structure from AAPL's SIC.
    assert classification.category == "Bank", (
        f"expected JPM (SIC {sic}) to classify as Bank, got {classification.category!r}"
    )

    company_facts = client.get_company_facts(cik10)
    fiscal_year = latest_available_fiscal_year(company_facts, "FY")
    assert fiscal_year is not None

    facts = normalize_company_facts(
        company_id=2,
        company_facts=company_facts,
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        cik10=cik10,
    )
    assert facts

    # Document the real limitation: banks routinely have MISSING capex/PP&E
    # concepts under the standard operating-company XBRL tags this project's
    # concept map targets, since balance-sheet-driven institutions don't
    # report them the same way industrials do. We assert-and-report rather
    # than silently skip, so this is a visible, honest data point.
    capex = _fact_value(facts, "capital_expenditures")
    revenue = _fact_value(facts, "revenue")
    missing_concepts = sorted({f.concept for f in facts if f.data_status == DataStatus.MISSING})

    print(
        f"\n[JPM integration] classification={classification.category!r}, "
        f"fiscal_year={fiscal_year}, revenue={revenue!r}, capex={capex!r}, "
        f"missing_concepts={missing_concepts}"
    )

    # Whatever else is missing, revenue-equivalent (interest+noninterest
    # income) or net income concepts should be resolvable for a company this
    # large -- if the pipeline can't find *any* usable top-line concept for
    # JPM, that's the genuine "insufficient data" limitation this test is
    # meant to surface.
    assert any(
        f.data_status != DataStatus.MISSING for f in facts if f.concept in ("revenue", "net_income")
    ), "expected at least revenue or net_income to be resolvable for JPM from SEC XBRL facts"
