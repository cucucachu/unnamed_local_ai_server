# shellcheck shell=bash
# Sign-in plumbing for the curl/urllib/websocket e2e scripts (M10-04).
#
# Since M10-04 every `/api/*` and `/ws/*` route except `/api/health` sits
# behind Caddy's `forward_auth`, so scripts sign in as a throwaway `e2e-*`
# user created with the platform's recovery CLI, through Caddy's
# `/api/auth/login` like the web app does. Never completes bootstrap — the
# maintainer must become the first admin.
#
#   source "$SCRIPT_DIR/lib/auth.sh"
#   e2e_auth_begin threads      # user e2e-threads-<hex>, signed in
#   ... python3 / wget / ws_smoke.py with the exported credentials ...
#   e2e_auth_end                # call from the script's EXIT trap
#
# `e2e_auth_begin` exports:
#   E2E_AUTH_USER    the username
#   E2E_AUTH_COOKIE  `homeai_session=hs_...` - send as a `Cookie:` header
#                    (urllib, `wget --header`, websockets `additional_headers`)
#   E2E_COOKIE_JAR   the same cookie as a Netscape jar (`wget --load-cookies`,
#                    `curl -b`)
# If E2E_AUTH_COOKIE is already set (a parent script such as gate_full.sh
# signed in), it's reused and this script owns no user: per-user state like
# `hitl_enabled` then carries across the whole chain, as before M10-04.
#
# `e2e_auth_create_user <prefix>` makes an extra user for multi-user checks
# (sets E2E_NEW_USER / E2E_NEW_PASSWORD); `e2e_auth_login <username>
# <password>` prints a cookie. Every user created here is deleted by `e2e_auth_end`, together
# with its personal space and its agent-server threads, checkpoints, turn
# stats, and settings.
#
# Env: E2E_BASE (default http://localhost) is where Caddy is reached.

E2E_BASE="${E2E_BASE:-http://localhost}"
_E2E_AUTH_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
_E2E_AUTH_CREATED=()
_E2E_AUTH_OWN_COOKIE=""

_e2e_env_value() {
  local value
  value="$(sed -n "s/^$1=\(.*\)$/\1/p" "$_E2E_AUTH_REPO_ROOT/.env" 2>/dev/null | tail -n1 | xargs)"
  printf '%s' "${value:-$2}"
}

_e2e_compose() {
  (cd "$_E2E_AUTH_REPO_ROOT" && docker compose "$@")
}

_e2e_psql() {
  local db="$1" query="$2"
  _e2e_compose exec -T postgres psql -qtA -v ON_ERROR_STOP=1 \
    -U "$(_e2e_env_value POSTGRES_USER homeai)" -d "$db" -c "$query"
}

# Sets E2E_NEW_USER / E2E_NEW_PASSWORD. Not for `$(...)`: the subshell would
# lose the record `e2e_auth_end` cleans up from.
e2e_auth_create_user() {
  local prefix="$1"
  E2E_NEW_USER="e2e-${prefix}-$(openssl rand -hex 4)"
  E2E_NEW_PASSWORD="$(openssl rand -hex 16)"
  _E2E_AUTH_CREATED+=("$E2E_NEW_USER")
  printf '%s\n' "$E2E_NEW_PASSWORD" | _e2e_compose exec -T platform \
    python -m app.cli create-user "$E2E_NEW_USER" --display-name "E2E ${prefix}" --password-stdin \
    >/dev/null
}

