#!/usr/bin/env bash
# verify_tenancy.sh — M11-04: docs/PLATFORM.md §9 security invariants 1-7
# (7 since M12-08), scripted against the live stack.
#
#   1. No authenticated route without a session; client-supplied
#      `X-HomeAI-Identity` (forged, or another user's genuine token) and
#      delegation tokens are stripped/ignored at Caddy; `/internal/*` is
#      not routed.                                              checks 1-4
#   2. User B can't read, list or write user A's personal space — through
#      the files API as B, with B's delegation (what B's agent file tools
#      carry), or from B's exec container.                      checks 5-8
#   3. A viewer can't write to a space by the same paths.        checks 9-11
#   4. An agent run (an `act=agent` delegation) can't perform admin or
#      auth/session/membership actions, even for a stepped-up admin whose
#      own identity token can.                                  check 12
#   5. agent-server has no user-data mounts and no credentials for the
#      platform database (compose config, the live container, and a
#      connection attempt with its own credentials).             checks 13-16
#   6. Only code-exec-manager holds docker.sock; agent-server and platform
#      have read-only roots; an exec container has no network, no socket,
#      and exactly the binds its grants list.                    checks 17-19
#   7. App sandboxes hold no credentials: A's runner for the shared
#      instance, in headless Chromium, is `sandbox="allow-scripts"` with
#      `connect-src 'none'`, no cookie/token in its document, and nothing
#      in it can reach cookies, storage, the parent or the network
#      (scripts/e2e/app_sandbox_tenancy.mjs).                   check 20
#      An instance's RPC only touches that instance's database, for its
#      space's members at their role: another space's member gets 404, a
#      viewer's writes 403, an `act=agent` delegation has exactly its
#      user's rights, one instance's URL can't reach another instance's
#      rows or file, and the bundle endpoint needs space read.  checks 21-25
#
# For invariant 7, A installs the reference Grocery list app
# (examples/apps/grocery-list) in its personal space and in the shared one,
# builds both with the real builder, and adds a marker row to each. That is
# also invariant 2/3 for app RPC; `app_sql` arrives with M13 and gets its
# checks then. The agent's file tools are checked here at the
# platform boundary (B's own delegation, the credential PlatformFilesBackend
# sends); scripts/e2e/agent_tenancy_smoke.sh drives the same thing through
# the real model.
#
# Users: `e2e-ten-a-*` owns shared space `e2e-ten-<hex>`, `e2e-ten-b-*`
# views it, and `e2e-ten-adm-*` is an admin (recovery CLI, `lib/auth.sh`;
# bootstrap is never completed). A plants a marker file in its personal
# space and another in the shared one. Browser-facing calls go through Caddy
# on http://localhost; platform-internal calls (delegations, exec grants,
# the exec manager) go from a runner container on `homeai-internal`
# (`lib/internal.sh`), the way agent-server and code-exec-manager make them.
# Secrets (service tokens, cookies, tokens) are passed by environment and
# never printed. Everything is deleted on exit: exec sessions and
# containers, the runner, the shared space and its directory, the users,
# their personal spaces and threads, the apps, instances and bundles.
#
# Any failing check prints in red and the suite keeps going, then exits 1.
# Needs no sudo; check 20 needs Node and Playwright's Chromium (installed
# into scripts/e2e like the browser smokes), and the builder image
# (services/app-builder/build-builder-image.sh).
#
# Usage: scripts/verify_tenancy.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=e2e/lib/auth.sh
source "${SCRIPT_DIR}/e2e/lib/auth.sh"
# shellcheck source=e2e/lib/internal.sh
source "${SCRIPT_DIR}/e2e/lib/internal.sh"

HEX="$(openssl rand -hex 3)"
SPACE="e2e-ten-${HEX}"
PMARK="ten-personal-$(openssl rand -hex 4)"
SMARK="ten-shared-$(openssl rand -hex 4)"
APP_PMARK="app-personal-$(openssl rand -hex 4)"
APP_SMARK="app-shared-$(openssl rand -hex 4)"
APP_DIR="${REPO_ROOT}/examples/apps/grocery-list"
APP_IDS=()
SESSION_A="tenancy-a-$$"
SESSION_B="tenancy-b-$$"
RUNNER_NAME="verify-tenancy-runner-$$"
BASE="${E2E_BASE:-http://localhost}"
WS_BASE="${BASE/http/ws}/ws"

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
NC=$'\033[0m'
PASS_COUNT=0
FAIL_COUNT=0
FAILED_CHECKS=()
ERRORS=()

log() { echo "[verify-tenancy] $(date '+%H:%M:%S') $*"; }

# Each check collects mismatches in ERRORS, then reports once.
finish() {
  local num="$1" desc="$2"
  if [ "${#ERRORS[@]}" -eq 0 ]; then
    PASS_COUNT=$((PASS_COUNT + 1))
    printf '%sPASS%s [%2d] %s\n' "$GREEN" "$NC" "$num" "$desc"
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILED_CHECKS+=("$num")
    printf '%sFAIL%s [%2d] %s\n' "$RED" "$NC" "$num" "$desc"
    local e
    for e in "${ERRORS[@]}"; do printf '%s       -> %s%s\n' "$RED" "$e" "$NC"; done
  fi
  ERRORS=()
}

# ---- HTTP ------------------------------------------------------------------

# argv: method url [body]; env E2E_HEADERS (JSON object), E2E_TIMEOUT
# (seconds, default 20), E2E_BODY_MAX (default 1500 characters). Prints
# {"status", "body", "identity"} — identity: the response carried an
# X-HomeAI-Identity header.
HTTP_PY="$(cat <<'EOF'
import json, os, sys, urllib.error, urllib.request
method, url = sys.argv[1:3]
body = sys.argv[3] if len(sys.argv) > 3 else ""
headers = json.loads(os.environ.get("E2E_HEADERS") or "{}")
data = None
if body:
    data = body.encode()
    headers.setdefault("Content-Type", "application/json")
elif method in ("POST", "PUT", "PATCH"):
    data = b""
req = urllib.request.Request(url, data=data, method=method, headers=headers)
try:
    with urllib.request.urlopen(req, timeout=float(os.environ.get("E2E_TIMEOUT") or 20)) as r:
        status, text, names = r.status, r.read().decode(errors="replace"), list(r.headers.keys())
except urllib.error.HTTPError as e:
    status, text, names = e.code, e.read().decode(errors="replace"), list(e.headers.keys())
except urllib.error.URLError as e:
    status, text, names = 0, str(e.reason), []
identity = "x-homeai-identity" in {n.lower() for n in names}
print(json.dumps({"status": status, "body": text[:int(os.environ.get("E2E_BODY_MAX") or 1500)], "identity": identity}))
EOF
)"

# Builds a headers object from name/value pairs; empty values are dropped.
headers() {
  python3 -c '
import json, sys
a = sys.argv[1:]
print(json.dumps({a[i]: a[i + 1] for i in range(0, len(a), 2) if a[i + 1]}))' "$@"
}
cookie() { headers Cookie "$1"; }
bearer() { headers Authorization "Bearer $1"; }

# $1 method, $2 path (from /api or /internal), $3 headers JSON, $4 body.
via_caddy() {
  E2E_HEADERS="$3" python3 -c "$HTTP_PY" "$1" "${BASE}$2" "${4:-}"
}

# Same, from the runner straight to platform:8100.
via_internal() {
  E2E_HEADERS="$3" INTERNAL_PY_ENV=E2E_HEADERS internal_py "$HTTP_PY" "$1" "http://platform:8100$2" "${4:-}"
}

