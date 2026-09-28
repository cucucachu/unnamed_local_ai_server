#!/usr/bin/env bash
# M13-01: live app source history against the running `platform` +
# `code-exec-manager` (docs/PLATFORM.md §7 "Source history").
#
# Needs the live stack and the builder image, which step 0 (re)builds from
# services/app-builder (cached). Requests go from a throwaway
# `curlimages/curl` container on `homeai_homeai-internal` straight to
# `platform:8100`, as in app_build_smoke.sh. Never completes bootstrap.
#
#   1. The recovery CLI creates users `e2e-ah-<rand>-{owner,outsider}`.
#   2. The owner uploads the fixture app (`fixtures/apps/hello/`) to
#      `/personal/Apps/hello` plus a planted git repo next to it: a
#      `.git/config` whose fsmonitor, hooks path and `evil` filter, and a
#      `.gitattributes` applying that filter, would each `touch` a marker
#      if git ever ran there. Control: plain `git add` over a copy of the
#      folder, in a throwaway `--network none` container of the platform
#      image, does create the marker (the plant is live).
#   3. Build, change app/index.tsx, build again: two commits, newest first,
#      the second one current; the repo is under /data/platform/app-git.
#      The outsider's history request is a 404.
#   4. Revert to the first commit: a third commit (kind `revert`, reverting
#      the first) is the new head and current; app/index.tsx is the
#      original again (read back through the files API); the app was
#      rebuilt (a new bundle).
#   5. The marker never appeared in the platform container, the planted
#      `.git/config` is byte-for-byte what was uploaded, and no commit's
#      tree has a dotfile.
# Everything it created (rows, directories, bundles, the repo) is deleted
# on exit.
#
# Usage: scripts/e2e/app_history_smoke.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

NETWORK="homeai_homeai-internal"
CURL_IMAGE="curlimages/curl:latest"
BASE="http://platform:8100"
API="$BASE/api/platform"
FIXTURE="$SCRIPT_DIR/fixtures/apps/hello"
PREFIX="e2e-ah-$(openssl rand -hex 3)"
ROLES=(owner outsider)
PASSWORD="$(openssl rand -hex 16)"
SRC=/personal/Apps/hello
MARK="homeai-e2e-git-marker-$(openssl rand -hex 6)"
APP_ID=""
env_value() { grep -E "^$1=" .env | tail -n1 | cut -d= -f2- || true; }
POSTGRES_USER="$(env_value POSTGRES_USER)"
POSTGRES_USER="${POSTGRES_USER:-homeai}"
SPACES_DIR="$(env_value SPACES_DIR)"

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

