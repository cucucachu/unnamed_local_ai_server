#!/usr/bin/env bash
# verify_caddy_domain.sh — M15-03 Tier A: optional domain + ACME DNS-01
# Caddyfile generation, without obtaining a real certificate and without
# recreating the live caddy container.
#
# Does **not** `compose up`. Does **not** set HOMEAI_DOMAIN on the live
# stack. Does **not** contact Let's Encrypt or DuckDNS. Dummy token is
# inlined here — never read from live `.env`.
#
# Checks:
#   1. `docker compose -p homeai ... config -q` without HOMEAI_DOMAIN
#      (vars omitted entirely; not required for the default stack).
#   2. Same with HOMEAI_DOMAIN=example.duckdns.org and a dummy token
#      in the environment (not from live `.env`).
#   3. Caddyfile validate, no-domain: stock `caddy:2-alpine`
#      (`tls internal` + `:80`).
#   4. Domain-on generated snippet: plugin-enabled binary
#      (`docker build --target caddy-dns`), then `caddy validate`.
#   5. Empty DUCKDNS_TOKEN + non-empty domain is a hard entrypoint
#      failure (does not silently serve HTTP).
#   6. Hostname validation rejects spaces/quotes/newlines before any
#      Caddyfile interpolation.
#
# Usage: scripts/verify_caddy_domain.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$REPO_ROOT"

# shellcheck disable=SC1091
source "${REPO_ROOT}/infra/lib/valid_hostname.sh"

RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
NC=$'\033[0m'

PASS=0
FAIL=0
# Distinct tag so this build does not retag/replace the live homeai-caddy image.
CADDY_DNS_IMAGE="${CADDY_DNS_IMAGE:-homeai-m15-03-caddy-dns}"
DUMMY_DOMAIN="example.duckdns.org"
# Deliberately fake; must never come from live .env.
DUMMY_TOKEN="dummy-token-not-real"

log() { echo "[verify-caddy-domain] $*"; }

pass() {
  echo "${GREEN}PASS${NC}: $*"
  PASS=$((PASS + 1))
}

fail() {
  echo "${RED}FAIL${NC}: $*" >&2
  FAIL=$((FAIL + 1))
}

TMPDIR="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR"; }
trap cleanup EXIT

# Dummy interpolation env — not live `.env`, not `/srv/homeai/*`.
write_dummy_env() {
  local dest="$1"
  cat > "$dest" <<'EOF'
HOMEAI_UID=1000
HOMEAI_GID=1000
FILES_DIR=/tmp/homeai-verify-caddy-domain/files
SPACES_DIR=/tmp/homeai-verify-caddy-domain/spaces
MODEL_FILE=x.gguf
MODEL_CTX_SIZE=1
MODEL_EXTRA_ARGS=
MODEL_NAME=x
RENDER_GID=989
VIDEO_GID=44
POSTGRES_USER=homeai
POSTGRES_PASSWORD=dummy
POSTGRES_DB=homeai
AGENT_DB_PASSWORD=dummy
PLATFORM_DB_PASSWORD=dummy
PLATFORM_AGENT_TOKEN=dummy
PLATFORM_EXEC_TOKEN=dummy
PLATFORM_BUILD_TOKEN=dummy
MODEL_BASE_URL=http://model-runner:8080/v1
EXEC_MANAGER_URL=http://code-exec-manager:8090
WEB_FETCH_URL=http://web-fetch:8000
WEB_FETCH_TOOL_MAX_CHARS=1
EXEC_IDLE_MINUTES=1
EXEC_DEFAULT_TIMEOUT_S=1
EGRESS_MAX_BYTES=1
EGRESS_PROXY_URL=http://egress-proxy:8080
FETCH_TIMEOUT_S=1
FETCH_MAX_BYTES=1
FETCH_MAX_TEXT_CHARS=1
FETCH_MAX_REDIRECTS=1
SEARXNG_URL=http://searxng:8080
SEARXNG_SECRET=dummy
EOF
}

