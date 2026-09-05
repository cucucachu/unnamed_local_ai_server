#!/usr/bin/env bash
# M3-01 acceptance: chat memory survives an agent-server restart via the
# Postgres checkpointer (as opposed to the M2-era in-memory `MemorySaver`,
# which loses all thread history on restart - see `gate_m2.sh`'s step 4
# docstring for that expected M2 behavior).
#
# From a running compose stack, this script:
#   1. Sends "My name is Bob." on thread `pg-1` over the real WS endpoint.
#   2. Restarts the agent-server container (fresh process, fresh in-process
#      state - the Postgres checkpointer is the only thing that could
#      possibly make the next step work) and waits for it to be healthy.
#   3. Sends "What is my name?" on the SAME thread `pg-1`, and asserts the
#      response mentions "Bob" - proof the checkpoint survived the restart.
#   4. (M8-08) With HITL on, prompts a `write_file` on a dedicated thread so
#      the turn pauses on `approval_request`, then asserts
#      `GET /api/threads/{id}/state` reports a non-null `pending_approval`.
#   5. (M8-08) Restarts agent-server again and re-GETs the same endpoint —
#      `pending_approval` (same `interrupt_id`) must still be there. HITL
#      is on by default; this step still PUTs `hitl_enabled: true` so a
#      leftover `false` from `settings_rest_smoke.sh` / a previous UI
#      toggle cannot silently skip the interrupt.
#
# Uses `scripts/ws_smoke.py` (already parameterized via `WS_SMOKE_THREAD_ID`/
# `WS_SMOKE_PROMPT` env vars, M2-07) rather than writing a new WS client, and
# mirrors `gate_m2.sh`'s logging/health-polling conventions (same
# `wget -O /dev/null` curl-equivalent - `curl` isn't installed on this host).
#
# M6-03: thread id renamed "pg-1" -> "persistence-smoke-pg-1" (prefixed with
# this script's own name, matching every other gate/smoke script's
# convention) and cleanup now deletes that checkpoint via the threads REST
# DELETE endpoint in an EXIT trap - this script previously had no cleanup
# at all, so its checkpoint/conversation history grew unbounded across
# repeated runs (e.g. inside gate_full.sh, which chains this script
# alongside 9 others against the same live stack).
#
# M8-08: the HITL thread / leftover target file / original `hitl_enabled`
# are cleaned up in the same EXIT trap so this stays re-runnable inside
# `gate_m8.sh` / `gate_full.sh`.
#
# Usage:
#   scripts/e2e/persistence_smoke.sh
#
# Exits non-zero (and prints the failing step) if any check fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

THREAD_ID="persistence-smoke-pg-1"
HITL_THREAD_ID="persistence-smoke-hitl-1"
HITL_FILE_NAME="persistence-smoke-pending.txt"
NAME="Bob"

API_BASE="http://localhost/api"
API_HEALTH_TIMEOUT_S=120
RESTART_GRACE_S=5
# Real-model write_file + interrupt; one retry on LLM nondeterminism.
HITL_WS_TIMEOUT_S=180

WORKSPACE_DIR="$(sed -n 's/^WORKSPACE_DIR=\(.*\)$/\1/p' .env | head -n1 | xargs)"
if [ -z "$WORKSPACE_DIR" ]; then
  echo "[persistence-smoke] ERROR: WORKSPACE_DIR not set in .env" >&2
  exit 1
fi
HITL_HOST_FILE="${WORKSPACE_DIR}/${HITL_FILE_NAME}"

# Empty until we successfully GET /api/settings — cleanup restores only then.
SAVED_HITL=""

log() {
  echo "[persistence-smoke] $(date '+%H:%M:%S') $*"
}

# ---- curl-equivalent helper (curl not installed; wget is) ------------------

http_ok() {
  # 0 if a GET to $1 is reachable with a successful (2xx/3xx) status.
  #
  # NOTE: deliberately NOT `wget --spider` - this wget version (1.25.0)
  # sends a HEAD request in spider mode, and /api/health is a GET-only
  # FastAPI route (405s on HEAD) - confirmed in `gate_m2.sh`. `-O /dev/null`
  # forces a real GET while still discarding the body.
  wget -q -O /dev/null --timeout=10 --tries=1 "$1" >/dev/null 2>&1
}

# ---- polling helper ---------------------------------------------------------

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

# Prints the HTTP status code on line 1, the raw response body on line 2.
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
    with urllib.request.urlopen(req, timeout=15) as resp:
        print(resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as e:
    print(e.code)
    print(e.read().decode())
PY
}

json_field() {
  python3 -c "
import json, sys
value = json.loads(sys.argv[1])[sys.argv[2]]
print(str(value).lower() if isinstance(value, bool) else value)
" "$1" "$2"
}

