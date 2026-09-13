# Architecture

## Overview

SOTP Intelligence is a monorepo: a Next.js 14 frontend (`apps/web`), a
FastAPI backend (`backend/`), and a PostgreSQL/Supabase database for real
persistence (tests use a throwaway per-test SQLite file instead — see
`backend/tests/conftest.py`). The backend is layered so that data sourcing,
deterministic calculation, AI reasoning, and human governance stay strictly
separated; this separation is the architectural expression of the project's
core governance principle (AI recommends, humans decide, math is never
touched by AI) — see `docs/ai-governance.md`.

This document describes the system **as actually built** across Phases 1–9,
not the original plan. Deviations from the original master spec are called
out explicitly below rather than glossed over.

## Backend layers

- **`api/`** — HTTP boundary. FastAPI routers (`api/routers/*.py`), request
  validation via Pydantic, dependency-injected clients (`api/deps.py`).
  Contains no business logic itself; delegates to `services`/`data`/
  `valuation`/`governance`. No auth layer exists (never in scope for
  Phases 1–9 — see the Definition-of-Done audit). One narrow concurrency
  exception: `api/routers/segments.py`'s `get_company_segments` runs
  `SECConnector.get_latest_filings` (submissions) and `get_company_facts`
  on a `concurrent.futures.ThreadPoolExecutor` since neither depends on the
  other's output, both depending only on the resolved CIK — this shortens
  the real dependency chain for a first-time (uncached) segment extraction
  by overlapping those two SEC EDGAR round trips instead of running them
  back-to-back. The subsequent inline-XBRL filing fetch+parse
  (`extract_segments_for_filing`) genuinely depends on the filings result
  (it needs the chosen filing's accession number/source URL) and stays
  sequential after the pool. `SECConnector`'s rate limiter and file cache
  are already safe under this concurrency (the limiter takes an internal
  lock; the cache writes one file per URL digest), so the ~8 req/s SEC
  fair-use ceiling is still honored.
- **`services/`** — external integration clients: `sec_client.py` (defines
  `SECConnector`, the single access point for every SEC EDGAR surface this
  project uses — ticker/CIK resolution, submissions, XBRL company facts,
  company-concept history, cross-filer frames, per-filing metadata, raw
  filing documents, and a filing's inline-XBRL data — with disk caching via
  `FileCache`, an internal rate limiter capped at ~8 req/s, and typed
  retry/backoff on 429/5xx), `market_data_client.py` (yfinance-backed,
  explicitly framed as "latest available, not real-time"). See "SEC access
  layer: SECConnector" below for the full method list.
- **`data/`** — normalization layer: `concept_mapping.py` (canonical
  financial concepts → XBRL US-GAAP tag candidates), `normalizer.py`
  (raw SEC companyfacts JSON → typed `FinancialFact` rows, one per
  canonical concept per period, `MISSING` rows kept rather than dropped),
  `segment_extractor.py` (see deviation below), `segment_coverage.py`
  (per-segment/per-metric coverage scoring), `business_classifier.py`
  (SIC code → business category, e.g. `Bank`, used to steer which
  valuation methodologies are eligible), `persistence.py` (upsert helpers
  shared by routers).
- **`models/`** — SQLModel ORM: `Company`, `Filing`, `FinancialFact`,
  `Segment`, `SegmentFinancialFact`, `MarketDataSnapshot`,
  `AssumptionDecision`, `ValuationRun`, `AuditLogEntry`, plus shared enums
  (`enums.py`: `DataStatus`, `ValuationMethod`, decision status). See
  `docs/data-model.md` for the full field-level reference.
- **`valuation/`** — deterministic financial engine: `dcf.py`, `comps.py`,
  `sotp.py`, `wacc.py`, `sensitivity.py`, `scenarios.py`,
  `reverse_valuation.py`, `value_unlock.py`, `risk_dashboard.py`. Pure
  functions over typed Pydantic inputs (`schemas/valuation.py`); no I/O, no
  AI calls, fully unit-testable in isolation. This is the single place
  dollar figures are computed.
- **`ai/`** — AI reasoning layer: `gemini_adapter.py` (Gemini API client) and
  `ollama_adapter.py` (local Ollama REST client, no API key needed) both
  implement the `AIAdapter` protocol (swappable for `FakeAIAdapter` in
  tests); `factory.py`'s `get_ai_adapter()` picks between them at call time
  via the `AI_PROVIDER` env var (`gemini` default, or `ollama`) so callers
  (`api/routers/scenarios.py`, `api/routers/memo.py`) never hardcode a
  provider. `tasks/` (one narrowly-scoped function per AI use case: assumption
  recommendation, methodology recommendation, peer recommendation,
  devil's-advocate critique, research summary, memo section generation,
  value-unlock idea proposal, risk explanation, reverse-valuation
  narrative). Every task validates AI output before returning it
  (`validation.py`) and can abstain rather than emit an untrustworthy
  answer. AI never writes to an authoritative field directly — see
  `test_ai_import_boundary.py`, which asserts `backend/ai/` has no import
  path into `backend/valuation/` internals that would let it compute or
  overwrite a number.
- **`governance/`** — `approval.py` (propose/decide workflow producing
  `AssumptionDecision` rows: `PENDING` → `APPROVED`/`EDITED`/`REJECTED`/
  `OVERRIDDEN`), `valuation_run.py` (`execute_valuation_run`, gated on every
  referenced decision being approved; `list_versions`/`get_version`/
  `diff_runs` for version history and diffing).
- **`audit/`** — `logger.py`: immutable `AuditLogEntry` rows recording every
  governance decision and valuation run, queryable per-company or per-run
  via `/valuation/*/audit-trail`.

## SEC access layer: `SECConnector`

`backend/services/sec_client.py` defines `SECConnector`, the single class
every SEC-EDGAR-touching call site in the codebase goes through (companies/
segments/filings/market-data routers, `peer_discovery.py`,
`segment_extractor.py`). It consolidates what used to be split between this
module and a separate inline-XBRL fetch helper (`backend/data/xbrl_instance.py`)
into one consistent surface, all sharing the same `FileCache`-backed caching,
~8 req/s rate limiting, retry/backoff on 429/5xx, and typed error hierarchy
(`SECNotFoundError` / `SECRateLimitError` / `SECUnavailableError`):

- `resolve_ticker(ticker)` — ticker → raw CIK exactly as it appears in SEC's
  ticker map (not zero-padded).
- `get_company_cik(ticker)` — ticker → zero-padded 10-digit CIK; the
  normalized form every other method expects. Built on `resolve_ticker`
  rather than duplicating the lookup, so the "raw" and "normalized" forms
  stay independently testable (see the module docstring for the full
  rationale).
- `get_submissions(cik10)` — company info + filing history.
- `get_company_facts(cik10)` — XBRL company facts (every concept, every
  period) for a company.
- `get_company_concept(cik10, tag, taxonomy)` — one XBRL concept's full
  reported history for a single company (narrower than `get_company_facts`
  when only one tag's time series is needed).
- `get_frame(tag, fiscal_year, quarter, instant, unit, taxonomy)` — one
  concept's reported value across every filer for a period (drives peer
  discovery).
- `get_filing_metadata(cik10, accession_number)` — a single filing's
  metadata (form, filing date, accession, primary document, source URL) by
  accession number, independent of "latest N filings".
- `get_latest_filings(cik10, form_types)` — the most recent filing per
  requested form type; built on the same metadata-assembly helper as
  `get_filing_metadata` so both stay consistent.
- `get_filing_document(cik10, accession_number, filename)` — fetch a raw
  filing document (e.g. the primary 10-K/10-Q htm) from EDGAR Archives.
- `get_filing_xbrl(source_url)` — fetch + parse a filing's inline-XBRL
  primary document into contexts (with dimensional qualifiers) and numeric
  facts. This absorbs what used to be a standalone, uncached
  `fetch_inline_xbrl_document` + `parse_inline_xbrl` call pair in
  `backend/data/xbrl_instance.py`; the parsing logic still lives in that
  module (it's pure and dependency-light), but the fetch now goes through
  this connector's shared caching/rate-limiting/typed-error handling instead
  of a bare `httpx.get`. `segment_extractor.py` is the primary consumer.
- `get_insider_filings(cik10, form_types=("3","4","5"))` — every Form 3/4/5
  filing an issuer has on file (accession numbers, dates), from the same
  `submissions.filings.recent` window every other submissions-derived method
  uses.
- `list_filing_directory(cik10, accession_number)` — lists every filename in
  a filing's EDGAR Archives directory via `index.json`; used to discover
  document filenames that aren't derivable from `primaryDocument` alone
  (the 13F information-table file).
- `get_ownership_document(cik10, accession_number)` — fetch + parse a single
  Form 3/4/5 ownership XML document (reporting owner identity/relationship,
  every non-derivative/derivative transaction row) via
  `backend/data/ownership_xml.py`.
- `get_13f_holdings(cik10, accession_number)` — fetch + parse a 13F-HR
  filing's cover page (`primary_doc.xml`) and information table into a
  filer's structured holdings, also via `backend/data/ownership_xml.py`.
  `cik10` here is the **institutional filer's** CIK, not an issuer's — see
  "Insider transactions & institutional holdings" below.

## Known SEC XBRL data quirk: `fy` metadata is not the fiscal year

SEC's XBRL "companyfacts" API attaches an `fy` (and `fp`) field to every fact
entry, and it is tempting to treat `fy` as "the fiscal year this period
covers." **It is not.** `fy` reflects which annual filing's XBRL submission a
datapoint was tagged under -- and a single reporting period routinely gets
re-tagged with several different `fy` values because it shows up again as a
prior-year comparative column in later 10-Ks. Confirmed on real GOOGL data:
the period 2020-01-01/2020-12-31 appears with `fy=2022` (it's a comparative
column in the FY2022 10-K). Matching on raw `fy` mislabels actual-FY2020
revenue as "FY2022," silently shifting an entire historical series.

The fix, in `backend/data/normalizer.py::_pick_fact_for_period` (and the
year-scanning helpers `latest_available_fiscal_year` /
`historical_analysis.available_fiscal_years`): derive the true fiscal year
from the period's own `end` date (the calendar year `end` falls in), not from
`fy`. This matches how companies with non-calendar year-ends label their own
fiscal years (e.g. Apple's FY2024 ended September 2024). `fp` is still used
to select FY vs Q1..Q4, with a duration sanity check (~365 vs ~90 days) so a
stray entry can't be conflated with the wrong period type.

One further wrinkle this uncovered: `dei:EntityCommonStockSharesOutstanding`
("shares outstanding," a cover-page fact) is an *instant* concept whose `end`
is the filing's cover-page date, not a fiscal period end -- e.g. an FY2025
10-K filed in Feb 2026 reports shares outstanding "as of 2026-01-31" with
`fp="FY"`. Applying the `end`-year rule to it would misidentify the company's
latest fiscal year as one year later than reality (confirmed on real JPM
data). `normalizer.YEAR_SCAN_EXCLUDED_CONCEPTS` excludes it from the
fiscal-year-scanning helpers for this reason.

### Sibling quirk: a company can switch XBRL tags mid-history

`backend/data/concept_mapping.py::resolve_concept` maps each canonical
concept (e.g. `revenue`) to an ordered list of candidate XBRL tags, because
different filers use different tags for the same concept. The original
implementation picked the FIRST candidate tag with ANY non-empty data
anywhere in the company's history and used ONLY that tag's facts for every
year -- which breaks when a single company itself switches tags across its
own filing history (ASC 606 adoption, FASB guidance changes, or a filer just
changing its disclosure tag).

Confirmed on real GOOGL data (CIK 0001652044): Alphabet reported revenue
under `RevenueFromContractWithCustomerExcludingAssessedTax` from ~2018
through FY2024, then switched to plain `Revenues` for its FY2025 10-K
(accession 0001652044-26-000018, `val=402836000000`). Locking onto the first
tag made FY2025 revenue silently disappear (MISSING) everywhere in the app,
even though the true figure was sitting in the company's own filed XBRL data
under another valid candidate tag. The same multi-tag pattern (older
`SalesRevenueNet` alongside the post-606 `RevenueFromContractWithCustomer...`
tags) is also present in AAPL's and MSFT's revenue history, though neither
currently has a *conflicting* value for an overlapping period.

Fix: `resolve_concept` now checks every candidate tag that has any data and
merges their fact-entry lists into one combined list (each entry keeps its
own `_xbrl_tag` and `_tag_priority` for provenance), so `_pick_fact_for_period`
can pick the right period regardless of which literal tag a company was
using that year. `ConceptMatch.tag` still reports the single
highest-priority tag with any data, for simple "which tag matched" callers,
but per-value provenance (`FinancialFact.xbrl_tag`) is now taken from the
individual fact entry (`_xbrl_tag`), not the company-wide `match.tag` --
previously every value was mislabeled with whichever tag happened to match
first, even when a later year's actual value came from a different tag.

If the exact same reporting period is reported with a genuinely different
value under two different candidate tags, `_pick_fact_for_period` returns
`is_conflicting=True` and the caller records `DataStatus.CONFLICTING` instead
of silently choosing one value -- no averaging, no guessing. When two tags
report the *same* value for an overlapping period (duplicate coverage, not a
real conflict), the higher-priority tag's entry is kept and no conflict is
raised. No real conflicting pair has been observed in GOOGL/AAPL/MSFT
revenue, operating_income, or net_income to date -- this path exists for
correctness when one is eventually found, not because a case is currently
known.

## Insider transactions & institutional holdings

Added directly against SEC's free public data (no third-party wrapper
services) after evaluating and rejecting five paid SEC-data-wrapper vendors
(StockFit, Edgrapi, Filingrail, Hotstoks, BriefTape) as unnecessary re-wraps
of data already reachable from EDGAR, violating the project's free-first,
no-paid-dependency principle.

- **Insider transactions (Form 3/4/5)** are issuer-centric: SEC's
  submissions data for a company lists every Form 3/4/5 filed *about* that
  company, so `GET /companies/{ticker}/insider-transactions` works exactly
  like the rest of this API — resolve ticker → CIK → filings → parse →
  persist.
- **Institutional holdings (13F-HR)** are filer-centric, not issuer-centric:
  a 13F is filed *by* an institutional investment manager *about* its own
  portfolio; SEC does not publish a reverse index ("which 13F filers hold
  ticker X"). `GET /companies/{ticker}/institutional-holdings` therefore
  requires an explicit `filer_cik` (a known institutional manager, e.g.
  Berkshire Hathaway's `0001067983`) and returns that filer's holdings,
  optionally narrowed with `issuer_contains` (a substring match against
  `nameOfIssuer`). This is a real constraint of SEC's data shape, not a
  simplification — see `docs/data-model.md` and `docs/api-documentation.md`
  for the full discussion.
- Both real document shapes (a real Apple Inc. Form 4 and a real Berkshire
  Hathaway 13F-HR information table) were fetched and inspected before
  writing the parser (`backend/data/ownership_xml.py`) — see that module's
  docstring and `backend/tests/fixtures/ownership_samples.py` for the
  real (Form 4) / real-trimmed (13F) fixtures used in tests.

## Deviations from the original master spec

- **Segment data required parsing inline-XBRL documents, not the
  companyfacts API.** The original plan assumed SEC's `companyfacts` JSON
  endpoint would carry segment-level (dimensional) facts the same way it
  carries consolidated facts. In practice, segment/dimensional data (e.g.
  revenue by reportable segment) is only reliably available in the
  filing's own inline-XBRL instance document (the `R*.htm`/`.xml` viewer
  data attached to the actual 10-K accession), not in the flattened
  companyfacts JSON, which only exposes top-level (non-dimensional)
  concepts. `backend/data/segment_extractor.py` calls
  `SECConnector.get_filing_xbrl` to fetch and parse that instance document
  directly (contexts, dimensions, segment members) rather than querying
  companyfacts a second time. This is slower and more fragile
  (single-segment filers, non-standard tagging, and axis-naming variance all
  show up as a `note` on the result rather than a hard failure) but is the
  only approach that actually returns real per-segment numbers.
- **No ticker → company_id resolution endpoint.** `POST
  /valuation/{ticker}/run` and `GET /valuation/{ticker}/runs` are `501`
  stubs; the governed valuation-run flow (`POST /valuation/run` etc.) is
  `company_id`-keyed only. Frontend pages that need a numeric `company_id`
  ask for it explicitly with an explanatory banner instead of pretending
  it's derivable from the ticker alone.
- **Investment memo generation (`GET /memo/{ticker}`) was never wired up.**
  The underlying AI task (`backend/ai/tasks/memo_generator.py`,
  `generate_memo_section`) exists and is unit-tested, but no router
  assembles a full memo from it; the endpoint remains a `501` stub and the
  frontend renders a graceful "not yet implemented" state.
- **No authentication, rate limiting beyond SEC's own, deployment configs,
  or Alembic migration CI landed.** These were scoped as a distinct
  hardening/deployment phase in the original plan and were consciously
  descoped from the 10 phases actually executed — see the
  Definition-of-Done audit (`docs/definition-of-done.md`) for the honest
  accounting of what's DONE vs PARTIAL vs NOT DONE.

## Request flow (typical: ticker → SOTP valuation)

1. Frontend resolves `GET /companies/{ticker}` (name/CIK/SIC) and
   `GET /companies/{ticker}/facts` (normalized `FinancialFact` rows,
   persisted on first fetch, served from DB on subsequent fresh requests).
2. `GET /companies/{ticker}/segments` extracts and persists per-segment
   facts + coverage scores from the latest 10-K's inline-XBRL document.
3. `GET /companies/{ticker}/market-data` gets a live price/market-cap/beta
   snapshot (best-effort persisted).
4. The caller (frontend or a script) assembles a `DcfInput`/`CompsInput`
   per segment and calls the pure-compute `POST /valuation/dcf` /
   `/valuation/comps` to get segment-level enterprise values.
5. Those segment EVs feed `POST /valuation/sotp` (non-operating assets,
   debt, minority interest, overhead treatment supplied explicitly) →
   `SotpResult` with equity value and implied price/share.
6. Where AI-recommended assumptions were used, they must first go through
   `POST /valuation/assumptions/propose` → human decision via
   `POST /valuation/assumptions/{id}/decide` before
   `POST /valuation/run` will persist a `ValuationRun` referencing them —
   unapproved assumptions are a hard `400`, not a soft warning.
7. Every governance decision and run is queryable via
   `/valuation/company/{id}/audit-trail` and
   `/valuation/company/{id}/runs` (version history + diff).

## Frontend

Next.js 14 App Router, TypeScript (`strict: true`), Tailwind CSS. Real
interactive pages (SOTP, DCF, Comps, Scenarios, Sensitivities, Reverse
Valuation, Risks, Value Unlock, AI Analyst) POST to the live backend and
render real results; read-oriented pages (Overview, Segments, Research
History, Investment Memo) fetch live data with loading/error states.
Reusable primitives live in `apps/web/components/` (skeletons, error/empty
states, provenance badges, AI-origin badges) and `apps/web/components/charts.tsx`
(Recharts-based: segment valuation bars, SOTP waterfall, scenario
comparison, WACC × terminal-growth sensitivity heatmap, DCF FCFF
projection line). `npm run build` performs full TypeScript type-checking
and a production build of all routes.

See `docs/implementation-plan.md` for the full phase-by-phase build log and
`docs/definition-of-done.md` for the final scope audit.
