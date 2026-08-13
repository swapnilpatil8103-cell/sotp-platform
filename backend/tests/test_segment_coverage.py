"""Unit tests for deterministic segment coverage scoring."""

from __future__ import annotations

from backend.data.segment_coverage import compute_company_segment_coverage, compute_segment_coverage
from backend.models.enums import DataStatus
from backend.models.segment_financial_fact import SegmentFinancialFact


def _fact(concept: str, status: DataStatus, year: int) -> SegmentFinancialFact:
    return SegmentFinancialFact(
        segment_id=1,
        concept=concept,
        value=100.0 if status == DataStatus.REPORTED else None,
        period=f"{year}-FY",
        fiscal_year=year,
        fiscal_period="FY",
        source="SEC XBRL",
        data_status=status,
    )


def test_metric_coverage_pct():
    facts = [
        _fact("revenue", DataStatus.REPORTED, 2021),
        _fact("revenue", DataStatus.REPORTED, 2022),
        _fact("revenue", DataStatus.MISSING, 2023),
    ]
    cov = compute_segment_coverage("Alpha", facts, tracked_metrics=["revenue", "operating_income"])
    assert cov.metrics["revenue"].coverage_pct == 66.67
    assert cov.metrics["operating_income"].total_periods == 0
    assert cov.metrics["operating_income"].coverage_pct == 0.0


def test_overall_company_coverage_averages_segments():
    segments_facts = {
        "Alpha": [_fact("revenue", DataStatus.REPORTED, 2023)],
        "Beta": [_fact("revenue", DataStatus.MISSING, 2023)],
    }
    per_segment, overall = compute_company_segment_coverage(segments_facts, tracked_metrics=["revenue"])
    assert per_segment["Alpha"].overall_coverage_pct == 100.0
    assert per_segment["Beta"].overall_coverage_pct == 0.0
    assert overall == 50.0


def test_empty_facts_gives_zero_coverage():
    per_segment, overall = compute_company_segment_coverage({})
    assert per_segment == {}
    assert overall == 0.0
