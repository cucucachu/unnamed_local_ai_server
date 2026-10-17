#!/usr/bin/env bash
# M21-10: the Memory routine on the live stack (`m21_memory_routine_smoke.py`).
#
# As a throwaway `e2e-m21r-*` user (`lib/auth.sh`) who has allowed file
# writes: sets a timezone, checks the Memory routine was delivered, seeds two
# chats (a partner, a hometown), presses Run now and waits for the run. Then
# /personal/Memory/memory.md and at least one page must exist with both
# facts, a fact must carry its month, and the run's reply must link what
# changed. Several GPU turns; takes a few minutes.
#
# Never completes bootstrap. Never recreates model-runner or postgres.
# Deletes the user (and with it the chats, routine and notes) on exit. Takes
# `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/m21_memory_routine_smoke.sh

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

e2e_auth_begin m21r
export E2E_AUTH_COOKIE E2E_BASE
echo "[m21-routine] user $E2E_AUTH_USER"

uvx --quiet --from websockets python3 "$SCRIPT_DIR/m21_memory_routine_smoke.py"
