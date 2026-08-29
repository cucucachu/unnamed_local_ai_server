#!/usr/bin/env bash
# setup-avahi.sh — install/enable avahi-daemon and make this host answer to
# homeai.local via mDNS.
#
# Idempotent: safe to re-run. Must be run with sudo.
#
# Usage: sudo infra/host/setup-avahi.sh [--keep-hostname]
#
#   --keep-hostname   Don't change the system hostname; just install/verify
#                      avahi against whatever hostname is already set.

set -euo pipefail

TARGET_HOSTNAME="homeai"
KEEP_HOSTNAME=0

for arg in "$@"; do
  case "${arg}" in
    --keep-hostname) KEEP_HOSTNAME=1 ;;
    *) echo "error: unknown argument '${arg}'" >&2; exit 1 ;;
  esac
done

if [[ "${EUID}" -ne 0 ]]; then
  echo "error: must be run with sudo" >&2
  exit 1
fi

if ! command -v avahi-daemon >/dev/null 2>&1; then
  echo "Installing avahi-daemon..."
  apt-get update -qq
  apt-get install -y avahi-daemon avahi-utils
fi

systemctl enable avahi-daemon >/dev/null 2>&1 || true

CURRENT_HOSTNAME="$(hostnamectl --static status 2>/dev/null || hostname)"
HOSTNAME_CHANGED=0

if [[ "${KEEP_HOSTNAME}" -eq 1 ]]; then
  if [[ "${CURRENT_HOSTNAME}" != "${TARGET_HOSTNAME}" ]]; then
    echo "warning: --keep-hostname set, current hostname is '${CURRENT_HOSTNAME}' (not '${TARGET_HOSTNAME}'); avahi will advertise '${CURRENT_HOSTNAME}.local' instead." >&2
  fi
elif [[ "${CURRENT_HOSTNAME}" != "${TARGET_HOSTNAME}" ]]; then
  echo "warning: overwriting hostname '${CURRENT_HOSTNAME}' -> '${TARGET_HOSTNAME}' (pass --keep-hostname to skip this)." >&2
  hostnamectl set-hostname "${TARGET_HOSTNAME}"
  HOSTNAME_CHANGED=1
fi

RESOLVE_NAME="$(hostnamectl --static status 2>/dev/null || hostname).local"

# avahi-daemon reads the hostname once at startup and does NOT notice a live
# hostnamectl change — it keeps advertising the old name until restarted.
# Restart whenever we changed the hostname, or whenever the daemon was freshly
# installed/enabled above, so it always advertises the current hostname.
if [[ "${HOSTNAME_CHANGED}" -eq 1 ]] || ! systemctl is-active --quiet avahi-daemon; then
  systemctl restart avahi-daemon
else
  systemctl start avahi-daemon
fi

echo "Verifying mDNS resolution of ${RESOLVE_NAME} (retrying up to 10s)..."
RESOLVED=0
for _ in $(seq 1 10); do
  if avahi-resolve -n "${RESOLVE_NAME}" >/dev/null 2>&1; then
    RESOLVED=1
    break
  fi
  sleep 1
done

echo "=== setup-avahi.sh summary ==="
echo "Hostname   : $(hostnamectl --static status 2>/dev/null || hostname)"
systemctl is-active avahi-daemon | xargs -I{} echo "avahi-daemon: {}"
if [[ "${RESOLVED}" -eq 1 ]]; then
  echo "Resolution : OK"
  avahi-resolve -n "${RESOLVE_NAME}"
else
  echo "Resolution : FAILED to resolve ${RESOLVE_NAME} within 10s" >&2
  exit 1
fi
