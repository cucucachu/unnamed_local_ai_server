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
# 80:80). Since caddy is the only service that publishes a port, the real
# enforcement for it is the DOCKER-USER chain, which Docker guarantees to
# consult before its own forwarding rules. See docs/NETWORKING.md.
#
# iptables rules added at the CLI don't survive a reboot by themselves, so this
# script also installs iptables-persistent/netfilter-persistent and saves the
# ruleset after adding the DOCKER-USER rule.

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

if ! command -v ufw >/dev/null 2>&1; then
  echo "Installing ufw..."
  apt-get update -qq
  apt-get install -y ufw
fi

echo "Allowing tcp/80 from ${LAN_SUBNET}..."
ufw allow from "${LAN_SUBNET}" to any port 80 proto tcp

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

  RULE_SPEC=(-I DOCKER-USER -i "${DEFAULT_IFACE}" '!' -s "${LAN_SUBNET}" -p tcp --dport 80 -j DROP)
  CHECK_SPEC=(-C DOCKER-USER -i "${DEFAULT_IFACE}" '!' -s "${LAN_SUBNET}" -p tcp --dport 80 -j DROP)

  if iptables "${CHECK_SPEC[@]}" 2>/dev/null; then
    echo "DOCKER-USER rule already present."
  else
    iptables "${RULE_SPEC[@]}"
    echo "Added DOCKER-USER rule: drop tcp/80 on ${DEFAULT_IFACE} not from ${LAN_SUBNET}."
  fi

  # Manually-added iptables rules are in-memory only and vanish on reboot.
  # Install netfilter-persistent (non-interactively — it would otherwise
  # prompt to save the current ruleset) so the rule above survives.
  if ! command -v netfilter-persistent >/dev/null 2>&1; then
    echo "Installing iptables-persistent for reboot-safe rules..."
    echo "iptables-persistent iptables-persistent/autosave_v4 boolean false" | debconf-set-selections
    echo "iptables-persistent iptables-persistent/autosave_v6 boolean false" | debconf-set-selections
    DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
  fi
  netfilter-persistent save
  echo "Saved iptables ruleset (survives reboot)."
fi

echo "=== setup-ufw.sh summary ==="
ufw status verbose
