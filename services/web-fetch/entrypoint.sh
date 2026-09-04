#!/usr/bin/env sh
# M7-03: builds a combined CA bundle (Debian's system bundle + egress-proxy's
# own MITM CA cert) before handing off to the real CMD, so this service's
# httpx client trusts the leaf certs `egress-proxy` (M7-02) generates when it
# terminates TLS - see docs/ARCHITECTURE.md §5's "CA handling" recipe, which
# this follows exactly: trust `mitmproxy-ca-cert.pem` specifically, never the
# private-key-bundling `mitmproxy-ca.pem`.
#
# Runs as the entrypoint (not baked into the Dockerfile's own CMD directly)
# because the CA file is only available at *container start* time, not
# build time - it's mitmproxy's own generated artifact, written into the
# `egress-proxy-ca` named volume (mounted here read-only at /ca) the first
# time the `egress-proxy` container itself starts, not baked into any image.
set -eu

SYSTEM_BUNDLE="/etc/ssl/certs/ca-certificates.crt"
MITM_CERT="/ca/mitmproxy-ca-cert.pem"
COMBINED_BUNDLE="/tmp/ca-bundle.pem"

# The CA volume is populated LAZILY (docs/ARCHITECTURE.md §5, point 4): if
# `egress-proxy` has never started even once, /ca/mitmproxy-ca-cert.pem
# won't exist yet. Retry with a short poll (30s total, matching the kind of
# window `docker compose up -d` gives services to come up together) rather
# than failing on the very first missed race with a freshly-created stack -
# but fail loud (nonzero exit, container restarts per `restart:
# unless-stopped` and tries again) rather than silently starting up with no
# CA trust at all, which would make every single fetch fail anyway with a
# much more confusing SSL error instead of an obvious startup failure.
attempt=0
max_attempts=30
while [ ! -f "$MITM_CERT" ]; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "entrypoint.sh: ${MITM_CERT} not found after ${max_attempts}s - has egress-proxy started at least once? (docs/ARCHITECTURE.md §5, CA handling point 4)" >&2
    exit 1
  fi
  echo "entrypoint.sh: waiting for ${MITM_CERT} (egress-proxy hasn't written its CA yet, attempt ${attempt}/${max_attempts})..." >&2
  sleep 1
done

cat "$SYSTEM_BUNDLE" "$MITM_CERT" > "$COMBINED_BUNDLE"

export SSL_CERT_FILE="$COMBINED_BUNDLE"
export REQUESTS_CA_BUNDLE="$COMBINED_BUNDLE"

exec "$@"
