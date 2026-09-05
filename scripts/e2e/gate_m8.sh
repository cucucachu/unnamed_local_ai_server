#!/usr/bin/env bash
# M8-08 GATE G8: stop, approve/reject, edit (truncate + fork) from a browser.
#
# Milestone gate for M8. Proves every agent control works end-to-end against
# the real model: Stop, HITL approve/reject/off, edit/resend/regenerate,
# fork/switch, thinking on/off (M8-07 shipped GO), plus a pending HITL
# approval surviving `docker compose restart agent-server`.
#
# From a running (or freshly brought-up) compose stack, this script runs,
# in this exact order, failing fast (clear message, non-zero exit) on the
# first failure:
#   1. Stack healthy — `docker compose up -d --build`, wait for
#      model-runner healthy + agent-server `/api/health` (same helpers as
#      `gate_m7.sh`).
#   2. `scripts/e2e/chat_browser_smoke.sh` — already contains the Playwright
#      coverage for M8-01 Stop, M8-03 HITL approve/reject/off, M8-04
#      edit/resend/regenerate, M8-05 fork/switch, and M8-07 thinking on/off.
#      This gate chains that script rather than duplicating those steps.
#   3. `scripts/e2e/persistence_smoke.sh` — thread/message checkpoint
#      survives agent-server restart, plus (M8-08) a pending HITL approval
#      still showing on `GET /api/threads/{id}/state` after another restart.
#
# `curl` is NOT installed on this host (same finding as every other
# `scripts/e2e/*.sh` script) — uses `wget` where an HTTP request is needed
# directly from this script.
#
# Usage:
#   scripts/e2e/gate_m8.sh
#
# Exits non-zero (and prints the failing step) if any check fails.
# Safe to re-run — each sub-script cleans up its own thread/session/file.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

API_BASE="http://localhost/api"
MODEL_RUNNER_HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120

WORKSPACE_DIR="$(sed -n 's/^WORKSPACE_DIR=\(.*\)$/\1/p' .env | head -n1 | xargs)"
if [ -z "$WORKSPACE_DIR" ]; then
  echo "[gate-m8] ERROR: WORKSPACE_DIR not set in .env" >&2
  exit 1
fi
export WORKSPACE_DIR

log() {
  echo "[gate-m8] $(date '+%H:%M:%S') $*"
}

# ---- health helpers (same pattern as every other scripts/e2e/*.sh script) --

http_ok() {
  wget -q -O /dev/null --timeout=10 --tries=1 "$1" >/dev/null 2>&1
}

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

step_stack_up_and_healthy() {
  log "Step 1/3: bringing up the full compose stack..."
  docker compose up -d --build
  wait_for_model_runner_healthy
  log "Waiting for agent-server API health (timeout ${API_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_api_health "$API_HEALTH_TIMEOUT_S"; then
    log "ERROR: ${API_BASE}/health never came up within ${API_HEALTH_TIMEOUT_S}s"
    return 1
  fi
  log "OK: model-runner healthy + ${API_BASE}/health OK"
}

step_chat_browser_smoke() {
  log "Step 2/3: scripts/e2e/chat_browser_smoke.sh (Stop, HITL, edit, fork, thinking)..."
  bash "${SCRIPT_DIR}/chat_browser_smoke.sh"
}

step_persistence_smoke() {
  log "Step 3/3: scripts/e2e/persistence_smoke.sh (checkpoint + pending approval across restart)..."
  bash "${SCRIPT_DIR}/persistence_smoke.sh"
}

main() {
  log "=== GATE M8 (G8): stop, approve/reject, edit (truncate + fork) from a browser ==="
  step_stack_up_and_healthy
  step_chat_browser_smoke
  step_persistence_smoke
  echo "GATE M8: PASS"
}

main
