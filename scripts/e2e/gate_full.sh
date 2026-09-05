#!/usr/bin/env bash
# M6-03: full-scenario e2e chain — the Tier A "gate_full.sh green end to
# end on the host, twice in a row" acceptance criterion.
#
# Chains, in this exact order (per the ticket) against ONE fresh
# `docker compose up -d --build` for the whole run:
#   gate_m2.sh -> persistence_smoke.sh -> gate_m3.sh -> exec_crossview_smoke.sh
#   -> gate_m4.sh -> verify_isolation.sh -> verify_network.sh
#   -> files_browser_smoke.sh -> media_browser_smoke.sh -> chat_browser_smoke.sh
#   -> gate_m7.sh (M7-07, added here per that ticket's own spec: "Add
#      gate_m7.sh to scripts/e2e/gate_full.sh" - the first milestone gate
#      script appended to this chain since M6-03 first wrote it; future
#      milestone gates append the same way)
#   -> gate_m8.sh (M8-08, same append convention: Stop/HITL/edit/fork/
#      thinking via chat_browser_smoke.sh + pending-approval restart via
#      persistence_smoke.sh. Re-runs those two scripts after the earlier
#      standalone steps; left in place deliberately, same idempotent
#      reasoning as gate_m7.sh re-running verify_network.sh.)
#
# M8-08: after the initial compose up, this script waits for /api/health
# and PUTs hitl_enabled=false. HITL is on by default (M8-03); older mutating
# gates (m2/m3/m4/exec/research) send write_file/execute_code without an
# approval_response and would stall on approval_request. Those scripts also
# disable HITL themselves now; this chain-level PUT is belt-and-suspenders
# so a leftover `true` from a previous UI toggle cannot fail the first
# step. persistence_smoke.sh / chat_browser_smoke.sh / gate_m8.sh turn HITL
# back on for their own assertions and restore afterwards.
#
# Every one of these is already a self-contained script that exits non-zero
# on its own failure and does its own health-waiting/cleanup (several also
# do their own internal `docker compose up -d --build` — left in place
# deliberately, per the ticket: after this script's own initial `up`, those
# calls are just fast no-ops). This script's only job is to run all 12 in
# order, capture PASS/FAIL + wall-clock seconds for each, and CONTINUE to
# the next one even if a step fails — so a single run gives the full
# picture instead of stopping at the first red — then print a summary table
# and exit 1 if anything failed.
#
# M6-03 also did a naming/idempotency/cleanup sweep across these scripts
# (see each script's own "M6-03:" comments for exactly what changed) so
# they don't fight over thread ids/files when run back-to-back inside this
# one chain, twice in a row, against the same live stack.
#
# `verify_network.sh` (M6-01) reads live `ufw`/`iptables` state and refuses
# to run as a non-root user — it is invoked here with `sudo`, exactly as its
# own usage comment documents (`sudo scripts/verify_network.sh`), which is
# the CORRECT way to call it: a human running this script interactively on
# the real host (with `sudo -v` cached, or willing to type a password when
# prompted) gets a real PASS/FAIL from it. In a non-interactive environment
# with no cached/passwordless sudo, this one step will fail (sudo refuses a
# password prompt with no tty) — that is an environment limitation, not a
# bug in this script, and is NOT special-cased away here (per the ticket:
# "the script should be correct for a human running it interactively").
#
# Usage:
#   scripts/e2e/gate_full.sh
#
# Exits 0 if every step passed, 1 if any failed (see the summary table for
# which — re-run that one script directly for the full failure transcript).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

log() {
  echo "[gate-full] $(date '+%H:%M:%S') $*"
}

# Not directly used by this script (each sub-script reads its own copy from
# .env) — resolved and sanity-checked once up front so a missing/misconfigured
# .env fails fast with one clear message instead of 10 confusing sub-script
# errors.
WORKSPACE_DIR="$(sed -n 's/^WORKSPACE_DIR=\(.*\)$/\1/p' .env | head -n1 | xargs)"
if [ -z "$WORKSPACE_DIR" ]; then
  echo "[gate-full] ERROR: WORKSPACE_DIR not set in .env" >&2
  exit 1
fi

STEP_NAMES=()
STEP_STATUSES=()
STEP_SECONDS=()

API_BASE="http://localhost/api"
API_HEALTH_TIMEOUT_S=120

