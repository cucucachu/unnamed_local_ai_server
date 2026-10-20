#!/usr/bin/env bash
# #467: the Group chat template (examples/apps/group-chat) in the real web app, through
# Caddy, in headless Chromium (`chat_template_smoke.mjs`).
#
# Three throwaway users from the recovery CLI: the owner, an editor and a
# viewer of a throwaway shared space `e2e-chat-*`. The owner installs Group
# chat from the catalog (built by the real builder); then in three browsers:
#   - the owner and the editor post, and each sees the other's message
#     arrive without reloading, with the sender's name and a day separator;
#   - the owner edits a message (the editor sees "edited") and deletes one
#     (the editor sees "Message deleted"), and can't select the editor's;
#   - the viewer reads the room but has no message box and can't select
#     anything; a write as the viewer is refused;
#   - search finds a message by its words.
# Everything it created (users, personal spaces, the shared space, the app,
# instance and bundle) is deleted on exit.
#
# Prerequisites: as grocery_app_smoke.sh (stack up with caddy, platform and
# frontend from this tree, the builder image, Node, docker). Search needs
# the embedding model.
#
# Usage: scripts/e2e/chat_template_smoke.sh
#        CHAT_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/chat_template_smoke.sh
set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

CHAT_SMOKE_SPACE_SLUG="e2e-chat-$(openssl rand -hex 3)"
CHAT_SMOKE_STATE="$(mktemp)"
export CHAT_SMOKE_SPACE_SLUG CHAT_SMOKE_STATE
cleanup() {
  local id ids
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$CHAT_SMOKE_STATE"
  rm -f "$CHAT_SMOKE_STATE"
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$CHAT_SMOKE_SPACE_SLUG'
    AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user chat
export CHAT_SMOKE_USER="$E2E_NEW_USER" CHAT_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user chat-editor
export CHAT_SMOKE_EDITOR="$E2E_NEW_USER" CHAT_SMOKE_EDITOR_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user chat-viewer
export CHAT_SMOKE_VIEWER="$E2E_NEW_USER" CHAT_SMOKE_VIEWER_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"
if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
npx playwright install chromium >/dev/null

echo "==> Running against ${CHAT_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/chat_template_smoke.mjs"
