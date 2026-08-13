# API Documentation

FastAPI backend, mounted in `backend/api/main.py`. All routers below are
`app.include_router(...)`'d there. JSON in/out throughout; request/response
bodies are Pydantic models from `backend/schemas/` (governance/valuation) or
per-router response models (companies/segments). Errors are FastAPI's
default `{"detail": "..."}` shape unless noted.

## Base URL

`http://localhost:8000` in local development. The frontend reads this from
`NEXT_PUBLIC_API_BASE_URL` (see top-level `README.md`).

## Conventions

- Every financial figure in a `FinancialFactRead`/`SegmentFactRead` carries
  `data_status` (`REPORTED` / `DERIVED` / `ESTIMATED` / `MISSING` /
  `CONFLICTING`), `source`, `source_url`, `accession_number`, and `xbrl_tag`
  for provenance — see `docs/data-model.md`.
- `501 Not Implemented` is used deliberately for endpoints that are
  documented-but-deferred (never a silent stub returning fake data).
- `503` = upstream (SEC EDGAR / market data provider) unavailable or rate
  limited; retry later. `404` = ticker/resource not found. `400` = bad
  input / validation error. `422` = an AI task abstained and could not
  produce a usable result.
- Governance/valuation-run endpoints (`/valuation/assumptions/*`,
  `/valuation/run`, `/valuation/company/{id}/*`) require a `Session`
  (SQLite in tests, Postgres/Supabase via `DATABASE_URL` in real use).
- The pure-compute valuation endpoints (`/valuation/sotp`, `/dcf`, `/comps`,
  `/sensitivity/dcf`, `/scenarios/*`) do **not** touch the DB — callers
  assemble inputs themselves (from `/companies/{ticker}/facts`,
  `/companies/{ticker}/segments`, `/companies/{ticker}/market-data`) and get
  back a computed result with no persistence.

---

## `GET /health`

Health check. Returns `{"status": "ok"}`. No auth, no params.

---

## Companies — `backend/api/routers/companies.py` (prefix `/companies`)

### `GET /companies`
Not implemented — `501`. Company listing requires a persisted company
catalog, which this build does not maintain; companies are resolved
on-demand by ticker instead.

### `GET /companies/{ticker}` → `CompanyRead`
Resolves a ticker to CIK via SEC EDGAR's ticker map, then fetches
`submissions` for name/SIC/exchange/fiscal-year-end. Also persists (or
looks up) the internal `Company` row via `get_or_create_company` and
returns its id.
- Response: `{ticker, cik, name, sic, sic_description, exchange, fiscal_year_end, company_id}`
- Errors: `404` unknown ticker; `503` SEC rate-limited or unavailable.

### `GET /companies/{ticker}/id`
Dedicated ticker→`company_id` resolution endpoint (closes the Phase 10
audit gap — previously the only way to get a `company_id` was to already
have called `/facts` or `/segments`). Thin wrapper around the same
persist-or-lookup path as `GET /companies/{ticker}`.
- Response: `{ticker, company_id}`
- Errors: `404` unknown ticker; `503` SEC rate-limited or unavailable.

### `GET /companies/{ticker}/filings`
Latest 10-K/10-Q filings for the ticker (from SEC `submissions`, not
persisted `Filing` rows — see `GET /filings` below for the persisted view).
- Response: `{ticker, cik, filings: [...]}` (raw SEC filing entries: form,
  accession number, filing date, period of report, primary document).
- Errors: `404` unknown ticker or no 10-K/10-Q found; `503` SEC unavailable.

### `GET /companies/{ticker}/facts` → `CompanyFactsRead`
Query params: `fiscal_year` (optional int, defaults to latest available),
`fiscal_period` (default `"FY"`).
Fetches XBRL company facts from SEC, normalizes them into the canonical
concept list (`backend/data/concept_mapping.py`) via
`normalize_company_facts`, and **persists** them (`upsert_financial_facts`).
If a fresh persisted set already exists for the requested `fiscal_year`, it
is served from the DB without re-hitting SEC EDGAR.
- Response: `{ticker, cik, fiscal_year, fiscal_period, facts: [FinancialFactRead...]}`
  where each fact has `concept, value, unit, currency, period, fiscal_year,
  fiscal_period, filing_date, source, source_url, accession_number,
  xbrl_tag, data_status`. Concepts with no XBRL match are still included
  with `value=null, data_status="MISSING"` (never omitted).
- Errors: `404` unknown ticker / no facts for that period; `503` SEC
  rate-limited or unavailable.

---

## Market data — `backend/api/routers/market_data.py` (prefix `/companies`)

