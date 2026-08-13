"""Market data client (yfinance-backed).

Retrieves latest-available price, market cap, shares outstanding, beta,
dividend info, and historical daily prices for a ticker. Follows the same
conventions as ``backend/services/sec_client.py``:

- Typed exceptions for failure modes; callers (the API layer) never see raw
  yfinance/network exceptions.
- Disk caching via ``backend/services/cache.py`` -- but with much shorter
  TTLs than SEC filings, since market data goes stale in minutes, not days.
- Every returned value is explicitly "latest available" / "source-dependent",
  never framed as real-time tick data. yfinance sources data from Yahoo
  Finance, which itself is typically delayed and best-effort -- this client
  makes no real-time guarantees.

Caching strategy:
- Price / market cap / shares outstanding: ``PRICE_CACHE_TTL_SECONDS`` (15 min)
- Beta / dividend info / historical prices: ``SLOW_CACHE_TTL_SECONDS`` (24h)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.services.cache import DEFAULT_CACHE_DIR, FileCache

PRICE_CACHE_TTL_SECONDS = 15 * 60  # 15 minutes
SLOW_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours

MARKET_DATA_CACHE_DIR = DEFAULT_CACHE_DIR.parent / "market_data"

SOURCE_NAME = "YAHOO_FINANCE_YFINANCE"


class MarketDataError(Exception):
    """Base class for all market data client errors."""


class MarketDataNotFoundError(MarketDataError):
    """Raised when a ticker cannot be resolved / has no market data available."""


class MarketDataUnavailableError(MarketDataError):
    """Raised for network failures, timeouts, or unexpected upstream errors."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class DividendInfo:
    """Latest-available dividend data. All fields optional -- yfinance omits
    them entirely for non-dividend-paying companies; we never estimate."""

    dividend_yield: Optional[float] = None  # trailing dividend yield, e.g. 0.0051 = 0.51%
    last_dividend_value: Optional[float] = None
    last_dividend_date: Optional[str] = None  # ISO date string, if available


@dataclass
class HistoricalPricePoint:
    date: str  # ISO date
    close: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None


@dataclass
class MarketDataSnapshotData:
    """A single "as of" snapshot of market data for a ticker.

    This is NOT real-time tick data. ``as_of`` reflects the latest available
    quote/timestamp yfinance/Yahoo Finance reports (itself typically delayed
    and best-effort), and ``fetched_at`` reflects when this client retrieved
    it. Any field yfinance doesn't report is left ``None`` -- never
    estimated or backfilled.
    """

    ticker: str
    price: Optional[float] = None
    currency: Optional[str] = None
    market_cap: Optional[float] = None
    shares_outstanding: Optional[float] = None
    beta: Optional[float] = None
    dividend: DividendInfo = field(default_factory=DividendInfo)
    as_of: Optional[str] = None  # latest-available quote timestamp/date reported by the source
    source: str = SOURCE_NAME
    fetched_at: str = field(default_factory=lambda: _utcnow().isoformat())
    is_latest_available_not_realtime: bool = True


