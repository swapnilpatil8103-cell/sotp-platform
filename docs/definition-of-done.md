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
| 11 | Human approval on methodology/peers/assumptions | DONE | `AssumptionDecision` propose/decide workflow (`governance/approval.py`) covers all three; `POST /valuation/run` hard-`400`s if any referenced decision isn't `APPROVED`/`OVERRIDDEN`, recomputes the outputs server-side (a client cannot supply them), and `400`s if an approved value differs from the input field it governs (`test_governed_run_integrity.py`). |
| 12 | DCF | DONE | `valuation/dcf.py`, unit-tested (`test_valuation_dcf.py`) and exercised end-to-end against real AAPL data in `test_integration_pipeline.py`. |
| 13 | Comps | DONE | `valuation/comps.py`, unit-tested (`test_valuation_comps.py`); router `POST /valuation/comps`. |
| 14 | SOTP | DONE | `valuation/sotp.py`, unit-tested and exercised end-to-end in the new integration test. |
| 15 | Net debt bridge | DONE | Explicit fields on `DcfInput`/`SotpInput` (`total_debt`, `cash_and_equivalents`, `investments`); bridge arithmetic asserted in `test_integration_pipeline.py` and a dedicated regression test in `test_valuation_dcf.py`. |
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
| 33 | Tests pass | DONE | 761 tests passing (749 unit/router + 12 integration that call real SEC EDGAR; `pytest backend`), up from 164 at the original gap-closure pass (added `test_memo_router.py` and `test_company_id_resolution.py` for the two items below). No drift found between phases requiring fixes. |
| 34 | README complete | DONE | Updated this phase: accurate phase status, Node/npm availability note, full env-var reference, Windows PATH workaround, SQLite-for-tests clarification. |
| 35 | Architecture documented | DONE | `docs/architecture.md` rewritten this phase to describe the system as built, including the inline-XBRL segment-parsing deviation and the memo/ticker-resolution gaps. |
| 36 | Env setup documented | DONE | README "Environment variables" section covers every `.env.example` key and which are required for which subsystem (SEC vs DB vs AI). |
| 37 | No secrets committed | DONE | See Secrets Check below — `.env` is gitignored, no hardcoded API keys/tokens found in any `.py`/`.ts`/`.tsx` source file. |
| 38 | No fabricated numbers presented as real | DONE | `DataStatus.MISSING`/`CONFLICTING` are first-class, never silently defaulted; AI narrative validation (`ai/validation.py`) rejects/abstains on AI text containing numbers not traceable to a real input; value-unlock proposals store only categorical action codes, never AI-invented dollar amounts. |
| 39 | Authentication | PARTIAL | Built: HS256 JWT Bearer auth (`AUTH_JWT_SECRET`, `backend/api/deps.py`) on every router except `/health` and `POST /auth/login`, applied at include_router time so new routes are protected by default (test_auth.py enumerates the OpenAPI paths against an explicit public allowlist); the user row is loaded from `app_user` on EVERY request with nothing cached and roles taken from the row, not the token, so a deactivated or deleted user's still-valid token gets 401 and a removed role gives 403 on the next request; every token carries a version (`tv`) that must match `app_user.token_version`, which `POST /auth/change-password` (current password required, 12-character minimum, reuse of the current password rejected, returns a fresh token) and the admin-only `POST /auth/revoke-sessions` (also `scripts/create_user.py --revoke-sessions`, and `--reset-password`) bump, so all earlier tokens stop working at once; password login via `POST /auth/login` (`backend/api/routers/auth.py`): Argon2id hashes in `app_user`, one generic 401 for unknown user / wrong password / disabled account, tokens valid 60 minutes (`AUTH_TOKEN_MINUTES`), users created with `scripts/create_user.py`; a DATABASE-BACKED login throttle (`login_attempt` table, migration `f6a7b8c9d0e1`, `backend/services/login_throttle.py`) shared by all workers and surviving restarts, with three counters (per email+ip, per ip, per email) and exponential back-off with caps, no recording of attempts made while locked, a bypass of the email-wide lock for an ip that has logged in as that user, and the table storing only an HMAC of the email; user administration without public sign-up: an `admin` role (separate from `analyst`) and admin-only `GET/POST /admin/users`, `PATCH /admin/users/{id}` (roles, deactivate/reactivate, revoke sessions) and `GET /admin/users/{id}/audit`, every change audited as `AppUser` events with the acting admin as actor in the same transaction (a failed audit write rolls the change back), deactivation also revoking sessions, and a last-active-admin guard (409) that stops the only admin removing their own admin role or deactivating themselves, including in combined edits; `scripts/create_user.py --bootstrap-admin` creates the first admin and refuses once one exists; request bodies over 1 MiB get 413 from both the API (ASGI middleware that counts streamed bytes, so a missing or false Content-Length does not help) and the Next.js proxy; CORS uses explicit methods and headers and the server refuses to start if `ALLOWED_ORIGINS` contains a wildcard; the web app signs in through Next.js route handlers that keep the token in an httpOnly, SameSite=Strict cookie (Secure in production builds) and proxy API calls through `/api/backend/*`, so no credential is in the browser bundle, with `proxy.ts` redirecting signed-out page loads to `/login`, an Origin check on state-changing requests and an `/account` page for changing the password; an `analyst` role gates `POST /valuation/assumptions/{id}/decide` and `POST /valuation/run`; identity (`decided_by`, `ValuationRun.created_by`, the `human:<sub>` audit actor) comes from the authenticated user and request bodies cannot name one; separation of duties: the user who recorded a proposal (`AssumptionDecision.proposed_by`, mandatory in `approval.propose()` and set by both propose routes) cannot decide it (403), and proposals with no recorded proposer (`unknown-legacy`, migration `e5f6a7b8c9d0`) cannot be approved or edited (400, re-propose) though they can be rejected. Covered by test_auth.py, test_login_and_separation.py, test_login_throttle.py, test_token_version_and_password.py, test_legacy_proposer.py, test_body_limit.py, test_cors.py and test_user_admin.py (the new suites also pass on Postgres, and migrations run up/down/up there); the web flow (413s, change password, other session signed out, throttle with forwarded addresses, real user not locked out) was exercised against a production build and Postgres. Not built: a forgot-password / email-reset flow, an admin web page (the admin API exists), MFA/SSO; `/docs` and `/openapi.json` are open; `POST /valuation/assumptions/propose` is open to any authenticated user. Caveat: the throttle's client address comes from `X-Forwarded-For` (`AUTH_TRUST_FORWARDED_FOR=true`, needed behind the Next.js server); `next start` exposed directly passes a client-supplied header through unchanged, so production needs a reverse proxy that appends the real peer address, otherwise a client can choose its own throttle address (the email-wide limit and Argon2id remain). The `/account` page and the header link were built and compile but were not exercised in a browser; the HTTP calls behind them were. |
| 40 | Deployment (CI, containers, Vercel/Supabase config) | PARTIAL | Built: `.github/workflows/ci.yml` runs on every push and PR: backend `pytest -m "not integration"` on Python 3.13 (dummy `SEC_USER_AGENT`), web `npm ci` + `npx tsc --noEmit` + `npm run build` on Node 20, and `npm audit --omit=dev --audit-level=high`; `backend/Dockerfile` (applies Alembic migrations, then serves uvicorn as a non-root user, with a `/health` check), `apps/web/Dockerfile` (multi-stage; `NEXT_PUBLIC_*` are build args because they are inlined into the bundle), and `docker-compose.yml` (Postgres 16 + backend + web; `AUTH_JWT_SECRET` required). Verified: the CI workflow has run on GitHub for every push to main since it was added, and all three jobs (backend on Python 3.13, web typecheck + build on Node 20, npm audit) passed. The compose environment was reproduced without Docker (Docker is not installed on the dev machine): a backend directory holding only backend/ and scripts/, and a web directory holding only production dependencies, the built .next and next.config.js, were run against a fresh Postgres 16 database with the compose environment variables. The container CMD (alembic upgrade head, then uvicorn) applied every migration from scratch, /health returned 200, /login served, scripts/create_user.py created a user, login through the web proxy set the session cookie over plain http (SESSION_COOKIE_SECURE=false), and GET /companies/AAPL returned data through the proxy. API_BASE_URL is read at runtime, not build time. All migrations were run up, down to base, and up again on Postgres (this found and fixed a downgrade that left four enum types behind). The non-integration suite passes on Postgres (TEST_DATABASE_URL), and the 12 integration tests pass with a real SEC user agent. Not verified: the Dockerfiles and docker-compose.yml themselves have never been built or run (no Docker available), and no pull_request-triggered run has happened. Not built: continuous deployment, image publishing, Vercel/Supabase config, and secrets management beyond `.env`. |

