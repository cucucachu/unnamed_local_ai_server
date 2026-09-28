#!/usr/bin/env bash
# M12-04: live app builds against the running `platform` + `code-exec-manager`.
#
# Needs the live stack and the builder image, which step 0 (re)builds from
# services/app-builder (cached; its `npm ci` needs the internet the first
# time). Requests go from a throwaway `curlimages/curl` container on
# `homeai_homeai-internal` straight to `platform:8100`, as in
# platform_apps_smoke.sh. Never completes bootstrap.
#
#   1. The recovery CLI creates users `e2e-ab-<rand>-{owner,outsider}`.
#   2. The owner uploads the fixture app (`fixtures/apps/hello/`) to
#      `/personal/Apps/hello` and registers it.
#   3. `POST /apps/{id}/build` builds it: `ok`, and the working version's
#      `bundle_path` is `app-bundles/<app>/<build>/app.js`, on disk in the
#      platform's data dir as an `__homeai_define(...)` bundle. The staging
#      dir and the builder containers are gone afterwards.
#   4. Each broken variant of app/index.tsx gets exactly one precise
#      diagnostic (step, file, line, column) and leaves the bundle as it was:
#      a missing import, a disallowed import, a type error, a render throw.
#      So does a bad app.json (step `manifest`, never reaching the builder).
#   5. The outsider can't build the app (404).
#   6. Rebuilding the fixed app replaces the bundle and removes the old one.
# Everything it created (rows, directories, bundles) is deleted on exit.
#
# Usage: scripts/e2e/app_build_smoke.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

NETWORK="homeai_homeai-internal"
CURL_IMAGE="curlimages/curl:latest"
BASE="http://platform:8100"
API="$BASE/api/platform"
FIXTURE="$SCRIPT_DIR/fixtures/apps/hello"
PREFIX="e2e-ab-$(openssl rand -hex 3)"
ROLES=(owner outsider)
PASSWORD="$(openssl rand -hex 16)"
SRC=/personal/Apps/hello
APP_ID=""
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

# Deleting the spaces cascades to their apps and versions; bundles are files.
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
  if [[ "$APP_ID" =~ ^[0-9a-f-]{36}$ ]]; then
    docker compose exec -T platform rm -rf "/data/platform/app-bundles/$APP_ID" "/data/platform/app-git/$APP_ID.git" || true
  fi
}
trap cleanup EXIT

