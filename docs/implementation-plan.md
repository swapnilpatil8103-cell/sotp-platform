# Implementation Plan

This is the map for building SOTP Intelligence across 10 phases. Phase 1
(this repository, as of now) is the foundation. Every later phase should read
this document first, then read the actual code it slots into.

## Current state (end of Phase 1)

- Monorepo scaffold exists: `apps/web` (Next.js landing page only),
  `backend` (FastAPI app with stub routers, real SQLModel schema, empty
  service/valuation/ai/governance/audit packages), `packages/shared-types`,
  `docs`, `data/cache`, `scripts`, top-level `tests`.
- Database schema is fully modeled in `backend/models/` (Company, Filing,
  FinancialFact, Segment, SegmentFinancialFact, ValuationRun,
  AssumptionDecision, AuditLogEntry) but no migrations have been run against a
  real database yet and no data has been ingested.
- No external API integrations exist yet (no SEC calls, no AI calls). All
  such surfaces are typed stubs or interfaces.
- Frontend is a static landing page; ticker "Analyze" can route to a stub
  `/dashboard` page but does no real work.

## Target architecture

```
                       ┌─────────────────────┐
                       │   apps/web (Next.js)│
                       │  landing → dashboard │
                       └──────────┬───────────┘
                                  │ HTTP (fetch)
                                  ▼
                       ┌─────────────────────┐
                       │  backend/api         │
                       │  FastAPI routers      │
                       └──────────┬───────────┘
             ┌────────────────────┼─────────────────────┐
             ▼                    ▼                      ▼
   ┌──────────────────┐ ┌──────────────────┐  ┌──────────────────────┐
   │ backend/services   │ │ backend/valuation │  │ backend/ai            │
   │ SEC/market clients │ │ DCF/comps/SOTP    │  │ Gemini adapter,       │
   │                    │ │ (deterministic)   │  │ recommendations only │
   └─────────┬──────────┘ └─────────┬────────┘  └───────────┬──────────┘
             ▼                      │                        ▼
   ┌──────────────────┐             │              ┌──────────────────────┐
   │ backend/data       │           │              │ backend/governance     │
   │ normalization      │           │              │ approval gates         │
   └─────────┬──────────┘           │              └───────────┬──────────┘
             └──────────────┬───────┴────────────────────────┬─┘
                             ▼                                ▼
                   ┌──────────────────────────────────────────────┐
                   │            backend/models (ORM)                │
                   │  Company, Filing, FinancialFact, Segment,       │
                   │  SegmentFinancialFact, ValuationRun,            │
                   │  AssumptionDecision, AuditLogEntry              │
                   └───────────────────┬──────────────────────────┘
                                       ▼
                             PostgreSQL (Supabase)
                                       ▲
                                       │ every write logged
                             ┌──────────────────┐
                             │  backend/audit     │
                             └──────────────────┘
```

## Data flow (SEC filing → valuation → memo)

```
SEC EDGAR ──fetch──▶ services ──normalize──▶ data ──persist──▶ models (FinancialFact)
                                                                     │
                                                                     ▼
                                                        valuation (deterministic DCF/comps/SOTP)
                                                                     │
                                          ai proposes assumptions ◀──┤
                                                    │                │
                                                    ▼                │
                                          governance (human approves/overrides)
                                                    │                │
                                                    ▼                ▼
                                          AssumptionDecision ──▶ ValuationRun (versioned)
                                                    │
                                                    ▼
                                              audit (AuditLogEntry)
                                                    │
                                                    ▼
                                        api ──▶ web (dashboard / memo view)
```

## Phases

### Phase 1 — Foundation (this repo)
Monorepo scaffold, DB schema, stub backend/frontend, docs, CI-ready structure.
No external calls. Deliverables: everything under this repo as it stands now.

### Phase 2 — SEC data pipeline (DONE)
Built:
- `backend/services/sec_client.py` — ticker→CIK (`company_tickers.json`),
  CIK→submissions, CIK→XBRL companyfacts, `SEC_USER_AGENT`-enforced headers,
  a token-bucket rate limiter (~8 req/sec) with retry/backoff, and typed
  errors (`SECNotFoundError`, `SECRateLimitError`, `SECUnavailableError`) so
  failures never propagate as raw exceptions.
- `backend/services/cache.py` — file-based JSON cache under `data/cache/sec/`
  keyed by URL hash, 24h TTL, checked before every network call.
