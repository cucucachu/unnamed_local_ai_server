#!/usr/bin/env bash
# M2-07 GATE G1+G2: browser -> agent -> real file write.
#
# Scripted half of the M2 gate (combines README.md's G1 "model serving" and
# G2 "agentic chat" checks). Human/host-only checks (phone browser, visible
# token streaming, PM sign-off) live in `docs/HOST-CHECKS.md` under `## M2`
# instead — see that file's own stated convention, "don't attempt these
# from an automated agent run".
#
# From a clean state, this script:
#   1. Brings the full compose stack up and waits for model-runner healthy
#      + the agent-server API healthy (real polling loops, not fixed sleeps).
#   2. Confirms chat streams: >=1 token frame + turn_end over the real WS.
#   3. Sends a message asking the agent to write a real file, then polls the
#      REAL HOST PATH for it, with one retry on failure (LLM nondeterminism
#      allowance - 2 strikes total before the gate fails).
#   4. Restarts agent-server and confirms the file survives (proves the
#      bind mount persists real files, independent of the in-memory agent/
#      checkpointer - chat memory loss on restart is expected/out of scope
#      here, per the ticket; Postgres persistence lands in M3-01).
#   5. Confirms the web root serves the built Expo bundle via Caddy.
#   6. Cleans up the file it created so re-running this script is safe
#      (idempotent - the acceptance criteria require two green runs in a row).
#
# M6-03: cleanup also deletes the "gate-m2" LangGraph checkpoint via the
# threads REST DELETE endpoint, so this thread's conversation history
# doesn't grow unbounded across repeated runs (e.g. inside gate_full.sh,
# which runs this script standalone AND again via gate_m4.sh's own
# regression step).
#
# `curl` is NOT installed on this host (verified: `apt install curl` would
# be needed, and passwordless sudo isn't available here either). `wget` IS
# available and used as the curl-equivalent for both a plain reachability
# check (`--spider`) and for pulling response bodies (`-O -`) below.
#
# Usage:
#   scripts/e2e/gate_m2.sh
#
# Exits non-zero (and prints the failing step) if any check fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

THREAD_ID="gate-m2"
FILE_NAME="gate-m2.txt"
EXPECTED_CONTENT="GATE-OK"

# WORKSPACE_DIR is the host path bind-mounted into agent-server at
# /data/workspace (see docker-compose.yml + .env) - read it from the real
# .env rather than hardcoding, so this script tracks whatever the host is
# actually configured with.
WORKSPACE_DIR="$(sed -n 's/^WORKSPACE_DIR=\(.*\)$/\1/p' .env | head -n1 | xargs)"
if [ -z "$WORKSPACE_DIR" ]; then
  echo "[gate-m2] ERROR: WORKSPACE_DIR not set in .env" >&2
  exit 1
fi
HOST_FILE_PATH="${WORKSPACE_DIR}/${FILE_NAME}"

MODEL_RUNNER_HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120
FILE_WRITE_TIMEOUT_S=90
RESTART_GRACE_S=5

log() {
  echo "[gate-m2] $(date '+%H:%M:%S') $*"
}

# ---- curl-equivalent helpers (curl not installed; wget is) ----------------

http_ok() {
  # 0 if a GET to $1 is reachable with a successful (2xx/3xx) status.
  #
  # NOTE: deliberately NOT `wget --spider` - this wget version (1.25.0)
  # sends a HEAD request in spider mode, and /api/health is a GET-only
  # FastAPI route (405s on HEAD) - confirmed by testing directly against
  # the real running stack. `-O /dev/null` forces a real GET while still
  # discarding the body, which is what we actually want to check.
  wget -q -O /dev/null --timeout=10 --tries=1 "$1" >/dev/null 2>&1
}

http_body() {
  wget -q -O - --timeout=10 --tries=1 "$1" 2>/dev/null
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
    if http_ok "http://localhost/api/health"; then
      return 0
    fi
    sleep 3
  done
  return 1
}

# ---- gate steps --------------------------------------------------------------

step_stack_up_and_healthy() {
  log "Step 1/5: bringing up the full compose stack..."
  docker compose up -d --build
  wait_for_model_runner_healthy
  log "Waiting for agent-server API health (timeout ${API_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_api_health "$API_HEALTH_TIMEOUT_S"; then
    log "ERROR: http://localhost/api/health never came up within ${API_HEALTH_TIMEOUT_S}s"
    return 1
  fi
  log "OK: model-runner healthy + http://localhost/api/health OK"
}

step_chat_streams() {
  log "Step 2/5: chat streams (thread=${THREAD_ID})..."
  local out
  out="$(mktemp)"
  if ! WS_SMOKE_THREAD_ID="$THREAD_ID" WS_SMOKE_PROMPT="Reply with one short sentence." \
      uvx --from websockets python "$SCRIPT_DIR/../ws_smoke.py" >"$out" 2>&1; then
    log "ERROR: ws_smoke.py exited non-zero:"
    cat "$out"
    rm -f "$out"
    return 1
  fi
  local ok=1
  grep -q "'type': 'token'" "$out" && ok=0 || true
  if [ "$ok" != 0 ]; then
    log "ERROR: no token frame observed:"
    cat "$out"
    rm -f "$out"
    return 1
  fi
  if ! grep -q "'type': 'turn_end'" "$out"; then
    log "ERROR: no turn_end frame observed:"
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
  rm -f "$out"
  log "OK: >=1 token frame + turn_end observed, no error frame"
}

