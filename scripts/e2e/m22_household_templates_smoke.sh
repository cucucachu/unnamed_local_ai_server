#!/usr/bin/env bash
# M22-16: the household templates (examples/apps/chores, pantry, recipes,
# contacts) in the real web app, through Caddy, in headless Chromium
# (`m22_household_templates_smoke.mjs`).
#
# Two throwaway users from the recovery CLI: an owner and an editor of a
# throwaway shared space `e2e-household-*`. The owner installs the four
# templates and the grocery list into it through the catalog API (each
# built by the real builder), then in the browser:
#   - Chores: add one with both members in the rota, mark it done (the turn
#     passes to the editor, who sees "Your turn" live and marks it done),
#     the history tab, edit how often it repeats;
#   - Pantry: add items (adding the same thing again tops it up), "Use
#     soon", filter by place, use one up, edit a quantity, search;
#   - Recipes: add one, edit it, plan it and replace the meal with a note,
#     and "Add ingredients to the grocery list" (an export action into the
#     Grocery list app, which then shows them; a second press doesn't
#     duplicate them);
#   - Contacts: add people from the one New form (several phones and
#     emails, a structured address, nickname, company, birthday), check the
#     formatted address, edit (drop a phone, change the city, add a
#     website), merge a duplicate, "Coming up", search by notes, company
#     and nickname.
# Everything it created (users, personal spaces, the shared space, the apps,
# instances and bundles) is deleted on exit.
#
# Prerequisites: as grocery_app_smoke.sh (stack up with caddy, platform and
# frontend from this tree, the builder image, Node, docker). Search needs
# the embedding model. Takes a few minutes.
#
# Usage: scripts/e2e/m22_household_templates_smoke.sh
#        HOUSEHOLD_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/m22_household_templates_smoke.sh
set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

HOUSEHOLD_SMOKE_SPACE_SLUG="e2e-household-$(openssl rand -hex 3)"
HOUSEHOLD_SMOKE_STATE="$(mktemp)"
export HOUSEHOLD_SMOKE_SPACE_SLUG HOUSEHOLD_SMOKE_STATE
cleanup() {
  local id ids
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$HOUSEHOLD_SMOKE_STATE"
  rm -f "$HOUSEHOLD_SMOKE_STATE"
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$HOUSEHOLD_SMOKE_SPACE_SLUG'
    AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user household
export HOUSEHOLD_SMOKE_USER="$E2E_NEW_USER" HOUSEHOLD_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user household-editor
export HOUSEHOLD_SMOKE_EDITOR="$E2E_NEW_USER" HOUSEHOLD_SMOKE_EDITOR_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"
if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
npx playwright install chromium >/dev/null

echo "==> Running against ${HOUSEHOLD_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/m22_household_templates_smoke.mjs"