## Secrets check (performed this phase)

- `.gitignore` includes `.env`, `.env.local`, `*.env`, `node_modules/`,
  `venv/`, `.venv/` — confirmed present.
- `grep -rE` for Gemini-key-shaped strings (`AQ\.[A-Za-z0-9_-]{20,}`),
  Google API key shapes (`AIza...`), and generic `sk-...` token shapes
  across every `.py`/`.ts`/`.tsx`/`.js` file under `backend/` and `apps/`
  (excluding `node_modules/`) returned **zero matches**. The only real
  Gemini key on this machine lives in the gitignored `.env` file, not in
  source.

## Post-Phase-10 additions

Three further improvements were added after the Phase 10 audit above (still
subject to the same "AI suggests, human decides" governance principle):

- **Automated per-segment valuation** (`backend/valuation/segment_valuation.py`,
  `POST /valuation/{ticker}/segments/auto-value`) — composes the existing
  `run_dcf`/multiple-application arithmetic at segment level to produce
  SUGGESTED segment EVs that pre-fill (never bypass) the SOTP form.
- **Peer-informed beta** (`backend/valuation/beta_analysis.py`,
  `POST /valuation/wacc/peer-beta`) — Hamada unlever/relever across a peer
  set, skipping any peer missing real D/E or tax-rate data. SUGGESTED only;
  the DCF page's toggle pre-fills the Beta field without auto-submitting it.
