# API Documentation

FastAPI backend, mounted in `backend/api/main.py`. All routers below are
`app.include_router(...)`'d there. JSON in/out throughout; request/response
bodies are Pydantic models from `backend/schemas/` (governance/valuation) or
per-router response models (companies/segments). Errors are FastAPI's
default `{"detail": "..."}` shape unless noted.

## Base URL

`http://localhost:8000` in local development. The web app does not call it
from the browser; its Next.js server proxies requests (`API_BASE_URL`, see top-level `README.md`).

## Conventions

- Every financial figure in a `FinancialFactRead`/`SegmentFactRead` carries
  `data_status` (`REPORTED` / `DERIVED` / `ESTIMATED` / `MISSING` /
  `CONFLICTING`), `source`, `source_url`, `accession_number`, and `xbrl_tag`
  for provenance — see `docs/data-model.md`.
- `501 Not Implemented` is used deliberately for endpoints that are
  documented-but-deferred (never a silent stub returning fake data).
- `503` = upstream (SEC EDGAR / market data provider) unavailable or rate
  limited; retry later. `404` = ticker/resource not found. `400` = bad
  input that is only discovered when the engine runs, or an unknown enum-like
  value (e.g. an unknown `method`). `422` = the request failed schema
  validation (FastAPI's standard `{"detail": [{loc, msg, type, ...}]}` shape,
  with per-field detail) — this is also what an out-of-range value on a
  constrained field gets — and is likewise used when an AI task abstained.
  Valuation endpoints never answer `500` for bad input. Non-finite numbers
  (`NaN`, `Infinity`) are not valid inputs; where one fails validation it is
  echoed back in the `422` as the string `"nan"` / `"inf"`, because JSON
  cannot carry the number itself.
- Governance/valuation-run endpoints (`/valuation/assumptions/*`,
  `/valuation/run`, `/valuation/company/{id}/*`) require a `Session`
  (SQLite in tests, Postgres/Supabase via `DATABASE_URL` in real use).
- The pure-compute valuation endpoints (`/valuation/sotp`, `/dcf`, `/comps`,
  `/sensitivity/dcf`, `/scenarios/*`) do **not** touch the DB — callers
  assemble inputs themselves (from `/companies/{ticker}/facts`,
  `/companies/{ticker}/segments`, `/companies/{ticker}/market-data`) and get
  back a computed result with no persistence.

## Authentication

Every endpoint **except `GET /health` and `POST /auth/login`** requires
`Authorization: Bearer <JWT>`. The auth dependency
(`backend/api/deps.py::get_current_user`) is attached to each router in
`main.py` at `include_router` time, so every route in those routers — including
routes added later — is protected by default.

**Token.** HS256, signed with `AUTH_JWT_SECRET` (at least 32 characters).
Required claims: `sub` (non-empty string, at most 256 characters — it is
written into `decided_by` / `created_by`), `exp`, and `tv` (an integer, see
"Token version" below). Optional: `roles`, a list
of strings. The algorithm is pinned server-side; unsigned (`alg: none`) and
differently-signed tokens are rejected. Users get tokens from `POST /auth/login`
(below); `python scripts/mint_dev_token.py --sub <email> [--hours N]`
mints one for scripts, for an existing active user only.

### `POST /auth/login`

Open. Body: `{"email": "...", "password": "..."}` (extra fields are a `422`; email
at most 254 characters, password 1-1024). Success (`200`):
`{"access_token", "token_type": "bearer", "expires_in": 3600, "subject", "roles"}`.
The token's `sub` is the user's lower-cased email and `roles` echo the user
row at login time (informational: see below). Lifetime is `AUTH_TOKEN_MINUTES` (default 60, clamped to 1..1440).

| Status | When |
|---|---|
| `401` | Unknown email, wrong password or disabled account; identical body `{"detail": "Invalid email or password."}`. |
| `429` | Locked out by the throttle (below); `Retry-After` gives the seconds left. A locked attempt is rejected before any password check and is not recorded. |
| `503` | `AUTH_JWT_SECRET` is unset or too short. |

