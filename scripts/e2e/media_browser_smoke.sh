#!/usr/bin/env bash
# M5-02 full-stack media-player smoke test.
#
# Seeds a small synthetic video (`media-browser-smoke-test-video.mp4`, via
# `ffmpeg` — see `seed_test_video` below) into a temp dir, uploads it into
# the e2e user's Personal space via the platform files API, opens a real headless browser against the live stack, navigates to the
# Files tab, taps the seeded file (asserting M5-02's tap-routing opens the
# player modal directly), and drives real script-level play/seek against
# the real `<video>` element — see `media_browser_smoke.mjs` for the full
# step-by-step. Cleans up the seeded file on any exit (success or failure).
#
# Prerequisites (not managed by this script — same convention as
# `chat_browser_smoke.sh`/`files_browser_smoke.sh`):
#   - The full docker-compose stack is up and healthy:
#       docker compose up -d
#     (rebuild `caddy` first if the frontend changed:
#       docker compose build caddy && docker compose up -d caddy)
#   - Node.js/npm available on PATH (e.g. via nvm).
#   - `docker` available on PATH, and the `homeai-exec-toolbox:latest`
#     image already built (`services/code-exec-manager/build-exec-image.sh`)
#     — this script shells out to THAT image for `ffmpeg` (mirroring M5-01's
#     own live-verification convention) rather than requiring ffmpeg on the
#     host directly.
#
# Usage:
#   scripts/e2e/media_browser_smoke.sh
#   MEDIA_SMOKE_BASE_URL=http://homeai.local/ scripts/e2e/media_browser_smoke.sh
#
# M6-03: video filename prefixed with this script's own name (was the bare
# generic "test-video.mp4"), which was a collision surface while it was
# written straight into the shared files root.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VIDEO_FILE_NAME="media-browser-smoke-test-video.mp4"

# M11-01: the clip is generated into a temp dir and uploaded by the .mjs
# through the platform files API as the e2e user (it lands in that user's
# Personal space and goes away with the user), never into a host files dir.
SEED_DIR="$(mktemp -d)"
VIDEO_SEED_PATH="${SEED_DIR}/${VIDEO_FILE_NAME}"

log() {
  echo "[media-browser-smoke] $(date '+%H:%M:%S') $*"
}

seed_test_video() {
  log "Generating ${VIDEO_SEED_PATH} via ffmpeg (homeai-exec-toolbox)..."
  docker run --rm --user "$(id -u):$(id -g)" -v "${SEED_DIR}:/w" homeai-exec-toolbox:latest \
    ffmpeg -y -f lavfi -i "testsrc=duration=10:size=640x360:rate=30" -pix_fmt yuv420p "/w/${VIDEO_FILE_NAME}"
  if [ ! -f "$VIDEO_SEED_PATH" ]; then
    log "ERROR: ${VIDEO_SEED_PATH} was not created"
    exit 1
  fi
  log "OK: generated ${VIDEO_SEED_PATH} ($(stat -c%s "$VIDEO_SEED_PATH") bytes)"
}

cleanup() {
  rm -rf "$SEED_DIR" 2>/dev/null || true
}
trap cleanup EXIT

cd "$SCRIPT_DIR"

if [ ! -d node_modules/playwright ]; then
  echo "==> Installing Playwright (npm install)..."
  npm install
fi

echo "==> Ensuring the Chromium browser binary is installed..."
npx playwright install chromium

seed_test_video

echo "==> Running the smoke test against ${MEDIA_SMOKE_BASE_URL:-http://localhost/}..."
MEDIA_SMOKE_FILE_PATH="$VIDEO_SEED_PATH" node "$SCRIPT_DIR/media_browser_smoke.mjs"
