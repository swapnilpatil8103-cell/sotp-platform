"""Unit tests for the MarketDataSnapshot upsert helper (SQLite test DB)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlmodel import select

from backend.data.persistence import get_or_create_company, upsert_market_data_snapshot
from backend.models.market_data_snapshot import MarketDataSnapshot


def test_upsert_market_data_snapshot_no_duplicates_on_same_day(session):
    company = get_or_create_company(session, ticker="AAPL", cik10="0000320193", name="Apple Inc.")
    as_of = date(2026, 8, 11)

    snap1 = MarketDataSnapshot(
        company_id=company.id,
        price=227.5,
        currency="USD",
        market_cap=3_450_000_000_000.0,
        shares_outstanding=15_150_000_000.0,
        beta=1.2,
        as_of=as_of,
        source="YAHOO_FINANCE_YFINANCE",
        fetched_at=datetime.now(timezone.utc),
    )
    persisted1 = upsert_market_data_snapshot(session, company.id, snap1)
    assert persisted1.id is not None

    snap2 = MarketDataSnapshot(
        company_id=company.id,
        price=230.0,
        currency="USD",
        market_cap=3_500_000_000_000.0,
        shares_outstanding=15_150_000_000.0,
        beta=1.25,
        as_of=as_of,
        source="YAHOO_FINANCE_YFINANCE",
        fetched_at=datetime.now(timezone.utc),
    )
    persisted2 = upsert_market_data_snapshot(session, company.id, snap2)

    assert persisted2.id == persisted1.id
    assert persisted2.price == 230.0

    all_rows = session.exec(
        select(MarketDataSnapshot).where(MarketDataSnapshot.company_id == company.id)
    ).all()
    assert len(all_rows) == 1


def test_upsert_market_data_snapshot_new_row_for_new_day(session):
    company = get_or_create_company(session, ticker="MSFT", cik10="0000789019", name="Microsoft Corp")

    snap_day1 = MarketDataSnapshot(
        company_id=company.id,
        price=400.0,
        as_of=date(2026, 8, 10),
        source="YAHOO_FINANCE_YFINANCE",
        fetched_at=datetime.now(timezone.utc),
    )
    snap_day2 = MarketDataSnapshot(
        company_id=company.id,
        price=405.0,
        as_of=date(2026, 8, 11),
        source="YAHOO_FINANCE_YFINANCE",
        fetched_at=datetime.now(timezone.utc),
    )
    upsert_market_data_snapshot(session, company.id, snap_day1)
    upsert_market_data_snapshot(session, company.id, snap_day2)

    all_rows = session.exec(
        select(MarketDataSnapshot).where(MarketDataSnapshot.company_id == company.id)
    ).all()
    assert len(all_rows) == 2