q() { python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe="/"))' "$1"; }
field() { python3 -c 'import json, sys; print(json.loads(sys.argv[1])[sys.argv[2]])' "$1" "$2"; }
jbody() { python3 -c 'import json, sys; d = json.loads(json.loads(sys.argv[1])["body"]); print(eval(sys.argv[2]))' "$1" "$2"; }

# $1 wanted status (or a|b|c alternatives), $2 response JSON, $3 label, $4 wanted detail.
want() {
  local status body
  status="$(field "$2" status)"
  body="$(field "$2" body)"
  if [[ "|$1|" != *"|$status|"* ]]; then
    ERRORS+=("$3: expected HTTP $1, got $status: ${body:0:300}")
  elif [ -n "${4:-}" ] && [[ "$body" != *"\"$4\""* ]]; then
    ERRORS+=("$3: expected detail $4, got ${body:0:300}")
  fi
}

# $1 response JSON, $2 python expression over `d` (the parsed body), $3 label.
want_body() {
  local out
  if ! out="$(jbody "$1" "$2" 2>&1)" || [ "$out" != True ]; then
    ERRORS+=("$3: $(field "$1" status) ${out:0:200}: $(field "$1" body | head -c 300)")
  fi
}

# ---- setup / teardown ------------------------------------------------------

cli() { _e2e_compose exec -T platform python -m app.cli "$@" >/dev/null; }

# Prints the identity token Caddy would set for a session cookie.
identity_of() {
  E2E_CALL_COOKIE="$1" INTERNAL_PY_ENV="E2E_CALL_COOKIE" internal_py "${_INTERNAL_DELEGATION_PY}
sys.stdout.write(identity())"
}

exec_as() {
  local cookie="$1" session="$2" command="$3" body
  body="$(python3 -c 'import json, sys; print(json.dumps({"command": sys.argv[1], "timeout_seconds": 15}))' "$command")"
  internal_exec_manager_call "$cookie" POST "$session" execute delegation 15 "$body"
}

setup() {
  local network
  network="$(internal_network)"
  docker network ls --format '{{.Name}}' | grep -qx "$network" || { log "ERROR: network ${network} not found - is the stack up?"; exit 1; }
  PLATFORM_AGENT_TOKEN="$(_e2e_env_value PLATFORM_AGENT_TOKEN "")"
  PLATFORM_EXEC_TOKEN="$(_e2e_env_value PLATFORM_EXEC_TOKEN "")"
  export PLATFORM_AGENT_TOKEN PLATFORM_EXEC_TOKEN
  [ -n "$PLATFORM_AGENT_TOKEN" ] && [ -n "$PLATFORM_EXEC_TOKEN" ] || { log "ERROR: PLATFORM_AGENT_TOKEN/PLATFORM_EXEC_TOKEN not set in .env"; exit 1; }

  log "Creating users A, B (viewer of ${SPACE}, owned by A) and an admin ..."
  e2e_auth_create_user ten-a
  USER_A="$E2E_NEW_USER"
  COOKIE_A="$(e2e_auth_login "$USER_A" "$E2E_NEW_PASSWORD")"
  e2e_auth_create_user ten-b
  USER_B="$E2E_NEW_USER"
  COOKIE_B="$(e2e_auth_login "$USER_B" "$E2E_NEW_PASSWORD")"
  e2e_auth_create_user ten-adm
  USER_ADM="$E2E_NEW_USER"
  PASSWORD_ADM="$E2E_NEW_PASSWORD"
  cli set-role "$USER_ADM" admin
  COOKIE_ADM="$(e2e_auth_login "$USER_ADM" "$PASSWORD_ADM")"
  cli create-space "$SPACE" --name "E2E tenancy" --owner "$USER_A"
  cli add-member "$SPACE" "$USER_B" --role viewer

  read -r HOME_A SLUG_A <<<"$(_e2e_psql homeai_platform "SELECT s.id || ' ' || s.slug FROM spaces s
    JOIN users u ON u.id = s.owner_user_id WHERE s.kind = 'personal' AND u.username = '${USER_A}'")"
  HOME_B="$(_e2e_psql homeai_platform "SELECT s.id FROM spaces s JOIN users u ON u.id = s.owner_user_id
    WHERE s.kind = 'personal' AND u.username = '${USER_B}'")"
  SPACE_ID="$(_e2e_psql homeai_platform "SELECT id FROM spaces WHERE slug = '${SPACE}'")"
  USER_B_ID="$(_e2e_psql homeai_platform "SELECT id FROM users WHERE username = '${USER_B}'")"
  USER_ADM_ID="$(_e2e_psql homeai_platform "SELECT id FROM users WHERE username = '${USER_ADM}'")"
  local id
  for id in "$HOME_A" "$HOME_B" "$SPACE_ID" "$USER_B_ID" "$USER_ADM_ID"; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] || { log "ERROR: couldn't resolve the test users' ids (got '${id}')"; exit 1; }
  done
  SHARED="/spaces/${SPACE}"
  A_FOREIGN="/spaces/${SLUG_A}"

  log "Starting runner ${RUNNER_NAME} on ${network} ..."
  internal_runner_start "$RUNNER_NAME" "$network" || { log "ERROR: runner never became exec-able"; exit 1; }
  IDENT_A="$(identity_of "$COOKIE_A")"
  DELEG_A="$(internal_delegation "$COOKIE_A" "$SESSION_A")"
  DELEG_B="$(internal_delegation "$COOKIE_B" "$SESSION_B")"
  [ -n "$IDENT_A" ] && [ -n "$DELEG_A" ] && [ -n "$DELEG_B" ] || { log "ERROR: couldn't mint tokens"; exit 1; }

  log "A plants ${PMARK} in /personal and ${SMARK} in ${SHARED} ..."
  local r
  r="$(via_caddy POST /api/platform/files/write "$(cookie "$COOKIE_A")" \
    "{\"path\": \"/personal/${PMARK}.txt\", \"content\": \"${PMARK}\"}")"
  [ "$(field "$r" status)" = 200 ] || { log "ERROR: A's personal write: $r"; exit 1; }
  r="$(via_caddy POST /api/platform/files/write "$(cookie "$COOKIE_A")" \
    "{\"path\": \"${SHARED}/${SMARK}.txt\", \"content\": \"${SMARK}\"}")"
  [ "$(field "$r" status)" = 200 ] || { log "ERROR: A's shared write: $r"; exit 1; }
  log "OK: A=${USER_A}, B=${USER_B}, admin=${USER_ADM}; A's personal slug ${SLUG_A}"
}

# ---- invariant 7 setup: the reference app in A's personal and shared space --

# $1 virtual root (/personal or /spaces/<slug>), $2 space id, $3 marker row.
# Sets APP_ID / INSTANCE_ID.
install_grocery() {
  local dir="$1/Apps/grocery-list" space_id="$2" marker="$3" rel body r
  while IFS= read -r rel; do
    body="$(python3 -c 'import json, sys; print(json.dumps({"path": sys.argv[1], "content": open(sys.argv[2]).read()}))' \
      "${dir}/${rel}" "${APP_DIR}/${rel}")"
    r="$(via_caddy POST /api/platform/files/write "$(cookie "$COOKIE_A")" "$body")"
    [ "$(field "$r" status)" = 200 ] || { log "ERROR: upload ${dir}/${rel}: ${r:0:300}"; return 1; }
  done < <(cd "$APP_DIR" && find . -type f | sed 's|^\./||' | sort)
  r="$(via_caddy POST /api/platform/apps "$(cookie "$COOKIE_A")" "{\"source_path\": \"${dir}\"}")"
  [ "$(field "$r" status)" = 201 ] || { log "ERROR: register ${dir}: ${r:0:300}"; return 1; }
  APP_ID="$(jbody "$r" "d['app']['id']")"
  APP_IDS+=("$APP_ID")
  r="$(via_caddy POST "/api/platform/spaces/${space_id}/instances" "$(cookie "$COOKIE_A")" "{\"app_id\": \"${APP_ID}\"}")"
  [ "$(field "$r" status)" = 201 ] || { log "ERROR: install ${dir}: ${r:0:300}"; return 1; }
  INSTANCE_ID="$(jbody "$r" "d['id']")"
  r="$(E2E_TIMEOUT=300 E2E_BODY_MAX=100000 via_caddy POST "/api/platform/apps/${APP_ID}/build" "$(cookie "$COOKIE_A")" '{}')"
  [ "$(jbody "$r" "d['ok'] and d['diagnostics'] == []" 2>/dev/null)" = True ] || { log "ERROR: build ${dir}: ${r:0:500}"; return 1; }
  r="$(via_caddy POST "/api/platform/apps/instances/${INSTANCE_ID}/rpc" "$(cookie "$COOKIE_A")" \
    "{\"op\": \"run\", \"sql\": \"INSERT INTO items (name) VALUES (?)\", \"params\": [\"${marker}\"]}")"
  [ "$(field "$r" status)" = 200 ] || { log "ERROR: marker row in ${dir}: ${r:0:300}"; return 1; }
}