- `backend/data/concept_mapping.py` — the 21 canonical concepts from the spec,
  each mapped to an ordered list of real US-GAAP XBRL tag candidates, plus a
  `resolve_concept`/`resolve_all_concepts` resolver with provenance (which tag
  matched, which taxonomy/unit).
- `backend/data/normalizer.py` — turns raw XBRL companyfacts + the concept map
  into `FinancialFact` rows for a target fiscal year/period, with full
  provenance (source, source_url, accession_number, xbrl_tag) and honest
  `data_status` (`REPORTED` vs `MISSING` — never estimated).
- `/companies/{ticker}`, `/companies/{ticker}/filings`, `/companies/{ticker}/facts`,
  and `/filings?ticker=` made real (`backend/api/routers/companies.py`,
  `filings.py`, schemas in `backend/schemas/company.py`), all backed live by
  SEC EDGAR with proper 404/503 handling — no DB persistence wired yet.
- Tests in `backend/tests/`: `test_concept_mapping.py`, `test_normalizer.py`,
  `test_sec_client.py` (unit, fixture-based) plus one `@pytest.mark.integration`
  test that hits real SEC EDGAR for AAPL (CIK lookup + submissions) — verified
  passing.

Deferred to a later pass (not required for this phase's scope): Alembic
migrations / actual DB persistence of ingested facts, and the `scripts/`
ingestion CLI. The service/data layer is DB-agnostic (`normalize_company_facts`
returns unpersisted `FinancialFact` instances), so wiring persistence later is
additive, not a rewrite. Depends on: Phase 1 models.

### DB persistence wiring (done, alongside Phase 3)
Built ahead of schedule (deferred item from Phase 2) since Phase 3 segment
data needed somewhere to land:
- `backend/db.py` — SQLAlchemy/SQLModel engine + session factory reading
  `DATABASE_URL`, a `get_session()` FastAPI dependency, and `create_all()` for
  dev/test bootstrapping.
- `backend/alembic.ini` + `backend/migrations/` — Alembic wired to
  `SQLModel.metadata`; initial migration (`initial schema`) covers all 8
  models including the natural-key unique constraints added for upsert safety
  (`FinancialFact(company_id, concept, period, xbrl_tag)`,
  `SegmentFinancialFact(segment_id, concept, period, xbrl_tag)`,
  `Segment(company_id, name)`).
- `backend/data/persistence.py` — upsert helpers (`get_or_create_company`,
  `upsert_financial_facts`, `get_or_create_segment`,
  `upsert_segment_financial_facts`) keyed on those natural keys so repeated
  ingestion never duplicates rows.
- `GET /companies/{ticker}/facts` now persists via the DB and serves already-
  ingested fiscal years from Postgres/SQLite instead of re-hitting SEC EDGAR.
- `backend/tests/conftest.py` — autouse fixture that points `DATABASE_URL` at
  a throwaway per-test SQLite file and runs `create_all()`, so the full test
  suite (unit + integration) never needs a live Postgres/Supabase instance.
  Real deployments still apply the Alembic migration.

### Phase 3 — Segment data extraction (DONE)
Built:
- `backend/data/xbrl_instance.py` — fetches and parses a filing's **inline
  XBRL** primary document (the `ix:*`-tagged 10-K HTML SEC filers have
  submitted since ~2019). This was necessary because SEC's `companyfacts`
  JSON API strips XBRL dimensional qualifiers entirely — segment-level facts
  (tagged with `us-gaap:StatementBusinessSegmentsAxis` or similar) only exist
  in the actual filing instance, not in `companyfacts`. Parses
  `xbrli:context` (incl. `xbrldi:explicitMember` dimensions) and
  `ix:nonFraction` facts (handling `scale`/`sign`/parenthesized-negative
  formatting) with `lxml`.
- `backend/data/segment_extractor.py` — discovers segment-axis-qualified
  contexts, groups them into human-readable segment names (e.g.
  `goog:GoogleCloudMember` → "Google Cloud"), and extracts
  revenue/operating_income/da/capex/assets per segment/period with full
  provenance (REPORTED/MISSING, xbrl_tag, accession_number, source_url).
  Returns an empty segment list with an explanatory note (never a fabricated
  segment) when a filing has no segment-dimensional facts at all.
- `backend/data/segment_coverage.py` — deterministic per-metric and overall
  coverage-% scoring (REPORTED rows / total rows seen), no AI involved.
- `GET /companies/{ticker}/segments` (`backend/api/routers/segments.py`) made
  real: resolves the latest 10-K, extracts segments from its inline-XBRL
  instance, persists `Segment`/`SegmentFinancialFact` via the Part A DB
  wiring, and returns segments with coverage scores and full provenance.
- Tests: `test_segment_extractor.py` / `test_segment_coverage.py` (unit,
  hand-crafted fixture instance document, no network) plus
  `test_segment_extractor_integration.py` (`@pytest.mark.integration`,
  verified passing against real Alphabet (GOOGL) 10-K data — correctly
  extracts Google Services / Google Cloud / All Other Segments revenue and
  operating income).

### Phase 4 — Market data integration (DONE)
Built:
- `backend/services/market_data_client.py` — `yfinance`-backed client
  fetching current/latest price, market cap, shares outstanding, beta,
  dividend yield/last dividend, and 1-2y daily historical OHLCV prices.
  Follows the SEC client's conventions: typed errors
  (`MarketDataNotFoundError`, `MarketDataUnavailableError`) so failures never
  propagate raw, and disk caching via `backend/services/cache.py` under
  `data/cache/market_data/` — but with much shorter TTLs than SEC filings
  (15 min for price/market-cap/shares, 24h for beta/dividends/historical
  prices), since market data goes stale far faster. Every value is
  explicitly framed as "latest available" / source-dependent
  (`is_latest_available_not_realtime=True`, `as_of` + `fetched_at`
  timestamps) — never presented as real-time tick data. Missing fields
  (e.g. beta for some tickers) are left `None`, never estimated.
- `backend/models/market_data_snapshot.py` — new `MarketDataSnapshot` model
  (price, currency, market_cap, shares_outstanding, beta, dividend_yield,
  last_dividend_value/date, `as_of` date, `source`, `fetched_at`) with the
  same provenance discipline as `FinancialFact`. Natural-key unique
  constraint on `(company_id, as_of)` so repeated same-day fetches upsert in
  place. Migration: `backend/migrations/versions/a1b2c3d4e5f6_add_market_data_snapshot.py`.
- `backend/data/persistence.py` — added `upsert_market_data_snapshot`,
  keyed on `(company_id, as_of)`, following the existing upsert-by-natural-key
  pattern.
- `GET /companies/{ticker}/market-data` (`backend/api/routers/market_data.py`)
  — returns price, market cap, shares outstanding, beta, dividend info, and
  freshness metadata; 404 for unknown tickers, 503 for provider failures.
  Best-effort persists a snapshot (via SEC CIK lookup to resolve/create the
  `Company` row) but still returns live data if that persistence step fails.
- Tests: `backend/tests/test_market_data_client.py` (unit, mocked yfinance
  responses), `test_market_data_persistence.py` (unit, upsert-by-natural-key),
  `test_market_data_router.py` (unit, mocked dependencies), plus
  `@pytest.mark.integration` tests hitting real Yahoo Finance for AAPL —
  verified passing (see below for real pulled values).
Depends on: Phase 2 (for the SEC CIK lookup used to resolve the Company row).

### Phase 5 — Deterministic valuation engine (DONE)
Built:
- `backend/data/business_classifier.py` — deterministic, rule-based SIC-code
  classifier (`classify_business`). An explicit, ordered SIC-range -> business
  category table (Bank, Insurance, REIT, Financial Institution, Mining,
  Energy, Healthcare, Software, Technology, Communication Services, Consumer,
  Consumer Staples, Industrial, Growth/Early Stage, Conglomerate, Other) plus
  a category -> eligible-valuation-methodologies table (e.g. Bank -> P/B, P/E,
  DDM; REIT -> NAV, P/FFO, AFFO; Software -> DCF, EV/Revenue, EV/EBITDA).
  Pure/no I/O; unknown or missing SIC codes classify as "Other" rather than
  guessing.
- `backend/valuation/wacc.py` — CAPM cost of equity, after-tax cost of debt,
  WACC, all inputs caller-supplied (no hardcoded market assumptions).
- `backend/valuation/dcf.py` — multi-year revenue/EBIT/FCFF projection from
  explicit growth-rate/margin schedules, Gordon Growth terminal value,
  EV -> equity bridge, implied price/share. Every intermediate value
  populated on the result for audit trail.
- `backend/valuation/comps.py` — per-peer EV/Revenue, EV/EBITDA, EV/EBIT,
  P/E, P/B multiples; percentile stats (`numpy.percentile`) across an
  already-assembled peer set; implied valuation from an explicitly chosen
  multiple. Does not select peers or multiples itself (upstream concern).
- `backend/valuation/sotp.py` — SOTP bridge: ownership-adjusted sum of
  pre-computed segment EVs, + non-operating assets, - debt/minority
  interest/corporate liabilities, with both `direct_deduction` and
  `capitalized_overhead` corporate-cost treatments always computed and
  returned side by side; conglomerate discount/premium and upside/downside
  vs. an optional current market price.
- `backend/valuation/sensitivity.py` — generic 2D sensitivity-matrix builder
  that re-runs an existing valuation function (DCF or comps) across a grid of
  two input values; convenience wrappers `dcf_sensitivity`/`comps_sensitivity`.
- `backend/schemas/valuation.py` — Pydantic input/result models for all of the
  above (`WaccInput/Result`, `DcfInput/Result` incl. per-year projection rows,
  `CompPeer`/`CompsInput/Result`, `SotpSegmentInput`/`SotpInput/Result`,
  `SensitivityInput/Result`). Every material assumption field is required;
  only cosmetic parameters may default.
- `backend/api/routers/valuation.py` — `POST /valuation/sotp` made real: takes
  a fully-assembled `SotpInput` and returns the computed `SotpResult`, no
  persistence. `POST /valuation/{ticker}/run` and `GET /valuation/{ticker}/runs`
  remain stubs (501) — assembling real inputs from persisted
  Company/FinancialFact/Segment/MarketDataSnapshot data and writing
  `ValuationRun` rows is deferred to a follow-up pass, since it depends on
  assumption-approval shapes that land properly in Phase 6/7.
- Tests: `test_business_classifier.py`, `test_valuation_wacc.py`,
  `test_valuation_dcf.py`, `test_valuation_comps.py`, `test_valuation_sotp.py`,
  `test_valuation_sensitivity.py`, `test_valuation_router.py` — all pure unit
  tests against hand-calculated/independently-recalculated expected values
  (no fixtures needed, no network). Full backend suite verified passing (62
  tests, unit-only; `-m "not integration"`).

Deferred: full ticker-driven `/valuation/{ticker}/run` (data assembly +
`ValuationRun` persistence) and reading back valuation history via
`/valuation/{ticker}/runs`. Depends on: Phases 2–4 for real inputs (the
engine itself was built/tested against hand-crafted fixture data, independent
of live data).

### Phase 6 — AI reasoning layer (DONE)
Built:
- `backend/ai/gemini_adapter.py` — `GeminiAdapter(AIAdapter)` using the real
  `google-generativeai` SDK, reading `GEMINI_API_KEY` from env. Lazily
  imports/initializes the SDK so module import never fails; any missing key
  or SDK failure raises `AIUnavailableError`/`AIRateLimitError`
  (`backend/ai/errors.py`) instead of crashing or fabricating a response.
  `backend/ai/adapter.py` extended with a `generate_text(prompt) -> str`
  abstract method (the shared entry point structured tasks use) alongside
  the original `recommend_assumption`/`draft_narrative` methods.
- `backend/ai/testing.py` — `FakeAIAdapter`, a deterministic test double
  implementing the same interface (canned/callable text, or simulated
  `AIUnavailableError`/`AIRateLimitError`), used by all AI unit tests.
- `backend/ai/tasks/` — six structured tasks, each building its prompt only
  from real input data, calling the adapter, parsing into a typed Pydantic
  result, and exposing an explicit ABSTAIN path:
  `research_summary.py`, `methodology_recommendation.py` (stays within
  `business_classifier.py`'s eligible-methodology list, drops anything
  else), `peer_recommendation.py` (flagged `requires_human_approval=True`),
  `assumption_recommendation.py` (proposals shaped to map onto
  `AssumptionDecision.ai_*` fields for Phase 7 to persist/approve),
  `devils_advocate.py` (7 risk categories, `advisory_only=True`, never
  mutates the valuation it critiques), `memo_generator.py`.
  `backend/ai/json_utils.py` provides shared best-effort JSON extraction
  from model text (fenced or bare), raising rather than guessing on failure.
- `backend/ai/validation.py` — the guardrail: `validate_ai_numbers()`
  regex-extracts numeric tokens from AI text and cross-checks each against a
  flattened set of numbers from the structured input (with tolerance for
  rounding and percentage-vs-fraction forms). Any AI output with an
  unmatched number is discarded/ABSTAINed, never silently kept.
- Tests: `backend/tests/test_ai_*.py` (31 tests) covering prompt
  construction from real data, response parsing, ABSTAIN triggers, the
  guardrail catching a fabricated number vs. passing a real one, and
  `test_ai_import_boundary.py` (AST-based check that no file under
  `backend/ai/` imports `backend/valuation/`). Full backend suite: 96 passed
  (with `SEC_USER_AGENT` set for the two market-data-router tests that need
  it; those two are unrelated to this phase).
- No real Gemini API key is configured in this environment, so only
  `FakeAIAdapter` was exercised in tests; `GeminiAdapter`'s unavailable-path
  was verified directly (`test_gemini_adapter_unavailable_without_api_key`).
Depends on: Phase 5 (needs assumption shapes) and Phase 1 governance stub.

### Phase 7 — Governance & human-in-the-loop backend (DONE)
Built (backend wiring only; frontend review UI deferred to Phase 9):
- `backend/governance/approval.py` — `propose()` creates a PENDING
  `AssumptionDecision` from a Phase 6 AI task's output (no valuation math,
  no AI call itself); `record_decision()` records a human APPROVE/EDIT/REJECT
  against a PENDING row, never mutating `ai_recommended_value`/`ai_rationale`
  (both AI-original and human-final stay visible on the same row forever).
  Re-deciding an already-decided row raises `GovernanceError`. A structural
  test (`test_governance_module_never_imports_valuation_engine`) asserts
  `backend/governance/approval.py` never imports `backend.valuation`.