# Deleting the spaces cascades to their apps and versions; bundles and repos are files.
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
    docker compose exec -T platform rm -rf "/data/platform/app-bundles/$APP_ID" \
      "/data/platform/app-git/$APP_ID.git" || true
  fi
  docker compose exec -T platform rm -f "/tmp/$MARK" >/dev/null 2>&1 || true
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
download() { body_of "$(get "$1" "/files/download?path=$2")"; }
build() { post_json "$1" "/apps/$APP_ID/build" '{}'; }
history() { body_of "$(get owner "/apps/$APP_ID/history")"; }
git_ro() {
  disk env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    git -c "safe.directory=/data/platform/app-git/$APP_ID.git" \
    --git-dir="/data/platform/app-git/$APP_ID.git" "$@"
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
disk git --version >/dev/null || fail "no git in the platform image"
echo "ok   $(disk git --version | tr -d '\r')"

echo "== 1. CLI: users"
for role in "${ROLES[@]}"; do
  printf '%s\n' "$PASSWORD" | cli create-user "$PREFIX-$role" --password-stdin
  IDENT[$role]="$(identity_for "$PREFIX-$role")"
done

echo "== 2. upload the app and a planted git repo"
while IFS= read -r rel; do
  expect 200 "$(put owner "$SRC/$rel" <"$FIXTURE/$rel")" "upload $rel" >/dev/null
done < <(cd "$FIXTURE" && find . -type f | sed 's|^\./||' | sort)
echo "ok   uploaded the fixture"
for dir in .git/objects .git/refs/heads .git/hooks; do
  expect 201 "$(post_json owner /files/mkdir "{\"path\":\"$SRC/$dir\"}")" "mkdir $dir" >/dev/null
done
EVIL="touch /tmp/$MARK; cat"
CONFIG="[core]
	repositoryformatversion = 0
	bare = false
	fsmonitor = \"$EVIL\"
	hooksPath = .git/hooks
	sshCommand = \"$EVIL\"
[filter \"evil\"]
	clean = \"$EVIL\"
	smudge = \"$EVIL\"
	required = true
"
expect 200 "$(put owner "$SRC/.git/config" <<<"$CONFIG")" "plant .git/config"
expect 200 "$(put owner "$SRC/.git/HEAD" <<<"ref: refs/heads/main")" "plant .git/HEAD"
for hook in pre-commit post-commit reference-transaction post-index-change; do
  expect 200 "$(put owner "$SRC/.git/hooks/$hook" <<<"#!/bin/sh
touch /tmp/$MARK")" "plant hook $hook" >/dev/null
done
expect 200 "$(put owner "$SRC/.gitattributes" <<<"* filter=evil")" "plant .gitattributes"
PLANTED="$(download owner "$SRC/.git/config" | sha256sum)"

OWNER_SPACE="$(psql_q "SELECT s.id FROM spaces s JOIN users u ON u.id = s.owner_user_id
  WHERE u.username = '$PREFIX-owner' AND s.kind = 'personal'")"
[[ "$OWNER_SPACE" =~ ^[0-9a-f-]{36}$ ]] || fail "owner's personal space: $OWNER_SPACE"
[[ -n "$SPACES_DIR" ]] || fail "no SPACES_DIR in .env"
PLATFORM_IMAGE="$(docker inspect -f '{{.Image}}' "$(docker compose ps -q platform)")"
if docker run --rm --network none --read-only --tmpfs /tmp --tmpfs /w \
  -v "$SPACES_DIR/$OWNER_SPACE/files/Apps/hello:/src:ro" --entrypoint sh "$PLATFORM_IMAGE" \
  -c "cp -a /src/. /w/ && cd /w && git -c safe.directory='*' add -A </dev/null >/dev/null 2>&1; test -e /tmp/$MARK"; then
  echo "ok   control: git run the ordinary way over a copy of the folder fires the plant"
else
  fail "control: the planted repo didn't fire under plain git; the check below would prove nothing"
fi

echo "== 3. two builds"
r="$(post_json owner /apps "{\"source_path\":\"$SRC\"}")"
expect 201 "$r" "register $SRC"
APP_ID="$(body_of "$r" | py 'print(d["app"]["id"])')"
r="$(build owner)"
expect 200 "$r" "build 1"
read -r ok C1 _B1 < <(body_of "$r" | py 'b=d["build"]; print(d["ok"], b["commit"], b["bundle_path"])') ||
  fail "build 1: $(body_of "$r")"
[[ "$ok" == True && "$C1" =~ ^[0-9a-f]{40}$ ]] || fail "build 1: $(body_of "$r")"
echo "ok   build 1 committed $C1"
disk test -f "/data/platform/app-git/$APP_ID.git/HEAD" || fail "no repo under /data/platform/app-git"
echo "ok   the repo is /data/platform/app-git/$APP_ID.git"

expect 200 "$(put owner "$SRC/app/index.tsx" <<'TSX'
import { Text } from 'react-native';

export default function Index() {
  return <Text>Changed</Text>;
}
TSX
)" "change app/index.tsx"
r="$(build owner)"
expect 200 "$r" "build 2"
read -r C2 B2 < <(body_of "$r" | py 'assert d["ok"], d; print(d["build"]["commit"], d["build"]["bundle_path"])') ||
  fail "build 2: $(body_of "$r")"
[[ "$C2" =~ ^[0-9a-f]{40}$ && "$C2" != "$C1" ]] || fail "build 2 commit $C2"
got="$(history | py 'print(" ".join(f"{c["id"]}:{c["kind"]}:{c["current"]}" for c in d["commits"]), d["next_offset"])')"
[[ "$got" == "$C2:build:True $C1:build:False None" ]] || fail "history after two builds: $got"
echo "ok   history: $C2 (current), $C1"
expect 404 "$(get outsider "/apps/$APP_ID/history")" "history (outsider)" not_found

echo "== 4. revert to the first build"
r="$(post_json owner "/apps/$APP_ID/revert" "{\"commit\":\"$C1\"}")"
expect 200 "$r" "revert to ${C1:0:12}"
read -r ok C3 BC3 B3 < <(body_of "$r" | py 'print(d["ok"], d["commit"], d["build"]["commit"], d["build"]["bundle_path"])') ||
  fail "revert: $(body_of "$r")"
[[ "$ok" == True && "$C3" =~ ^[0-9a-f]{40}$ && "$C3" != "$C2" && "$BC3" == "$C3" ]] ||
  fail "revert: $(body_of "$r")"
[[ "$B3" != "$B2" ]] && disk test -f "/data/platform/$B3" || fail "revert didn't rebuild: $B3"
echo "ok   reverted as $C3 and rebuilt ($B3)"
got="$(history | py 'c=d["commits"]; print(len(c), c[0]["id"], c[0]["kind"], c[0]["reverts"], c[0]["current"], c[0]["parent"])')"
[[ "$got" == "3 $C3 revert $C1 True $C2" ]] || fail "history after revert: $got"
echo "ok   history: $C3 reverts $C1, on top of $C2"
cmp -s <(download owner "$SRC/app/index.tsx") "$FIXTURE/app/index.tsx" ||
  fail "app/index.tsx wasn't restored"
echo "ok   app/index.tsx is the original again"
[[ "$(git_ro rev-parse "$C3^{tree}" | tr -d '\r')" == "$(git_ro rev-parse "$C1^{tree}" | tr -d '\r')" ]] ||
  fail "the revert's tree isn't the first build's"
echo "ok   the revert's tree is the first build's"

echo "== 5. the planted repo stayed inert"
disk test ! -e "/tmp/$MARK" || fail "the planted marker appeared in the platform container"
echo "ok   no marker in the platform container"
[[ "$(download owner "$SRC/.git/config" | sha256sum)" == "$PLANTED" ]] || fail ".git/config changed"
echo "ok   .git/config untouched"
for c in "$C1" "$C2" "$C3"; do
  if git_ro ls-tree -r --name-only "$c" | grep -Eq '(^|/)\.'; then
    fail "commit $c has a dotfile"
  fi
done
echo "ok   no commit has a dotfile"

echo "PASS: app history smoke"