# Prints `homeai_session=hs_...`.
e2e_auth_login() {
  python3 - "$E2E_BASE" "$1" "$2" <<'PY'
import json
import sys
import urllib.error
import urllib.request

base, username, password = sys.argv[1:4]
body = json.dumps({"username": username, "password": password, "device_label": "e2e script"})
req = urllib.request.Request(
    f"{base}/api/auth/login",
    data=body.encode(),
    method="POST",
    headers={"Content-Type": "application/json"},
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        cookies = resp.headers.get_all("Set-Cookie") or []
except urllib.error.HTTPError as e:
    raise SystemExit(f"e2e_auth_login: {username}: HTTP {e.code} {e.read().decode()}") from e
session = next((c.split(";", 1)[0] for c in cookies if c.startswith("homeai_session=")), None)
if not session:
    raise SystemExit(f"e2e_auth_login: {username}: no homeai_session cookie in {cookies!r}")
print(session)
PY
}

_e2e_write_cookie_jar() {
  local host
  host="$(python3 -c 'import sys, urllib.parse; print(urllib.parse.urlsplit(sys.argv[1]).hostname)' "$E2E_BASE")"
  E2E_COOKIE_JAR="$(mktemp -t e2e-cookies.XXXXXX)"
  printf '# Netscape HTTP Cookie File\n%s\tFALSE\t/\tFALSE\t0\thomeai_session\t%s\n' \
    "$host" "${E2E_AUTH_COOKIE#homeai_session=}" >"$E2E_COOKIE_JAR"
  export E2E_COOKIE_JAR
}

e2e_auth_begin() {
  if [ -n "${E2E_AUTH_COOKIE:-}" ]; then
    [ -n "${E2E_COOKIE_JAR:-}" ] && [ -f "$E2E_COOKIE_JAR" ] || _e2e_write_cookie_jar
    return 0
  fi
  e2e_auth_create_user "$1" || return 1
  E2E_AUTH_COOKIE="$(e2e_auth_login "$E2E_NEW_USER" "$E2E_NEW_PASSWORD")" || return 1
  E2E_AUTH_USER="$E2E_NEW_USER"
  _E2E_AUTH_OWN_COOKIE="$E2E_AUTH_COOKIE"
  export E2E_AUTH_USER E2E_AUTH_COOKIE
  _e2e_write_cookie_jar
}

_e2e_delete_user() {
  local username="$1" user_id space_ids db id
  [[ "$username" =~ ^e2e-[a-z0-9._-]+$ ]] || return 0
  user_id="$(_e2e_psql homeai_platform "SELECT id FROM users WHERE username = '$username'")"
  [[ "$user_id" =~ ^[0-9a-f-]{36}$ ]] || return 0
  db="$(_e2e_env_value POSTGRES_DB homeai)"
  _e2e_psql "$db" "
    CREATE TEMP TABLE doomed AS SELECT id::text AS id FROM threads WHERE owner_user_id = '$user_id';
    DELETE FROM checkpoint_writes WHERE thread_id IN (SELECT id FROM doomed);
    DELETE FROM checkpoint_blobs WHERE thread_id IN (SELECT id FROM doomed);
    DELETE FROM checkpoints WHERE thread_id IN (SELECT id FROM doomed);
    DELETE FROM turn_stats WHERE thread_id IN (SELECT id FROM doomed);
    DELETE FROM threads WHERE owner_user_id = '$user_id';
    DELETE FROM user_settings WHERE user_id = '$user_id';" >/dev/null
  space_ids="$(_e2e_psql homeai_platform "SELECT id FROM spaces WHERE owner_user_id = '$user_id'")"
  _e2e_psql homeai_platform "
    DELETE FROM spaces WHERE owner_user_id = '$user_id';
    DELETE FROM users WHERE id = '$user_id';" >/dev/null
  for id in $space_ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id"
  done
}

# Best effort: cleanup must not mask the script's real result.
e2e_auth_end() {
  if [ -n "$_E2E_AUTH_OWN_COOKIE" ]; then
    python3 - "$E2E_BASE" "$_E2E_AUTH_OWN_COOKIE" <<'PY' >/dev/null 2>&1 || true
import sys, urllib.request
req = urllib.request.Request(f"{sys.argv[1]}/api/auth/logout", method="POST", headers={"Cookie": sys.argv[2]})
urllib.request.urlopen(req, timeout=10)
PY
    rm -f "${E2E_COOKIE_JAR:-}"
    unset E2E_AUTH_COOKIE E2E_AUTH_USER E2E_COOKIE_JAR
    _E2E_AUTH_OWN_COOKIE=""
  fi
  local username
  for username in "${_E2E_AUTH_CREATED[@]}"; do
    _e2e_delete_user "$username" || echo "WARN: could not delete e2e user $username" >&2
  done
  _E2E_AUTH_CREATED=()
}
