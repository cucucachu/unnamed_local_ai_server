#!/usr/bin/env bash
# build-exec-image.sh — build the exec toolbox image used by code-exec
# containers (M4-02, not yet built).
#
# This is a HOST image build, not a compose service: compose can't build an
# image it never runs itself (docker-compose.yml's own service-list comment
# marks `code-exec-manager` as the M4-02 addition; the exec containers it
# spins up per-thread aren't compose services at all, just plain `docker
# run`s against this image). Run manually (or from a fresh-host setup),
# not via `docker compose up`.
#
# Idempotent: safe to re-run (`docker build` reuses cached layers; retagging
# `:latest` just moves the tag forward).
#
# Usage: services/code-exec-manager/build-exec-image.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

# Pull a single KEY=value out of .env without sourcing the whole file (values
# may not be shell-safe), falling back to a default if missing/empty - same
# helper as infra/host/setup-workspace.sh, kept local rather than shared
# since it's a 5-line function and these two scripts otherwise have no
# common dependency.
env_var() {
  local key="$1" default="$2"
  local val=""
  if [[ -f "${ENV_FILE}" ]]; then
    val="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n1 | cut -d= -f2- || true)"
  fi
  echo "${val:-${default}}"
}

HOMEAI_UID="$(env_var HOMEAI_UID 1000)"
HOMEAI_GID="$(env_var HOMEAI_GID 1000)"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "warning: ${ENV_FILE} not found - using defaults (HOMEAI_UID=${HOMEAI_UID}, HOMEAI_GID=${HOMEAI_GID})" >&2
fi

echo "=== build-exec-image.sh: building homeai-exec-toolbox:latest ==="
echo "HOMEAI_UID : ${HOMEAI_UID}"
echo "HOMEAI_GID : ${HOMEAI_GID}"

docker build \
  --build-arg "HOMEAI_UID=${HOMEAI_UID}" \
  --build-arg "HOMEAI_GID=${HOMEAI_GID}" \
  -t homeai-exec-toolbox:latest \
  "${SCRIPT_DIR}/exec-image/"

echo "=== build-exec-image.sh: done ==="
docker images homeai-exec-toolbox:latest
