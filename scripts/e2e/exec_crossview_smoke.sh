#!/usr/bin/env bash
# M4-04 acceptance: "cross-view" smoke test - proves `execute_code` and the
# file tools (both already-merged M2-03/M4-03 machinery) see the exact same
# `/workspace` directory that's bind-mounted to the real host filesystem.
#
# From a running (already up and healthy, per M4-04's own ticket) compose
# stack, this script drives a single WS thread (`scripts/ws_smoke.py`'s
# connect/send/recv pattern, same as `gate_m2.sh`/`gate_m3.sh`) through two
# turns:
#   1. Ask the agent to run `bash -lc 'date > /workspace/exec-proof.txt'` via
#      its `execute_code` tool.
#   2. On the SAME thread, ask it to `read_file` that same file and report
#      its content.
#
# Asserts BOTH tool calls show up as successful `tool_end` frames, AND that
# the file genuinely exists on the HOST at `${WORKSPACE_DIR}/exec-proof.txt`
# (`WORKSPACE_DIR` read from `.env`, same as `gate_m3.sh`/`files_rest_smoke.sh`).
#
# `curl` is NOT installed on this host - uses `wget`/Python (`urllib.request`)
# helpers, same as the other `scripts/e2e/*.sh` gate scripts.
#
# Model nondeterminism: each WS turn gets one retry, same policy as
# `gate_m2.sh`/`gate_m3.sh`.
#
# Cleans up the created thread + host file via an EXIT trap, so re-running
# this script is safe.
#
# M6-03: cleanup now also deletes the code-exec-manager session/container
# this script's `execute_code` call creates (session_id == THREAD_ID) -
# without this, the exec container sat alive until the 30-min idle reaper
# (EXEC_IDLE_MINUTES) fired, which is well past a `gate_full.sh` run's own
# timeframe.
#
# Usage:
#   scripts/e2e/exec_crossview_smoke.sh
#
# Exits non-zero (and prints the failing step) if any check fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

API_BASE="http://localhost/api"

RUN_ID="$$-$(date +%s)"
THREAD_ID="exec-crossview-${RUN_ID}"
FILE_NAME="exec-proof.txt"

MODEL_RUNNER_HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120
WS_TURN_TIMEOUT_S=90
FILE_APPEAR_TIMEOUT_S=15

WORKSPACE_DIR="$(sed -n 's/^WORKSPACE_DIR=\(.*\)$/\1/p' .env | head -n1 | xargs)"
if [ -z "$WORKSPACE_DIR" ]; then
  echo "[exec-crossview-smoke] ERROR: WORKSPACE_DIR not set in .env" >&2
  exit 1
fi
HOST_FILE_PATH="${WORKSPACE_DIR}/${FILE_NAME}"
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

check_host_file_exists() {
  [ -f "$HOST_FILE_PATH" ]
}

poll_host_file_exists() {
  local timeout_s="$1"
  local deadline=$(( $(date +%s) + timeout_s ))
  while (( $(date +%s) < deadline )); do
    if check_host_file_exists; then
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
  log "Step 2/5: WS prompt -> agent runs 'date > /workspace/${FILE_NAME}' via execute_code..."
  rm -f "$HOST_FILE_PATH"
  local prompt="Use your execute_code tool to run exactly this command: bash -lc 'date > /workspace/${FILE_NAME}'. Just run it and confirm when done."

  log "Sending execute_code prompt (attempt 1/2)..."
  run_ws_turn "$prompt" /tmp/exec-crossview-attempt-1.log
  if successful_tool_end /tmp/exec-crossview-attempt-1.log "execute_code" && poll_host_file_exists "$FILE_APPEAR_TIMEOUT_S"; then
    log "OK: execute_code tool_end succeeded and ${HOST_FILE_PATH} appeared on attempt 1"
    return 0
  fi

  log "WARN: execute_code tool_end/file not observed on attempt 1 - retrying once (LLM nondeterminism allowance, same policy as gate_m2.sh/gate_m3.sh)"
  run_ws_turn "$prompt" /tmp/exec-crossview-attempt-2.log
  if successful_tool_end /tmp/exec-crossview-attempt-2.log "execute_code" && poll_host_file_exists "$FILE_APPEAR_TIMEOUT_S"; then
    log "OK: execute_code tool_end succeeded and ${HOST_FILE_PATH} appeared on attempt 2"
    return 0
  fi

  log "ERROR: execute_code never produced a successful tool_end + host file after 2 attempts - gate FAILS"
  log "--- attempt 1 transcript ---"
  cat /tmp/exec-crossview-attempt-1.log 2>/dev/null || true
  log "--- attempt 2 transcript ---"
  cat /tmp/exec-crossview-attempt-2.log 2>/dev/null || true
  return 1
}

