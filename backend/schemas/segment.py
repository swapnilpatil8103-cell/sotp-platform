"""Pydantic response schemas for the /segments endpoint."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class SegmentFactRead(BaseModel):
    concept: str
    value: Optional[float] = None
    unit: str
    currency: str
    period: str
    fiscal_year: int
    fiscal_period: str
    source: str
    source_url: Optional[str] = None
    accession_number: Optional[str] = None
    xbrl_tag: Optional[str] = None
    data_status: str
    status_note: Optional[str] = None


class MetricCoverageRead(BaseModel):
    metric: str
    reported_periods: int
    total_periods: int
    coverage_pct: float


class SegmentRead(BaseModel):
    name: str
    # Set when the filing nests this member under another listed segment (e.g. Intel's CCG and
    # DCAI under Intel Products). Its amounts are already INCLUDED in the parent's: do not add them.
    parent_segment: Optional[str] = None
    facts: list[SegmentFactRead]
    coverage: list[MetricCoverageRead]
    overall_coverage_pct: float


class CompanySegmentsRead(BaseModel):
    ticker: str
    cik: str
    fiscal_year: int
    fiscal_period: str
    segments: list[SegmentRead]
    overall_coverage_pct: float
    note: Optional[str] = None
