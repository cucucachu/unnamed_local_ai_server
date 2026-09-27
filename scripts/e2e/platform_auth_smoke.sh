#!/usr/bin/env bash
# M10-03: live accounts/sessions round-trip against the running `platform`.
#
# Caddy doesn't route to the platform yet (M10-04), so every request goes
# from a throwaway `curlimages/curl` container on `homeai_homeai-internal`
# straight to `platform:8100`, playing Caddy's part by hand: it calls
# `/internal/auth/verify` with the client's cookie/bearer and forwards the
# returned `X-HomeAI-Identity` to `/api/platform/*`.
#
# Never completes bootstrap (the real first admin must be the human who
# uses the setup code). Instead:
#   1. `GET /api/auth/status`; while setup is still required, the setup code
#      must be in `docker compose logs platform` and /data/platform/setup-code,
#      and a wrong code must get 401 `invalid_setup_code`.
#   2. The recovery CLI creates a throwaway member `e2e-auth-<random>`.
#   3. Web login (cookie: HttpOnly, SameSite=Lax) and native login (token).
#   4. verify accepts the cookie and the bearer and returns an identity JWT;
#      `/api/platform/me` with that identity is the new member;
#      `/api/platform/admin/users` is 403 `admin_required`.
#   5. Logout; verify with the old cookie is 401.
# The member and their personal space (row and directory) are deleted on
# exit (EXIT trap, via psql as the superuser).
#
# Usage: scripts/e2e/platform_auth_smoke.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

NETWORK="homeai_homeai-internal"
CURL_IMAGE="curlimages/curl:latest"
BASE="http://platform:8100"
USERNAME="e2e-auth-$(openssl rand -hex 4)"
PASSWORD="$(openssl rand -hex 16)"
POSTGRES_USER="$(grep -E '^POSTGRES_USER=' .env | tail -n1 | cut -d= -f2- || true)"
POSTGRES_USER="${POSTGRES_USER:-homeai}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cleanup() {
  local ids
  ids="$(docker compose exec -T postgres psql -qtA -U "$POSTGRES_USER" -d homeai_platform \
    -c "SELECT s.id FROM spaces s JOIN users u ON u.id = s.owner_user_id
      WHERE u.username = '$USERNAME'" 2>/dev/null || true)"
  docker compose exec -T postgres psql -q -U "$POSTGRES_USER" -d homeai_platform \
    -c "DELETE FROM spaces s USING users u WHERE u.id = s.owner_user_id
      AND u.username = '$USERNAME'; DELETE FROM users WHERE username = '$USERNAME'" \
    >/dev/null 2>&1 || true
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && docker compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
}
trap cleanup EXIT

# curl_i ARGS... -> full response (status line + headers + body) on stdout.
curl_i() {
  docker run --rm --network "$NETWORK" "$CURL_IMAGE" -sS -i --max-time 10 "$@"
}
status_of() { head -n1 <<<"$1" | awk '{print $2}'; }
header_of() { grep -i "^$2:" <<<"$1" | head -n1 | cut -d' ' -f2- | tr -d '\r'; }
body_of() { awk 'BEGIN{b=0} b{print} /^\r?$/{b=1}' <<<"$1"; }
json_get() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval('d'+sys.argv[1]))" "$1"; }

expect_status() {
  local want="$1" response="$2" what="$3"
  local got
  got="$(status_of "$response")"
  [[ "$got" == "$want" ]] || fail "$what: expected HTTP $want, got $got: $(body_of "$response")"
  echo "ok   $what -> $got"
}

echo "== waiting for platform health"
for _ in $(seq 1 30); do
  if docker compose exec -T platform python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8100/internal/health', timeout=3)" \
    >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "== 1. status + setup code"
