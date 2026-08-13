"""MarketDataSnapshot model -- a single "as of" market data point for a Company."""

from datetime import datetime, date, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field, Relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MarketDataSnapshot(SQLModel, table=True):
    """
    A single company-level market data snapshot (price, market cap, shares
    outstanding, beta, dividend yield) as of a given date.

    Market data goes stale far faster than SEC filings, so every row carries
    explicit freshness provenance: ``source``, ``fetched_at`` (when this
    client retrieved it), and ``as_of`` (the latest-available quote
    date/timestamp reported by the source -- never real-time). Repeated
    fetches within the same day upsert in place rather than creating new
    rows, keyed on (company_id, as_of).
    """

    __tablename__ = "market_data_snapshot"
    __table_args__ = (
        UniqueConstraint("company_id", "as_of", name="uq_market_data_snapshot_company_as_of"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id", index=True, nullable=False)

    price: Optional[float] = Field(default=None)
    currency: Optional[str] = Field(default=None, max_length=8)
    market_cap: Optional[float] = Field(default=None)
    shares_outstanding: Optional[float] = Field(default=None)
    beta: Optional[float] = Field(default=None)
    dividend_yield: Optional[float] = Field(default=None)
    last_dividend_value: Optional[float] = Field(default=None)
    last_dividend_date: Optional[date] = Field(default=None)

    as_of: date = Field(index=True, description="Latest-available quote date reported by the source, not real-time")

    source: str = Field(max_length=64, description="e.g. YAHOO_FINANCE_YFINANCE")
    fetched_at: datetime = Field(default_factory=_utcnow, nullable=False)

    created_at: datetime = Field(default_factory=_utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=_utcnow, nullable=False)

    company: "Company" = Relationship(back_populates="market_data_snapshots")
