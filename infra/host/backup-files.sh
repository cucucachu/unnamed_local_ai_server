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
# What's backed up: the spaces directory (SPACES_DIR: every user's and
# shared space's files, including what the agent writes, M11), the legacy
# files directory (FILES_DIR: pre-M11 files until the platform migrates
# them into the bootstrap admin's personal space, plus execute_code's
# /files until M11-03), the Postgres databases (`homeai`: thread/message state; `homeai_platform`:
# users, sessions, invites — see docs/ARCHITECTURE.md), and the
# `platform-data` volume (the platform's signing key and, before the first
# admin exists, the setup code). The volume copy stays root-only.
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
SPACES_DIR="$(env_var SPACES_DIR /srv/homeai/spaces)"
BACKUP_DIR="$(env_var BACKUP_DIR /srv/homeai/backups)"
HOMEAI_UID="$(env_var HOMEAI_UID 1000)"
HOMEAI_GID="$(env_var HOMEAI_GID 1000)"
POSTGRES_USER="$(env_var POSTGRES_USER homeai)"
POSTGRES_DB="$(env_var POSTGRES_DB homeai)"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "warning: ${ENV_FILE} not found — using defaults (FILES_DIR=${FILES_DIR}, BACKUP_DIR=${BACKUP_DIR})" >&2
fi

PLATFORM_DB="homeai_platform"
PLATFORM_VOLUME="homeai_platform-data"

mkdir -p "${BACKUP_DIR}/files" "${BACKUP_DIR}/pg" "${BACKUP_DIR}/platform-data"
# Same owner as the files directory itself, so the regular dev user can browse/
# restore from backups without needing sudo just to read them. Not
# platform-data: it holds the platform's private signing key.
chown "${HOMEAI_UID}:${HOMEAI_GID}" "${BACKUP_DIR}"
chown -R "${HOMEAI_UID}:${HOMEAI_GID}" "${BACKUP_DIR}/files" "${BACKUP_DIR}/pg"
chown root:root "${BACKUP_DIR}/platform-data"
chmod 0700 "${BACKUP_DIR}/platform-data"

# --- 1. Files mirrors ----------------------------------------------------
# The spaces mirror keeps the source's owners and modes (per-space uid/gid,
# 2770), so it's as private as SPACES_DIR itself - never chowned here.

if [[ -d "${SPACES_DIR}" ]]; then
  echo "Mirroring ${SPACES_DIR} -> ${BACKUP_DIR}/spaces ..."
  rsync -a --delete "${SPACES_DIR}/" "${BACKUP_DIR}/spaces/"
  echo "Spaces mirror done."
else
  echo "warning: ${SPACES_DIR} does not exist — skipping spaces mirror." >&2
fi

if [[ -d "${FILES_DIR}" ]]; then
  echo "Mirroring ${FILES_DIR} -> ${BACKUP_DIR}/files ..."
  rsync -a --delete "${FILES_DIR}/" "${BACKUP_DIR}/files/"
  echo "Files mirror done."
else
  echo "warning: ${FILES_DIR} does not exist — skipping files mirror." >&2
fi

# --- 2. Postgres dumps ---------------------------------------------------------
# Skips (with a warning, not an error — a nightly timer shouldn't fail the
# whole run just because the stack happened to be down) if the postgres
# container isn't up.

cd "${REPO_ROOT}"

# dump_db DB PREFIX: dump DB to pg/PREFIX-<date>.sql.gz, keep the newest
# KEEP_PG_DUMPS of that prefix (by count, mtime order).
dump_db() {
  local db="$1" prefix="$2" dump_file
  dump_file="${BACKUP_DIR}/pg/${prefix}-$(date +%F).sql.gz"
  echo "Dumping Postgres (${db}) -> ${dump_file} ..."
  docker compose exec -T postgres pg_dump -U "${POSTGRES_USER}" "${db}" | gzip >"${dump_file}"
  chown "${HOMEAI_UID}:${HOMEAI_GID}" "${dump_file}"
  echo "Postgres dump (${db}) done."

  mapfile -t dumps < <(ls -1t "${BACKUP_DIR}/pg/${prefix}"-*.sql.gz 2>/dev/null)
  if [[ "${#dumps[@]}" -gt "${KEEP_PG_DUMPS}" ]]; then
    for ((i = KEEP_PG_DUMPS; i < ${#dumps[@]}; i++)); do
      echo "Pruning old dump: ${dumps[$i]}"
      rm -f "${dumps[$i]}"
    done
  fi
}

if docker compose exec -T postgres pg_isready -U "${POSTGRES_USER}" >/dev/null 2>&1; then
  dump_db "${POSTGRES_DB}" homeai
  if [[ "$(docker compose exec -T postgres psql -U "${POSTGRES_USER}" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '${PLATFORM_DB}'")" == 1* ]]; then
    dump_db "${PLATFORM_DB}" "${PLATFORM_DB}"
  else
    echo "warning: database ${PLATFORM_DB} doesn't exist yet — skipping its dump." >&2
  fi
else
  echo "warning: postgres container not reachable (stack down?) — skipping pg dump." >&2
fi

# --- 3. platform-data volume ------------------------------------------------
# Copied straight from the volume's host directory (root can read it), so
# this works whether or not the stack is up.

if PLATFORM_DATA_SRC="$(docker volume inspect -f '{{.Mountpoint}}' "${PLATFORM_VOLUME}" 2>/dev/null)"; then
  echo "Mirroring volume ${PLATFORM_VOLUME} -> ${BACKUP_DIR}/platform-data ..."
  rsync -a --delete "${PLATFORM_DATA_SRC}/" "${BACKUP_DIR}/platform-data/"
  chmod 0700 "${BACKUP_DIR}/platform-data"
  echo "platform-data mirror done."
else
  echo "warning: volume ${PLATFORM_VOLUME} not found — skipping platform-data mirror." >&2
fi

echo "=== backup-files.sh summary ==="
echo "Spaces mirror    : ${BACKUP_DIR}/spaces"
echo "Files mirror     : ${BACKUP_DIR}/files"
DUMP_COUNT="$(find "${BACKUP_DIR}/pg" -maxdepth 1 -name 'homeai-*.sql.gz' | wc -l)"
PLATFORM_DUMP_COUNT="$(find "${BACKUP_DIR}/pg" -maxdepth 1 -name "${PLATFORM_DB}-*.sql.gz" | wc -l)"
echo "Postgres dumps   : ${BACKUP_DIR}/pg (${DUMP_COUNT} homeai, ${PLATFORM_DUMP_COUNT} ${PLATFORM_DB} kept)"
echo "platform-data    : ${BACKUP_DIR}/platform-data (root-only)"
