#!/usr/bin/env bash
# M3-05 full-stack files-browser smoke test.
#
# Opens a real headless browser against the live stack (`caddy` fronting the
# built frontend + proxying to `platform`), navigates to the Files tab,
# and drives the actual file-manager UI (no mocking — real REST
# `/api/platform/files*` calls) through the FULL flow from the ticket's
# acceptance criteria, inside the user's Personal space:
#
#   create a folder -> upload a small file into it -> rename it -> verify
#   the rename via a raw REST GET -> delete the folder -> verify it's gone
#   (again via REST).
#
# Run TWICE per the ticket: once with plain ASCII names, once with a folder/
# file name containing a space and a non-ASCII name (`тест файл.txt`) —
# proving the whole round trip (breadcrumb navigation, upload, rename,
# delete, and the URL-encoded REST verification) works for both.
#
# M11-01 adds the space-aware tree: the root lists Personal and a throwaway
# shared space, the ASCII pass moves its file into that space through the
# destination picker, and a second user who is a viewer of the space gets
# the read-only UI. See `files_browser_smoke.mjs` for the step-by-step.
#
# Everything the run creates (both users, their personal spaces, the shared
# space and its directory) is deleted on exit, even if node is killed.
#
# Prerequisites (not managed by this script — same convention as
# `chat_browser_smoke.sh`):
#   - The full docker-compose stack is up and healthy:
#       docker compose up -d
#     (rebuild `caddy` first if the frontend changed:
#       docker compose build caddy && docker compose up -d caddy)
#   - Node.js/npm available on PATH (e.g. via nvm).
#   - `docker` on PATH (the e2e users come from the platform recovery CLI,
#     see `auth_helpers.mjs`).
#
# Usage:
#   scripts/e2e/files_browser_smoke.sh
#   FILES_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/files_browser_smoke.sh
#
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

# The users and the shared space are named here so this trap can delete them
# even when node is killed before its own `finally` cleanup runs.
FILES_SMOKE_SPACE_SLUG="e2e-files-$(openssl rand -hex 3)"
export FILES_SMOKE_SPACE_SLUG
cleanup() {
  local ids id
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$FILES_SMOKE_SPACE_SLUG'
    AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user files
export FILES_SMOKE_USER="$E2E_NEW_USER" FILES_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user files-viewer
export FILES_SMOKE_VIEWER="$E2E_NEW_USER" FILES_SMOKE_VIEWER_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"

if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi

echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

echo "==> Running the smoke test against ${FILES_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/files_browser_smoke.mjs"
