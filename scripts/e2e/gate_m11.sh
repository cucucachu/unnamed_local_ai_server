#!/usr/bin/env bash
# M11-05 GATE G11: cross-user isolation across UI, agent tools, and exec.
#
# Milestone gate for M11 (files & tenancy). Chains the M11 checks rather
# than duplicating them, failing fast on the first failure:
#   1. Stack healthy: model-runner and platform containers healthy,
#      `/api/health` OK. Deliberately NOT `docker compose up -d --build`
#      (unlike gate_m10.sh/gate_m4.sh): the stack is shared, and nothing
#      here needs a rebuild — bring it up yourself first if it's down.
#   2. `scripts/verify_tenancy.sh`   — docs/PLATFORM.md §9 invariants 1-6
#      (sessions/identity at Caddy, personal-space privacy and viewer
#      writes via the files API, delegations and exec, agent runs never
#      administer, agent-server holds no user data, read-only roots).
#   3. `scripts/verify_isolation.sh` — exec container hardening, runs as
#      the user with one bind per space (read-only for a viewer), app
#      builds.
#   4. `platform_files_smoke.sh`     — files API role matrix, cross-space
#      move, Range, ownership on disk, path guards.
#   5. `files_browser_smoke.sh`      — Files UI in a real browser: space
#      tree, full file flow, cross-space move, viewer gets the read-only UI.
#   6. `agent_tenancy_smoke.sh`      — the agent's file tools per user and
#      role, real model (personal isolation, editor edits, viewer refused).
#   7. `exec_crossview_smoke.sh`     — `execute_code` and the file tools see
#      the same personal files, with the user's uid/gid on disk.
#   8. `shared_space_viewer_smoke.sh` — the G11 scenario, real model: A's
#      agent writes into a shared space; viewer B reads it in the Files UI
#      (Playwright), B's agent's write into it is refused and nothing is
#      written, and B's `execute_code` sees it read-only.
#   9. `scripts/verify_network.sh`   — LAN-only posture. Needs root: run
#      when this gate runs as root, otherwise skipped with a message (run
#      `sudo scripts/verify_network.sh` yourself; docs/HOST-CHECKS.md M11).
#
# Every sub-script uses throwaway `e2e-*` users and spaces from the
# recovery CLI and cleans up after itself (users, spaces, threads, exec
# containers); none of them completes the real bootstrap.
#
# Usage:
#   scripts/e2e/gate_m11.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

API_BASE="http://localhost/api"
HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120
STEPS=9

log() {
  echo "[gate-m11] $(date '+%H:%M:%S') $*"
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

run() {
  local n="$1" script="$2"
  log "Step ${n}/${STEPS}: ${script#"${REPO_ROOT}/"}..."
  bash "$script"
}

step_verify_network() {
  if [ "${EUID}" -eq 0 ]; then
    run 9 "${REPO_ROOT}/scripts/verify_network.sh"
    NETWORK_RESULT="verify_network.sh passed"
  else
    log "Step 9/${STEPS}: SKIPPED scripts/verify_network.sh - it needs root and this gate runs as uid ${EUID}."
    log "  Run it yourself: sudo scripts/verify_network.sh (docs/HOST-CHECKS.md, M11)."
    NETWORK_RESULT="verify_network.sh SKIPPED: not root"
  fi
}

main() {
  log "=== GATE M11 (G11): cross-user isolation across UI, agent tools, and exec ==="
  log "Step 1/${STEPS}: checking the stack is up and healthy (no rebuild)..."
  wait_for_healthy model-runner
  wait_for_healthy platform
  wait_for_api_health
  run 2 "${REPO_ROOT}/scripts/verify_tenancy.sh"
  run 3 "${REPO_ROOT}/scripts/verify_isolation.sh"
  run 4 "${SCRIPT_DIR}/platform_files_smoke.sh"
  run 5 "${SCRIPT_DIR}/files_browser_smoke.sh"
  run 6 "${SCRIPT_DIR}/agent_tenancy_smoke.sh"
  run 7 "${SCRIPT_DIR}/exec_crossview_smoke.sh"
  run 8 "${SCRIPT_DIR}/shared_space_viewer_smoke.sh"
  step_verify_network
  echo "GATE M11: PASS (${NETWORK_RESULT})"
}

main
