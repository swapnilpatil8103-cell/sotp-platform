# Data Model

## Overview

The schema is implemented in `backend/models/` using SQLModel (SQLAlchemy under
the hood), targeting PostgreSQL / Supabase. Every financial number in the
system is traceable to a source filing; nothing is stored without provenance.

## Core entities

- **Company** — a covered public company (ticker, CIK, name, sector, exchange).
- **Filing** — a single SEC filing (10-K, 10-Q, etc.) belonging to a Company,
  with accession number, form type, filing date, and source URL.
- **FinancialFact** — one reported/derived/estimated financial data point
  (concept, value, unit, currency, period, fiscal_year, fiscal_period,
  filing_date, source, source_url, accession_number, xbrl_tag, data_status).
  `data_status` is one of REPORTED, DERIVED, ESTIMATED, MISSING, CONFLICTING.
- **Segment** — a reporting segment/business unit of a Company (from segment
  disclosures in filings).
- **SegmentFinancialFact** — same shape as FinancialFact but scoped to a
  Segment, for SOTP analysis.
- **ValuationRun** — a versioned snapshot of a valuation calculation (method,
  inputs, outputs, version number, created_at, created_by).
- **AssumptionDecision** — records an AI-proposed assumption alongside the
  human-approved value, the human's reasoning, the user, and a timestamp. This
  is the central artifact of the human-in-the-loop governance model.
- **AuditLogEntry** — immutable append-only log of every meaningful action
  (data fetch, valuation run, assumption decision, override) for
  accountability and reproducibility.
- **MarketDataSnapshot** — one market-data "as of" point for a Company
  (price, currency, market_cap, shares_outstanding, beta, dividend_yield,
  last_dividend_value/date, `as_of` date, `source`, `fetched_at`). Sourced
  from `yfinance`/Yahoo Finance via `backend/services/market_data_client.py`.
  See "Market data freshness semantics" below — this is explicitly
  latest-available, not real-time.

## Relationships

```
Company 1---* Filing
Company 1---* FinancialFact
Company 1---* Segment
Segment 1---* SegmentFinancialFact
Company 1---* ValuationRun
Company 1---* MarketDataSnapshot
ValuationRun 1---* AssumptionDecision
(Company|Filing|ValuationRun) 1---* AuditLogEntry
```

## Persistence & upsert keys

Ingestion is idempotent: re-fetching a company's facts never duplicates rows.
Natural-key unique constraints (see `backend/migrations/versions/`) enforce this
at the DB level, and `backend/data/persistence.py` upserts against them:

- `FinancialFact`: unique on `(company_id, concept, period, xbrl_tag)`.
- `Segment`: unique on `(company_id, name)`.
- `SegmentFinancialFact`: unique on `(segment_id, concept, period, xbrl_tag)`.
- `MarketDataSnapshot`: unique on `(company_id, as_of)` — repeated fetches on
  the same "as of" date update the existing row instead of creating a new one.

`backend/db.py` provides the engine/session factory (`DATABASE_URL`-driven);
`backend/alembic.ini` + `backend/migrations/` manage schema changes. Tests
never touch Postgres — `backend/tests/conftest.py` points `DATABASE_URL` at a
throwaway per-test SQLite file and calls `create_all()`.

## Segment extraction & coverage methodology

SEC's `companyfacts` XBRL JSON API returns only *consolidated* facts — it
drops XBRL dimensional qualifiers entirely. Segment-level disclosures (e.g.
revenue by business segment) are tagged with an XBRL dimension, almost always
`us-gaap:StatementBusinessSegmentsAxis`, and that dimension only survives in
the filing's actual XBRL instance document. Since ~2019 filers embed that
instance as **inline XBRL** directly inside the primary 10-K HTML document.

`backend/data/xbrl_instance.py` owns the parsing of that document (via
`lxml`): `xbrli:context` elements (with `xbrldi:explicitMember` dimensional
qualifiers) and `ix:nonFraction` facts (handling `scale`, `sign`, and
parenthesized-negative number formatting). The fetch side goes through
`SECConnector.get_filing_xbrl` (`backend/services/sec_client.py`), which
wraps that parsing with the connector's shared disk caching, rate limiting,
and typed-error handling — see `docs/architecture.md` for the full
`SECConnector` method list.

`backend/data/segment_extractor.py` then:
1. Finds every context dimensionally qualified on a business-segment axis and
   derives a human-readable segment name from the XBRL member local name
   (e.g. `goog:GoogleCloudMember` → "Google Cloud").
2. For each segment, matches contexts to the target fiscal year/period (by
   period end date + duration for durational concepts, or instant date for
   point-in-time concepts like `assets`).
3. Resolves revenue / operating_income / da / capex / assets against the same
   candidate-tag vocabulary as the company-level concept map, producing one
   `SegmentFinancialFact` per (segment, concept) with `data_status=REPORTED`
   when a dimensionally-qualified value exists, or `MISSING` (value=None,
   never fabricated) when it doesn't.
4. If a filing has **no** segment-dimensional facts at all (single-segment
   reporter, or segment note not XBRL-tagged), returns an empty segment list
   with an explanatory note rather than inventing a segment.

`backend/data/segment_coverage.py` scores data completeness deterministically
(no AI): for each segment, `coverage_pct` per tracked metric is
`REPORTED rows / total rows seen` for that metric across the periods
supplied; a segment's `overall_coverage_pct` is the mean across its tracked
metrics (`revenue`, `operating_income`, `da`, `capex`, `assets`); a
company's overall segment coverage score is the mean of its segments'
`overall_coverage_pct`. Verified end-to-end against Alphabet's real 10-K:
Google Services, Google Cloud, and All Other Segments revenue/operating
income extracted correctly with full REPORTED provenance; D&A/capex/assets
came back MISSING (not disclosed at the segment level in that filing),
exactly as intended.
```

## Market data freshness semantics

`backend/services/market_data_client.py` wraps `yfinance` (which itself
sources from Yahoo Finance, a delayed/best-effort feed — not an exchange
direct-feed). This is deliberately **not** presented as real-time:

- Every `MarketDataSnapshot` / API response carries `as_of` (the latest
  quote timestamp/date the source reports) separately from `fetched_at`
  (when this client retrieved it), plus an explicit
  `is_latest_available_not_realtime=True` flag in the API response.
- Caching TTLs are short relative to SEC filing data (which is cached 24h):
  price/market-cap/shares outstanding are cached 15 minutes; beta, dividend
  info, and historical prices are cached 24 hours — reflecting how much
  faster each of those fields actually changes.
- Fields `yfinance` doesn't report for a given ticker (e.g. `beta` is often
  absent for thinly-covered or newly-listed tickers) are left `None`. They
  are never estimated, interpolated, or backfilled from another source.
- `GET /companies/{ticker}/market-data` returns 404 for tickers with no
  market data and 503 for upstream/network failures, matching the SEC
  router's error-handling style — callers never see a raw exception.

## Conventions

- All tables have `id` (UUID or int PK), `created_at`, `updated_at` where
  mutable.
- Enums are implemented as Python `enum.Enum` + SQLAlchemy `Enum` columns.
- Foreign keys are always indexed.
- Monetary values are stored with explicit `currency` and `unit` columns —
  never assume USD or a fixed scale.

See `backend/models/` for the authoritative, executable definition of this
schema.
