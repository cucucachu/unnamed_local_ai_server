#!/usr/bin/env bash
# M12-05: the app runtime page against the live stack, in headless Chromium.
#
# A throwaway `e2e-rt-*` user uploads the SDK's fixture app
# (packages/homeai-sdk/tests/fixtures/runtime-check) to /personal/Apps,
# registers, installs and builds it with the real builder, and a static test
# host page served at Caddy's origin opens the instance with the SDK's host
# library, as a web host would: bundle from `GET …/instances/{id}/bundle`,
# runtime from `/app-runtime/1/runtime.js`, RPC through
# `POST …/instances/{id}/rpc` and events from `/ws/platform/events`, all
# with the page's own session cookie. The checks (harness.mjs): the CSP'd
# `sandbox="allow-scripts"` frame renders, routes navigate, getAllAsync /
# getFirstAsync / runAsync / runAction round-trip to the instance database,
# useQuery re-runs on db_changed from a write made outside the sandbox, the
# host refuses methods outside the allowlist and ignores instance ids in
# params, no escape probe reaches the network or the parent, a rebuild
# hot-reloads the app on its route, a self-navigating frame is removed, and a
# read-only host refuses writes. Everything it created (user, personal
# space, apps, instances, bundles) is deleted on exit.
#
# Prerequisites: the stack is up with caddy and platform built from this
# tree (caddy serves the runtime; platform serves the bundle), the builder
# image (services/app-builder/build-builder-image.sh), Node, docker.
#
# Usage: scripts/e2e/app_runtime_smoke.sh
#        RUNTIME_SMOKE_BASE_URL=http://homeai.local scripts/e2e/app_runtime_smoke.sh

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

RUNTIME_SMOKE_STATE="$(mktemp)"
export RUNTIME_SMOKE_STATE
cleanup() {
  local id
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/$id" || true
  done <"$RUNTIME_SMOKE_STATE"
  rm -f "$RUNTIME_SMOKE_STATE"
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user rt
export RUNTIME_SMOKE_USER="$E2E_NEW_USER" RUNTIME_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"

echo "==> Installing the SDK's and the smoke's node modules..."
(cd "$repo_root/packages/homeai-sdk" && npm ci --ignore-scripts --no-audit --no-fund >/dev/null)
(cd "$script_dir" && { [ -d node_modules/playwright ] || npm install >/dev/null; } && npx playwright install chromium >/dev/null)

echo "==> Running against ${RUNTIME_SMOKE_BASE_URL:-http://localhost}..."
node "$script_dir/app_runtime_smoke.mjs"