Users are created with `scripts/create_user.py` (there is no sign-up endpoint);
passwords are stored as Argon2id hashes in `app_user`.

**Throttle.** Failed attempts are rows in `login_attempt` (`email_hash` = HMAC-SHA256 of
the lower-cased email keyed by `AUTH_JWT_SECRET`, `ip`, `ts`, `success`), so the limit is
shared by all workers and survives restarts. Over a rolling 15 minutes, a caller is
locked for `base * 2^(failures - threshold)` seconds after its latest failure, up to a
cap:

| Scope | Key | Threshold | Base | Cap |
|---|---|---|---|---|
| pair | (email, ip) | 5 | 60 s | 15 min |
| ip | ip across all emails | 20 | 60 s | 15 min |
| email | email across all ips | 10 | 60 s | 5 min |

The longest applicable lock is reported. Attempts made while locked are neither
checked nor recorded, so they cannot extend a lock. A successful login clears that
(email, ip) pair's failures, and the email-wide lock does not apply to an ip with a
successful login for that email in the last 30 days. Rows older than the window
(failures) or 30 days (successes) are deleted as new ones are written. Unknown
emails are recorded too, so the table does not reveal which accounts exist. The ip
is the socket peer. With `AUTH_TRUST_FORWARDED_FOR=true` the `X-Forwarded-For` header is
believed only when the connection comes from an address in `AUTH_TRUSTED_PROXIES`, and the
rightmost untrusted entry is used; the server refuses to start if the first is set without
the second. See the README ("Login throttling").

### Token version

Every token carries `tv`, which must equal `app_user.token_version`. A mismatch (or a
missing/non-integer `tv`) is a `401` ("Session has been revoked. Sign in again."). The
version is bumped by `POST /auth/change-password`, `POST /auth/revoke-sessions`,
`scripts/create_user.py --revoke-sessions` and `--reset-password`.

### `POST /auth/change-password`

Requires a Bearer token. Body: `{"current_password", "new_password"}` (extra fields are a
`422`). The caller can only change their own password. Success (`200`) returns the
same body as login with a **new** token (version +1); every previously issued token,
including the one used for this call, stops working.

| Status | When |
|---|---|
| `401` | No/invalid token, or the account is no longer active. |
| `403` | `current_password` is wrong. (Not 401, so clients do not treat it as an expired session.) Counts toward the login throttle. |
| `400` | `new_password` equals the current password. |
| `422` | `new_password` is shorter than 12 or longer than 1024 characters, or unknown fields. |
| `429` | Locked out by the throttle. |
| `503` | `AUTH_JWT_SECRET` is unset or too short. |

### `POST /auth/revoke-sessions`

Requires the `admin` role. Body: `{"email"}`. Invalidates all of that user's existing
tokens and returns `{"email", "token_version"}`; the user can sign in again
normally. `404` for an unknown email, `403` without the role. An admin may revoke
themselves.

### User administration (`/admin`)

Every route requires the `admin` role (`403` otherwise, `401` without a token). There is
no public sign-up; the first admin is created with
`python scripts/create_user.py --email <e> --bootstrap-admin`, which refuses once an active
admin exists. Roles are `analyst` and `admin`; any other value is a `422`, so a typo cannot
create a powerless role. Admin and analyst are independent: an admin who is not also an
analyst cannot decide assumptions or execute runs.

**`UserOut`** (every response): `id, email, roles[], is_active, token_version, created_at,
last_login_at`. Password hashes are never returned.

| Route | Request | Success | Errors |
|---|---|---|---|
| `GET /admin/users` | `?is_active=true|false`, `limit` 1-500 (default 100), `offset` | `200 {total, users[]}` ordered by email | `422` bad paging |
| `POST /admin/users` | `{email, password (12-1024 chars), roles[]}`, extra fields forbidden | `201 UserOut` | `409` email exists (case-insensitive); `422` weak password, invalid email, unknown role, more than 8 roles |
| `PATCH /admin/users/{id}` | any of `{roles[], is_active, revoke_sessions}`, at least one; extra fields forbidden | `200 UserOut` | `404` unknown id; `409` last-admin rule; `422` empty body, unknown role |
| `GET /admin/users/{id}/audit` | | `200` list of audit entries, oldest first | `404` |

