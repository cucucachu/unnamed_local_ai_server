#!/usr/bin/env bash
# M13-05 GATE G13: "make me a grocery list app" end-to-end with the real
# model; the agent and the UI edit the same data.
#
# Milestone gate for M13 (agent builds apps). Chains the M13 checks rather
# than duplicating them, stopping at the first failure and printing a
# per-step summary either way:
#   1. Stack healthy: model-runner and platform containers healthy,
#      `/api/health` OK, the builder image present. Deliberately NOT
#      `docker compose up -d --build` (like gate_m12.sh): the stack is
#      shared — bring it up from this tree yourself first if it's down.
#      Never recreates model-runner or postgres.
#   2. `app_history_smoke.sh`     — M13-01: a commit per build, history,
#      revert restores the files and rebuilds; a planted .git stays inert.
#   3. agent-server `test_app_tools.py` — M13-02: create_app / build_app /
#      app_sql / app_action / approve_migration (fake model, no GPU).
#   4. `app_runner_browser_smoke.sh` — M12-06 / M13-04: Apps tab, sandboxed
#      runner, Ask the agent panel + live db_changed.
#   5. `g13_grocery_smoke.sh`     — the G13 scenario, real model: create a
#      grocery list app from chat, iterate once ("add quantities"), use it
#      in the runner, the agent reads a UI write and writes a row the UI
#      shows, then revert a change.
#
# The authoring eval (`scripts/eval/app_authoring/`, M13-03) stays out of
# this gate and out of `gate_full.sh`: ten GPU prompts, tens of minutes;
# G13 is the one real-model scenario here.
#
# Every sub-script uses throwaway `e2e-*` users and spaces from the
# recovery CLI and deletes what it created (users, spaces, apps, instances,
# bundles, git repos); none of them completes the real bootstrap. Needs no
# sudo. GPU / compose work takes `/tmp/homeai-stack.lock`.
#
# Usage:
#   scripts/e2e/gate_m13.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

API_BASE="http://localhost/api"
HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120
BUILDER_IMAGE="homeai-app-builder:latest"
STEPS=5

STEP_NAMES=()
STEP_RESULTS=()
STEP_SECONDS=()

log() {
  echo "[gate-m13] $(date '+%H:%M:%S') $*"
}

http_ok() {
  wget -q -O /dev/null --timeout=10 --tries=1 "$1" >/dev/null 2>&1
}

wait_for_healthy() {
  local service="$1" deadline cid status
  log "Waiting for ${service} container health (timeout ${HEALTHY_TIMEOUT_S}s)..."
  deadline=$(( $(date +%s) + HEALTHY_TIMEOUT_S ))
  while (( $(date +%s) < deadline )); do
    cid="$(docker compose ps -q "$service" || true)"
    if [ -n "$cid" ]; then
      status="$(docker inspect --format '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo "unknown")"
      if [ "$status" = "healthy" ]; then
        return 0
      fi
    fi
    sleep 5
  done
  log "ERROR: ${service} is not healthy after ${HEALTHY_TIMEOUT_S}s - is the stack up? (docker compose up -d)"
  return 1
}

wait_for_api_health() {
  local deadline=$(( $(date +%s) + API_HEALTH_TIMEOUT_S ))
  while (( $(date +%s) < deadline )); do
    if http_ok "${API_BASE}/health"; then
      return 0
    fi
    sleep 3
  done
  log "ERROR: ${API_BASE}/health never came up within ${API_HEALTH_TIMEOUT_S}s"
  return 1
}

stack_healthy() {
  wait_for_healthy model-runner
  wait_for_healthy platform
  wait_for_api_health
  docker image inspect "$BUILDER_IMAGE" >/dev/null 2>&1 || {
    log "ERROR: no ${BUILDER_IMAGE} - run services/app-builder/build-builder-image.sh"
    return 1
  }
}

app_tools_pytest() {
  (cd "${REPO_ROOT}/services/agent-server" && uv run -q pytest -q -p no:cacheprovider -rs tests/test_app_tools.py)
}

print_summary() {
  local i
  echo
  log "=== SUMMARY ==="
  printf '%-4s %-36s %-7s %8s\n' "STEP" "CHECK" "RESULT" "SECONDS"
  for i in "${!STEP_NAMES[@]}"; do
    printf '%-4s %-36s %-7s %8s\n' "$((i + 1))" "${STEP_NAMES[$i]}" "${STEP_RESULTS[$i]}" "${STEP_SECONDS[$i]}"
  done
  echo
}

# $1 display name, $2.. the command. Stops the gate at the first failure.
step() {
  local name="$1" start rc
  shift
  log "Step $(( ${#STEP_NAMES[@]} + 1 ))/${STEPS}: ${name}..."
  start="$(date +%s)"
  set +e
  "$@"
  rc=$?
  set -e
  STEP_NAMES+=("$name")
  STEP_SECONDS+=("$(( $(date +%s) - start ))")
  if [ "$rc" -eq 0 ]; then
    STEP_RESULTS+=("PASS")
    return 0
  fi
  STEP_RESULTS+=("FAIL")
  log "${name}: FAIL (exit code ${rc}) - stopping; re-run it directly for the full transcript"
  print_summary
  echo "GATE M13: FAIL (step $(( ${#STEP_NAMES[@]} ))/${STEPS}: ${name})"
  exit 1
}

main() {
  log "=== GATE M13 (G13): grocery list app end-to-end with the real model ==="
  step "stack healthy (no rebuild)"     stack_healthy
  step "app_history_smoke.sh"           bash "${SCRIPT_DIR}/app_history_smoke.sh"
  step "agent-server app tools pytest"  app_tools_pytest
  step "app_runner_browser_smoke.sh"    bash "${SCRIPT_DIR}/app_runner_browser_smoke.sh"
  step "g13_grocery_smoke.sh"           bash "${SCRIPT_DIR}/g13_grocery_smoke.sh"
  print_summary
  echo "GATE M13: PASS (${#STEP_NAMES[@]}/${STEPS} steps)"
}

main