http_ok() {
  wget -q -O /dev/null --timeout=10 --tries=1 "$1" >/dev/null 2>&1
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

step_stack_up() {
  log "=== Bringing up the full compose stack (one docker compose up -d --build for the whole chain) ==="
  docker compose up -d --build
  log "Waiting for agent-server API health (timeout ${API_HEALTH_TIMEOUT_S}s)..."
  if ! wait_for_api_health "$API_HEALTH_TIMEOUT_S"; then
    log "ERROR: ${API_BASE}/health never came up within ${API_HEALTH_TIMEOUT_S}s"
    return 1
  fi
  # HITL-on-by-default would stall pre-M8 mutating gates on approval_request.
  log "Turning hitl_enabled off for pre-M8 mutating steps..."
  bash "${SCRIPT_DIR}/ensure_hitl.sh" false >/dev/null
  log "OK: docker compose up -d --build done; hitl_enabled=false"
}

# $1: display name for the summary table. $2..: the command to run.
#
# `set +e` / `set -e` bracketing the call (rather than `cmd || rc=$?`) is
# deliberate — under `set -euo pipefail`, a bare `"$@"` failing inside an
# `if`/`&&` context is the one form `set -e` reliably exempts, but capturing
# BOTH stdout-to-terminal (no redirection - the whole point is to watch each
# step live) AND the exit code needs the explicit toggle, same pattern
# `verify_isolation.sh`'s own `check_generic` uses for the same `set -e`
# reason.
run_step() {
  local name="$1"
  shift
  log "--- Running ${name} ---"
  local start end elapsed rc
  start="$(date +%s)"
  set +e
  "$@"
  rc=$?
  set -e
  end="$(date +%s)"
  elapsed=$(( end - start ))

  STEP_NAMES+=("$name")
  STEP_SECONDS+=("$elapsed")
  if [ "$rc" -eq 0 ]; then
    STEP_STATUSES+=("PASS")
    log "${name}: PASS (${elapsed}s)"
  else
    STEP_STATUSES+=("FAIL")
    log "${name}: FAIL (${elapsed}s, exit code ${rc})"
  fi
}

print_summary() {
  echo
  log "=== SUMMARY ==="
  printf '%-28s %-6s %10s\n' "SCRIPT" "RESULT" "SECONDS"
  printf '%-28s %-6s %10s\n' "----------------------------" "------" "----------"
  local i
  for i in "${!STEP_NAMES[@]}"; do
    printf '%-28s %-6s %10s\n' "${STEP_NAMES[$i]}" "${STEP_STATUSES[$i]}" "${STEP_SECONDS[$i]}"
  done
  echo
}

main() {
  log "=== GATE FULL (M6-03): chained full-scenario e2e run ==="
  step_stack_up

  run_step "gate_m2.sh"              bash "${SCRIPT_DIR}/gate_m2.sh"
  run_step "persistence_smoke.sh"    bash "${SCRIPT_DIR}/persistence_smoke.sh"
  run_step "gate_m3.sh"              bash "${SCRIPT_DIR}/gate_m3.sh"
  run_step "exec_crossview_smoke.sh" bash "${SCRIPT_DIR}/exec_crossview_smoke.sh"
  run_step "gate_m4.sh"              bash "${SCRIPT_DIR}/gate_m4.sh"
  run_step "verify_isolation.sh"     bash "${REPO_ROOT}/scripts/verify_isolation.sh"
  run_step "verify_network.sh"       sudo bash "${REPO_ROOT}/scripts/verify_network.sh"
  run_step "files_browser_smoke.sh"  bash "${SCRIPT_DIR}/files_browser_smoke.sh"
  run_step "media_browser_smoke.sh"  bash "${SCRIPT_DIR}/media_browser_smoke.sh"
  run_step "chat_browser_smoke.sh"   bash "${SCRIPT_DIR}/chat_browser_smoke.sh"
  # M7-07: gate_m7.sh already re-runs verify_network.sh/verify_isolation.sh
  # itself as part of its own chain (see that script's own header comment)
  # - left in place deliberately, same "self-contained scripts fighting
  # each other is fine, they're idempotent" reasoning M6-03 already applied
  # to every other step above.
  run_step "gate_m7.sh"              bash "${SCRIPT_DIR}/gate_m7.sh"
  # M8-08: gate_m8.sh re-runs chat_browser_smoke.sh + persistence_smoke.sh
  # as part of its own chain (see that script's header). Same "self-contained
  # scripts are idempotent" reasoning as the gate_m7.sh step above.
  run_step "gate_m8.sh"              bash "${SCRIPT_DIR}/gate_m8.sh"

  print_summary

  local any_failed=0 i
  for i in "${!STEP_STATUSES[@]}"; do
    if [ "${STEP_STATUSES[$i]}" != "PASS" ]; then
      any_failed=1
    fi
  done

  if [ "$any_failed" -eq 1 ]; then
    log "GATE FULL: FAIL (see summary table above)"
    exit 1
  fi
  log "GATE FULL: PASS (all ${#STEP_NAMES[@]} steps green)"
}

main