`roles` in a PATCH replaces the whole list (`[]` makes the account read-only). The admin
chooses the initial password on create; the user should change it with
`POST /auth/change-password`.

**Semantics.**
- *Deactivating* sets `is_active=false` and bumps `token_version`, so the user's tokens die
  immediately and are not revived by reactivation (they must sign in again).
- `revoke_sessions: true` bumps `token_version` without deactivating. `false` does nothing.
- Roles take effect on the user's very next request (they are read from the database).
- **Last-admin guard:** if the change would leave no *active* admin (removing the `admin`
  role from, or deactivating, the only active admin, alone or combined with other
  fields) the whole request is refused with `409` and nothing is changed or audited. An
  inactive admin does not count as a spare. With another active admin present, an admin
  may demote or deactivate themselves. Admin rows are locked (`FOR UPDATE`) while checking
  so two admins demoting each other at once on Postgres serialise.
- The change and its audit rows commit in one transaction; if the audit write fails the
  change is rolled back.

**Audit.** Entries have `entity_type: "AppUser"`, `entity_id` = the user's id, and
`actor: "human:<admin email>"` (`"system"` for `scripts/create_user.py`). Actions:
`USER_CREATED` (`email, roles`), `USER_ROLES_CHANGED` (`before, after`), `USER_DEACTIVATED`,
`USER_REACTIVATED`, `USER_SESSIONS_REVOKED` (`reason: "requested"|"deactivated"`,
`token_version`), `USER_PASSWORD_RESET` (CLI only). No entry is written for a no-op, and none
ever contains a password or hash. `POST /auth/revoke-sessions` is audited the same way.

### Request limits and CORS

Request bodies over `MAX_REQUEST_BODY_BYTES` (default 1048576) are answered `413`
`{"detail": "Request body too large (limit N bytes)."}` before the route runs, whether
or not `Content-Length` is present or accurate (bytes are counted as they arrive). The
limit applies to every route, including the unauthenticated login. The Next.js proxy
applies the same limit (`MAX_PROXY_BODY_BYTES`, and 16 KB on its login and
change-password routes) and answers `413` itself.

CORS allows the methods `GET, POST, PUT, PATCH, DELETE, OPTIONS` and the request
headers `Authorization, Content-Type, Accept` (plus the CORS-safelisted ones the
middleware always adds), with credentials. `ALLOWED_ORIGINS` must list exact
`scheme://host[:port]` origins; a wildcard anywhere in it makes the server refuse to
start. A preflight asking for another method or header, or from an unlisted
origin, gets `400`.

### Separation of duties

`POST /valuation/assumptions/propose` records the caller as `proposed_by` (returned
on every `AssumptionDecisionOut`). `POST /valuation/assumptions/{id}/decide` answers
`403` ("a different user must decide it") when the caller is the proposer, compared
case-insensitively, whatever the decision; the proposal stays `PENDING`. Proposals
with no recorded proposer are handled as below.

**Legacy proposals.** Rows that predate proposer tracking hold
`proposed_by = "unknown-legacy"` (migration `e5f6a7b8c9d0`; a NULL is treated the
same way). `decide` with `APPROVE` or `EDIT` on one answers `400`: "no recorded
proposer ... Re-propose the assumption". `REJECT` is allowed so they can be cleared.
`proposed_by` is mandatory when creating a proposal, so no new unattributed rows
are created (including by `POST /scenarios/value-unlock/propose`).

| Status | When |
|---|---|
| `401` | No `Authorization` header, a non-`Bearer` scheme, or a token that is malformed, badly signed, expired, or missing `exp`/`sub`; **or a validly signed, unexpired token whose user no longer exists or is deactivated** (`"Account is no longer active."`). Carries `WWW-Authenticate: Bearer`. |
| `403` | An active user whose `app_user.roles` lack the role the endpoint requires. |
| `503` | `AUTH_JWT_SECRET` is unset or shorter than 32 characters. The server fails closed and never lets an unauthenticated request through. |

