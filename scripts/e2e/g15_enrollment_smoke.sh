#!/usr/bin/env bash
# M15-07 (GATE G15) scenario: public origin refuses enrollment, through
# platform:8100 (not Caddy).
#
# Caddy overwrites client `X-Forwarded-For` (no `trusted_proxies`), so a
# spoofed WAN address through Caddy cannot prove public origin — that is
# `origin_policy_smoke.sh`'s opposite check. This script hits
# `platform:8100` from a throwaway runner on `homeai-internal` (same
# transport as `platform_auth_smoke.sh`) with `X-Forwarded-For: 8.8.8.8`
# as the last-hop classifier.
#
# Throwaway `e2e-g15-*` human via `lib/auth.sh`. Asserts
# `403 {"detail":"public_origin"}` **before** other business errors on
# privileged routes (setup, invite accept, WG create, pair begin, device
# enroll, passkey register/begin). Unauthenticated admin stays 401.
# Login / logout / GET-DELETE wireguard-devices / GET-DELETE device-pairs
# from public are not `public_origin`.
#
# Then temporarily sets `WEBAUTHN_RP_ID=localhost` (never `HOMEAI_DOMAIN`),
# PATCHes `public_https` on as a CLI `--role admin` throwaway (step-up),
# and checks web password login from public is `403 passkey_required`
# without checking the password, native/host password still allowed, and
# enrollment still `public_origin`. EXIT trap always PATCHes the flag off
# and restores platform without `WEBAUTHN_RP_ID`.
#
# Never completes bootstrap. Never recreates model-runner or postgres.
# Never prints `.env`. Takes `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/g15_enrollment_smoke.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

export COMPOSE_PROJECT_NAME=homeai

# Worktrees don't get the gitignored .env; compose still needs it. Never cat it.
if [ ! -f .env ] && [ -f /home/cody/code/unnamed_local_ai_server/.env ]; then
  ln -s /home/cody/code/unnamed_local_ai_server/.env .env
fi

# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"
# shellcheck source=lib/internal.sh
source "$SCRIPT_DIR/lib/internal.sh"

RP_ID_TOGGLED=0
PUBLIC_HTTPS_TOGGLED=0
ADMIN_COOKIE=""
ADMIN_PASSWORD=""
G15_COOKIE=""
PEER_ID=""

log() { echo "[g15] $(date '+%H:%M:%S') $*"; }

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

cid_of() {
  docker compose -p homeai ps -q "$1" 2>/dev/null | head -n1
}

wait_platform() {
  local i
  for i in $(seq 1 30); do
    if docker compose -p homeai exec -T platform python -c \
      "import urllib.request; urllib.request.urlopen('http://localhost:8100/internal/health', timeout=3)" \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  fail "platform not healthy"
}

assert_ids() {
  local pg_now mr_now
  pg_now="$(cid_of postgres)"
  mr_now="$(cid_of model-runner)"
  [ "$PG_BEFORE" = "$pg_now" ] || fail "postgres container id changed ($PG_BEFORE -> $pg_now)"
  [ "$MR_BEFORE" = "$mr_now" ] || fail "model-runner container id changed ($MR_BEFORE -> $mr_now)"
}

rp_id_state() {
  docker compose -p homeai exec -T platform python -c \
    "from app.core.config import Settings; s=Settings(); print('set' if (s.webauthn_rp_id or '').strip() else 'empty')"
}

domain_state() {
  docker compose -p homeai exec -T platform python -c \
    "from app.core.config import Settings; s=Settings(); print('set' if (s.homeai_domain or '').strip() else 'empty')"
}

restore_platform() {
  log "restoring platform without WEBAUTHN_RP_ID"
  env -u WEBAUTHN_RP_ID -u HOMEAI_DOMAIN docker compose -p homeai up -d --no-deps --no-build platform
  wait_platform
  local rp domain
  rp="$(rp_id_state)"
  domain="$(domain_state)"
  log "restored WEBAUTHN_RP_ID=$rp HOMEAI_DOMAIN=$domain"
  [ "$rp" = "empty" ] || fail "WEBAUTHN_RP_ID was not empty after restore"
  [ "$domain" = "empty" ] || fail "HOMEAI_DOMAIN is set after restore"
}

