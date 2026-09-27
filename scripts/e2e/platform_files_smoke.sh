#!/usr/bin/env bash
# M11-01: live platform files API over virtual paths against the running `platform`.
#
# Like platform_spaces_smoke.sh, requests go from a throwaway `curlimages/curl`
# container on `homeai_homeai-internal` straight to `platform:8100`, playing
# Caddy's part (verify -> X-HomeAI-Identity) by hand. Never completes
# bootstrap.
#
#   1. The recovery CLI creates users `e2e-pf-<rand>-{owner,editor,viewer,outsider}`
#      and shared spaces `e2e-pf-<rand>` (owner/editor/viewer; the outsider is
#      not a member) and `e2e-pf-<rand>-t` (owner, plus the viewer as an
#      editor). The owner uploads `seed.txt` into the first one.
#   2. Role matrix on the first space: list, stat, download and Range stream
#      are 200/206 for every member; upload, mkdir, copy, rename and delete
#      succeed for owner and editor, are 403 insufficient_role for the viewer,
#      and 404 not_found for the outsider — the same 404 a slug that doesn't
#      exist gets.
#   3. On disk: the editor's upload is `<editor uid>:<space gid>` 0660, its
#      folder 2770.
#   4. Cross-space: the owner moves a file into the second space (it's then
#      group-owned by that space); the editor (not a member there) gets 404,
#      and the viewer (editor there, viewer here) gets 403 moving or copying
#      in either direction.
#   5. Range: 206 with the right slice and Content-Range, 416 past the end,
#      HEAD with the length.
#   6. Guards: a symlink planted in one space's files/ pointing into the
#      other's is 422 invalid_path, so is `..`; `/spaces` is read-only; an unknown top level
#      is 404.
# Everything it created (rows and directories) is deleted on exit.
#
# Usage: scripts/e2e/platform_files_smoke.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

NETWORK="homeai_homeai-internal"
CURL_IMAGE="curlimages/curl:latest"
BASE="http://platform:8100"
FILES="$BASE/api/platform/files"
PREFIX="e2e-pf-$(openssl rand -hex 3)"
SPACE="$PREFIX"
OTHER="$PREFIX-t"
ROLES=(owner editor viewer outsider)
PASSWORD="$(openssl rand -hex 16)"
SEED="0123456789abcdef"
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

cleanup() {
  local ids users
  users="$(user_list)"
  ids="$(psql_q "SELECT s.id FROM spaces s LEFT JOIN users u ON u.id = s.owner_user_id
    WHERE s.slug IN ('$SPACE', '$OTHER') OR u.username IN ($users)" 2>/dev/null || true)"
  psql_q "DELETE FROM spaces s USING users u WHERE u.id = s.owner_user_id AND u.username IN ($users);
    DELETE FROM spaces WHERE slug IN ('$SPACE', '$OTHER');
    DELETE FROM users WHERE username IN ($users)" >/dev/null 2>&1 || true
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && docker compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
}
trap cleanup EXIT

curl_i() {
  docker run --rm -i --network "$NETWORK" "$CURL_IMAGE" -sS -i --max-time 20 "$@"
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
    -d "{\"username\":\"$1\",\"password\":\"$PASSWORD\"}" "$BASE/api/auth/login" </dev/null)"
  [[ "$(status_of "$r")" == 200 ]] || fail "login $1: $(body_of "$r")"
  token="$(body_of "$r" | py 'print(d["session_token"])')"
  r="$(curl_i -H "Authorization: Bearer $token" "$BASE/internal/auth/verify" </dev/null)"
  [[ "$(status_of "$r")" == 200 ]] || fail "verify $1"
  header_of "$r" x-homeai-identity
}

cli() { docker compose exec -T platform python -m app.cli "$@"; }

declare -A IDENT
# $1 role, then curl args; stdin is the request body for uploads.
as() {
  local role="$1"
  shift
  curl_i -H "X-HomeAI-Identity: ${IDENT[$role]}" "$@"
}
get() { as "$1" -G --data-urlencode "path=$3" "$FILES$2" </dev/null; }
post_json() { as "$1" -H 'Content-Type: application/json' -d "$3" "$FILES$2" </dev/null; }
upload() { printf '%s' "$4" | as "$1" -F "path=$2" -F "file=@-;filename=$3" "$FILES/upload"; }
delete() { as "$1" -X DELETE -G --data-urlencode "path=$2" "$FILES" </dev/null; }
disk() { docker compose exec -T platform "$@"; }

echo "== waiting for platform health"
for _ in $(seq 1 30); do
  if disk python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8100/internal/health', timeout=3)" \
    >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "== 1. CLI: users, spaces, memberships; seed"
for role in "${ROLES[@]}"; do
  printf '%s\n' "$PASSWORD" | cli create-user "$PREFIX-$role" --password-stdin
