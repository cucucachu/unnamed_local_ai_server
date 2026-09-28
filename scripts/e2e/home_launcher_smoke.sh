#!/usr/bin/env bash
# M14-02: Home launcher navigation smoke through Caddy in headless Chromium.
#
# A throwaway `e2e-home-*` member signs in: `/` is Home (system tiles, catalog,
# space switcher); Chat / Files / Settings tabs work; a throwaway shared space
# appears in the switcher. Deletes the user, personal space, and shared space
# on exit.
#
# Prerequisites: stack up with a `caddy` built from this tree. Node, docker.
# Takes `/tmp/homeai-stack.lock` itself if the caller hasn't. Never completes
# bootstrap. Never recreates model-runner or postgres.
#
# Usage: scripts/e2e/home_launcher_smoke.sh
#        HOME_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/home_launcher_smoke.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"

if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

echo "==> Running against ${HOME_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/home_launcher_smoke.mjs"
