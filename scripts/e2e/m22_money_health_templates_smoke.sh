#!/usr/bin/env bash
# M22-17: the money and health templates (examples/apps/budget, workouts,
# health-log, countdowns) in the real web app, through Caddy, in headless
# Chromium (`m22_money_health_templates_smoke.mjs`).
#
# A throwaway user from the recovery CLI installs the four templates into
# their Personal space through the catalog API (each built by the real
# builder), then in the browser:
#   - Budget: the usual categories, a monthly budget, a bank statement CSV
#     imported from the app info screen's Data section (a bad file is
#     refused with its line; the good one needs its Date header mapped and
#     Balance skipped), categorizing with "Always use this category" (the
#     other matching row follows), "Apply my rules", a manual transaction,
#     and the month's totals, budget, pie chart and trend chart;
#   - Workouts: an exercise, a workout with sets (one deleted), "Repeat
#     this workout today", and the progress chart against an older workout;
#   - Health log: weight, sleep and water; readings on two days, water
#     adding up, the weight chart and stats, a goal, a new measure, deleting
#     a reading;
#   - Countdowns: a trip, a yearly anniversary and a past date, an edit;
#     then Contacts is installed with a person and their birthday shows up
#     (through the `people` export), can be hidden and shown again.
# Everything it created (the user, their personal space with the apps,
# instances and bundles) is deleted on exit.
#
# Prerequisites: as grocery_app_smoke.sh (stack up with caddy, platform and
# frontend from this tree, the builder image, Node, docker). Takes a few
# minutes.
#
# Usage: scripts/e2e/m22_money_health_templates_smoke.sh
#        MONEY_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/m22_money_health_templates_smoke.sh
set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

MONEY_SMOKE_STATE="$(mktemp)"
export MONEY_SMOKE_STATE
cleanup() {
  local id
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$MONEY_SMOKE_STATE"
  rm -f "$MONEY_SMOKE_STATE"
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user money
export MONEY_SMOKE_USER="$E2E_NEW_USER" MONEY_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"
if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
npx playwright install chromium >/dev/null

echo "==> Running against ${MONEY_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/m22_money_health_templates_smoke.mjs"
