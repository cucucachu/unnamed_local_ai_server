#!/usr/bin/env bash
# M14-06 GATE G14: family installs a published app; planner reads calendar
# exports. (The two-phone half is Tier B: docs/HOST-CHECKS.md, M14.)
#
# Milestone gate for M14 (sharing & system apps). Chains the M14 checks
# rather than duplicating them, stopping at the first failure and printing
# a per-step summary either way:
#   1. Stack healthy: platform container healthy, `/api/health` OK (Caddy
#      → agent-server; agent-server has no compose healthcheck), the
#      builder image present. Deliberately
#      NOT `docker compose up -d --build` (like gate_m13.sh): the stack is
#      shared — bring it up from this tree yourself first if it's down.
#      Never recreates model-runner or postgres. No GPU scenario (G13
#      already has one).
#   2. `app_publish_smoke.sh`     — M14-01: family catalog install + update
#      approval (the "family installs a published app" half).
#   3. `home_launcher_smoke.sh`   — M14-02 Home.
#   4. Platform pytest (host `uv run pytest`): `test_app_exports.py`
#      (planner reads both calendars, cannot write, ungranted denied),
#      `test_system_apps.py` (M14-03), `test_exec_grants.py` (M14-05
#      grants), `test_manifest.py` (export/read contract).
#   5. `g14_exports_smoke.sh`     — the G14 scenario through Caddy: calendar
#      in personal + family, published planner pinned-installed in family
#      with `granted_reads`, merged `calendar_events` view, write denied,
#      ungranted app cannot see the view.
#
# `verify_tenancy.sh` stays out of this gate (already in `gate_full.sh`
# earlier; check 29 is M14-05). Every sub-script uses throwaway `e2e-*`
# users and spaces from the recovery CLI and deletes what it created
# (users, spaces, apps, instances, bundles, git repos, app-releases);
# none of them completes the real bootstrap. Needs no sudo. Compose work
# takes `/tmp/homeai-stack.lock`.
#
# Usage:
#   scripts/e2e/gate_m14.sh

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
PLATFORM_TESTS=(tests/test_app_exports.py tests/test_system_apps.py
  tests/test_exec_grants.py tests/test_manifest.py)

STEP_NAMES=()
STEP_RESULTS=()
STEP_SECONDS=()

log() {
  echo "[gate-m14] $(date '+%H:%M:%S') $*"
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
  wait_for_healthy platform
  wait_for_api_health
  docker image inspect "$BUILDER_IMAGE" >/dev/null 2>&1 || {
    log "ERROR: no ${BUILDER_IMAGE} - run services/app-builder/build-builder-image.sh"
    return 1
  }
}

platform_tests() {
  (cd "${REPO_ROOT}/services/platform" && uv run -q pytest -q -p no:cacheprovider -rs "${PLATFORM_TESTS[@]}")
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
  echo "GATE M14: FAIL (step $(( ${#STEP_NAMES[@]} ))/${STEPS}: ${name})"
  exit 1
}

main() {
  log "=== GATE M14 (G14): family installs a published app; planner reads calendar exports ==="
  step "stack healthy (no rebuild)"     stack_healthy
  step "app_publish_smoke.sh"           bash "${SCRIPT_DIR}/app_publish_smoke.sh"
  step "home_launcher_smoke.sh"         bash "${SCRIPT_DIR}/home_launcher_smoke.sh"
  step "platform M14 pytest"            platform_tests
  step "g14_exports_smoke.sh"           bash "${SCRIPT_DIR}/g14_exports_smoke.sh"
  print_summary
  echo "GATE M14: PASS (${#STEP_NAMES[@]}/${STEPS} steps)"
}

main
