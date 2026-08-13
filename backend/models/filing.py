"""Filing model — a single SEC filing belonging to a Company."""

from datetime import datetime, date, timezone
from typing import List, Optional

from sqlmodel import SQLModel, Field, Relationship

from .enums import FilingType


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Filing(SQLModel, table=True):
    """A single SEC filing (10-K, 10-Q, etc.)."""

    __tablename__ = "filing"

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True, nullable=False)

    accession_number: str = Field(index=True, unique=True, max_length=32)
    form_type: FilingType = Field(default=FilingType.OTHER)
    filing_date: date
    period_of_report: Optional[date] = Field(default=None)
    fiscal_year: Optional[int] = Field(default=None)
    source_url: str = Field(max_length=1024)

    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)

    company: "Company" = Relationship(back_populates="filings")
    financial_facts: List["FinancialFact"] = Relationship(back_populates="filing")
