#!/usr/bin/env bash
# Issue #124 full-stack image-viewer + thumbnail smoke test.
#
# Opens a real headless browser against the live stack (`caddy` fronting
# the built frontend + proxying to `agent-server`), navigates to the Files
# tab, and drives the actual UI (no mocking — real REST `/api/files/upload`,
# real `/api/media/stream` byte-range serving reused as the image source)
# through: upload a tiny synthetic PNG -> assert a real thumbnail `<img>`
# renders in the list -> tap it (bypasses the action sheet, opens the
# in-app viewer directly, no download) -> right-click -> action sheet
# offers "View" (not "Play") -> delete. See `image_browser_smoke.mjs` for
# the step-by-step.
#
# Unlike `media_browser_smoke.sh`'s video (which needs `ffmpeg` inside
# `homeai-exec-toolbox`), the seeded PNG is uploaded straight through the
# real Chromium file chooser with an in-memory buffer — no `docker run`/
# host-filesystem seeding step needed here at all.
#
# Prerequisites (same convention as `files_browser_smoke.sh`):
#   - The full docker-compose stack is up and healthy:
#       docker compose up -d
#     (rebuild `caddy` first if the frontend changed:
#       docker compose build caddy && docker compose up -d caddy)
#   - Node.js/npm available on PATH (e.g. via nvm).
#
# Usage:
#   scripts/e2e/image_browser_smoke.sh
#   IMAGE_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/image_browser_smoke.sh
#
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$script_dir"

if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi

echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

echo "==> Running the smoke test against ${IMAGE_SMOKE_BASE_URL:-http://localhost/}..."
node "$script_dir/image_browser_smoke.mjs"