**The user row is authoritative.** `get_current_user` verifies the signature and expiry, then loads the user from `app_user` by (case-insensitive) email on every request; nothing is cached. The roles come from that row; the token's own `roles` claim is ignored. Consequences: deactivating or deleting a user cuts off their existing tokens on the next request, and a demotion or promotion takes effect immediately. The subject recorded in `decided_by` etc. is the row's canonical (lower-case) email.

**Roles.** Only `POST /valuation/assumptions/{decision_id}/decide` and
`POST /valuation/run` require the `analyst` role. Every other protected
endpoint — including `POST /valuation/assumptions/propose` — is open to any
authenticated user.

**Identity comes from the token, never the body.** The token's `sub` is
recorded as `AssumptionDecision.decided_by`, `ValuationRun.created_by`, and the
`human:<sub>` actor in the audit log. The decide and run request bodies have no
`user_id` / `created_by` field, and unknown body fields are forbidden, so a
client that still sends one gets a `422` rather than having it ignored.
**Breaking change:** both fields were previously required.

CORS preflight (`OPTIONS`) is answered by the CORS middleware before auth and
needs no token. The interactive docs (`/docs`, `/redoc`, `/openapi.json`) are
not part of the routers and remain open.

---

## `GET /health`

Health check. Returns `{"status": "ok"}`. The only endpoint that needs no
token. No params.

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

### `GET /companies/{ticker}/peer-candidates`
Phase 10 addition. Query params: `fiscal_year` (required int), `quarter`
(optional int 1-4; omitted = full fiscal year), `frame_concept` (default
`"Revenues"`, a US-GAAP duration tag), `max_shortlist` (default 15).

Discovers candidate comps peers via the real SEC XBRL **frames** API
(`backend/services/sec_client.py::SECConnector.get_frame`,
`backend/data/peer_discovery.py::discover_peer_candidates`): pulls every
filer that reported `frame_concept` for the period, ranks by proximity to
the target's own reported value, takes a bounded shortlist, cross-references
each candidate's SIC code (via cached `submissions`) against the target's own
`backend.data.business_classifier` category, and for survivors pulls real
financial facts (revenue, operating income, D&A, net income, debt, cash)
with honest `REPORTED`/`MISSING` status per concept (never fabricated).

**Read-only research/discovery endpoint** — it does not create or mutate any
`AssumptionDecision`, `ValuationRun`, or other governance-tracked state, and
it never calls `backend.valuation.comps.run_comps` itself. Exactly like AI
peer recommendation (Phase 6) and manual peer entry, a discovered candidate
still requires explicit human/caller review and confirmation before it's used
in an actual comps run — see `docs/valuation-methodology.md`.

- Response: `{ticker, cik, target_classification: {category, sic_code,
  rationale}, frame_concept, fiscal_year, quarter, frame_company_count, note,
  candidates: [{cik, ticker, entity_name, sic, sic_description,
  classification_category, frame_concept, frame_value, financials: [{concept,
  value, unit, xbrl_tag, data_status}...]}...], governance_note}`.
- Errors: `404` unknown ticker; `503` SEC rate-limited or unavailable.

### `GET /companies/{ticker}/insider-transactions`
Query params: `limit` (default 20 — number of most-recent Form 3/4/5 filings
to fetch and parse; each filing requires its own XML fetch, so this bounds
the request fan-out, not the transaction count returned).

