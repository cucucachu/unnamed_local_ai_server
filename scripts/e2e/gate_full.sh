#!/usr/bin/env bash
# M6-03: full-scenario e2e chain — the Tier A "gate_full.sh green end to
# end on the host, twice in a row" acceptance criterion.
#
# Chains, in this exact order (per the ticket) against ONE fresh
# `docker compose up -d --build` for the whole run:
#   gate_m2.sh -> tenancy_threads_smoke.sh (M10-04)
#   -> agent_tenancy_smoke.sh (M11-02: the agent's file tools per user/role)
#   -> persistence_smoke.sh
#   -> gate_m3.sh -> exec_crossview_smoke.sh -> gate_m4.sh -> verify_isolation.sh
#   -> verify_tenancy.sh (M11-04: docs/PLATFORM.md §9 invariants 1-7 across
#      Caddy, the files API, delegations, exec, the compose config, and
#      (M12-08) the app sandbox and app RPC)
#   -> verify_network.sh
#   -> auth_browser_smoke.sh (M10-06: sign-in flow; every browser smoke
#      after it signs in as a throwaway CLI user via auth_helpers.mjs)
#   -> admin_browser_smoke.sh (M10-07: Settings invites/spaces/TOTP)
#   -> platform_files_smoke.sh (M11-01: files API role matrix, cross-space
#      move, Range, ownership, straight against `platform`)
#   -> platform_apps_smoke.sh (M12-02: register + install a fixture app,
#      instance dir on disk, reserved Apps folder, uninstall to trash)
#   -> app_build_smoke.sh (M12-04: real builds and their diagnostics)
#   -> app_history_smoke.sh (M13-01: a commit per build, history, revert
#      restores the files and rebuilds; a planted .git in the source stays
#      inert)
#   -> platform_app_data_smoke.sh (M12-03: per-instance SQLite migrations,
#      RPC role matrix, db_changed event, read-only copy for exec)
#   -> app_runner_browser_smoke.sh (M12-06 / M13-04: Apps tab -> sandboxed runner,
#      a written row survives a reload, Ask the agent panel with app context +
#      live db_changed, rebuild hot-reloads, runtime error overlay + Reload,
#      a viewer can't write)
#   -> grocery_app_smoke.sh (M12-07: the reference Grocery list app in a
#      personal and a shared space: add/check/detail/clear, a second
#      member sees it live, a viewer can't write)
#   -> app_publish_smoke.sh (M14-01: author publishes from app info into a
#      family catalog; a family editor installs from Catalog, then
#      approves an update)
#   -> home_launcher_smoke.sh (M14-02: Home is the default tab; system
#      tiles, space switcher, catalog, Chat/Files/Settings tabs)
#   -> files_browser_smoke.sh -> media_browser_smoke.sh -> image_browser_smoke.sh
#   -> video_thumbnail_browser_smoke.sh -> chat_browser_smoke.sh
#   -> gate_m7.sh (M7-07, added here per that ticket's own spec: "Add
#      gate_m7.sh to scripts/e2e/gate_full.sh" - the first milestone gate
#      script appended to this chain since M6-03 first wrote it; future
#      milestone gates append the same way)
#   -> gate_m8.sh (M8-08, same append convention: Stop/HITL/edit/fork/
#      thinking via chat_browser_smoke.sh + pending-approval restart via
#      persistence_smoke.sh. Re-runs those two scripts after the earlier
#      standalone steps; left in place deliberately, same idempotent
#      reasoning as gate_m7.sh re-running verify_network.sh.)
#   -> gate_m9.sh (M9-07, same append convention: verify_network.sh +
#      chat_browser_smoke.sh's markdown/activity-panel/file-link/voice
#      coverage, this time over `https://homeai.local` with Caddy's
#      local CA trusted. Re-runs verify_network.sh and
#      chat_browser_smoke.sh yet again — same idempotent reasoning as the
#      gate_m7.sh/gate_m8.sh steps above.)
#   -> image_browser_smoke.sh (issue #124: upload -> real thumbnail <img> ->
#      direct-tap in-app viewer, no download -> right-click "View" action.
#      Inserted right after media_browser_smoke.sh, its closest sibling —
#      same "one dedicated script per Files-tab preview feature" shape as
#      that M5-02 script.)
#   -> video_thumbnail_browser_smoke.sh (issue #125: seeded video -> real
#      ffmpeg-generated poster-frame thumbnail <img> in the Files list ->
#      still cached/loads on revisit -> direct-tap playback still works.
#      Inserted right after image_browser_smoke.sh, its closest sibling —
#      same "one dedicated script per Files-tab preview feature" shape.)
#   -> gate_m10.sh (M10-08) -> gate_m11.sh (M11-05: cross-user isolation
#      across the Files UI, the agent's file tools, and exec)
#   -> gate_m12.sh (M12-08: builder/SDK/platform app tests, the app smokes,
#      the reference app in personal and shared spaces, verify_tenancy.sh
#      with invariant 7)
#   -> gate_m13.sh (M13-05: history, app tools pytest, Ask-the-agent
#      runner smoke, and the G13 real-model grocery-list scenario)
#   -> gate_m14.sh (M14-06: publish/install smoke, Home launcher, platform
#      export/system-app/grants pytest, and the G14 calendar/planner
#      scenario — no extra GPU run)
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
# M10-04: every API call needs a session and settings are per user, so this
# script signs in once as a throwaway `e2e-*` user (`lib/auth.sh`) before
# that PUT; the curl/urllib/WS scripts it runs inherit the exported session
# (and so the HITL setting), and the user is deleted on exit. The browser
# smokes sign in as their own users. tenancy_threads_smoke.sh (two more
# users, cross-user isolation) runs right after gate_m2.sh.
#
# Every one of these is already a self-contained script that exits non-zero
# on its own failure and does its own health-waiting/cleanup (several also
# do their own internal `docker compose up -d --build` — left in place
# deliberately, per the ticket: after this script's own initial `up`, those
# calls are just fast no-ops). This script's only job is to run all 16 in
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
# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"
trap e2e_auth_end EXIT

