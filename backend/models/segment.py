"""Segment model — a reporting segment/business unit of a Company."""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field, Relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Segment(SQLModel, table=True):
    """A business/reporting segment of a Company, used for SOTP analysis."""

    __tablename__ = "segment"
    __table_args__ = (UniqueConstraint("company_id", "name", name="uq_segment_company_name"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True, nullable=False)

    name: str = Field(max_length=256)
    description: Optional[str] = Field(default=None, max_length=2048)

    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)

    company: "Company" = Relationship(back_populates="segments")
    financial_facts: List["SegmentFinancialFact"] = Relationship(back_populates="segment")