Real Form 3/4/5 insider transaction history for `ticker` as **issuer** (SEC
submissions data for a company lists every Form 3/4/5 filed about it, so
this is a straightforward ticker→CIK→filings→parse→persist flow, unlike
institutional holdings below). Uses `SECConnector.get_insider_filings` +
`get_ownership_document`; persists every transaction row via
`upsert_insider_transactions` (natural key: accession_number +
reporting_owner_cik + security_title + transaction_date). A filing that
fails to fetch/parse is skipped rather than failing the whole request.
- Response: `{ticker, cik, company_id, transaction_count, transactions: [
  {reporting_owner_name, reporting_owner_cik, is_officer, is_director,
  is_ten_percent_owner, is_other, officer_title, transaction_table
  ("nonDerivative"|"derivative"), security_title, transaction_date,
  transaction_code, shares_transacted, price_per_share,
  transaction_acquired_disposed_code, shares_owned_after, ownership_type
  ("D"|"I"), accession_number, source, source_url, filing_date,
  data_status}...]}`. Fields genuinely absent from a given filing's XML
  come back `null`/`data_status="MISSING"` — never fabricated.
- Errors: `404` unknown ticker or no Form 3/4/5 filings found; `503` SEC
  rate-limited or unavailable.

### `GET /companies/{ticker}/institutional-holdings`
Query params: `filer_cik` (**required** — a known institutional manager's
CIK, e.g. Berkshire Hathaway's `0001067983`), `accession_number` (optional,
defaults to the filer's latest 13F-HR), `issuer_contains` (optional,
case-insensitive substring match against `nameOfIssuer`).

**Real constraint, not a simplification:** 13F filings are filed BY
institutional investment managers ABOUT their own holdings — SEC does not
publish a reverse index from issuer to the 13F filers holding it, so there
is no ticker-only "who holds this company" lookup to build. This endpoint
is filer-keyed: `ticker` in the path only resolves/persists the calling
company's own `Company` row (for consistency with the rest of this router)
and does **not** filter the filer's holdings by itself — use
`issuer_contains` for that. See `docs/data-model.md` for the full
investigation of SEC's 13F data shape that led to this design.

Uses `SECConnector.get_13f_holdings` (cover page + information table,
discovered via `index.json` since the information-table filename isn't
derivable from `primaryDocument`); persists via
`upsert_institutional_holdings` (natural key: filer_cik + accession_number +
cusip).
- Response: `{ticker, filer_cik, filer_name, accession_number,
  period_of_report, issuer_contains, holding_count, holdings: [
  {filer_cik, filer_name, issuer_name, title_of_class, cusip,
  period_of_report, value (thousands of USD, per SEC convention),
  shares_or_principal_amount, shares_or_principal_type ("SH"|"PRN"),
  investment_discretion, voting_authority_sole/shared/none,
  accession_number, source, source_url, filing_date, data_status}...],
  governance_note}`.
- Errors: `404` unknown ticker, unknown filer CIK, or no 13F-HR filings
  found for that filer; `503` SEC rate-limited or unavailable.

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
(same response shape, same errors) to avoid duplicating `SECConnector` error
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
  overhead treatments, implied price/share, conglomerate discount % = (implied - market) / implied, positive = market below SOTP; plus upside/downside % vs market;
  and `warnings`: `NEGATIVE_EQUITY_VALUE`, `NEGATIVE_SEGMENT_EV`, `OVERHEAD_NOT_DEDUCTED`,
  `LARGE_GAP_TO_MARKET`).
  `400` on invalid input (e.g. length mismatches, non-positive shares).
