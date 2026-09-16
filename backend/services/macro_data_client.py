"""Macro/risk-free-rate data client (London Strategic Edge "vault" API).

Used for exactly one thing: suggesting a real, sourced risk-free rate (US
10-year Treasury yield) for the WACC cost-of-equity input, which was
previously a blank manual field with no sourcing at all. Nothing else this
provider offers (market prices, "financial_reports"/"fundamentals",
options data) is wired in -- those are redundant with, and less
transparently sourced than, the SEC XBRL pipeline and yfinance client
already in this codebase.

Follows the same conventions as ``backend/services/sec_client.py`` and
``backend/services/market_data_client.py``:

- Typed exceptions; callers never see raw httpx/network exceptions.
- Disk caching via ``backend/services/cache.py`` -- treasury yields update
  at most once per trading day, so a long TTL is appropriate.
- The result is explicitly labeled SUGGESTED and "latest available", never
  presented as a real-time or authoritative rate -- a human must still
  choose to use it (see the WACC page's "Use latest 10Y treasury yield"
  affordance), exactly like the existing peer-informed-beta suggestion.

Auth: ``LSE_API_KEY`` via the ``x-api-key`` header (confirmed against the
real API; the ``Authorization: Bearer`` scheme in some client-library docs
does not work). Base URL / key are read from env (``LSE_API_BASE_URL``,
``LSE_API_KEY``) so this degrades to "unavailable" rather than crashing when
unconfigured -- the risk-free rate simply stays a manual input in that case.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from backend.services.cache import DEFAULT_CACHE_DIR, FileCache

MACRO_CACHE_TTL_SECONDS = 12 * 60 * 60  # 12 hours -- treasury yields update once/trading day
MACRO_CACHE_DIR = DEFAULT_CACHE_DIR.parent / "macro_data"

SOURCE_NAME = "LONDON_STRATEGIC_EDGE_VAULT"
DEFAULT_BASE_URL = "https://api.londonstrategicedge.com"
USER_AGENT = "sotp-intelligence (+https://github.com/) macro-data-client"


class MacroDataError(Exception):
    """Base class for all macro data client errors."""


class MacroDataUnavailableError(MacroDataError):
    """Raised when the provider is unreachable, unconfigured (no API key), or
    returns an unexpected/error response. Callers must treat this as "no
    suggestion available" and leave the risk-free rate as a manual input --
    never fabricate a rate."""


class MacroDataNotFoundError(MacroDataError):
    """Raised when the requested series/symbol has no data."""


@dataclass
class RiskFreeRateSuggestion:
    symbol: str
    rate_pct: float
    as_of_date: str
    source: str = SOURCE_NAME
    fetched_at: str = ""
    is_suggestion_not_authoritative: bool = True

    def __post_init__(self) -> None:
        if not self.fetched_at:
            self.fetched_at = datetime.now(timezone.utc).isoformat()


class MacroDataClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        cache: Optional[FileCache] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("LSE_API_KEY", "")
        self.base_url = (base_url if base_url is not None else os.environ.get("LSE_API_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.cache = cache or FileCache(MACRO_CACHE_DIR, ttl_seconds=MACRO_CACHE_TTL_SECONDS)
        self.timeout_seconds = timeout_seconds

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.api_key:
            raise MacroDataUnavailableError(
                "LSE_API_KEY is not configured. Risk-free-rate suggestions are unavailable; "
                "the risk-free rate remains a manual WACC input."
            )

        cache_key = f"{path}?{sorted(params.items())}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        url = f"{self.base_url}{path}"
        try:
            response = httpx.get(
                url,
                params=params,
                headers={"x-api-key": self.api_key, "User-Agent": USER_AGENT},
                timeout=self.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise MacroDataUnavailableError(f"Failed to reach London Strategic Edge vault API: {exc}") from exc

        if response.status_code == 401:
            raise MacroDataUnavailableError("London Strategic Edge API key rejected (401 Unauthorized).")
        if response.status_code == 404:
            raise MacroDataNotFoundError(f"No data found for: {url}")
        if response.status_code >= 400:
            raise MacroDataUnavailableError(f"London Strategic Edge vault API returned {response.status_code} for {url}")

        data = response.json()
        self.cache.set(cache_key, data)
        return data

    def get_risk_free_rate_suggestion(self, symbol: str = "US10Y") -> RiskFreeRateSuggestion:
        """Latest available Treasury yield close, as a SUGGESTED risk-free
        rate for WACC. Returns the most recent single data point -- callers
        must not treat this as authoritative or real-time (bond markets can
        move intraday); it is a defensible, sourced starting point for a
        human to review, exactly like the peer-informed-beta suggestion.
        """
        rows = self._get("/vault/ref/bond_yields", {"symbol": symbol, "order": "desc", "limit": 1})
        if not rows:
            raise MacroDataNotFoundError(f"No bond yield data returned for symbol={symbol}")

        row = rows[0]
        close = row.get("close")
        if close is None:
            raise MacroDataNotFoundError(f"Bond yield row for {symbol} has no 'close' value: {row}")

        return RiskFreeRateSuggestion(
            symbol=symbol,
            rate_pct=float(close) / 100.0,
            as_of_date=row.get("date", ""),
        )
