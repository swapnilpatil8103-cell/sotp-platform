"""Regression tests for bugs found by verifying the pipeline against real SEC filings.

The fixtures in ``fixtures/real_companyfacts`` are trimmed copies of the real
``data.sec.gov/api/xbrl/companyfacts`` responses (only the tags these tests need, FY2023+).
Expected values were checked against the filings' own statements (see
docs/data-validation-report.md). No network access.
"""
import json
from pathlib import Path

import pytest

from backend.data.normalizer import build_fact
from backend.data.segment_extractor import extract_segments_from_instance
from backend.data.xbrl_instance import XbrlContext, XbrlFact
from backend.models.enums import DataStatus

FIXTURES = Path(__file__).parent / "fixtures" / "real_companyfacts"


def _facts(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf8"))


def _fact(name: str, concept: str, fy: int):
    return build_fact(1, concept, _facts(name), fy, "FY")


# ---- shares: cover-page count must not be picked by period-end matching
@pytest.mark.parametrize(
    "name,fy,expected,as_of",
    [("JPM", 2025, 2_697_032_375, "2026-01-31"), ("GE", 2025, 1_048_813_612, "2026-01-15"), ("INTC", 2025, 4_995_000_000, "2026-01-16")],
)
def test_shares_come_from_the_cover_page_with_its_date(name, fy, expected, as_of):
    f = _fact(name, "shares_outstanding", fy)
    assert f.value == expected
    assert f.data_status == DataStatus.REPORTED
    assert as_of in (f.status_note or "")


# ---- revenue: total beats the contract-revenue subset
def test_berkshire_revenue_is_total_not_contract_subset():
    f = _fact("BRK_B", "revenue", 2025)
    assert f.value == 371_444_000_000
    assert f.xbrl_tag == "Revenues"
    assert "247,244" in (f.status_note or "") or "contracts" in (f.status_note or "")


def test_aapl_revenue_uses_contract_tag_when_no_total_exists():
    f = _fact("AAPL", "revenue", 2025)
    assert f.value == 416_161_000_000
    assert f.data_status == DataStatus.REPORTED


# ---- debt: LongTermDebt includes the current portion; total is derived from components
def test_total_debt_derived_from_components_is_labelled_derived():
    f = _fact("AAPL", "total_debt", 2025)
    assert f.value == 98_657_000_000
    assert f.data_status == DataStatus.DERIVED
    assert f.xbrl_tag.startswith("derived:")
    assert f.status_note and "Derived" in f.status_note
    lt = _fact("AAPL", "long_term_debt", 2025)
    assert lt.value == 78_328_000_000  # noncurrent only: not total debt


def test_ge_long_term_debt_uses_capital_lease_inclusive_tag():
    assert _fact("GE", "long_term_debt", 2025).value == 18_808_000_000
    assert _fact("GE", "total_debt", 2025).value == 20_494_000_000


# ---- step 5: missing and conflicting data are reported, never guessed
def test_missing_tags_report_missing_with_no_value():
    for name, concept in [("JPM", "operating_income"), ("GE", "operating_income"), ("BRK_B", "shares_outstanding"), ("BRK_B", "total_debt")]:
        f = _fact(name, concept, 2025)
        assert f.data_status == DataStatus.MISSING, (name, concept)
        assert f.value is None


def test_restated_year_is_conflicting_with_a_note():
    f = _fact("GE", "revenue", 2023)
    assert f.data_status == DataStatus.CONFLICTING
    assert "different values" in (f.status_note or "")
    assert f.value is not None  # the latest-filed value is shown, but flagged


def test_unrestated_year_is_not_flagged():
    assert _fact("AAPL", "net_income", 2025).data_status == DataStatus.REPORTED


# ---- segments
def _ctx(cid, dims, start="2025-01-01", end="2025-12-31"):
    from datetime import date

    return XbrlContext(context_id=cid, dimensions=list(dims), start=date.fromisoformat(start), end=date.fromisoformat(end), instant=None)


def _num(name, cid, value):
    return XbrlFact(name=name, context_id=cid, value=value, unit_ref="USD")


SEG_AXIS = "us-gaap:StatementBusinessSegmentsAxis"


def test_bank_segments_use_revenues_net_of_interest_expense():
    contexts = {"a": _ctx("a", [(SEG_AXIS, "jpm:ConsumerCommunityBankingMember")])}
    facts = [_num("us-gaap:RevenuesNetOfInterestExpense", "a", 76_029_000_000)]
    r = extract_segments_from_instance(contexts, facts, 2025)
    rev = {f.concept: f for f in r.segments[0].facts}["revenue"]
    assert rev.value == 76_029_000_000
    assert rev.xbrl_tag == "us-gaap:RevenuesNetOfInterestExpense"


def test_segment_total_revenue_beats_contract_subset_and_is_noted():
    contexts = {"a": _ctx("a", [(SEG_AXIS, "brk:BNSFMember")])}
    facts = [
        _num("us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", "a", 23_256_000_000),
        _num("us-gaap:Revenues", "a", 23_533_000_000),
    ]
    rev = {f.concept: f for f in extract_segments_from_instance(contexts, facts, 2025).segments[0].facts}["revenue"]
    assert rev.value == 23_533_000_000
    assert "subset" in (rev.status_note or "")


def test_segment_disagreeing_disclosures_are_conflicting_not_guessed():
    contexts = {
        "t": _ctx("t", [("us-gaap:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember"), (SEG_AXIS, "x:AMember")]),
        "o": _ctx("o", [(SEG_AXIS, "x:AMember")]),
    }
    facts = [_num("us-gaap:Revenues", "t", 100.0), _num("us-gaap:Revenues", "o", 90.0)]
    rev = {f.concept: f for f in extract_segments_from_instance(contexts, facts, 2025).segments[0].facts}["revenue"]
    assert rev.data_status == DataStatus.CONFLICTING
    assert rev.value == 100.0  # reportable-segment table wins, and the note says so
    assert "differs" in rev.status_note


def test_segment_without_current_period_data_is_omitted_and_named():
    contexts = {
        "a": _ctx("a", [(SEG_AXIS, "x:LiveMember")]),
        "b": _ctx("b", [(SEG_AXIS, "x:OldMember")], start="2022-01-01", end="2022-12-31"),
    }
    facts = [_num("us-gaap:Revenues", "a", 5.0), _num("us-gaap:Revenues", "b", 9.0)]
    r = extract_segments_from_instance(contexts, facts, 2025)
    assert [s.name for s in r.segments] == ["Live"]
    assert "Old" in r.note


def test_nested_member_gets_parent_segment():
    contexts = {
        "p": _ctx("p", [(SEG_AXIS, "x:ParentMember")]),
        "c": _ctx("c", [(SEG_AXIS, "x:ChildMember")]),
    }
    facts = [_num("us-gaap:Revenues", "p", 10.0), _num("us-gaap:Revenues", "c", 4.0)]
    r = extract_segments_from_instance(contexts, facts, 2025, member_parents={"ChildMember": {"ParentMember"}})
    by = {s.name: s for s in r.segments}
    assert by["Child"].parent_segment == "Parent"
    assert by["Parent"].parent_segment is None
    assert "do not add" in r.note


def test_definition_linkbase_hierarchy_parser():
    from backend.data.xbrl_instance import parse_definition_hierarchy

    xml = b"""<linkbase xmlns="http://www.xbrl.org/2003/linkbase" xmlns:xlink="http://www.w3.org/1999/xlink">
    <definitionLink><loc xlink:href="x.xsd#x_ParentMember" xlink:label="p"/><loc xlink:href="x.xsd#x_ChildMember" xlink:label="c"/>
    <definitionArc xlink:arcrole="http://xbrl.org/int/dim/arcrole/domain-member" xlink:from="p" xlink:to="c"/></definitionLink></linkbase>"""
    assert parse_definition_hierarchy(xml) == {"ChildMember": {"ParentMember"}}
