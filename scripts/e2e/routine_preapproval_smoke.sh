#!/usr/bin/env bash
# #326: pre-approving a routine, on the live stack and the real model
# (`routine_preapproval_smoke.py`): approving a paused run with "allow
# writes from now on" finishes it and switches the routine. About two GPU
# turns.
#
# Needs the scheduler on in agent-server (the default). Never completes
# bootstrap. Never recreates model-runner or postgres. Deletes its
# routine, threads, files and the throwaway `e2e-preapproval-*` user on
# exit. Takes `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/routine_preapproval_smoke.sh

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

e2e_auth_begin preapproval
export E2E_AUTH_COOKIE E2E_BASE
echo "[preapproval] user $E2E_AUTH_USER"

uvx --quiet --from websockets python3 "$SCRIPT_DIR/routine_preapproval_smoke.py"
