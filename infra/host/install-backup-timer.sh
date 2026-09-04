#!/usr/bin/env bash
# install-backup-timer.sh — install/uninstall the daily backup systemd timer.
#
# Installs infra/host/systemd/homeai-backup.{service,timer} into
# /etc/systemd/system, substituting this repo's actual path into the
# service's ExecStart (so it works regardless of where the repo is cloned).
# Idempotent: safe to re-run (regenerates the service unit every time, in
# case the repo moved; the timer unit is static). Runs
# infra/host/backup-workspace.sh as root once a day at 03:00 (see that
# script's own header for why root is required).
#
# Usage: sudo infra/host/install-backup-timer.sh              # install + enable
#        sudo infra/host/install-backup-timer.sh --uninstall  # disable + remove

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
UNIT_DIR="/etc/systemd/system"
SERVICE_NAME="homeai-backup.service"
TIMER_NAME="homeai-backup.timer"

if [[ "${EUID}" -ne 0 ]]; then
  echo "error: must be run with sudo" >&2
  exit 1
fi

if [[ "${1:-}" == "--uninstall" ]]; then
  echo "Disabling and removing ${TIMER_NAME}/${SERVICE_NAME}..."
  systemctl disable --now "${TIMER_NAME}" 2>/dev/null || true
  rm -f "${UNIT_DIR}/${SERVICE_NAME}" "${UNIT_DIR}/${TIMER_NAME}"
  systemctl daemon-reload
  echo "Uninstalled."
  exit 0
elif [[ -n "${1:-}" ]]; then
  echo "Usage: sudo $0 [--uninstall]" >&2
  exit 1
fi

echo "Installing ${SERVICE_NAME} (repo: ${REPO_ROOT})..."
sed "s#__REPO_ROOT__#${REPO_ROOT}#g" "${SCRIPT_DIR}/systemd/homeai-backup.service" >"${UNIT_DIR}/${SERVICE_NAME}"
cp "${SCRIPT_DIR}/systemd/homeai-backup.timer" "${UNIT_DIR}/${TIMER_NAME}"

systemctl daemon-reload
systemctl enable --now "${TIMER_NAME}"

echo "=== install-backup-timer.sh summary ==="
systemctl list-timers "${TIMER_NAME}" --no-pager