curl_i() {
  docker run --rm -i --network "$NETWORK" "$CURL_IMAGE" -sS -i --globoff --max-time 300 "$@"
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
# $1 role, $2 virtual path; the content on stdin.
put() { as "$1" -X PUT -H 'Content-Type: application/octet-stream' --data-binary @- "$API/files/content?path=$2"; }
build() { post_json "$1" "/apps/$APP_ID/build" '{}'; }
bundle_path() { body_of "$(get owner "/apps/$APP_ID")" | py 'print(d["working_version"]["bundle_path"])'; }

# $1 what, $2 expected "step file line column", $3 a substring of the message.
expect_diagnostic() {
  local r got
  r="$(build owner)"
  expect 200 "$r" "build ($1)"
  got="$(body_of "$r" | py '
ds = d["diagnostics"]
assert d["ok"] is False and len(ds) == 1, ds
x = ds[0]
print(x["step"], x["file"], x["line"], x["column"], "|", x["message"])')" ||
    fail "$1: $(body_of "$r")"
  [[ "${got%% | *}" == "$2" ]] || fail "$1: expected '$2', got '$got'"
  [[ "$got" == *"$3"* ]] || fail "$1: message lacks '$3': $got"
  echo "ok   $1: $got"
  [[ "$(bundle_path)" == "$GOOD" ]] || fail "$1: bundle_path changed"
  disk test -f "/data/platform/$GOOD" || fail "$1: the previous bundle is gone"
}

set_index() {
  expect 200 "$(put owner "$SRC/app/index.tsx")" "upload app/index.tsx ($1)" >/dev/null
}

echo "== 0. builder image"
bash services/app-builder/build-builder-image.sh >/dev/null
docker image inspect homeai-app-builder:latest >/dev/null || fail "no builder image"
echo "ok   homeai-app-builder:latest"

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

echo "== 2. upload and register $SRC"
while IFS= read -r rel; do
  expect 200 "$(put owner "$SRC/$rel" <"$FIXTURE/$rel")" "upload $rel"
done < <(cd "$FIXTURE" && find . -type f | sed 's|^\./||' | sort)
r="$(post_json owner /apps "{\"source_path\":\"$SRC\"}")"
expect 201 "$r" "register $SRC"
APP_ID="$(body_of "$r" | py 'print(d["app"]["id"])')"

echo "== 3. build"
r="$(build owner)"
expect 200 "$r" "build hello"
read -r ok BUILD_ID GOOD < <(body_of "$r" | py 'b=d["build"]; print(d["ok"], b["id"], b["bundle_path"])') ||
  fail "build body: $(body_of "$r")"
[[ "$ok" == True ]] || fail "build failed: $(body_of "$r")"
[[ "$GOOD" == "app-bundles/$APP_ID/$BUILD_ID/app.js" ]] || fail "bundle_path $GOOD"
[[ "$(bundle_path)" == "$GOOD" ]] || fail "GET /apps/{id} bundle_path isn't $GOOD"
echo "ok   bundle_path $GOOD ($(body_of "$r" | py 'print(d["build"]["bundle_bytes"], "bytes,", d["build"]["duration_ms"], "ms")'))"
[[ "$(disk head -c 16 "/data/platform/$GOOD")" == "__homeai_define(" ]] || fail "not a bundle"
disk test -s "/data/platform/${GOOD}.map" || fail "no source map"
echo "ok   the bundle and its map are in the platform's data dir"
disk test ! -e "/data/builds/$BUILD_ID" || fail "staging dir /data/builds/$BUILD_ID left behind"
[[ -z "$(docker ps -aq --filter "label=homeai.build=$BUILD_ID")" ]] || fail "builder containers left behind"
echo "ok   staging dir and builder containers removed"

echo "== 4. precise diagnostics"
set_index "missing import" <<'TSX'
import { Text } from 'react-native';
import { greet } from './greet';

export default function Index() {
  return <Text>{greet()}</Text>;
}
TSX
expect_diagnostic "missing import" "import app/index.tsx 2 23" "Could not resolve"

set_index "disallowed import" <<'TSX'
import { Text } from 'react-native';
import { readFileSync } from 'fs';

export default function Index() {
  return <Text>{String(readFileSync)}</Text>;
}
TSX
expect_diagnostic "disallowed import" "import app/index.tsx 2 30" 'Import "fs" is not allowed in apps'

set_index "type error" <<'TSX'
import { Text } from 'react-native';

export default function Index() {
  const count: number = 'three';
  return <Text>{count}</Text>;
}
TSX
expect_diagnostic "type error" "type app/index.tsx 4 9" "TS2322"

set_index "render throw" <<'TSX'
import { Text } from 'react-native';

export default function Index() {
  const items: string[] | undefined = undefined as string[] | undefined;
  return <Text>{items!.length}</Text>;
}
TSX
expect_diagnostic "render throw" "render app/index.tsx 5 24" "(on screen /"

expect 200 "$(put owner "$SRC/app/index.tsx" <"$FIXTURE/app/index.tsx")" "restore app/index.tsx"
r="$(put owner "$SRC/app.json" < <(sed 's/"sdk": "1"/"sdk": "2"/' "$FIXTURE/app.json"))"
expect 200 "$r" "upload app.json with sdk 2"
r="$(build owner)"
expect 200 "$r" "build (bad app.json)"
got="$(body_of "$r" | py 'x=d["diagnostics"]; assert d["build"] is None and len(x) == 1, x; x=x[0]; print(x["step"], x["file"], x["path"], "|", x["message"])')" ||
  fail "bad app.json: $(body_of "$r")"
[[ "$got" == 'manifest app.json /homeai/sdk | sdk must be one of: "1"' ]] || fail "bad app.json: $got"
echo "ok   bad app.json: $got"
[[ "$(bundle_path)" == "$GOOD" ]] || fail "bad app.json: bundle_path changed"
expect 200 "$(put owner "$SRC/app.json" <"$FIXTURE/app.json")" "restore app.json"

echo "== 5. outsider"
expect 404 "$(build outsider)" "build (outsider)" not_found

echo "== 6. rebuild"
r="$(build owner)"
expect 200 "$r" "rebuild hello"
NEW="$(body_of "$r" | py 'assert d["ok"], d; print(d["build"]["bundle_path"])')" || fail "rebuild: $(body_of "$r")"
[[ "$NEW" != "$GOOD" && "$(bundle_path)" == "$NEW" ]] || fail "rebuild bundle_path $NEW"
disk test -f "/data/platform/$NEW" || fail "no new bundle"
disk test ! -e "/data/platform/$(dirname "$GOOD")" || fail "old bundle $GOOD not removed"
echo "ok   $NEW replaced $GOOD, which is gone"
[[ -z "$(docker ps -aq --filter label=homeai.build)" ]] || fail "builder containers left behind"

echo "PASS: app build smoke"
