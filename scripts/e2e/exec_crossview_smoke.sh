#!/usr/bin/env bash
# M4-04 acceptance: "cross-view" smoke test - proves `execute_code` and the
# file tools see the exact same files: the signed-in user's personal space,
# addressed at `/files/personal` from execute_code's shell and at
# `/personal` from the file tools (M11-03: exec runs as the user, with one
# bind per space; the file tools go through the platform files API).
#
# From a running (already up and healthy, per M4-04's own ticket) compose
# stack, this script drives a single WS thread (`scripts/ws_smoke.py`'s
# connect/send/recv pattern, same as `gate_m2.sh`/`gate_m3.sh`) through two
# turns:
#   1. Ask the agent to run `bash -lc 'date > /files/personal/exec-proof.txt'`
#      via its `execute_code` tool.
#   2. On the SAME thread, ask it to `read_file` `/personal/exec-proof.txt`
#      and report its content.
#
# Asserts BOTH tool calls show up as successful `tool_end` frames, that the
# read_file result carries exactly what the platform files API returns for
# `/personal/exec-proof.txt`, AND that on the host the file is owned by the
# user's uid and their personal space's gid, group-writable (`umask 002`),
# checked with `stat` inside the platform container (the host's space dirs
# are root:<gid> 2770, not readable by the invoking user).
#
# `curl` is NOT installed on this host - uses `wget`/Python (`urllib.request`)
# helpers, same as the other `scripts/e2e/*.sh` gate scripts.
#
# Model nondeterminism: each WS turn gets one retry, same policy as
# `gate_m2.sh`/`gate_m3.sh`.
#
# Cleans up the created thread, the file, and the exec container
# (`homeai-exec-<thread_id>`, removed with `docker rm` - the manager's own
# DELETE needs the run's delegation) via an EXIT trap, so re-running this
# script is safe. The signed-in `e2e-exec-*` user (`lib/auth.sh`) and its
# personal space are deleted too.
#
# Usage:
#   scripts/e2e/exec_crossview_smoke.sh
#
# Exits non-zero (and prints the failing step) if any check fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"
# shellcheck source=lib/files.sh
source "$SCRIPT_DIR/lib/files.sh"

API_BASE="http://localhost/api"

# Created once signed in (`create_thread`).
THREAD_ID=""
FILE_NAME="exec-proof.txt"

MODEL_RUNNER_HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120
WS_TURN_TIMEOUT_S=90
FILE_APPEAR_TIMEOUT_S=15

# Empty until we successfully PUT hitl_enabled=false after the API is up.
SAVED_HITL=""

log() {
  echo "[exec-crossview-smoke] $(date '+%H:%M:%S') $*"
}

# ---- curl-equivalent helpers (curl not installed; wget is) -----------------

http_ok() {
  # NOTE: deliberately NOT `wget --spider` - see `gate_m2.sh`'s own note
  # (this wget's spider mode sends HEAD, and `/api/health` is GET-only).
  wget -q -O /dev/null --timeout=10 --tries=1 "$1" >/dev/null 2>&1
}

# ---- polling helpers --------------------------------------------------------