force_public_https_off() {
  # PATCH from LAN (explicit XFF: with the flag on, docker-bridge is public).
  if [ -n "$ADMIN_COOKIE" ] && [ -n "${INTERNAL_RUNNER:-}" ]; then
    G15_MODE=patch_off G15_COOKIE="$ADMIN_COOKIE" G15_PASSWORD="${ADMIN_PASSWORD:-}" \
      INTERNAL_PY_ENV="G15_MODE G15_COOKIE G15_PASSWORD" \
      internal_py "$G15_PY" >/dev/null 2>&1 || true
  fi
  _e2e_psql homeai_platform \
    "INSERT INTO platform_state (key, value) VALUES ('public_https', 'false'::jsonb)
     ON CONFLICT (key) DO UPDATE SET value = 'false'::jsonb" >/dev/null 2>&1 || true
}

confirm_public_https_false() {
  local flag
  flag="$(G15_MODE=status_flag INTERNAL_PY_ENV="G15_MODE" internal_py "$G15_PY" 2>/dev/null || true)"
  if [ "$flag" != "false" ]; then
    log "WARN: live public_https is '$flag' (wanted false); forcing off via postgres"
    force_public_https_off
    restore_platform
    flag="$(G15_MODE=status_flag INTERNAL_PY_ENV="G15_MODE" internal_py "$G15_PY")"
  fi
  [ "$flag" = "false" ] || fail "live public_https is still '$flag' (must be false)"
  log "ok   live public_https is false"
}

cleanup() {
  if [ -n "$PEER_ID" ] && [ -n "$G15_COOKIE" ]; then
    python3 - "${E2E_BASE:-http://localhost}" "$G15_COOKIE" "$PEER_ID" <<'PY' >/dev/null 2>&1 || true
import sys, urllib.request
base, cookie, peer = sys.argv[1:4]
req = urllib.request.Request(
    f"{base}/api/platform/me/wireguard-devices/{peer}",
    method="DELETE",
    headers={"Cookie": cookie},
)
try:
    urllib.request.urlopen(req, timeout=10)
except Exception:
    pass
PY
  fi
  if [ "$PUBLIC_HTTPS_TOGGLED" -eq 1 ] || [ "$RP_ID_TOGGLED" -eq 1 ]; then
    force_public_https_off
  fi
  if [ "$RP_ID_TOGGLED" -eq 1 ]; then
    restore_platform || true
  fi
  if [ -n "${INTERNAL_RUNNER:-}" ]; then
    if [ "$PUBLIC_HTTPS_TOGGLED" -eq 1 ] || [ "$RP_ID_TOGGLED" -eq 1 ]; then
      confirm_public_https_false || true
    fi
  fi
  internal_runner_stop
  e2e_auth_end
}
trap cleanup EXIT

# HTTP client against platform:8100 from the throwaway runner.
# Secrets travel in the environment, never argv (lib/internal.sh).
G15_PY="$(cat <<'EOF'
import json, os, sys, urllib.error, urllib.request

PLATFORM = "http://platform:8100"
PUBLIC = "8.8.8.8"
LAN = "192.168.1.10"


def log(msg: str) -> None:
    print(f"[g15] {msg}", flush=True)


def fail(msg: str) -> None:
    raise SystemExit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}", flush=True)