setup_apps() {
  log "A installs and builds ${APP_DIR#"${REPO_ROOT}/"} in /personal and ${SHARED} ..."
  install_grocery /personal "$HOME_A" "$APP_PMARK" || return 1
  INST_P="$INSTANCE_ID"
  install_grocery "$SHARED" "$SPACE_ID" "$APP_SMARK" || return 1
  APP_S="$APP_ID" INST_S="$INSTANCE_ID"
  DELEG_A="$(internal_delegation "$COOKIE_A" "${SESSION_A}-apps")"
  DELEG_B="$(internal_delegation "$COOKIE_B" "${SESSION_B}-apps")"
  DELEG_ADM="$(internal_delegation "$COOKIE_ADM" "tenancy-adm-apps-$$")"
  [ -n "$DELEG_A" ] && [ -n "$DELEG_B" ] && [ -n "$DELEG_ADM" ] || { log "ERROR: couldn't mint delegations"; return 1; }
  log "OK: personal instance ${INST_P}, shared instance ${INST_S}"
}

cleanup() {
  local id
  for id in "${APP_IDS[@]}"; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/${id}" >/dev/null 2>&1 || true
  done
  if [ -n "$INTERNAL_RUNNER" ]; then
    [ -n "${COOKIE_A:-}" ] && internal_exec_manager_call "$COOKIE_A" DELETE "$SESSION_A" delete delegation 10 >/dev/null
    [ -n "${COOKIE_B:-}" ] && internal_exec_manager_call "$COOKIE_B" DELETE "$SESSION_B" delete delegation 10 >/dev/null
    internal_runner_stop
  fi
  docker rm -f "homeai-exec-${SESSION_A}" "homeai-exec-${SESSION_B}" >/dev/null 2>&1 || true
  if [[ "${SPACE_ID:-}" =~ ^[0-9a-f-]{36}$ ]]; then
    _e2e_psql homeai_platform "DELETE FROM spaces WHERE id = '${SPACE_ID}'" >/dev/null 2>&1 || true
    _e2e_compose exec -T platform rm -rf "/data/spaces/${SPACE_ID}" >/dev/null 2>&1 || true
  fi
  e2e_auth_end
}
trap cleanup EXIT

# ---- invariant 1: sessions, identity headers, Caddy ------------------------

ws_status() {
  uvx --from websockets python - "$WS_BASE" "$1" <<'PY' 2>&1 | tail -n1
import asyncio, json, sys, uuid
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

base, extra = sys.argv[1], json.loads(sys.argv[2])


async def main() -> None:
    try:
        async with connect(f"{base}/chat/{uuid.uuid4()}", additional_headers=extra):
            print("open")
    except InvalidStatus as e:
        print(f"http {e.response.status_code}")


asyncio.run(main())
PY
}

check_1() {
  local path
  for path in /api/threads /api/settings /api/platform/me "/api/platform/files?path=/personal" /api/platform/spaces; do
    want 401 "$(via_caddy GET "$path" '{}')" "GET ${path} without a session"
  done
  want 401 "$(via_caddy POST /api/platform/files/write '{}' '{"path": "/personal/x", "content": "x"}')" \
    "POST files/write without a session"
  local ws
  ws="$(ws_status '{}')"
  [ "$ws" = "http 401" ] || ERRORS+=("WS /ws/chat upgrade without a session: '${ws}'")
  finish 1 "no session: /api/*, /api/platform/* and the chat socket are 401 at Caddy"
}

check_2() {
  local path h
  for h in "$(headers X-HomeAI-Identity "$IDENT_A")" "$(headers X-HomeAI-Identity forged.not.ajwt)" \
    "$(headers X-HomeAI-Identity "$DELEG_A")" "$(bearer "$DELEG_A")" "$(bearer "$IDENT_A")"; do
    for path in /api/threads /api/platform/me "/api/platform/files?path=/personal"; do
      want 401 "$(via_caddy GET "$path" "$h")" "GET ${path}, no session, with $(python3 -c 'import json,sys; k=next(iter(json.loads(sys.argv[1]))); print(k)' "$h")"
    done
  done
  local ws
  ws="$(ws_status "$(headers X-HomeAI-Identity "$IDENT_A")")"
  [ "$ws" = "http 401" ] || ERRORS+=("WS upgrade with only A's identity token: '${ws}'")
  finish 2 "no session: A's genuine identity token, a forged one, or A's delegation (header or Bearer) are 401"
}

check_3() {
  local r h
  for h in "$(headers Cookie "$COOKIE_B" X-HomeAI-Identity "$IDENT_A")" \
    "$(headers Cookie "$COOKIE_B" X-HomeAI-Identity "$DELEG_A")" \
    "$(headers Cookie "$COOKIE_B" Authorization "Bearer $DELEG_A")"; do
    r="$(via_caddy GET /api/platform/me "$h")"
    want 200 "$r" "B's session + A's token: /api/platform/me"
    want_body "$r" "d['username'] == '${USER_B}'" "B's session + A's token: /api/platform/me is B"
    want 404 "$(via_caddy GET "/api/platform/files/stat?path=$(q "/personal/${PMARK}.txt")" "$h")" \
      "B's session + A's token: stat A's file as /personal"
  done
  finish 3 "with B's session, A's identity/delegation sent alongside is replaced: every call is B's"
}

check_4() {
  local r
  r="$(via_caddy GET /internal/jwks '{}')"
  [[ "$(field "$r" body)" != *'"keys"'* ]] || ERRORS+=("/internal/jwks is served through Caddy")
  r="$(via_caddy GET /internal/auth/verify "$(cookie "$COOKIE_A")")"
  [ "$(field "$r" identity)" = False ] || ERRORS+=("/internal/auth/verify through Caddy returned an identity header")
  r="$(via_caddy POST /internal/delegations "$(bearer "$PLATFORM_AGENT_TOKEN")" \
    "{\"identity_token\": \"${IDENT_A}\", \"thread_id\": \"x\"}")"
  [[ "$(field "$r" body)" != *'"token"'* ]] || ERRORS+=("/internal/delegations is reachable through Caddy")
  r="$(via_caddy POST /internal/exec-grants "$(bearer "$PLATFORM_EXEC_TOKEN")" "{\"delegation\": \"${DELEG_A}\"}")"
  [[ "$(field "$r" body)" != *'"mounts"'* ]] || ERRORS+=("/internal/exec-grants is reachable through Caddy")
  finish 4 "the platform's /internal/* routes are not reachable through Caddy"
}

# ---- invariant 2: B vs A's personal space ----------------------------------

