"""Integration test: real end-to-end segment extraction against Alphabet's
(GOOGL) most recent 10-K, hitting live SEC EDGAR. Skipped by default; run
with `pytest -m integration`."""

from __future__ import annotations

import os

import pytest

from backend.data.segment_extractor import extract_segments_for_filing
from backend.data.normalizer import latest_available_fiscal_year
from backend.services.cache import FileCache
from backend.services.sec_client import SECConnector


@pytest.mark.integration
def test_real_googl_segment_extraction(tmp_path):
    os.environ.setdefault("SEC_USER_AGENT", "SOTP Intelligence Test Suite test@example.com")
    client = SECConnector(cache=FileCache(cache_dir=tmp_path))

    cik10 = client.get_company_cik("GOOGL")
    filings = client.get_latest_filings(cik10, form_types=("10-K",))
    assert filings, "expected at least one 10-K for GOOGL"
    filing = filings[0]

    company_facts = client.get_company_facts(cik10)
    fiscal_year = latest_available_fiscal_year(company_facts, "FY")
    assert fiscal_year is not None

    result = extract_segments_for_filing(
        connector=client,
        cik10=cik10,
        fiscal_year=fiscal_year,
        fiscal_period="FY",
        filing=filing,
    )

    assert result.note is None, f"expected real segment data, got note: {result.note}"
    names = {s.name for s in result.segments}
    # Alphabet discloses at least Google Services and Google Cloud as segments.
    assert any("Cloud" in n for n in names)
    assert any("Services" in n for n in names)

    cloud = next(s for s in result.segments if "Cloud" in s.name)
    revenue_fact = next(f for f in cloud.facts if f.concept == "revenue")
    assert revenue_fact.value is not None and revenue_fact.value > 0
    assert revenue_fact.xbrl_tag is not None
    assert revenue_fact.accession_number is not None
