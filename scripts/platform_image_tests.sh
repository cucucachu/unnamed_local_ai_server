#!/usr/bin/env bash
# Platform tests that need the platform image's system packages: the ffmpeg
# tests, which skip under the host's `uv run pytest` (no ffmpeg on the host,
# and nothing gets installed there).
#
# Builds the platform Dockerfile's `base` stage (python + uv + ffmpeg, the
# layers the runtime image starts from), then runs pytest in it as your uid,
# with the source mounted read-only, the host's uv cache, and a throwaway
# `postgres:17` on a private network (`TEST_PG_HOST`, see
# services/platform/tests/conftest.py). Touches nothing of the compose
# project: no live image, volume, or service.
#
# Usage:
#   scripts/platform_image_tests.sh                  # the ffmpeg tests
#   scripts/platform_image_tests.sh tests/test_x.py  # any pytest arguments
#
# Tests that start containers themselves (test_fsops.py, test_storage.py,
# test_db_init.py) need the host's `uv run pytest`: there's no docker CLI in
# here.

set -euo pipefail

PLATFORM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../services/platform" && pwd)"
IMAGE="homeai-platform-test-base"
tag="$(openssl rand -hex 4)"
net="homeai-platform-test-$tag"
pg="homeai-platform-test-pg-$tag"
uv_cache="${UV_CACHE_DIR:-$HOME/.cache/uv}"

cleanup() {
  docker rm -f "$pg" >/dev/null 2>&1 || true
  docker network rm "$net" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker build -q --target base -t "$IMAGE" "$PLATFORM_DIR" >/dev/null
docker network create "$net" >/dev/null
# Superuser name and password: SUPERUSER / SUPERUSER_PASSWORD in tests/conftest.py.
docker run -d --rm --name "$pg" --network "$net" --label homeai.test=platform \
  -e POSTGRES_USER=homeai -e POSTGRES_PASSWORD=test-superuser-password \
  "${TEST_PG_IMAGE:-postgres:17}" >/dev/null

mkdir -p "$uv_cache"
[ "$#" -gt 0 ] || set -- tests/test_thumbnails.py tests/test_media_api.py
docker run --rm --network "$net" --user "$(id -u):$(id -g)" \
  -v "$PLATFORM_DIR:/src:ro" -w /src -v "$uv_cache:/uv-cache" \
  -e HOME=/tmp -e UV_CACHE_DIR=/uv-cache -e UV_PROJECT_ENVIRONMENT=/tmp/venv \
  -e UV_LINK_MODE=copy -e PYTHONPYCACHEPREFIX=/tmp/pycache -e TEST_PG_HOST="$pg" \
  "$IMAGE" sh -c 'uv sync -q --frozen && exec uv run -q --frozen pytest -p no:cacheprovider -rs "$@"' \
  sh "$@"