compose_config_q() {
  local envfile="$1"
  # Unset live domain/token so a stray shell export cannot leak in.
  # --env-file is the dummy file above, never the host `.env`.
  env -u HOMEAI_DOMAIN -u DUCKDNS_TOKEN \
    docker compose -p homeai \
      --project-directory "$REPO_ROOT" \
      -f "$REPO_ROOT/docker-compose.yml" \
      --env-file "$envfile" \
      config -q
}

# --- 1. compose config without domain ---------------------------------------
ENV_OFF="${TMPDIR}/env.off"
write_dummy_env "$ENV_OFF"
if compose_config_q "$ENV_OFF"; then
  pass "compose config -q without HOMEAI_DOMAIN / DUCKDNS_TOKEN"
else
  fail "compose config -q without HOMEAI_DOMAIN / DUCKDNS_TOKEN"
fi

# --- 2. compose config with dummy domain + token ----------------------------
ENV_ON="${TMPDIR}/env.on"
write_dummy_env "$ENV_ON"
{
  echo "HOMEAI_DOMAIN=${DUMMY_DOMAIN}"
  echo "DUCKDNS_TOKEN=${DUMMY_TOKEN}"
} >> "$ENV_ON"
if compose_config_q "$ENV_ON"; then
  pass "compose config -q with dummy HOMEAI_DOMAIN + DUCKDNS_TOKEN (not from live .env)"
else
  fail "compose config -q with dummy HOMEAI_DOMAIN + DUCKDNS_TOKEN"
fi

# --- 3. stock caddy: no-domain Caddyfile ------------------------------------
NODIR="${TMPDIR}/caddy-off"
mkdir -p "$NODIR"
cp "${REPO_ROOT}/infra/caddy/Caddyfile" "$NODIR/Caddyfile"
printf '%s\n' '# domain mode off' > "$NODIR/domain.caddy"
printf '%s\n' '# wireguard via unset' > "$NODIR/via.caddy"
if docker run --rm \
    -v "${NODIR}/Caddyfile:/etc/caddy/Caddyfile:ro" \
    -v "${NODIR}/domain.caddy:/etc/caddy/domain.caddy:ro" \
    -v "${NODIR}/via.caddy:/etc/caddy/via.caddy:ro" \
    caddy:2-alpine \
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
  pass "caddy validate (stock caddy:2-alpine) for no-domain Caddyfile"
else
  fail "caddy validate (stock caddy:2-alpine) for no-domain Caddyfile"
fi

# --- 4. plugin-enabled binary: domain-on snippet ----------------------------
log "building plugin-enabled caddy binary (${CADDY_DNS_IMAGE}, --target caddy-dns; not replacing live caddy)"
if docker build -f "${REPO_ROOT}/infra/caddy/Dockerfile" \
    --target caddy-dns \
    -t "${CADDY_DNS_IMAGE}" \
    "${REPO_ROOT}"; then
  pass "docker build --target caddy-dns (xcaddy + duckdns)"
else
  fail "docker build --target caddy-dns"
fi

ONDIR="${TMPDIR}/caddy-on"
mkdir -p "$ONDIR"
cp "${REPO_ROOT}/infra/caddy/Caddyfile" "$ONDIR/Caddyfile"
: > "$ONDIR/domain.caddy"
printf '%s\n' '# wireguard via unset' > "$ONDIR/via.caddy"

run_entrypoint() {
  # Args: extra docker run args after image, then command.
  docker run --rm \
    -v "${ONDIR}:/etc/caddy" \
    -v "${REPO_ROOT}/infra/caddy/entrypoint.sh:/entrypoint.sh:ro" \
    -v "${REPO_ROOT}/infra/lib/valid_hostname.sh:/usr/local/lib/homeai-valid-hostname.sh:ro" \
    --entrypoint /bin/sh \
    "$@"
}

if run_entrypoint \
    -e "HOMEAI_DOMAIN=${DUMMY_DOMAIN}" \
    -e "DUCKDNS_TOKEN=${DUMMY_TOKEN}" \
    "${CADDY_DNS_IMAGE}" \
    -c '/bin/sh /entrypoint.sh caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile'; then
  pass "caddy validate (plugin-enabled binary) for domain-on generated snippet"
