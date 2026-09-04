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
# Usage:
#   scripts/e2e/persistence_smoke.sh
#
# Exits non-zero (and prints the failing step) if any check fails.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

THREAD_ID="persistence-smoke-pg-1"
NAME="Bob"

API_HEALTH_TIMEOUT_S=120
RESTART_GRACE_S=5

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
    if http_ok "http://localhost/api/health"; then
      return 0
    fi
    sleep 3
  done
  return 1
}

# ---- steps -------------------------------------------------------------

step_tell_name() {
  log "Step 1/3: telling the agent my name (thread=${THREAD_ID})..."
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
  log "Step 2/3: restarting agent-server..."
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
  log "Step 3/3: asking the agent my name on the same thread after restart..."
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

cleanup() {
  # M6-03: always runs (success or failure) so this script is safely
  # re-runnable and doesn't leave its checkpoint growing across repeated
  # runs - this script previously had no cleanup at all.
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
  log "=== PERSISTENCE SMOKE (M3-01): checkpoint survives agent-server restart ==="
  step_tell_name
  step_restart_agent_server
  step_ask_name_survived
  echo "PERSISTENCE SMOKE: PASS"
}

main