- `backend/models/assumption_decision.py` — extended: `valuation_run_id` is
  now nullable (a proposal exists before any run consumes it), plus new
  `company_id` and `subject` columns. Migration:
  `backend/migrations/versions/b2c3d4e5f6a7_governance_fields.py`.
- `backend/governance/valuation_run.py` — the enforcement point.
  `assemble_approved_assumptions()`/`execute_valuation_run()` re-check every
  `decision_id` against the DB (status must be APPROVED or OVERRIDDEN, never
  PENDING/REJECTED, and must belong to the target company) and raise
  `UnapprovedAssumptionError` otherwise -- so no `ValuationRun` row can ever
  be written from an AI recommendation that a human hasn't acted on.
  `execute_valuation_run()` persists a versioned `ValuationRun` (version
  auto-incremented per company+method), snapshotting which decisions/
  assumption values/source-data version produced it. `diff_runs()` is a pure,
  deterministic (no AI) flattened-field comparison between two runs for the
  same company, highlighting key assumption/output field changes.
- `backend/audit/logger.py` — `log_event()` is the single write path for
  `AuditLogEntry` (append-only); `get_audit_trail()` returns a
  chronologically ordered, role-tagged (`AI_INTERPRETATION` / `INPUT` /
  `CALCULATION_AND_OUTPUT` / `SOURCE`) trail per entity or per company, so a
  caller can reconstruct the CLAIM->SOURCE->INPUT->CALCULATION->OUTPUT->AI
  INTERPRETATION story. `propose`/`record_decision`/`execute_valuation_run`
  all call it automatically with the correct actor (`"ai"`,
  `"human:<user_id>"`, or `"system"`).
