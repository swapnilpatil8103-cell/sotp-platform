"""Deterministic coverage scoring for extracted segment data.

For each discovered segment, computes what fraction of a set of periods have
a REPORTED (vs MISSING/other) value for each tracked metric, plus an overall
segment-level and company-level coverage score. Pure, deterministic, no AI
involved -- this is a data-completeness metric, not a valuation judgment.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.models.enums import DataStatus
from backend.models.segment_financial_fact import SegmentFinancialFact

TRACKED_METRICS: list[str] = ["revenue", "operating_income", "da", "capex", "assets"]


@dataclass
class MetricCoverage:
    metric: str
    reported_periods: int
    total_periods: int

    @property
    def coverage_pct(self) -> float:
        if self.total_periods == 0:
            return 0.0
        return round(100.0 * self.reported_periods / self.total_periods, 2)


@dataclass
class SegmentCoverage:
    segment_name: str
    metrics: dict[str, MetricCoverage] = field(default_factory=dict)

    @property
    def overall_coverage_pct(self) -> float:
        if not self.metrics:
            return 0.0
        return round(sum(m.coverage_pct for m in self.metrics.values()) / len(self.metrics), 2)


def compute_segment_coverage(
    segment_name: str,
    facts: list[SegmentFinancialFact],
    tracked_metrics: list[str] = TRACKED_METRICS,
) -> SegmentCoverage:
    """Compute per-metric and overall coverage % for one segment's facts.

    ``facts`` should span the periods you want scored (e.g. the last N fiscal
    years of that segment's SegmentFinancialFact rows, one row per
    concept/period). Coverage = REPORTED rows / total rows seen for that
    metric, expressed as a percentage.
    """
    by_metric: dict[str, list[SegmentFinancialFact]] = {m: [] for m in tracked_metrics}
    for f in facts:
        if f.concept in by_metric:
            by_metric[f.concept].append(f)

    metrics: dict[str, MetricCoverage] = {}
    for metric, metric_facts in by_metric.items():
        total = len(metric_facts)
        reported = sum(1 for f in metric_facts if f.data_status == DataStatus.REPORTED)
        metrics[metric] = MetricCoverage(metric=metric, reported_periods=reported, total_periods=total)

    return SegmentCoverage(segment_name=segment_name, metrics=metrics)


def compute_company_segment_coverage(
    segments_facts: dict[str, list[SegmentFinancialFact]],
    tracked_metrics: list[str] = TRACKED_METRICS,
) -> tuple[dict[str, SegmentCoverage], float]:
    """Compute coverage for every segment, plus an overall company-level score
    (mean of each segment's overall_coverage_pct)."""
    per_segment = {
        name: compute_segment_coverage(name, facts, tracked_metrics) for name, facts in segments_facts.items()
    }
    if not per_segment:
        return per_segment, 0.0
    overall = round(sum(c.overall_coverage_pct for c in per_segment.values()) / len(per_segment), 2)
    return per_segment, overall
