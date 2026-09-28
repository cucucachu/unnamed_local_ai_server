#!/usr/bin/env bash
# build-builder-image.sh — build the app builder image (M12-04) that
# code-exec-manager runs for `POST /builds/{id}/{compile|smoke}`.
#
# A host image build, not a compose service, for the same reason as
# services/code-exec-manager/build-exec-image.sh: compose never runs build
# containers itself. Needs the internet (npm ci); build containers never do.
# Idempotent: re-running reuses cached layers and moves the tag forward.
#
# Usage: services/app-builder/build-builder-image.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
IMAGE="homeai-app-builder:latest"

echo "=== build-builder-image.sh: building ${IMAGE} ==="
# The repo root, for packages/homeai-sdk; the root .dockerignore keeps
# node_modules, dist and model weights out of the context.
docker build -t "${IMAGE}" -f "${SCRIPT_DIR}/Dockerfile" "${REPO_ROOT}"
echo "=== build-builder-image.sh: done ==="
docker images "${IMAGE}"
