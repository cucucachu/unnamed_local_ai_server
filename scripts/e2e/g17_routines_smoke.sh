#!/usr/bin/env bash
# M17-08 (GATE G17) scenario: routines run headlessly on the live stack.
#
# As a throwaway `e2e-g17-*` user (`lib/auth.sh`), makes three routines due
# at the same minute, one to two minutes out (`g17_routines_smoke.py`):
# a read-only one-shot that must run with nobody connected and show up
# under the routine and in the chats list; one disabled at once, which must
# never run; one whose grant is revoked from Settings -> Sessions, which
# must not run and ends up disabled. One short GPU turn (the model replies
# with a marker). Takes about four minutes.
#
# Needs the scheduler on in agent-server (the default). Never completes
# bootstrap. Never recreates model-runner or postgres. Deletes its
# routines, their run threads and the user on exit. Takes
# `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/g17_routines_smoke.sh

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

e2e_auth_begin g17
export E2E_AUTH_COOKIE E2E_BASE
echo "[g17] user $E2E_AUTH_USER"

python3 "$SCRIPT_DIR/g17_routines_smoke.py"
