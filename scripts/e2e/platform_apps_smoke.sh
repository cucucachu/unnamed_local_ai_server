#!/usr/bin/env bash
# M12-02: live app registry against the running `platform`.
#
# Like platform_files_smoke.sh, requests go from a throwaway `curlimages/curl`
# container on `homeai_homeai-internal` straight to `platform:8100`, playing
# Caddy's part (verify -> X-HomeAI-Identity) by hand. Never completes
# bootstrap.
#
#   1. The recovery CLI creates users `e2e-pa-<rand>-{owner,outsider}`.
#   2. `GET /api/platform/apps/schema` serves the app.json JSON Schema.
#   3. The owner uploads the fixture app (`fixtures/apps/hello/`, minus
#      AGENT.md) to `/personal/Apps/hello` through the files API; registering
#      it is 422 `invalid_app` with an AGENT.md diagnostic. After uploading
#      AGENT.md it registers (201), and `POST /apps/{id}/validate` is valid.
#   4. The owner installs it in their personal space: the instance dir
#      `apps/<instance_id>/` exists on disk as `0:<space gid>` 2750.
#   5. The outsider can't see the app (404), list the owner's instances (404),
#      or install it into the owner's space (404).
#   6. The files API refuses to delete or rename `/personal/Apps` (403 reserved).
#   7. The CLI registers a second copy (`hello2`), installs it, and lists both.
#   8. Uninstalling moves the instance dir to `apps/.trash/<instance_id>-<stamp>/`.
# Everything it created (rows and directories) is deleted on exit.
#
# Usage: scripts/e2e/platform_apps_smoke.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

NETWORK="homeai_homeai-internal"
CURL_IMAGE="curlimages/curl:latest"
BASE="http://platform:8100"
API="$BASE/api/platform"
FIXTURE="$SCRIPT_DIR/fixtures/apps/hello"
PREFIX="e2e-pa-$(openssl rand -hex 3)"
ROLES=(owner outsider)
PASSWORD="$(openssl rand -hex 16)"
env_value() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }
POSTGRES_USER="$(env_value POSTGRES_USER)"
POSTGRES_USER="${POSTGRES_USER:-homeai}"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

psql_q() {
  docker compose exec -T postgres psql -qtA -U "$POSTGRES_USER" -d homeai_platform -c "$1"
}

user_list() {
  local r out=""
  for r in "${ROLES[@]}"; do out+="${out:+, }'$PREFIX-$r'"; done
  echo "$out"
}

# Deleting the spaces cascades to their apps, versions and instances.
cleanup() {
  local ids users
  users="$(user_list)"
  ids="$(psql_q "SELECT s.id FROM spaces s JOIN users u ON u.id = s.owner_user_id
    WHERE u.username IN ($users)" 2>/dev/null || true)"
  psql_q "DELETE FROM spaces s USING users u WHERE u.id = s.owner_user_id AND u.username IN ($users);
    DELETE FROM users WHERE username IN ($users)" >/dev/null 2>&1 || true
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && docker compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
}
trap cleanup EXIT

curl_i() {
  docker run --rm -i --network "$NETWORK" "$CURL_IMAGE" -sS -i --globoff --max-time 20 "$@"
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
    [[ "$(body_of "$response" | py 'print(d["detail"])')" == "$detail" ]] ||
      fail "$what: expected detail $detail, got $(body_of "$response")"
  fi
  echo "ok   $what -> $got${detail:+ $detail}"
}

identity_for() {
  local r token
  r="$(curl_i -H 'Content-Type: application/json' -H 'X-HomeAI-Client: native' \
    -d "{\"username\":\"$1\",\"password\":\"$PASSWORD\"}" "$BASE/api/auth/login" </dev/null)"
  [[ "$(status_of "$r")" == 200 ]] || fail "login $1: $(body_of "$r")"
  token="$(body_of "$r" | py 'print(d["session_token"])')"
  r="$(curl_i -H "Authorization: Bearer $token" "$BASE/internal/auth/verify" </dev/null)"
  [[ "$(status_of "$r")" == 200 ]] || fail "verify $1"
  header_of "$r" x-homeai-identity
}

