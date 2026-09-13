// Mirrors docs/definition-of-done.md — transcribed, not embellished.
// Update this file whenever that doc changes.

export type DoDStatus = "DONE" | "PARTIAL" | "NOT DONE";

export interface DoDItem {
  number: number;
  item: string;
  status: DoDStatus;
  note: string;
}

export const DEFINITION_OF_DONE_ITEMS: DoDItem[] = [
  { number: 1, item: "Ticker entry", status: "DONE", note: "GET /companies/{ticker} resolves any ticker via SEC's ticker map." },
  { number: 2, item: "Company ID resolution", status: "DONE", note: "GET /companies/{ticker} now returns company_id directly, and GET /companies/{ticker}/id is a dedicated ticker→company_id endpoint (both persist/look up the row via get_or_create_company). Frontend pages (Research History, Value Unlock, AI Analyst) resolve company_id automatically from the ticker on load; the manual-entry UI/banner was removed. /valuation/{ticker}/run and /{ticker}/runs remain 501 stubs (ticker-driven auto data assembly is a separate, still-deferred item — use the resolved company_id with the governed POST /valuation/run flow)." },
  { number: 3, item: "SEC data ingestion", status: "DONE", note: "SECClient (cache + rate limit + retry) pulls submissions, companyfacts, filings." },
  { number: 4, item: "Normalization", status: "DONE", note: "normalizer.py maps raw XBRL to canonical concepts, keeps MISSING rows rather than dropping them." },
  { number: 5, item: "Market data", status: "DONE", note: "market_data_client.py (yfinance), explicitly labeled latest-available/not-real-time." },
  { number: 6, item: "Segments", status: "DONE", note: "Inline-XBRL instance-document parsing (see architecture.md deviation note); returns a note for single-segment/non-standard filers rather than failing silently." },
  { number: 7, item: "Coverage scoring", status: "DONE", note: "segment_coverage.py, per-segment/per-metric %, surfaced in API and Risk Dashboard's Data Risk category." },
  { number: 8, item: "Missing-data handling", status: "DONE", note: "DataStatus.MISSING facts are explicit, first-class rows (never silently omitted or zero-filled)." },
  { number: 9, item: "Business classification", status: "DONE", note: "business_classifier.py, SIC→category (verified live: JPM → Bank, AAPL → non-bank), drives eligible methodologies." },
  { number: 10, item: "Methodology recommendation", status: "DONE", note: "AI task (backend/ai/tasks/methodology_recommendation.py) proposes eligible methodologies; constrained to the classifier's eligible set." },
  { number: 11, item: "Human approval on methodology/peers/assumptions", status: "DONE", note: "AssumptionDecision propose/decide workflow (governance/approval.py) covers all three; POST /valuation/run hard-400s if any referenced decision isn't APPROVED/OVERRIDDEN." },
  { number: 12, item: "DCF", status: "DONE", note: "valuation/dcf.py, unit-tested (test_valuation_dcf.py) and exercised end-to-end against real AAPL data in test_integration_pipeline.py." },
  { number: 13, item: "Comps", status: "DONE", note: "valuation/comps.py, unit-tested (test_valuation_comps.py); router POST /valuation/comps." },
  { number: 14, item: "SOTP", status: "DONE", note: "valuation/sotp.py, unit-tested and exercised end-to-end in the new integration test." },
  { number: 15, item: "Net debt bridge", status: "DONE", note: "Explicit fields on DcfInput/SotpInput (net_debt/total_debt, cash_and_equivalents, investments); bridge arithmetic asserted in test_integration_pipeline.py." },
  { number: 16, item: "Minority interest", status: "DONE", note: "minority_interest field flows through both DCF and SOTP equity bridges." },
  { number: 17, item: "Corporate overhead", status: "DONE", note: "SotpInput.corporate_overhead_treatment (direct_deduction / capitalized_overhead), both paths returned in SotpResult." },
  { number: 18, item: "Scenarios", status: "DONE", note: "valuation/scenarios.py + POST /scenarios/run (bull/base/bear)." },
  { number: 19, item: "Sensitivity", status: "DONE", note: "valuation/sensitivity.py + POST /valuation/sensitivity/dcf (2D grid over any two DCF fields)." },
  { number: 20, item: "Reverse valuation", status: "DONE", note: "valuation/reverse_valuation.py + POST /scenarios/reverse-valuation, classifies implied assumptions CONSERVATIVE/REASONABLE/AGGRESSIVE/EXTREME." },
  { number: 21, item: "Value-unlock", status: "DONE", note: "valuation/value_unlock.py (spin-off, IPO, asset sale, buyback, debt reduction, special dividend), gated on human-approved proposal before any dollar figure is computed." },
  { number: 22, item: "AI devil's advocate", status: "DONE", note: "ai/tasks/devils_advocate.py, unit-tested (test_ai_devils_advocate.py)." },
  { number: 23, item: "Data confidence (distinct metric)", status: "DONE", note: "Risk Dashboard's Data Risk category is scored purely from segment/fact coverage %, kept structurally separate from the other five categories." },
  { number: 24, item: "Valuation confidence (distinct metric)", status: "DONE", note: "Risk Dashboard's Model Risk + Forecast Risk categories (methodology count, sensitivity spread, growth-assumption classification) are the valuation-confidence signal, separate from Data Risk — confirmed the spec's two-metric split was actually kept apart in risk_dashboard.py, not collapsed into one score." },
  { number: 25, item: "Audit trail", status: "DONE", note: "AuditLogEntry + GET /valuation/company/{id}/audit-trail and /runs/{id}/audit-trail." },
  { number: 26, item: "Version history", status: "DONE", note: "ValuationRun versioning, GET /valuation/company/{id}/runs, .../runs/diff." },
  { number: 27, item: "Investment memo", status: "DONE", note: "GET /memo/{ticker} assembles real persisted data (Company, latest FinancialFacts, Segments, most recent ValuationRun per method) and calls ai/tasks/memo_generator.py section-by-section (Executive Summary, Company Overview, Segment Analysis, Valuation Methodology, SOTP Breakdown, Risks & Uncertainties). Returns a clear 404 \"insufficient data\" error when no completed valuation run exists for the company, rather than fabricating a memo. Frontend memo page renders the real structured sections (or the abstain reason per section). Covered by backend/tests/test_memo_router.py (happy path + insufficient-data path)." },
  { number: 28, item: "Source provenance", status: "DONE", note: "Every FinancialFact/SegmentFinancialFact carries source, source_url, accession_number, xbrl_tag." },
  { number: 29, item: "AI cannot overwrite facts", status: "DONE", note: "Enforced structurally: AI tasks only ever produce AssumptionDecision proposals or narrative text; no AI code path writes to FinancialFact/SegmentFinancialFact/MarketDataSnapshot. test_ai_import_boundary.py asserts backend/ai/ cannot import backend/valuation/ internals that compute or persist numbers." },
  { number: 30, item: "AI cannot calculate", status: "DONE", note: "Same boundary test + code review: all dollar-figure computation lives in backend/valuation/, which has zero imports from backend/ai/." },
  { number: 31, item: "Graceful missing-data handling", status: "DONE", note: "DataStatus.MISSING throughout; segment extractor returns a note instead of raising when data isn't cleanly resolvable; JPM integration test explicitly demonstrates and asserts this (missing capex/operating_income/other bank-inapplicable concepts, revenue/net_income still resolvable)." },
  { number: 32, item: "Graceful API-failure handling", status: "DONE", note: "Typed exception hierarchy (SECNotFoundError/SECRateLimitError/SECUnavailableError, MarketDataNotFoundError/MarketDataUnavailableError) mapped to 404/503 everywhere; frontend has ErrorState + loading skeletons on every fetch-driven page, verified against a backend-down scenario in Phase 9." },
  { number: 33, item: "Tests pass", status: "DONE", note: "164 unit/router/integration tests passing as of this final gap-closure pass (added test_memo_router.py and test_company_id_resolution.py for the two items below). No drift found between phases requiring fixes." },
  { number: 34, item: "README complete", status: "DONE", note: "Updated this phase: accurate phase status, Node/npm availability note, full env-var reference, Windows PATH workaround, SQLite-for-tests clarification." },
  { number: 35, item: "Architecture documented", status: "DONE", note: "docs/architecture.md rewritten this phase to describe the system as built, including the inline-XBRL segment-parsing deviation and the memo/ticker-resolution gaps." },
  { number: 36, item: "Env setup documented", status: "DONE", note: "README \"Environment variables\" section covers every .env.example key and which are required for which subsystem (SEC vs DB vs AI)." },
  { number: 37, item: "No secrets committed", status: "DONE", note: "See Secrets Check in docs/definition-of-done.md — .env is gitignored, no hardcoded API keys/tokens found in any .py/.ts/.tsx source file." },
  { number: 38, item: "No fabricated numbers presented as real", status: "DONE", note: "DataStatus.MISSING/CONFLICTING are first-class, never silently defaulted; AI narrative validation (ai/validation.py) rejects/abstains on AI text containing numbers not traceable to a real input; value-unlock proposals store only categorical action codes, never AI-invented dollar amounts." },
  { number: 39, item: "Authentication", status: "NOT DONE", note: "Never in scope of the 10 phases as executed; explicitly deferred to a future hardening/deployment phase (see docs/architecture.md \"Deviations\")." },
  { number: 40, item: "Deployment (CI, containers, Vercel/Supabase config)", status: "NOT DONE", note: "Same as above — no CI pipeline, Dockerfile, or Vercel config exists in this repo. Local dev + tests are fully documented and working; production deployment is out of scope as executed." },
];

export const DEFINITION_OF_DONE_SUMMARY =
  "Of 40 checklist items: 38 DONE, 2 NOT DONE (authentication, deployment/CI). Both remaining NOT DONE items were explicitly descoped in the original phase plan (auth/deployment were slated for an 11th \"hardening\" phase never executed). The two previously PARTIAL/NOT DONE items from the Phase 10 audit — ticker→company_id resolution (#2) and investment memo assembly (#27) — were closed in a final gap-closure pass.";
