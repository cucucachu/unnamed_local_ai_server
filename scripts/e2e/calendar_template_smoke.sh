#!/usr/bin/env bash
# #477: the Calendar template (examples/apps/calendar) in the real web app, through
# Caddy, in headless Chromium (`calendar_template_smoke.mjs`).
#
# Three throwaway users from the recovery CLI: the owner, an editor and a
# viewer of a throwaway shared space `e2e-cal-*`. The owner installs Contacts
# (with a birthday this month) and Calendar from the catalog (built by the
# real builder); then in three browsers:
#   - the month shows the Contacts birthday on its day;
#   - tapping an empty 2 PM slot in Day view adds a 2–3 PM event for the
#     editor, which the editor's month shows without reloading;
#   - an all-day event over three days sits in each day's all-day strip;
#   - a weekly Monday/Wednesday event shows on those days this week and next;
#     editing one occurrence makes it its own event and skips it in the
#     series; deleting one occurrence skips it; Delete all removes the series;
#   - two overlapping events share the Day column side by side;
#   - the person filter shows only the editor's event and hides birthdays;
#   - the viewer sees events but has no +, no empty-slot taps, no Edit or
#     Delete; a write as the viewer is refused.
# Everything it created (users, personal spaces, the shared space, the apps,
# instances and bundles) is deleted on exit.
#
# Prerequisites: as grocery_app_smoke.sh (stack up with caddy, platform and
# frontend from this tree, the builder image, Node, docker).
#
# Usage: scripts/e2e/calendar_template_smoke.sh
#        CAL_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/calendar_template_smoke.sh
set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

CAL_SMOKE_SPACE_SLUG="e2e-cal-$(openssl rand -hex 3)"
CAL_SMOKE_STATE="$(mktemp)"
export CAL_SMOKE_SPACE_SLUG CAL_SMOKE_STATE
cleanup() {
  local id ids
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$CAL_SMOKE_STATE"
  rm -f "$CAL_SMOKE_STATE"
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$CAL_SMOKE_SPACE_SLUG'
    AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user cal
export CAL_SMOKE_USER="$E2E_NEW_USER" CAL_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user cal-editor
export CAL_SMOKE_EDITOR="$E2E_NEW_USER" CAL_SMOKE_EDITOR_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user cal-viewer
export CAL_SMOKE_VIEWER="$E2E_NEW_USER" CAL_SMOKE_VIEWER_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"
if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
npx playwright install chromium >/dev/null

echo "==> Running against ${CAL_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/calendar_template_smoke.mjs"