cli() { docker compose exec -T platform python -m app.cli "$@"; }
disk() { docker compose exec -T platform "$@"; }

declare -A IDENT
as() {
  local role="$1"
  shift
  curl_i -H "X-HomeAI-Identity: ${IDENT[$role]}" "$@"
}
get() { as "$1" "$API$2" </dev/null; }
post_json() { as "$1" -H 'Content-Type: application/json' -d "$3" "$API$2" </dev/null; }
delete() { as "$1" -X DELETE "$API$2" </dev/null; }
# $1 role, $2 fixture-relative file, $3 destination folder (virtual path).
put_file() {
  as "$1" -X PUT -H 'Content-Type: application/octet-stream' --data-binary @- \
    "$API/files/content?path=$3/$2" <"$FIXTURE/$2"
}
upload_fixture() {
  local rel
  while IFS= read -r rel; do
    [[ "$rel" == AGENT.md && "${3:-}" == no-agent ]] && continue
    expect 200 "$(put_file "$1" "$rel" "$2")" "upload $2/$rel"
  done < <(cd "$FIXTURE" && find . -type f | sed 's|^\./||' | sort)
}

echo "== waiting for platform health"
for _ in $(seq 1 30); do
  if disk python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8100/internal/health', timeout=3)" \
    >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "== 1. CLI: users"
for role in "${ROLES[@]}"; do
  printf '%s\n' "$PASSWORD" | cli create-user "$PREFIX-$role" --password-stdin
  IDENT[$role]="$(identity_for "$PREFIX-$role")"
done
spaces_json="$(cli list-spaces --json)"
space_field() { py "print(next(s for s in d if s['slug'] == '$1')['$2'])" <<<"$spaces_json"; }
S_ID="$(space_field "$PREFIX-owner" id)"
S_GID="$(space_field "$PREFIX-owner" gid)"

echo "== 2. schema"
r="$(get owner /apps/schema)"
expect 200 "$r" "GET /apps/schema"
# shellcheck disable=SC2016 # "$schema" is a JSON key, not a shell variable.
[[ "$(body_of "$r" | py 'print(d["$schema"], d["properties"]["homeai"]["properties"]["sdk"]["enum"])')" == \
  "https://json-schema.org/draft/2020-12/schema ['1']" ]] || fail "schema body: $(body_of "$r")"

echo "== 3. register /personal/Apps/hello"
SRC=/personal/Apps/hello
upload_fixture owner "$SRC" no-agent
r="$(post_json owner /apps "{\"source_path\":\"$SRC\"}")"
expect 422 "$r" "register without AGENT.md" invalid_app
[[ "$(body_of "$r" | py 'print([x["file"] for x in d["diagnostics"]])')" == "['AGENT.md']" ]] ||
  fail "diagnostics: $(body_of "$r")"
echo "     diagnostics: $(body_of "$r" | py 'print(d["diagnostics"])')"
expect 200 "$(put_file owner AGENT.md "$SRC")" "upload $SRC/AGENT.md"
r="$(post_json owner /apps "{\"source_path\":\"$SRC/\"}")"
expect 201 "$r" "register $SRC"
APP_ID="$(body_of "$r" | py 'print(d["app"]["id"])')"
[[ "$(body_of "$r" | py 'a=d["app"]; print(a["slug"], a["source_path"], a["working_version"]["version"], d["valid"])')" == \
  "hello $SRC 1.0.0 True" ]] || fail "register body: $(body_of "$r")"
expect 409 "$(post_json owner /apps "{\"source_path\":\"$SRC\"}")" "register $SRC again" app_exists
r="$(post_json owner "/apps/$APP_ID/validate" '{}')"
expect 200 "$r" "validate"
[[ "$(body_of "$r" | py 'print(d["valid"], d["diagnostics"])')" == "True []" ]] || fail "validate: $(body_of "$r")"

