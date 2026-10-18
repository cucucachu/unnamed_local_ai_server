#!/usr/bin/env bash
# M21-12: the G21 memory scenario on the live stack (`g21_memory_smoke.py`).
#
# Two throwaway users (`lib/auth.sh`). The first, with file writes allowed:
# deleting the Memory routine is refused; a chat states a fact (and the
# conventions README appears in /personal/Memory/); Run now on
# Memory puts it in /personal/Memory/ without a web or integration call; a
# new chat recalls it. The second can't list the first's notes, and neither
# the search API nor its agent's `search_files` finds them. A few GPU turns
# and one Memory run; takes a few minutes.
#
# Never completes bootstrap. Never recreates model-runner or postgres.
# Deletes both users (and with them their chats, routines and notes) on
# exit. Takes `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/g21_memory_smoke.sh

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

trap e2e_auth_end EXIT

e2e_auth_create_user g21
G21_COOKIE="$(e2e_auth_login "$E2E_NEW_USER" "$E2E_NEW_PASSWORD")"
echo "[g21] user $E2E_NEW_USER"
e2e_auth_create_user g21-other
G21_OTHER_COOKIE="$(e2e_auth_login "$E2E_NEW_USER" "$E2E_NEW_PASSWORD")"
echo "[g21] other user $E2E_NEW_USER"
export E2E_BASE G21_COOKIE G21_OTHER_COOKIE

uvx --quiet --from websockets python3 "$SCRIPT_DIR/g21_memory_smoke.py"
