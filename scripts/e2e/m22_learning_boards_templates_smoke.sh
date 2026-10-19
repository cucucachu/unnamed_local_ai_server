#!/usr/bin/env bash
# M22-18: the learning and boards templates (examples/apps/flashcards,
# kanban, books, calculator) in the real web app, through Caddy, in
# headless Chromium (`m22_learning_boards_templates_smoke.mjs`).
#
# A throwaway user from the recovery CLI installs the four templates into
# their Personal space through the catalog API (each built by the real
# builder), then in the browser:
#   - Flashcards: a deck with three cards, a study session (Show answer,
#     the grade buttons' intervals, Good / Again / Easy, the Again card
#     coming back last, the day's count), the Good button and grade.sql
#     agreeing on a card three days apart, start a card over, edit, search,
#     and the Progress totals and chart;
#   - Kanban: a board with To do / Doing / Done, cards added, a mouse swipe
#     and a long-press moving cards on, positions staying dense, a column
#     over its limit, a card's note, due date, assignee and Move up, moving
#     to Done and deleting from the swipe actions, and the columns screen
#     (add, move left, a limit, delete);
#   - Books and media: a book, a film and a game with ratings and dates,
#     the shelf's sections and wording, swiping to finish and to start, the
#     status and kind chips, search by notes, "Gave up" and back to "Want
#     to", and This year's counts, average and chart;
#   - Calculator and units: precedence, parentheses, = and Earlier,
#     dividing by 0, % and ⌫, 0.1 + 0.2, temperature with swap, kitchen
#     volumes, and a tip split three ways with rounding up.
# Everything it created (the user, their personal space with the apps,
# instances and bundles) is deleted on exit.
#
# Prerequisites: as grocery_app_smoke.sh (stack up with caddy, platform and
# frontend from this tree, the builder image, Node, docker). Takes a few
# minutes.
#
# Usage: scripts/e2e/m22_learning_boards_templates_smoke.sh
#        BOARDS_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/m22_learning_boards_templates_smoke.sh
set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/auth.sh
source "$script_dir/lib/auth.sh"

BOARDS_SMOKE_STATE="$(mktemp)"
export BOARDS_SMOKE_STATE
cleanup() {
  local id
  while read -r id || [ -n "$id" ]; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" </dev/null || true
  done <"$BOARDS_SMOKE_STATE"
  rm -f "$BOARDS_SMOKE_STATE"
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user boards
export BOARDS_SMOKE_USER="$E2E_NEW_USER" BOARDS_SMOKE_PASSWORD="$E2E_NEW_PASSWORD"

cd "$script_dir"
if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi
npx playwright install chromium >/dev/null

echo "==> Running against ${BOARDS_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/m22_learning_boards_templates_smoke.mjs"
