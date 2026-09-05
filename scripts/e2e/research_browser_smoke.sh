#!/usr/bin/env bash
# M7-07: thin Playwright-setup wrapper around `research_browser_smoke.mjs`,
# same pattern as `chat_browser_smoke.sh` (install node_modules/Chromium
# once, then hand off to node) — see that script's own header comment for
# why `npx playwright` directly (not a container) is used on this host.
#
# `research_browser_smoke.mjs` takes exactly one positional arg,
# "positive" or "negative", so `gate_m7.sh` can invoke each scenario
# separately (it needs to capture an egress-proxy log baseline
# immediately before the negative scenario only — see gate_m7.sh's own
# comments).
#
# Usage:
#   scripts/e2e/research_browser_smoke.sh positive
#   scripts/e2e/research_browser_smoke.sh negative
#
# Requires (same as chat_browser_smoke.sh):
#   - The full docker-compose stack up and healthy.
#   - Node.js/npm on PATH.
#   - WORKSPACE_DIR exported in the environment (gate_m7.sh does this,
#     read from .env, same convention as every other scripts/e2e/*.sh script).

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$script_dir"

if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi

echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

echo "==> Running research_browser_smoke.mjs $* against ${RESEARCH_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/research_browser_smoke.mjs" "$@"