wait_for_model_runner_healthy() {
  log "Waiting for model-runner container health (timeout ${MODEL_RUNNER_HEALTHY_TIMEOUT_S}s)..."
  local deadline=$(( $(date +%s) + MODEL_RUNNER_HEALTHY_TIMEOUT_S ))
  local cid status
  while (( $(date +%s) < deadline )); do
    cid="$(docker compose ps -q model-runner || true)"
    if [ -n "$cid" ]; then
      status="$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "unknown")"
      if [ "$status" = "healthy" ]; then
        log "model-runner is healthy."
        return 0
      fi
    fi
    sleep 5
  done
  log "ERROR: model-runner did not become healthy within ${MODEL_RUNNER_HEALTHY_TIMEOUT_S}s"
  return 1
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

# ---- REST helper (Python's urllib - `curl` unavailable) --------------------

# Prints the HTTP status code on line 1, the raw response body on line 2.
rest_request() {
  local method="$1" url="$2" json_body="${3:-}"
  python3 - "$method" "$url" "$json_body" <<'PY'
import os
import sys
import urllib.error
import urllib.request

method, url, json_body = sys.argv[1], sys.argv[2], sys.argv[3]
data = json_body.encode() if json_body else None
headers = {"Content-Type": "application/json"} if data else {}
headers["Cookie"] = os.environ["E2E_AUTH_COOKIE"]
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

# $1: raw ws_smoke.py output file (one Python-dict-repr frame per line).
# $2: tool name. Returns 0 if a `tool_end` frame for that tool with
# `status: 'success'` appears anywhere in the output.
successful_tool_end() {
  local out="$1" name="$2"
  grep "'type': 'tool_end'" "$out" | grep "'name': '${name}'" | grep -q "'status': 'success'"
}

has_error_frame() {
  grep -q "'type': 'error'" "$1"
}

# ---- gate steps -------------------------------------------------------------

step_stack_healthy() {
  log "Step 1/5: confirming the compose stack is up and healthy..."
  wait_for_model_runner_healthy
  log "Waiting for agent-server API health (timeout ${API_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_api_health "$API_HEALTH_TIMEOUT_S"; then
    log "ERROR: ${API_BASE}/health never came up within ${API_HEALTH_TIMEOUT_S}s"
    return 1
  fi
  log "OK: model-runner healthy + ${API_BASE}/health OK"
}

check_file_exists() {
  [ "$(e2e_personal_status "$FILE_NAME")" = "200" ]
}

poll_file_exists() {
  local timeout_s="$1"
  local deadline=$(( $(date +%s) + timeout_s ))
  while (( $(date +%s) < deadline )); do
    if check_file_exists; then
      return 0
    fi
    sleep 1
  done
  return 1
}

run_ws_turn() {
  # $1: prompt. $2: output log file.
  WS_SMOKE_THREAD_ID="$THREAD_ID" WS_SMOKE_PROMPT="$1" \
    timeout "$WS_TURN_TIMEOUT_S" uvx --from websockets python3 "$SCRIPT_DIR/../ws_smoke.py" >"$2" 2>&1 || true
}

step_execute_code_writes_file() {
  log "Step 2/5: WS prompt -> agent runs 'date > /files/personal/${FILE_NAME}' via execute_code..."
  e2e_personal_rm "$FILE_NAME"
  local prompt="Use your execute_code tool to run exactly this command: bash -lc 'date > /files/personal/${FILE_NAME}'. Just run it and confirm when done."

  log "Sending execute_code prompt (attempt 1/2)..."
  run_ws_turn "$prompt" /tmp/exec-crossview-attempt-1.log
  if successful_tool_end /tmp/exec-crossview-attempt-1.log "execute_code" && poll_file_exists "$FILE_APPEAR_TIMEOUT_S"; then
    log "OK: execute_code tool_end succeeded and /personal/${FILE_NAME} appeared on attempt 1"
    return 0
  fi

  log "WARN: execute_code tool_end/file not observed on attempt 1 - retrying once (LLM nondeterminism allowance, same policy as gate_m2.sh/gate_m3.sh)"
  run_ws_turn "$prompt" /tmp/exec-crossview-attempt-2.log
  if successful_tool_end /tmp/exec-crossview-attempt-2.log "execute_code" && poll_file_exists "$FILE_APPEAR_TIMEOUT_S"; then
    log "OK: execute_code tool_end succeeded and /personal/${FILE_NAME} appeared on attempt 2"
    return 0
  fi

  log "ERROR: execute_code never produced a successful tool_end + /personal/${FILE_NAME} after 2 attempts - gate FAILS"
  log "--- attempt 1 transcript ---"
  cat /tmp/exec-crossview-attempt-1.log 2>/dev/null || true
  log "--- attempt 2 transcript ---"
  cat /tmp/exec-crossview-attempt-2.log 2>/dev/null || true
  return 1
}

# $1: WS log. Succeeds if a successful read_file tool_end carries the file's
# first line exactly as the platform files API returns it.
read_file_saw_content() {
  local log_file="$1" expected
  expected="$(e2e_personal_cat "$FILE_NAME" | head -n1)"
  [ -n "$expected" ] || return 1
  grep "'type': 'tool_end'" "$log_file" | grep "'name': 'read_file'" \
    | grep "'status': 'success'" | grep -qF -- "$expected"
}

step_read_file_sees_same_content() {
  log "Step 3/5: same thread, WS prompt -> agent read_file's /personal/${FILE_NAME}..."
  # The file tools' spelling of the same file: `/personal/...`, never the
  # exec shell's `/files/personal/...` - spelled out here so this plumbing
  # check isn't gated on model path reasoning it wasn't prompted for.
  local prompt="Now use your read_file tool with file_path exactly '/personal/${FILE_NAME}' (the /files/ prefix is only for execute_code shell commands) and tell me exactly what it contains."
  local retry_prompt="Wrong path. Call read_file now with file_path exactly '/personal/${FILE_NAME}' — not '/files/personal/${FILE_NAME}'. Then quote the file contents."

  log "Sending read_file prompt (attempt 1/2)..."
  run_ws_turn "$prompt" /tmp/exec-crossview-read-attempt-1.log
  if read_file_saw_content /tmp/exec-crossview-read-attempt-1.log; then
    log "OK: read_file returned the exec-written content on attempt 1"
    return 0
  fi

  log "WARN: read_file of the exec-written content not observed on attempt 1 - retrying once (LLM nondeterminism allowance)"
  run_ws_turn "$retry_prompt" /tmp/exec-crossview-read-attempt-2.log
  if read_file_saw_content /tmp/exec-crossview-read-attempt-2.log; then
    log "OK: read_file returned the exec-written content on attempt 2"
    return 0
  fi

  log "ERROR: read_file never returned the exec-written content after 2 attempts - gate FAILS"
  log "--- attempt 1 transcript ---"
  cat /tmp/exec-crossview-read-attempt-1.log 2>/dev/null || true
  log "--- attempt 2 transcript ---"
  cat /tmp/exec-crossview-read-attempt-2.log 2>/dev/null || true
  return 1
}

step_no_error_frames() {
  log "Step 4/5: confirming no error frames were observed in either turn..."
  for f in /tmp/exec-crossview-attempt-1.log /tmp/exec-crossview-attempt-2.log \
           /tmp/exec-crossview-read-attempt-1.log /tmp/exec-crossview-read-attempt-2.log; do
    if [ -f "$f" ] && has_error_frame "$f"; then
      log "ERROR: an error frame was observed in ${f}:"
      cat "$f"
      return 1
    fi
  done
  log "OK: no error frames observed"
}

step_host_ownership() {
  log "Step 5/5: confirming the file on the host is <user uid>:<personal gid>, group-writable..."
  local row space_id gid uid got expected
  row="$(_e2e_psql homeai_platform "SELECT s.id || ' ' || s.gid || ' ' || u.uid FROM spaces s
    JOIN users u ON u.id = s.owner_user_id WHERE s.kind = 'personal' AND u.username = '${E2E_AUTH_USER}'")"
  read -r space_id gid uid <<<"$row"
  if ! [[ "$space_id" =~ ^[0-9a-f-]{36}$ ]]; then
    log "ERROR: personal space of ${E2E_AUTH_USER} not found (got '${row}')"
    return 1
  fi
  got="$(_e2e_compose exec -T platform stat -c '%u:%g %a' "/data/spaces/${space_id}/files/${FILE_NAME}")"
  expected="${uid}:${gid} 664"
  if [ "$got" != "$expected" ]; then
    log "ERROR: \${SPACES_DIR}/${space_id}/files/${FILE_NAME} is '${got}', expected '${expected}'"
    return 1
  fi
  log "OK: \${SPACES_DIR}/${space_id}/files/${FILE_NAME} is ${got}; content: $(e2e_personal_cat "$FILE_NAME")"
}

cleanup() {
  # Always runs (success or failure) so the script is safely re-runnable.
  if [ -n "$SAVED_HITL" ]; then
    bash "${SCRIPT_DIR}/ensure_hitl.sh" "$SAVED_HITL" >/dev/null 2>&1 || true
  fi
  if [ -n "${E2E_AUTH_COOKIE:-}" ]; then
    e2e_personal_rm "$FILE_NAME"
  fi
  rm -f /tmp/exec-crossview-attempt-1.log /tmp/exec-crossview-attempt-2.log \
        /tmp/exec-crossview-read-attempt-1.log /tmp/exec-crossview-read-attempt-2.log 2>/dev/null || true
  if [ -n "$THREAD_ID" ]; then
    rest_request DELETE "${API_BASE}/threads/${THREAD_ID}" >/dev/null 2>&1 || true
    docker rm -f "homeai-exec-${THREAD_ID}" >/dev/null 2>&1 || true
  fi
  e2e_auth_end
}
trap cleanup EXIT

create_thread() {
  local resp status body
  resp="$(rest_request POST "${API_BASE}/threads" '{}')"
  status="$(sed -n '1p' <<<"$resp")"
  body="$(sed -n '2p' <<<"$resp")"
  if [ "$status" != "201" ]; then
    log "ERROR: expected 201 from POST /api/threads, got ${status}: ${body}"
    return 1
  fi
  THREAD_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['id'])" "$body")"
  log "OK: created thread ${THREAD_ID}"
}

main() {
  log "=== EXEC CROSSVIEW SMOKE (M4-04): execute_code + read_file see the same files ==="
  step_stack_healthy
  e2e_auth_begin exec
  create_thread
  # M8-03 made HITL on by default; this smoke's execute_code prompt is not
  # wired to send approval_response, so turn HITL off for the run.
  log "Turning hitl_enabled off so execute_code is not interrupted..."
  SAVED_HITL="$(bash "${SCRIPT_DIR}/ensure_hitl.sh" false)"
  log "OK: hitl_enabled=false (was ${SAVED_HITL})"
  step_execute_code_writes_file
  step_read_file_sees_same_content
  step_no_error_frames
  step_host_ownership
  echo "EXEC CROSSVIEW SMOKE: PASS"
}

main
