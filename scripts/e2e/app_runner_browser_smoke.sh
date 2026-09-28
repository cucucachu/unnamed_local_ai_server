#!/usr/bin/env bash
# M12-06: the app host (Apps tab + runner) in the real web app, through
# Caddy, in headless Chromium.
#
# Two throwaway users from the recovery CLI: an owner and a viewer of a
# throwaway shared space `e2e-runner-*`. The owner uploads the SDK's fixture
# app (packages/homeai-sdk/tests/fixtures/runtime-check) to the space's
# Apps folder, registers, installs and builds it with the real builder;
# then, in the browser (see app_runner_browser_smoke.mjs):
#   1. the Apps tab lists it under the space; opening it runs it in a
#      `sandbox="allow-scripts"` iframe (no allow-same-origin);
#   2. a row written in the app is in the instance database, and still
#      shown after a page reload;
#   2b. Ask the agent opens a panel seeded with app.json, AGENT.md, schema.sql,
#      instance id and space; a write to the instance (as if the agent ran
#      app_sql) shows up live via db_changed while the panel is open;
#   3. a rebuild hot-reloads the running app (same frame, same route) to v2;
#   4. a runtime error in the app (v2's Crash button) raises the host's
#      error overlay, whose Reload brings the app back;
#   5. the viewer sees the app as "View only", sees the row, and a write is
#      refused (`read_only`) with the database unchanged;
#   and no request from the sandbox frame reaches anything.
# Everything it created (users, personal spaces, the shared space, its app,
# instance and bundles) is deleted on exit, even if node is killed.
#
# Prerequisites: the stack is up with caddy and platform built from this
# tree (caddy bakes in the web app and the runtime), the builder image
# (services/app-builder/build-builder-image.sh), Node, docker.
#
# Usage: scripts/e2e/app_runner_browser_smoke.sh
#        RUNNER_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/app_runner_browser_smoke.sh

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

RUNNER_SMOKE_SPACE_SLUG="e2e-runner-$(openssl rand -hex 3)"
RUNNER_SMOKE_STATE="$(mktemp)"
export RUNNER_SMOKE_SPACE_SLUG RUNNER_SMOKE_STATE
cleanup() {
  local id ids
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$RUNNER_SMOKE_STATE"
  rm -f "$RUNNER_SMOKE_STATE"
  # Apps and instances cascade from the space.
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$RUNNER_SMOKE_SPACE_SLUG'
    AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user runner
export RUNNER_SMOKE_USER="$E2E_NEW_USER" RUNNER_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user runner-viewer
export RUNNER_SMOKE_VIEWER="$E2E_NEW_USER" RUNNER_SMOKE_VIEWER_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"
if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

echo "==> Running against ${RUNNER_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/app_runner_browser_smoke.mjs"
