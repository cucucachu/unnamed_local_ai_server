#!/usr/bin/env bash
# M13-03: real-model app authoring eval (docs/PLATFORM.md §11).
#
# ≥10 prompts against the live stack + local GPU model. Pass for a case =
# the app builds (compile + smoke render) and a scripted check of its data
# model succeeds. Hits the GPU: this script takes /tmp/homeai-stack.lock
# itself if the caller hasn't.
#
# Prerequisites: stack up (caddy, agent-server with this branch's templates
# + authoring guide, model-runner, platform, builder image). Never
# completes bootstrap. Creates a throwaway e2e-* user and deletes it (and
# leftover app-bundles / app-git) on exit.
#
# Usage:
#   scripts/eval/app_authoring/run.sh
#   EVAL_ONLY=grocery-quantities scripts/eval/app_authoring/run.sh
#   EVAL_LIMIT=2 scripts/eval/app_authoring/run.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=scripts/e2e/lib/auth.sh
source "$REPO_ROOT/scripts/e2e/lib/auth.sh"

EVAL_OUT="${EVAL_OUT:-$SCRIPT_DIR}"
mkdir -p "$EVAL_OUT"
export EVAL_OUT
APP_IDS="$EVAL_OUT/app-ids.txt"
: >"$APP_IDS"

cleanup() {
  local id
  if [ -f "$APP_IDS" ]; then
    while read -r id || [ -n "$id" ]; do
      [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform \
        rm -rf "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
    done <"$APP_IDS"
  fi
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_begin authoring
export E2E_AUTH_COOKIE E2E_BASE
echo "==> user $E2E_AUTH_USER; HITL off; ${#} extra args: $*"
"$REPO_ROOT/scripts/e2e/ensure_hitl.sh" false >/dev/null

uvx --quiet --from websockets python3 "$SCRIPT_DIR/run.py" "$@"
