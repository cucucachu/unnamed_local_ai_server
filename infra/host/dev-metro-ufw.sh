#!/usr/bin/env bash
# dev-metro-ufw.sh — dev-only LAN access to the Metro bundler (Expo Go).
#
# `npx expo start` (default port 8081) needs to be reachable from a phone on
# the LAN for Expo Go to load the JS bundle / connect its dev-tools socket.
# This is NOT part of the always-on stack's firewall posture (setup-ufw.sh) —
# Metro only runs while you're actively developing, so this rule is meant to
# be opened for a session and closed again afterward, not left on
# permanently. Idempotent either way: safe to run `open` repeatedly (no
# duplicate rule) and `close` when no rule exists (no error).
#
# Reads LAN_SUBNET from .env at the repo root (same variable, same default,
# as setup-ufw.sh — keep these in sync if you change it).
#
# Usage: sudo infra/host/dev-metro-ufw.sh open|close

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
METRO_PORT=8081

usage() {
  echo "Usage: sudo $0 open|close" >&2
  exit 1
}

if [[ "${EUID}" -ne 0 ]]; then
  echo "error: must be run with sudo" >&2
  exit 1
fi

if [[ $# -ne 1 || ( "$1" != "open" && "$1" != "close" ) ]]; then
  usage
fi
ACTION="$1"

env_var() {
  local key="$1" default="$2"
  local val=""
  if [[ -f "${ENV_FILE}" ]]; then
    val="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n1 | cut -d= -f2- || true)"
  fi
  echo "${val:-${default}}"
}

LAN_SUBNET="$(env_var LAN_SUBNET 192.168.1.0/24)"
if [[ ! -f "${ENV_FILE}" ]]; then
  echo "warning: ${ENV_FILE} not found — using default LAN_SUBNET=${LAN_SUBNET}." >&2
fi

if ! command -v ufw >/dev/null 2>&1; then
  echo "error: ufw not found — run infra/host/setup-ufw.sh first." >&2
  exit 1
fi

if [[ "${ACTION}" == "open" ]]; then
  echo "Allowing tcp/${METRO_PORT} from ${LAN_SUBNET} (Metro bundler, Expo Go dev)..."
  ufw allow from "${LAN_SUBNET}" to any port "${METRO_PORT}" proto tcp
else
  echo "Removing tcp/${METRO_PORT} allow rule for ${LAN_SUBNET} (if present)..."
  # `ufw delete allow ...` errors if the rule doesn't exist; don't fail the
  # script over that — closing an already-closed port is a no-op, not an error.
  ufw delete allow from "${LAN_SUBNET}" to any port "${METRO_PORT}" proto tcp || true
fi

echo "=== dev-metro-ufw.sh summary ==="
ufw status verbose | grep -E "^Status|${METRO_PORT}" || true
