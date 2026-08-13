"""SegmentFinancialFact model — a financial data point scoped to a Segment."""

from datetime import datetime, date, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field, Relationship

from .enums import DataStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SegmentFinancialFact(SQLModel, table=True):
    """
    A single segment-level financial data point (e.g. Revenue for the
    Cloud segment, FY2023). Mirrors FinancialFact's provenance fields.
    """

    __tablename__ = "segment_financial_fact"
    __table_args__ = (
        UniqueConstraint(
            "segment_id", "concept", "period", "xbrl_tag", name="uq_segment_financial_fact_natural_key"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    segment_id: int = Field(foreign_key="segment.id", index=True, nullable=False)
    filing_id: Optional[int] = Field(default=None, foreign_key="filing.id", index=True)

    concept: str = Field(index=True, max_length=128)
    value: Optional[float] = Field(default=None)
    unit: str = Field(default="USD", max_length=16)
    currency: str = Field(default="USD", max_length=8)

    period: str = Field(max_length=32)
    fiscal_year: int
    fiscal_period: str = Field(max_length=8)
    filing_date: Optional[date] = Field(default=None)

    source: str = Field(max_length=64, description="e.g. SEC_EDGAR_XBRL")
    source_url: Optional[str] = Field(default=None, max_length=1024)
    accession_number: Optional[str] = Field(default=None, max_length=32, index=True)
    xbrl_tag: Optional[str] = Field(default=None, max_length=256)

    data_status: DataStatus = Field(default=DataStatus.MISSING)

    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)

    segment: "Segment" = Relationship(back_populates="financial_facts")
