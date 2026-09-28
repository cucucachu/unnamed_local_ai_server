#!/usr/bin/env bash
# M10-05: live spaces + memberships + on-disk layout against the running `platform`.
#
# Like platform_auth_smoke.sh, requests go from a throwaway `curlimages/curl`
# container on `homeai_homeai-internal` straight to `platform:8100`, playing
# Caddy's part (verify -> X-HomeAI-Identity) by hand. Never completes
# bootstrap.
#
#   1. The recovery CLI creates two members `e2e-sp-<rand>-a`/`-b` (each gets
#      a personal space), a shared space `e2e-sp-<rand>` owned by A, and adds
#      B as a viewer.
#   2. On the host, `${SPACES_DIR}` (default /srv/homeai/spaces) holds a
#      `<space_id>` dir per space, `root:<gid>` with mode 2770 (drwxrws---);
#      inside the container `files/` is the same and `apps/` is 2750 (only the
#      platform writes app data).
#   3. API: B lists both spaces with the right roles; B (viewer) can't add a
#      member (403 insufficient_role) and A's personal space is 404 to B;
#      A promotes B to editor, can't remove the last owner (409), and the
#      directory lists both users.
# Everything it created (rows and directories) is deleted on exit.
#
# Usage: scripts/e2e/platform_spaces_smoke.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

NETWORK="homeai_homeai-internal"
CURL_IMAGE="curlimages/curl:latest"
BASE="http://platform:8100"
PREFIX="e2e-sp-$(openssl rand -hex 3)"
USER_A="$PREFIX-a"
USER_B="$PREFIX-b"
SPACE="$PREFIX"
PASSWORD="$(openssl rand -hex 16)"
env_value() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }
POSTGRES_USER="$(env_value POSTGRES_USER)"
POSTGRES_USER="${POSTGRES_USER:-homeai}"
SPACES_DIR="$(env_value SPACES_DIR)"
SPACES_DIR="${SPACES_DIR:-/srv/homeai/spaces}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

psql_q() {
  docker compose exec -T postgres psql -qtA -U "$POSTGRES_USER" -d homeai_platform -c "$1"
}

cleanup() {
  local ids
  ids="$(psql_q "SELECT s.id FROM spaces s LEFT JOIN users u ON u.id = s.owner_user_id
    WHERE s.slug = '$SPACE' OR u.username IN ('$USER_A', '$USER_B')" 2>/dev/null || true)"
  psql_q "DELETE FROM spaces s USING users u WHERE u.id = s.owner_user_id
      AND u.username IN ('$USER_A', '$USER_B');
    DELETE FROM spaces WHERE slug = '$SPACE';
    DELETE FROM users WHERE username IN ('$USER_A', '$USER_B')" >/dev/null 2>&1 || true
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && docker compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
}
trap cleanup EXIT

curl_i() {
  docker run --rm --network "$NETWORK" "$CURL_IMAGE" -sS -i --max-time 10 "$@"
}
status_of() { head -n1 <<<"$1" | awk '{print $2}'; }
header_of() { grep -i "^$2:" <<<"$1" | head -n1 | cut -d' ' -f2- | tr -d '\r'; }
body_of() { awk 'BEGIN{b=0} b{print} /^\r?$/{b=1}' <<<"$1"; }
py() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }

expect() {
  local want="$1" response="$2" what="$3" detail="${4:-}"
  local got
  got="$(status_of "$response")"
  [[ "$got" == "$want" ]] || fail "$what: expected HTTP $want, got $got: $(body_of "$response")"
  if [[ -n "$detail" ]]; then
    [[ "$(body_of "$response")" == "{\"detail\":\"$detail\"}" ]] ||
      fail "$what: expected detail $detail, got $(body_of "$response")"
  fi
  echo "ok   $what -> $got${detail:+ $detail}"
}

identity_for() {
  local r token
  r="$(curl_i -H 'Content-Type: application/json' -H 'X-HomeAI-Client: native' \
    -d "{\"username\":\"$1\",\"password\":\"$PASSWORD\"}" "$BASE/api/auth/login")"
  [[ "$(status_of "$r")" == 200 ]] || fail "login $1: $(body_of "$r")"
  token="$(body_of "$r" | py 'print(d["session_token"])')"
  r="$(curl_i -H "Authorization: Bearer $token" "$BASE/internal/auth/verify")"
  [[ "$(status_of "$r")" == 200 ]] || fail "verify $1"
  header_of "$r" x-homeai-identity
}

cli() { docker compose exec -T platform python -m app.cli "$@"; }

echo "== waiting for platform health"
for _ in $(seq 1 30); do
  if docker compose exec -T platform python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8100/internal/health', timeout=3)" \
    >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "== 1. CLI: users, shared space, membership"
for u in "$USER_A" "$USER_B"; do
  printf '%s\n' "$PASSWORD" | cli create-user "$u" --password-stdin
