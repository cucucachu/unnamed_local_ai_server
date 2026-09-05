#!/usr/bin/env bash
# export-ca.sh — copy Caddy's local-CA root certificate out of the
# `caddy-data` volume to ${BACKUP_DIR}/homeai-root-ca.crt.
#
# Caddy generates this file the first time the `https://homeai.local`
# site starts (`tls internal`). The same public cert is also served at
# http://homeai.local/ca.crt (trusted-LAN trade-off — see
# docs/NETWORKING.md). The CA private key stays in the volume and is
# never copied or served.
#
# Usage (stack must be up):
#   scripts/export-ca.sh
#
# Writing ${BACKUP_DIR} under /srv typically needs sudo, same as
# infra/host/backup-workspace.sh. If this script isn't already root it
# will try `sudo -n` for the write; if that isn't cached, it prints the
# command to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="${REPO_ROOT}/.env"

env_var() {
  local key="$1" default="$2"
  local val=""
  if [[ -f "${ENV_FILE}" ]]; then
    val="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n1 | cut -d= -f2- || true)"
  fi
  echo "${val:-${default}}"
}

BACKUP_DIR="$(env_var BACKUP_DIR /srv/homeai/backups)"
HOMEAI_UID="$(env_var HOMEAI_UID 1000)"
HOMEAI_GID="$(env_var HOMEAI_GID 1000)"
DEST="${BACKUP_DIR}/homeai-root-ca.crt"
# Official Caddy image layout (caddy:2-alpine): local-CA root lives here
# once `tls internal` has issued at least one cert.
CONTAINER_CA="/data/caddy/pki/authorities/local/root.crt"

log() {
  echo "[export-ca] $*"
}

if ! docker compose ps --status running --format '{{.Service}}' 2>/dev/null | grep -qx caddy; then
  echo "error: caddy is not running — start the stack first (docker compose up -d)" >&2
  exit 1
fi

# First boot: the file appears after Caddy issues the homeai.local cert.
# A single HTTPS handshake (even one that the host doesn't trust yet) is
# enough to force issuance if it hasn't happened.
wait_for_ca() {
  local i
  for i in $(seq 1 30); do
    if docker compose exec -T caddy test -f "${CONTAINER_CA}" 2>/dev/null; then
      return 0
    fi
    python3 -c "
import ssl, urllib.request
ctx = ssl._create_unverified_context()
try:
    urllib.request.urlopen('https://homeai.local/', context=ctx, timeout=3)
except Exception:
    pass
" >/dev/null 2>&1 || true
    sleep 1
  done
  return 1
}

log "Waiting for ${CONTAINER_CA} inside caddy..."
if ! wait_for_ca; then
  echo "error: Caddy has not written ${CONTAINER_CA} after 30s. Is the https://homeai.local site in the Caddyfile?" >&2
  exit 1
fi

write_dest() {
  local dest_dir dest="$1"
  dest_dir="$(dirname "${dest}")"
  mkdir -p "${dest_dir}"
  docker compose exec -T caddy cat "${CONTAINER_CA}" >"${dest}"
  chmod 644 "${dest}"
  if [[ "$(id -u)" -eq 0 ]]; then
    chown "${HOMEAI_UID}:${HOMEAI_GID}" "${dest}"
  fi
}

if mkdir -p "${BACKUP_DIR}" 2>/dev/null && [[ -w "${BACKUP_DIR}" ]]; then
  write_dest "${DEST}"
elif [[ "$(id -u)" -ne 0 ]] && sudo -n true 2>/dev/null; then
  sudo -n mkdir -p "${BACKUP_DIR}"
  tmp="$(mktemp)"
  docker compose exec -T caddy cat "${CONTAINER_CA}" >"${tmp}"
  sudo -n cp "${tmp}" "${DEST}"
  sudo -n chmod 644 "${DEST}"
  sudo -n chown "${HOMEAI_UID}:${HOMEAI_GID}" "${DEST}"
  rm -f "${tmp}"
else
  echo "error: cannot write ${DEST} (BACKUP_DIR=${BACKUP_DIR}). Re-run as:" >&2
  echo "  sudo ${SCRIPT_DIR}/export-ca.sh" >&2
  exit 1
fi

log "Wrote ${DEST}"
log "Same cert is also at http://homeai.local/ca.crt (HTTP, trusted LAN)."
