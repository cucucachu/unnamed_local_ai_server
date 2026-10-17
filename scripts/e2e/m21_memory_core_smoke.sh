#!/usr/bin/env bash
# M21-06: the memory core on the live stack (`m21_memory_core_smoke.py`).
#
# As a throwaway `e2e-m21m-*` user (`lib/auth.sh`): writes
# /personal/Memory/memory.md, then in a new chat asks "what do you know about
# me?"; the agent must answer from the note without looking it up (it's in
# the prompt). Then "remember X" must not write a file and must say it's
# added overnight. Two GPU turns; takes about a minute.
#
# Never completes bootstrap. Never recreates model-runner or postgres.
# Deletes the user (and with it the chat and the note) on exit. Takes
# `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/m21_memory_core_smoke.sh

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

e2e_auth_begin m21m
export E2E_AUTH_COOKIE E2E_BASE
echo "[m21-core] user $E2E_AUTH_USER"

uvx --quiet --from websockets python3 "$SCRIPT_DIR/m21_memory_core_smoke.py"
