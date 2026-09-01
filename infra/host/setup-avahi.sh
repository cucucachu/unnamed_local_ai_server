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

# Force IPv4-only mDNS publishing. This host typically has no routable IPv6
# on the LAN — only a link-local (fe80::...) address — and avahi's default
# (use-ipv6=yes) happily publishes an AAAA record for it too. mDNS clients
# that pick that AAAA record over the A record (observed in practice: phone
# browsers and Expo Go's RN networking stack) get a connection that just
# hangs forever, since a link-local address is meaningless without the
# originating device's own zone/scope index, which mDNS doesn't convey
# across devices. Disabling IPv6 publishing here means `homeai.local` only
# ever resolves to the real LAN IPv4 address, which every client can reach.
AVAHI_CONF="/etc/avahi/avahi-daemon.conf"
IPV6_DISABLED=0
if [[ -f "${AVAHI_CONF}" ]] && grep -qE '^use-ipv6=yes\s*$' "${AVAHI_CONF}"; then
  sed -i 's/^use-ipv6=yes\s*$/use-ipv6=no/' "${AVAHI_CONF}"
  IPV6_DISABLED=1
elif [[ -f "${AVAHI_CONF}" ]] && ! grep -qE '^use-ipv6=no\s*$' "${AVAHI_CONF}"; then
  # Neither yes nor no present (unexpected stock config) — append explicitly
  # under [server] so the setting is unambiguous.
  sed -i '/^\[server\]/a use-ipv6=no' "${AVAHI_CONF}"
  IPV6_DISABLED=1
fi

# Restrict avahi to the real LAN-facing interface only. By default avahi
# publishes records on every non-loopback interface it sees, including
# Docker's virtual bridges (docker0, and a br-* per compose project) — those
# come and go and their IPs (e.g. 172.18.0.1) are only reachable from this
# host itself, never from a phone on the LAN. If avahi answers a query using
# one of those interfaces' addresses instead of the real one, the client
# gets an address it can never route to. Same auto-detection technique as
# setup-ufw.sh's DOCKER-USER rule: whatever interface currently owns the
# default route is "the" LAN interface.
DEFAULT_IFACE="$(ip route show default 2>/dev/null | awk '/^default/ {print $5; exit}')"
IFACE_CHANGED=0
if [[ -z "${DEFAULT_IFACE}" ]]; then
  echo "warning: could not auto-detect default-route interface; avahi may still publish Docker bridge addresses. Set allow-interfaces manually in ${AVAHI_CONF}, see docs/NETWORKING.md." >&2
else
  if grep -qE "^allow-interfaces=${DEFAULT_IFACE}\$" "${AVAHI_CONF}"; then
    : # already correct
  elif grep -qE '^allow-interfaces=' "${AVAHI_CONF}"; then
    sed -i "s/^allow-interfaces=.*/allow-interfaces=${DEFAULT_IFACE}/" "${AVAHI_CONF}"
    IFACE_CHANGED=1
  else
    sed -i "/^\[server\]/a allow-interfaces=${DEFAULT_IFACE}" "${AVAHI_CONF}"
    IFACE_CHANGED=1
  fi
fi

# avahi-daemon reads the hostname (and this conf file) once at startup and
# does NOT notice a live hostnamectl change or an edit to avahi-daemon.conf
# — it keeps the old behavior until restarted. Restart whenever we changed
# the hostname, changed the IPv6 setting or allowed interface, or whenever
# the daemon was freshly installed/enabled above, so it always reflects the
# current config.
if [[ "${HOSTNAME_CHANGED}" -eq 1 ]] || [[ "${IPV6_DISABLED}" -eq 1 ]] || [[ "${IFACE_CHANGED}" -eq 1 ]] || ! systemctl is-active --quiet avahi-daemon; then
  systemctl restart avahi-daemon
else
  systemctl start avahi-daemon
fi

echo "Verifying mDNS resolution of ${RESOLVE_NAME} (retrying up to 10s)..."
RESOLVED=0
RESOLVED_ADDR=""
for _ in $(seq 1 10); do
  if RESOLVED_ADDR="$(avahi-resolve -n "${RESOLVE_NAME}" 2>/dev/null | awk '{print $2}')" && [[ -n "${RESOLVED_ADDR}" ]]; then
    RESOLVED=1
    break
  fi
  sleep 1
done

echo "=== setup-avahi.sh summary ==="
echo "Hostname   : $(hostnamectl --static status 2>/dev/null || hostname)"
systemctl is-active avahi-daemon | xargs -I{} echo "avahi-daemon: {}"
if [[ "${RESOLVED}" -ne 1 ]]; then
  echo "Resolution : FAILED to resolve ${RESOLVE_NAME} within 10s" >&2
  exit 1
fi

# An IPv6 address containing a colon means the fix above didn't take (or
# use-ipv6 was somehow re-enabled some other way) — fail loudly rather than
# report false success, since this exact failure mode is silent from a
# phone's perspective (hung connection, not an error).
if [[ "${RESOLVED_ADDR}" == *:* ]]; then
  echo "Resolution : FAILED — ${RESOLVE_NAME} resolved to an IPv6 address (${RESOLVED_ADDR}); phones/apps that prefer AAAA over A will hang connecting to it. Check use-ipv6 in ${AVAHI_CONF}." >&2
  exit 1
fi

# Confirm the resolved address is actually the LAN interface's current IP,
# not some other interface avahi is still (mis)publishing on — e.g. a
# Docker bridge's 172.x address, which is only reachable from this host
# itself. This is the exact failure this script was hit by in practice.
if [[ -n "${DEFAULT_IFACE}" ]]; then
  IFACE_ADDR="$(ip -4 -o addr show dev "${DEFAULT_IFACE}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"
  if [[ -n "${IFACE_ADDR}" ]] && [[ "${RESOLVED_ADDR}" != "${IFACE_ADDR}" ]]; then
    echo "Resolution : FAILED — ${RESOLVE_NAME} resolved to ${RESOLVED_ADDR}, but ${DEFAULT_IFACE} (the LAN interface) is currently ${IFACE_ADDR}. avahi is still answering from a different interface (e.g. a Docker bridge) — a phone on the LAN cannot reach ${RESOLVED_ADDR}." >&2
    exit 1
  fi
fi

echo "Resolution : OK (${RESOLVED_ADDR})"
