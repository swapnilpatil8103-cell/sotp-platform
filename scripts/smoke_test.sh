#!/usr/bin/env bash
# End-to-end smoke test of the docker compose stack. Exits non-zero if any check fails.
#
#   bash scripts/smoke_test.sh            # build, start, test, tear down
#   KEEP=1 bash scripts/smoke_test.sh     # leave the stack running afterwards (for poking at it)
#   SMOKE_SKIP_SEC=1 bash scripts/smoke_test.sh   # skip the one check that calls real SEC EDGAR
#
# Needs: docker with the compose plugin, bash, curl, python3. Uses its own project name
# (sotp-smoke), freshly generated secrets in a temp env file (your .env is NOT read), and
# removes its containers and volumes on exit.
#
# Steps (each check prints PASS/FAIL with the evidence it saw):
#   1  build, start, wait until db / backend / web are healthy
#   2  Alembic ran to head inside the backend container, with no errors in its logs
#   3  bootstrap the first admin inside the container; a second bootstrap is refused
#   4  through the web app: /login, sign in, session cookie flags, company page, DCF run, sign out
#   5  exposure and hygiene: published ports, non-root processes, no secrets in image
#      layers or the image filesystem, image sizes

set -uo pipefail
cd "$(dirname "$0")/.."

PROJECT="${SMOKE_PROJECT:-sotp-smoke}"
WEB="http://localhost:3000"
API="http://127.0.0.1:8000"
KEEP="${KEEP:-0}"
TMP="$(mktemp -d)"
ENVFILE="$TMP/smoke.env"
JAR="$TMP/cookies.txt"
BACKEND_MAX_BYTES="${BACKEND_MAX_BYTES:-1800000000}"
WEB_MAX_BYTES="${WEB_MAX_BYTES:-900000000}"

PASSED=0
FAILED=0
REPORT=()

rand() { head -c 96 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c "$1"; }

AUTH_JWT_SECRET="$(rand 48)"
POSTGRES_PASSWORD="$(rand 24)"
ADMIN_EMAIL="admin@smoke.test"
ADMIN_PASSWORD="Sm0ke-$(rand 20)"
{
  echo "AUTH_JWT_SECRET=$AUTH_JWT_SECRET"
  echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD"
  echo "SEC_USER_AGENT=${SEC_USER_AGENT:-SOTP Intelligence smoke test smoke@example.com}"
} > "$ENVFILE"

dc() { docker compose -p "$PROJECT" --env-file "$ENVFILE" "$@"; }

# ---------------------------------------------------------------- reporting
clean() {  # one line, ASCII, safe inside a GitHub workflow command, limited length
  printf '%s' "$1" | tr '\r\n' '  ' | sed -e 's/%/%25/g' -e 's/::/- -/g' | LC_ALL=C tr -cd '[:print:]' | cut -c1-"${2:-900}"
}
annotate() { [ -n "${GITHUB_ACTIONS:-}" ] && echo "::error title=smoke: $(clean "$1" 120)::$(clean "$2")"; true; }
ok()  { PASSED=$((PASSED + 1)); echo "PASS  $1${2:+  [$2]}"; REPORT+=("PASS | $1 | $(clean "${2:-}" 300)"); }
bad() { FAILED=$((FAILED + 1)); echo "FAIL  $1${2:+  [$2]}"; REPORT+=("FAIL | $1 | $(clean "${2:-}" 600)"); annotate "$1" "${2:-}"; }
step() { echo; echo "== $1"; }

