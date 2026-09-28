#!/usr/bin/env bash
# M15-01: live WireGuard peer connects from a throwaway container and
# reaches the UI *only* through the tunnel.
#
# Needs the live stack (caddy + platform + the new wireguard sidecar).
# Never completes bootstrap. Never recreates model-runner or postgres.
# Throwaway user `e2e-wg-*`. Takes `/tmp/homeai-stack.lock` if the caller
# hasn't.
#
# The kernel's `wireguard` module must be loaded on the host
# (`sudo modprobe wireguard`). Missing module is a hard FAIL, not a skip.
# This script does not change ufw/router. Handshake is a throwaway L2 to
# the sidecar (sibling-bridge UDP hairpin does not return WG replies);
# LAN phones still use host UDP 51820 via `homeai-wg`.
#
# Usage: scripts/e2e/wireguard_smoke.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

# Worktrees don't get the gitignored .env; compose still needs it. Never cat it.
if [ ! -f .env ] && [ -f /home/cody/code/unnamed_local_ai_server/.env ]; then
  ln -s /home/cody/code/unnamed_local_ai_server/.env .env
fi

# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"

CLIENT_NAME="e2e-wg-client-$$"
NET_NAME="e2e-wg-iso-$$"
CFG_DIR="$(mktemp -d -t e2e-wg-cfg.XXXXXX)"
PEER_ID=""
WG_CID=""

log() { echo "[wireguard-smoke] $(date '+%H:%M:%S') $*"; }

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

require_module() {
  if [ -d /sys/module/wireguard ]; then
    log "kernel module wireguard is loaded"
    return 0
  fi
  if ip link add wg-e2e-probe type wireguard 2>/dev/null; then
    ip link delete wg-e2e-probe 2>/dev/null || true
    log "kernel supports wireguard (interface probe)"
    return 0
  fi
  fail "wireguard kernel module is not loaded. Run: sudo modprobe wireguard (do not skip)"
}

lan_ipv4() {
  local iface addr
  iface="$(ip route show default 2>/dev/null | awk '/^default/ {print $5; exit}')"
  if [ -n "$iface" ]; then
    addr="$(ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1)"
    if [ -n "$addr" ]; then
      printf '%s' "$addr"
      return 0
    fi
  fi
  hostname -I 2>/dev/null | awk '{print $1}'
}

cleanup() {
  docker rm -f "$CLIENT_NAME" >/dev/null 2>&1 || true
  if [ -n "$WG_CID" ]; then
    docker network disconnect "$NET_NAME" "$WG_CID" >/dev/null 2>&1 || true
  fi
  docker network rm "$NET_NAME" >/dev/null 2>&1 || true
  if [ -n "$PEER_ID" ]; then
    python3 - "$E2E_BASE" "${E2E_AUTH_COOKIE:-}" "$PEER_ID" <<'PY' >/dev/null 2>&1 || true
import sys, urllib.request
base, cookie, peer = sys.argv[1:4]
req = urllib.request.Request(
    f"{base}/api/platform/me/wireguard-devices/{peer}",
    method="DELETE",
    headers={"Cookie": cookie} if cookie else {},
)
try:
    urllib.request.urlopen(req, timeout=10)
except Exception:
    pass
PY
  fi
  rm -rf "$CFG_DIR"
  e2e_auth_end
}
trap cleanup EXIT

require_module

log "building platform + wireguard (no-deps; not model-runner/postgres)"
docker compose up -d --build --no-deps platform wireguard

log "waiting for platform health"
for _ in $(seq 1 30); do
  if docker compose exec -T platform python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8100/internal/health', timeout=3)" \
    >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
docker compose exec -T platform python -c \
  "import urllib.request; urllib.request.urlopen('http://localhost:8100/internal/health', timeout=3)" \
  >/dev/null || fail "platform not healthy"

log "waiting for wireguard container"
for _ in $(seq 1 30); do
  if docker compose ps --status running --format '{{.Name}}' 2>/dev/null | grep -q wireguard; then
    break
  fi
  sleep 2
