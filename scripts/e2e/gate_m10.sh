#!/usr/bin/env bash
# M10-08 GATE G10: two users, separate devices, isolated threads; admin
# manages users and spaces.
#
# Milestone gate for M10 (identity & spaces). Chains the M10 smokes rather
# than duplicating them, failing fast on the first failure:
#   1. Stack healthy (`docker compose up -d --build`, model-runner healthy,
#      `/api/health` OK).
#   2. `platform_auth_smoke.sh`  — accounts/sessions straight against
#      `platform`: status + setup code while bootstrap is pending (never
#      completed), web + native login, verify -> identity -> `/me`, member
#      refused on admin routes, logout.
#   3. `platform_spaces_smoke.sh` — spaces/members API roles, personal-space
#      privacy, last-owner protection, per-space GID dirs on disk.
#   4. `tenancy_threads_smoke.sh` — through Caddy: unauthenticated 401,
#      forged `X-HomeAI-Identity` ignored, user B gets 404 / WS 4404 for
#      user A's thread.
#   5. `auth_browser_smoke.sh`   — sign-in, logout, invite accept (browser).
#   6. `admin_browser_smoke.sh`  — admin steps up, invites a second browser
#      context, creates a shared space and adds them; TOTP enroll + sign-in.
#
# Rate limiting, step-up windows, and agent-act rejection on admin/auth
# routes are covered by `services/platform` pytest (not re-driven here).
#
# Every sub-script uses throwaway `e2e-*` users from the recovery CLI and
# cleans up after itself; none of them completes the real bootstrap.
#
# Usage:
#   scripts/e2e/gate_m10.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

API_BASE="http://localhost/api"
MODEL_RUNNER_HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120

log() {
  echo "[gate-m10] $(date '+%H:%M:%S') $*"
}

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
        return 0
      fi
    fi
    sleep 5
  done
  log "ERROR: model-runner did not become healthy within ${MODEL_RUNNER_HEALTHY_TIMEOUT_S}s"
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
  local n="$1" name="$2"
  log "Step ${n}/6: ${name}..."
  bash "${SCRIPT_DIR}/${name}"
}

main() {
  log "=== GATE M10 (G10): two users, isolated threads; admin manages users and spaces ==="
  log "Step 1/6: bringing up the full compose stack..."
  docker compose up -d --build
  wait_for_model_runner_healthy
  wait_for_api_health
  run 2 platform_auth_smoke.sh
  run 3 platform_spaces_smoke.sh
  run 4 tenancy_threads_smoke.sh
  run 5 auth_browser_smoke.sh
  run 6 admin_browser_smoke.sh
  echo "GATE M10: PASS"
}

main
