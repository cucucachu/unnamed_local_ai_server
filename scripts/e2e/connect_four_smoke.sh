#!/usr/bin/env bash
# #481: the Connect Four template (examples/apps/connect-four) in the real web
# app, through Caddy, in headless Chromium (`connect_four_smoke.mjs`).
#
# Three throwaway users from the recovery CLI: the owner, an editor and a
# viewer of a throwaway shared space `e2e-c4-*`. The owner installs Connect
# Four from the catalog (built by the real builder); then in three browsers:
#   - the owner starts a game against the editor, going first; the editor
#     waits, and tapping the board out of turn does nothing;
#   - they play seven moves, each appearing on the other's board without
#     reloading (prints the times), and the owner wins; the moves are
#     stamped with the right players;
#   - Rematch swaps colors; a full column is refused;
#   - a move made around the screen (REST, out of turn) is taken once even
#     when sent twice, and the other player's screen points it out;
#   - resigning hands the win over;
#   - the viewer watches with no New game or Rematch; a move as the viewer
#     is refused.
# Everything it created (users, personal spaces, the shared space, the app,
# instance and bundle) is deleted on exit.
#
# Prerequisites: as grocery_app_smoke.sh (stack up with caddy, platform and
# frontend from this tree, the builder image, Node, docker).
#
# Usage: scripts/e2e/connect_four_smoke.sh
#        C4_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/connect_four_smoke.sh
set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

C4_SMOKE_SPACE_SLUG="e2e-c4-$(openssl rand -hex 3)"
C4_SMOKE_STATE="$(mktemp)"
export C4_SMOKE_SPACE_SLUG C4_SMOKE_STATE
cleanup() {
  local id ids
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$C4_SMOKE_STATE"
  rm -f "$C4_SMOKE_STATE"
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$C4_SMOKE_SPACE_SLUG'
    AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user c4
export C4_SMOKE_USER="$E2E_NEW_USER" C4_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user c4-editor
export C4_SMOKE_EDITOR="$E2E_NEW_USER" C4_SMOKE_EDITOR_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user c4-viewer
export C4_SMOKE_VIEWER="$E2E_NEW_USER" C4_SMOKE_VIEWER_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"
if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
npx playwright install chromium >/dev/null

echo "==> Running against ${C4_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/connect_four_smoke.mjs"