done
docker compose ps --status running --format '{{.Name}}' | grep -q wireguard \
  || fail "wireguard sidecar is not running (see docker compose logs wireguard)"

WG_IMAGE="$(docker inspect -f '{{.Image}}' "$(docker compose ps -q wireguard)")"
[ -n "$WG_IMAGE" ] || fail "could not resolve wireguard image id"

e2e_auth_begin wg
LAN_IP="$(lan_ipv4)"
[ -n "$LAN_IP" ] || fail "could not determine host LAN IPv4 for WireGuard Endpoint"

log "creating peer for ${E2E_AUTH_USER} (config written to a temp file, not printed)"
PEER_META="$(python3 - "$E2E_BASE" "$E2E_AUTH_COOKIE" "$CFG_DIR/wg.conf" <<'PY'
import json, sys, urllib.error, urllib.request

base, cookie, path = sys.argv[1:4]
body = json.dumps({"name": "e2e-wg-client"}).encode()
req = urllib.request.Request(
    f"{base}/api/platform/me/wireguard-devices",
    data=body,
    method="POST",
    headers={"Content-Type": "application/json", "Cookie": cookie},
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())
except urllib.error.HTTPError as e:
    raise SystemExit(f"create peer: HTTP {e.code} {e.read().decode()}") from e
config = data["config"]
# Don't print PrivateKey. Endpoint is rewritten later to the sidecar's
# address on the throwaway L2 (sibling-bridge UDP hairpin is unreliable).
open(path, "w").write(config if config.endswith("\n") else config + "\n")
print(f"{data['id']} {data['address']}")
PY
)"
PEER_ID="${PEER_META%% *}"
PEER_ADDR="${PEER_META#* }"
[[ "$PEER_ID" =~ ^[0-9a-f-]{36}$ ]] || fail "create peer: bad id in '$PEER_META'"
log "peer id=${PEER_ID} address=${PEER_ADDR}"

log "waiting for sidecar to load the peer"
for _ in $(seq 1 15); do
  if docker compose exec -T wireguard wg show 2>/dev/null | grep -q '^peer:'; then
    log "sidecar has the peer"
    break
  fi
  sleep 1
done
docker compose exec -T wireguard wg show 2>/dev/null | grep -q '^peer:' \
  || fail "wireguard sidecar did not load the new peer (see docker compose logs wireguard)"

# Isolated L2 to the sidecar (not homeai-internal, so no Docker DNS to
# caddy). Sibling-bridge hairpin to published UDP 51820 does not return
# WG replies; LAN phones still use host:51820 via homeai-wg. iptables
# drops TCP 80/443 except on wg0 so the UI cannot be fetched off-tunnel.
WG_CID="$(docker compose ps -q wireguard)"
[ -n "$WG_CID" ] || fail "could not resolve wireguard container id"
docker network create "$NET_NAME" >/dev/null
docker network connect "$NET_NAME" "$WG_CID"
WG_IP="$(docker inspect "$WG_CID" --format='{{json .NetworkSettings.Networks}}' \
  | python3 -c "import json,sys; print(json.load(sys.stdin)[sys.argv[1]]['IPAddress'])" "$NET_NAME")"
[ -n "$WG_IP" ] || fail "wireguard has no address on ${NET_NAME}"
python3 - "$CFG_DIR/wg.conf" "$WG_IP" <<'PY'
import sys
path, endpoint_ip = sys.argv[1:3]
lines = []
for line in open(path):
    if line.startswith("Endpoint ="):
        lines.append(f"Endpoint = {endpoint_ip}:51820\n")
    else:
        lines.append(line)
open(path, "w").writelines(lines)
PY
log "client network ${NET_NAME} Endpoint ${WG_IP}:51820 (TCP 80/443 dropped except via wg0)"

