"""SEC EDGAR connector.

``SECConnector`` is the single access point for every kind of data this
project pulls from SEC EDGAR: ticker/CIK resolution, submissions (filing
history), XBRL company facts, a single XBRL concept across all filers
("frames"), a single company's history for one XBRL concept
("companyconcept"), per-filing metadata, raw filing documents fetched from
EDGAR Archives, and a filing's inline-XBRL dimensional/segment data. All of
it shares the same disk caching (`backend.services.cache.FileCache`), rate
limiting (~8 req/s by default, comfortably under SEC's fair-access guidance),
retry/backoff on 429/5xx, and typed error hierarchy (`SECNotFoundError`,
`SECRateLimitError`, `SECUnavailableError`), plus the mandatory descriptive
User-Agent header.

SEC docs: https://www.sec.gov/os/webmaster-faq#developers

## Design note: ``resolve_ticker`` vs ``get_company_cik``

These are deliberately two different methods, not two names for the same
behavior:

- ``resolve_ticker(ticker)`` is the raw ticker-map lookup: it returns the CIK
  exactly as it appears in SEC's ``company_tickers.json`` (an int-valued
  string, not zero-padded, e.g. ``"320193"``).
- ``get_company_cik(ticker)`` is the normalized form used by every other
  method on this class (``get_submissions``, ``get_company_facts``,
  ``get_company_concept``, ...), all of which expect a zero-padded 10-digit
  CIK string (e.g. ``"0000320193"``). It calls ``resolve_ticker`` and
  zero-pads the result.

Keeping them distinct means the "raw lookup" and "the normalization every
other method needs" are independently testable and independently reusable
(e.g. a caller that wants to display the CIK exactly as SEC reports it, vs.
one that's about to build a `CIK##########` URL), rather than baking the
zero-padding assumption into the only lookup method.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from typing import Any, Optional

import httpx

from backend.data.xbrl_instance import XbrlContext, XbrlFact, parse_inline_xbrl
from backend.services.cache import FileCache

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL_TMPL = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANY_FACTS_URL_TMPL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
COMPANY_CONCEPT_URL_TMPL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik10}/{taxonomy}/{tag}.json"
FRAMES_URL_TMPL = "https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json"
ARCHIVES_URL_TMPL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/{filename}"

# Frames responses are large, cover a whole historical quarter/year of
# already-filed data, and never change once published -- cache them much
# longer than the 24h default used for per-company lookups.
FRAMES_CACHE_TTL_SECONDS = 24 * 60 * 60

# Company-concept history for a single tag; same "keep reasonably fresh"
# tradeoff as company facts (new periods get added as filings land).
COMPANY_CONCEPT_CACHE_TTL_SECONDS = 24 * 60 * 60

# Filing documents (primary 10-K/10-Q htm/txt, inline-XBRL instance) never
# change once filed -- still cached at the same 24h TTL as everything else
# for consistency; there's no correctness reason to cache them longer, only
# a traffic-reduction one, and 24h already achieves that.
FILING_DOCUMENT_CACHE_TTL_SECONDS = 24 * 60 * 60

DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
DEFAULT_DOCUMENT_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class SECError(Exception):
    """Base class for all SEC connector errors."""


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


class SECConnector:
    """Connector for every SEC EDGAR surface this project touches: ticker/CIK
    resolution, submissions, XBRL company facts/concepts/frames, per-filing
    metadata, raw filing documents, and inline-XBRL segment data."""

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
        self._ticker_map: Optional[dict[str, str]] = None  # ticker (upper) -> raw cik string

    @property
    def user_agent(self) -> str:
        return self._user_agent

    # ---------------------------------------------------------------- HTTP

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": self._user_agent, "Accept-Encoding": "gzip, deflate"}

    def _request(self, url: str, *, timeout: httpx.Timeout = DEFAULT_TIMEOUT) -> httpx.Response:
        """GET ``url`` with rate limiting, retry/backoff, and typed error handling.

        Shared by every fetch method below (JSON and raw-document alike) so
        there is exactly one implementation of the retry/backoff/typed-error
        policy instead of one per content type.
        """
        headers = self._headers()
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries):
            self._rate_limiter.wait()
            try:
                resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
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

            return resp

        if isinstance(last_exc, SECError):
            raise last_exc
        raise SECUnavailableError(f"Failed to reach SEC after {self._max_retries} attempts: {url}") from last_exc

    def _fetch_json(self, url: str, cache_ttl_seconds: int = 24 * 60 * 60, use_cache: bool = True) -> Any:
        """GET a JSON resource with caching, rate limiting, and typed error handling."""
        if use_cache:
            cached = self._cache.get(url)
            if cached is not None:
                return cached

        resp = self._request(url)
        try:
            data = resp.json()
        except ValueError as exc:
            raise SECUnavailableError(f"SEC returned non-JSON payload for: {url}") from exc

        if use_cache:
            self._cache.set(url, data)
        return data

    def _fetch_document(
        self,
        url: str,
        cache_ttl_seconds: int = FILING_DOCUMENT_CACHE_TTL_SECONDS,
        use_cache: bool = True,
    ) -> bytes:
        """GET a raw (non-JSON) document -- e.g. a primary filing htm/txt --
        with the same caching, rate limiting, and typed-error handling as
        `_fetch_json`. Cached as base64 text since the on-disk cache is JSON."""
        cache_key = f"doc:{url}"
        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return base64.b64decode(cached)

        resp = self._request(url, timeout=DEFAULT_DOCUMENT_TIMEOUT)
        data = resp.content

        if use_cache:
            self._cache.set(cache_key, base64.b64encode(data).decode("ascii"))
        return data

    # ------------------------------------------------------------ Lookups

    def _load_ticker_map(self) -> dict[str, str]:
        if self._ticker_map is not None:
            return self._ticker_map
        data = self._fetch_json(TICKERS_URL, cache_ttl_seconds=24 * 60 * 60)
        mapping: dict[str, str] = {}
        # company_tickers.json is a dict of {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        for entry in data.values():
            ticker = str(entry.get("ticker", "")).upper()
            cik = str(entry.get("cik_str", ""))
            if ticker and cik:
                mapping[ticker] = cik
        self._ticker_map = mapping
        return mapping

    def resolve_ticker(self, ticker: str) -> str:
        """Resolve a ticker (case-insensitive) to the raw CIK as it appears in
        SEC's ticker map (not zero-padded, e.g. "320193")."""
        mapping = self._load_ticker_map()
        cik = mapping.get(ticker.upper())
        if cik is None:
            raise SECNotFoundError(f"Unknown ticker: {ticker}")
        return cik

    def get_company_cik(self, ticker: str) -> str:
        """Resolve a ticker (case-insensitive) to a zero-padded 10-digit CIK --
        the normalized form every other method on this class expects."""
        return self.resolve_ticker(ticker).zfill(10)

    def get_submissions(self, cik10: str) -> dict[str, Any]:
        """Fetch the submissions (company info + filing history) for a zero-padded CIK."""
        url = SUBMISSIONS_URL_TMPL.format(cik10=cik10)
        return self._fetch_json(url, cache_ttl_seconds=24 * 60 * 60)

    def get_company_facts(self, cik10: str) -> dict[str, Any]:
        """Fetch XBRL company facts (all reported concepts, all periods) for a zero-padded CIK."""
        url = COMPANY_FACTS_URL_TMPL.format(cik10=cik10)
        return self._fetch_json(url, cache_ttl_seconds=24 * 60 * 60)

    def get_company_concept(self, cik10: str, tag: str, taxonomy: str = "us-gaap") -> dict[str, Any]:
        """Fetch one XBRL concept's full reported history for a single company.

        https://data.sec.gov/api/xbrl/companyconcept/CIK##########/{taxonomy}/{tag}.json

        Unlike `get_company_facts` (every concept, one company) this is one
        concept, one company -- useful when a caller only needs a single
        tag's time series without pulling the (often large) full companyfacts
        payload. Same caching, rate limiting, and typed-error handling as
        every other method here.
        """
        url = COMPANY_CONCEPT_URL_TMPL.format(cik10=cik10, taxonomy=taxonomy, tag=tag)
        return self._fetch_json(url, cache_ttl_seconds=COMPANY_CONCEPT_CACHE_TTL_SECONDS)

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
        SECUnavailableError) as every other SEC connector method.
        """
        period = self.frame_period(fiscal_year, quarter, instant)
        url = FRAMES_URL_TMPL.format(taxonomy=taxonomy, tag=tag, unit=unit, period=period)
        return self._fetch_json(url, cache_ttl_seconds=FRAMES_CACHE_TTL_SECONDS)

    # ------------------------------------------------------------ Filings

    @staticmethod
    def _build_filing_metadata(
        cik10: str,
        index: int,
        forms: list[str],
        accession_numbers: list[str],
        filing_dates: list[str],
        report_dates: list[str],
        primary_docs: list[str],
    ) -> dict[str, Any]:
        accession_raw = accession_numbers[index]
        accession_nodash = accession_raw.replace("-", "")
        primary_doc = primary_docs[index] if index < len(primary_docs) else ""
        source_url = ARCHIVES_URL_TMPL.format(cik_int=int(cik10), accession_nodash=accession_nodash, filename=primary_doc)
        return {
            "form": forms[index],
            "accession_number": accession_raw,
            "filing_date": filing_dates[index] if index < len(filing_dates) else None,
            "period_of_report": report_dates[index] if index < len(report_dates) else None,
            "primary_document": primary_doc,
            "source_url": source_url,
        }

    def get_filing_metadata(self, cik10: str, accession_number: str) -> dict[str, Any]:
        """Look up a single filing's metadata (form, filing date, accession
        number, primary document, source URL) by accession number.

        Refactored out of `get_latest_filings` so a specific, already-known
        filing's metadata is independently retrievable -- not just "the
        latest N filings of these form types".
        """
        submissions = self.get_submissions(cik10)
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accession_numbers = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        primary_docs = recent.get("primaryDocument", [])

        target = accession_number.strip().replace("-", "")
        for i, accn in enumerate(accession_numbers):
            if accn.replace("-", "") == target:
                return self._build_filing_metadata(cik10, i, forms, accession_numbers, filing_dates, report_dates, primary_docs)

        raise SECNotFoundError(f"No filing with accession number {accession_number} found for CIK {cik10}")

    def get_latest_filings(self, cik10: str, form_types: tuple[str, ...] = ("10-K", "10-Q")) -> list[dict[str, Any]]:
        """Return the most recent filing metadata dict per requested form type.

        Each result dict has: form, accession_number, filing_date,
        period_of_report, primary_document, and a derived source_url --
        built via the same `_build_filing_metadata` helper `get_filing_metadata`
        uses, so both stay consistent.
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
            latest_by_form[form] = self._build_filing_metadata(
                cik10, i, forms, accession_numbers, filing_dates, report_dates, primary_docs
            )
            if len(latest_by_form) == len(form_types):
                break
        return list(latest_by_form.values())

    def get_filing_document(self, cik10: str, accession_number: str, filename: str) -> bytes:
        """Fetch a raw filing document (e.g. a primary 10-K/10-Q htm or txt)
        from EDGAR Archives, given its CIK, accession number, and filename.

        https://www.sec.gov/Archives/edgar/data/{cik}/{accession-no-dashes}/{filename}

        Same caching, rate limiting, and typed-error handling as every other
        connector method. Returns raw bytes -- callers decode/parse as
        appropriate for the document type (see `get_filing_xbrl` for the
        inline-XBRL case).
        """
        accession_nodash = accession_number.replace("-", "")
        url = ARCHIVES_URL_TMPL.format(cik_int=int(cik10), accession_nodash=accession_nodash, filename=filename)
        return self._fetch_document(url, cache_ttl_seconds=FILING_DOCUMENT_CACHE_TTL_SECONDS)

    def get_filing_xbrl(self, source_url: str) -> tuple[dict[str, XbrlContext], list[XbrlFact]]:
        """Fetch + parse a filing's inline-XBRL primary document, returning its
        parsed contexts (with dimensional qualifiers) and numeric facts.

        This absorbs what used to be `backend.data.xbrl_instance.fetch_inline_xbrl_document`
        + `parse_inline_xbrl` called back-to-back: the fetch now goes through
        this connector's shared caching/rate-limiting/typed-error machinery
        instead of a bare `httpx.get`, while the actual parsing logic
        (contexts, dimensions, `ix:nonFraction` facts) still lives in
        `backend.data.xbrl_instance` -- this method is the single place that
        wires fetch + parse together, so `backend.data.segment_extractor`
        (the highest-risk downstream consumer) gets identical parsed output
        for identical filing content, plus caching it didn't have before.

        ``source_url`` is the filing's primary document URL, as returned by
        `get_latest_filings`/`get_filing_metadata` (`source_url` field).
        """
        document = self._fetch_document(source_url, cache_ttl_seconds=FILING_DOCUMENT_CACHE_TTL_SECONDS)
        return parse_inline_xbrl(document)
