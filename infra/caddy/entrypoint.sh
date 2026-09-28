#!/bin/sh
# Generate the optional ACME DNS-01 site block from HOMEAI_DOMAIN, then
# exec Caddy. https://homeai.local { tls internal } and :80 always stay
# in the baked Caddyfile; this only writes /etc/caddy/domain.caddy.
#
# Also writes /etc/caddy/via.caddy: after Docker DNS for `wireguard` is
# up, match that container's IP and set X-HomeAI-Via: vpn (client copies
# were already stripped in the Caddyfile). The DB `public_https` flag is
# the source of truth for passkey-only — this header is only the VPN
# classifier hook for docker-bridge SNAT (M15-05).
#
# Never log DUCKDNS_TOKEN (or any other secret). The generated snippet
# references {env.DUCKDNS_TOKEN} so the token is not written to disk.
set -eu

DOMAIN_SNIPPET=/etc/caddy/domain.caddy
VIA_SNIPPET=/etc/caddy/via.caddy
CADDYFILE=/etc/caddy/Caddyfile

# shellcheck disable=SC1091
. /usr/local/lib/homeai-valid-hostname.sh

log() { echo "caddy: $*"; }

fail() {
  echo "caddy: FAIL: $*" >&2
  exit 1
}

valid_ipv4() {
  ip=$1
  oldifs=$IFS
  IFS=.
  # shellcheck disable=SC2086
  set -- $ip
  IFS=$oldifs
  [ $# -eq 4 ] || return 1
  for o; do
    case $o in
      ''|*[!0-9]*) return 1 ;;
    esac
    [ "$o" -ge 0 ] && [ "$o" -le 255 ] || return 1
  done
  return 0
}

write_via_unset() {
  printf '%s\n' '# wireguard via unset' > "$VIA_SNIPPET"
}

write_via_vpn() {
  ip=$1
  # IP was validated; safe to interpolate as a Caddy remote_ip matcher.
  cat > "$VIA_SNIPPET" <<EOF
@from_wireguard remote_ip ${ip}
request_header @from_wireguard X-HomeAI-Via vpn
EOF
}

# Resolve the compose service name to an IPv4 and rewrite via.caddy.
# Caddy starts before wireguard; we poll and reload. Never interpolates
# a non-IPv4 string into the Caddyfile.
watch_wireguard_via() {
  last=""
  loaded=""
  waits=0
  while true; do
    ip="$(getent hosts wireguard 2>/dev/null | awk '$1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print $1; exit }')" || ip=""
    if [ -n "$ip" ] && valid_ipv4 "$ip"; then
      if [ "$ip" != "$last" ]; then
        write_via_vpn "$ip"
        last=$ip
        loaded=""
      fi
      if [ -z "$loaded" ]; then
        if caddy reload --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1; then
          loaded=1
          log "wireguard via: X-HomeAI-Via=vpn for remote_ip ${ip}"
        fi
      fi
      sleep 10
      continue
    fi
    waits=$((waits + 1))
    if [ "$waits" -gt 90 ]; then
      log "wireguard via: DNS for wireguard never resolved; VPN HTTP may look public while public_https is on"
      return
    fi
    sleep 2
  done
}

# Empty snippet when domain mode is off — Caddyfile `import`s this path
# unconditionally (an empty site address would be invalid). A comment
# keeps the import from warning "Import file is empty".
printf '%s\n' '# domain mode off' > "$DOMAIN_SNIPPET"
write_via_unset

domain="${HOMEAI_DOMAIN:-}"
if [ -n "$domain" ]; then
  if ! valid_hostname "$domain"; then
    fail "HOMEAI_DOMAIN is not a valid hostname (no spaces, quotes, or newlines); not interpolating into the Caddyfile"
  fi
  if [ -z "${DUCKDNS_TOKEN:-}" ]; then
    fail "HOMEAI_DOMAIN is set but DUCKDNS_TOKEN is empty; refusing to start (DNS-01 cannot obtain a cert; will not silently serve HTTP on that name)"
  fi
  # Hostname charset is restricted above; safe to interpolate as the site
  # address. Token stays in the environment, not in this file.
  # HSTS only on this HTTPS site (not :80, not homeai.local). auto_https
  # disable_redirects stays — this header does not force HTTP→HTTPS.
  cat > "$DOMAIN_SNIPPET" <<EOF
https://${domain} {
	tls {
		dns duckdns {env.DUCKDNS_TOKEN}
	}
	header Strict-Transport-Security "max-age=31536000"
	import app
}
EOF
  log "domain mode: https://${domain} (ACME DNS-01 via DuckDNS; token not logged)"
fi

should_watch_via() {
  # Default CMD is `caddy run`. verify_caddy_domain.sh calls
  # `caddy validate` — do not background a reloader then.
  if [ "$#" -eq 0 ]; then
    return 0
  fi
  [ "${1:-}" = "caddy" ] && [ "${2:-}" = "run" ]
}

if should_watch_via "$@"; then
  watch_wireguard_via &
fi

if [ "$#" -eq 0 ]; then
  exec caddy run --config "$CADDYFILE" --adapter caddyfile
fi
exec "$@"
