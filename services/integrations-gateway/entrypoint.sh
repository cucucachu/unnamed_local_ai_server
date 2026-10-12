#!/usr/bin/env sh
# Trust egress-proxy's MITM CA as well as the system bundle, the same as
# web-fetch's entrypoint.sh (docs/ARCHITECTURE.md §5, "CA handling"): the
# CA only exists once egress-proxy has started, so this waits for it and
# fails loud rather than starting with no way to reach any server.
set -eu

SYSTEM_BUNDLE="/etc/ssl/certs/ca-certificates.crt"
MITM_CERT="/ca/mitmproxy-ca-cert.pem"
COMBINED_BUNDLE="/tmp/ca-bundle.pem"

attempt=0
max_attempts=30
while [ ! -f "$MITM_CERT" ]; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "entrypoint.sh: ${MITM_CERT} not found after ${max_attempts}s - has egress-proxy started at least once?" >&2
    exit 1
  fi
  echo "entrypoint.sh: waiting for ${MITM_CERT} (attempt ${attempt}/${max_attempts})..." >&2
  sleep 1
done

cat "$SYSTEM_BUNDLE" "$MITM_CERT" > "$COMBINED_BUNDLE"

export SSL_CERT_FILE="$COMBINED_BUNDLE"
export CA_BUNDLE="$COMBINED_BUNDLE"

exec "$@"
