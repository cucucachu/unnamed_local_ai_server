#!/usr/bin/env bash
# M14-01: publish → family member installs → author updates → member approves,
# through Caddy in headless Chromium.
#
# Two throwaway users: an author and an editor of a throwaway shared space
# `e2e-pub-*`. The author uploads the fixture hello app to Personal, registers,
# installs and builds it; then in the browser:
#   1. App info → Publish into the family catalog
#   2. The editor opens Catalog, confirms permissions, installs
#   3. The author bumps the version, rebuilds, publishes again
#   4. The editor's Home launcher shows an update badge; they approve it
# Everything it created (users, personal spaces, the shared space, apps,
# instances, bundles, git repos, app-releases) is deleted on exit.
#
# Prerequisites: stack up with caddy and platform from this tree, the builder
# image, Node, docker. Takes `/tmp/homeai-stack.lock` itself if the caller
# hasn't. Never completes bootstrap. Never recreates model-runner or postgres.
#
# Usage: scripts/e2e/app_publish_smoke.sh
#        PUBLISH_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/app_publish_smoke.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

PUBLISH_SMOKE_SPACE_SLUG="e2e-pub-$(openssl rand -hex 3)"
PUBLISH_SMOKE_STATE="$(mktemp)"
export PUBLISH_SMOKE_SPACE_SLUG PUBLISH_SMOKE_STATE
cleanup() {
  local id ids
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf \
      "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" \
      "/data/platform/app-releases/$id" </dev/null || true
  done <"$PUBLISH_SMOKE_STATE"
  rm -f "$PUBLISH_SMOKE_STATE"
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$PUBLISH_SMOKE_SPACE_SLUG'
    AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user pub
export PUBLISH_SMOKE_USER="$E2E_NEW_USER" PUBLISH_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user pub-editor
export PUBLISH_SMOKE_EDITOR="$E2E_NEW_USER" PUBLISH_SMOKE_EDITOR_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"
if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

echo "==> Running against ${PUBLISH_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/app_publish_smoke.mjs"
