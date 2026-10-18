#!/usr/bin/env bash
# M22-05: start a new app from a template in the catalog, through Caddy in
# headless Chromium.
#
# A throwaway user opens Personal → Add app → Start from a template → Habit
# Tracker, names it, creates it, and lands in the runner with the built app;
# it then adds a habit (an action) and checks the copy in Apps/ records which
# template it came from. Everything it created (the user, their personal
# space, the app, its bundles and git repo) is deleted on exit.
#
# Prerequisites: stack up with caddy, platform and the frontend from this
# tree, the builder image, Node, docker. Takes `/tmp/homeai-stack.lock` itself
# if the caller hasn't. Never completes bootstrap. Never recreates
# model-runner or postgres.
#
# Usage: scripts/e2e/app_template_smoke.sh
#        TEMPLATE_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/app_template_smoke.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

TEMPLATE_SMOKE_STATE="$(mktemp)"
export TEMPLATE_SMOKE_STATE
cleanup() {
  local id
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf \
      "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$TEMPLATE_SMOKE_STATE"
  rm -f "$TEMPLATE_SMOKE_STATE"
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user tpl
export TEMPLATE_SMOKE_USER="$E2E_NEW_USER" TEMPLATE_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"
if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

echo "==> Running against ${TEMPLATE_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/app_template_smoke.mjs"
