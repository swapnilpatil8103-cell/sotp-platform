"""Upsert helpers for persisting normalized facts to the DB.

Keyed on natural keys so repeated ingestion calls never duplicate rows:
- FinancialFact: (company_id, concept, period, xbrl_tag)
- SegmentFinancialFact: (segment_id, concept, period, xbrl_tag)
- Segment: (company_id, name)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlmodel import Session, select

from backend.models.company import Company
from backend.models.financial_fact import FinancialFact
from backend.models.institutional_holding import InstitutionalHolding
from backend.models.insider_transaction import InsiderTransaction
from backend.models.market_data_snapshot import MarketDataSnapshot
from backend.models.segment import Segment
from backend.models.segment_financial_fact import SegmentFinancialFact


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_company(session: Session, ticker: str, cik10: str, name: str = "") -> Company:
    ticker = ticker.upper()
    company = session.exec(select(Company).where(Company.ticker == ticker)).first()
    if company is not None:
        return company
    company = session.exec(select(Company).where(Company.cik == cik10)).first()
    if company is not None:
        return company
    company = Company(ticker=ticker, cik=cik10, name=name or ticker)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def upsert_financial_facts(session: Session, company_id: int, facts: Iterable[FinancialFact]) -> list[FinancialFact]:
    """Insert or update FinancialFact rows, keyed on (company_id, concept, period, xbrl_tag)."""
    persisted: list[FinancialFact] = []
    for fact in facts:
        existing = session.exec(
            select(FinancialFact).where(
                FinancialFact.company_id == company_id,
                FinancialFact.concept == fact.concept,
                FinancialFact.period == fact.period,
                FinancialFact.xbrl_tag == fact.xbrl_tag,
            )
        ).first()
        if existing is not None:
            existing.value = fact.value
            existing.unit = fact.unit
            existing.currency = fact.currency
            existing.fiscal_year = fact.fiscal_year
            existing.fiscal_period = fact.fiscal_period
            existing.filing_date = fact.filing_date
            existing.source = fact.source
            existing.source_url = fact.source_url
            existing.accession_number = fact.accession_number
            existing.data_status = fact.data_status
            existing.updated_at = _utcnow()
            session.add(existing)
            persisted.append(existing)
        else:
            fact.company_id = company_id
            session.add(fact)
            persisted.append(fact)
    session.commit()
    for f in persisted:
        session.refresh(f)
    return persisted


def facts_are_fresh(session: Session, company_id: int, fiscal_year: int, fiscal_period: str) -> bool:
    """True if we already have any persisted FinancialFact rows for this company/period.

    Used to decide whether to skip a re-fetch from SEC EDGAR. "Freshness" here
    just means "already ingested" — SEC data is cached separately (24h TTL) at
    the HTTP layer, so this is a coarser DB-level short-circuit.
    """
    existing = session.exec(
        select(FinancialFact).where(
            FinancialFact.company_id == company_id,
            FinancialFact.fiscal_year == fiscal_year,
            FinancialFact.fiscal_period == fiscal_period,
        )
    ).first()
    return existing is not None


def get_persisted_facts(session: Session, company_id: int, fiscal_year: int, fiscal_period: str) -> list[FinancialFact]:
    return list(
        session.exec(
            select(FinancialFact).where(
                FinancialFact.company_id == company_id,
                FinancialFact.fiscal_year == fiscal_year,
                FinancialFact.fiscal_period == fiscal_period,
            )
        ).all()
    )


def get_or_create_segment(session: Session, company_id: int, name: str, description: Optional[str] = None) -> Segment:
    segment = session.exec(
        select(Segment).where(Segment.company_id == company_id, Segment.name == name)
    ).first()
    if segment is not None:
        return segment
    segment = Segment(company_id=company_id, name=name, description=description)
    session.add(segment)
    session.commit()
    session.refresh(segment)
    return segment


def upsert_segment_financial_facts(
    session: Session, segment_id: int, facts: Iterable[SegmentFinancialFact]
) -> list[SegmentFinancialFact]:
    persisted: list[SegmentFinancialFact] = []
    for fact in facts:
        existing = session.exec(
            select(SegmentFinancialFact).where(
                SegmentFinancialFact.segment_id == segment_id,
                SegmentFinancialFact.concept == fact.concept,
                SegmentFinancialFact.period == fact.period,
                SegmentFinancialFact.xbrl_tag == fact.xbrl_tag,
            )
        ).first()
        if existing is not None:
            existing.value = fact.value
            existing.unit = fact.unit
            existing.currency = fact.currency
            existing.fiscal_year = fact.fiscal_year
            existing.fiscal_period = fact.fiscal_period
            existing.filing_date = fact.filing_date
            existing.source = fact.source
            existing.source_url = fact.source_url
            existing.accession_number = fact.accession_number
            existing.data_status = fact.data_status
            existing.updated_at = _utcnow()
            session.add(existing)
            persisted.append(existing)
        else:
            fact.segment_id = segment_id
            session.add(fact)
            persisted.append(fact)
    session.commit()
    for f in persisted:
        session.refresh(f)
    return persisted


def upsert_market_data_snapshot(session: Session, company_id: int, snapshot: MarketDataSnapshot) -> MarketDataSnapshot:
    """Insert or update a MarketDataSnapshot row, keyed on (company_id, as_of).

    Repeated fetches for the same company on the same "as of" date update the
    existing row in place instead of duplicating it.
    """
    existing = session.exec(
        select(MarketDataSnapshot).where(
            MarketDataSnapshot.company_id == company_id,
            MarketDataSnapshot.as_of == snapshot.as_of,
        )
    ).first()
    if existing is not None:
        existing.price = snapshot.price
        existing.currency = snapshot.currency
        existing.market_cap = snapshot.market_cap
        existing.shares_outstanding = snapshot.shares_outstanding
        existing.beta = snapshot.beta
        existing.dividend_yield = snapshot.dividend_yield
        existing.last_dividend_value = snapshot.last_dividend_value
        existing.last_dividend_date = snapshot.last_dividend_date
        existing.source = snapshot.source
        existing.fetched_at = snapshot.fetched_at
        existing.updated_at = _utcnow()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    snapshot.company_id = company_id
    session.add(snapshot)
    session.commit()
    session.refresh(snapshot)
    return snapshot


def upsert_insider_transactions(
    session: Session, company_id: int, transactions: Iterable[InsiderTransaction]
) -> list[InsiderTransaction]:
    """Insert or update InsiderTransaction rows, keyed on
    (accession_number, reporting_owner_cik, security_title, transaction_date)
    so re-ingesting the same Form 3/4/5 filing never duplicates a row."""
    persisted: list[InsiderTransaction] = []
    for txn in transactions:
        existing = session.exec(
            select(InsiderTransaction).where(
                InsiderTransaction.accession_number == txn.accession_number,
                InsiderTransaction.reporting_owner_cik == txn.reporting_owner_cik,
                InsiderTransaction.security_title == txn.security_title,
                InsiderTransaction.transaction_date == txn.transaction_date,
            )
        ).first()
        if existing is not None:
            existing.company_id = company_id
            existing.reporting_owner_name = txn.reporting_owner_name
            existing.is_officer = txn.is_officer
            existing.is_director = txn.is_director
            existing.is_ten_percent_owner = txn.is_ten_percent_owner
            existing.is_other = txn.is_other
            existing.officer_title = txn.officer_title
            existing.transaction_table = txn.transaction_table
            existing.transaction_code = txn.transaction_code
            existing.shares_transacted = txn.shares_transacted
            existing.price_per_share = txn.price_per_share
            existing.transaction_acquired_disposed_code = txn.transaction_acquired_disposed_code
            existing.shares_owned_after = txn.shares_owned_after
            existing.ownership_type = txn.ownership_type
            existing.source = txn.source
            existing.source_url = txn.source_url
            existing.filing_date = txn.filing_date
            existing.data_status = txn.data_status
            existing.updated_at = _utcnow()
            session.add(existing)
            persisted.append(existing)
        else:
            txn.company_id = company_id
            session.add(txn)
            persisted.append(txn)
    session.commit()
    for t in persisted:
        session.refresh(t)
    return persisted


def upsert_institutional_holdings(
    session: Session, holdings: Iterable[InstitutionalHolding]
) -> list[InstitutionalHolding]:
    """Insert or update InstitutionalHolding rows, keyed on
    (filer_cik, accession_number, cusip) so re-ingesting the same 13F
    information table never duplicates a row.

    Unlike the other upsert helpers here, this one takes no `company_id` --
    13F holdings are filer-centric, not issuer-centric (see
    `backend.models.institutional_holding` and `docs/data-model.md`)."""
    persisted: list[InstitutionalHolding] = []
    for holding in holdings:
        existing = session.exec(
            select(InstitutionalHolding).where(
                InstitutionalHolding.filer_cik == holding.filer_cik,
                InstitutionalHolding.accession_number == holding.accession_number,
                InstitutionalHolding.cusip == holding.cusip,
            )
        ).first()
        if existing is not None:
            existing.filer_name = holding.filer_name
            existing.issuer_name = holding.issuer_name
            existing.title_of_class = holding.title_of_class
            existing.period_of_report = holding.period_of_report
            existing.value = holding.value
            existing.shares_or_principal_amount = holding.shares_or_principal_amount
            existing.shares_or_principal_type = holding.shares_or_principal_type
            existing.investment_discretion = holding.investment_discretion
            existing.voting_authority_sole = holding.voting_authority_sole
            existing.voting_authority_shared = holding.voting_authority_shared
            existing.voting_authority_none = holding.voting_authority_none
            existing.source = holding.source
            existing.source_url = holding.source_url
            existing.filing_date = holding.filing_date
            existing.data_status = holding.data_status
            existing.updated_at = _utcnow()
            session.add(existing)
            persisted.append(existing)
        else:
            session.add(holding)
            persisted.append(holding)
    session.commit()
    for h in persisted:
        session.refresh(h)
    return persisted
