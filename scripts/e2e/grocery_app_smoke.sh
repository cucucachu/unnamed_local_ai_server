#!/usr/bin/env bash
# M12-07: the reference Grocery list app (examples/apps/grocery-list) in the
# real web app, through Caddy, in headless Chromium.
#
# Three throwaway users from the recovery CLI: an owner, an editor and a
# viewer of a throwaway shared space `e2e-grocery-*`. The owner uploads the
# app to their Personal space's Apps folder and to the shared space's,
# registers, installs and builds both with the real builder (zero
# diagnostics); then, in the browser (see grocery_app_smoke.mjs):
#   1. Personal space: open it from the Apps tab; add items (the addItem
#      action; adding an existing name doesn't duplicate it), check and
#      uncheck, open an item's detail screen and save a quantity, clear the
#      checked items (the clearChecked action); the list survives a reload;
#   2. shared space: the owner adds an item and the editor, with the app
#      open in their own browser, sees it without reloading (db_changed),
#      and the other way round;
#   3. the viewer sees the list with no edit controls, and a write through
#      the platform RPC as the viewer is refused with the database unchanged.
# Everything it created (users, personal spaces, the shared space, both
# apps, instances and bundles) is deleted on exit, even if node is killed.
#
# Prerequisites: as app_runner_browser_smoke.sh (stack up with caddy and
# platform from this tree, the builder image, Node, docker).
#
# Usage: scripts/e2e/grocery_app_smoke.sh
#        GROCERY_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/grocery_app_smoke.sh

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

GROCERY_SMOKE_SPACE_SLUG="e2e-grocery-$(openssl rand -hex 3)"
GROCERY_SMOKE_STATE="$(mktemp)"
export GROCERY_SMOKE_SPACE_SLUG GROCERY_SMOKE_STATE
cleanup() {
  local id ids
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/$id" || true
  done <"$GROCERY_SMOKE_STATE"
  rm -f "$GROCERY_SMOKE_STATE"
  # Apps and instances cascade from the spaces; e2e_auth_end removes the
  # personal ones.
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$GROCERY_SMOKE_SPACE_SLUG'
    AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user grocery
export GROCERY_SMOKE_USER="$E2E_NEW_USER" GROCERY_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user grocery-editor
export GROCERY_SMOKE_EDITOR="$E2E_NEW_USER" GROCERY_SMOKE_EDITOR_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user grocery-viewer
export GROCERY_SMOKE_VIEWER="$E2E_NEW_USER" GROCERY_SMOKE_VIEWER_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"
if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

echo "==> Running against ${GROCERY_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/grocery_app_smoke.mjs"
