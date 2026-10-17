#!/usr/bin/env bash
# M21-05: reading past chats on the live stack (`m21_chats_smoke.py`).
#
# As a throwaway `e2e-m21c-*` user (`lib/auth.sh`): tells the agent a fact in
# one chat, then in a new chat asks it to look through earlier chats; it
# must use `read_chat`, see the earlier user message (never tool output),
# and answer from it. Two GPU turns; takes about a minute.
#
# Never completes bootstrap. Never recreates model-runner or postgres.
# Deletes the user (and with it both chats) on exit. Takes
# `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/m21_chats_smoke.sh

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

e2e_auth_begin m21c
export E2E_AUTH_COOKIE E2E_BASE
echo "[m21-chats] user $E2E_AUTH_USER"

uvx --quiet --from websockets python3 "$SCRIPT_DIR/m21_chats_smoke.py"