step_read_file_sees_same_content() {
  log "Step 3/5: same thread, WS prompt -> agent read_file's ${FILE_NAME}..."
  # NOTE: explicit file-tool virtual path (`/${FILE_NAME}`, NOT
  # `/workspace/${FILE_NAME}`) - deepagents' `FilesystemBackend` is already
  # rooted at the workspace dir for file tools (`virtual_mode=True`), so a
  # file-tool path of `/workspace/...` would look one level too deep. This
  # is exactly the "file tool paths and /workspace in execute_code refer to
  # the same directory" nuance the M4-04 system-prompt addition documents -
  # spelled out here so this plumbing check isn't gated on model path
  # reasoning it wasn't specifically prompted to get right.
  local prompt="Now use your read_file tool with file_path exactly '${FILE_NAME}' (workspace-relative; do NOT prefix /workspace — that prefix is only for execute_code shell commands) and tell me exactly what it contains."
  local retry_prompt="Wrong path. Call read_file now with file_path exactly '${FILE_NAME}' — not '/workspace/${FILE_NAME}', not '/${FILE_NAME}'. Then quote the file contents."

  log "Sending read_file prompt (attempt 1/2)..."
  run_ws_turn "$prompt" /tmp/exec-crossview-read-attempt-1.log
  if successful_tool_end /tmp/exec-crossview-read-attempt-1.log "read_file"; then
    log "OK: read_file tool_end succeeded on attempt 1"
    return 0
  fi

  log "WARN: read_file tool_end not observed on attempt 1 - retrying once (LLM nondeterminism allowance)"
  run_ws_turn "$retry_prompt" /tmp/exec-crossview-read-attempt-2.log
  if successful_tool_end /tmp/exec-crossview-read-attempt-2.log "read_file"; then
    log "OK: read_file tool_end succeeded on attempt 2"
    return 0
  fi

  log "ERROR: read_file never produced a successful tool_end after 2 attempts - gate FAILS"
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

step_host_file_final_check() {
  log "Step 5/5: confirming ${HOST_FILE_PATH} genuinely exists on the host..."
  if ! check_host_file_exists; then
    log "ERROR: ${HOST_FILE_PATH} does not exist on the host"
    return 1
  fi
  log "OK: ${HOST_FILE_PATH} exists on the host, content:"
  cat "$HOST_FILE_PATH"
}

cleanup() {
  # Always runs (success or failure) so the script is safely re-runnable.
  if [ -n "$SAVED_HITL" ]; then
    bash "${SCRIPT_DIR}/ensure_hitl.sh" "$SAVED_HITL" >/dev/null 2>&1 || true
  fi
  rm -f "$HOST_FILE_PATH" 2>/dev/null || true
  rm -f /tmp/exec-crossview-attempt-1.log /tmp/exec-crossview-attempt-2.log \
        /tmp/exec-crossview-read-attempt-1.log /tmp/exec-crossview-read-attempt-2.log 2>/dev/null || true
  rest_request DELETE "${API_BASE}/threads/${THREAD_ID}" >/dev/null 2>&1 || true
  # M6-03: code-exec-manager publishes no host port (M4-03) - reached here
  # by execing python3 directly inside its own container against its own
  # localhost:8090 (same "no published port" workaround
  # `scripts/verify_isolation.sh` uses via a separate runner container,
  # simplified here since a session-delete call doesn't need its own
  # network-attached container).
  docker exec homeai-code-exec-manager-1 python3 -c "
import sys, urllib.request
try:
    urllib.request.urlopen(urllib.request.Request(f'http://localhost:8090/sessions/{sys.argv[1]}', method='DELETE'), timeout=15)
except Exception:
    pass
" "$THREAD_ID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

main() {
  log "=== EXEC CROSSVIEW SMOKE (M4-04): execute_code + read_file see the same /workspace ==="
  step_stack_healthy
  # M8-03 made HITL on by default; this smoke's execute_code prompt is not
  # wired to send approval_response, so turn HITL off for the run.
  log "Turning hitl_enabled off so execute_code is not interrupted..."
  SAVED_HITL="$(bash "${SCRIPT_DIR}/ensure_hitl.sh" false)"
  log "OK: hitl_enabled=false (was ${SAVED_HITL})"
  step_execute_code_writes_file
  step_read_file_sees_same_content
  step_no_error_frames
  step_host_file_final_check
  echo "EXEC CROSSVIEW SMOKE: PASS"
}

main
