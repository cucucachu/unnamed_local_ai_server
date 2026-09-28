#!/usr/bin/env bash
# M12-08 GATE G12: the reference app runs in personal and shared spaces on
# web and phone (the phone half is Tier B: docs/HOST-CHECKS.md, M12).
#
# Milestone gate for M12 (apps). Chains the M12 checks rather than
# duplicating them, stopping at the first failure and printing a per-step
# summary either way:
#   1. Stack healthy: the platform container healthy, `/api/health` OK,
#      the builder image present. Deliberately NOT `docker compose up -d
#      --build` (like gate_m11.sh): the stack is shared — bring it up from
#      this tree yourself first, and build the builder image
#      (services/app-builder/build-builder-image.sh).
#   2. `services/app-builder` `npm test` — runtime build, compile/type/
#      import diagnostics, smoke render (incl. the reference app).
#   3. `packages/homeai-sdk` `npm test`  — SDK, bridge, host library, the
#      runtime in Chromium.
#   4. `services/platform` pytest, the app modules: manifest, registry,
#      app schema/migrations, per-instance SQLite, RPC, builds.
#   5. `platform_apps_smoke.sh`        — register/install/uninstall live.
#   6. `platform_app_data_smoke.sh`    — migrations, RPC role matrix,
#      `db_changed`, the read-only copy for exec.
#   7. `app_build_smoke.sh`            — real builds and their diagnostics.
#   8. `app_runtime_smoke.sh`          — the runtime page and host library
#      against the live platform (CSP'd frame, RPC, events, escape probes).
#   9. `app_runner_browser_smoke.sh`   — the web app's Apps tab + runner.
#  10. `grocery_app_smoke.sh`          — the G12 scenario: the reference
#      Grocery list app in a personal and a shared space; a second member
#      sees edits live (`db_changed`); a viewer can't write.
#  11. `scripts/verify_tenancy.sh`     — docs/PLATFORM.md §9 invariants 1-7
#      (7: the sandbox holds no credentials; app RPC is scoped).
#
# Every sub-script uses throwaway `e2e-*` users and spaces from the
# recovery CLI and deletes what it created (users, spaces, apps, instances,
# bundles); none of them completes the real bootstrap. Needs no sudo.
#
# Usage:
#   scripts/e2e/gate_m12.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

API_BASE="http://localhost/api"
HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120
BUILDER_IMAGE="homeai-app-builder:latest"
STEPS=11
PLATFORM_TESTS=(tests/test_manifest.py tests/test_apps_api.py tests/test_appschema.py
  tests/test_appdb.py tests/test_appdata_api.py tests/test_app_build.py)

STEP_NAMES=()
STEP_RESULTS=()
STEP_SECONDS=()

log() {
  echo "[gate-m12] $(date '+%H:%M:%S') $*"
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

# $1 package dir: `npm ci` if it has no node_modules yet, then `npm test`.
npm_test() {
  (
    cd "${REPO_ROOT}/$1"
    [ -d node_modules ] || npm ci --no-audit --no-fund
    npm test
  )
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
  echo "GATE M12: FAIL (step $(( ${#STEP_NAMES[@]} ))/${STEPS}: ${name})"
  exit 1
}

main() {
  log "=== GATE M12 (G12): reference app in personal and shared spaces ==="
  step "stack healthy (no rebuild)"          stack_healthy
  step "app-builder npm test"                npm_test services/app-builder
  step "homeai-sdk npm test"                 npm_test packages/homeai-sdk
  step "platform app pytest"                 platform_tests
  step "platform_apps_smoke.sh"              bash "${SCRIPT_DIR}/platform_apps_smoke.sh"
  step "platform_app_data_smoke.sh"          bash "${SCRIPT_DIR}/platform_app_data_smoke.sh"
  step "app_build_smoke.sh"                  bash "${SCRIPT_DIR}/app_build_smoke.sh"
  step "app_runtime_smoke.sh"                bash "${SCRIPT_DIR}/app_runtime_smoke.sh"
  step "app_runner_browser_smoke.sh"         bash "${SCRIPT_DIR}/app_runner_browser_smoke.sh"
  step "grocery_app_smoke.sh"                bash "${SCRIPT_DIR}/grocery_app_smoke.sh"
  step "verify_tenancy.sh"                   bash "${REPO_ROOT}/scripts/verify_tenancy.sh"
  print_summary
  echo "GATE M12: PASS (${#STEP_NAMES[@]}/${STEPS} steps)"
}

main