# Prints pending_approval.interrupt_id, or exits 1 if pending_approval is null.
pending_interrupt_id() {
  python3 -c "
import json, sys
body = json.loads(sys.argv[1])
pending = body.get('pending_approval')
if not isinstance(pending, dict) or not pending.get('interrupt_id'):
    sys.exit(1)
print(pending['interrupt_id'])
" "$1"
}

delete_thread() {
  rest_request DELETE "${API_BASE}/threads/${1}" >/dev/null 2>&1 || true
}

get_thread_state_body() {
  local thread_id="$1"
  local resp status body
  resp="$(rest_request GET "${API_BASE}/threads/${thread_id}/state")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "200" ]; then
    log "ERROR: GET /api/threads/${thread_id}/state expected 200, got ${status}: ${body}"
    return 1
  fi
  printf '%s' "$body"
}

# ---- steps -------------------------------------------------------------

step_tell_name() {
  log "Step 1/5: telling the agent my name (thread=${THREAD_ID})..."
  local out
  out="$(mktemp)"
  if ! WS_SMOKE_THREAD_ID="$THREAD_ID" WS_SMOKE_PROMPT="My name is ${NAME}." \
      uvx --from websockets python "$SCRIPT_DIR/../ws_smoke.py" >"$out" 2>&1; then
    log "ERROR: ws_smoke.py exited non-zero:"
    cat "$out"
    rm -f "$out"
    return 1
  fi
  if grep -q "'type': 'error'" "$out"; then
    log "ERROR: an error frame was observed:"
    cat "$out"
    rm -f "$out"
    return 1
  fi
  log "OK: sent 'My name is ${NAME}.' on thread ${THREAD_ID}"
  rm -f "$out"
}

step_restart_agent_server() {
  local label="${1:-restarting agent-server}"
  log "${label}..."
  docker compose restart agent-server
  log "Waiting for agent-server API health after restart (timeout ${API_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_api_health "$API_HEALTH_TIMEOUT_S"; then
    log "ERROR: agent-server did not report healthy API after restart"
    return 1
  fi
  sleep "$RESTART_GRACE_S"
  log "OK: agent-server restarted and healthy again"
}

step_ask_name_survived() {
  log "Step 3/5: asking the agent my name on the same thread after restart..."
  local out
  out="$(mktemp)"
  if ! WS_SMOKE_THREAD_ID="$THREAD_ID" WS_SMOKE_PROMPT="What is my name?" \
      uvx --from websockets python "$SCRIPT_DIR/../ws_smoke.py" >"$out" 2>&1; then
    log "ERROR: ws_smoke.py exited non-zero:"
    cat "$out"
    rm -f "$out"
    return 1
  fi
  if grep -q "'type': 'error'" "$out"; then
    log "ERROR: an error frame was observed:"
    cat "$out"
    rm -f "$out"
    return 1
  fi

  # Case-insensitive substring check across the concatenated token content
  # rather than the raw frames - robust to the real model wrapping "Bob" in
  # extra prose (e.g. "Your name is Bob!").
  local reply
  reply="$(python3 -c "
import ast, sys

tokens = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        frame = ast.literal_eval(line)
    except (ValueError, SyntaxError):
        continue
    if isinstance(frame, dict) and frame.get('type') == 'token':
        tokens.append(frame.get('content', ''))
print(''.join(tokens))
" <"$out")"

  log "Reply content: ${reply}"
  if ! printf '%s' "$reply" | grep -qi "$NAME"; then
    log "ERROR: reply did not mention '${NAME}' - checkpoint did not survive the restart:"
    cat "$out"
    rm -f "$out"
    return 1
  fi
  log "OK: reply mentions '${NAME}' - checkpoint survived the agent-server restart"
  rm -f "$out"
}

ensure_hitl_on() {
  local resp status body current
  resp="$(rest_request GET "${API_BASE}/settings")"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "200" ]; then
    log "ERROR: GET /api/settings expected 200, got ${status}: ${body}"
    return 1
  fi
  SAVED_HITL="$(json_field "$body" hitl_enabled)"
  current="$SAVED_HITL"
  if [ "$current" != "true" ]; then
    log "hitl_enabled=${current}; PUT hitl_enabled=true for the pending-approval check..."
    resp="$(rest_request PUT "${API_BASE}/settings" '{"hitl_enabled": true}')"
    status="$(sed -n '1p' <<<"$resp")"
    body="$(sed -n '2p' <<<"$resp")"
    if [ "$status" != "200" ]; then
      log "ERROR: PUT /api/settings expected 200, got ${status}: ${body}"
      return 1
    fi
    if [ "$(json_field "$body" hitl_enabled)" != "true" ]; then
      log "ERROR: PUT /api/settings did not set hitl_enabled=true: ${body}"
      return 1
    fi
  fi
  log "OK: hitl_enabled=true (was ${SAVED_HITL})"
}

