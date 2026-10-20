#!/usr/bin/env bash
# M22-20 GATE G22: app framework and templates.
# (Installing three templates on the phone and using them for a few days,
# customizing one through Ask the agent, and reading a weekly review in
# Files is Tier B: docs/HOST-CHECKS.md, M22.)
#
# Milestone gate for M22. Chains the M22 checks, stopping at the first
# failure and printing a per-step summary either way:
#   1. Stack healthy: platform container healthy, `/api/health` OK. Fail
#      clearly if Caddy/platform down (do not skip). Deliberately NOT
#      `docker compose up -d --build`: the stack is shared, bring it up
#      from this tree yourself first if it's down. Never recreates
#      model-runner or postgres.
#   2. SDK tests (the router, UI kit, charts and CSV helpers).
#   3. Builder tests (every template compiles and smoke-renders its routes).
#   4. platform pytest: apps, templates, data map, app search, CSV, file
#      versions.
#   5. agent-server pytest: the app tools, app changes, search, the Memory
#      routine and the reviews.
#   6. Frontend jest (whole suite).
#   7-9. The template smokes in headless Chromium, each template installed
#      from the catalog: household, money and health, learning and boards.
#   10. `m22_data_map_smoke.sh` - the data map, and a chat that uses it.
#   11. `m22_app_search_smoke.sh` - app rows found by meaning.
#   12. `m22_csv_smoke.sh` - CSV export and import.
#   13. `m22_text_editor_smoke.sh` - the Files editor's conflict prompt.
#   14. `g22_framework_smoke.sh` - the G22 scenario: an installed template
#      in the data map; its row found by `search_files`, and not by another
#      user; Run now on Memory writes a day file from the row; Run now on
#      the weekly review writes the week and a Lately section.
#
# Throwaway `e2e-*` users only, deleted with what they made; never
# completes the real bootstrap. Compose work takes `/tmp/homeai-stack.lock`.
#
# Usage:
#   scripts/e2e/gate_m22.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

export COMPOSE_PROJECT_NAME=homeai

# Worktrees don't get the gitignored .env; compose still needs it. Never cat it.
if [ ! -f .env ] && [ -f /home/cody/code/unnamed_local_ai_server/.env ]; then
  ln -s /home/cody/code/unnamed_local_ai_server/.env .env
fi

API_BASE="http://localhost/api"
HEALTHY_TIMEOUT_S=600
API_HEALTH_TIMEOUT_S=120
STEPS=14
AGENT_TESTS=(tests/test_app_tools.py tests/test_app_changes_tool.py tests/test_search_tool.py
  tests/test_delivered_routines.py tests/test_memory_routine.py tests/test_memory_reviews.py)
PLATFORM_TESTS=(tests/test_app.py tests/test_app_build.py tests/test_app_changes.py
  tests/test_app_csv.py tests/test_app_exports.py tests/test_app_history.py tests/test_app_search.py
  tests/test_app_sharing.py tests/test_app_templates.py tests/test_appdata_api.py tests/test_appdb.py
  tests/test_appschema.py tests/test_apps_api.py tests/test_data_map.py tests/test_file_search.py
  tests/test_files_api.py)

STEP_NAMES=()
STEP_RESULTS=()
STEP_SECONDS=()

log() {
  echo "[gate-m22] $(date '+%H:%M:%S') $*"
}

cid_of() {
  docker compose -p homeai ps -q "$1" 2>/dev/null | head -n1
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
  log "ERROR: ${service} is not healthy after ${HEALTHY_TIMEOUT_S}s - is the stack up? (docker compose up -d). Do not skip."
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
  log "ERROR: ${API_BASE}/health never came up within ${API_HEALTH_TIMEOUT_S}s (Caddy/platform down? do not skip)"
  return 1
}

stack_healthy() {
  local cid
  cid="$(cid_of caddy)"
  if [ -z "$cid" ]; then
    log "ERROR: Caddy is not running - is the stack up? (docker compose up -d). Do not skip."
    return 1
  fi
  wait_for_healthy platform
  wait_for_api_health
}

agent_tests() {
  (cd "${REPO_ROOT}/services/agent-server" && uv run -q pytest -q -p no:cacheprovider -rs "${AGENT_TESTS[@]}")
}

sdk_tests() {
  (cd "${REPO_ROOT}/packages/homeai-sdk" && npm test --silent)
}

builder_tests() {
  (cd "${REPO_ROOT}/services/app-builder" && npm test --silent)
}

platform_tests() {
  (cd "${REPO_ROOT}/services/platform" && uv run -q pytest -q -p no:cacheprovider -rs "${PLATFORM_TESTS[@]}")
}

frontend_tests() {
  (cd "${REPO_ROOT}/services/frontend" && npx jest --silent)
}

print_summary() {
  local i
  echo
  log "=== SUMMARY ==="
  printf '%-4s %-40s %-7s %8s\n' "STEP" "CHECK" "RESULT" "SECONDS"
  for i in "${!STEP_NAMES[@]}"; do
    printf '%-4s %-40s %-7s %8s\n' "$((i + 1))" "${STEP_NAMES[$i]}" "${STEP_RESULTS[$i]}" "${STEP_SECONDS[$i]}"
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
  echo "GATE M22: FAIL (step $(( ${#STEP_NAMES[@]} ))/${STEPS}: ${name})"
  exit 1
}

main() {
  local pg_before mr_before pg_after mr_after
  pg_before="$(cid_of postgres)"
  mr_before="$(cid_of model-runner)"
  log "=== GATE M22 (G22): app framework and templates ==="
  log "postgres id=${pg_before:-?} model-runner id=${mr_before:-?} (must stay unchanged)"

  step "stack healthy (no rebuild)"           stack_healthy
  step "SDK tests"                            sdk_tests
  step "builder tests"                        builder_tests
  step "platform M22 pytest"                  platform_tests
  step "agent-server M22 pytest"              agent_tests
  step "frontend jest"                        frontend_tests
  step "m22_household_templates_smoke.sh"     bash "${SCRIPT_DIR}/m22_household_templates_smoke.sh"
  step "m22_money_health_templates_smoke.sh"  bash "${SCRIPT_DIR}/m22_money_health_templates_smoke.sh"
  step "m22_learning_boards_templates_smoke"  bash "${SCRIPT_DIR}/m22_learning_boards_templates_smoke.sh"
  step "m22_data_map_smoke.sh"                bash "${SCRIPT_DIR}/m22_data_map_smoke.sh"
  step "m22_app_search_smoke.sh"              bash "${SCRIPT_DIR}/m22_app_search_smoke.sh"
  step "m22_csv_smoke.sh"                     bash "${SCRIPT_DIR}/m22_csv_smoke.sh"
  step "m22_text_editor_smoke.sh"             bash "${SCRIPT_DIR}/m22_text_editor_smoke.sh"
  step "g22_framework_smoke.sh"               bash "${SCRIPT_DIR}/g22_framework_smoke.sh"
  pg_after="$(cid_of postgres)"
  mr_after="$(cid_of model-runner)"
  if [ "$pg_before" != "$pg_after" ] || [ "$mr_before" != "$mr_after" ]; then
    log "ERROR: postgres/model-runner id changed (${pg_before}/${mr_before} -> ${pg_after}/${mr_after})"
    print_summary
    echo "GATE M22: FAIL (postgres/model-runner recreated)"
    exit 1
  fi
  log "postgres and model-runner ids unchanged"

  print_summary
  echo "GATE M22: PASS (${#STEP_NAMES[@]}/${STEPS} steps)"
}

main
