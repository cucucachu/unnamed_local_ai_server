#!/usr/bin/env bash
# M13-05 (GATE G13) real-model scenario: "make me a grocery list app"
# end-to-end; the agent and the UI edit the same data.
#
# A throwaway `e2e-g13-*` user (recovery CLI, `lib/auth.sh`) with HITL off:
#   1. Chat: create a grocery list app (`create_app` + `build_app`).
#   2. Iterate once ("add quantities") and build again.
#   3. The Apps tab runner shows it; adding an item in the UI lands in the
#      instance database.
#   4. The agent `app_sql`s that row; then `app_action` / `app_sql` adds
#      another, which appears in the still-open runner (`db_changed`).
#   5. `POST /apps/{id}/revert` to the first build's commit; a revert
#      commit is current and the app rebuilds.
#
# Assertions are on tool transcripts, the apps API, the instance RPC, and
# the runner DOM — never on the model's prose alone. Each model turn is
# retried at most once, and only when the required tool call didn't happen.
#
# Everything (user, personal space, app, instance, bundles, git repo,
# threads) is deleted on exit. Never completes bootstrap. Never recreates
# model-runner or postgres.
#
# Needs the live stack with the real model: NOT runnable offline.
# Takes `/tmp/homeai-stack.lock` itself if the caller hasn't.
#
# Usage: scripts/e2e/g13_grocery_smoke.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"

G13_STATE="$(mktemp -t g13-state.XXXXXX)"
G13_OUT="${G13_OUT:-$SCRIPT_DIR}"
mkdir -p "$G13_OUT"
export G13_STATE G13_OUT
APP_ID=""

log() { echo "[g13] $(date '+%H:%M:%S') $*"; }

cleanup() {
  local id
  if [ -f "$G13_STATE" ]; then
    id="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("app_id") or "")' "$G13_STATE" 2>/dev/null || true)"
    APP_ID="${APP_ID:-$id}"
  fi
  if [[ "$APP_ID" =~ ^[0-9a-f-]{36}$ ]]; then
    _e2e_compose exec -T platform \
      rm -rf "/data/platform/app-bundles/$APP_ID" "/data/platform/app-git/$APP_ID.git" \
      </dev/null || true
  fi
  rm -f "$G13_STATE"
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user g13
G13_USER="$E2E_NEW_USER"
G13_PASSWORD="$E2E_NEW_PASSWORD"
E2E_AUTH_COOKIE="$(e2e_auth_login "$G13_USER" "$G13_PASSWORD")"
export G13_USER G13_PASSWORD E2E_AUTH_COOKIE E2E_BASE
log "user ${G13_USER}; HITL off"
"$SCRIPT_DIR/ensure_hitl.sh" false >/dev/null

cd "$SCRIPT_DIR"
if [ ! -d node_modules/playwright ]; then
  log "Installing Playwright (npm install)..."
  npm install
fi
log "Ensuring the Chromium browser binary is installed..."
npx playwright install chromium >/dev/null
cd "$REPO_ROOT"

log "Running G13 against ${E2E_BASE:-http://localhost}..."
uvx --quiet --from websockets python3 "$SCRIPT_DIR/g13_grocery_smoke.py"
echo "G13 GROCERY SMOKE: PASS"
