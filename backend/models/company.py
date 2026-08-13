"""Company model."""

from datetime import datetime, timezone
from typing import List, Optional

from sqlmodel import SQLModel, Field, Relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Company(SQLModel, table=True):
    """A publicly traded company covered by the platform."""

    __tablename__ = "company"

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(index=True, unique=True, max_length=16)
    cik: str = Field(index=True, unique=True, max_length=10, description="SEC CIK, zero-padded")
    name: str = Field(max_length=256)
    sector: Optional[str] = Field(default=None, max_length=128)
    industry: Optional[str] = Field(default=None, max_length=128)
    exchange: Optional[str] = Field(default=None, max_length=32)
    currency: str = Field(default="USD", max_length=8)

    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)

    filings: List["Filing"] = Relationship(back_populates="company")
    financial_facts: List["FinancialFact"] = Relationship(back_populates="company")
    segments: List["Segment"] = Relationship(back_populates="company")
    valuation_runs: List["ValuationRun"] = Relationship(back_populates="company")
    market_data_snapshots: List["MarketDataSnapshot"] = Relationship(back_populates="company")