# One WS turn that should pause on write_file. Prints the transcript path
# via HITL_WS_LOG. Returns 0 if GET .../state has a pending_approval.
trigger_pending_approval_once() {
  local out
  out="$(mktemp)"
  HITL_WS_LOG="$out"
  rm -f "$HITL_HOST_FILE"
  delete_thread "$HITL_THREAD_ID"

  # `timeout` + PYTHONUNBUFFERED so a hung turn still leaves a partial log
  # (same reason as gate_m4.sh). ws_smoke.py stops on turn_end, including
  # `{"status": "awaiting_approval"}` after approval_request.
  set +e
  WS_SMOKE_THREAD_ID="$HITL_THREAD_ID" \
    WS_SMOKE_PROMPT="Create ${HITL_FILE_NAME} containing hi. Use write_file." \
    PYTHONUNBUFFERED=1 \
    timeout "$HITL_WS_TIMEOUT_S" \
    uvx --from websockets python "$SCRIPT_DIR/../ws_smoke.py" >"$out" 2>&1
  local ws_rc=$?
  set -e

  local body
  if ! body="$(get_thread_state_body "$HITL_THREAD_ID")"; then
    log "WS transcript:"
    cat "$out"
    return 1
  fi
  log "GET /api/threads/${HITL_THREAD_ID}/state -> ${body}"
  if pending_interrupt_id "$body" >/dev/null; then
    PENDING_INTERRUPT_ID="$(pending_interrupt_id "$body")"
    log "OK: pending_approval present (interrupt_id=${PENDING_INTERRUPT_ID}, ws_rc=${ws_rc})"
    return 0
  fi
  log "WARN: no pending_approval after write_file prompt (ws_rc=${ws_rc})"
  log "WS transcript:"
  cat "$out"
  return 1
}

step_pending_approval_before_restart() {
  log "Step 4/5: pending HITL approval on thread ${HITL_THREAD_ID}..."
  ensure_hitl_on

  if trigger_pending_approval_once; then
    rm -f "$HITL_WS_LOG"
    return 0
  fi
  log "WARN: first write_file turn did not pause for approval - retrying once (LLM nondeterminism allowance)"
  rm -f "$HITL_WS_LOG"
  if trigger_pending_approval_once; then
    rm -f "$HITL_WS_LOG"
    return 0
  fi
  rm -f "$HITL_WS_LOG"
  log "ERROR: write_file turn did not leave a pending_approval after 2 attempts"
  return 1
}

step_pending_approval_survived_restart() {
  log "Step 5/5: pending HITL approval survives agent-server restart..."
  step_restart_agent_server "Step 5/5: restarting agent-server (pending-approval persistence)"

  local body
  if ! body="$(get_thread_state_body "$HITL_THREAD_ID")"; then
    return 1
  fi
  log "GET /api/threads/${HITL_THREAD_ID}/state after restart -> ${body}"

  local after_id
  if ! after_id="$(pending_interrupt_id "$body")"; then
    log "ERROR: pending_approval was null after restart — interrupt did not survive:"
    log "${body}"
    return 1
  fi
  if [ "$after_id" != "$PENDING_INTERRUPT_ID" ]; then
    log "ERROR: interrupt_id changed across restart (before=${PENDING_INTERRUPT_ID} after=${after_id}): ${body}"
    return 1
  fi
  if [ -f "$HITL_HOST_FILE" ]; then
    log "ERROR: ${HITL_HOST_FILE} exists — write_file ran without approval"
    return 1
  fi
  log "OK: pending_approval interrupt_id=${after_id} survived the agent-server restart"
}

cleanup() {
  # M6-03: always runs (success or failure) so this script is safely
  # re-runnable and doesn't leave its checkpoint growing across repeated
  # runs - this script previously had no cleanup at all.
  # M8-08: also drop the HITL thread, the unused target file, and restore
  # whatever hitl_enabled was before this script forced it on.
  delete_thread "$THREAD_ID"
  delete_thread "$HITL_THREAD_ID"
  rm -f "$HITL_HOST_FILE" 2>/dev/null || true
  if [ -n "$SAVED_HITL" ] && [ "$SAVED_HITL" != "true" ]; then
    rest_request PUT "${API_BASE}/settings" "{\"hitl_enabled\": ${SAVED_HITL}}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

main() {
  log "=== PERSISTENCE SMOKE (M3-01/M8-08): checkpoint + pending HITL approval survive agent-server restart ==="
  step_tell_name
  step_restart_agent_server "Step 2/5: restarting agent-server"
  step_ask_name_survived
  step_pending_approval_before_restart
  step_pending_approval_survived_restart
  echo "PERSISTENCE SMOKE: PASS"
}

main
