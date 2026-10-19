#!/usr/bin/env bash
# M22-15: the Memory reviews eval on the real model (README.md here).
#
# Two throwaway e2e-* users per repetition (EVAL_RUNS, default 3): one with
# a month of seeded notes that gets the weekly, monthly and yearly reviews,
# and one whose reviewed week is empty. run.py scores the cases. Hits the
# GPU for a long time: takes /tmp/homeai-stack.lock itself if the caller
# hasn't.
#
# Never completes bootstrap. Deletes its users on exit.
#
# Usage:
#   scripts/eval/memory_reviews/run.sh
#   EVAL_RUNS=1 scripts/eval/memory_reviews/run.sh

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

cleanup() {
  rm -f "$USERS_FILE"
  e2e_auth_end
}
trap cleanup EXIT

for i in $(seq 1 "$RUNS"); do
  e2e_auth_create_user reveval
  cookie="$(e2e_auth_login "$E2E_NEW_USER" "$E2E_NEW_PASSWORD")"
  user="$E2E_NEW_USER"
  e2e_auth_create_user revevalq
  quiet_cookie="$(e2e_auth_login "$E2E_NEW_USER" "$E2E_NEW_PASSWORD")"
  printf '%s\t%s\t%s\t%s\n' "$user" "$cookie" "$E2E_NEW_USER" "$quiet_cookie" >>"$USERS_FILE"
  echo "==> run $i: user $user, quiet user $E2E_NEW_USER"
done

export E2E_BASE EVAL_USERS_FILE="$USERS_FILE"
python3 "$SCRIPT_DIR/run.py" "$@"
