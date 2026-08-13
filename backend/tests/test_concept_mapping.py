"""Unit tests for the concept mapping resolver (backend.data.concept_mapping).

Uses small hand-crafted fixture XBRL facts -- no network access.
"""

from backend.data.concept_mapping import resolve_all_concepts, resolve_concept


def _fixture_facts(tag: str = "Revenues", value: int = 1000, fy: int = 2023, fp: str = "FY"):
    return {
        "cik": 1234567890,
        "entityName": "Fake Co",
        "facts": {
            "us-gaap": {
                tag: {
                    "label": tag,
                    "units": {
                        "USD": [
                            {
                                "end": "2023-12-31",
                                "val": value,
                                "accn": "0000123456-24-000001",
                                "fy": fy,
                                "fp": fp,
                                "form": "10-K",
                                "filed": "2024-02-01",
                                "start": "2023-01-01",
                            }
                        ]
                    },
                }
            }
        },
    }


def test_resolve_concept_matches_first_candidate_tag():
    facts = _fixture_facts(tag="RevenueFromContractWithCustomerExcludingAssessedTax", value=5000)
    match = resolve_concept("revenue", facts)
    assert match.matched is True
    assert match.tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert match.unit == "USD"
    assert match.facts[0]["val"] == 5000


def test_resolve_concept_falls_back_to_later_candidate_tag():
    # "Revenues" is a later candidate than the ASC 606 tag; should still match.
    facts = _fixture_facts(tag="Revenues", value=7500)
    match = resolve_concept("revenue", facts)
    assert match.matched is True
    assert match.tag == "Revenues"
    assert match.facts[0]["val"] == 7500


def test_resolve_concept_no_match_returns_unmatched():
    facts = _fixture_facts(tag="SomeUnrelatedTag", value=1)
    match = resolve_concept("revenue", facts)
    assert match.matched is False
    assert match.tag is None
    assert match.facts is None


def test_resolve_all_concepts_returns_entry_per_canonical_concept():
    facts = _fixture_facts(tag="Revenues", value=100)
    results = resolve_all_concepts(facts)
    assert "revenue" in results
    assert results["revenue"].matched is True
    # concepts with no matching tag in this tiny fixture should be unmatched, not raise
    assert results["net_income"].matched is False


def test_resolve_concept_prefers_usd_unit_when_multiple_units_present():
    facts = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "EUR": [{"val": 1, "fy": 2023, "fp": "FY", "filed": "2024-01-01", "accn": "x"}],
                        "USD": [{"val": 999, "fy": 2023, "fp": "FY", "filed": "2024-01-01", "accn": "y"}],
                    }
                }
            }
        }
    }
    match = resolve_concept("revenue", facts)
    assert match.unit == "USD"
    assert match.facts[0]["val"] == 999
