#!/usr/bin/env bash
# M19-06 GATE G19: the redesigned app shell, through Caddy in headless
# Chromium, as a throwaway `e2e-g19-*` user.
#
# Seeds three chats over REST and marks one as waiting on an approval and
# one unread straight in the agent database (the real approval and unread
# flows are G17's; this checks how the shell shows them), plus a throwaway
# `e2e-g19-*` shared space with the SDK's runtime-check app. Then
# g19_shell_smoke.mjs checks: a cold launch lands in an empty chat; the
# history drawer lists the chats in "needs you" order with their markers;
# + makes a new chat; Apps shows the grid and the dock; swiping reaches the
# shared space and its app; Files, Routines and Settings open from their
# tiles and back returns to Apps. No GPU turns.
#
# Deletes the user (threads, personal space), the shared space and the
# app's bundle and git repo on exit. Takes `/tmp/homeai-stack.lock` itself
# if the caller hasn't. Never recreates model-runner or postgres.
#
# Usage: scripts/e2e/g19_shell_smoke.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"

G19_SPACE_SLUG="e2e-g19-$(openssl rand -hex 3)"
G19_STATE="$(mktemp)"
export G19_SPACE_SLUG G19_STATE

cleanup() {
  local id ids
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$G19_STATE"
  rm -f "$G19_STATE"
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$G19_SPACE_SLUG'
    AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

log() {
  echo "[g19] $(date '+%H:%M:%S') $*"
}

e2e_auth_create_user g19
export G19_USER="$E2E_NEW_USER" G19_PASSWORD="$E2E_NEW_PASSWORD"
cookie="$(e2e_auth_login "$G19_USER" "$G19_PASSWORD")"

new_thread() {
  python3 - "$E2E_BASE" "$cookie" "$1" <<'PY'
import json
import sys
import urllib.request

base, cookie, title = sys.argv[1:4]
req = urllib.request.Request(
    f"{base}/api/threads",
    data=json.dumps({"title": title}).encode(),
    method="POST",
    headers={"Content-Type": "application/json", "Cookie": cookie},
)
with urllib.request.urlopen(req, timeout=15) as resp:
    print(json.load(resp)["id"])
PY
}

# Oldest first, so by recency alone the plain chat would come first.
G19_NEEDS_TITLE="G19 needs you $$"
G19_UNREAD_TITLE="G19 unread $$"
G19_PLAIN_TITLE="G19 plain $$"
G19_NEEDS_ID="$(new_thread "$G19_NEEDS_TITLE")"
sleep 1
G19_UNREAD_ID="$(new_thread "$G19_UNREAD_TITLE")"
sleep 1
G19_PLAIN_ID="$(new_thread "$G19_PLAIN_TITLE")"
for id in "$G19_NEEDS_ID" "$G19_UNREAD_ID" "$G19_PLAIN_ID"; do
  [[ "$id" =~ ^[0-9a-f-]{36}$ ]] || { log "FAIL: couldn't create a chat (got '$id')"; exit 1; }
done
_e2e_psql "$(_e2e_env_value POSTGRES_DB homeai)" "
  UPDATE threads SET awaiting_approval = true WHERE id = '$G19_NEEDS_ID';
  UPDATE threads SET unread = true WHERE id = '$G19_UNREAD_ID';" >/dev/null
export G19_NEEDS_TITLE G19_UNREAD_TITLE G19_PLAIN_TITLE G19_NEEDS_ID G19_UNREAD_ID G19_PLAIN_ID
log "seeded ${G19_USER}'s chats: needs you ${G19_NEEDS_ID}, unread ${G19_UNREAD_ID}, plain ${G19_PLAIN_ID}"

cd "$SCRIPT_DIR"
if [ ! -d node_modules/playwright ]; then
  npm install
fi
npx playwright install chromium >/dev/null
node "$SCRIPT_DIR/g19_shell_smoke.mjs"