- **`POST /valuation/dcf`** — body: `DcfInput` (base revenue, per-year
  growth/margin/D&A/capex/NWC schedules, WACC, terminal growth, bridge
  fields `total_debt` (gross, **not** net of cash) / `cash_and_equivalents` /
  `investments` / `minority_interest`, diluted shares) → `DcfResult`
  (year-by-year projections, terminal value, EV, equity value, implied
  price/share). Equity = EV − `total_debt` + `cash_and_equivalents` +
  `investments` − `minority_interest`. **Breaking change:** this field was
  previously `net_debt`; a body still sending it is rejected with `422`
  (missing `total_debt`).
  **Bounds** (each violation is a `422`): `0 <= tax_rate < 1`; `wacc > -1`;
  every `revenue_growth_rates` entry in `(-1, 10]` (revenue cannot fall by
  more than 100% a year); every `ebit_margins` entry in `[-5, 1]`;
  `terminal_growth_rate` in `(-1, 1]`; `exit_multiple`, if given, in
  `(0, 1000]`; `diluted_shares_outstanding > 0`; no `NaN`/`Infinity`.
  Optional `mid_year_convention` (bool, default `false`) discounts FCFF at
  `t - 0.5` and the Gordon terminal value at `N - 0.5`; the exit-multiple
  terminal value stays at `N`. Omitting it changes nothing.
  **NWC method (exactly one is required, else `422`):** `nwc_change_pct_of_revenue`
  (per-year list, the original method) or `nwc_pct_of_incremental_revenue` (one rate in
  `[-1, 1]` applied to the year-over-year increase in revenue; the Gordon terminal
  value then uses steady-state NWC, see `docs/valuation-methodology.md`). Optional
  `risk_free_rate` only feeds a warning. The response gains `terminal_fcff`,
  `terminal_value_share_of_ev` and `warnings` (a list of `{code, message}`; empty when
  nothing looks off, and never affecting a figure): `NEGATIVE_TERMINAL_FCFF`,
  `TERMINAL_VALUE_SHARE_HIGH` (> 85% of EV), `EXIT_MULTIPLE_TERMINAL_VALUE_SHARE_HIGH`,
  `TERMINAL_GROWTH_ABOVE_RISK_FREE`, `LOW_WACC` (< 5%), `THIN_WACC_SPREAD` (< 2 points),
  `NEGATIVE_EQUITY_VALUE`.
  `400` when the inputs pass validation but cannot be computed: mismatched
  schedule lengths (`DcfInput.validate_lengths()`), `wacc <= terminal_growth_rate`,
  an arithmetic failure (`ZeroDivisionError` / `OverflowError`, e.g. `wacc`
  just above -1 over a long horizon so a discount factor underflows to zero,
  or a very large `wacc`), or a result so large it is not finite.
- **`POST /valuation/wacc`** — body: `WaccInput` (`risk_free_rate, beta,
  equity_risk_premium, cost_of_debt_pretax, tax_rate, market_value_equity,
  market_value_debt`) → `WaccResult`. The body is typed (it was previously an
  untyped object), so a missing, mistyped or out-of-range field is a `422`
  with per-field detail: `0 <= tax_rate < 1`, `market_value_equity > 0`,
  `market_value_debt >= 0`, no `NaN`/`Infinity`.
- **`POST /valuation/comps`** — body: `CompsInput` (peer set + multiples
  basis) → `CompsResult`. A peer metric that is zero or negative (negative
  EBITDA, EBIT, earnings or book value, or non-positive revenue) yields no
  multiple (`null` in `peer_multiples`) and so never enters the min / 25th /
  median / 75th / max statistics. `422` on schema violations (including
  `NaN`/`Infinity`), `400` on e.g. a target with no value for the chosen
  metric. Optional `trim_outliers` (default `false`) and `iqr_multiplier` (default
  1.5, `0 < k <= 10`) drop IQR outliers from each multiple's statistics (never from
  the chosen multiple). Nothing is dropped silently: the response lists every
  (peer, multiple) left out in `exclusions` (`reason` is `not_meaningful`,
  `iqr_outlier_low` or `iqr_outlier_high`, with the fences for outliers), each
  `stats` row carries `n_peers_used` / `n_not_meaningful` / `n_outliers_excluded`,
  `excluded_outlier_peer_count` counts distinct trimmed peers, and `warnings`
  reports skipped trimming, too few peers and a chosen multiple outside the peer range.