done
cli create-space "$SPACE" --name "E2E Spaces" --owner "$USER_A"
cli add-member "$SPACE" "$USER_B" --role viewer
spaces_json="$(cli list-spaces --json)"
space_field() { py "print(next(s for s in d if s['slug'] == '$1')['$2'])" <<<"$spaces_json"; }
SHARED_ID="$(space_field "$SPACE" id)"
SHARED_GID="$(space_field "$SPACE" gid)"
A_ID="$(space_field "$USER_A" id)"
A_GID="$(space_field "$USER_A" gid)"
B_ID="$(space_field "$USER_B" id)"
B_GID="$(space_field "$USER_B" gid)"
members="$(py "print(','.join(m['username']+':'+m['role'] for s in d if s['slug']=='$SPACE' for m in s['members']))" <<<"$spaces_json")"
[[ "$members" == "$USER_A:owner,$USER_B:viewer" ]] || fail "members: $members"
echo "ok   list-spaces: $SPACE (gid $SHARED_GID) members $members; personal gids $A_GID, $B_GID"
[[ "$(space_field "$USER_A" kind)" == personal && "$(space_field "$USER_B" kind)" == personal ]] ||
  fail "personal spaces missing"

echo "== 2. storage under $SPACES_DIR"
host_listing="$(ls -ln "$SPACES_DIR")"
for pair in "$SHARED_ID:$SHARED_GID" "$A_ID:$A_GID" "$B_ID:$B_GID"; do
  id="${pair%%:*}" gid="${pair##*:}"
  line="$(grep " $id\$" <<<"$host_listing")" || fail "host: no $SPACES_DIR/$id"
  read -r perms _ uid g _ <<<"$line"
  [[ "$perms" == drwxrws--- && "$uid" == 0 && "$g" == "$gid" ]] ||
    fail "host: $SPACES_DIR/$id is $perms $uid:$g, want drwxrws--- 0:$gid"
  echo "ok   host $SPACES_DIR/$id  $perms $uid:$g"
  inside="$(docker compose exec -T platform stat -c '%n %u:%g %a' \
    "/data/spaces/$id/files" "/data/spaces/$id/apps")"
  while read -r path owner mode; do
    want=2770
    [[ "$path" == */apps ]] && want=2750
    [[ "$owner" == "0:$gid" && "$mode" == "$want" ]] || fail "container: $path is $owner $mode, want 0:$gid $want"
    echo "ok   container $path  $owner $mode"
  done <<<"$inside"
done

echo "== 3. API"
ID_A="$(identity_for "$USER_A")"
ID_B="$(identity_for "$USER_B")"
r="$(curl_i -H "X-HomeAI-Identity: $ID_B" "$BASE/api/platform/spaces")"
expect 200 "$r" "GET /spaces (B)"
roles="$(body_of "$r" | py "print(','.join(s['slug']+':'+s['role'] for s in d['spaces']))")"
[[ "$roles" == "$USER_B:owner,$SPACE:viewer" ]] || fail "B's spaces: $roles"
echo "     B sees $roles"
r="$(curl_i -H "X-HomeAI-Identity: $ID_B" "$BASE/api/platform/spaces/$A_ID")"
expect 404 "$r" "GET A's personal space (B)" not_found
r="$(curl_i -H "X-HomeAI-Identity: $ID_A" "$BASE/api/platform/users/directory")"
expect 200 "$r" "GET /users/directory (A)"
A_USER_ID="$(body_of "$r" | py "print(next(u['id'] for u in d['users'] if u['username']=='$USER_A'))")"
B_USER_ID="$(body_of "$r" | py "print(next(u['id'] for u in d['users'] if u['username']=='$USER_B'))")"
r="$(curl_i -H "X-HomeAI-Identity: $ID_B" -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"$A_USER_ID\",\"role\":\"viewer\"}" "$BASE/api/platform/spaces/$SHARED_ID/members")"
expect 403 "$r" "POST members (B, viewer)" insufficient_role
r="$(curl_i -X PATCH -H "X-HomeAI-Identity: $ID_A" -H 'Content-Type: application/json' \
  -d '{"role":"editor"}' "$BASE/api/platform/spaces/$SHARED_ID/members/$B_USER_ID")"
expect 200 "$r" "PATCH B -> editor (A)"
r="$(curl_i -X DELETE -H "X-HomeAI-Identity: $ID_A" \
  "$BASE/api/platform/spaces/$SHARED_ID/members/$A_USER_ID")"
expect 409 "$r" "DELETE last owner (A)" last_owner
r="$(curl_i -H "X-HomeAI-Identity: $ID_B" "$BASE/api/platform/spaces/$SHARED_ID")"
expect 200 "$r" "GET shared space (B)"
[[ "$(body_of "$r" | py 'print(d["role"])')" == editor ]] || fail "B's role after promote"

echo "PASS: platform spaces smoke"
