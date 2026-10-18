#!/usr/bin/env bash
# M22-10: the Files text viewer and editor through the real web UI
# (`m22_text_editor_smoke.mjs`).
#
# A throwaway `e2e-m22t-*` user opens a seeded markdown file from Files
# (rendered, relative link opens Files), starts editing, and the real agent
# edits the same file from a chat in a second tab before the save. The save
# must show the conflict prompt; "Reload theirs" then a second save keeps
# both changes. Needs the live stack with the current frontend in caddy and
# the real model. Takes a minute or two. Deletes the user on exit.
#
# Usage:
#   scripts/e2e/m22_text_editor_smoke.sh
#   TEXT_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/m22_text_editor_smoke.sh
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
npx playwright install chromium >/dev/null

node "$script_dir/m22_text_editor_smoke.mjs"
