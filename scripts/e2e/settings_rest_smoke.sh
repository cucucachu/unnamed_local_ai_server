#!/usr/bin/env bash
# M8-02 acceptance: `GET`/`PUT /api/settings` against the live stack,
# scripted for repeatability — mirrors `threads_rest_smoke.sh`'s structure
# and helpers exactly (same `curl`-not-installed / `wget`+Python `urllib.
# request` convention).
#
# From a running (or freshly brought-up) compose stack, this script:
#   1. GETs the current document (records `hitl_enabled`'s starting value
#      so this script is re-runnable without assuming a fresh DB).
#   2. PUTs `{"hitl_enabled": <the opposite of the starting value>}` and
#      confirms the response reflects it while the other two fields stay
#      at their prior values (a real partial-merge check, not just
#      "toggled to false" — safe to re-run against a DB that already has
#      `hitl_enabled: false` from a previous run).
#   3. GETs again and confirms it still reflects the change (in-process
#      consistency, before touching the container at all).
#   4. Runs `docker compose restart agent-server` FOR REAL, waits for
#      `/api/health` to come back, then GETs once more and confirms the
#      SAME value still reflects the change — proving Postgres persistence
#      (a fresh Python process reading from a cold connection pool), not
#      just in-process/module-level state.
#
# Usage:
#   scripts/e2e/settings_rest_smoke.sh
#
# Exits non-zero (and prints the failing step) if any check fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

API_BASE="http://localhost/api"
API_HEALTH_TIMEOUT_S=120
RESTART_HEALTH_TIMEOUT_S=120

log() {
  echo "[settings-rest-smoke] $(date '+%H:%M:%S') $*"
}

# ---- curl-equivalent / polling helpers (curl not installed; wget is) -------
# See `threads_rest_smoke.sh` for why `wget --spider` isn't used here.

http_ok() {
  wget -q -O /dev/null --timeout=10 --tries=1 "$1" >/dev/null 2>&1
}

wait_for_api_health() {
  local timeout_s="$1"
  local deadline=$(( $(date +%s) + timeout_s ))
  while (( $(date +%s) < deadline )); do
    if http_ok "${API_BASE}/health"; then
      return 0
    fi
    sleep 3
  done
  return 1
}

# ---- REST helpers (Python's urllib - JSON in/out, `curl` unavailable) ------

# Prints the HTTP status code on line 1, the raw response body (compact
# single-line JSON) on line 2.
rest_request() {
  local method="$1" url="$2" json_body="${3:-}"
  python3 - "$method" "$url" "$json_body" <<'PY'
import sys
import urllib.error
import urllib.request

method, url, json_body = sys.argv[1], sys.argv[2], sys.argv[3]
data = json_body.encode() if json_body else None
headers = {"Content-Type": "application/json"} if data else {}
req = urllib.request.Request(url, data=data, method=method, headers=headers)
try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode())
PY
}

# $1: response body (a settings JSON object). $2: field name. Prints the
# field's value as a lowercase string ('true'/'false' for bools).
json_field() {
  python3 -c "
import json, sys
value = json.loads(sys.argv[1])[sys.argv[2]]
print(str(value).lower() if isinstance(value, bool) else value)
" "$1" "$2"
}

# ---- gate steps -------------------------------------------------------------

step_stack_up_and_healthy() {
  log "Step 1/5: bringing up the compose stack (idempotent if already up)..."
  docker compose up -d --build agent-server
  log "Waiting for agent-server API health (timeout ${API_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_api_health "$API_HEALTH_TIMEOUT_S"; then
    log "ERROR: ${API_BASE}/health never came up within ${API_HEALTH_TIMEOUT_S}s"
    return 1
  fi
  log "OK: ${API_BASE}/health OK"
}

