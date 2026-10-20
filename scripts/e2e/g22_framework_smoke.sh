#!/usr/bin/env bash
# M22-20: the G22 app framework scenario on the live stack
# (`g22_framework_smoke.py`).
#
# Two throwaway users (`lib/auth.sh`). The first, with file writes and
# `execute_code` allowed (a review sometimes counts memory.md's characters
# with it, which by default asks for approval and parks the run), installs Books and media from the template catalog and finishes a made-up
# book in it today. The data map lists the app; the owner's search API finds
# the row by meaning and the owner's agent finds it with `search_files`; the
# second user's search and agent don't. Run now on Memory reads the row
# (`list_app_changes`) and writes today's day file about it. Then two day
# files are seeded in the last full week and Run now on the weekly review
# must write that week's file and a `## Lately` section in memory.md that
# links it. One retry for each model step. (The Files conflict prompt is
# `m22_text_editor_smoke.sh`, which the gate runs too.) A few GPU turns and
# three or four routine runs; takes 10-20 minutes.
#
# Never completes bootstrap. Never recreates model-runner or postgres.
# Deletes both users (their chats, routines, notes and personal space with
# the app) and the app's bundle and git repo on exit. Takes
# `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/g22_framework_smoke.sh

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

G22_STATE="$(mktemp)"
export G22_STATE
cleanup() {
  local id
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf \
      "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$G22_STATE"
  rm -f "$G22_STATE"
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user g22
G22_COOKIE="$(e2e_auth_login "$E2E_NEW_USER" "$E2E_NEW_PASSWORD")"
echo "[g22] user $E2E_NEW_USER"
e2e_auth_create_user g22-other
G22_OTHER_COOKIE="$(e2e_auth_login "$E2E_NEW_USER" "$E2E_NEW_PASSWORD")"
echo "[g22] other user $E2E_NEW_USER"
export E2E_BASE G22_COOKIE G22_OTHER_COOKIE

uvx --quiet --from websockets python3 "$SCRIPT_DIR/g22_framework_smoke.py"