- `backend/schemas/governance.py` — typed request/response models for the
  new endpoints.
- `backend/api/routers/valuation.py` — added `POST /valuation/assumptions/propose`,
  `POST /valuation/assumptions/{id}/decide`, `GET /valuation/company/{id}/assumptions`,
  `POST /valuation/run` (governed, gated execution), `GET /valuation/company/{id}/runs`,
  `GET /valuation/company/{id}/runs/diff`, and two audit-trail endpoints
  (`GET /valuation/company/{id}/audit-trail`, `GET /valuation/runs/{id}/audit-trail`).
  The old ticker-driven `/valuation/{ticker}/run` and `/valuation/{ticker}/runs`
  stubs remain 501 (ticker->company_id auto-resolution is still deferred);
  the governed flow works off explicit `company_id`.
- Tests: `backend/tests/test_governance_approval.py` (8),
  `test_governance_valuation_run.py` (6), `test_audit_logger.py` (4) — 18 new
  tests. Full backend suite: 111 total, 109 passed (`-m "not integration"`);
  the 2 pre-existing failures are unrelated `test_market_data_router.py`
  cases that need `SEC_USER_AGENT` set in the environment.
- Not wired (explicitly deferred, per phase scope): ingestion-level audit
  hooks in `backend/data/persistence.py` (Phase 2/3's ingestion path) --
  demonstrated instead via the governance/valuation-run flow, which is a
  clean, low-risk place to prove the audit write path without touching
  ingestion's existing passing tests. Also deferred: the frontend
  approve/edit/reject review UI (Phase 9) and ticker-driven `/run` auto data
  assembly (still needs Phase 2-4 read wiring).
Depends on: Phase 6 (AI proposal shapes), Phase 5 (engine invoked, not
modified), Phase 1 (`AssumptionDecision`/`AuditLogEntry`/`ValuationRun`
models).

### Phase 8 — Scenario engine, reverse valuation, value-unlock, risk dashboard (DONE)
Built (all core numeric work deterministic Python in `backend/valuation/`;
AI only proposes/explains, never computes a dollar figure):
- `backend/valuation/scenarios.py` — `run_scenarios()` takes three explicit
  `ScenarioRunInput` sets (BULL/BASE/BEAR, each a fully-formed `DcfInput` --
  no invented defaults), runs each through Phase 5's `run_dcf` unmodified,
  and returns all three outcomes plus upside/downside vs. an optional
  current market price. Pure composition, no duplicated DCF math.
- `backend/valuation/reverse_valuation.py` — `solve_reverse_valuation()`
  uses `scipy.optimize.brentq` to solve for the single DCF assumption
  (`revenue_growth` flat-rate or `terminal_growth`) that reconciles a
  target enterprise value, holding every other input fixed; raises
  `ValueError` (never fabricates an answer) if the target isn't bracketed.
  `classify_assumption()` is a deterministic, quantile-based rule against
  an explicit historical range + explicit peer range: position = (implied -
  combined_low) / (combined_high - combined_low); REASONABLE in [0,1],
  CONSERVATIVE in [-0.5,0), AGGRESSIVE in (1,1.5], EXTREME outside
  [-0.5,1.5]. `backend/ai/tasks/reverse_valuation_explainer.py` — optional
  AI task that explains an already-finalized result in prose; every numeric
  token is checked via `validate_ai_numbers` and the whole explanation is
  discarded (ABSTAIN) if any number can't be traced to the input.
- `backend/valuation/value_unlock.py` — deterministic compute functions for
  spin-off/segment-separation, subsidiary IPO, asset sale, buyback, debt
  reduction, and special dividend, each taking only explicit caller-supplied
  multiples/proceeds/amounts (no hardcoded "typical" discount or multiple).
  `backend/ai/tasks/value_unlock_ideas.py` — AI proposes WHICH action types
  might be worth modeling (from a fixed enum) with rationale; reuses Phase
  7's `approval.propose()`/`AssumptionDecision` pattern for human approval
  (a proposed structural action is treated as the same shape as a proposed
  assumption -- the `ai_recommended_value` float column stores a stable
  categorical code for the action_type via `ACTION_TYPE_CODES`, never a
  dollar figure). The AI module's result type has no field capable of
  holding an uplift or potential value at all.
- `backend/valuation/risk_dashboard.py` — six risk categories. Data/Model/
  Forecast/Market Risk are rule-based from real inputs (segment coverage %,
  methodologies-used + sensitivity spread, reverse-valuation's
  classification, beta/volatility) via fixed documented thresholds.
  Strategic Risk and Execution Risk are AI-assisted
  (`backend/ai/tasks/risk_explanation.py`) but constrained to a strict
  LOW/MEDIUM/HIGH enum -- any AI response that doesn't map exactly onto
  that enum for both categories is discarded, not coerced. `RiskCategoryScore`
  lives in `backend/schemas/valuation.py` (not `backend/valuation/`) so the
  AI task can use the same shape without importing `backend.valuation`
  (preserves the Phase 6 AI-import-boundary rule).
- `backend/api/routers/scenarios.py` — `POST /scenarios/run`,
  `POST /scenarios/reverse-valuation` (+ `/explain`),
  `POST /scenarios/value-unlock/propose` (+ `/compute/{action_type}`,
  gated on the proposal's `AssumptionDecision` being APPROVED/OVERRIDDEN),
  `POST /scenarios/risk-dashboard`.
- Tests: `test_scenarios.py`, `test_reverse_valuation.py` (incl. two
  round-trip solves), `test_value_unlock.py`, `test_risk_dashboard.py` (incl.
  adversarial AI enum-rejection cases) — 155 total backend tests passing
  (`-m "not integration"`).
Depends on: Phase 5 (engine invoked, not modified), Phase 6 (AI task/
guardrail conventions), Phase 7 (governance approval pattern reused for
value-unlock proposals).

### Phase 9 — Polished frontend UI (DONE)
Built: a full multi-page dashboard in `apps/web` consuming the real backend
API, replacing the Phase 1 static landing page + stub `/dashboard` route.

- Navigation: 14 routed pages under
  `apps/web/app/(dashboard)/[ticker]/` (Next.js App Router, dynamic `ticker`
  segment, shared sidebar layout in `.../[ticker]/layout.tsx`) — Overview,
  Segments, Valuation, DCF, Comps, SOTP, Scenarios, Sensitivities, Reverse
  Valuation, Value Unlock, AI Analyst, Risks, Research History, Investment
  Memo. `apps/web/app/page.tsx` now routes `Analyze` to
  `/dashboard/{TICKER}`; the old `/dashboard?ticker=` route
  (`apps/web/app/dashboard/page.tsx`) is kept as a redirect shim for
  backwards compatibility.
- Types: colocated in `apps/web/lib/types.ts` rather than
  `packages/shared-types` — deliberate choice, documented in that file's
  header comment: the backend response shapes are Pydantic models, and
  mirroring their field names 1:1 (snake_case, not idiomatic camelCase)
  avoids a transformation layer across ~10 schema files. The Phase 1
  `packages/shared-types/index.ts` stub is left untouched for later
  reconciliation.
- API client: `apps/web/lib/api.ts`, a thin `fetch` wrapper with a typed
  `ApiError` (status 0 = backend unreachable, distinguished from 404/501/503
  from the API itself) so every page can render the right graceful message
  ("Backend unavailable" vs "Not yet implemented" vs "Insufficient data
  coverage for reliable valuation").
- Design system: `apps/web/components/ui.tsx` (Card, MetricCard,
  ProvenanceBadge, AIBadge/AIPanel, RiskBadge, ClassificationBadge, Table,
  Skeleton/PageSkeleton, ErrorState, EmptyState, SectionHeading) implementing
  the master spec's palette/radius/typography exactly, plus the two
  first-class visual requirements: a provenance badge
  (REPORTED/DERIVED/ESTIMATED/MISSING/CONFLICTING, each a distinct color) on
  every financial figure, and a consistent AI-origin badge/panel treatment
  wherever AI content appears (AI Analyst, Value Unlock proposals, Risks'
  Strategic/Execution categories, Memo).
- Charts: `apps/web/components/charts.tsx`, real Recharts components bound
  to live API response shapes (`recharts` added to
  `apps/web/package.json`) — `SegmentValuationChart` (bar), `SotpWaterfallChart`
  (bridge waterfall), `ScenarioComparisonChart` (bull/base/bear bars),
  `SensitivityHeatmap` (WACC × terminal-growth grid, color-interpolated),
  `DcfProjectionChart` (FCFF line chart).
- Fully interactive pages (live inputs → real POST to the backend → real
  rendered results): **SOTP**, **DCF**, **Comps**, **Scenarios**,
  **Sensitivities**, **Reverse Valuation**, **Risks**, **Value Unlock**
  (full propose → approve/reject → compute governance loop wired to
  `/scenarios/value-unlock/propose` and `/valuation/assumptions/{id}/decide`),
  **AI Analyst** (Why? / View Evidence / Challenge / View Sources wired to
  the Phase 7 assumptions + audit-trail endpoints).
- Real-fetch, read-oriented pages (no editable form, but real typed fetch
  against the live endpoints with loading/error states): **Overview**
  (`/companies/{ticker}`, `/market-data`, `/filings`, `/segments`),
  **Segments** (full per-segment fact table with provenance), **Research
  History** (run list + diff viewer against Phase 7's endpoints), **Investment
  Memo** (calls the real `/memo/{ticker}`, which is still a 501 stub —
  rendered as a graceful "not yet implemented" state, not faked content).
  **Valuation** (triangulation) is a client-side weighted blend of the
  DCF/SOTP/Comps implied prices computed on their own tabs — there is no
  single backend "triangulate" endpoint, so this composes results the user
  pastes in rather than calling a nonexistent route.
- Known integration gap surfaced (not fixed, per phase scope): the backend
  has no ticker→company_id resolution endpoint (`/valuation/{ticker}/run`
  and `/runs` are still 501 per Phase 5/7). Pages needing a numeric
  `company_id` (Research History, Value Unlock, AI Analyst) ask for it
  explicitly with a banner explaining why, rather than pretending it's
  derived from the ticker.
- Backend fixes made (minimal, additive, same pure-compute/no-persistence
  convention as the existing `POST /valuation/sotp`): added
  `POST /valuation/dcf`, `POST /valuation/comps`, and
  `POST /valuation/sensitivity/dcf` to `backend/api/routers/valuation.py`.
  These wrap `backend.valuation.dcf.run_dcf`, `backend.valuation.comps.run_comps`,
  and `backend.valuation.sensitivity.dcf_sensitivity` respectively — all
  three engine functions already existed and were tested (Phase 5), but had
  no router exposing them at all, which would have made the DCF/Comps/
  Sensitivities pages unable to call anything real. No valuation math was
  touched.
- Every data-fetching page has a loading skeleton (`PageSkeleton`/`Skeleton`)
  and a graceful error state (`ErrorState`, status-aware) — no page can
  render a blank crash on fetch failure.
- Node/npm are now available and the build has been verified end-to-end.
  `npm install` in `apps/web` succeeded cleanly (143 packages, no
  unresolved dependency/version conflicts). `npm run build` (Next.js 14
  production build, which includes full TypeScript type-checking under
  `strict: true`) initially failed with 12 type errors, all of the same
  shape: `{error && <ErrorState .../>}` where `error` is typed `unknown`
  (from `useState<unknown>`), which TS rejects as a bare JSX child
  condition (`unknown` is not assignable to `ReactNode`). Fixed by
  wrapping each with `Boolean(error)` (or equivalently `!!error`) across
  `ai-analyst`, `comps`, `dcf`, `research-history`, `reverse-valuation`,
  `risks`, `scenarios`, `sensitivities`, `sotp`, `memo`, `[ticker]`
  (`marketError`), and `value-unlock` (`proposeError`) — no `tsconfig.json`
  weakening or `@ts-ignore` was needed. `npm run build` now succeeds
  cleanly (all 14+ routes compile and prerender/build without error).
  `npm run dev` was also smoke-tested: the landing page (`/`) renders,
  and the dynamic `/AAPL` route renders its loading skeleton then
  correctly falls back to the "Backend unavailable" `ErrorState` (with
  only expected `ERR_CONNECTION_REFUSED` console errors, no React
  crashes) when the FastAPI backend isn't running — confirming the
  graceful-degradation requirement holds in practice, not just by
  hand-review.
Depends on: Phases 2–8 (consumes their API surfaces).

### Phase 10 — Hardening, auth, deployment
Authentication/authorization, rate limiting, observability, Alembic migration
CI, deployment configs (Vercel for web, containerized FastAPI + Supabase for
backend), end-to-end test suite in top-level `tests/`. Depends on: all prior
phases.

## AI governance rules (summary)

See `docs/ai-governance.md` for the full contract. Summary: AI only
recommends; humans approve; every decision and every AI call is attributed,
timestamped, and audit-logged; the valuation arithmetic itself is never
touched by AI.

## Testing strategy

- **Backend unit tests** (`backend/tests/`): pytest, one test file per
  package; `valuation/` gets the heaviest coverage since it's pure functions.
- **Backend integration tests**: spin up FastAPI with `httpx.AsyncClient` /
  `TestClient` against a test database (or SQLite for fast tests where the
  schema allows).
- **Top-level integration tests** (`tests/`): full-stack scenarios once
  Phase 2+ lands (e.g., "ingest AAPL 10-K → compute SOTP → expect X shape of
  output", not fixed numeric fixtures since real filing data changes).
- **Frontend**: component tests (Phase 9+) once real interactive UI exists;
  Phase 1 ships no frontend tests since the landing page is static.
- Every phase that adds a new package should also add its first passing test
  before being considered complete.
