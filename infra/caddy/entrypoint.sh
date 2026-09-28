#!/bin/sh
# Generate the optional ACME DNS-01 site block from HOMEAI_DOMAIN, then
# exec Caddy. https://homeai.local { tls internal } and :80 always stay
# in the baked Caddyfile; this only writes /etc/caddy/domain.caddy.
#
# Never log DUCKDNS_TOKEN (or any other secret). The generated snippet
# references {env.DUCKDNS_TOKEN} so the token is not written to disk.
set -eu

DOMAIN_SNIPPET=/etc/caddy/domain.caddy

# shellcheck disable=SC1091
. /usr/local/lib/homeai-valid-hostname.sh

log() { echo "caddy: $*"; }

fail() {
  echo "caddy: FAIL: $*" >&2
  exit 1
}

# Empty snippet when domain mode is off — Caddyfile `import`s this path
# unconditionally (an empty site address would be invalid). A comment
# keeps the import from warning "Import file is empty".
printf '%s\n' '# domain mode off' > "$DOMAIN_SNIPPET"

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
  cat > "$DOMAIN_SNIPPET" <<EOF
https://${domain} {
	tls {
		dns duckdns {env.DUCKDNS_TOKEN}
	}
	import app
}
EOF
  log "domain mode: https://${domain} (ACME DNS-01 via DuckDNS; token not logged)"
fi

if [ "$#" -eq 0 ]; then
  exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
fi
exec "$@"