finish() {
  echo; echo "== result: $PASSED passed, $FAILED failed"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      echo "### Docker smoke test: $PASSED passed, $FAILED failed"; echo
      echo "| Result | Check | Evidence |"; echo "|---|---|---|"
      for line in "${REPORT[@]}"; do
        IFS='|' read -r r c e <<<"$line"; echo "| ${r// /} |${c} |${e//|/\\|} |"
      done
      echo; echo '```'; [ -f "$TMP/images.txt" ] && cat "$TMP/images.txt"; echo '```'
    } >> "$GITHUB_STEP_SUMMARY"
  fi
  if [ "$FAILED" -gt 0 ] && [ -f "$TMP/logs.txt" ]; then
    annotate "container logs (tail)" "$(tail -c 700 "$TMP/logs.txt")"
  fi
  if [ "$KEEP" != "1" ]; then dc down -v --remove-orphans >/dev/null 2>&1; else echo "(KEEP=1: stack left running; project $PROJECT)"; fi
  rm -rf "$TMP"
  [ "$FAILED" -eq 0 ]
  exit $?
}

dump_logs() { dc logs --no-color --tail 60 > "$TMP/logs.txt" 2>&1; tail -n 60 "$TMP/logs.txt"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing required tool: $1"; exit 2; }; }
need docker; need curl; need python3
docker compose version >/dev/null 2>&1 || { echo "docker compose plugin not found"; exit 2; }
json() { python3 -c "import sys,json; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

# ================================================================ 1. build, up, healthy
step "1. docker compose build / up / healthy"
dc down -v --remove-orphans >/dev/null 2>&1
if dc build >"$TMP/build.log" 2>&1; then
  ok "docker compose build" "backend and web images built"
else
  bad "docker compose build" "$(tail -c 800 "$TMP/build.log")"; finish
fi
if dc up -d >"$TMP/up.log" 2>&1; then
  ok "docker compose up -d" "containers started"
else
  bad "docker compose up -d" "$(tail -c 800 "$TMP/up.log")"; dump_logs; finish
fi

health_of() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' "$(dc ps -q "$1")" 2>/dev/null; }
deadline=$((SECONDS + 300))
while :; do
  statuses=""; all=1
  for svc in db backend web; do
    h="$(health_of "$svc")"; statuses="$statuses $svc=$h"
    [ "$h" = "healthy" ] || all=0
    [ "$h" = "unhealthy" ] && { all=2; break; }
  done
  [ "$all" = 1 ] && break
  if [ "$all" = 2 ] || [ "$SECONDS" -ge "$deadline" ]; then
    bad "services healthy" "$statuses"; dump_logs; finish
  fi
  sleep 3
done
ok "all services healthy" "$statuses"

# ================================================================ 2. migrations
step "2. Alembic migrations"
dc logs --no-color backend > "$TMP/backend.log" 2>&1
upgrades="$(grep -c 'Running upgrade' "$TMP/backend.log")"
if [ "$upgrades" -gt 0 ]; then ok "migrations ran in the backend container" "$upgrades 'Running upgrade' lines"; else bad "migrations ran in the backend container" "no 'Running upgrade' lines in the backend log"; fi
if grep -E 'Traceback|\bERROR\b|\bFAILED\b|CRITICAL' "$TMP/backend.log" >"$TMP/errs.txt"; then bad "no errors in backend logs" "$(head -c 500 "$TMP/errs.txt")"; else ok "no errors in backend logs" "$(wc -l < "$TMP/backend.log") log lines"; fi
head_rev="$(dc exec -T backend alembic -c backend/alembic.ini heads 2>/dev/null | awk '{print $1}' | head -1)"
db_rev="$(dc exec -T db psql -U postgres -d sotp_intelligence -tAc 'select version_num from alembic_version' 2>/dev/null | tr -d '[:space:]')"
if [ -n "$head_rev" ] && [ "$head_rev" = "$db_rev" ]; then ok "database is at the Alembic head" "$db_rev"; else bad "database is at the Alembic head" "head=$head_rev database=$db_rev"; fi

# ================================================================ 3. bootstrap admin
step "3. bootstrap the first admin"
out="$(dc exec -T -e SOTP_NEW_PASSWORD="$ADMIN_PASSWORD" backend python scripts/create_user.py --email "$ADMIN_EMAIL" --bootstrap-admin 2>&1)"; rc=$?
if [ "$rc" -eq 0 ] && grep -q 'Created first admin' <<<"$out"; then ok "bootstrap-admin creates the first admin" "$(clean "$out" 160)"; else bad "bootstrap-admin creates the first admin" "rc=$rc $out"; fi
out2="$(dc exec -T -e SOTP_NEW_PASSWORD="$ADMIN_PASSWORD" backend python scripts/create_user.py --email second@smoke.test --bootstrap-admin 2>&1)"; rc2=$?
if [ "$rc2" -ne 0 ] && grep -q 'active admin already exists' <<<"$out2"; then ok "a second bootstrap-admin is refused" "rc=$rc2"; else bad "a second bootstrap-admin is refused" "rc=$rc2 $out2"; fi

# ================================================================ 4. through the web app
step "4. web app flow on $WEB"
code="$(curl -s -o /dev/null -w '%{http_code}' "$WEB/login")"
[ "$code" = "200" ] && ok "GET /login" "HTTP $code" || bad "GET /login" "HTTP $code"
read -r rcode rloc < <(curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' "$WEB/AAPL/dcf")
[ "$rcode" = "307" ] && [[ "$rloc" == */login* ]] && ok "signed-out pages redirect to /login" "HTTP $rcode -> $rloc" || bad "signed-out pages redirect to /login" "HTTP $rcode -> $rloc"

curl -si -c "$JAR" -X POST "$WEB/api/auth/login" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$ADMIN_EMAIL\",\"password\":\"$ADMIN_PASSWORD\"}" > "$TMP/login.txt"
status="$(head -1 "$TMP/login.txt" | awk '{print $2}')"
cookie_line="$(grep -i '^set-cookie: sotp_session=' "$TMP/login.txt" | tr -d '\r')"
[ "$status" = "200" ] && ok "sign in through /api/auth/login" "HTTP $status" || bad "sign in through /api/auth/login" "HTTP $status $(tail -1 "$TMP/login.txt")"
attrs="$(sed -E 's/^[Ss]et-[Cc]ookie: sotp_session=[^;]*//' <<<"$cookie_line")"
grep -qi 'httponly' <<<"$attrs"          && ok "session cookie is HttpOnly" "$attrs" || bad "session cookie is HttpOnly" "$attrs"
grep -qi 'samesite=strict' <<<"$attrs"   && ok "session cookie is SameSite=Strict" "$attrs" || bad "session cookie is SameSite=Strict" "$attrs"
if grep -qiE '(^|;)[[:space:]]*secure' <<<"$attrs"; then bad "session cookie works over plain http (no Secure flag)" "$attrs"; else ok "session cookie works over plain http (no Secure flag)" "SESSION_COOKIE_SECURE defaults to false in compose; $attrs"; fi
grep -q 'access_token' "$TMP/login.txt" && bad "login response body carries no token" "access_token found in the body" || ok "login response body carries no token" "only subject and roles"
me="$(curl -s -b "$JAR" -w '\n%{http_code}' "$WEB/api/auth/me")"
[ "$(tail -1 <<<"$me")" = "200" ] && ok "cookie is sent on the next request" "$(head -1 <<<"$me")" || bad "cookie is sent on the next request" "$me"

page="$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' "$WEB/AAPL")"
[ "$page" = "200" ] && ok "company page /AAPL loads" "HTTP $page" || bad "company page /AAPL loads" "HTTP $page"
if [ "${SMOKE_SKIP_SEC:-0}" = "1" ]; then
  echo "SKIP  company data from SEC (SMOKE_SKIP_SEC=1)"
else
  comp="$(curl -s -b "$JAR" --max-time 180 -w '\n%{http_code}' "$WEB/api/backend/companies/AAPL")"
  ccode="$(tail -1 <<<"$comp")"; cname="$(head -1 <<<"$comp" | json "d.get('name')")"
  [ "$ccode" = "200" ] && [ -n "$cname" ] && ok "company data for AAPL via the proxy (real SEC call)" "HTTP $ccode name=$cname" || bad "company data for AAPL via the proxy (real SEC call)" "HTTP $ccode $(head -c 300 <<<"$comp")"
fi

dcf_page="$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' "$WEB/AAPL/dcf")"
[ "$dcf_page" = "200" ] && ok "DCF page /AAPL/dcf loads" "HTTP $dcf_page" || bad "DCF page /AAPL/dcf loads" "HTTP $dcf_page"
DCF_BODY='{"base_revenue":1000,"revenue_growth_rates":[0.1,0.1,0.08,0.06,0.05],"ebit_margins":[0.2,0.2,0.2,0.2,0.2],"tax_rate":0.25,"da_pct_of_revenue":[0.04,0.04,0.04,0.04,0.04],"capex_pct_of_revenue":[0.05,0.05,0.05,0.05,0.05],"nwc_change_pct_of_revenue":[0.01,0.01,0.01,0.01,0.01],"wacc":0.09,"terminal_growth_rate":0.025,"total_debt":200,"cash_and_equivalents":100,"diluted_shares_outstanding":100}'
dcf="$(curl -s -b "$JAR" -w '\n%{http_code}' -X POST "$WEB/api/backend/valuation/dcf" -H 'Content-Type: application/json' -d "$DCF_BODY")"
dcode="$(tail -1 <<<"$dcf")"; ev="$(head -1 <<<"$dcf" | json "round(d['enterprise_value'],2)")"; px="$(head -1 <<<"$dcf" | json "round(d['implied_price_per_share'],2)")"
[ "$dcode" = "200" ] && [ -n "$ev" ] && ok "run a DCF valuation" "HTTP $dcode enterprise_value=$ev price=$px" || bad "run a DCF valuation" "HTTP $dcode $(head -c 300 <<<"$dcf")"

curl -si -b "$JAR" -c "$JAR" -X POST "$WEB/api/auth/logout" > "$TMP/logout.txt"
lstatus="$(head -1 "$TMP/logout.txt" | awk '{print $2}')"
grep -i '^set-cookie: sotp_session=' "$TMP/logout.txt" | grep -qi 'max-age=0' && cleared=1 || cleared=0
[ "$lstatus" = "200" ] && [ "$cleared" = 1 ] && ok "sign out clears the session cookie" "HTTP $lstatus, Max-Age=0" || bad "sign out clears the session cookie" "HTTP $lstatus cleared=$cleared"
after="$(curl -s -b "$JAR" -o /dev/null -w '%{http_code}' "$WEB/api/backend/valuation/company/1/assumptions")"
[ "$after" = "401" ] && ok "API calls after sign out are refused" "HTTP $after" || bad "API calls after sign out are refused" "HTTP $after"

# ================================================================ 5. exposure and hygiene
step "5. exposure, users, secrets, size"
bport="$(dc port backend 8000 2>/dev/null)"
[[ "$bport" == 127.0.0.1:* ]] && ok "backend is published on loopback only" "$bport" || bad "backend is published on loopback only" "'$bport'"
dport="$(dc port db 5432 2>/dev/null)"
[ -z "$dport" ] && ok "database port is not published" "no host binding" || bad "database port is not published" "$dport"
wport="$(dc port web 3000 2>/dev/null)"
[ -n "$wport" ] && ok "web is published (intended)" "$wport" || bad "web is published (intended)" "no binding"
HOSTIP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [ -n "$HOSTIP" ] && [[ "$HOSTIP" != 127.* ]]; then
  curl -s -o /dev/null --max-time 5 "http://$HOSTIP:8000/health" && bad "backend is NOT reachable on the host's external address" "http://$HOSTIP:8000 answered" || ok "backend is NOT reachable on the host's external address" "http://$HOSTIP:8000 refused"
  curl -s -o /dev/null --max-time 5 "http://$HOSTIP:5432" && bad "database is NOT reachable on the host's external address" "http://$HOSTIP:5432 answered" || ok "database is NOT reachable on the host's external address" "http://$HOSTIP:5432 refused"
  curl -s -o /dev/null --max-time 5 "http://$HOSTIP:3000/login" && ok "web IS reachable on the host's external address (intended)" "http://$HOSTIP:3000" || bad "web IS reachable on the host's external address (intended)" "http://$HOSTIP:3000 refused"
else
  echo "SKIP  external-address reachability (no non-loopback address found)"
fi
curl -s -o /dev/null -w '' --max-time 5 "$API/health" && ok "backend answers on loopback (intended)" "$API/health" || bad "backend answers on loopback (intended)" "$API/health did not answer"

for svc in db backend web; do
  uid="$(dc exec -T "$svc" sh -c "awk '/^Uid:/{print \$2}' /proc/1/status" 2>/dev/null | tr -d '[:space:]')"
  [ -n "$uid" ] && [ "$uid" != "0" ] && ok "$svc runs as a non-root user" "PID 1 uid=$uid" || bad "$svc runs as a non-root user" "PID 1 uid='$uid'"
done

for svc in backend web; do
  img="$(docker inspect -f '{{.Image}}' "$(dc ps -q "$svc")")"
  hist="$(docker history --no-trunc --format '{{.CreatedBy}}' "$img" 2>&1)"
  envs="$(docker image inspect -f '{{json .Config.Env}}' "$img")"
  leaked=""
  for secret in "$AUTH_JWT_SECRET" "$POSTGRES_PASSWORD" "$ADMIN_PASSWORD"; do
    grep -qF -- "$secret" <<<"$hist$envs" && leaked="$leaked a-generated-secret"
  done
  if grep -qiE '(API_KEY|SECRET|PASSWORD|TOKEN)=[^[:space:]"]+' <<<"$hist$envs"; then leaked="$leaked a-KEY/SECRET/PASSWORD/TOKEN=value-in-layers-or-env"; fi
  [ -z "$leaked" ] && ok "no secret in $svc image layers or image env" "$(wc -l <<<"$hist") layers inspected" || bad "no secret in $svc image layers or image env" "$leaked"
  files="$(dc exec -T "$svc" sh -c 'ls -A /app' 2>/dev/null | grep -E '^(\.env.*|\.git|\.github|tests?)$' | tr '\n' ' ')"
  [ -z "$files" ] && ok "no .env/.git/tests in the $svc image" "/app: $(dc exec -T "$svc" sh -c 'ls -A /app' 2>/dev/null | tr '\n' ' ' | cut -c1-120)" || bad "no .env/.git/tests in the $svc image" "$files"
done

{ echo "image sizes:"; docker image ls --format '  {{.Repository}}:{{.Tag}}  {{.Size}}' | grep "$PROJECT" || true; } | tee "$TMP/images.txt"
bsize="$(docker image inspect -f '{{.Size}}' "$(docker inspect -f '{{.Image}}' "$(dc ps -q backend)")")"
wsize="$(docker image inspect -f '{{.Size}}' "$(docker inspect -f '{{.Image}}' "$(dc ps -q web)")")"
mb() { echo $(( $1 / 1000000 )); }
[ "$bsize" -le "$BACKEND_MAX_BYTES" ] && ok "backend image size" "$(mb "$bsize") MB (limit $(mb "$BACKEND_MAX_BYTES") MB)" || bad "backend image size" "$(mb "$bsize") MB exceeds $(mb "$BACKEND_MAX_BYTES") MB"
[ "$wsize" -le "$WEB_MAX_BYTES" ] && ok "web image size" "$(mb "$wsize") MB (limit $(mb "$WEB_MAX_BYTES") MB)" || bad "web image size" "$(mb "$wsize") MB exceeds $(mb "$WEB_MAX_BYTES") MB"

[ "$FAILED" -gt 0 ] && dump_logs >/dev/null
finish