r="$(curl_i "$BASE/api/auth/status")"
expect_status 200 "$r" "GET /api/auth/status"
setup_required="$(body_of "$r" | json_get '["setup_required"]')"
echo "     setup_required=$setup_required"
if [[ "$setup_required" == "True" ]]; then
  code="$(docker compose exec -T platform cat /data/platform/setup-code | tr -d '\r\n')"
  [[ "$code" =~ ^[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}$ ]] || fail "setup-code file: '$code'"
  docker compose logs platform | grep -q "HOME AI SETUP CODE: $code" ||
    fail "setup code not in docker compose logs platform"
  echo "ok   setup code present in /data/platform/setup-code and the logs"
  r="$(curl_i -H 'Content-Type: application/json' \
    -d '{"setup_code":"AAAA-AAAA-AAAA-AAAA","username":"e2e-never","display_name":"x","password":"xxxxxxxxxx"}' \
    "$BASE/api/auth/setup")"
  expect_status 401 "$r" "POST /api/auth/setup (wrong code)"
  [[ "$(body_of "$r")" == '{"detail":"invalid_setup_code"}' ]] || fail "setup detail: $(body_of "$r")"
else
  echo "     (bootstrap already complete; skipping setup-code checks)"
fi

echo "== 2. CLI creates $USERNAME"
printf '%s\n' "$PASSWORD" | docker compose exec -T platform \
  python -m app.cli create-user "$USERNAME" --display-name "E2E Auth" --password-stdin
docker compose exec -T platform python -m app.cli list-users | grep -q "^$USERNAME " ||
  fail "list-users doesn't show $USERNAME"
echo "ok   list-users shows $USERNAME"

login_body="{\"username\":\"$USERNAME\",\"password\":\"$PASSWORD\",\"device_label\":\"e2e smoke\"}"

echo "== 3. login"
r="$(curl_i -H 'Content-Type: application/json' -d "$login_body" "$BASE/api/auth/login")"
expect_status 200 "$r" "POST /api/auth/login (web)"
set_cookie="$(header_of "$r" set-cookie)"
grep -qi 'HttpOnly' <<<"$set_cookie" || fail "cookie not HttpOnly: $set_cookie"
grep -qi 'SameSite=lax' <<<"$set_cookie" || fail "cookie not SameSite=Lax: $set_cookie"
cookie="${set_cookie%%;*}"
[[ "$cookie" == homeai_session=hs_* ]] || fail "unexpected cookie: $cookie"

r="$(curl_i -H 'Content-Type: application/json' -H 'X-HomeAI-Client: native' -H 'X-Forwarded-Proto: https' \
  -d "$login_body" "$BASE/api/auth/login")"
expect_status 200 "$r" "POST /api/auth/login (native)"
token="$(body_of "$r" | json_get '["session_token"]')"
[[ "$token" == hs_* ]] || fail "native login returned no session_token"

echo "== 4. verify + identity"
r="$(curl_i -H "Cookie: $cookie" "$BASE/internal/auth/verify")"
expect_status 200 "$r" "GET /internal/auth/verify (cookie)"
identity="$(header_of "$r" x-homeai-identity)"
[[ -n "$identity" ]] || fail "no X-HomeAI-Identity header"
r="$(curl_i -H "Authorization: Bearer $token" "$BASE/internal/auth/verify")"
expect_status 200 "$r" "GET /internal/auth/verify (bearer)"

r="$(curl_i -H "X-HomeAI-Identity: $identity" "$BASE/api/platform/me")"
expect_status 200 "$r" "GET /api/platform/me (identity)"
[[ "$(body_of "$r" | json_get '["username"]')" == "$USERNAME" ]] || fail "me: $(body_of "$r")"
r="$(curl_i -H "X-HomeAI-Identity: $identity" "$BASE/api/platform/admin/users")"
expect_status 403 "$r" "GET /api/platform/admin/users (member)"
r="$(curl_i -H "X-HomeAI-Identity: forged.jwt.value" "$BASE/api/platform/me")"
expect_status 401 "$r" "GET /api/platform/me (forged identity)"

echo "== 5. logout"
r="$(curl_i -X POST -H "Cookie: $cookie" "$BASE/api/auth/logout")"
expect_status 204 "$r" "POST /api/auth/logout"
r="$(curl_i -H "Cookie: $cookie" "$BASE/internal/auth/verify")"
expect_status 401 "$r" "GET /internal/auth/verify (after logout)"
r="$(curl_i -H "Authorization: Bearer $token" "$BASE/internal/auth/verify")"
expect_status 200 "$r" "GET /internal/auth/verify (other session unaffected)"

echo "PASS: platform auth smoke"
