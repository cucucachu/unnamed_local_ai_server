#!/usr/bin/env bash
# M22-08: CSV export and import on the live stack (`m22_csv_smoke.py`).
#
# As a throwaway `e2e-m22c-*` user (`lib/auth.sh`): installs the notes
# template, adds two notes and exports them (notes.csv + schema.sql in a new
# Exports/ folder; a second export into that folder is 409). Imports the
# export back (dry run, then the real import), maps a semicolon CSV with
# other headers and a BOM, and checks that a file with a bad row on line 3
# imports nothing (422, line 3). Then one chat turn on the real model, with
# app_import allowed, must import a CSV into the notes table with app_import.
# One retry if it didn't. Takes a minute or two.
#
# Never completes bootstrap. Never recreates model-runner or postgres.
# Deletes the user (their personal space, the app's bundle and git repo,
# the thread) on exit. Takes `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/m22_csv_smoke.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

export COMPOSE_PROJECT_NAME=homeai

# Worktrees don't get the gitignored .env; compose still needs it. Never cat it.
if [ ! -f .env ] && [ -f /home/cody/code/unnamed_local_ai_server/.env ]; then
  ln -s /home/cody/code/unnamed_local_ai_server/.env .env
fi

# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"

E2E_CSV_STATE="$(mktemp)"
export E2E_CSV_STATE
cleanup() {
  local id
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf \
      "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$E2E_CSV_STATE"
  rm -f "$E2E_CSV_STATE"
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_begin m22c
export E2E_AUTH_COOKIE E2E_BASE
echo "[m22-csv] user $E2E_AUTH_USER"

uvx --quiet --from websockets python3 "$SCRIPT_DIR/m22_csv_smoke.py"