log() {
  echo "[gate-full] $(date '+%H:%M:%S') $*"
}

# Not directly used by this script (exec_crossview_smoke.sh, gate_m4.sh and
# verify_isolation.sh read their own copy from .env for execute_code's
# /files mount) — resolved and sanity-checked once up front so a missing/misconfigured
# .env fails fast with one clear message instead of 10 confusing sub-script
# errors.
FILES_DIR="$(sed -n 's/^FILES_DIR=\(.*\)$/\1/p' .env | head -n1 | xargs)"
if [ -z "$FILES_DIR" ]; then
  echo "[gate-full] ERROR: FILES_DIR not set in .env" >&2
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
  e2e_auth_begin gate-full
  log "Signed in as ${E2E_AUTH_USER} for the whole chain"
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
  run_step "tenancy_threads_smoke.sh" bash "${SCRIPT_DIR}/tenancy_threads_smoke.sh"
  run_step "agent_tenancy_smoke.sh"  bash "${SCRIPT_DIR}/agent_tenancy_smoke.sh"
  run_step "persistence_smoke.sh"    bash "${SCRIPT_DIR}/persistence_smoke.sh"
  run_step "gate_m3.sh"              bash "${SCRIPT_DIR}/gate_m3.sh"
  run_step "exec_crossview_smoke.sh" bash "${SCRIPT_DIR}/exec_crossview_smoke.sh"
  run_step "gate_m4.sh"              bash "${SCRIPT_DIR}/gate_m4.sh"
  run_step "verify_isolation.sh"     bash "${REPO_ROOT}/scripts/verify_isolation.sh"
  run_step "verify_tenancy.sh"       bash "${REPO_ROOT}/scripts/verify_tenancy.sh"
  run_step "verify_network.sh"       sudo bash "${REPO_ROOT}/scripts/verify_network.sh"
  run_step "auth_browser_smoke.sh"   bash "${SCRIPT_DIR}/auth_browser_smoke.sh"
  run_step "admin_browser_smoke.sh"  bash "${SCRIPT_DIR}/admin_browser_smoke.sh"
  run_step "platform_files_smoke.sh" bash "${SCRIPT_DIR}/platform_files_smoke.sh"
  run_step "platform_apps_smoke.sh"  bash "${SCRIPT_DIR}/platform_apps_smoke.sh"
  run_step "app_build_smoke.sh"      bash "${SCRIPT_DIR}/app_build_smoke.sh"
  run_step "app_history_smoke.sh"    bash "${SCRIPT_DIR}/app_history_smoke.sh"
  run_step "platform_app_data_smoke.sh" bash "${SCRIPT_DIR}/platform_app_data_smoke.sh"
  run_step "app_runner_browser_smoke.sh" bash "${SCRIPT_DIR}/app_runner_browser_smoke.sh"
  # M13-04: the same smoke also opens Ask the agent (context + db_changed).
  run_step "grocery_app_smoke.sh"    bash "${SCRIPT_DIR}/grocery_app_smoke.sh"
  run_step "app_publish_smoke.sh"    bash "${SCRIPT_DIR}/app_publish_smoke.sh"
  run_step "home_launcher_smoke.sh"  bash "${SCRIPT_DIR}/home_launcher_smoke.sh"
  run_step "files_browser_smoke.sh"  bash "${SCRIPT_DIR}/files_browser_smoke.sh"
  run_step "media_browser_smoke.sh"  bash "${SCRIPT_DIR}/media_browser_smoke.sh"
  run_step "image_browser_smoke.sh"  bash "${SCRIPT_DIR}/image_browser_smoke.sh"
  run_step "video_thumbnail_browser_smoke.sh" bash "${SCRIPT_DIR}/video_thumbnail_browser_smoke.sh"
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
  # M9-07: gate_m9.sh re-runs verify_network.sh + chat_browser_smoke.sh
  # (this time over https://homeai.local) as part of its own chain (see
  # that script's header). Same idempotent reasoning as the gate_m7.sh/
  # gate_m8.sh steps above.
  run_step "gate_m9.sh"              bash "${SCRIPT_DIR}/gate_m9.sh"
  # M10-08: gate_m10.sh re-runs the M10 platform/tenancy/auth smokes above
  # as its own chain; same idempotent reasoning.
  run_step "gate_m10.sh"             bash "${SCRIPT_DIR}/gate_m10.sh"
  # M11-05: gate_m11.sh re-runs verify_tenancy/verify_isolation and the
  # files/agent/exec smokes above, then the G11 shared-space scenario
  # (shared_space_viewer_smoke.sh); same idempotent reasoning. It skips
  # verify_network.sh unless run as root — the sudo step above covers it.
  run_step "gate_m11.sh"             bash "${SCRIPT_DIR}/gate_m11.sh"
  # M12-08: gate_m12.sh re-runs the app smokes and verify_tenancy.sh above,
  # plus the builder/SDK/platform app unit tests; same idempotent reasoning.
  run_step "gate_m12.sh"             bash "${SCRIPT_DIR}/gate_m12.sh"
  # M13-05: gate_m13.sh re-runs app_history_smoke.sh and
  # app_runner_browser_smoke.sh above, plus app-tools pytest and the G13
  # real-model grocery-list scenario; same idempotent reasoning.
  run_step "gate_m13.sh"             bash "${SCRIPT_DIR}/gate_m13.sh"
  # M14-06: gate_m14.sh re-runs app_publish_smoke.sh and
  # home_launcher_smoke.sh above, plus platform export/system-app/grants
  # pytest and the G14 calendar/planner scenario; same idempotent reasoning.
  run_step "gate_m14.sh"             bash "${SCRIPT_DIR}/gate_m14.sh"

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
