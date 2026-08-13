"""Market data router -- latest-available price/market-cap/beta/dividend data
for a ticker, backed by yfinance (see backend/services/market_data_client.py).

Not real-time: every response is explicitly framed as "latest available",
source-dependent, with freshness metadata (``as_of`` / ``fetched_at``).
"""

from __future__ import annotations

from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from backend.api.deps import get_market_data_client, get_sec_client
from backend.data.persistence import get_or_create_company, upsert_market_data_snapshot
from backend.db import get_session
from backend.models.market_data_snapshot import MarketDataSnapshot
from backend.schemas.company import DividendRead, MarketDataRead
from backend.services.market_data_client import (
    MarketDataError,
    MarketDataNotFoundError,
    MarketDataUnavailableError,
)
from backend.services.sec_client import SECError, SECNotFoundError, SECRateLimitError, SECUnavailableError

router = APIRouter(prefix="/companies", tags=["market-data"])


@router.get("/{ticker}/market-data", response_model=MarketDataRead)
def get_market_data(
    ticker: str,
    session: Session = Depends(get_session),
    market_client=Depends(get_market_data_client),
    sec_client=Depends(get_sec_client),
) -> MarketDataRead:
    try:
        snapshot = market_client.get_snapshot(ticker)
    except MarketDataNotFoundError:
        raise HTTPException(status_code=404, detail=f"No market data found for ticker: {ticker}")
    except MarketDataUnavailableError:
        raise HTTPException(status_code=503, detail="Market data provider is currently unavailable, please retry shortly")
    except MarketDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    # Best-effort DB persistence: resolve/create the Company via SEC CIK lookup
    # so market data snapshots link to the same company rows as SEC-derived
    # data. If SEC lookup fails, we still return the live market data --
    # persistence is a bonus, not a prerequisite for this endpoint.
    try:
        cik10 = sec_client.get_cik(ticker)
        company = get_or_create_company(session, ticker=ticker, cik10=cik10)
        as_of_date = _parse_as_of_date(snapshot.as_of)
        upsert_market_data_snapshot(
            session,
            company.id,
            MarketDataSnapshot(
                company_id=company.id,
                price=snapshot.price,
                currency=snapshot.currency,
                market_cap=snapshot.market_cap,
                shares_outstanding=snapshot.shares_outstanding,
                beta=snapshot.beta,
                dividend_yield=snapshot.dividend.dividend_yield,
                last_dividend_value=snapshot.dividend.last_dividend_value,
                last_dividend_date=_parse_iso_date(snapshot.dividend.last_dividend_date),
                as_of=as_of_date,
                source=snapshot.source,
                fetched_at=datetime.fromisoformat(snapshot.fetched_at),
            ),
        )
    except (SECError, SECNotFoundError, SECRateLimitError, SECUnavailableError):
        pass

    return MarketDataRead(
        ticker=snapshot.ticker,
        price=snapshot.price,
        currency=snapshot.currency,
        market_cap=snapshot.market_cap,
        shares_outstanding=snapshot.shares_outstanding,
        beta=snapshot.beta,
        dividend=DividendRead(
            dividend_yield=snapshot.dividend.dividend_yield,
            last_dividend_value=snapshot.dividend.last_dividend_value,
            last_dividend_date=_parse_iso_date(snapshot.dividend.last_dividend_date),
        ),
        as_of=snapshot.as_of,
        source=snapshot.source,
        fetched_at=snapshot.fetched_at,
        is_latest_available_not_realtime=snapshot.is_latest_available_not_realtime,
    )


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_as_of_date(as_of_iso: str | None) -> date:
    if as_of_iso:
        try:
            return datetime.fromisoformat(as_of_iso).date()
        except ValueError:
            pass
    return datetime.now().date()