step_get_starting_document() {
  log "Step 2/5: GET /api/settings - record the starting document..."
  local resp status body
  resp="$(rest_request GET "${API_BASE}/settings")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"

  if [ "$status" != "200" ]; then
    log "ERROR: expected 200, got ${status}: ${body}"
    return 1
  fi

  STARTING_HITL="$(json_field "$body" hitl_enabled)"
  STARTING_THINKING="$(json_field "$body" thinking_enabled)"
  STARTING_EDIT_MODE="$(json_field "$body" edit_mode_default)"

  if [ "$STARTING_HITL" = "true" ]; then
    TARGET_HITL="false"
  else
    TARGET_HITL="true"
  fi

  log "OK: starting document hitl_enabled=${STARTING_HITL} thinking_enabled=${STARTING_THINKING} edit_mode_default=${STARTING_EDIT_MODE}"
  log "    (this run flips hitl_enabled -> ${TARGET_HITL} and leaves the other two fields untouched)"
}

step_put_partial_and_confirm_merge() {
  log "Step 3/5: PUT {\"hitl_enabled\": ${TARGET_HITL}} - confirm the merged response..."
  local resp status body
  resp="$(rest_request PUT "${API_BASE}/settings" "{\"hitl_enabled\": ${TARGET_HITL}}")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"

  if [ "$status" != "200" ]; then
    log "ERROR: expected 200, got ${status}: ${body}"
    return 1
  fi

  local put_hitl put_thinking put_edit_mode
  put_hitl="$(json_field "$body" hitl_enabled)"
  put_thinking="$(json_field "$body" thinking_enabled)"
  put_edit_mode="$(json_field "$body" edit_mode_default)"

  if [ "$put_hitl" != "$TARGET_HITL" ]; then
    log "ERROR: expected hitl_enabled=${TARGET_HITL} in PUT response, got ${put_hitl}"
    return 1
  fi
  if [ "$put_thinking" != "$STARTING_THINKING" ] || [ "$put_edit_mode" != "$STARTING_EDIT_MODE" ]; then
    log "ERROR: PUT response changed a field it shouldn't have (partial-merge broken): ${body}"
    return 1
  fi
  log "OK: PUT response reflects hitl_enabled=${TARGET_HITL}, other fields unchanged"
}

step_get_confirms_change_in_process() {
  log "Step 4/5: GET /api/settings - confirm the change, before any restart..."
  local resp status body get_hitl
  resp="$(rest_request GET "${API_BASE}/settings")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"

  if [ "$status" != "200" ]; then
    log "ERROR: expected 200, got ${status}: ${body}"
    return 1
  fi

  get_hitl="$(json_field "$body" hitl_enabled)"
  if [ "$get_hitl" != "$TARGET_HITL" ]; then
    log "ERROR: expected hitl_enabled=${TARGET_HITL}, got ${get_hitl}: ${body}"
    return 1
  fi
  log "OK: GET reflects hitl_enabled=${TARGET_HITL}"
}

step_restart_and_confirm_persistence() {
  log "Step 5/5: docker compose restart agent-server - confirm the change SURVIVES it..."
  docker compose restart agent-server
  log "Waiting for agent-server API health after restart (timeout ${RESTART_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_api_health "$RESTART_HEALTH_TIMEOUT_S"; then
    log "ERROR: ${API_BASE}/health never came back within ${RESTART_HEALTH_TIMEOUT_S}s after restart"
    return 1
  fi

  local resp status body get_hitl
  resp="$(rest_request GET "${API_BASE}/settings")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "200" ]; then
    log "ERROR: expected 200, got ${status}: ${body}"
    return 1
  fi

  get_hitl="$(json_field "$body" hitl_enabled)"
  if [ "$get_hitl" != "$TARGET_HITL" ]; then
    log "ERROR: hitl_enabled did NOT survive the restart - expected ${TARGET_HITL}, got ${get_hitl}: ${body}"
    return 1
  fi
  log "OK: hitl_enabled=${TARGET_HITL} survived a real agent-server restart (Postgres persistence confirmed)"
}

main() {
  log "=== SETTINGS REST SMOKE (M8-02): GET defaults -> PUT partial -> GET reflects -> restart -> GET still reflects ==="
  step_stack_up_and_healthy
  step_get_starting_document
  step_put_partial_and_confirm_merge
  step_get_confirms_change_in_process
  step_restart_and_confirm_persistence
  echo "SETTINGS REST SMOKE: PASS"
}

main
