# SOTP Intelligence

An institutional-style, SEC-data-driven company valuation platform. SOTP Intelligence
builds Sum-of-the-Parts (SOTP), DCF, and comparable-company valuations from primary
SEC filing data, with an AI research layer that assists human analysts — never
replaces their judgment.

## Core principle

> **DATA IS SOURCED. CALCULATIONS ARE DETERMINISTIC. AI REASONS. HUMANS DECIDE.**

- **Data is sourced** — every financial fact traces back to a specific SEC filing
  (accession number, XBRL tag, source URL). No numbers are invented.
- **Calculations are deterministic** — the valuation engine (DCF, comps, SOTP math)
  is plain, auditable code. Given the same inputs, it always produces the same
  output. No LLM is in the arithmetic path.
- **AI reasons** — the AI layer (Gemini-based) proposes assumptions, flags
  inconsistencies, and drafts research narratives, always with a stated
  rationale and confidence.
- **Humans decide** — every AI-proposed assumption is a *recommendation* until a
  human approves, overrides, or rejects it. That decision, and who made it, is
  permanently recorded (`AssumptionDecision`, `AuditLogEntry`).

This repository implements **Phases 1–10 of the build** — SEC data pipeline,
XBRL normalization, segment extraction, deterministic valuation engine (DCF,
comps, SOTP, sensitivity, scenarios, reverse valuation, value-unlock),
governance/approval workflow, AI reasoning layer, a fully interactive
Next.js frontend, and this phase's testing/documentation consolidation.
Deployment hardening (CI, containers, hosted config) was not built. API
authentication — JWT Bearer tokens with an `analyst` role — was added after
the ten phases (see [Authentication](#authentication)), though without a login
flow yet. See `docs/definition-of-done.md` for the honest, itemized status of
every item in the original spec's Definition of Done. See
`docs/implementation-plan.md` for the full phase-by-phase build log.

## Project layout

```
apps/web/          Next.js 14 (App Router) + TypeScript + Tailwind frontend
backend/            FastAPI backend (api, models, services, valuation, ai, governance, audit, tests)
packages/shared-types/  TypeScript types shared across frontend (and future services)
data/cache/         Local cache for SEC/market data responses (gitignored contents)
docs/               Architecture, data model, valuation methodology, AI governance, API docs
scripts/            Dev scripts (`create_user.py` adds a login, `mint_dev_token.py` mints a JWT for an existing user)
```

## Running locally

### Backend (FastAPI)

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux
pip install -r requirements.txt
uvicorn api.main:app --reload
```

The API boots at `http://localhost:8000`. Health check: `GET /health`.
See `docs/api-documentation.md` for every endpoint.

`SEC_USER_AGENT` (see Environment variables below) must be set before the
API or the test suite can make any SEC EDGAR request — without it,
`SECClient` raises immediately (`backend/services/sec_client.py`).

### Authentication

Every endpoint **except `GET /health` and `POST /auth/login`** requires
`Authorization: Bearer <JWT>` (tokens are HS256-signed). Without a valid token
the API answers `401`; for an active user without the `analyst` role, the two
endpoints that record a human decision or execute a governed run
(`POST /valuation/assumptions/{id}/decide`, `POST /valuation/run`) answer
`403`. All other endpoints are open to any authenticated user.

**The user row is checked on every request.** A valid signature only proves the
token was issued by this server. After verifying it, the API loads the user from
`app_user` by email (nothing is cached) and takes the **roles from that row, not
from the token**. So a user who is deactivated or deleted gets `401` on their very
next request even though their token has not expired, and removing the
`analyst` role turns the next decide/run call into a `403` straight away.

Signing in (web app and API):

```bash
# 1. Put a signing secret (>= 32 chars) in .env, then restart the backend.
python -c "import secrets; print(secrets.token_urlsafe(48))"   # -> AUTH_JWT_SECRET=...

# 2. Apply migrations (creates the app_user table) and create a login.
#    There is no sign-up page; the password is read from a hidden prompt.
alembic -c backend/alembic.ini upgrade head
python scripts/create_user.py --email you@example.com --roles analyst

# 3. Open http://localhost:3000 and sign in, or use the API directly:
curl -s -X POST http://localhost:8000/auth/login -H "Content-Type: application/json" \
     -d '{"email":"you@example.com","password":"..."}'      # -> {"access_token": "...", ...}
```

`POST /auth/login` is the only open route besides `/health`. It returns a
token valid for 60 minutes (`AUTH_TOKEN_MINUTES`); the password is verified
against an Argon2id hash. Wrong password, unknown email and disabled account all
return the same `401`. Failures are throttled (`429` with `Retry-After`) by a
**database-backed** counter, so the limit survives restarts and is shared by every
worker; see "Login throttling" below. Passwords must be at least 12 characters.

**The browser never holds the token.** The web app signs in through
`/api/auth/login`, a Next.js route handler that stores the token in an
`httpOnly`, `SameSite=Strict` cookie (`Secure` in production builds; set
`SESSION_COOKIE_SECURE=false` only for a production build on plain http). Pages
call `/api/backend/*`, a proxy route that reads the cookie and adds the
`Authorization` header, so no credential is in the JavaScript bundle and page
scripts cannot read it. `proxy.ts` sends signed-out visitors to `/login`, and
state-changing proxy calls are refused if their `Origin` is not the app's own.
The server-side `API_BASE_URL` (default `http://localhost:8000`) says where the
Next.js server reaches the backend. `scripts/mint_dev_token.py --sub <email>`
mints a token for scripts and `curl`, but only for a user who already exists and
is active (the API would reject any other subject).

**Who did it comes from the token, never the request body.** The token's `sub`
(the user's email) is recorded as `AssumptionDecision.decided_by`,
`ValuationRun.created_by` and the `human:<sub>` actor in the audit log. The
request bodies have no `user_id` / `created_by` fields (sending one is a
`422`), so a caller cannot approve or run "as" someone else.

**Separation of duties.** `POST /valuation/assumptions/propose` records the
caller as `proposed_by` (so does `POST /scenarios/value-unlock/propose`;
`approval.propose()` requires it). That same user (compared case-insensitively)
gets a `403` if they try to approve, edit or reject the proposal; a different
`analyst` must decide it.

Proposals that predate this tracking were marked `proposed_by = "unknown-legacy"`
by migration `e5f6a7b8c9d0`. Because nobody can say who proposed them, they
cannot be approved or edited: `decide` answers `400` telling the caller to
re-propose. They can still be rejected, which clears them out.

If `AUTH_JWT_SECRET` is unset or shorter than 32 characters, every protected
endpoint (and login) answers `503`: the server fails closed.

**Login throttling.** Every attempt is recorded in the `login_attempt` table
(an HMAC of the email, never the address itself, plus the IP and time). Three
counters over a rolling 15 minutes lock a caller out with exponential back-off:

| Scope | Key | Locks after | Lock length |
|---|---|---|---|
| pair | (email, ip) | 5 failures | 1 min, doubling, max 15 min |
| ip | ip, all emails | 20 failures | 1 min, doubling, max 15 min |
| email | email, all ips | 10 failures | 1 min, doubling, max 5 min |

Attempts made *while locked* are rejected without being recorded, so hammering a
locked account cannot extend the lock. Guessing someone's password from your own
address locks only (their email, your ip), not them. The email-wide lock is
skipped for an address that has already logged in successfully as that user in
the last 30 days, so spreading failures across many addresses does not lock the
real user out of where they actually sign in. Change-password attempts with a wrong
current password count too.

The "ip" is the socket peer unless `AUTH_TRUST_FORWARDED_FOR=true`, which uses the
rightmost `X-Forwarded-For` entry. Behind the Next.js server you need it (else every
web user looks like one address, which would make the pair lock global), **but only
if the backend is reachable solely through your own proxy** and that proxy
appends the real client address. `next start` exposed directly to the internet
passes a client-supplied `X-Forwarded-For` through unchanged, so a client could
pick its own throttle address; the email-wide limit and Argon2id then remain the
defence. Put a reverse proxy in front for production.

**Changing a password and revoking sessions.** Every token carries a version
(`tv`) that must equal `app_user.token_version`. `POST /auth/change-password`
(current password required; new one at least 12 characters and different) and the
admin action `POST /auth/revoke-sessions` bump it, which invalidates every token
issued earlier, including on other devices. Changing your own password returns a
fresh token, and the web app (`/account`, linked from the header) swaps the cookie
so you stay signed in. `scripts/create_user.py --revoke-sessions` does the same from a
shell, and `--reset-password` also bumps the version. The admin endpoint needs the
`admin` role: `create_user.py --roles admin,analyst`.

**Request size and CORS.** Bodies over 1 MiB (`MAX_REQUEST_BODY_BYTES`) get `413` from
the API, counted as they stream so a missing or false `Content-Length` does not
help, and from the web proxy (`MAX_PROXY_BODY_BYTES`; its login routes allow 16 KB).
CORS allows only `GET POST PUT PATCH DELETE OPTIONS` and the `Authorization`,
`Content-Type` and `Accept` headers. `ALLOWED_ORIGINS` must be exact origins: a
`*` (or `https://*.example.com`) makes the server refuse to start, because
credentials are allowed.

**Not built yet:** a forgot-password / reset-by-email flow, a user-admin page, MFA
or SSO. Tokens issued before the token-version change are rejected (sign in
again). The interactive docs (`/docs`, `/openapi.json`) remain open, and each
request costs one indexed user lookup.

### Database (Postgres/Supabase) + migrations

Real persistence (ingested facts, segments, valuation runs) requires
`DATABASE_URL` pointed at a Postgres/Supabase instance (see `.env.example`).
Apply the schema with Alembic from the repo root:

```bash
pip install -r backend/requirements.txt
alembic -c backend/alembic.ini upgrade head
```

The backend test suite does **not** require Postgres: `backend/tests/conftest.py`
auto-overrides `DATABASE_URL` with a throwaway per-test SQLite file, so `pytest`
works out of the box with no external DB running.

Run tests:

```bash
cd backend
pytest                      # unit tests only need no external services
pytest -m integration       # also hits live SEC EDGAR (needs SEC_USER_AGENT)

# Run the suite on Postgres instead of per-test SQLite. Use a throwaway database
# whose name ends in "_test": every test drops and recreates all of its tables.
# (DATABASE_URL is ignored by the tests; TEST_DATABASE_URL is the switch.)
TEST_DATABASE_URL=postgresql://user:pw@localhost:5432/sotp_test pytest -m "not integration"
```

### CI and containers

`.github/workflows/ci.yml` runs on every push and pull request:

- **backend**: Python 3.13, `pip install -r backend/requirements.txt`,
  `pytest -m "not integration"` with a dummy `SEC_USER_AGENT` (the integration
  tests call real SEC EDGAR and are excluded).
- **web**: Node 20, `npm ci`, `npx tsc --noEmit`, `npm run build` in `apps/web`.
- **audit**: `npm audit --omit=dev --audit-level=high`.

Run the whole stack (Postgres 16 + backend + web) with Docker:

```bash
# .env needs AUTH_JWT_SECRET (>= 32 chars); see .env.example
docker compose up --build
```

The backend container applies the Alembic migrations on start, then serves on
port 8000; the web app is on port 3000 and reaches the backend through its own
server (`API_BASE_URL=http://backend:8000`), so nothing secret is baked into the
image. Create a login once the stack is up (prompts for the password):

```bash
docker compose exec backend python scripts/create_user.py --email you@example.com
```

**What has been verified.** The workflow has run on GitHub for every push to
`main`; backend, web and audit all pass. Docker itself was not available when
this was written, so the Dockerfiles and `docker-compose.yml` have **not been
built or run**. Instead the same steps were run directly against a fresh
Postgres 16 database with the compose environment (migrations from scratch,
`/health`, `/login`, `create_user.py`, sign-in through the web proxy over plain
http, loading a company), and they work; `API_BASE_URL` is read at runtime. The
first real `docker compose up` is still the test of the image files.

### Frontend (Next.js)

Node.js/npm are available in this dev environment (verify with `node -v` /
`npm -v`; on a fresh Windows shell they may not be on `PATH` yet — see
"Windows PATH note" below).

```bash
cd apps/web
npm install
npm run dev          # dev server at http://localhost:3000
npm run build         # production build; runs full TypeScript strict type-check
```

`npm install` and `npm run build` are both verified to succeed cleanly as of
Phase 9/10 (all routes compile and prerender). The app expects the backend
at `API_BASE_URL` (server-side, default `http://localhost:8000`); with no
backend running, pages degrade gracefully to an explicit "Backend
unavailable" error state rather than crashing.

#### Windows PATH note

If `node`/`npm` aren't found in a fresh PowerShell session, run:

```powershell
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
```

or, in bash: `export PATH="/c/Program Files/nodejs:$PATH"`.

### Environment variables

Copy `.env.example` to `.env` and fill in real values. Never commit `.env`
(it's gitignored — see `.gitignore`).

- `SEC_USER_AGENT` — **required**, format `"Name email@domain.com"`, per
  SEC's fair-access policy. Needed for any endpoint or test that touches
  SEC EDGAR (companies, filings, segments, and the `-m integration` tests).
- `DATABASE_URL` — Postgres/Supabase connection string, used for real
  persistence when running the app locally (ingested facts, segments,
  valuation runs, governance decisions, audit log). **Not required to run
  the test suite** — `backend/tests/conftest.py` automatically overrides
  `DATABASE_URL` with a throwaway per-test SQLite file, so `pytest` works
  with zero external DB setup.
- `GEMINI_API_KEY` (and optionally `GROQ_API_KEY`) — AI layer credentials.
  Endpoints that don't call AI (e.g. `/valuation/sotp`, `/valuation/dcf`)
  work without these; AI-backed endpoints (assumption recommendation, risk
  scoring, value-unlock proposals, memo sections) will fail without a valid
  key.
- `AUTH_JWT_SECRET` — **required**, at least 32 characters. HS256 signing
  secret for API Bearer tokens (see [Authentication](#authentication)). If
  unset or too short, every endpoint except `/health` answers `503`. **Not
  required to run the test suite** — `conftest.py` pins a test-only secret.
- `ALLOWED_ORIGINS` — exact origins allowed by CORS; a wildcard aborts startup.
- `AUTH_TRUST_FORWARDED_FOR` — `true` to take the login throttle's client address from
  the rightmost `X-Forwarded-For` entry (see Login throttling). Default `false`.
- `MAX_REQUEST_BODY_BYTES` — request body limit for the API (default 1048576);
  `MAX_PROXY_BODY_BYTES` is the web proxy's (set in `apps/web/.env.local`).
- `AUTH_TOKEN_MINUTES` — lifetime of a login token (default 60, clamped to
  1..1440).
- `API_BASE_URL` — set in `apps/web/.env.local`: where the Next.js server reaches
  the backend (server-side only; the browser never sees it).
- `SESSION_COOKIE_SECURE` — set to `false` only to serve a production build over
  plain http. Defaults to `Secure` in production.

## Documentation

- [`docs/implementation-plan.md`](docs/implementation-plan.md) — full 10-phase roadmap, architecture, data flow
- [`docs/architecture.md`](docs/architecture.md) — system architecture
- [`docs/data-model.md`](docs/data-model.md) — database schema and entity relationships
- [`docs/valuation-methodology.md`](docs/valuation-methodology.md) — DCF / comps / SOTP methodology
- [`docs/ai-governance.md`](docs/ai-governance.md) — AI governance and human-approval rules
- [`docs/api-documentation.md`](docs/api-documentation.md) — API endpoints

- [`docs/definition-of-done.md`](docs/definition-of-done.md) — final, honest
  audit of the master spec's Definition-of-Done checklist

## Status

All 10 phases complete as scoped. Backend: 684 unit/router tests +
12 integration tests (real SEC EDGAR calls, `-m integration`), 696 in total, all passing.
Frontend: `npm run build` succeeds cleanly under TypeScript `strict` mode.
JWT authentication with an `analyst` role gate has since been added (item 39
is PARTIAL: login, sessions and live user checks built; no password reset or SSO). CI and containers are in place (see "CI and containers" below; item 40 is
PARTIAL: CI green on GitHub, Docker images never built, no CD) — see
`docs/definition-of-done.md` for the itemized final status of every
Definition-of-Done requirement, including what's PARTIAL or NOT DONE.
