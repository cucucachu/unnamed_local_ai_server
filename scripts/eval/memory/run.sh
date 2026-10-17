#!/usr/bin/env bash
# M21-11: the Memory routine eval on the real model (README.md here).
#
# One throwaway e2e-* user and one shared space per repetition (EVAL_RUNS,
# default 3). Each plays three simulated days of chats with a Memory run
# after each, then a recall chat; run.py scores the cases. Hits the GPU for
# a long time: takes /tmp/homeai-stack.lock itself if the caller hasn't.
#
# Never completes bootstrap. Deletes its users and spaces on exit.
#
# Usage:
#   scripts/eval/memory/run.sh
#   EVAL_RUNS=1 scripts/eval/memory/run.sh
#   EVAL_WEB_URL=https://... scripts/eval/memory/run.sh   # adds the fetched-page case

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=scripts/e2e/lib/auth.sh
source "$REPO_ROOT/scripts/e2e/lib/auth.sh"

RUNS="${EVAL_RUNS:-3}"
USERS_FILE="$(mktemp)"
SLUGS=()

cleanup() {
  local slug ids id
  for slug in "${SLUGS[@]}"; do
    ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$slug'
      AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
    for id in $ids; do
      [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
    done
  done
  rm -f "$USERS_FILE"
  e2e_auth_end
}
trap cleanup EXIT

for i in $(seq 1 "$RUNS"); do
  e2e_auth_create_user memeval
  cookie="$(e2e_auth_login "$E2E_NEW_USER" "$E2E_NEW_PASSWORD")"
  slug="memeval-$(openssl rand -hex 4)"
  SLUGS+=("$slug")
  printf '%s\t%s\t%s\n' "$E2E_NEW_USER" "$cookie" "$slug" >>"$USERS_FILE"
  echo "==> run $i: user $E2E_NEW_USER, space $slug"
done

export E2E_BASE EVAL_USERS_FILE="$USERS_FILE"
uvx --quiet --from websockets python3 "$SCRIPT_DIR/run.py" "$@"
