"""Unit tests for backend.data.normalizer -- fixture-based, no network access."""

from backend.data.normalizer import latest_available_fiscal_year, normalize_company_facts
from backend.models.enums import DataStatus


def _fixture_company_facts():
    return {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {
                                "val": 1000,
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-02-01",
                                "accn": "0000123456-24-000001",
                            },
                            {
                                "val": 800,
                                "fy": 2022,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2023-02-01",
                                "accn": "0000123456-23-000001",
                            },
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {
                                "val": 200,
                                "fy": 2023,
                                "fp": "FY",
                                "form": "10-K",
                                "filed": "2024-02-01",
                                "accn": "0000123456-24-000001",
                            }
                        ]
                    }
                },
            }
        }
    }


def test_normalize_company_facts_reported_status_for_matched_concept():
    facts = _fixture_company_facts()
    rows = normalize_company_facts(company_id=1, company_facts=facts, fiscal_year=2023, cik10="0000320193")
    revenue_row = next(r for r in rows if r.concept == "revenue")
    assert revenue_row.data_status == DataStatus.REPORTED
    assert revenue_row.value == 1000
    assert revenue_row.xbrl_tag == "Revenues"
    assert revenue_row.accession_number == "0000123456-24-000001"
    assert revenue_row.source == "SEC XBRL"
    assert revenue_row.source_url is not None


def test_normalize_company_facts_missing_status_for_unmatched_concept():
    facts = _fixture_company_facts()
    rows = normalize_company_facts(company_id=1, company_facts=facts, fiscal_year=2023)
    capex_row = next(r for r in rows if r.concept == "capex")
    assert capex_row.data_status == DataStatus.MISSING
    assert capex_row.value is None
    assert capex_row.xbrl_tag is None


def test_normalize_company_facts_missing_when_year_has_no_data_for_matched_tag():
    facts = _fixture_company_facts()
    # 2021 has no data for Revenues at all -> should be MISSING even though tag is known
    rows = normalize_company_facts(company_id=1, company_facts=facts, fiscal_year=2021)
    revenue_row = next(r for r in rows if r.concept == "revenue")
    assert revenue_row.data_status == DataStatus.MISSING
    assert revenue_row.value is None


def test_normalize_company_facts_produces_row_per_canonical_concept():
    facts = _fixture_company_facts()
    rows = normalize_company_facts(company_id=1, company_facts=facts, fiscal_year=2023)
    from backend.data.concept_mapping import CANONICAL_CONCEPTS

    assert len(rows) == len(CANONICAL_CONCEPTS)
    assert {r.concept for r in rows} == set(CANONICAL_CONCEPTS)


def test_latest_available_fiscal_year():
    facts = _fixture_company_facts()
    assert latest_available_fiscal_year(facts) == 2023