echo "== 4. install in the personal space"
r="$(post_json owner "/spaces/$S_ID/instances" "{\"app_id\":\"$APP_ID\",\"tracks\":\"working\"}")"
expect 201 "$r" "install hello"
IID="$(body_of "$r" | py 'print(d["id"])')"
read -r owner mode < <(disk stat -c '%u:%g %a' "/data/spaces/$S_ID/apps/$IID")
[[ "$owner $mode" == "0:$S_GID 2750" ]] || fail "instance dir is $owner $mode, want 0:$S_GID 2750"
echo "ok   apps/$IID is $owner $mode"
r="$(get owner "/spaces/$S_ID/instances")"
expect 200 "$r" "list instances"
[[ "$(body_of "$r" | py 'print([(i["id"], i["app"]["slug"], i["tracks"]) for i in d["instances"]])')" == \
  "[('$IID', 'hello', 'working')]" ]] || fail "instances: $(body_of "$r")"
expect 409 "$(post_json owner "/spaces/$S_ID/instances" "{\"app_id\":\"$APP_ID\"}")" \
  "install hello again" already_installed

echo "== 5. outsider"
expect 404 "$(get outsider "/apps/$APP_ID")" "get app (outsider)" not_found
r="$(get outsider /apps)"
body_of "$r" | py "assert '$APP_ID' not in [a['id'] for a in d['apps']], d" || fail "outsider lists the app"
echo "ok   outsider's /apps doesn't list it"
expect 404 "$(get outsider "/spaces/$S_ID/instances")" "list instances (outsider)" not_found
expect 404 "$(post_json outsider "/spaces/$S_ID/instances" "{\"app_id\":\"$APP_ID\"}")" \
  "install into the owner's space (outsider)" not_found

echo "== 6. reserved Apps folder"
expect 403 "$(as owner -X DELETE "$API/files?path=/personal/Apps" </dev/null)" "delete /personal/Apps" reserved
expect 403 "$(post_json owner /files/rename '{"path":"/personal/Apps","name":"Old"}')" \
  "rename /personal/Apps" reserved
expect 403 "$(post_json owner /files/move '{"src":"/personal/Apps","dst":"/personal/x/Apps"}')" \
  "move /personal/Apps" reserved

echo "== 7. CLI: register-app, install-app, list-apps"
upload_fixture owner /personal/Apps/hello2 >/dev/null
r="$(as owner -X PUT --data-binary @- "$API/files/content?path=/personal/Apps/hello2/app.json" \
  < <(sed 's/"slug": "hello"/"slug": "hello2"/' "$FIXTURE/app.json"))"
expect 200 "$r" "rewrite hello2/app.json"
out="$(cli register-app "$PREFIX-owner" /personal/Apps/hello2)"
echo "     $out"
APP2="$(sed -n 's/.*(id \([0-9a-f-]*\)).*/\1/p' <<<"$out")"
[[ "$APP2" =~ ^[0-9a-f-]{36}$ ]] || fail "register-app: $out"
echo "     $(cli install-app "$PREFIX-owner" "$APP2")"
listed="$(cli list-apps --json | py "print(sorted((a['slug'], a['instances']) for a in d if a['space'] == '$PREFIX-owner'))")"
[[ "$listed" == "[('hello', 1), ('hello2', 1)]" ]] || fail "list-apps: $listed"
echo "ok   list-apps: $listed"

echo "== 8. uninstall keeps a final snapshot"
expect 404 "$(delete outsider "/spaces/$S_ID/instances/$IID")" "uninstall (outsider)" not_found
expect 204 "$(delete owner "/spaces/$S_ID/instances/$IID")" "uninstall hello"
disk test ! -e "/data/spaces/$S_ID/apps/$IID" || fail "apps/$IID still there"
kept="$(disk sh -c "ls /data/spaces/$S_ID/apps/.trash" | tr -d '\r')"
[[ "$kept" == "$IID-"* ]] || fail "trash has '$kept'"
echo "ok   apps/.trash/$kept"
r="$(get owner "/spaces/$S_ID/instances")"
[[ "$(body_of "$r" | py 'print([i["app"]["slug"] for i in d["instances"]])')" == "['hello2']" ]] ||
  fail "instances after uninstall: $(body_of "$r")"
echo "ok   only hello2 is still installed"

echo "PASS: platform apps smoke"