# $1 transport (via_caddy|via_internal), $2 headers JSON, $3 label.
b_cannot_touch_a() {
  local t="$1" h="$2" who="$3" p f
  for p in "$A_FOREIGN" "${A_FOREIGN}/${PMARK}.txt"; do
    want 404 "$($t GET "/api/platform/files?path=$(q "$p")" "$h")" "${who}: list ${p}" not_found
    want 404 "$($t GET "/api/platform/files/stat?path=$(q "$p")" "$h")" "${who}: stat ${p}" not_found
  done
  f="${A_FOREIGN}/${PMARK}.txt"
  want 404 "$($t GET "/api/platform/files/download?path=$(q "$f")" "$h")" "${who}: download ${f}" not_found
  want 404 "$($t GET "/api/platform/files/stream?path=$(q "$f")" "$h")" "${who}: stream ${f}" not_found
  want 404 "$($t POST /api/platform/files/read "$h" "{\"path\": \"${f}\"}")" "${who}: read ${f}" not_found
  want 404 "$($t POST /api/platform/files/write "$h" "{\"path\": \"${A_FOREIGN}/b.txt\", \"content\": \"b\"}")" \
    "${who}: write into ${A_FOREIGN}" not_found
  want 404 "$($t POST /api/platform/files/edit "$h" "{\"path\": \"${f}\", \"old_string\": \"${PMARK}\", \"new_string\": \"b\"}")" \
    "${who}: edit ${f}" not_found
  want 404 "$($t POST /api/platform/files/mkdir "$h" "{\"path\": \"${A_FOREIGN}/d\"}")" "${who}: mkdir in ${A_FOREIGN}" not_found
  want 404 "$($t POST /api/platform/files/copy "$h" "{\"src\": \"${f}\", \"dst\": \"/personal/stolen.txt\"}")" \
    "${who}: copy ${f} -> own /personal" not_found
  want 404 "$($t POST /api/platform/files/move "$h" "{\"src\": \"${f}\", \"dst\": \"/personal/stolen.txt\"}")" \
    "${who}: move ${f} -> own /personal" not_found
  want 404 "$($t DELETE "/api/platform/files?path=$(q "$f")" "$h")" "${who}: delete ${f}" not_found
  want 404 "$($t GET "/api/platform/files/stat?path=$(q "/personal/${PMARK}.txt")" "$h")" "${who}: stat /personal/${PMARK}.txt" not_found
  want "404|422" "$($t GET "/api/platform/files/stat?path=$(q "/personal/../spaces/${SLUG_A}/${PMARK}.txt")" "$h")" \
    "${who}: stat via /personal/../spaces/${SLUG_A}"
  local r
  r="$($t POST /api/platform/files/grep "$h" "{\"pattern\": \"${PMARK}\"}")"
  want 200 "$r" "${who}: grep ${PMARK}"
  want_body "$r" "'${PMARK}.txt' not in json.dumps(d)" "${who}: grep finds A's file"
  r="$($t POST /api/platform/files/glob "$h" "{\"pattern\": \"**/${PMARK}*\"}")"
  want 200 "$r" "${who}: glob ${PMARK}"
  want_body "$r" "'${PMARK}' not in json.dumps(d)" "${who}: glob finds A's file"
  r="$($t GET "/api/platform/files?path=/spaces" "$h")"
  want_body "$r" "'${SLUG_A}' not in json.dumps(d)" "${who}: /spaces lists A's personal slug"
  want 404 "$($t GET "/api/platform/spaces/${HOME_A}" "$h")" "${who}: GET A's personal space"
  want 404 "$($t GET "/api/platform/spaces/${HOME_A}/members" "$h")" "${who}: A's personal space members"
  want 404 "$($t GET "/api/platform/spaces/${HOME_A}/instances" "$h")" "${who}: A's personal app instances"
}

a_file_intact() {
  local r
  r="$(via_caddy POST /api/platform/files/read "$(cookie "$COOKIE_A")" "{\"path\": \"/personal/${PMARK}.txt\"}")"
  want 200 "$r" "A reads its own file afterwards"
  want_body "$r" "'${PMARK}' in json.dumps(d)" "A's file content unchanged"
  r="$(via_caddy GET "/api/platform/files?path=/personal" "$(cookie "$COOKIE_A")")"
  want_body "$r" "sorted(e['name'] for e in d['entries']) == ['${PMARK}.txt']" "A's /personal holds only its file"
}

check_5() {
  b_cannot_touch_a via_caddy "$(cookie "$COOKIE_B")" "B (files API)"
  a_file_intact
  finish 5 "files API as B (via Caddy): A's personal space is 404 for every operation; A's file intact"
}

check_6() {
  b_cannot_touch_a via_internal "$(bearer "$DELEG_B")" "B's delegation"
  a_file_intact
  finish 6 "B's delegation (the agent file tools' credential): A's personal space is 404 for every operation"
}

check_7() {
  local ensure r grants
  ensure="$(internal_exec_manager_call "$COOKIE_B" POST "$SESSION_B" ensure delegation 30)"
  [[ "$ensure" == *container_id* ]] || { ERRORS+=("B's ensure failed: ${ensure}"); finish 7 "B's exec container can't see A's personal space"; return; }
  r="$(exec_as "$COOKIE_B" "$SESSION_B" "ls /files/spaces; echo ---; ls -a /files/personal; echo ---; grep -rl ${PMARK} /files 2>/dev/null; true")"
  python3 -c "
import json, sys
r = json.loads(sys.argv[1])
assert r.get('exit_code') == 0, r
spaces, personal, hits = r['stdout'].split('---')
assert spaces.split() == ['${SPACE}'], f'/files/spaces: {spaces.split()}'
assert '${PMARK}' not in personal and not hits.strip(), f'A marker visible: {personal!r} {hits!r}'
" "$r" 2>/dev/null || ERRORS+=("B's container: ${r:0:400}")
  grants="$(E2E_DELEG="$DELEG_B" INTERNAL_PY_ENV="E2E_DELEG PLATFORM_EXEC_TOKEN" internal_py '
import json, os, urllib.request
req = urllib.request.Request("http://platform:8100/internal/exec-grants", method="POST",
    data=json.dumps({"delegation": os.environ["E2E_DELEG"]}).encode(),
    headers={"Authorization": "Bearer " + os.environ["PLATFORM_EXEC_TOKEN"], "Content-Type": "application/json"})
print(urllib.request.urlopen(req, timeout=15).read().decode())' 2>&1)"
  python3 -c "
import json, sys
g = json.loads(sys.argv[1])
got = sorted((m['container_path'], m['read_only']) for m in g['mounts'])
assert got == [('/files/personal', False), ('/files/spaces/${SPACE}', True)], got
assert not any('${HOME_A}' in m['host_path'] for m in g['mounts']), g['mounts']
" "$grants" 2>/dev/null || ERRORS+=("B's exec grants: ${grants:0:400}")
  GRANTS_B="$grants"
  finish 7 "B's exec container and grants: only B's /files/personal and ${SHARED} (ro); A's marker nowhere"
}

