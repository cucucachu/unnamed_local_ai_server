#!/usr/bin/env bash
# M10-07 Settings/admin smoke: admin steps up and creates an invite -> a
# second browser accepts it -> admin creates a shared space and adds the new
# member -> the member sees it; then TOTP enroll -> sign out -> sign-in
# requires the code. See `admin_browser_smoke.mjs` for the steps.
#
# Prerequisites (not managed by this script):
#   - The stack is up with a `caddy` built from this tree (it bakes in the
#     web bundle and routes `/api/auth/*` and `/api/platform/*`):
#       docker compose up -d --build --no-deps caddy
#   - Node.js/npm on PATH; `docker compose exec` works (recovery CLI + psql
#     cleanup of the throwaway `e2e-*` accounts, space, and invite).
#
# Usage:
#   scripts/e2e/admin_browser_smoke.sh
#   ADMIN_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/admin_browser_smoke.sh

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"

if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi

echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

echo "==> Running the admin smoke against ${ADMIN_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/admin_browser_smoke.mjs"
