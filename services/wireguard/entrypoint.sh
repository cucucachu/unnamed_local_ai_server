#!/bin/sh
# Apply the platform-written wg0.conf, proxy HTTP/HTTPS from the tunnel to
# Caddy, and answer DNS for homeai.local on the tunnel gateway.
#
# Requires the host kernel's wireguard module (this container is NET_ADMIN
# only — it does not load SYS_MODULE). Missing module is a hard failure.
set -eu

CONF_SRC="${WG_CONF:-/data/wireguard/wg0.conf}"
CONF_DST=/etc/wireguard/wg0.conf
GATEWAY="${WG_GATEWAY:-10.13.13.1}"
CADDY_HOST="${WG_CADDY_HOST:-caddy}"

DNSMASQ_PID=""
SOCAT80_PID=""
SOCAT443_PID=""

log() { echo "wireguard: $*"; }

fail() {
  echo "wireguard: FAIL: $*" >&2
  exit 1
}

alive() {
  [ -n "$1" ] && kill -0 "$1" 2>/dev/null
}

require_module() {
  if [ -d /sys/module/wireguard ]; then
    return 0
  fi
  if ip link add wg-homeai-probe type wireguard 2>/dev/null; then
    ip link delete wg-homeai-probe 2>/dev/null || true
    return 0
  fi
  fail "wireguard kernel module is not loaded on the host. Run: sudo modprobe wireguard"
}

wait_for_conf() {
  i=0
  while [ ! -s "$CONF_SRC" ]; do
    i=$((i + 1))
    if [ "$i" -gt 90 ]; then
      fail "$CONF_SRC not written by platform after 90s"
    fi
    sleep 1
  done
}

apply_conf() {
  cp "$CONF_SRC" "$CONF_DST"
  chmod 600 "$CONF_DST"
  if ip link show wg0 >/dev/null 2>&1; then
    wg-quick strip wg0 > /tmp/wg0.strip
    wg syncconf wg0 /tmp/wg0.strip
    rm -f /tmp/wg0.strip
    log "synced wg0 from platform config"
  else
    wg-quick up wg0
    log "brought up wg0"
  fi
}

ensure_helpers() {
  if ! alive "$DNSMASQ_PID"; then
    # -d/--no-daemon: alpine's --keep-in-foreground still exits 5 without it.
    dnsmasq --conf-file=/dev/null --pid-file=/run/dnsmasq.pid \
      --user=root --no-daemon \
      --listen-address="$GATEWAY" --bind-interfaces \
      --address=/homeai.local/"$GATEWAY" \
      --no-resolv --no-hosts --port=53 &
    DNSMASQ_PID=$!
    log "started dnsmasq pid=${DNSMASQ_PID} on ${GATEWAY}:53"
  fi
  if ! alive "$SOCAT80_PID"; then
    socat TCP-LISTEN:80,bind="$GATEWAY",fork,reuseaddr TCP:"${CADDY_HOST}":80 &
    SOCAT80_PID=$!
  fi
  if ! alive "$SOCAT443_PID"; then
    socat TCP-LISTEN:443,bind="$GATEWAY",fork,reuseaddr TCP:"${CADDY_HOST}":443 &
    SOCAT443_PID=$!
  fi
}

checksum() {
  sha256sum "$CONF_SRC" | awk '{print $1}'
}

require_module
wait_for_conf
apply_conf
ensure_helpers
log "dns+proxy on ${GATEWAY} (homeai.local -> ${GATEWAY}, :80/:443 -> ${CADDY_HOST})"

last="$(checksum)"
while true; do
  sleep 2
  if [ -s "$CONF_SRC" ]; then
    now="$(checksum)"
    if [ "$now" != "$last" ]; then
      apply_conf
      last="$now"
    fi
  fi
  ensure_helpers
done