def call(method, path, *, xff, cookie=None, identity=None, client=None, json_body=None, timeout=15):
    headers = {"X-Forwarded-For": xff}
    if cookie:
        headers["Cookie"] = cookie
    if identity:
        headers["X-HomeAI-Identity"] = identity
    if client:
        headers["X-HomeAI-Client"] = client
    data = None
    if json_body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(json_body).encode()
    req = urllib.request.Request(PLATFORM + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else None, dict(resp.headers)
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            doc = json.loads(body) if body else None
        except json.JSONDecodeError:
            doc = body
        return e.code, doc, dict(e.headers)


def expect(status, doc, want, what, detail=None):
    if status != want:
        fail(f"{what}: expected HTTP {want}, got {status}: {doc}")
    if detail is not None:
        got = doc.get("detail") if isinstance(doc, dict) else None
        if got != detail:
            fail(f"{what}: expected detail {detail!r}, got {doc}")
    ok(f"{what} -> {status}" + (f" {detail}" if detail else ""))
    return doc


def header(hdrs, name):
    for k, v in hdrs.items():
        if k.lower() == name.lower():
            return v
    return None


def ident(cookie, xff=LAN):
    status, doc, hdrs = call("GET", "/internal/auth/verify", xff=xff, cookie=cookie)
    if status != 200:
        fail(f"verify: HTTP {status} {doc}")
    token = header(hdrs, "X-HomeAI-Identity")
    if not token:
        fail("verify: no X-HomeAI-Identity")
    return token


def public_origin(method, path, what, *, identity=None, cookie=None, json_body=None):
    status, doc, _ = call(
        method, path, xff=PUBLIC, identity=identity, cookie=cookie, json_body=json_body
    )
    expect(status, doc, 403, what, detail="public_origin")


mode = os.environ["G15_MODE"]

if mode == "status_flag":
    status, doc, _ = call("GET", "/api/auth/status", xff=LAN)
    if status != 200:
        fail(f"status: HTTP {status} {doc}")
    sys.stdout.write("true" if doc.get("public_https") else "false")
    sys.exit(0)

if mode == "patch_on":
    cookie = os.environ["G15_COOKIE"]
    password = os.environ["G15_PASSWORD"]
    status, doc, _ = call(
        "POST", "/api/auth/step-up", xff=LAN, cookie=cookie, json_body={"password": password}
    )
    expect(status, doc, 200, "admin step-up (LAN)")
    token = ident(cookie)
    status, doc, _ = call(
        "PATCH",
        "/api/platform/admin/settings",
        xff=LAN,
        identity=token,
        json_body={"public_https": True},
    )
    expect(status, doc, 200, "PATCH public_https true")
    if doc.get("public_https") is not True:
        fail(f"PATCH on: {doc}")
    sys.exit(0)

if mode == "patch_off":
    cookie = os.environ["G15_COOKIE"]
    password = os.environ.get("G15_PASSWORD") or ""
    if password:
        call("POST", "/api/auth/step-up", xff=LAN, cookie=cookie, json_body={"password": password})
    token = ident(cookie)
    status, doc, _ = call(
        "PATCH",
        "/api/platform/admin/settings",
        xff=LAN,
        identity=token,
        json_body={"public_https": False},
    )
    if status != 200 or (isinstance(doc, dict) and doc.get("public_https") is not False):
        fail(f"PATCH public_https false: HTTP {status} {doc}")
    ok("PATCH public_https false")
    sys.exit(0)

if mode == "flag_off":
    cookie = os.environ["G15_COOKIE"]
    user = os.environ["G15_USER"]
    password = os.environ["G15_PASSWORD"]
    token = ident(cookie)

    log("public origin refuses enrollment (before other business errors)")
    public_origin(
        "POST",
        "/api/auth/setup",
        "POST /api/auth/setup from public",
        json_body={
            "setup_code": "AAAA-AAAA-AAAA-AAAA",
            "username": "must-not-create",
            "display_name": "Must Not",
            "password": "correct horse battery",
        },
    )
    public_origin(
        "POST",
        "/api/auth/invite/accept",
        "POST /api/auth/invite/accept from public",
        json_body={
            "token": "hd_not-a-real-invite",
            "username": "must-not-accept",
            "display_name": "Must Not",
            "password": "correct horse battery",
        },
    )
    public_origin(
        "POST",
        "/api/auth/device/enroll",
        "POST /api/auth/device/enroll from public",
        json_body={
            "token": "hd_not-a-real-enroll",
            "public_key": "x",
            "name": "x",
            "signature": "x",
        },
    )
    public_origin(
        "POST",
        "/api/platform/me/wireguard-devices",
        "POST /api/platform/me/wireguard-devices from public",
        identity=token,
        json_body={"name": "e2e-g15-public"},
    )
    public_origin(
        "POST",
        "/api/platform/me/device-pairs/begin",
        "POST /api/platform/me/device-pairs/begin from public",
        identity=token,
    )
    # Origin is checked before require_rp_id, so this is public_origin
    # even with WEBAUTHN_RP_ID empty (not 409 domain_required).
    public_origin(
        "POST",
        "/api/platform/me/passkeys/register/begin",
        "POST /api/platform/me/passkeys/register/begin from public",
        identity=token,
    )

    status, doc, _ = call("GET", "/api/platform/admin/users", xff=PUBLIC)
    expect(status, doc, 401, "unauthenticated admin from public", detail="unauthenticated")

    log("login / logout / GET-DELETE from public are not public_origin")
    status, doc, _ = call(
        "POST",
        "/api/auth/login",
        xff=PUBLIC,
        json_body={"username": user, "password": password, "device_label": "g15 public"},
    )
    expect(status, doc, 200, "web password login from public (flag off)")
    # A second session; logout it. The lib/auth.sh cookie stays for identity.
    status, doc, hdrs = call(
        "POST",
        "/api/auth/login",
        xff="1.1.1.1",
        json_body={"username": user, "password": password, "device_label": "g15 logout"},
    )
    expect(status, doc, 200, "web login from another public IP")
    set_cookie = header(hdrs, "Set-Cookie") or ""
    session = set_cookie.split(";", 1)[0]
    if not session.startswith("homeai_session="):
        fail(f"login Set-Cookie: {set_cookie!r}")
    status, doc, _ = call("POST", "/api/auth/logout", xff=PUBLIC, cookie=session)
    expect(status, doc, 204, "logout from public")

    status, doc, _ = call(
        "GET", "/api/platform/me/wireguard-devices", xff=PUBLIC, identity=token
    )
    expect(status, doc, 200, "GET wireguard-devices from public")
    status, doc, _ = call(
        "GET", "/api/platform/me/device-pairs", xff=PUBLIC, identity=token
    )
    expect(status, doc, 200, "GET device-pairs from public")

    status, doc, _ = call(
        "POST",
        "/api/platform/me/wireguard-devices",
        xff=LAN,
        identity=token,
        json_body={"name": "e2e-g15-lan"},
    )
    expect(status, doc, 201, "POST wireguard-devices from LAN")
    peer_id = doc["id"]
    print(f"PEER_ID={peer_id}", flush=True)
    status, doc, _ = call(
        "DELETE",
        f"/api/platform/me/wireguard-devices/{peer_id}",
        xff=PUBLIC,
        identity=token,
    )
    expect(status, doc, 204, "DELETE wireguard-devices from public")
    print("PEER_ID=", flush=True)

    status, doc, _ = call(
        "DELETE",
        "/api/platform/me/device-pairs/00000000-0000-4000-8000-000000000000",
        xff=PUBLIC,
        identity=token,
    )
    expect(status, doc, 404, "DELETE device-pairs from public (missing)", detail="not_found")
    sys.exit(0)

if mode == "flag_on":
    cookie = os.environ["G15_COOKIE"]
    user = os.environ["G15_USER"]
    password = os.environ["G15_PASSWORD"]
    token = ident(cookie)

    log("public_https on: web password from public is passkey_required")
    status, doc, _ = call(
        "POST",
        "/api/auth/login",
        xff="1.0.0.1",
        json_body={"username": user, "password": password},
    )
    expect(status, doc, 403, "web password login from public (correct)", detail="passkey_required")
    status, doc, _ = call(
        "POST",
        "/api/auth/login",
        xff="9.9.9.9",
        json_body={"username": user, "password": "nope nope nope"},
    )
    expect(status, doc, 403, "web password login from public (wrong)", detail="passkey_required")

    status, doc, _ = call(
        "POST",
        "/api/auth/login",
        xff="8.8.4.4",
        client="native",
        json_body={"username": user, "password": password, "device_label": "g15 native"},
    )
    expect(status, doc, 200, "native password login from public still allowed")
    status, doc, _ = call(
        "POST",
        "/api/auth/login",
        xff="4.4.4.4",
        client="host",
        json_body={"username": user, "password": password, "device_label": "g15 host"},
    )
    expect(status, doc, 200, "host password login from public still allowed")

    log("enrollment still public_origin with the flag on")
    public_origin(
        "POST",
        "/api/auth/setup",
        "POST /api/auth/setup from public (flag on)",
        json_body={
            "setup_code": "AAAA-AAAA-AAAA-AAAA",
            "username": "must-not-create",
            "display_name": "Must Not",
            "password": "correct horse battery",
        },
    )
    public_origin(
        "POST",
        "/api/platform/me/wireguard-devices",
        "POST wireguard-devices from public (flag on)",
        identity=token,
        json_body={"name": "e2e-g15-flag-on"},
    )
    public_origin(
        "POST",
        "/api/platform/me/device-pairs/begin",
        "POST device-pairs/begin from public (flag on)",
        identity=token,
    )
    public_origin(
        "POST",
        "/api/platform/me/passkeys/register/begin",
        "POST passkeys/register/begin from public (flag on)",
        identity=token,
    )
    sys.exit(0)

fail(f"unknown G15_MODE={mode!r}")
EOF
)"