### `GET /companies/{ticker}/market-data` → `MarketDataRead`
Live snapshot (price, market cap, shares outstanding, beta, dividend) from
`backend/services/market_data_client.py` (yfinance-backed). Explicitly
framed as latest-available, not real-time (`is_latest_available_not_realtime:
true` in the response). Best-effort persists a `MarketDataSnapshot` row
(resolves the company via SEC CIK lookup first) — if that resolution fails,
the live data is still returned; persistence is not a precondition.
- Response: `{ticker, price, currency, market_cap, shares_outstanding, beta,
  dividend: {dividend_yield, last_dividend_value, last_dividend_date},
  as_of, source, fetched_at, is_latest_available_not_realtime}`
- Errors: `404` no market data for ticker; `503` provider unavailable.

---

## Filings — `backend/api/routers/filings.py` (prefix `/filings`)

### `GET /filings?ticker=XYZ`
Thin wrapper: delegates directly to `GET /companies/{ticker}/filings`
(same response shape, same errors) to avoid duplicating `SECClient` error
handling. Query param `ticker` is **required**.
- Errors: `400` if `ticker` omitted; otherwise identical to
  `/companies/{ticker}/filings` above.

---

## Segments — `backend/api/routers/segments.py` (no prefix)

### `GET /segments?ticker=XYZ`
Convenience/legacy path. Requires `ticker`; if provided, responds `400`
redirecting callers to `GET /companies/{ticker}/segments` (the real
endpoint). Not a functional listing endpoint on its own.

### `GET /companies/{ticker}/segments` → `CompanySegmentsRead`
Query param: `fiscal_year` (optional, defaults to latest available).
Extracts per-segment financial facts (revenue, operating income, D&A,
capex, assets where disclosed) by parsing the company's most recent 10-K's
**inline-XBRL instance document** directly (not the companyfacts API — see
`docs/architecture.md` for why), scores per-segment/per-metric coverage via
`compute_company_segment_coverage`, and persists segments + segment facts.
- Response: `{ticker, cik, fiscal_year, fiscal_period, segments: [
  {name, facts: [SegmentFactRead...], coverage: [MetricCoverageRead...],
  overall_coverage_pct}], overall_coverage_pct, note}`. `note` is populated
  (and `segments` may be empty) when the filer does not cleanly disclose
  multi-segment XBRL data — this is a real, expected outcome for
  single-segment companies, not an error.
- Errors: `404` unknown ticker / no 10-K / no resolvable fiscal year; `503`
  SEC unavailable or segment-document parse failure.

---

## Valuation — `backend/api/routers/valuation.py` (prefix `/valuation`)

### Pure-compute endpoints (no persistence)

- **`POST /valuation/sotp`** — body: `SotpInput` (segments with EV +
  ownership %, cash, debt, minority interest, overhead treatment, diluted
  shares) → `SotpResult` (segment attributed EVs, equity value under both
  overhead treatments, implied price/share, conglomerate discount %).
  `400` on invalid input (e.g. length mismatches, non-positive shares).
- **`POST /valuation/dcf`** — body: `DcfInput` (base revenue, per-year
  growth/margin/D&A/capex/NWC schedules, WACC, terminal growth, net debt
  bridge, diluted shares) → `DcfResult` (year-by-year projections, terminal
  value, EV, equity value, implied price/share). `400` on invalid input
  (e.g. mismatched schedule lengths — `DcfInput.validate_lengths()`).
- **`POST /valuation/comps`** — body: `CompsInput` (peer set + multiples
  basis) → `CompsResult`. `400` on invalid input.
- **`POST /valuation/sensitivity/dcf`** — body: `{base_dcf_input: dict,
  row_field, row_values, col_field, col_values, output_field}` → grid of
  `output_field` (default `implied_price_per_share`) varying two DCF input
  fields. `400` on invalid field names or types.

### Ticker-driven endpoints (deferred)

- **`POST /valuation/{ticker}/run`** — `501`. Automatic ticker→data
  assembly is not implemented; use `POST /valuation/run` with an explicit
  `company_id` (resolve it first via `GET /companies/{ticker}`).
- **`GET /valuation/{ticker}/runs`** — `501`; use
  `GET /valuation/company/{company_id}/runs`.

### Governance endpoints (Phase 7, DB-backed)

- **`POST /valuation/assumptions/propose`** — body: `ProposeAssumptionRequest`
  (`company_id, assumption_key, ai_recommended_value, ai_rationale,
  ai_confidence, subject`) → `AssumptionDecisionOut` with status `PENDING`.
  Records an AI proposal; does not call AI itself (callers run a
  `backend.ai.tasks` function first).
- **`POST /valuation/assumptions/{decision_id}/decide`** — body:
  `RecordDecisionRequest` (`decision: APPROVE|EDIT|REJECT, user_id,
  human_value, reason`) → updated `AssumptionDecisionOut`. `400` on
  governance errors (e.g. deciding an already-decided proposal).
- **`GET /valuation/company/{company_id}/assumptions`** →
  `list[AssumptionDecisionOut]`.
