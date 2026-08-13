"""Shared FastAPI dependencies (e.g. a process-wide SECClient instance)."""

from __future__ import annotations

from functools import lru_cache

from backend.services.market_data_client import MarketDataClient
from backend.services.sec_client import SECClient


@lru_cache(maxsize=1)
def get_sec_client() -> SECClient:
    """Return a singleton SECClient, constructed lazily so importing this module
    doesn't require SEC_USER_AGENT to be set (only calling this does)."""
    return SECClient()


@lru_cache(maxsize=1)
def get_market_data_client() -> MarketDataClient:
    """Return a singleton MarketDataClient."""
    return MarketDataClient()