PG_BEFORE="$(cid_of postgres)"
MR_BEFORE="$(cid_of model-runner)"
log "postgres id=${PG_BEFORE:-?} model-runner id=${MR_BEFORE:-?} (must stay unchanged)"
[ -n "$PG_BEFORE" ] || fail "postgres is not running"
[ -n "$MR_BEFORE" ] || fail "model-runner is not running"

log "building platform only (no-deps; not caddy/postgres/model-runner)"
docker compose -p homeai up -d --build --no-deps platform
assert_ids
wait_platform
log "postgres and model-runner ids unchanged"

log "starting throwaway runner on homeai-internal"
internal_runner_start "e2e-g15-$$" || fail "could not start internal runner"
log "runner ${INTERNAL_RUNNER}"

e2e_auth_create_user g15
G15_USER="$E2E_NEW_USER"
G15_PASSWORD="$E2E_NEW_PASSWORD"
G15_COOKIE="$(e2e_auth_login "$G15_USER" "$G15_PASSWORD")"
log "member ${G15_USER}"

log "=== flag off: public origin refuses enrollment ==="
FLAG_OFF_OUT="$(
  G15_MODE=flag_off G15_COOKIE="$G15_COOKIE" G15_USER="$G15_USER" G15_PASSWORD="$G15_PASSWORD" \
    INTERNAL_PY_ENV="G15_MODE G15_COOKIE G15_USER G15_PASSWORD" \
    internal_py "$G15_PY"
)"
echo "$FLAG_OFF_OUT"
PEER_ID="$(echo "$FLAG_OFF_OUT" | awk -F= '/^PEER_ID=/{print $2}' | tail -n1)"
[ -z "$PEER_ID" ] || fail "LAN WireGuard peer ${PEER_ID} was not revoked from public"

