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
  Phases 1–9 — see the Definition-of-Done audit).
- **`services/`** — external integration clients: `sec_client.py` (SEC
  EDGAR, with disk caching via `FileCache`, an internal rate limiter capped
  at ~8 req/s, and typed retry/backoff on 429/5xx), `market_data_client.py`
  (yfinance-backed, explicitly framed as "latest available, not real-time").
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
- **`ai/`** — AI reasoning layer: `gemini_adapter.py` (Gemini API client
  behind the `AIAdapter` protocol, swappable for `FakeAIAdapter` in tests),
  `tasks/` (one narrowly-scoped function per AI use case: assumption
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

## Deviations from the original master spec

- **Segment data required parsing inline-XBRL documents, not the
  companyfacts API.** The original plan assumed SEC's `companyfacts` JSON
  endpoint would carry segment-level (dimensional) facts the same way it
  carries consolidated facts. In practice, segment/dimensional data (e.g.
  revenue by reportable segment) is only reliably available in the
  filing's own inline-XBRL instance document (the `R*.htm`/`.xml` viewer
  data attached to the actual 10-K accession), not in the flattened
  companyfacts JSON, which only exposes top-level (non-dimensional)
  concepts. `backend/data/segment_extractor.py` fetches and parses that
  instance document directly (contexts, dimensions, segment members) rather
  than querying companyfacts a second time. This is slower and more
  fragile (single-segment filers, non-standard tagging, and axis-naming
  variance all show up as a `note` on the result rather than a hard
  failure) but is the only approach that actually returns real per-segment
  numbers.
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
