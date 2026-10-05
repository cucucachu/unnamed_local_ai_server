#!/usr/bin/env bash
# M17-07: real-model routine eval. Can the local model turn natural-language
# requests into the right routine schedules?
#
# Each case is a fresh chat; the agent's create_routine approval card is
# checked (schedule, timezone) and then rejected, so nothing is saved.
# Hits the GPU: this script takes /tmp/homeai-stack.lock itself if the
# caller hasn't.
#
# Prerequisites: stack up with an agent-server that has the routine tools.
# Never completes bootstrap. Creates a throwaway e2e-* user and deletes it
# on exit.
#
# Usage:
#   scripts/eval/routines/run.sh
#   EVAL_ONLY=tomorrow scripts/eval/routines/run.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=scripts/e2e/lib/auth.sh
source "$REPO_ROOT/scripts/e2e/lib/auth.sh"

trap e2e_auth_end EXIT

e2e_auth_begin routines
export E2E_AUTH_COOKIE E2E_BASE
echo "==> user $E2E_AUTH_USER"

uvx --quiet --from websockets python3 "$SCRIPT_DIR/run.py" "$@"
