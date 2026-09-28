#!/usr/bin/env bash
# M10-06 web auth smoke: setup screen renders (never submitted), CLI user
# logs in -> Home, logout -> login, invite accept — see
# `auth_browser_smoke.mjs` for the steps.
#
# Prerequisites (not managed by this script):
#   - The stack is up with a `caddy` built from this tree (it bakes in the
#     web bundle and routes `/api/auth/*` to `platform`):
#       docker compose up -d --build --no-deps caddy
#   - Node.js/npm on PATH; `docker compose exec` works (recovery CLI + psql
#     cleanup of the throwaway `e2e-*` accounts).
#
# Usage:
#   scripts/e2e/auth_browser_smoke.sh
#   AUTH_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/auth_browser_smoke.sh

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"

if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi

echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

echo "==> Running the auth smoke against ${AUTH_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/auth_browser_smoke.mjs"