class MarketDataClient:
    """Client for fetching market data via yfinance, with caching and typed errors."""

    def __init__(
        self,
        cache: Optional[FileCache] = None,
        price_ttl_seconds: int = PRICE_CACHE_TTL_SECONDS,
        slow_ttl_seconds: int = SLOW_CACHE_TTL_SECONDS,
    ):
        self._cache = cache or FileCache(cache_dir=MARKET_DATA_CACHE_DIR, ttl_seconds=price_ttl_seconds)
        self._price_ttl = price_ttl_seconds
        self._slow_ttl = slow_ttl_seconds

    # ------------------------------------------------------------- Helpers

    def _get_yf_ticker(self, ticker: str):
        try:
            import yfinance as yf
        except ImportError as exc:  # pragma: no cover - dependency always installed
            raise MarketDataUnavailableError("yfinance is not installed") from exc
        return yf.Ticker(ticker.upper())

    def _fetch_info(self, ticker: str) -> dict[str, Any]:
        """Fetch (and cache) the raw ``.info`` dict for a ticker."""
        cache_key = f"info:{ticker.upper()}"
        cached = self._cache_get(cache_key, self._price_ttl)
        if cached is not None:
            return cached

        try:
            yf_ticker = self._get_yf_ticker(ticker)
            info = yf_ticker.info or {}
        except Exception as exc:  # yfinance raises assorted exception types on network/parse errors
            raise MarketDataUnavailableError(f"Failed to fetch market data for {ticker}: {exc}") from exc

        if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None and info.get("previousClose") is None:
            # yfinance returns a near-empty dict (rather than raising) for unknown tickers.
            raise MarketDataNotFoundError(f"No market data found for ticker: {ticker}")

        self._cache_set(cache_key, info)
        return info

    def _fetch_history(self, ticker: str, period: str = "2y", interval: str = "1d") -> list[dict[str, Any]]:
        cache_key = f"history:{ticker.upper()}:{period}:{interval}"
        cached = self._cache_get(cache_key, self._slow_ttl)
        if cached is not None:
            return cached

        try:
            yf_ticker = self._get_yf_ticker(ticker)
            hist = yf_ticker.history(period=period, interval=interval)
        except Exception as exc:
            raise MarketDataUnavailableError(f"Failed to fetch historical prices for {ticker}: {exc}") from exc

        if hist is None or hist.empty:
            raise MarketDataNotFoundError(f"No historical price data found for ticker: {ticker}")

        records: list[dict[str, Any]] = []
        for idx, row in hist.iterrows():
            records.append(
                {
                    "date": idx.date().isoformat() if hasattr(idx, "date") else str(idx),
                    "close": _safe_float(row.get("Close")),
                    "open": _safe_float(row.get("Open")),
                    "high": _safe_float(row.get("High")),
                    "low": _safe_float(row.get("Low")),
                    "volume": _safe_float(row.get("Volume")),
                }
            )
        self._cache_set(cache_key, records)
        return records

    def _cache_get(self, key: str, ttl_seconds: int) -> Optional[Any]:
        path = self._cache._path_for(key)  # reuse FileCache's hashing/path scheme
        if not path.exists():
            return None
        import json

        try:
            with path.open("r", encoding="utf-8") as f:
                envelope = json.load(f)
        except (OSError, ValueError):
            return None
        cached_at = envelope.get("cached_at", 0)
        if time.time() - cached_at > ttl_seconds:
            return None
        return envelope.get("payload")

    def _cache_set(self, key: str, payload: Any) -> None:
        self._cache.set(key, payload)

    # --------------------------------------------------------------- API

    def get_snapshot(self, ticker: str) -> MarketDataSnapshotData:
        """Return a full market data snapshot: price, market cap, shares
        outstanding, beta, and dividend info -- all "latest available",
        never real-time."""
        info = self._fetch_info(ticker)

        price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
        currency = info.get("currency")
        market_cap = info.get("marketCap")
        shares_outstanding = info.get("sharesOutstanding")
        beta = info.get("beta")

        dividend_yield = info.get("dividendYield")
        last_dividend_value = info.get("lastDividendValue")
        last_dividend_raw = info.get("lastDividendDate")
        last_dividend_date = None
        if last_dividend_raw:
            try:
                last_dividend_date = datetime.fromtimestamp(last_dividend_raw, tz=timezone.utc).date().isoformat()
            except (TypeError, ValueError, OSError):
                last_dividend_date = None

        as_of_raw = info.get("regularMarketTime")
        as_of = None
        if as_of_raw:
            try:
                as_of = datetime.fromtimestamp(as_of_raw, tz=timezone.utc).isoformat()
            except (TypeError, ValueError, OSError):
                as_of = None

        return MarketDataSnapshotData(
            ticker=ticker.upper(),
            price=_safe_float(price),
            currency=currency,
            market_cap=_safe_float(market_cap),
            shares_outstanding=_safe_float(shares_outstanding),
            beta=_safe_float(beta),
            dividend=DividendInfo(
                dividend_yield=_safe_float(dividend_yield),
                last_dividend_value=_safe_float(last_dividend_value),
                last_dividend_date=last_dividend_date,
            ),
            as_of=as_of,
        )

    def get_historical_prices(self, ticker: str, period: str = "2y") -> list[HistoricalPricePoint]:
        """Return daily historical OHLCV prices for the given lookback period
        (default last 2 years) for charting purposes."""
        records = self._fetch_history(ticker, period=period, interval="1d")
        return [HistoricalPricePoint(**r) for r in records]


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
