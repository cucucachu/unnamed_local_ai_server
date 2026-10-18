#!/usr/bin/env bash
# M22-07: app rows in search on the live stack (`m22_app_search_smoke.py`).
#
# As a throwaway `e2e-m22s-*` user (`lib/auth.sh`): builds a Recipes app
# whose app.json declares `homeai.index` on its recipes table, adds three
# recipes, and waits for a paraphrase with no word in common ("spiced legume
# stew from south asia") to find the dal row by meaning in `/files/search`,
# with its app, table and row id. An exact ingredient finds its row too.
# The app's own `search` RPC op (the SDK's `search()`) finds the dal first.
# Then one chat turn on the real model must use search_files and name it.
# One retry if the model answers without it. Takes a minute or two.
#
# Never completes bootstrap. Never recreates model-runner or postgres.
# Deletes the user (their personal space, the apps' bundles and git repos,
# the thread) on exit. Takes `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/m22_app_search_smoke.sh

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

E2E_APP_SEARCH_STATE="$(mktemp)"
export E2E_APP_SEARCH_STATE
cleanup() {
  local id
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf \
      "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$E2E_APP_SEARCH_STATE"
  rm -f "$E2E_APP_SEARCH_STATE"
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_begin m22s
export E2E_AUTH_COOKIE E2E_BASE
echo "[m22-app-search] user $E2E_AUTH_USER"

uvx --quiet --from websockets python3 "$SCRIPT_DIR/m22_app_search_smoke.py"
