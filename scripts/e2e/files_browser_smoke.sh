#!/usr/bin/env bash
# M3-05 full-stack files-browser smoke test.
#
# Opens a real headless browser against the live stack (`caddy` fronting the
# built frontend + proxying to `agent-server`), navigates to the Files tab,
# and drives the actual file-manager UI (no mocking — real REST `/api/files*`
# calls) through the FULL flow from the ticket's acceptance criteria:
#
#   create a folder -> upload a small file into it -> rename it -> verify
#   the rename via a raw `GET /api/files` (Python `urllib.request` — see
#   `files_rest_smoke.sh` for the house pattern; `curl` is not installed on
#   this host) -> delete the folder -> verify it's gone (again via REST).
#
# Run TWICE per the ticket: once with plain ASCII names, once with a folder/
# file name containing a space and a non-ASCII name (`тест файл.txt`) —
# proving the whole round trip (breadcrumb navigation, upload, rename,
# delete, and the URL-encoded REST verification) works for both. See
# `files_browser_smoke.mjs` for the step-by-step.
#
# Prerequisites (not managed by this script — same convention as
# `chat_browser_smoke.sh`):
#   - The full docker-compose stack is up and healthy:
#       docker compose up -d
#     (rebuild `caddy` first if the frontend changed:
#       docker compose build caddy && docker compose up -d caddy)
#   - Node.js/npm available on PATH (e.g. via nvm).
#   - `python3` available on PATH (used for the REST verification steps,
#     same as every other `scripts/e2e/*_rest_smoke.sh` script).
#
# Usage:
#   scripts/e2e/files_browser_smoke.sh
#   FILES_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/files_browser_smoke.sh
#
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$script_dir"

if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi

echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

echo "==> Running the smoke test against ${FILES_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/files_browser_smoke.mjs"