# The client image is the sidecar image (wireguard-tools) with a different
# entrypoint. Isolated bridge: handshake to the sidecar on this L2, cannot
# Docker-DNS to caddy. iptables drops LAN :80/:443 so the UI is only
# reachable via 10.13.13.1.
set +e
CLIENT_OUT="$(docker run --rm --name "$CLIENT_NAME" \
  --cap-add NET_ADMIN \
  --sysctl net.ipv4.conf.all.src_valid_mark=1 \
  --network "$NET_NAME" \
  --entrypoint /bin/sh \
  -v "$CFG_DIR:/cfg:ro" \
  -e LAN_IP="$LAN_IP" \
  "$WG_IMAGE" -c '
set -e
iptables -A OUTPUT -o wg0 -j ACCEPT
iptables -A OUTPUT -p udp --dport 51820 -j ACCEPT
iptables -A OUTPUT -p udp --sport 51820 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 80 -j DROP
iptables -A OUTPUT -p tcp --dport 443 -j DROP

echo "== before tunnel: 10.13.13.1 must fail"
if wget --connect-timeout=3 --timeout=3 --tries=1 -q -O /dev/null http://10.13.13.1/ ; then
  echo "FAIL: 10.13.13.1 reachable before WireGuard is up"
  exit 1
fi
echo "ok   10.13.13.1 unreachable without tunnel"

echo "== before tunnel: LAN :80 must be dropped"
if wget --connect-timeout=3 --timeout=3 --tries=1 -q -O /dev/null "http://${LAN_IP}/" ; then
  echo "FAIL: LAN :80 reachable from client without tunnel (iptables should drop it)"
  exit 1
fi
echo "ok   LAN :80 dropped on eth0"

cp /cfg/wg.conf /etc/wireguard/wg0.conf
chmod 600 /etc/wireguard/wg0.conf
wg-quick up wg0
echo "ok   wg-quick up"

# Docker bind-mounts resolv.conf; wg-quick resolvconf is a no-op in this
# image. Replace it so homeai.local is resolved by the tunnel DNS.
umount /etc/resolv.conf 2>/dev/null || true
printf "nameserver 10.13.13.1\noptions ndots:0\n" > /etc/resolv.conf

echo "== via tunnel: http://10.13.13.1/"
wget --inet4-only --connect-timeout=8 --timeout=15 --tries=1 -q -O /tmp/ui.html http://10.13.13.1/ || {
  echo "FAIL: wget http://10.13.13.1/ via tunnel"
  wg show || true
  exit 1
}
grep -qiE "<html|script" /tmp/ui.html || {
  echo "FAIL: tunnel response did not look like the UI"
  exit 1
}
echo "ok   UI via 10.13.13.1"

echo "== via tunnel: http://homeai.local/ (DNS 10.13.13.1)"
wget --inet4-only --connect-timeout=8 --timeout=15 --tries=1 -q -O /tmp/ui2.html http://homeai.local/ || {
  echo "FAIL: wget http://homeai.local/ via tunnel DNS"
  echo "resolv.conf:"; cat /etc/resolv.conf || true
  wget --inet4-only --connect-timeout=3 --timeout=5 --tries=1 -q -O /dev/null http://10.13.13.1/ && echo "10.13.13.1 still ok"
  exit 1
}
grep -qiE "<html|script" /tmp/ui2.html || {
  echo "FAIL: homeai.local response did not look like the UI"
  exit 1
}
echo "ok   UI via homeai.local"
' 2>&1)"
CLIENT_RC=$?
set -e
echo "$CLIENT_OUT"
[ "$CLIENT_RC" -eq 0 ] || fail "client container failed (exit ${CLIENT_RC})"

log "revoking peer"
python3 - "$E2E_BASE" "$E2E_AUTH_COOKIE" "$PEER_ID" <<'PY'
import sys, urllib.error, urllib.request
base, cookie, peer = sys.argv[1:4]
req = urllib.request.Request(
    f"{base}/api/platform/me/wireguard-devices/{peer}",
    method="DELETE",
    headers={"Cookie": cookie},
)
try:
    urllib.request.urlopen(req, timeout=15)
except urllib.error.HTTPError as e:
    if e.code != 204:
        raise SystemExit(f"revoke: HTTP {e.code} {e.read().decode()}") from e
print("ok   revoked peer")
PY
PEER_ID=""

log "PASS"
