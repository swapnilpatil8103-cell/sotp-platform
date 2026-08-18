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


class InsiderTransactionRead(BaseModel):
    reporting_owner_name: Optional[str] = None
    reporting_owner_cik: Optional[str] = None
    is_officer: Optional[bool] = None
    is_director: Optional[bool] = None
    is_ten_percent_owner: Optional[bool] = None
    is_other: Optional[bool] = None
    officer_title: Optional[str] = None
    transaction_table: str
    security_title: Optional[str] = None
    transaction_date: Optional[date] = None
    transaction_code: Optional[str] = None
    shares_transacted: Optional[float] = None
    price_per_share: Optional[float] = None
    transaction_acquired_disposed_code: Optional[str] = None
    shares_owned_after: Optional[float] = None
    ownership_type: Optional[str] = None
    accession_number: str
    source: str
    source_url: Optional[str] = None
    filing_date: Optional[date] = None
    data_status: str


class InstitutionalHoldingRead(BaseModel):
    filer_cik: str
    filer_name: Optional[str] = None
    issuer_name: Optional[str] = None
    title_of_class: Optional[str] = None
    cusip: Optional[str] = None
    period_of_report: Optional[date] = None
    value: Optional[float] = None
    shares_or_principal_amount: Optional[float] = None
    shares_or_principal_type: Optional[str] = None
    investment_discretion: Optional[str] = None
    voting_authority_sole: Optional[float] = None
    voting_authority_shared: Optional[float] = None
    voting_authority_none: Optional[float] = None
    accession_number: str
    source: str
    source_url: Optional[str] = None
    filing_date: Optional[date] = None
    data_status: str


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
