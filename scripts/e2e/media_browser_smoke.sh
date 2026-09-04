#!/usr/bin/env bash
# M5-02 full-stack media-player smoke test.
#
# Seeds a small synthetic video (`test-video.mp4`, via `ffmpeg` — see
# `seed_test_video` below) directly into the WORKSPACE_DIR host directory,
# opens a real headless browser against the live stack, navigates to the
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
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

VIDEO_FILE_NAME="test-video.mp4"

# Same ".env, one KEY=value, not the whole file" extraction as
# `exec_crossview_smoke.sh` — this script only needs WORKSPACE_DIR, and the
# rest of `.env` isn't guaranteed to be shell-safe to `source`.
ENV_FILE="${REPO_ROOT}/.env"
WORKSPACE_DIR="$(sed -n 's/^WORKSPACE_DIR=\(.*\)$/\1/p' "$ENV_FILE" | head -n1 | xargs)"
if [ -z "$WORKSPACE_DIR" ]; then
  echo "[media-browser-smoke] ERROR: WORKSPACE_DIR not set in ${ENV_FILE}" >&2
  exit 1
fi
VIDEO_HOST_PATH="${WORKSPACE_DIR}/${VIDEO_FILE_NAME}"

log() {
  echo "[media-browser-smoke] $(date '+%H:%M:%S') $*"
}

# Same one-liner M5-01 used for its own live verification: a 10s synthetic
# test pattern, `yuv420p` for broad player/codec compatibility. Run inside
# `homeai-exec-toolbox` (which already has `ffmpeg` baked in, per
# `services/code-exec-manager/exec-image/Dockerfile`) rather than requiring
# it on the host directly, bind-mounting WORKSPACE_DIR at `/w` so the
# output lands exactly where `agent-server`'s own workspace mount expects
# it (`docker-compose.yml`: `${WORKSPACE_DIR}:/data/workspace`).
seed_test_video() {
  log "Seeding ${VIDEO_HOST_PATH} via ffmpeg (homeai-exec-toolbox)..."
  rm -f "$VIDEO_HOST_PATH"
  docker run --rm -v "${WORKSPACE_DIR}:/w" homeai-exec-toolbox:latest \
    ffmpeg -y -f lavfi -i "testsrc=duration=10:size=640x360:rate=30" -pix_fmt yuv420p "/w/${VIDEO_FILE_NAME}"
  if [ ! -f "$VIDEO_HOST_PATH" ]; then
    log "ERROR: ${VIDEO_HOST_PATH} was not created"
    exit 1
  fi
  log "OK: seeded ${VIDEO_HOST_PATH} ($(stat -c%s "$VIDEO_HOST_PATH" 2>/dev/null || stat -f%z "$VIDEO_HOST_PATH") bytes)"
}

cleanup() {
  # Always runs (success or failure) so this script is safely re-runnable
  # and never leaves the seeded file behind in the real workspace.
  rm -f "$VIDEO_HOST_PATH" 2>/dev/null || true
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
MEDIA_SMOKE_FILE_NAME="$VIDEO_FILE_NAME" node "$SCRIPT_DIR/media_browser_smoke.mjs"
