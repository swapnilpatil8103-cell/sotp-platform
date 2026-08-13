"""Unit tests for backend.services.market_data_client -- mocked yfinance responses
(no real network access). Also a real-network integration test, marked so it
can be skipped in CI.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from backend.services.cache import FileCache
from backend.services.market_data_client import (
    MarketDataClient,
    MarketDataNotFoundError,
    MarketDataUnavailableError,
)

FAKE_INFO = {
    "currentPrice": 227.5,
    "regularMarketPrice": 227.5,
    "previousClose": 226.0,
    "currency": "USD",
    "marketCap": 3_450_000_000_000.0,
    "sharesOutstanding": 15_150_000_000.0,
    "beta": 1.2,
    "dividendYield": 0.0051,
    "lastDividendValue": 0.25,
    "lastDividendDate": 1731542400,  # 2024-11-14 UTC
    "regularMarketTime": 1731600000,
}


@pytest.fixture
def market_client(tmp_path):
    return MarketDataClient(cache=FileCache(cache_dir=tmp_path, ttl_seconds=15 * 60))


class _FakeYFTicker:
    def __init__(self, info=None, history_df=None):
        self.info = info
        self._history_df = history_df

    def history(self, period="2y", interval="1d"):
        return self._history_df


def test_get_snapshot_parses_price_and_market_cap(market_client, monkeypatch):
    monkeypatch.setattr(market_client, "_get_yf_ticker", lambda t: _FakeYFTicker(info=FAKE_INFO))
    snapshot = market_client.get_snapshot("AAPL")

    assert snapshot.ticker == "AAPL"
    assert snapshot.price == 227.5
    assert snapshot.market_cap == 3_450_000_000_000.0
    assert snapshot.shares_outstanding == 15_150_000_000.0
    assert snapshot.beta == 1.2
    assert snapshot.dividend.dividend_yield == 0.0051
    assert snapshot.dividend.last_dividend_value == 0.25
    assert snapshot.dividend.last_dividend_date == "2024-11-14"
    assert snapshot.source == "YAHOO_FINANCE_YFINANCE"
    assert snapshot.is_latest_available_not_realtime is True
    assert snapshot.as_of is not None
    assert snapshot.fetched_at is not None


def test_get_snapshot_missing_beta_and_dividend_left_none(market_client, monkeypatch):
    info = {
        "currentPrice": 10.0,
        "currency": "USD",
        "marketCap": 1_000_000.0,
        "sharesOutstanding": 100_000.0,
        # no beta, no dividend fields -- some tickers (e.g. small caps) omit these
    }
    monkeypatch.setattr(market_client, "_get_yf_ticker", lambda t: _FakeYFTicker(info=info))
    snapshot = market_client.get_snapshot("SMALLCO")

    assert snapshot.beta is None
    assert snapshot.dividend.dividend_yield is None
    assert snapshot.dividend.last_dividend_value is None
    assert snapshot.dividend.last_dividend_date is None


def test_get_snapshot_raises_not_found_for_unknown_ticker(market_client, monkeypatch):
    monkeypatch.setattr(market_client, "_get_yf_ticker", lambda t: _FakeYFTicker(info={}))
    with pytest.raises(MarketDataNotFoundError):
        market_client.get_snapshot("NOTAREALTICKERXYZ")


def test_get_snapshot_raises_unavailable_on_exception(market_client, monkeypatch):
    def _raise(_ticker):
        raise RuntimeError("network boom")

    monkeypatch.setattr(market_client, "_get_yf_ticker", _raise)
    with pytest.raises(MarketDataUnavailableError):
        market_client.get_snapshot("AAPL")


def test_get_historical_prices_returns_points(market_client, monkeypatch):
    import pandas as pd

    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    df = pd.DataFrame(
        {
            "Open": [10.0, 11.0, 12.0],
            "High": [10.5, 11.5, 12.5],
            "Low": [9.5, 10.5, 11.5],
            "Close": [10.2, 11.2, 12.2],
            "Volume": [1000, 1100, 1200],
        },
        index=idx,
    )
    monkeypatch.setattr(market_client, "_get_yf_ticker", lambda t: _FakeYFTicker(history_df=df))

    points = market_client.get_historical_prices("AAPL", period="1y")
    assert len(points) == 3
    assert points[0].date == "2024-01-01"
    assert points[0].close == 10.2
    assert points[2].volume == 1200


def test_get_historical_prices_raises_not_found_on_empty(market_client, monkeypatch):
    import pandas as pd

    monkeypatch.setattr(market_client, "_get_yf_ticker", lambda t: _FakeYFTicker(history_df=pd.DataFrame()))
    with pytest.raises(MarketDataNotFoundError):
        market_client.get_historical_prices("NOTAREALTICKERXYZ")


def test_snapshot_caching_avoids_second_fetch(market_client, monkeypatch):
    calls = {"count": 0}

    def _get_ticker(t):
        calls["count"] += 1
        return _FakeYFTicker(info=FAKE_INFO)

    monkeypatch.setattr(market_client, "_get_yf_ticker", _get_ticker)
    market_client.get_snapshot("AAPL")
    market_client.get_snapshot("AAPL")
    assert calls["count"] == 1


@pytest.mark.integration
def test_real_yfinance_lookup_for_aapl(tmp_path):
    """Hits real Yahoo Finance via yfinance for AAPL."""
    client = MarketDataClient(cache=FileCache(cache_dir=tmp_path, ttl_seconds=15 * 60))
    snapshot = client.get_snapshot("AAPL")

    assert snapshot.ticker == "AAPL"
    assert snapshot.price is not None and snapshot.price > 0
    assert snapshot.market_cap is not None and snapshot.market_cap > 0
    assert snapshot.shares_outstanding is not None and snapshot.shares_outstanding > 0
    assert snapshot.source == "YAHOO_FINANCE_YFINANCE"
    assert snapshot.is_latest_available_not_realtime is True

    history = client.get_historical_prices("AAPL", period="1mo")
    assert len(history) > 0
    assert history[-1].close is not None