- **`POST /valuation/run`** — body: `RunValuationRequest` (`company_id,
  method, decision_ids, inputs, outputs, created_by, source_data_version`)
  → `ValuationRunOut`. Persists a new versioned `ValuationRun`, gated on
  every `decision_ids` entry being `APPROVED`/`OVERRIDDEN` for that company.
  Does not itself run the valuation engine — `inputs`/`outputs` are the
  already-computed result of an engine call using approved assumption
  values. `400` on unknown `method` or an unapproved/foreign decision id.
- **`GET /valuation/company/{company_id}/runs?method=...`** →
  `list[ValuationRunOut]` (version history, optionally filtered by method).
- **`GET /valuation/company/{company_id}/runs/diff?from_version=&to_version=&method=`**
  → `DiffResponse` (field-level diff between two run versions). `404` if
  either version not found.
- **`GET /valuation/company/{company_id}/audit-trail`** →
  `list[AuditTrailEntryOut]`.
- **`GET /valuation/runs/{valuation_run_id}/audit-trail`** →
  `list[AuditTrailEntryOut]` scoped to that run.

---

## Scenarios / reverse valuation / value-unlock / risk — `backend/api/routers/scenarios.py` (prefix `/scenarios`)

Same "pure computation, caller assembles inputs" convention as
`/valuation/sotp`; AI is only invoked through explicit `backend.ai.tasks`
functions, and never writes a dollar figure without a human approval step.

- **`POST /scenarios/run`** — body: `ScenarioEngineInput` (bull/base/bear
  assumption sets) → `ScenarioEngineResult`. `400` on invalid input.
- **`POST /scenarios/reverse-valuation`** — body: `ReverseValuationInput`
  (target price + DCF/SOTP structure to solve for an implied assumption,
  e.g. implied growth rate) → `ReverseValuationResult`. `400` on invalid
  input (e.g. unsolvable/out-of-range target).
- **`POST /scenarios/reverse-valuation/explain`** — body: the raw result
  dict from the above → AI narrative explanation (never recomputes the
  number; abstains and returns accordingly if the AI text contains an
  untraceable number). `503` on AI adapter failure.
- **`POST /scenarios/value-unlock/propose`** — query/body:
  `company_id, sotp_breakdown, conglomerate_discount_pct?` → AI proposes
  candidate structural actions (spin-off, IPO, asset sale, buyback, debt
  reduction, special dividend); each becomes a `PENDING`
  `AssumptionDecision` with only a categorical `action_type` code, never a
  dollar figure. Response: `{abstained, reason?, proposals: [{decision_id,
  action_type, target_segment, rationale}]}`. `503` on AI failure.
- **`POST /scenarios/value-unlock/compute/{action_type}`** — query:
  `decision_id`; body: `params` (explicit numeric inputs for that action
  type, e.g. multiples/proceeds/amounts). Gated on `decision_id` being
  `APPROVED`/`OVERRIDDEN`. Returns the deterministic dollar-uplift result
  from `backend/valuation/value_unlock.py`. `404` unknown decision; `400`
  decision not yet approved, or invalid/mismatched `params` for the action
  type.
- **`POST /scenarios/risk-dashboard`** — query params: `coverage_pct,
  methodologies_used, sensitivity_spread_pct, forecast_classification,
  beta?, volatility_pct?, strategic_execution_context?` → `RiskDashboardResult`,
  combining deterministic data-confidence inputs with an AI-scored
  Strategic/Execution risk pair. `422` if the AI abstains or returns an
  unusable score; `400` on invalid deterministic inputs.

---

## Memo — `backend/api/routers/memo.py` (prefix `/memo`)

### `GET /memo/{ticker}` → `MemoResponse`
Assembles a real investment memo from persisted data: the `Company` row
(resolved/created via the same ticker→CIK→`get_or_create_company` path as
`/companies/{ticker}`), the latest persisted `FinancialFact`s, all
`Segment`/`SegmentFinancialFact` rows, and the most recent `ValuationRun`
per method (`DCF`/`COMPS`/`SOTP`). That structured snapshot is passed to
`backend/ai/tasks/memo_generator.generate_memo_section` once per section
(`Executive Summary`, `Company Overview`, `Segment Analysis`, `Valuation
Methodology`, `SOTP Breakdown`, `Risks & Uncertainties`); every number the
AI writes must trace back to the snapshot (`ai/validation.py`) or that
section abstains rather than fabricating.
- Response: `{ticker, company_id, company_name, insufficient_data, data_snapshot, sections: [{section, abstained, memo_text?, reason?, validation_ok?}]}`
- Errors: `404` — unknown ticker, **or** no completed `ValuationRun` exists
  yet for the company (insufficient data to generate a memo; never a
  fabricated one). `503` SEC rate-limited/unavailable while resolving the
  ticker.