log "=== public_https toggle (RP ID=localhost; restore is EXIT-trapped) ==="
log "rebuilding platform with WEBAUTHN_RP_ID=localhost (never HOMEAI_DOMAIN)"
RP_ID_TOGGLED=1
WEBAUTHN_RP_ID=localhost docker compose -p homeai up -d --build --no-deps platform
assert_ids
wait_platform
rp="$(rp_id_state)"
[ "$rp" = "set" ] || fail "expected WEBAUTHN_RP_ID set on platform, got '$rp'"
domain="$(domain_state)"
[ "$domain" = "empty" ] || fail "HOMEAI_DOMAIN must stay unset (got '$domain')"
log "ok   WEBAUTHN_RP_ID=localhost; HOMEAI_DOMAIN empty; ids unchanged"

ADMIN_USER="e2e-g15-admin-$(openssl rand -hex 4)"
ADMIN_PASSWORD="$(openssl rand -hex 16)"
_E2E_AUTH_CREATED+=("$ADMIN_USER")
printf '%s\n' "$ADMIN_PASSWORD" | docker compose -p homeai exec -T platform \
  python -m app.cli create-user "$ADMIN_USER" --role admin --display-name "E2E g15-admin" --password-stdin \
  >/dev/null
ADMIN_COOKIE="$(e2e_auth_login "$ADMIN_USER" "$ADMIN_PASSWORD")"
log "admin ${ADMIN_USER} (CLI --role admin; never bootstrap)"

G15_MODE=patch_on G15_COOKIE="$ADMIN_COOKIE" G15_PASSWORD="$ADMIN_PASSWORD" \
  INTERNAL_PY_ENV="G15_MODE G15_COOKIE G15_PASSWORD" \
  internal_py "$G15_PY"
PUBLIC_HTTPS_TOGGLED=1

G15_MODE=flag_on G15_COOKIE="$G15_COOKIE" G15_USER="$G15_USER" G15_PASSWORD="$G15_PASSWORD" \
  INTERNAL_PY_ENV="G15_MODE G15_COOKIE G15_USER G15_PASSWORD" \
  internal_py "$G15_PY"

log "turning public_https off, then restoring empty RP ID"
G15_MODE=patch_off G15_COOKIE="$ADMIN_COOKIE" G15_PASSWORD="$ADMIN_PASSWORD" \
  INTERNAL_PY_ENV="G15_MODE G15_COOKIE G15_PASSWORD" \
  internal_py "$G15_PY"
PUBLIC_HTTPS_TOGGLED=0
restore_platform
RP_ID_TOGGLED=0
assert_ids
confirm_public_https_false

echo "G15 ENROLLMENT SMOKE: PASS"
