#!/usr/bin/env bash
# M21-12 GATE G21: memory.
# (A few days of normal use from the phone with the Memory routine on is
# Tier B: docs/HOST-CHECKS.md, M21.)
#
# Milestone gate for M21. Chains the M21 checks, stopping at the first
# failure and printing a per-step summary either way:
#   1. Stack healthy: platform container healthy, `/api/health` OK. Fail
#      clearly if Caddy/platform down (do not skip). Deliberately NOT
#      `docker compose up -d --build`: the stack is shared, bring it up
#      from this tree yourself first if it's down. Never recreates
#      model-runner or postgres.
#   2. `embeddings` healthy and without egress (a connect to 1.1.1.1:443
#      from inside it must fail).
#   3. agent-server pytest: the search and chat tools, the memory core,
#      delivered routines and the admin switch, the Memory routine.
#   4. platform pytest: the file index and search, the delivered-routine
#      switches.
#   5. Frontend jest (whole suite).
#   6. `m21_search_smoke.sh` - search by meaning on the live index.
#   7. `m21_chats_smoke.sh` - reading past chats.
#   8. `m21_memory_core_smoke.sh` - the memory core in a new chat.
#   9. `g21_memory_smoke.sh` - the G21 scenario: a chat states a fact; Run
#      now on Memory puts it in Memory without a web or integration call; a
#      new chat recalls it; another user can't see it or find it with
#      `search_files`; deleting the Memory routine is refused.
#
# Throwaway `e2e-*` users only, deleted with what they made; never
# completes the real bootstrap. Compose work takes `/tmp/homeai-stack.lock`.
#
# Usage:
#   scripts/e2e/gate_m21.sh

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
STEPS=9
AGENT_TESTS=(tests/test_search_tool.py tests/test_chat_tools.py tests/test_memory_core.py
  tests/test_delivered_routines.py tests/test_memory_routine.py tests/test_routine_tools.py)
PLATFORM_TESTS=(tests/test_file_search.py tests/test_delivered_routines.py)

STEP_NAMES=()
STEP_RESULTS=()
STEP_SECONDS=()

log() {
  echo "[gate-m21] $(date '+%H:%M:%S') $*"
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

embeddings_isolated() {
  local cid
  cid="$(cid_of embeddings)"
  if [ -z "$cid" ]; then
    log "ERROR: embeddings is not running - is the stack up? Do not skip."
    return 1
  fi
  wait_for_healthy embeddings
  if docker exec "$cid" timeout 3 bash -c 'exec 3<>/dev/tcp/1.1.1.1/443' >/dev/null 2>&1; then
    log "ERROR: embeddings reached 1.1.1.1:443 - it must have no egress"
    return 1
  fi
  log "embeddings is healthy and can't reach the internet"
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
  echo "GATE M21: FAIL (step $(( ${#STEP_NAMES[@]} ))/${STEPS}: ${name})"
  exit 1
}

main() {
  local pg_before mr_before pg_after mr_after
  pg_before="$(cid_of postgres)"
  mr_before="$(cid_of model-runner)"
  log "=== GATE M21 (G21): memory ==="
  log "postgres id=${pg_before:-?} model-runner id=${mr_before:-?} (must stay unchanged)"

  step "stack healthy (no rebuild)"     stack_healthy
  step "embeddings healthy, no egress"  embeddings_isolated
  step "agent-server M21 pytest"        agent_tests
  step "platform M21 pytest"            platform_tests
  step "frontend jest"                  frontend_tests
  step "m21_search_smoke.sh"            bash "${SCRIPT_DIR}/m21_search_smoke.sh"
  step "m21_chats_smoke.sh"             bash "${SCRIPT_DIR}/m21_chats_smoke.sh"
  step "m21_memory_core_smoke.sh"       bash "${SCRIPT_DIR}/m21_memory_core_smoke.sh"
  step "g21_memory_smoke.sh"            bash "${SCRIPT_DIR}/g21_memory_smoke.sh"

  pg_after="$(cid_of postgres)"
  mr_after="$(cid_of model-runner)"
  if [ "$pg_before" != "$pg_after" ] || [ "$mr_before" != "$mr_after" ]; then
    log "ERROR: postgres/model-runner id changed (${pg_before}/${mr_before} -> ${pg_after}/${mr_after})"
    print_summary
    echo "GATE M21: FAIL (postgres/model-runner recreated)"
    exit 1
  fi
  log "postgres and model-runner ids unchanged"

  print_summary
  echo "GATE M21: PASS (${#STEP_NAMES[@]}/${STEPS} steps)"
}

main
