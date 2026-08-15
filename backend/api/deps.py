"""Shared FastAPI dependencies (e.g. a process-wide SECConnector instance)."""

from __future__ import annotations

from functools import lru_cache

from backend.services.market_data_client import MarketDataClient
from backend.services.sec_client import SECConnector


@lru_cache(maxsize=1)
def get_sec_client() -> SECConnector:
    """Return a singleton SECConnector, constructed lazily so importing this module
    doesn't require SEC_USER_AGENT to be set (only calling this does)."""
    return SECConnector()


@lru_cache(maxsize=1)
def get_market_data_client() -> MarketDataClient:
    """Return a singleton MarketDataClient."""
    return MarketDataClient()
