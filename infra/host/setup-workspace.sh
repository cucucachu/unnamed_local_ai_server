#!/usr/bin/env bash
# setup-workspace.sh — create and own the shared Home AI Agent workspace directory.
#
# Idempotent: safe to re-run. Must be run with sudo. Reads HOMEAI_UID, HOMEAI_GID,
# and WORKSPACE_DIR from .env at the repo root (falls back to the same defaults
# as .env.example if a variable is unset/missing).
#
# Usage: sudo infra/host/setup-workspace.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

if [[ "${EUID}" -ne 0 ]]; then
  echo "error: must be run with sudo (needs to chown the workspace directory)" >&2
  exit 1
fi

# Pull a single KEY=value out of .env without sourcing the whole file (values
# may not be shell-safe), falling back to a default if missing/empty.
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
WORKSPACE_DIR="$(env_var WORKSPACE_DIR /srv/homeai/workspace)"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "warning: ${ENV_FILE} not found — using defaults (HOMEAI_UID=${HOMEAI_UID}, HOMEAI_GID=${HOMEAI_GID}, WORKSPACE_DIR=${WORKSPACE_DIR})" >&2
fi

mkdir -p "${WORKSPACE_DIR}"
chown -R "${HOMEAI_UID}:${HOMEAI_GID}" "${WORKSPACE_DIR}"
chmod 775 "${WORKSPACE_DIR}"

echo "=== setup-workspace.sh summary ==="
echo "Workspace dir : ${WORKSPACE_DIR}"
echo "Owner         : ${HOMEAI_UID}:${HOMEAI_GID}"
ls -ld "${WORKSPACE_DIR}"