done
cli create-space "$SPACE" --name "E2E Files" --owner "$PREFIX-owner"
cli add-member "$SPACE" "$PREFIX-editor" --role editor
cli add-member "$SPACE" "$PREFIX-viewer" --role viewer
cli create-space "$OTHER" --name "E2E Files T" --owner "$PREFIX-owner"
cli add-member "$OTHER" "$PREFIX-viewer" --role editor
spaces_json="$(cli list-spaces --json)"
space_field() { py "print(next(s for s in d if s['slug'] == '$1')['$2'])" <<<"$spaces_json"; }
S_ID="$(space_field "$SPACE" id)"
S_GID="$(space_field "$SPACE" gid)"
T_ID="$(space_field "$OTHER" id)"
T_GID="$(space_field "$OTHER" gid)"
EDITOR_UID="$(psql_q "SELECT uid FROM users WHERE username = '$PREFIX-editor'")"
for role in "${ROLES[@]}"; do IDENT[$role]="$(identity_for "$PREFIX-$role")"; done
S="/spaces/$SPACE"
T="/spaces/$OTHER"
expect 201 "$(upload owner "$S" seed.txt "$SEED")" "upload $S/seed.txt (owner)"

echo "== 2. role matrix on $S"
for role in "${ROLES[@]}"; do
  case "$role" in
    owner | editor) read_ok=1 write_ok=1 ;;
    viewer) read_ok=1 write_ok=0 ;;
    outsider) read_ok=0 write_ok=0 ;;
  esac
  if ((read_ok)); then
    r="$(get "$role" "" "$S")"
    expect 200 "$r" "list $S ($role)"
    if ((write_ok)); then writable=True; else writable=False; fi
    [[ "$(body_of "$r" | py 'print(d["role"], d["writable"])')" == "$role $writable" ]] ||
      fail "list $S ($role): role/writable $(body_of "$r")"
    expect 200 "$(get "$role" /stat "$S/seed.txt")" "stat seed.txt ($role)"
    r="$(get "$role" /download "$S/seed.txt")"
    expect 200 "$r" "download seed.txt ($role)"
    [[ "$(body_of "$r")" == "$SEED" ]] || fail "download seed.txt ($role): $(body_of "$r")"
    expect 206 "$(as "$role" -H 'Range: bytes=0-3' -G --data-urlencode "path=$S/seed.txt" "$FILES/stream" </dev/null)" \
      "stream seed.txt bytes=0-3 ($role)"
  else
    expect 404 "$(get "$role" "" "$S")" "list $S ($role)" not_found
    expect 404 "$(get "$role" /stat "$S/seed.txt")" "stat seed.txt ($role)" not_found
    expect 404 "$(get "$role" /download "$S/seed.txt")" "download seed.txt ($role)" not_found
    expect 404 "$(as "$role" -G --data-urlencode "path=$S/seed.txt" "$FILES/stream" </dev/null)" \
      "stream seed.txt ($role)" not_found
  fi

  if ((write_ok)); then
    expect 201 "$(upload "$role" "$S" "up-$role.txt" "by $role")" "upload up-$role.txt ($role)"
    expect 201 "$(post_json "$role" /mkdir "{\"path\":\"$S/dir-$role\"}")" "mkdir dir-$role ($role)"
    expect 200 "$(post_json "$role" /copy "{\"src\":\"$S/seed.txt\",\"dst\":\"$S/copy-$role.txt\"}")" \
      "copy seed.txt -> copy-$role.txt ($role)"
    expect 200 "$(post_json "$role" /rename "{\"path\":\"$S/copy-$role.txt\",\"name\":\"renamed-$role.txt\"}")" \
      "rename copy-$role.txt ($role)"
    expect 204 "$(delete "$role" "$S/renamed-$role.txt")" "delete renamed-$role.txt ($role)"
  else
    if [[ "$role" == viewer ]]; then want=403 detail=insufficient_role; else want=404 detail=not_found; fi
    expect "$want" "$(upload "$role" "$S" "up-$role.txt" "by $role")" "upload ($role)" "$detail"
    expect "$want" "$(post_json "$role" /mkdir "{\"path\":\"$S/dir-$role\"}")" "mkdir ($role)" "$detail"
    expect "$want" "$(post_json "$role" /copy "{\"src\":\"$S/seed.txt\",\"dst\":\"$S/copy-$role.txt\"}")" \
      "copy ($role)" "$detail"
    expect "$want" "$(post_json "$role" /rename "{\"path\":\"$S/seed.txt\",\"name\":\"x.txt\"}")" \
      "rename seed.txt ($role)" "$detail"
    expect "$want" "$(delete "$role" "$S/seed.txt")" "delete seed.txt ($role)" "$detail"
  fi
done
expect 200 "$(get owner /stat "$S/seed.txt")" "seed.txt survived the matrix"

r1="$(get outsider "" "$S")"
r2="$(get outsider "" "/spaces/$PREFIX-nope")"
[[ "$(status_of "$r1") $(body_of "$r1")" == "$(status_of "$r2") $(body_of "$r2")" ]] ||
  fail "outsider: member space and unknown slug differ: $(body_of "$r1") vs $(body_of "$r2")"