send_file_write_message() {
  WS_SMOKE_THREAD_ID="$THREAD_ID" \
  WS_SMOKE_PROMPT="Create a file named ${FILE_NAME} in the workspace root containing exactly the text ${EXPECTED_CONTENT}. Use your file tools." \
    uvx --from websockets python "$SCRIPT_DIR/../ws_smoke.py"
}

check_file_content() {
  [ -f "$HOST_FILE_PATH" ] || return 1
  python3 -c "
import sys
expected = sys.argv[2]
try:
    content = open(sys.argv[1], encoding='utf-8').read().strip()
except OSError:
    sys.exit(1)
sys.exit(0 if content == expected else 1)
" "$HOST_FILE_PATH" "$EXPECTED_CONTENT"
}

poll_file_content() {
  local timeout_s="$1"
  local deadline=$(( $(date +%s) + timeout_s ))
  while (( $(date +%s) < deadline )); do
    if check_file_content; then
      return 0
    fi
    sleep 3
  done
  return 1
}

step_agent_writes_file() {
  log "Step 3/5: agent writes a real file (same thread, ${FILE_NAME})..."
  rm -f "$HOST_FILE_PATH"

  log "Sending file-write prompt (attempt 1/2)..."
  send_file_write_message >/tmp/gate-m2-write-attempt-1.log 2>&1 || true
  if poll_file_content "$FILE_WRITE_TIMEOUT_S"; then
    log "OK: ${FILE_NAME} created with expected content on attempt 1"
    return 0
  fi

  log "WARN: ${FILE_NAME} not correct within ${FILE_WRITE_TIMEOUT_S}s of attempt 1 - retrying once (LLM nondeterminism allowance)"
  send_file_write_message >/tmp/gate-m2-write-attempt-2.log 2>&1 || true
  if poll_file_content "$FILE_WRITE_TIMEOUT_S"; then
    log "OK: ${FILE_NAME} created with expected content on attempt 2"
    return 0
  fi

  log "ERROR: ${FILE_NAME} still missing/incorrect after 2 attempts - gate FAILS"
  log "--- attempt 1 transcript ---"
  cat /tmp/gate-m2-write-attempt-1.log 2>/dev/null || true
  log "--- attempt 2 transcript ---"
  cat /tmp/gate-m2-write-attempt-2.log 2>/dev/null || true
  return 1
}

step_persistence_across_restart() {
  log "Step 4/5: persistence of ${FILE_NAME} across agent-server restart..."
  docker compose restart agent-server
  log "Waiting for agent-server API health after restart (timeout ${API_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_api_health "$API_HEALTH_TIMEOUT_S"; then
    log "ERROR: agent-server did not report healthy API after restart"
    return 1
  fi
  sleep "$RESTART_GRACE_S"
  if ! check_file_content; then
    log "ERROR: ${FILE_NAME} missing or content changed after agent-server restart"
    return 1
  fi
  log "OK: ${FILE_NAME} persisted across agent-server restart (bind mount, not in-memory agent state)"
}

step_web_build_serves() {
  log "Step 5/5: web root serves the built Expo bundle via Caddy..."
  local body
  body="$(http_body "http://localhost/")"
  if ! printf '%s' "$body" | grep -qE '<script[^>]*src="[^"]*_expo/static/js/[^"]*\.js"'; then
    log "ERROR: http://localhost/ did not contain the expected Expo bundle <script> tag:"
    printf '%s\n' "$body"
    return 1
  fi
  log "OK: Expo bundle script tag found in http://localhost/"
}

cleanup() {
  # Always runs (success or failure) so the script is safely re-runnable.
  rm -f "$HOST_FILE_PATH" 2>/dev/null || true
  # M6-03: also delete the "gate-m2" checkpoint. `PgThreadStore.delete`/
  # `ensure_exists` no-op for this non-UUID thread id (see
  # app/db/threads.py's own docstring - it's a legacy/manual WS-only id,
  # never inserted into the `threads` table), but
  # `checkpointer.adelete_thread` still deletes the real chat-memory
  # checkpoint regardless of UUID-ness, so this is a genuine (not just
  # REST-list-cosmetic) cleanup.
  python3 -c "
import urllib.request
try:
    urllib.request.urlopen(urllib.request.Request('http://localhost/api/threads/${THREAD_ID}', method='DELETE'), timeout=15)
except Exception:
    pass
" 2>/dev/null || true
}
trap cleanup EXIT

main() {
  log "=== GATE M2 (G1+G2): browser -> agent -> real file write ==="
  step_stack_up_and_healthy
  step_chat_streams
  step_agent_writes_file
  step_persistence_across_restart
  step_web_build_serves
  echo "GATE M2: PASS"
}

main
