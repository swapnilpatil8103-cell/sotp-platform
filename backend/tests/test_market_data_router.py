"""Unit tests for GET /companies/{ticker}/market-data (mocked MarketDataClient/SECClient)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.api import deps
from backend.api.main import app
from backend.services.market_data_client import (
    DividendInfo,
    MarketDataNotFoundError,
    MarketDataSnapshotData,
    MarketDataUnavailableError,
)

client = TestClient(app)


class _FakeMarketDataClient:
    def __init__(self, snapshot=None, exc=None):
        self._snapshot = snapshot
        self._exc = exc

    def get_snapshot(self, ticker):
        if self._exc:
            raise self._exc
        return self._snapshot


class _FakeSECClient:
    def get_cik(self, ticker):
        return "0000320193"


def _fake_snapshot():
    return MarketDataSnapshotData(
        ticker="AAPL",
        price=227.5,
        currency="USD",
        market_cap=3_450_000_000_000.0,
        shares_outstanding=15_150_000_000.0,
        beta=1.2,
        dividend=DividendInfo(dividend_yield=0.0051, last_dividend_value=0.25, last_dividend_date="2024-11-14"),
        as_of="2026-08-11T20:00:00+00:00",
    )


def test_market_data_endpoint_returns_snapshot(monkeypatch):
    app.dependency_overrides[deps.get_market_data_client] = lambda: _FakeMarketDataClient(snapshot=_fake_snapshot())
    app.dependency_overrides[deps.get_sec_client] = lambda: _FakeSECClient()
    try:
        resp = client.get("/companies/AAPL/market-data")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ticker"] == "AAPL"
        assert body["price"] == 227.5
        assert body["market_cap"] == 3_450_000_000_000.0
        assert body["dividend"]["dividend_yield"] == 0.0051
        assert body["is_latest_available_not_realtime"] is True
        assert body["source"] == "YAHOO_FINANCE_YFINANCE"
    finally:
        app.dependency_overrides.clear()


def test_market_data_endpoint_404_on_unknown_ticker(monkeypatch):
    app.dependency_overrides[deps.get_market_data_client] = lambda: _FakeMarketDataClient(
        exc=MarketDataNotFoundError("not found")
    )
    try:
        resp = client.get("/companies/NOTAREALTICKERXYZ/market-data")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_market_data_endpoint_503_on_unavailable(monkeypatch):
    app.dependency_overrides[deps.get_market_data_client] = lambda: _FakeMarketDataClient(
        exc=MarketDataUnavailableError("boom")
    )
    try:
        resp = client.get("/companies/AAPL/market-data")
        assert resp.status_code == 503
    finally:
        app.dependency_overrides.clear()