echo "ok   outsider: $S and an unknown slug are indistinguishable"
r="$(get outsider "" /spaces)"
expect 200 "$r" "list /spaces (outsider)"
body_of "$r" | py "assert '$SPACE' not in [e['name'] for e in d['entries']], d" || fail "outsider sees $SPACE"
r="$(get viewer "" /spaces)"
[[ "$(body_of "$r" | py "print(sorted(e['name'] for e in d['entries'] if e['name'].startswith('$PREFIX')))")" == "['$SPACE', '$OTHER']" ]] ||
  fail "viewer's /spaces: $(body_of "$r")"
echo "ok   /spaces lists exactly the caller's shared spaces"

echo "== 3. ownership on disk"
read -r owner mode < <(disk stat -c '%u:%g %a' "/data/spaces/$S_ID/files/up-editor.txt")
[[ "$owner $mode" == "$EDITOR_UID:$S_GID 660" ]] || fail "up-editor.txt is $owner $mode, want $EDITOR_UID:$S_GID 660"
echo "ok   up-editor.txt $owner $mode"
read -r owner mode < <(disk stat -c '%u:%g %a' "/data/spaces/$S_ID/files/dir-editor")
[[ "$owner $mode" == "$EDITOR_UID:$S_GID 2770" ]] || fail "dir-editor is $owner $mode, want $EDITOR_UID:$S_GID 2770"
echo "ok   dir-editor $owner $mode"

echo "== 4. cross-space move/copy"
expect 201 "$(upload owner "$S" move-me.txt "moving")" "upload move-me.txt (owner)"
expect 200 "$(post_json owner /move "{\"src\":\"$S/move-me.txt\",\"dst\":\"$T/move-me.txt\"}")" \
  "move $S -> $T (owner of both)"
expect 404 "$(get owner /stat "$S/move-me.txt")" "move-me.txt gone from $S" not_found
r="$(get owner /download "$T/move-me.txt")"
[[ "$(status_of "$r") $(body_of "$r")" == "200 moving" ]] || fail "move-me.txt in $T: $r"
group="$(disk stat -c '%g' "/data/spaces/$T_ID/files/move-me.txt")"
[[ "$group" == "$T_GID" ]] || fail "moved file group $group, want $T_GID"
echo "ok   moved file is in $T, group $T_GID"
expect 404 "$(post_json editor /move "{\"src\":\"$S/up-editor.txt\",\"dst\":\"$T/up-editor.txt\"}")" \
  "move $S -> $T (editor here, not a member there)" not_found
expect 403 "$(post_json viewer /move "{\"src\":\"$S/seed.txt\",\"dst\":\"$T/seed.txt\"}")" \
  "move $S -> $T (viewer here, editor there)" insufficient_role
expect 403 "$(post_json viewer /copy "{\"src\":\"$S/seed.txt\",\"dst\":\"$T/seed.txt\"}")" \
  "copy $S -> $T (viewer here, editor there)" insufficient_role
expect 403 "$(post_json viewer /move "{\"src\":\"$T/move-me.txt\",\"dst\":\"$S/move-me.txt\"}")" \
  "move $T -> $S (editor there, viewer here)" insufficient_role
expect 200 "$(post_json editor /copy "{\"src\":\"$S/seed.txt\",\"dst\":\"/personal/seed.txt\"}")" \
  "copy $S -> /personal (editor)"

echo "== 5. Range"
r="$(as viewer -H 'Range: bytes=2-5' -G --data-urlencode "path=$S/seed.txt" "$FILES/stream" </dev/null)"
expect 206 "$r" "stream bytes=2-5"
[[ "$(body_of "$r")" == "2345" && "$(header_of "$r" content-range)" == "bytes 2-5/16" &&
  "$(header_of "$r" accept-ranges)" == bytes ]] ||
  fail "bytes=2-5: body '$(body_of "$r")' content-range '$(header_of "$r" content-range)'"
echo "     body 2345, Content-Range bytes 2-5/16"
r="$(as viewer -H 'Range: bytes=100-200' -G --data-urlencode "path=$S/seed.txt" "$FILES/stream" </dev/null)"
expect 416 "$r" "stream bytes=100-200"
[[ "$(header_of "$r" content-range)" == "bytes */16" ]] || fail "416 content-range: $(header_of "$r" content-range)"
r="$(as viewer -I -G --data-urlencode "path=$S/seed.txt" "$FILES/stream" </dev/null)"
expect 200 "$r" "HEAD stream"
[[ "$(header_of "$r" content-length)" == 16 ]] || fail "HEAD content-length: $(header_of "$r" content-length)"

echo "== 6. guards"
disk ln -s "/data/spaces/$T_ID/files/move-me.txt" "/data/spaces/$S_ID/files/escape"
expect 422 "$(get owner /download "$S/escape")" "download a symlink into $T" invalid_path
expect 422 "$(as owner -G --data-urlencode "path=$S/escape" "$FILES/stream" </dev/null)" \
  "stream a symlink into $T" invalid_path
expect 422 "$(get owner "" "$S/../$OTHER")" "list $S/../$OTHER" invalid_path
expect 403 "$(post_json owner /mkdir '{"path":"/spaces"}')" "mkdir /spaces (synthetic)" read_only
expect 404 "$(get owner "" /elsewhere)" "list /elsewhere" not_found

echo "PASS: platform files smoke"
