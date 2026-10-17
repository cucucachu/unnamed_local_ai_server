#!/usr/bin/env bash
# M21-04: file search on the live stack (`m21_search_smoke.py`).
#
# As a throwaway `e2e-m21s-*` user (`lib/auth.sh`): writes three notes
# through the files API and waits until a paraphrase finds the right one
# first by meaning (the platform's index and the `embeddings` service), and
# an exact name finds it too; then one chat turn on the real model asks a
# paraphrased question, which must use `search_files` and answer from the
# note. Takes about a minute.
#
# Never completes bootstrap. Never recreates model-runner or postgres.
# Deletes the user (and with it the notes and the thread) on exit. Takes
# `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/m21_search_smoke.sh

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

e2e_auth_begin m21s
export E2E_AUTH_COOKIE E2E_BASE
echo "[m21-search] user $E2E_AUTH_USER"

uvx --quiet --from websockets python3 "$SCRIPT_DIR/m21_search_smoke.py"