check_8() {
  local ensure_a r
  ensure_a="$(internal_exec_manager_call "$COOKIE_A" POST "$SESSION_A" ensure delegation 30)"
  [[ "$ensure_a" == *container_id* ]] || ERRORS+=("A's ensure failed: ${ensure_a}")
  r="$(internal_exec_manager_call "$COOKIE_B" POST "$SESSION_A" ensure delegation 30)"
  [[ "$r" == *'"http_error": 403'* ]] || ERRORS+=("B ensure on A's session: ${r:0:300}")
  r="$(internal_exec_manager_call "$COOKIE_B" POST "$SESSION_A" execute delegation 15 '{"command": "cat /files/personal/*"}')"
  [[ "$r" == *'"http_error": 403'* ]] || ERRORS+=("B execute on A's session: ${r:0:300}")
  r="$(internal_exec_manager_call "$COOKIE_B" POST "$SESSION_A" execute "delegation-for:${SESSION_B}" 15 '{"command": "id"}')"
  [[ "$r" == *'"http_error": 403'* ]] || ERRORS+=("B execute on A's session with B's own thread delegation: ${r:0:300}")
  finish 8 "B can't ensure or execute in A's exec session (403)"
}

# ---- invariant 3: a viewer can't write ---------------------------------------

# $1 transport, $2 headers, $3 label.
viewer_cannot_write() {
  local t="$1" h="$2" who="$3" f="${SHARED}/${SMARK}.txt"
  want 200 "$($t POST /api/platform/files/read "$h" "{\"path\": \"${f}\"}")" "${who}: read ${f} (control)"
  want 403 "$($t POST /api/platform/files/write "$h" "{\"path\": \"${SHARED}/v.txt\", \"content\": \"v\"}")" \
    "${who}: write" insufficient_role
  want 403 "$($t POST /api/platform/files/write "$h" "{\"path\": \"${f}\", \"content\": \"v\"}")" \
    "${who}: overwrite ${f}" insufficient_role
  want 403 "$($t POST /api/platform/files/edit "$h" "{\"path\": \"${f}\", \"old_string\": \"${SMARK}\", \"new_string\": \"v\"}")" \
    "${who}: edit" insufficient_role
  want 403 "$($t POST /api/platform/files/mkdir "$h" "{\"path\": \"${SHARED}/vdir\"}")" "${who}: mkdir" insufficient_role
  want 403 "$($t POST /api/platform/files/rename "$h" "{\"path\": \"${f}\", \"name\": \"v.txt\"}")" "${who}: rename" insufficient_role
  want 403 "$($t POST /api/platform/files/copy "$h" "{\"src\": \"${f}\", \"dst\": \"${SHARED}/v.txt\"}")" \
    "${who}: copy within" insufficient_role
  want 403 "$($t POST /api/platform/files/move "$h" "{\"src\": \"${f}\", \"dst\": \"/personal/v.txt\"}")" \
    "${who}: move out" insufficient_role
  want 403 "$($t DELETE "/api/platform/files?path=$(q "$f")" "$h")" "${who}: delete" insufficient_role
}

shared_intact() {
  local r
  r="$(via_caddy GET "/api/platform/files?path=$(q "$SHARED")" "$(cookie "$COOKIE_A")")"
  want_body "$r" "sorted(e['name'] for e in d['entries']) == ['${SMARK}.txt']" "${SHARED} unchanged"
  r="$(via_caddy POST /api/platform/files/read "$(cookie "$COOKIE_A")" "{\"path\": \"${SHARED}/${SMARK}.txt\"}")"
  want_body "$r" "'${SMARK}' in json.dumps(d)" "${SMARK}.txt content unchanged"
}

check_9() {
  viewer_cannot_write via_caddy "$(cookie "$COOKIE_B")" "viewer B (files API)"
  shared_intact
  finish 9 "files API as viewer B: every write to ${SHARED} is 403 insufficient_role"
}

check_10() {
  viewer_cannot_write via_internal "$(bearer "$DELEG_B")" "viewer B's delegation"
  shared_intact
  finish 10 "viewer B's delegation: every write to ${SHARED} is 403 insufficient_role"
}

check_11() {
  local r
  r="$(exec_as "$COOKIE_B" "$SESSION_B" "cat /files/spaces/${SPACE}/${SMARK}.txt; echo x >> /files/spaces/${SPACE}/${SMARK}.txt; touch /files/spaces/${SPACE}/v; echo rc=\$?")"
  python3 -c "
import json, sys
r = json.loads(sys.argv[1])
out = r.get('stdout', '')
assert out.startswith('${SMARK}'), out
assert 'rc=0' not in out, out
assert 'Read-only file system' in r.get('stderr', ''), r.get('stderr')
" "$r" 2>/dev/null || ERRORS+=("B's container: ${r:0:400}")
  shared_intact
  finish 11 "viewer B's exec container: ${SHARED} readable, writes fail (read-only mount)"
}

# ---- invariant 4: agent runs never administer --------------------------------

check_12() {
  local r h_adm deleg_adm sid
  r="$(via_caddy POST /api/auth/step-up "$(cookie "$COOKIE_ADM")" "{\"password\": \"${PASSWORD_ADM}\"}")"
  want 200 "$r" "admin steps up"
  want 200 "$(via_caddy GET /api/platform/admin/users "$(cookie "$COOKIE_ADM")")" "admin's own session: GET admin/users (control)"
  deleg_adm="$(internal_delegation "$COOKIE_ADM" "tenancy-adm-$$")"
  h_adm="$(bearer "$deleg_adm")"
  want 200 "$(via_internal GET /api/platform/me "$h_adm")" "admin's delegation: GET /me (control)"
  want 403 "$(via_internal GET /api/platform/admin/users "$h_adm")" "admin's delegation: GET admin/users" agent_not_allowed
  want 403 "$(via_internal GET /api/platform/admin/spaces "$h_adm")" "admin's delegation: GET admin/spaces" agent_not_allowed
  want 403 "$(via_internal GET /api/platform/admin/invites "$h_adm")" "admin's delegation: GET admin/invites" agent_not_allowed
  want 403 "$(via_internal POST /api/platform/admin/invites "$h_adm" '{"label": "e2e-ten"}')" \
    "admin's delegation: POST admin/invites" agent_not_allowed
  want 403 "$(via_internal PATCH "/api/platform/admin/users/${USER_B_ID}" "$h_adm" '{"role": "admin"}')" \
    "admin's delegation: PATCH admin/users/B role=admin" agent_not_allowed
  r="$(_e2e_psql homeai_platform "SELECT role FROM users WHERE id = '${USER_B_ID}'")"
  [ "$r" = member ] || ERRORS+=("B's role is now '${r}'")
  want 403 "$(via_internal GET /api/platform/me/sessions "$h_adm")" "admin's delegation: GET me/sessions" agent_not_allowed
  sid="$(_e2e_psql homeai_platform "SELECT id FROM sessions WHERE user_id = '${USER_ADM_ID}' AND revoked_at IS NULL LIMIT 1")"
  want 403 "$(via_internal DELETE "/api/platform/me/sessions/${sid}" "$h_adm")" "admin's delegation: revoke a session" agent_not_allowed
  want 403 "$(via_internal PATCH /api/platform/me "$h_adm" '{"display_name": "pwned"}')" "admin's delegation: PATCH /me" agent_not_allowed
  want 403 "$(via_internal POST /api/platform/me/totp/enroll "$h_adm" "{\"password\": \"${PASSWORD_ADM}\"}")" \
    "admin's delegation: TOTP enroll" agent_not_allowed
  want 403 "$(via_internal POST /api/platform/spaces "$h_adm" "{\"slug\": \"${SPACE}-x\", \"name\": \"x\"}")" \
    "admin's delegation: create a space" agent_not_allowed
  want 403 "$(via_internal GET /api/platform/users/directory "$h_adm")" "admin's delegation: user directory" agent_not_allowed
  want 403 "$(via_internal POST "/api/platform/spaces/${SPACE_ID}/members" "$(bearer "$DELEG_A")" \
    "{\"user_id\": \"${USER_ADM_ID}\", \"role\": \"editor\"}")" "owner A's delegation: add a member" agent_not_allowed
  want 403 "$(via_internal PATCH "/api/platform/spaces/${SPACE_ID}" "$(bearer "$DELEG_A")" '{"name": "renamed"}')" \
    "owner A's delegation: rename the space" agent_not_allowed
  want 403 "$(via_internal PATCH "/api/platform/spaces/${SPACE_ID}/members/${USER_B_ID}" "$(bearer "$DELEG_A")" '{"role": "editor"}')" \
    "owner A's delegation: promote viewer B" agent_not_allowed
  want 401 "$(via_internal POST /api/auth/step-up "$h_adm" "{\"password\": \"${PASSWORD_ADM}\"}")" "admin's delegation: step-up"
  via_internal POST /api/auth/logout "$h_adm" >/dev/null
  r="$(E2E_DELEG="$deleg_adm" INTERNAL_PY_ENV="E2E_DELEG PLATFORM_AGENT_TOKEN" internal_py '
import json, os, urllib.error, urllib.request
req = urllib.request.Request("http://platform:8100/internal/delegations", method="POST",
    data=json.dumps({"identity_token": os.environ["E2E_DELEG"], "thread_id": "x"}).encode(),
    headers={"Authorization": "Bearer " + os.environ["PLATFORM_AGENT_TOKEN"], "Content-Type": "application/json"})
try:
    print(urllib.request.urlopen(req, timeout=15).status)
except urllib.error.HTTPError as e:
    print(e.code)' 2>&1)"
  [ "$r" = 401 ] || ERRORS+=("a delegation exchanged for a new delegation: ${r}")
  want 200 "$(via_caddy GET /api/platform/admin/users "$(cookie "$COOKIE_ADM")")" \
    "admin's own session afterwards: still signed in and stepped up"
  r="$(_e2e_psql homeai_platform "SELECT count(*) FROM space_members WHERE space_id = '${SPACE_ID}'")"
  [ "$r" = 2 ] || ERRORS+=("${SPACE} has ${r} members, expected 2")
  finish 12 "act=agent: admin, session, TOTP, profile, directory and membership calls are 403 even for a stepped-up admin"
}

# ---- invariant 5: agent-server holds no user data or platform credentials ----

# Env file values, never printed: POSTGRES_PASSWORD, PLATFORM_DB_PASSWORD, PLATFORM_EXEC_TOKEN,
# PLATFORM_BUILD_TOKEN.
SECRETS_PY="$(cat <<'EOF'
import json, pathlib, re, sys
env = {}
for line in pathlib.Path(".env").read_text().splitlines():
    m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
    if m:
        env[m.group(1)] = m.group(2).strip()
forbidden = {k: env[k] for k in ("POSTGRES_PASSWORD", "PLATFORM_DB_PASSWORD", "PLATFORM_EXEC_TOKEN", "PLATFORM_BUILD_TOKEN") if env.get(k)}
superuser = env.get("POSTGRES_USER", "homeai")
EOF
)"

check_13() {
  local out
  if ! out="$(docker compose config --format json | python3 -c "${SECRETS_PY}
svc = json.load(sys.stdin)['services']['agent-server']
errors = []
if svc.get('volumes'):
    errors.append(f\"volumes: {[v.get('target') for v in svc['volumes']]}\")
envs = svc.get('environment') or {}
for name, value in forbidden.items():
    if name in envs and name != 'POSTGRES_PASSWORD':
        errors.append(f'{name} is set')
    if any(v == value for v in envs.values()):
        errors.append(f\"an env var carries {name}'s value\")
if envs.get('POSTGRES_USER') in (superuser, 'platform'):
    errors.append(f\"POSTGRES_USER={envs.get('POSTGRES_USER')}\")
print('; '.join(errors))
sys.exit(1 if errors else 0)
" 2>&1)"; then
    ERRORS+=("$out")
  fi
  finish 13 "compose: agent-server has no volumes, the non-superuser role, and no superuser/platform/exec secrets"
}

check_14() {
  local cid out
  cid="$(docker compose ps -q agent-server)"
  if ! out="$(docker inspect "$cid" | python3 -c "${SECRETS_PY}
c = json.load(sys.stdin)[0]
errors = []
if c.get('Mounts'):
    errors.append(f\"mounts: {[m.get('Destination') for m in c['Mounts']]}\")
values = [e.split('=', 1)[1] for e in c['Config']['Env'] if '=' in e]
for name, value in forbidden.items():
    if value in values:
        errors.append(f\"the container's env carries {name}'s value\")
print('; '.join(errors))
sys.exit(1 if errors else 0)
" 2>&1)"; then
    ERRORS+=("$out")
  fi
  finish 14 "live agent-server container: no mounts, no superuser/platform/exec secrets in its env"
}

check_15() {
  local out
  out="$(_e2e_compose exec -T agent-server /app/.venv/bin/python - <<'PY' 2>&1
import psycopg
from app.core.config import Settings

s = Settings()
base = s.postgres_dsn.rsplit("/", 1)[0]
with psycopg.connect(s.postgres_dsn) as conn:
    role, superuser = conn.execute(
        "SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
    print(f"own-db role={role} superuser={superuser}")
for db in ("homeai_platform", "postgres", "template1"):
    try:
        psycopg.connect(f"{base}/{db}", connect_timeout=5).close()
        print(f"{db}=CONNECTED")
    except psycopg.OperationalError as e:
        print(f"{db}={'denied' if 'permission denied' in str(e) else 'error: ' + str(e).splitlines()[0]}")
PY
)"
  local line
  for line in "own-db role=agent superuser=False" "homeai_platform=denied" "postgres=denied" "template1=denied"; do
    grep -qxF "$line" <<<"$out" || ERRORS+=("expected '${line}' in: $(tr '\n' ' ' <<<"$out")")
  done
  finish 15 "agent-server's own DB credentials: role 'agent' (not superuser), refused by homeai_platform/postgres/template1"
}

check_16() {
  local ip roles
  ip="$(docker inspect "$(docker compose ps -q agent-server)" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' | xargs)"
  roles="$(_e2e_psql postgres "SELECT string_agg(DISTINCT usename || '@' || coalesce(datname, '-'), ',')
    FROM pg_stat_activity WHERE host(client_addr) = '${ip%% *}'")"
  [ "$roles" = "agent@$(_e2e_env_value POSTGRES_DB homeai)" ] || ERRORS+=("agent-server (${ip}) connections: '${roles}'")
  finish 16 "every live Postgres connection from agent-server is role 'agent' on its own database"
}

# ---- invariant 6: docker.sock, read-only roots, exec containers --------------

check_17() {
  local out
  out="$(bash "${REPO_ROOT}/scripts/check_socket_exclusivity.sh" 2>&1)" || ERRORS+=("$out")
  finish 17 "only code-exec-manager mounts docker.sock (check_socket_exclusivity.sh)"
}

check_18() {
  local svc cfg ro
  cfg="$(docker compose config --format json)"
  for svc in agent-server platform; do
    ro="$(python3 -c 'import json, sys; print(json.loads(sys.argv[1])["services"][sys.argv[2]].get("read_only", False))' "$cfg" "$svc")"
    [ "$ro" = True ] || ERRORS+=("compose: ${svc} read_only=${ro}")
    ro="$(docker inspect -f '{{.HostConfig.ReadonlyRootfs}}' "$(docker compose ps -q "$svc")")"
    [ "$ro" = true ] || ERRORS+=("live ${svc}: ReadonlyRootfs=${ro}")
  done
  finish 18 "agent-server and platform run with read-only root filesystems (compose and live)"
}

check_19() {
  local out r
  if ! out="$(docker inspect "homeai-exec-${SESSION_B}" | python3 -c "
import json, sys
c = json.load(sys.stdin)[0]
grants = json.loads(sys.argv[1])
hc = c['HostConfig']
errors = []
if hc.get('NetworkMode') != 'none':
    errors.append(f\"NetworkMode={hc.get('NetworkMode')}\")
if hc.get('ReadonlyRootfs') is not True or hc.get('Privileged') or hc.get('CapDrop') != ['ALL']:
    errors.append('not read-only/unprivileged/cap-dropped')
binds = sorted((m['Source'], m['Destination'], not m['RW']) for m in c['Mounts'] if m['Type'] == 'bind')
want = sorted((m['host_path'], m['container_path'], m['read_only']) for m in grants['mounts'])
if binds != want:
    errors.append(f'binds {binds} != grants {want}')
if any('docker.sock' in (m.get('Source') or '') + (m.get('Destination') or '') for m in c['Mounts']):
    errors.append('docker.sock mounted')
print('; '.join(errors))
sys.exit(1 if errors else 0)
" "${GRANTS_B:-{\}}" 2>&1)"; then
    ERRORS+=("docker inspect: $out")
  fi
  r="$(exec_as "$COOKIE_B" "$SESSION_B" 'ls /sys/class/net; ls /var/run/docker.sock /run/docker.sock 2>/dev/null; true')"
  python3 -c "import json, sys; r = json.loads(sys.argv[1]); assert r['stdout'].split() == ['lo'], r" "$r" 2>/dev/null ||
    ERRORS+=("inside: ${r:0:300}")
  finish 19 "B's exec container: network none, no docker.sock, binds exactly its exec grants"
}

# ---- invariant 7: app sandboxes hold no credentials; RPC is scoped ------------

check_20() {
  local out line secrets
  secrets="$(printf '%s\n' "${COOKIE_A#homeai_session=}" "$IDENT_A" "$DELEG_A" "$COOKIE_B" "$PLATFORM_AGENT_TOKEN")"
  if ! (cd "${SCRIPT_DIR}/e2e" && { [ -d node_modules/playwright ] || npm install >/dev/null 2>&1; } &&
    npx playwright install chromium >/dev/null 2>&1); then
    ERRORS+=("couldn't install Playwright/Chromium into scripts/e2e")
  else
    out="$(TEN_BASE="$BASE" TEN_COOKIE="$COOKIE_A" TEN_INSTANCE="$INST_S" TEN_OTHER_INSTANCE="$INST_P" \
      TEN_MARK="$APP_SMARK" TEN_OTHER_MARK="$APP_PMARK" TEN_SECRETS="$secrets" \
      node "${SCRIPT_DIR}/e2e/app_sandbox_tenancy.mjs" 2>&1)" || ERRORS+=("app_sandbox_tenancy.mjs exited non-zero")
    while IFS= read -r line; do
      case "$line" in
        ok*) log "  ${line}" ;;
        FAIL*) ERRORS+=("${line#FAIL }") ;;
        *) [ -n "$line" ] && ERRORS+=("${line:0:300}") ;;
      esac
    done <<<"$out"
  fi
  finish 20 "A's runner (Chromium): allow-scripts frame, connect-src 'none', no credential in or reachable from the sandbox"
}

# $1 transport, $2 headers, $3 label, $4 instance id, $5 wanted status, $6 wanted detail.
rpc_all_ops() {
  local t="$1" h="$2" who="$3" iid="$4" st="$5" detail="${6:-}" rpc="/api/platform/apps/instances/$4/rpc"
  want "$st" "$($t POST "$rpc" "$h" '{"op": "getAll", "sql": "SELECT name FROM items"}')" "${who}: getAll" "$detail"
  want "$st" "$($t POST "$rpc" "$h" '{"op": "getFirst", "sql": "SELECT count(*) AS n FROM items"}')" "${who}: getFirst" "$detail"
  want "$st" "$($t POST "$rpc" "$h" '{"op": "run", "sql": "DELETE FROM items"}')" "${who}: run" "$detail"
  want "$st" "$($t POST "$rpc" "$h" '{"op": "transaction", "statements": [{"sql": "DELETE FROM items"}]}')" "${who}: transaction" "$detail"
  want "$st" "$($t POST "$rpc" "$h" '{"op": "action", "name": "clearChecked", "params": {}}')" "${who}: action clearChecked" "$detail"
  want "$st" "$($t POST "$rpc" "$h" '{"op": "action", "name": "addItem", "params": {"name": "x"}}')" "${who}: action addItem" "$detail"
  want "$st" "$($t POST "/api/platform/apps/instances/${iid}/migrate" "$h" '{}')" "${who}: migrate" "$detail"
}

# $1 instance id, $2 wanted names (python list literal).
rows_are() {
  local r
  r="$(via_caddy POST "/api/platform/apps/instances/$1/rpc" "$(cookie "$COOKIE_A")" \
    '{"op": "getAll", "sql": "SELECT name FROM items ORDER BY id"}')"
  want_body "$r" "[x['name'] for x in d['rows']] == $2" "rows of instance $1"
}
apps_intact() {
  rows_are "$INST_P" "['${APP_PMARK}']"
  rows_are "$INST_S" "['${APP_SMARK}']"
}

check_21() {
  rpc_all_ops via_caddy "$(cookie "$COOKIE_B")" "B (shared member) on A's personal instance" "$INST_P" 404 not_found
  want 404 "$(via_caddy GET "/api/platform/apps/instances/${INST_P}/migrations" "$(cookie "$COOKIE_B")")" \
    "B: A's personal instance migrations" not_found
  rpc_all_ops via_caddy "$(cookie "$COOKIE_ADM")" "admin (member of neither) on the shared instance" "$INST_S" 404 not_found
  rpc_all_ops via_caddy "$(cookie "$COOKIE_ADM")" "admin (member of neither) on A's personal instance" "$INST_P" 404 not_found
  want 401 "$(via_caddy POST "/api/platform/apps/instances/${INST_S}/rpc" '{}' '{"op": "getAll", "sql": "SELECT 1"}')" \
    "no session: RPC"
  apps_intact
  finish 21 "RPC with a session from outside the instance's space is 404 for every op (B on A's personal, a non-member admin)"
}

check_22() {
  local h rpc="/api/platform/apps/instances/${INST_S}/rpc" r
  h="$(cookie "$COOKIE_B")"
  r="$(via_caddy POST "$rpc" "$h" '{"op": "getAll", "sql": "SELECT name FROM items"}')"
  want 200 "$r" "viewer B: getAll (control)"
  want_body "$r" "[x['name'] for x in d['rows']] == ['${APP_SMARK}']" "viewer B reads the shared rows"
  want 403 "$(via_caddy POST "$rpc" "$h" '{"op": "run", "sql": "DELETE FROM items"}')" "viewer B: run" insufficient_role
  want 403 "$(via_caddy POST "$rpc" "$h" '{"op": "run", "sql": "SELECT 1"}')" "viewer B: run (a read)" insufficient_role
  want 403 "$(via_caddy POST "$rpc" "$h" '{"op": "transaction", "statements": [{"sql": "DELETE FROM items"}]}')" \
    "viewer B: transaction" insufficient_role
  want 403 "$(via_caddy POST "$rpc" "$h" '{"op": "action", "name": "clearChecked", "params": {}}')" \
    "viewer B: action clearChecked" insufficient_role
  want 403 "$(via_caddy POST "$rpc" "$h" '{"op": "action", "name": "addItem", "params": {"name": "v"}}')" \
    "viewer B: action addItem" insufficient_role
  want 422 "$(via_caddy POST "$rpc" "$h" '{"op": "getAll", "sql": "DELETE FROM items RETURNING id"}')" \
    "viewer B: a write inside getAll" sql_not_allowed
  want 403 "$(via_caddy POST "/api/platform/apps/instances/${INST_S}/migrate" "$h" '{}')" "viewer B: migrate" insufficient_role
  want 403 "$(E2E_TIMEOUT=60 via_caddy POST "/api/platform/apps/${APP_S}/build" "$h" '{}')" "viewer B: build the app" insufficient_role
  apps_intact
  finish 22 "viewer B on the shared instance: reads work, every write (run, transaction, actions, migrate, build) is 403"
}

check_23() {
  local r
  r="$(via_internal POST "/api/platform/apps/instances/${INST_S}/rpc" "$(bearer "$DELEG_B")" '{"op": "getAll", "sql": "SELECT name FROM items"}')"
  want 200 "$r" "B's delegation: getAll on the shared instance (control)"
  want_body "$r" "[x['name'] for x in d['rows']] == ['${APP_SMARK}']" "B's delegation reads the shared rows"
  want 403 "$(via_internal POST "/api/platform/apps/instances/${INST_S}/rpc" "$(bearer "$DELEG_B")" '{"op": "run", "sql": "DELETE FROM items"}')" \
    "B's delegation: run on the shared instance" insufficient_role
  want 403 "$(via_internal POST "/api/platform/apps/instances/${INST_S}/rpc" "$(bearer "$DELEG_B")" \
    '{"op": "action", "name": "clearChecked", "params": {}}')" "B's delegation: action on the shared instance" insufficient_role
  rpc_all_ops via_internal "$(bearer "$DELEG_B")" "B's delegation on A's personal instance" "$INST_P" 404 not_found
  want 404 "$(via_internal GET "/api/platform/apps/instances/${INST_P}/bundle" "$(bearer "$DELEG_B")")" \
    "B's delegation: A's personal bundle" not_found
  rpc_all_ops via_internal "$(bearer "$DELEG_ADM")" "admin's delegation on A's personal instance" "$INST_P" 404 not_found
  rpc_all_ops via_internal "$(bearer "$DELEG_ADM")" "admin's delegation on the shared instance" "$INST_S" 404 not_found
  r="$(via_internal POST "/api/platform/apps/instances/${INST_P}/rpc" "$(bearer "$DELEG_A")" \
    '{"op": "run", "sql": "UPDATE items SET note = note WHERE 0"}')"
  want 200 "$r" "A's delegation: run on A's own personal instance (control)"
  r="$(via_internal POST "/api/platform/apps/instances/${INST_S}/rpc" "$(bearer "$DELEG_A")" \
    '{"op": "getAll", "sql": "SELECT name FROM items"}')"
  want 200 "$r" "A's delegation: getAll on the shared instance (control)"
  apps_intact
  finish 23 "act=agent delegations carry their user's rights only: B's reads, not writes; A's personal and admin's are 404"
}

check_24() {
  local r rpc="/api/platform/apps/instances/${INST_S}/rpc" h
  h="$(cookie "$COOKIE_A")"
  r="$(via_caddy POST "$rpc" "$h" "{\"op\": \"getAll\", \"sql\": \"SELECT name FROM items\", \"instance_id\": \"${INST_P}\"}")"
  want 200 "$r" "A on the shared instance, naming the personal one in the body"
  want_body "$r" "[x['name'] for x in d['rows']] == ['${APP_SMARK}']" "the body's instance_id is ignored"
  r="$(via_caddy POST "$rpc" "$h" '{"op": "getAll", "sql": "SELECT name FROM pragma_database_list"}')"
  want "200|422" "$r" "A: pragma_database_list"
  [ "$(field "$r" status)" != 200 ] || want_body "$r" "[x['name'] for x in d['rows']] == ['main']" "only main is open"
  want 422 "$(via_caddy POST "$rpc" "$h" "{\"op\": \"run\", \"sql\": \"ATTACH '/data/spaces/${HOME_A}/apps/${INST_P}/data.sqlite' AS p\"}")" \
    "A: ATTACH the personal instance's database" sql_not_allowed
  want 422 "$(via_caddy POST "$rpc" "$h" "{\"op\": \"getAll\", \"sql\": \"SELECT name FROM p.items\"}")" \
    "A: read a schema-qualified other database"
  want 422 "$(via_caddy POST "$rpc" "$h" "{\"op\": \"run\", \"sql\": \"VACUUM INTO '/data/spaces/${HOME_A}/apps/${INST_P}/data.sqlite'\"}")" \
    "A: VACUUM INTO the personal instance's database" sql_not_allowed
  want "422" "$(via_caddy POST "$rpc" "$h" "{\"op\": \"getAll\", \"sql\": \"SELECT load_extension('/data/x')\"}")" "A: load_extension"
  want "422" "$(via_caddy POST "$rpc" "$h" "{\"op\": \"getAll\", \"sql\": \"SELECT readfile('/data/spaces/${HOME_A}/apps/${INST_P}/data.sqlite')\"}")" \
    "A: readfile() the personal instance's database"
  apps_intact
  finish 24 "one instance's RPC can't reach another's rows or file (ignored body id, only main, no ATTACH/VACUUM INTO/readfile)"
}

check_25() {
  local r iid
  for iid in "$INST_P" "$INST_S"; do
    r="$(E2E_BODY_MAX=100000 via_caddy GET "/api/platform/apps/instances/${iid}/bundle" "$(cookie "$COOKIE_A")")"
    want 200 "$r" "A: bundle of ${iid} (control)"
    want_body "$r" "d['code'].startswith('__homeai_define(')" "A: bundle of ${iid} is an app bundle"
  done
  want 200 "$(via_caddy GET "/api/platform/apps/instances/${INST_S}/bundle" "$(cookie "$COOKIE_B")")" "viewer B: shared bundle"
  want 404 "$(via_caddy GET "/api/platform/apps/instances/${INST_P}/bundle" "$(cookie "$COOKIE_B")")" \
    "B: A's personal bundle" not_found
  want 404 "$(via_caddy GET "/api/platform/apps/instances/${INST_S}/bundle" "$(cookie "$COOKIE_ADM")")" \
    "admin (non-member): shared bundle" not_found
  want 404 "$(via_caddy GET "/api/platform/apps/instances/${INST_P}/bundle" "$(cookie "$COOKIE_ADM")")" \
    "admin (non-member): A's personal bundle" not_found
  want 404 "$(via_internal GET "/api/platform/apps/instances/${INST_S}/bundle" "$(bearer "$DELEG_ADM")")" \
    "admin's delegation: shared bundle" not_found
  want 401 "$(via_caddy GET "/api/platform/apps/instances/${INST_S}/bundle" '{}')" "no session: bundle"
  want 404 "$(via_caddy GET "/api/platform/apps/instances/$(python3 -c 'import uuid; print(uuid.uuid4())')/bundle" "$(cookie "$COOKIE_A")")" \
    "A: an unknown instance's bundle" not_found
  finish 25 "the bundle endpoint needs read on the instance's space (viewer yes; outsider, admin, their delegations 404; no session 401)"
}

summary() {
  local total=$((PASS_COUNT + FAIL_COUNT))
  echo
  if [ "$FAIL_COUNT" -eq 0 ]; then
    printf '%sALL %d/%d CHECKS PASSED%s\n' "$GREEN" "$PASS_COUNT" "$total" "$NC"
  else
    printf '%s%d/%d CHECKS PASSED - %d FAILED (checks: %s)%s\n' \
      "$RED" "$PASS_COUNT" "$total" "$FAIL_COUNT" "${FAILED_CHECKS[*]}" "$NC"
  fi
}

main() {
  log "=== M11-04/M12-08: tenancy suite (docs/PLATFORM.md §9 invariants 1-7) ==="
  setup
  local n
  for n in $(seq 1 19); do "check_${n}"; done
  if setup_apps; then
    for n in $(seq 20 25); do "check_${n}"; done
  else
    for n in $(seq 20 25); do
      ERRORS+=("invariant 7 setup failed (see the ERROR above)")
      finish "$n" "invariant 7"
    done
  fi
  summary
}

main
[ "$FAIL_COUNT" -eq 0 ]
