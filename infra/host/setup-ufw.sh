#!/usr/bin/env bash
# setup-ufw.sh — LAN-only firewall for the Home AI Agent host.
#
# Idempotent: safe to re-run. Must be run with sudo. Reads LAN_SUBNET from
# .env at the repo root (default 192.168.1.0/24 if missing — override in .env
# to match your actual network before running this for real).
#
# Usage: sudo infra/host/setup-ufw.sh
#
# Docker caveat (important): Docker publishes container ports via its own
# iptables/nftables rules, inserted ahead of ufw's chain — so `ufw allow`/`deny`
# alone does NOT restrict traffic to published container ports (e.g. caddy's
# 80:80 and 443:443). Since caddy is the only service that publishes ports, the
# real enforcement for them is the DOCKER-USER chain, which Docker guarantees
# to consult before its own forwarding rules. See docs/NETWORKING.md.
#
# Persistence (M6-01 fix — do NOT reintroduce iptables-persistent here): the
# DOCKER-USER rule added below is in-memory only and would normally vanish on
# reboot. The obvious fix (`apt-get install iptables-persistent`) is a REAL
# BUG, discovered live on this exact host by M6-01's verify_network.sh: on
# this Ubuntu release, the `ufw` package itself declares
# `Breaks: iptables-persistent, netfilter-persistent` (confirmed via
# `apt-cache show ufw`) — installing either of those SILENTLY REMOVES ufw as
# part of the same apt transaction (apt reports "will be REMOVED: ufw" but a
# non-interactive `-y` install sails right past that). An earlier version of
# this script did exactly that and took the whole firewall down. Instead,
# persistence here is a small systemd oneshot unit
# (homeai-docker-user-fw.service, installed below) that re-inserts the same
# rule idempotently every boot, ordered `After=docker.service` (Docker
# recreates the DOCKER-USER chain from scratch on every dockerd start,
# wiping anything inserted into it previously — this unit's ordering is what
# makes it actually work, not just "eventually run at boot"). No conflicting
# package, no dependency on ufw's own presence at all.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

if [[ "${EUID}" -ne 0 ]]; then
  echo "error: must be run with sudo" >&2
  exit 1
fi

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
  echo "warning: ${ENV_FILE} not found — using default LAN_SUBNET=${LAN_SUBNET}. Copy .env.example to .env and set your real subnet first." >&2
fi

# Defensive: if a previous run of this script (before the M6-01 fix)
# installed iptables-persistent/netfilter-persistent, they need to come out
# BEFORE ufw can be (re)installed, since ufw's own package metadata declares
# `Breaks:` against both — apt will otherwise refuse (or silently remove
# ufw again) rather than let both coexist.
for pkg in iptables-persistent netfilter-persistent; do
  if dpkg -s "$pkg" >/dev/null 2>&1; then
    echo "Removing ${pkg} (conflicts with ufw's own package metadata — see this script's header)..."
    DEBIAN_FRONTEND=noninteractive apt-get purge -y "$pkg"
  fi
done

if ! command -v ufw >/dev/null 2>&1; then
  echo "Installing ufw..."
  apt-get update -qq
  apt-get install -y ufw
fi

echo "Allowing tcp/80 and tcp/443 from ${LAN_SUBNET}..."
ufw allow from "${LAN_SUBNET}" to any port 80 proto tcp
ufw allow from "${LAN_SUBNET}" to any port 443 proto tcp

if ufw app list 2>/dev/null | grep -qx "OpenSSH"; then
  ufw allow OpenSSH
  echo "Allowed OpenSSH (openssh-server detected)."
else
  echo "note: 'OpenSSH' ufw app profile not found (openssh-server not installed) — skipping SSH rule. If you install openssh-server later, run this script again or: sudo ufw allow OpenSSH" >&2
fi

ufw default deny incoming
ufw default allow outgoing
ufw --force enable

# --- DOCKER-USER chain: the actual enforcement point for published container ports ---

DEFAULT_IFACE="$(ip route show default 2>/dev/null | awk '/^default/ {print $5; exit}')"

if [[ -z "${DEFAULT_IFACE}" ]]; then
  echo "warning: could not auto-detect default-route interface; skipping DOCKER-USER rule. Add it manually, see docs/NETWORKING.md." >&2
else
  echo "Default-route interface: ${DEFAULT_IFACE}"

  # Ensure the DOCKER-USER chain exists (Docker creates it, but be defensive
  # in case this runs before Docker's first start).
  iptables -N DOCKER-USER 2>/dev/null || true

  # One DROP rule per published caddy port (80 + 443). Same shape, same
  # interface/subnet, so a non-LAN source is dropped before Docker's own
  # published-port NAT for either listener.
  ensure_docker_user_drop() {
    local port="$1"
    local check_spec insert_spec
    check_spec=(-C DOCKER-USER -i "${DEFAULT_IFACE}" '!' -s "${LAN_SUBNET}" -p tcp --dport "${port}" -j DROP)
    insert_spec=(-I DOCKER-USER -i "${DEFAULT_IFACE}" '!' -s "${LAN_SUBNET}" -p tcp --dport "${port}" -j DROP)
    if iptables "${check_spec[@]}" 2>/dev/null; then
      echo "DOCKER-USER tcp/${port} rule already present."
    else
      iptables "${insert_spec[@]}"
      echo "Added DOCKER-USER rule: drop tcp/${port} on ${DEFAULT_IFACE} not from ${LAN_SUBNET}."
    fi
  }
  ensure_docker_user_drop 80
  ensure_docker_user_drop 443

  # Install/refresh the boot-persistence unit (see header comment for why
  # this replaces iptables-persistent). Regenerated unconditionally every
  # run so a changed DEFAULT_IFACE/LAN_SUBNET (e.g. Wi-Fi -> Ethernet, or a
  # different .env) is always reflected, not just on first install.
  UNIT_PATH="/etc/systemd/system/homeai-docker-user-fw.service"
  RULE_CHECK_80="iptables -C DOCKER-USER -i ${DEFAULT_IFACE} ! -s ${LAN_SUBNET} -p tcp --dport 80 -j DROP"
  RULE_INSERT_80="iptables -I DOCKER-USER -i ${DEFAULT_IFACE} ! -s ${LAN_SUBNET} -p tcp --dport 80 -j DROP"
  RULE_CHECK_443="iptables -C DOCKER-USER -i ${DEFAULT_IFACE} ! -s ${LAN_SUBNET} -p tcp --dport 443 -j DROP"
  RULE_INSERT_443="iptables -I DOCKER-USER -i ${DEFAULT_IFACE} ! -s ${LAN_SUBNET} -p tcp --dport 443 -j DROP"
  cat >"${UNIT_PATH}" <<EOF
[Unit]
Description=Home AI Agent - restore DOCKER-USER LAN-only rules for tcp/80 and tcp/443 (see infra/host/setup-ufw.sh)
After=docker.service
Requires=docker.service
PartOf=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
# Idempotent: the -C existence check comes first so re-running (or a
# service restart) never inserts a duplicate rule.
ExecStart=/bin/sh -c '${RULE_CHECK_80} 2>/dev/null || ${RULE_INSERT_80}; ${RULE_CHECK_443} 2>/dev/null || ${RULE_INSERT_443}'

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now homeai-docker-user-fw.service
  echo "Installed/refreshed homeai-docker-user-fw.service (re-applies the DOCKER-USER rules after every boot, once docker.service starts)."
fi

echo "=== setup-ufw.sh summary ==="
ufw status verbose