else
  fail "caddy validate (plugin-enabled binary) for domain-on generated snippet"
fi

if grep -q "dns duckdns {env.DUCKDNS_TOKEN}" "$ONDIR/domain.caddy" \
    && grep -q "https://${DUMMY_DOMAIN}" "$ONDIR/domain.caddy" \
    && grep -q "Strict-Transport-Security" "$ONDIR/domain.caddy" \
    && ! grep -Fq "$DUMMY_TOKEN" "$ONDIR/domain.caddy"; then
  pass "generated snippet uses DNS-01 duckdns + env token placeholder (token not written to disk)"
else
  fail "generated snippet missing dns duckdns, missing site address, or leaked the dummy token"
fi

# --- 5. empty token must fail clearly ---------------------------------------
: > "$ONDIR/domain.caddy"
set +e
TOKEN_FAIL_OUT="$(run_entrypoint \
    -e "HOMEAI_DOMAIN=${DUMMY_DOMAIN}" \
    -e "DUCKDNS_TOKEN=" \
    "${CADDY_DNS_IMAGE}" \
    -c '/bin/sh /entrypoint.sh caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile' 2>&1)"
TOKEN_FAIL_RC=$?
set -e
if [ "$TOKEN_FAIL_RC" -ne 0 ] && echo "$TOKEN_FAIL_OUT" | grep -q 'DUCKDNS_TOKEN'; then
  pass "empty DUCKDNS_TOKEN + non-empty HOMEAI_DOMAIN fails clearly"
else
  fail "empty token did not fail clearly (rc=${TOKEN_FAIL_RC})"
fi

# --- 6. hostname validation (no interpolation of junk) ----------------------
hostname_ok() {
  if valid_hostname "$1" 2>/dev/null; then
    return 0
  fi
  return 1
}

if hostname_ok "$DUMMY_DOMAIN" && hostname_ok "my-box.example.com"; then
  pass "valid_hostname accepts FQDNs"
else
  fail "valid_hostname rejected a good FQDN"
fi

bad_names=(
  "bad name.com"
  "bad\"quote.com"
  "evil.example.com{tls"
  $'new\nline.com'
  "localhost"
  ""
)
bad_ok=1
for n in "${bad_names[@]}"; do
  if hostname_ok "$n"; then
    bad_ok=0
    fail "valid_hostname accepted invalid name: $(printf '%q' "$n")"
  fi
done
if [ "$bad_ok" -eq 1 ]; then
  pass "valid_hostname rejects spaces, quotes, newlines, metacharacters, single-label"
fi

: > "$ONDIR/domain.caddy"
set +e
HOST_FAIL_OUT="$(run_entrypoint \
    -e "HOMEAI_DOMAIN=bad name.com" \
    -e "DUCKDNS_TOKEN=${DUMMY_TOKEN}" \
    "${CADDY_DNS_IMAGE}" \
    -c '/bin/sh /entrypoint.sh caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile' 2>&1)"
HOST_FAIL_RC=$?
set -e
if [ "$HOST_FAIL_RC" -ne 0 ] && echo "$HOST_FAIL_OUT" | grep -qi 'hostname'; then
  if grep -q 'domain mode off' "$ONDIR/domain.caddy" && ! grep -q 'dns duckdns' "$ONDIR/domain.caddy"; then
    pass "invalid HOMEAI_DOMAIN fails before Caddyfile interpolation (snippet empty)"
  else
    fail "invalid hostname failed but snippet was still written"
  fi
else
  fail "invalid HOMEAI_DOMAIN did not fail before interpolation (rc=${HOST_FAIL_RC})"
fi

echo
log "${PASS} passed, ${FAIL} failed"
if [ "$FAIL" -ne 0 ]; then
  exit 1
fi
log "live caddy was not recreated; postgres/model-runner were not touched"
exit 0
