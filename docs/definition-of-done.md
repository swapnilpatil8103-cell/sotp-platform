# Definition of Done — final audit (Phase 10)

Honest, item-by-item status against the master spec's Definition of Done.
Status legend: **DONE** / **PARTIAL** / **NOT DONE**.

| # | Item | Status | Note |
|---|------|--------|------|
| 1 | Ticker entry | DONE | `GET /companies/{ticker}` resolves any ticker via SEC's ticker map. |
| 2 | Company ID resolution | DONE | `GET /companies/{ticker}` now returns `company_id` directly, and `GET /companies/{ticker}/id` is a dedicated ticker→`company_id` endpoint (both persist/look up the row via `get_or_create_company`). Frontend pages (Research History, Value Unlock, AI Analyst) resolve `company_id` automatically from the ticker on load; the manual-entry UI/banner was removed. `/valuation/{ticker}/run` and `/{ticker}/runs` remain `501` stubs (ticker-driven auto data assembly is a separate, still-deferred item — use the resolved `company_id` with the governed `POST /valuation/run` flow). |
| 3 | SEC data ingestion | DONE | `SECClient` (cache + rate limit + retry) pulls submissions, companyfacts, filings. |
| 4 | Normalization | DONE | `normalizer.py` maps raw XBRL to canonical concepts, keeps `MISSING` rows rather than dropping them. |
| 5 | Market data | DONE | `market_data_client.py` (yfinance), explicitly labeled latest-available/not-real-time. |
| 6 | Segments | DONE | Inline-XBRL instance-document parsing (see architecture.md deviation note); returns a `note` for single-segment/non-standard filers rather than failing silently. |
| 7 | Coverage scoring | DONE | `segment_coverage.py`, per-segment/per-metric %, surfaced in API and Risk Dashboard's Data Risk category. |
| 8 | Missing-data handling | DONE | `DataStatus.MISSING` facts are explicit, first-class rows (never silently omitted or zero-filled). |
| 9 | Business classification | DONE | `business_classifier.py`, SIC→category (verified live: JPM → `Bank`, AAPL → non-bank), drives eligible methodologies. |
| 10 | Methodology recommendation | DONE | AI task (`backend/ai/tasks/methodology_recommendation.py`) proposes eligible methodologies; constrained to the classifier's eligible set. |
| 11 | Human approval on methodology/peers/assumptions | DONE | `AssumptionDecision` propose/decide workflow (`governance/approval.py`) covers all three; `POST /valuation/run` hard-`400`s if any referenced decision isn't `APPROVED`/`OVERRIDDEN`. |
| 12 | DCF | DONE | `valuation/dcf.py`, unit-tested (`test_valuation_dcf.py`) and exercised end-to-end against real AAPL data in `test_integration_pipeline.py`. |
| 13 | Comps | DONE | `valuation/comps.py`, unit-tested (`test_valuation_comps.py`); router `POST /valuation/comps`. |
| 14 | SOTP | DONE | `valuation/sotp.py`, unit-tested and exercised end-to-end in the new integration test. |
| 15 | Net debt bridge | DONE | Explicit fields on `DcfInput`/`SotpInput` (`net_debt`/`total_debt`, `cash_and_equivalents`, `investments`); bridge arithmetic asserted in `test_integration_pipeline.py`. |
| 16 | Minority interest | DONE | `minority_interest` field flows through both DCF and SOTP equity bridges. |
| 17 | Corporate overhead | DONE | `SotpInput.corporate_overhead_treatment` (`direct_deduction` / `capitalized_overhead`), both paths returned in `SotpResult`. |
| 18 | Scenarios | DONE | `valuation/scenarios.py` + `POST /scenarios/run` (bull/base/bear). |
| 19 | Sensitivity | DONE | `valuation/sensitivity.py` + `POST /valuation/sensitivity/dcf` (2D grid over any two DCF fields). |
| 20 | Reverse valuation | DONE | `valuation/reverse_valuation.py` + `POST /scenarios/reverse-valuation`, classifies implied assumptions CONSERVATIVE/REASONABLE/AGGRESSIVE/EXTREME. |
| 21 | Value-unlock | DONE | `valuation/value_unlock.py` (spin-off, IPO, asset sale, buyback, debt reduction, special dividend), gated on human-approved proposal before any dollar figure is computed. |
| 22 | AI devil's advocate | DONE | `ai/tasks/devils_advocate.py`, unit-tested (`test_ai_devils_advocate.py`). |
| 23 | Data confidence (distinct metric) | DONE | Risk Dashboard's **Data Risk** category is scored purely from segment/fact coverage %, kept structurally separate from the other five categories. |
| 24 | Valuation confidence (distinct metric) | DONE | Risk Dashboard's **Model Risk** + **Forecast Risk** categories (methodology count, sensitivity spread, growth-assumption classification) are the valuation-confidence signal, separate from Data Risk — confirmed the spec's two-metric split was actually kept apart in `risk_dashboard.py`, not collapsed into one score. |
| 25 | Audit trail | DONE | `AuditLogEntry` + `GET /valuation/company/{id}/audit-trail` and `/runs/{id}/audit-trail`. |
| 26 | Version history | DONE | `ValuationRun` versioning, `GET /valuation/company/{id}/runs`, `.../runs/diff`. |
| 27 | Investment memo | DONE | `GET /memo/{ticker}` assembles real persisted data (Company, latest FinancialFacts, Segments, most recent ValuationRun per method) and calls `ai/tasks/memo_generator.py` section-by-section (Executive Summary, Company Overview, Segment Analysis, Valuation Methodology, SOTP Breakdown, Risks & Uncertainties). Returns a clear `404` "insufficient data" error when no completed valuation run exists for the company, rather than fabricating a memo. Frontend memo page renders the real structured sections (or the abstain reason per section). Covered by `backend/tests/test_memo_router.py` (happy path + insufficient-data path). |
| 28 | Source provenance | DONE | Every `FinancialFact`/`SegmentFinancialFact` carries `source`, `source_url`, `accession_number`, `xbrl_tag`. |
| 29 | AI cannot overwrite facts | DONE | Enforced structurally: AI tasks only ever produce `AssumptionDecision` proposals or narrative text; no AI code path writes to `FinancialFact`/`SegmentFinancialFact`/`MarketDataSnapshot`. `test_ai_import_boundary.py` asserts `backend/ai/` cannot import `backend/valuation/` internals that compute or persist numbers. |
| 30 | AI cannot calculate | DONE | Same boundary test + code review: all dollar-figure computation lives in `backend/valuation/`, which has zero imports from `backend/ai/`. |
| 31 | Graceful missing-data handling | DONE | `DataStatus.MISSING` throughout; segment extractor returns a `note` instead of raising when data isn't cleanly resolvable; JPM integration test explicitly demonstrates and asserts this (missing `capex`/`operating_income`/other bank-inapplicable concepts, revenue/net_income still resolvable). |
| 32 | Graceful API-failure handling | DONE | Typed exception hierarchy (`SECNotFoundError`/`SECRateLimitError`/`SECUnavailableError`, `MarketDataNotFoundError`/`MarketDataUnavailableError`) mapped to `404`/`503` everywhere; frontend has `ErrorState` + loading skeletons on every fetch-driven page, verified against a backend-down scenario in Phase 9. |
| 33 | Tests pass | DONE | 164 unit/router/integration tests passing as of this final gap-closure pass (added `test_memo_router.py` and `test_company_id_resolution.py` for the two items below). No drift found between phases requiring fixes. |
| 34 | README complete | DONE | Updated this phase: accurate phase status, Node/npm availability note, full env-var reference, Windows PATH workaround, SQLite-for-tests clarification. |
| 35 | Architecture documented | DONE | `docs/architecture.md` rewritten this phase to describe the system as built, including the inline-XBRL segment-parsing deviation and the memo/ticker-resolution gaps. |
| 36 | Env setup documented | DONE | README "Environment variables" section covers every `.env.example` key and which are required for which subsystem (SEC vs DB vs AI). |
| 37 | No secrets committed | DONE | See Secrets Check below — `.env` is gitignored, no hardcoded API keys/tokens found in any `.py`/`.ts`/`.tsx` source file. |
| 38 | No fabricated numbers presented as real | DONE | `DataStatus.MISSING`/`CONFLICTING` are first-class, never silently defaulted; AI narrative validation (`ai/validation.py`) rejects/abstains on AI text containing numbers not traceable to a real input; value-unlock proposals store only categorical action codes, never AI-invented dollar amounts. |
| 39 | Authentication | NOT DONE | Never in scope of the 10 phases as executed; explicitly deferred to a future hardening/deployment phase (see `docs/architecture.md` "Deviations"). |
| 40 | Deployment (CI, containers, Vercel/Supabase config) | NOT DONE | Same as above — no CI pipeline, Dockerfile, or Vercel config exists in this repo. Local dev + tests are fully documented and working; production deployment is out of scope as executed. |

