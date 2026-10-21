#!/usr/bin/env bash
# #483: the Stack template (examples/apps/stack), the first three.js app, in
# the real web app, through Caddy, in headless Chromium (`stack_smoke.mjs`).
#
# Three throwaway users from the recovery CLI: the owner, an editor and a
# viewer of a throwaway shared space `e2e-stack-*`. The owner installs Stack
# from the catalog (built by the real builder, so three comes from
# /app-runtime/1/three.js). Then in three browsers:
#   - the owner starts a game and taps when each block is over the tower
#     (the template's own timing), stacking 3, then taps at once and misses:
#     "New best!", and one row with score 3 stamped with the owner;
#   - the editor (in dark mode) misses at once, scoring 0; the owner's
#     Scores tab shows the editor on the leaderboard without reloading, and
#     the owner's best and game count;
#   - the owner's second game (0) says "Game over" with the best still 3;
#   - the viewer sees the leaderboard and can't play; a write is refused.
# Everything it created is deleted on exit.
#
# Prerequisites: as grocery_app_smoke.sh (stack up with caddy, platform and
# frontend from this tree, the builder image, Node, docker).
#
# Usage: scripts/e2e/stack_smoke.sh
#        STACK_SMOKE_SCREENSHOTS=/tmp/shots scripts/e2e/stack_smoke.sh
set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

STACK_SMOKE_SPACE_SLUG="e2e-stack-$(openssl rand -hex 3)"
STACK_SMOKE_STATE="$(mktemp)"
export STACK_SMOKE_SPACE_SLUG STACK_SMOKE_STATE
cleanup() {
  local id ids
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$STACK_SMOKE_STATE"
  rm -f "$STACK_SMOKE_STATE"
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$STACK_SMOKE_SPACE_SLUG'
    AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user stack
export STACK_SMOKE_USER="$E2E_NEW_USER" STACK_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user stack-editor
export STACK_SMOKE_EDITOR="$E2E_NEW_USER" STACK_SMOKE_EDITOR_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user stack-viewer
export STACK_SMOKE_VIEWER="$E2E_NEW_USER" STACK_SMOKE_VIEWER_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"
if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
npx playwright install chromium >/dev/null

echo "==> Running against ${STACK_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/stack_smoke.mjs"