- **Dual DCF terminal value** (`backend/valuation/dcf.py`) — an optional
  `exit_multiple` input on `DcfInput` adds an independent Exit Multiple
  terminal value/EV/equity/price alongside (never replacing) the existing
  Gordon Growth result; omitted by default, so existing callers see
  unchanged behavior.

Unit tests: `backend/tests/test_segment_valuation.py`,
`backend/tests/test_beta_analysis.py`,
`backend/tests/test_valuation_dcf_dual_tv.py`. Full suite: 233 → 255 passing
(22 new tests), no regressions.

## Summary

Of 40 checklist items: **38 DONE**, **2 PARTIAL** (authentication, #39 — login, cookie sessions and separation of duties are built, but no forgot-password flow, admin web page or SSO; deployment, #40 — CI and container files exist but are unverified on GitHub/Docker and there is no continuous deployment), **0 NOT DONE**. Both were explicitly descoped in the original phase plan (auth/deployment were slated for an 11th "hardening" phase never executed) and were started afterwards. The two previously PARTIAL/NOT DONE
items from the Phase 10 audit — ticker→company_id resolution (#2) and
investment memo assembly (#27) — were closed in a final gap-closure pass:
`GET /companies/{ticker}/id` (+ `company_id` on `GET /companies/{ticker}`)
and a real `GET /memo/{ticker}` backed by `ai/tasks/memo_generator.py` over
persisted Company/FinancialFact/Segment/ValuationRun data, both covered by
new tests, with the frontend updated to consume them (auto-resolved
company_id on Research History/Value Unlock/AI Analyst; real memo sections
rendered on the Memo page).
