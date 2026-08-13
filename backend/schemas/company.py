"""Pydantic response schemas for the /companies and /filings endpoints."""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel


class CompanyRead(BaseModel):
    ticker: str
    cik: str
    name: str
    sic: Optional[str] = None
    sic_description: Optional[str] = None
    exchange: Optional[str] = None
    fiscal_year_end: Optional[str] = None
    company_id: Optional[int] = None


class FilingRead(BaseModel):
    form: str
    accession_number: str
    filing_date: Optional[date] = None
    period_of_report: Optional[date] = None
    source_url: str


class FinancialFactRead(BaseModel):
    concept: str
    value: Optional[float] = None
    unit: str
    currency: str
    period: str
    fiscal_year: int
    fiscal_period: str
    filing_date: Optional[date] = None
    source: str
    source_url: Optional[str] = None
    accession_number: Optional[str] = None
    xbrl_tag: Optional[str] = None
    data_status: str


class CompanyFactsRead(BaseModel):
    ticker: str
    cik: str
    fiscal_year: int
    fiscal_period: str
    facts: list[FinancialFactRead]


class DividendRead(BaseModel):
    dividend_yield: Optional[float] = None
    last_dividend_value: Optional[float] = None
    last_dividend_date: Optional[date] = None


class MarketDataRead(BaseModel):
    ticker: str
    price: Optional[float] = None
    currency: Optional[str] = None
    market_cap: Optional[float] = None
    shares_outstanding: Optional[float] = None
    beta: Optional[float] = None
    dividend: DividendRead
    as_of: Optional[str] = None
    source: str
    fetched_at: str
    is_latest_available_not_realtime: bool = True
