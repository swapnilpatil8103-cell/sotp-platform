"""SEC EDGAR client.

Provides ticker -> CIK lookup, company submissions (filing history), and
XBRL company facts, with disk caching and basic rate limiting so we stay
comfortably under SEC's fair-access guidance (max ~10 req/sec, always send a
descriptive User-Agent).

SEC docs: https://www.sec.gov/os/webmaster-faq#developers
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Optional

import httpx

from backend.services.cache import FileCache

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TMPL = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANY_FACTS_URL_TMPL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
FRAMES_URL_TMPL = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json"

# Frames responses are large, cover a whole historical quarter/year of
# already-filed data, and never change once published -- cache them much
# longer than the 24h default used for per-company lookups.
FRAMES_CACHE_TTL_SECONDS = 24 * 60 * 60

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class SECError(Exception):
    """Base class for all SEC client errors."""


class SECNotFoundError(SECError):
    """Raised when a ticker/CIK cannot be resolved or a resource returns 404."""


class SECRateLimitError(SECError):
    """Raised when SEC responds with 429 (Too Many Requests)."""


class SECUnavailableError(SECError):
    """Raised for network failures, timeouts, or 5xx responses from SEC."""


class _RateLimiter:
    """Simple thread-safe token-bucket-ish throttle: at most N requests/sec."""

    def __init__(self, max_per_second: float = 8.0):
        self._min_interval = 1.0 / max_per_second
        self._lock = threading.Lock()
        self._last_request_ts = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_ts
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_request_ts = time.monotonic()


def _get_user_agent() -> str:
    ua = os.environ.get("SEC_USER_AGENT")
    if not ua or "@" not in ua:
        raise SECError(
            "SEC_USER_AGENT env var must be set to 'Name email@domain.com' per SEC's "
            "fair-access policy (see .env.example)."
        )
    return ua


class SECClient:
    """Client for SEC EDGAR's public JSON endpoints (ticker lookup, submissions, XBRL facts)."""

    def __init__(
        self,
        user_agent: Optional[str] = None,
        cache: Optional[FileCache] = None,
        max_requests_per_second: float = 8.0,
        max_retries: int = 3,
    ):
        self._user_agent = user_agent or _get_user_agent()
        self._cache = cache or FileCache()
        self._rate_limiter = _RateLimiter(max_requests_per_second)
        self._max_retries = max_retries
        self._ticker_map: Optional[dict[str, str]] = None  # ticker (upper) -> cik10

    @property
    def user_agent(self) -> str:
        return self._user_agent

    # ---------------------------------------------------------------- HTTP

    def _fetch_json(self, url: str, cache_ttl_seconds: int = 24 * 60 * 60, use_cache: bool = True) -> Any:
        """GET a JSON resource with caching, rate limiting, and typed error handling."""
        if use_cache:
            cached = self._cache.get(url)
            if cached is not None:
                return cached

        headers = {"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"}

        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries):
            self._rate_limiter.wait()
            try:
                resp = httpx.get(url, headers=headers, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                time.sleep(0.5 * (attempt + 1))
                continue

            if resp.status_code == 404:
                raise SECNotFoundError(f"SEC resource not found: {url}")
            if resp.status_code == 429:
                if attempt < self._max_retries - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise SECRateLimitError(f"SEC rate limit exceeded for: {url}")
            if resp.status_code >= 500:
                last_exc = SECUnavailableError(f"SEC returned {resp.status_code} for: {url}")
                time.sleep(0.5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise SECUnavailableError(f"SEC returned {resp.status_code} for: {url}")

            try:
                data = resp.json()
            except ValueError as exc:
                raise SECUnavailableError(f"SEC returned non-JSON payload for: {url}") from exc

            if use_cache:
                self._cache.set(url, data)
            return data

        if isinstance(last_exc, SECError):
            raise last_exc
        raise SECUnavailableError(f"Failed to reach SEC after {self._max_retries} attempts: {url}") from last_exc

    # ------------------------------------------------------------ Lookups

    def _load_ticker_map(self) -> dict[str, str]:
        if self._ticker_map is not None:
            return self._ticker_map
        data = self._fetch_json(TICKERS_URL, cache_ttl_seconds=24 * 60 * 60)
        mapping: dict[str, str] = {}
        # company_tickers.json is a dict of {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        for entry in data.values():
            ticker = str(entry.get("ticker", "")).upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            if ticker:
                mapping[ticker] = cik
        self._ticker_map = mapping
        return mapping

    def get_cik(self, ticker: str) -> str:
        """Resolve a ticker (case-insensitive) to a zero-padded 10-digit CIK."""
        mapping = self._load_ticker_map()
        cik = mapping.get(ticker.upper())
        if cik is None:
            raise SECNotFoundError(f"Unknown ticker: {ticker}")
        return cik

    def get_submissions(self, cik10: str) -> dict[str, Any]:
        """Fetch the submissions (company info + filing history) for a zero-padded CIK."""
        url = SUBMISSIONS_URL_TMPL.format(cik10=cik10)
        return self._fetch_json(url, cache_ttl_seconds=24 * 60 * 60)

    def get_company_facts(self, cik10: str) -> dict[str, Any]:
        """Fetch XBRL company facts (all reported concepts, all periods) for a zero-padded CIK."""
        url = COMPANY_FACTS_URL_TMPL.format(cik10=cik10)
        return self._fetch_json(url, cache_ttl_seconds=24 * 60 * 60)

    @staticmethod
    def frame_period(fiscal_year: int, quarter: Optional[int], instant: bool) -> str:
        """Build the SEC frames period token, e.g. 'CY2023Q4I', 'CY2023Q4', 'CY2023'.

        Instant concepts (balance-sheet items like Assets, CashAndCashEquivalents)
        are point-in-time as of a quarter-end date and require the 'I' suffix.
        Duration concepts (income-statement/cash-flow items like Revenues) cover a
        period: a specific quarter (no suffix) or, if ``quarter`` is None, the full
        fiscal year.
        """
        if quarter is None:
            if instant:
                raise ValueError("Full-year instant frames aren't a thing -- pass a quarter for instant concepts")
            return f"CY{fiscal_year}"
        if quarter not in (1, 2, 3, 4):
            raise ValueError(f"quarter must be 1-4 or None, got {quarter}")
        suffix = "I" if instant else ""
        return f"CY{fiscal_year}Q{quarter}{suffix}"

    def get_frame(
        self,
        tag: str,
        fiscal_year: int,
        quarter: Optional[int] = None,
        *,
        instant: bool = False,
        unit: str = "USD",
        taxonomy: str = "us-gaap",
    ) -> dict[str, Any]:
        """Fetch an XBRL "frame": one concept's reported value across ALL filers
        for a given period.

        https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json

        ``instant=True`` selects a point-in-time period (period gets an 'I'
        suffix, e.g. CY2023Q4I) for balance-sheet-style concepts (Assets, Cash,
        ...). ``instant=False`` (default) selects a duration period: a single
        quarter (CY2023Q4) if ``quarter`` is given, or the full fiscal year
        (CY2023) if ``quarter`` is None -- appropriate for income-statement /
        cash-flow concepts (Revenues, NetIncomeLoss, ...).

        Returns the raw JSON payload: {"data": [{"cik": ..., "entityName": ...,
        "val": ..., ...}, ...], plus metadata fields}. Same caching, rate
        limiting, and typed-error handling (SECNotFoundError/SECRateLimitError/
        SECUnavailableError) as every other SEC client method.
        """
        period = self.frame_period(fiscal_year, quarter, instant)
        url = FRAMES_URL_TMPL.format(taxonomy=taxonomy, tag=tag, unit=unit, period=period)
        return self._fetch_json(url, cache_ttl_seconds=FRAMES_CACHE_TTL_SECONDS)

    # ------------------------------------------------------------ Helpers

    def get_latest_filings(self, cik10: str, form_types: tuple[str, ...] = ("10-K", "10-Q")) -> list[dict[str, Any]]:
        """Return the most recent filing metadata dict per requested form type.

        Each result dict has: form, accessionNumber, filingDate, reportDate,
        primaryDocument, and a derived source_url.
        """
        submissions = self.get_submissions(cik10)
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accession_numbers = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        primary_docs = recent.get("primaryDocument", [])

        latest_by_form: dict[str, dict[str, Any]] = {}
        for i, form in enumerate(forms):
            if form not in form_types or form in latest_by_form:
                continue
            accession_raw = accession_numbers[i]
            accession_nodash = accession_raw.replace("-", "")
            primary_doc = primary_docs[i] if i < len(primary_docs) else ""
            source_url = (
                f"https://www.sec.gov/Archives/edgar/data/{int(cik10)}/{accession_nodash}/{primary_doc}"
            )
            latest_by_form[form] = {
                "form": form,
                "accession_number": accession_raw,
                "filing_date": filing_dates[i] if i < len(filing_dates) else None,
                "period_of_report": report_dates[i] if i < len(report_dates) else None,
                "source_url": source_url,
            }
            if len(latest_by_form) == len(form_types):
                break
        return list(latest_by_form.values())
