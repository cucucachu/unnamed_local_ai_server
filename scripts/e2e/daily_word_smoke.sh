#!/usr/bin/env bash
# #480: the Daily Word template (examples/apps/daily-word) in the real web app,
# through Caddy, in headless Chromium (`daily_word_smoke.mjs`).
#
# Three throwaway users from the recovery CLI: the owner, an editor and a
# viewer of a throwaway shared space `e2e-word-*`. The owner installs Daily
# Word from the catalog (built by the real builder); the smoke works out
# today's word from the template's list. Then in three browsers:
#   - the owner's non-word and short guesses are refused; a guess with a
#     repeated letter, typed on a hardware keyboard, is saved and marked
#     (one copy present, the other absent); reopening resumes the game; the
#     word wins in 2;
#   - the editor sees "Solved in 2" but not the owner's grid, then loses in
#     six, and the owner's Today tab shows it without reloading; finished,
#     the editor sees the owner's grid;
#   - one game row each, stamped with the player; the owner's stats and the
#     editor's line in Everyone;
#   - the viewer has no keyboard, sees both grids, and a write is refused.
# Everything it created (users, personal spaces, the shared space, the app,
# instance and bundle) is deleted on exit.
#
# Prerequisites: as grocery_app_smoke.sh (stack up with caddy, platform and
# frontend from this tree, the builder image, Node, docker).
#
# Usage: scripts/e2e/daily_word_smoke.sh
#        WORD_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/daily_word_smoke.sh
set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

WORD_SMOKE_SPACE_SLUG="e2e-word-$(openssl rand -hex 3)"
WORD_SMOKE_STATE="$(mktemp)"
export WORD_SMOKE_SPACE_SLUG WORD_SMOKE_STATE
cleanup() {
  local id ids
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$WORD_SMOKE_STATE"
  rm -f "$WORD_SMOKE_STATE"
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$WORD_SMOKE_SPACE_SLUG'
    AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user word
export WORD_SMOKE_USER="$E2E_NEW_USER" WORD_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user word-editor
export WORD_SMOKE_EDITOR="$E2E_NEW_USER" WORD_SMOKE_EDITOR_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user word-viewer
export WORD_SMOKE_VIEWER="$E2E_NEW_USER" WORD_SMOKE_VIEWER_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"
if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
npx playwright install chromium >/dev/null

echo "==> Running against ${WORD_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/daily_word_smoke.mjs"
