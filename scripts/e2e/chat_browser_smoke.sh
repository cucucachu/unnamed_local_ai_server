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
#   CHAT_SMOKE_BASE_URL=https://homeai.local/ scripts/e2e/chat_browser_smoke.sh
#     (HTTPS: exports CHAT_SMOKE_CA from ${BACKUP_DIR}/homeai-root-ca.crt
#     if unset — run scripts/export-ca.sh first. Playwright Chromium is
#     launched with that CA in its trust store.)
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

repo_root="$(cd "$script_dir/../.." && pwd)"
if [ -f "$repo_root/.env" ]; then
  WORKSPACE_DIR="$(sed -n 's/^WORKSPACE_DIR=\(.*\)$/\1/p' "$repo_root/.env" | head -n1 | xargs)"
  export WORKSPACE_DIR
  if [ -z "${BACKUP_DIR:-}" ]; then
    BACKUP_DIR="$(sed -n 's/^BACKUP_DIR=\(.*\)$/\1/p' "$repo_root/.env" | head -n1 | xargs)"
  fi
fi
if [ -z "${WORKSPACE_DIR:-}" ]; then
  echo "ERROR: WORKSPACE_DIR is not set (needed for M8-03 hello.txt assertions)" >&2
  exit 1
fi

# HTTPS smoke (M9-05): trust Caddy's local CA. HTTP invocations are
# unchanged — CHAT_SMOKE_CA is optional unless the base URL is https://.
BACKUP_DIR="${BACKUP_DIR:-/srv/homeai/backups}"
if [ -z "${CHAT_SMOKE_CA:-}" ] && [ -f "${BACKUP_DIR}/homeai-root-ca.crt" ]; then
  CHAT_SMOKE_CA="${BACKUP_DIR}/homeai-root-ca.crt"
fi
if [ -n "${CHAT_SMOKE_CA:-}" ]; then
  export CHAT_SMOKE_CA
fi

base_url="${CHAT_SMOKE_BASE_URL:-http://localhost/}"
if [[ "${base_url}" == https://* ]]; then
  if [ -z "${CHAT_SMOKE_CA:-}" ] || [ ! -f "${CHAT_SMOKE_CA}" ]; then
    echo "ERROR: HTTPS smoke needs the exported Caddy root CA." >&2
    echo "       Run: scripts/export-ca.sh" >&2
    echo "       Then: CHAT_SMOKE_CA=${BACKUP_DIR}/homeai-root-ca.crt \\" >&2
    echo "             CHAT_SMOKE_BASE_URL=https://homeai.local/ $0" >&2
    exit 1
  fi
fi

echo "==> Running the smoke test against ${base_url}..."
echo "    WORKSPACE_DIR=${WORKSPACE_DIR}"
if [ -n "${CHAT_SMOKE_CA:-}" ]; then
  echo "    CHAT_SMOKE_CA=${CHAT_SMOKE_CA}"
fi
node "$script_dir/chat_browser_smoke.mjs"