- **`POST /valuation/sensitivity/dcf`** — body: `{base_dcf_input: dict,
  row_field, row_values, col_field, col_values, output_field}` → grid of
  `output_field` varying two DCF input fields (one DCF run per cell).
  `row_field` / `col_field` must be scalar numeric `DcfInput` fields
  (`base_revenue`, `tax_rate`, `wacc`, `terminal_growth_rate`, `total_debt`,
  `cash_and_equivalents`, `investments`, `minority_interest`,
  `diluted_shares_outstanding`, `exit_multiple`) and must differ from each
  other; `output_field` is one of `enterprise_value`, `equity_value`,
  `implied_price_per_share` (default), `terminal_value_undiscounted`,
  `terminal_value_discounted`. Each axis has 1–25 values (so at most 625
  runs). Anything else is a `422`. `400` when a grid cell cannot be computed
  — a value outside that field's bound (e.g. `wacc = -1` or `tax_rate = 1.5`
  in the grid) or an incomplete `base_dcf_input`. An unknown axis or output
  name used to be silently ignored or crash with a `500`.

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
  `RecordDecisionRequest` (`decision: APPROVE|EDIT|REJECT, human_value,
  reason`) → updated `AssumptionDecisionOut`. **Requires the `analyst` role**
  (`403` otherwise). The approver recorded in `decided_by` and the audit actor
  is the token subject; there is no `user_id` field (sending one is a `422`).
  `400` on governance errors (e.g. deciding an already-decided proposal).
- **`GET /valuation/company/{company_id}/assumptions`** →
  `list[AssumptionDecisionOut]`.
- **`POST /valuation/run`** — body: `RunValuationRequest` (`company_id,
  method, decision_ids, inputs, source_data_version`)
  → `ValuationRunOut`. **Requires the `analyst` role** (`403` otherwise); the
  run's `created_by` and audit actor are the token subject (there is no
  `created_by` field; sending one is a `422`). Computes and persists a new
  versioned `ValuationRun`, gated on every `decision_ids` entry being
  `APPROVED`/`OVERRIDDEN` for that company.
  **The server never trusts a client-supplied result.** The request has no
  `outputs`; unknown body fields are forbidden, so sending `outputs` is a
  `422` rather than being silently ignored. `inputs` is validated against
  `DcfInput` / `CompsInput` / `SotpInput` for the `method` (unknown keys are
  rejected, not dropped), the deterministic engine (`run_dcf` / `run_comps` /
  `run_sotp`) is run server-side, and the persisted `outputs` are the
  engine's result. The persisted `inputs` are the validated model's fields
  plus a server-built `_governance_snapshot` (`assumption_decision_ids`,
  `assumptions_used`, `assumptions_verified`, `assumptions_unmapped`,
  `source_data_version`).
  Each approved decision whose `assumption_key` maps to an input field must
  equal that field (relative tolerance 1e-9); the value used is the human's
  `approved_value`, so after an `EDIT` the AI's original number no longer
  matches. Mapped keys: DCF — `wacc`, `terminal_growth_rate`,
  `exit_multiple`, `revenue_growth_rate` (→ `revenue_growth_rates`) and
  `ebit_margin` (→ `ebit_margins`), where the two schedule keys are flat
  rates and so must hold in **every** forecast year; COMPS — `peer_multiple`
  (→ `chosen_multiple_value`); SOTP — none today. Any other approved key
  still clears the approval gate but is listed under `assumptions_unmapped`
  and is not value-checked. Two decisions on one key with different approved
  values can never both match, so such a run is refused.
  `400`, with nothing written, on: unknown `method`; a decision that is
  missing, `PENDING`, `REJECTED`, or belongs to another company (checked
  first, before the inputs are looked at); invalid, incomplete or
  unknown-keyed `inputs`, or inputs the engine rejects (e.g. WACC ≤ terminal
  growth, mismatched schedule lengths); or an approved value that does not
  match its input field. **Breaking change:** `outputs` was previously a
  required request field.
- **`GET /valuation/company/{company_id}/runs?method=...`** →
  `list[ValuationRunOut]` (version history, optionally filtered by method).
  `method` is `DCF`, `COMPS` or `SOTP`, case-insensitive; an unknown value is
  a `400` listing the valid ones (it was previously an unhandled `500`).
  Omitted or empty means no filter.
- **`GET /valuation/company/{company_id}/runs/diff?from_version=&to_version=&method=`**
  → `DiffResponse` (field-level diff between two run versions). Same `method`
  rule: unknown → `400`, checked before the versions are looked up. `404` if
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
