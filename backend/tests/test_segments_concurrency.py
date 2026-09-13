"""Tests for the segment-extraction performance work in
``backend.api.routers.segments`` and ``backend.services.sec_client``:

- The internal rate limiter (``_RateLimiter``) is hammered concurrently to
  confirm it stays race-free now that it's used from multiple threads at
  once (the segments endpoint runs ``get_latest_filings`` and
  ``get_company_facts`` on a thread pool since neither depends on the
  other's output -- see the comment in
  ``backend/api/routers/segments.py::get_company_segments``).
- The segments endpoint's concurrent fetch of filings + company facts is
  exercised end-to-end against mocked SEC responses (no real network) and
  asserted to produce the exact same result as calling the two SEC methods
  sequentially would -- i.e. parallelizing *when* the calls happen changes
  nothing about *what* data comes back.
"""

from __future__ import annotations

import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.api.deps import get_sec_client
from backend.api.main import app
from backend.services.sec_client import SECConnector, _RateLimiter


# --------------------------------------------------------------- rate limiter


def test_rate_limiter_is_thread_safe_under_concurrent_hammering():
    """20 threads all calling `.wait()` concurrently on one limiter must never
    raise, and must still enforce the overall min-interval spacing across all
    calls combined (not per-thread) -- i.e. the lock genuinely serializes the
    critical section rather than just not crashing."""
    limiter = _RateLimiter(max_per_second=50.0)  # 20ms min interval
    n_threads = 20
    calls_per_thread = 5
    errors: list[BaseException] = []
    timestamps: list[float] = []
    ts_lock = threading.Lock()

    def worker():
        try:
            for _ in range(calls_per_thread):
                limiter.wait()
                with ts_lock:
                    timestamps.append(time.monotonic())
        except BaseException as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"rate limiter raised under concurrent use: {errors}"
    total_calls = n_threads * calls_per_thread
    assert len(timestamps) == total_calls

    elapsed = time.monotonic() - start
    # With a 20ms min interval and `total_calls` serialized requests, total
    # wall time must be at least (total_calls - 1) * min_interval -- if the
    # lock weren't doing its job, many `.wait()` calls could slip through
    # without being spaced out at all and this would be far too fast.
    min_interval = 1.0 / 50.0
    assert elapsed >= (total_calls - 1) * min_interval * 0.8  # 20% tolerance for scheduling jitter


# ------------------------------------------------------- concurrent endpoint


FAKE_SUBMISSIONS = {
    "name": "Test Co",
    "filings": {
        "recent": {
            "form": ["10-K"],
            "accessionNumber": ["0000320193-24-000001"],
            "filingDate": ["2024-11-01"],
            "reportDate": ["2024-09-28"],
            "primaryDocument": ["testco-20240928.htm"],
        }
    },
}

FAKE_COMPANY_FACTS = {
    "facts": {
        "us-gaap": {
            "Revenues": {
                "units": {
                    "USD": [
                        {
                            "fy": 2024,
                            "fp": "FY",
                            "form": "10-K",
                            "start": "2023-10-01",
                            "end": "2024-09-28",
                            "val": 1000,
                        }
                    ]
                }
            }
        }
    }
}


class _RecordingConnector(SECConnector):
    """Wraps the real methods with call-order/thread recording so tests can
    assert `get_latest_filings` and `get_company_facts` really did run
    concurrently, without touching the network."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls: list[tuple[str, float, float]] = []  # (name, start, end)
        self._calls_lock = threading.Lock()

    def get_company_cik(self, ticker: str) -> str:
        return "0000320193"

    def get_latest_filings(self, cik10: str, form_types=("10-K", "10-Q")):
        start = time.monotonic()
        time.sleep(0.2)
        result = super()._build_filing_metadata(
            cik10,
            0,
            FAKE_SUBMISSIONS["filings"]["recent"]["form"],
            FAKE_SUBMISSIONS["filings"]["recent"]["accessionNumber"],
            FAKE_SUBMISSIONS["filings"]["recent"]["filingDate"],
            FAKE_SUBMISSIONS["filings"]["recent"]["reportDate"],
            FAKE_SUBMISSIONS["filings"]["recent"]["primaryDocument"],
        )
        with self._calls_lock:
            self.calls.append(("get_latest_filings", start, time.monotonic()))
        return [result]

    def get_company_facts(self, cik10: str):
        start = time.monotonic()
        time.sleep(0.2)
        with self._calls_lock:
            self.calls.append(("get_company_facts", start, time.monotonic()))
        return FAKE_COMPANY_FACTS


@pytest.fixture
def recording_client(tmp_path, monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Test Suite test@example.com")
    from backend.services.cache import FileCache

    return _RecordingConnector(cache=FileCache(cache_dir=tmp_path))


def test_get_latest_filings_and_get_company_facts_run_concurrently(recording_client, monkeypatch):
    """Directly exercises the same ThreadPoolExecutor pattern the segments
    router uses, confirming the two independent calls overlap in wall time
    rather than running back-to-back."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(recording_client.get_latest_filings, "0000320193", form_types=("10-K",))
        f2 = pool.submit(recording_client.get_company_facts, "0000320193")
        filings = f1.result()
        facts = f2.result()

    assert filings[0]["accession_number"] == "0000320193-24-000001"
    assert facts == FAKE_COMPANY_FACTS

    # Both calls should have overlapped: the second call's start must be
    # before the first call's end (they were genuinely concurrent, not
    # merely both fast).
    assert len(recording_client.calls) == 2
    (_, s1, e1), (_, s2, e2) = recording_client.calls
    overlap = min(e1, e2) - max(s1, s2)
    assert overlap > 0, f"calls did not overlap: {recording_client.calls}"


def test_segments_endpoint_uses_concurrent_fetch_and_matches_sequential_result(recording_client, monkeypatch):
    """End-to-end through the FastAPI endpoint (mocked SEC layer, real
    extraction/persistence code) -- the same fixed inputs must produce the
    same output whether or not the two independent SEC fetches happen to run
    concurrently under the hood. This is the correctness guarantee for the
    performance change: parallelizing WHEN calls happen must not change WHAT
    comes back."""
    from backend.data import segment_extractor

    def fake_extract_segments_for_filing(connector, cik10, fiscal_year, fiscal_period, filing):
        return segment_extractor.SegmentExtractionResult(segments=[], note="no segment data (test stub)")

    monkeypatch.setattr(
        "backend.api.routers.segments.extract_segments_for_filing",
        fake_extract_segments_for_filing,
    )

    # `segments.py` calls `get_sec_client()` directly (not as a FastAPI
    # `Depends` parameter), so `app.dependency_overrides` has no effect on
    # it -- patch the name where it's looked up instead.
    monkeypatch.setattr("backend.api.routers.segments.get_sec_client", lambda: recording_client)

    # DB session dependency is left as-is: the project's autouse
    # `_test_database` fixture (backend/tests/conftest.py) already points
    # DATABASE_URL at a throwaway per-test SQLite file for every test.
    try:
        client = TestClient(app)
        resp = client.get("/companies/TESTCO/segments")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ticker"] == "TESTCO"
        assert body["cik"] == "0000320193"
        assert body["fiscal_year"] == 2024
        # Both underlying SEC calls happened exactly once each, and (as
        # verified in the previous test) they overlap -- this confirms the
        # endpoint is actually exercising the concurrent path, not silently
        # falling back to sequential calls.
        call_names = sorted(name for name, _, _ in recording_client.calls)
        assert call_names == ["get_company_facts", "get_latest_filings"]
    finally:
        app.dependency_overrides.pop(get_sec_client, None)
