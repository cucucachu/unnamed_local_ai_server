#!/usr/bin/env bash
# M16-02: one long real-model agent turn (web research + file write),
# EVAL_RUNS times, as a throwaway e2e-* user with HITL off. Needs the live
# stack with real internet egress. EVAL_AGENT=candidate runs it on the
# agent-candidate service (compose profile `candidate`).
#
#   scripts/eval/long_turn/run.sh
#   EVAL_AGENT=candidate EVAL_RUNS=3 scripts/eval/long_turn/run.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=scripts/e2e/lib/auth.sh
source "$REPO_ROOT/scripts/e2e/lib/auth.sh"

trap e2e_auth_end EXIT
e2e_auth_begin longturn
export E2E_AUTH_COOKIE E2E_BASE
echo "==> user $E2E_AUTH_USER; HITL off; agent ${EVAL_AGENT:-default}"
"$REPO_ROOT/scripts/e2e/ensure_hitl.sh" false >/dev/null

uvx --quiet --from websockets python3 "$SCRIPT_DIR/run.py"
