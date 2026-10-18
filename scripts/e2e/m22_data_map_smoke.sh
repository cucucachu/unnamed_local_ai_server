#!/usr/bin/env bash
# M22-06: the data map on the live stack (`m22_data_map_smoke.py`).
#
# As a throwaway `e2e-m22d-*` user (`lib/auth.sh`): installs and builds the
# tracker and list templates (as Habits and Chores) and a small Budget app
# whose schema.sql describes its tables, adds three expenses, and checks
# `GET /api/platform/data-map` (row counts, descriptions). Then one chat turn
# on the real model asks "which of my apps track money?": it must name
# Budget, and not call list_apps (or any app tool) before answering. One
# retry if the model reaches for a tool anyway. Takes a minute or two.
#
# Never completes bootstrap. Never recreates model-runner or postgres.
# Deletes the user (their personal space, the apps' bundles and git repos,
# the thread) on exit. Takes `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/m22_data_map_smoke.sh

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

E2E_DATA_MAP_STATE="$(mktemp)"
export E2E_DATA_MAP_STATE
cleanup() {
  local id
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf \
      "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$E2E_DATA_MAP_STATE"
  rm -f "$E2E_DATA_MAP_STATE"
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_begin m22d
export E2E_AUTH_COOKIE E2E_BASE
echo "[m22-data-map] user $E2E_AUTH_USER"

uvx --quiet --from websockets python3 "$SCRIPT_DIR/m22_data_map_smoke.py"
