#!/usr/bin/env bash
# backup-files.sh — mirror the files directory + dump Postgres to BACKUP_DIR.
#
# Idempotent: safe to re-run (rsync --delete mirrors the current state each
# time; pg dumps are one-per-day, overwritten if re-run same day, oldest
# pruned beyond 14). Must be run with sudo: FILES_DIR is root-owned at
# the /srv/homeai parent level (only chowned to HOMEAI_UID/GID one level
# down by setup-files.sh), and BACKUP_DIR lives under the same /srv tree
# by default — a non-root user can't mkdir there. Running as root also means
# the installed systemd service (infra/host/install-backup-timer.sh) needs
# no extra User=/permission wrangling: root can always read FILES_DIR
# and always reach docker.sock for the pg_dump step below.
#
# What's backed up: the files directory (files.rest-managed content) and
# the Postgres database (thread/message state — see docs/ARCHITECTURE.md).
# What's NOT backed up: model weights (services/model-runner/models/ —
# multi-GB, re-downloadable via fetch-model.sh, not user data), Docker
# images/containers, .env (secrets — back that up yourself, out of band, if
# you want to).
#
# Usage: sudo infra/host/backup-files.sh
#        (run manually, or installed as a daily timer — see
#        infra/host/install-backup-timer.sh)

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "error: must be run with sudo (needs to read FILES_DIR and write BACKUP_DIR under /srv)" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"
KEEP_PG_DUMPS=14

env_var() {
  local key="$1" default="$2"
  local val=""
  if [[ -f "${ENV_FILE}" ]]; then
    val="$(grep -E "^${key}=" "${ENV_FILE}" | tail -n1 | cut -d= -f2- || true)"
  fi
  echo "${val:-${default}}"
}

FILES_DIR="$(env_var FILES_DIR /srv/homeai/files)"
BACKUP_DIR="$(env_var BACKUP_DIR /srv/homeai/backups)"
HOMEAI_UID="$(env_var HOMEAI_UID 1000)"
HOMEAI_GID="$(env_var HOMEAI_GID 1000)"
POSTGRES_USER="$(env_var POSTGRES_USER homeai)"
POSTGRES_DB="$(env_var POSTGRES_DB homeai)"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "warning: ${ENV_FILE} not found — using defaults (FILES_DIR=${FILES_DIR}, BACKUP_DIR=${BACKUP_DIR})" >&2
fi

mkdir -p "${BACKUP_DIR}/files" "${BACKUP_DIR}/pg"
# Same owner as the files directory itself, so the regular dev user can browse/
# restore from backups without needing sudo just to read them.
chown -R "${HOMEAI_UID}:${HOMEAI_GID}" "${BACKUP_DIR}"

# --- 1. Files mirror ------------------------------------------------------

if [[ -d "${FILES_DIR}" ]]; then
  echo "Mirroring ${FILES_DIR} -> ${BACKUP_DIR}/files ..."
  rsync -a --delete "${FILES_DIR}/" "${BACKUP_DIR}/files/"
  echo "Files mirror done."
else
  echo "warning: ${FILES_DIR} does not exist — skipping files mirror." >&2
fi

# --- 2. Postgres dump ----------------------------------------------------------
# Skips (with a warning, not an error — a nightly timer shouldn't fail the
# whole run just because the stack happened to be down) if the postgres
# container isn't up.

cd "${REPO_ROOT}"

if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER}" >/dev/null 2>&1; then
  DUMP_FILE="${BACKUP_DIR}/pg/homeai-$(date +%F).sql.gz"
  echo "Dumping Postgres (${POSTGRES_DB}) -> ${DUMP_FILE} ..."
  docker compose exec -T postgres pg_dump -U "${POSTGRES_USER}" "${POSTGRES_DB}" | gzip >"${DUMP_FILE}"
  echo "Postgres dump done."

  # Prune to the last KEEP_PG_DUMPS by count (mtime order, oldest first).
  mapfile -t dumps < <(ls -1t "${BACKUP_DIR}/pg"/homeai-*.sql.gz 2>/dev/null)
  if [[ "${#dumps[@]}" -gt "${KEEP_PG_DUMPS}" ]]; then
    for ((i = KEEP_PG_DUMPS; i < ${#dumps[@]}; i++)); do
      echo "Pruning old dump: ${dumps[$i]}"
      rm -f "${dumps[$i]}"
    done
  fi
else
  echo "warning: postgres container not reachable (stack down?) — skipping pg dump." >&2
fi

echo "=== backup-files.sh summary ==="
echo "Files mirror     : ${BACKUP_DIR}/files"
DUMP_COUNT="$(find "${BACKUP_DIR}/pg" -maxdepth 1 -name 'homeai-*.sql.gz' | wc -l)"
echo "Postgres dumps   : ${BACKUP_DIR}/pg (${DUMP_COUNT} kept)"