## Secrets check (performed this phase)

- `.gitignore` includes `.env`, `.env.local`, `*.env`, `node_modules/`,
  `venv/`, `.venv/` — confirmed present.
- `grep -rE` for Gemini-key-shaped strings (`AQ\.[A-Za-z0-9_-]{20,}`),
  Google API key shapes (`AIza...`), and generic `sk-...` token shapes
  across every `.py`/`.ts`/`.tsx`/`.js` file under `backend/` and `apps/`
  (excluding `node_modules/`) returned **zero matches**. The only real
  Gemini key on this machine lives in the gitignored `.env` file, not in
  source.

## Summary

Of 40 checklist items: **38 DONE**, **2 NOT DONE** (authentication,
deployment/CI). Both remaining NOT DONE items were explicitly descoped in
the original phase plan (auth/deployment were slated for an 11th
"hardening" phase never executed). The two previously PARTIAL/NOT DONE
items from the Phase 10 audit — ticker→company_id resolution (#2) and
investment memo assembly (#27) — were closed in a final gap-closure pass:
`GET /companies/{ticker}/id` (+ `company_id` on `GET /companies/{ticker}`)
and a real `GET /memo/{ticker}` backed by `ai/tasks/memo_generator.py` over
persisted Company/FinancialFact/Segment/ValuationRun data, both covered by
new tests, with the frontend updated to consume them (auto-resolved
company_id on Research History/Value Unlock/AI Analyst; real memo sections
rendered on the Memo page).
