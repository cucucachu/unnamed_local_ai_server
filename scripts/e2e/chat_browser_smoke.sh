#!/usr/bin/env bash
# M2-06/M3-04 full-stack chat smoke test.
#
# Opens a real headless browser against the live stack (`caddy` fronting the
# built frontend + proxying to `agent-server`/`model-runner`), navigates to
# the Chat tab, creates a new thread from the UI, sends a message, goes back
# to the thread list (asserting the title reflects the message, per M3-02's
# title-derivation), reopens the thread (asserting history hydration
# actually rendered the prior messages, per M3-04), and sends a follow-up
# message — see `chat_browser_smoke.mjs` for the full step-by-step.
#
# Prerequisites (not managed by this script):
#   - The full docker-compose stack is up and healthy:
#       docker compose up -d
#     (rebuild `caddy` first if the frontend changed:
#       docker compose build caddy && docker compose up -d caddy)
#   - Node.js/npm available on PATH (e.g. via nvm).
#
# Usage:
#   scripts/e2e/chat_browser_smoke.sh
#   CHAT_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/chat_browser_smoke.sh
#
# Alternative (not used here, noted per the ticket): this could instead run
# in a container via
#   docker run --rm --network host mcr.microsoft.com/playwright:v1.<ver> \
#     node /work/chat_browser_smoke.mjs
# — avoided in favor of `npx playwright` directly, since Node/npx are already
# set up on this host and it sidesteps Docker network/volume-mount plumbing
# for this one script.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$script_dir"

if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi

echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

echo "==> Running the smoke test against ${CHAT_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/chat_browser_smoke.mjs"
