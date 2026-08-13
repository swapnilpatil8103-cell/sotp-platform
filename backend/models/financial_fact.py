"""FinancialFact model — a single sourced/derived financial data point."""

from datetime import datetime, date, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field, Relationship

from .enums import DataStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FinancialFact(SQLModel, table=True):
    """
    A single company-level financial data point (e.g. Revenue for FY2023).

    Every fact carries full provenance: which filing it came from, the XBRL
    tag, and a data_status describing whether it was reported verbatim,
    derived, estimated, missing, or conflicting across sources.
    """

    __tablename__ = "financial_fact"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "concept", "period", "xbrl_tag", name="uq_financial_fact_natural_key"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True, nullable=False)
    filing_id: Optional[int] = Field(default=None, foreign_key="filing.id", index=True)

    concept: str = Field(index=True, max_length=128, description="e.g. Revenue, NetIncome, TotalAssets")
    value: Optional[float] = Field(default=None)
    unit: str = Field(default="USD", max_length=16)
    currency: str = Field(default="USD", max_length=8)

    period: str = Field(max_length=32, description="e.g. FY2023, Q2FY2024")
    fiscal_year: int
    fiscal_period: str = Field(max_length=8, description="e.g. FY, Q1, Q2, Q3, Q4")
    filing_date: Optional[date] = Field(default=None)

    source: str = Field(max_length=64, description="e.g. SEC_EDGAR_XBRL")
    source_url: Optional[str] = Field(default=None, max_length=1024)
    accession_number: Optional[str] = Field(default=None, max_length=32, index=True)
    xbrl_tag: Optional[str] = Field(default=None, max_length=256)

    data_status: DataStatus = Field(default=DataStatus.MISSING)

    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)

    company: "Company" = Relationship(back_populates="financial_facts")
    filing: Optional["Filing"] = Relationship(back_populates="financial_facts")
