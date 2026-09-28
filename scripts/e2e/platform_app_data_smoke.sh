#!/usr/bin/env bash
# M12-03: live app data (per-instance SQLite, migrations, RPC, change events)
# against the running `platform` (and `code-exec-manager` for step 8).
#
# Step 0 (re)builds the builder image like app_build_smoke.sh (cached; its
# `npm ci` needs the internet the first time).
# Like platform_apps_smoke.sh, requests go from a throwaway `curlimages/curl`
# container on `homeai_homeai-internal` straight to `platform:8100`, playing
# Caddy's part (verify -> X-HomeAI-Identity) by hand. Never completes
# bootstrap.
#
#   1. The recovery CLI creates users `e2e-pad-<rand>-{owner,viewer,outsider}`
#      and a shared space `e2e-pad-<rand>` (owner + viewer).
#   2. The owner uploads the fixture app (`fixtures/apps/hello/`) to the
#      space's `Apps/hello`, registers it and installs it there.
#   3. `migrate` applies `schema.sql` (one additive step); again is up_to_date.
#   4. RPC: the owner `run`s an insert and the `addGreeting` action (`:text`),
#      the viewer `getAll`s both rows; a `db_changed` event reaches the
#      viewer's `/ws/platform/events` socket.
#   5. Schema changes: adding a column is `additive` and applies at once (with
#      a snapshot); dropping it is `destructive` and stays `pending` until the
#      owner approves (the viewer can't), then applies with a snapshot on disk.
#   6. The viewer's writes are refused (403 insufficient_role, and 422
#      sql_not_allowed for a write smuggled into getAll); the outsider gets
#      404 on every route; ATTACH is 422 sql_not_allowed.
#   7. `apps/<instance_id>/` is `0:<gid>` 2750, and `ro/data.sqlite` (0444) is
#      readable, with the current rows, by an exec-shaped container (member
#      uid, space gid, `--network none`, only `ro/` mounted read-only).
#   8. Build → data: with a column added to the source's schema.sql, `POST
#      /apps/{id}/build` succeeds, its `migrations` show the instance's
#      additive migration applied, the column exists, and the viewer's
#      socket gets `app_built` for the app. Dropping the column again and
#      building leaves a `pending` destructive migration, in the build
#      response and in the instance's migration list, and the column stays.
# Everything it created (rows and directories) is deleted on exit.
#
# Usage: scripts/e2e/platform_app_data_smoke.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

NETWORK="homeai_homeai-internal"
CURL_IMAGE="curlimages/curl:latest"
EXEC_IMAGE="python:3.12-slim"
BASE="http://platform:8100"
API="$BASE/api/platform"
FIXTURE="$SCRIPT_DIR/fixtures/apps/hello"
PREFIX="e2e-pad-$(openssl rand -hex 3)"
SPACE="$PREFIX"
APP_ID=""
ROLES=(owner viewer outsider)
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

user_list() {
  local r out=""
  for r in "${ROLES[@]}"; do out+="${out:+, }'$PREFIX-$r'"; done
  echo "$out"
}

# Deleting the spaces cascades to their apps, instances and migrations.
cleanup() {
  local ids users
  users="$(user_list)"
  ids="$(psql_q "SELECT s.id FROM spaces s LEFT JOIN users u ON u.id = s.owner_user_id
    WHERE s.slug = '$SPACE' OR u.username IN ($users)" 2>/dev/null || true)"
  psql_q "DELETE FROM spaces WHERE slug = '$SPACE';
    DELETE FROM spaces s USING users u WHERE u.id = s.owner_user_id AND u.username IN ($users);
    DELETE FROM users WHERE username IN ($users)" >/dev/null 2>&1 || true
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && docker compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  if [[ "$APP_ID" =~ ^[0-9a-f-]{36}$ ]]; then
    docker compose exec -T platform rm -rf "/data/platform/app-bundles/$APP_ID" || true
  fi
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
field() { body_of "$1" | py "print($2)"; }

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
post_json() { as "$1" -H 'Content-Type: application/json' -d "$3" "$API$2" </dev/null; }
put_text() {
  as "$1" -X PUT -H 'Content-Type: application/octet-stream' --data-binary @- \
    "$API/files/content?path=$2"
}
rpc() { post_json "$1" "/apps/instances/$IID/rpc" "$2"; }

echo "== 0. builder image"
bash services/app-builder/build-builder-image.sh >/dev/null

echo "== waiting for platform health"
for _ in $(seq 1 30); do
  if disk python -c \
    "import urllib.request; urllib.request.urlopen('http://localhost:8100/internal/health', timeout=3)" \
    >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

echo "== 1. CLI: users and a shared space"
for role in "${ROLES[@]}"; do
  printf '%s\n' "$PASSWORD" | cli create-user "$PREFIX-$role" --password-stdin
  IDENT[$role]="$(identity_for "$PREFIX-$role")"
done
cli create-space "$SPACE" --name "E2E App Data" --owner "$PREFIX-owner"
cli add-member "$SPACE" "$PREFIX-viewer" --role viewer
spaces_json="$(cli list-spaces --json)"
space_field() { py "print(next(s for s in d if s['slug'] == '$1')['$2'])" <<<"$spaces_json"; }
S_ID="$(space_field "$SPACE" id)"
S_GID="$(space_field "$SPACE" gid)"
VIEWER_UID="$(cli list-users --json | py "print(next(u['uid'] for u in d if u['username'] == '$PREFIX-viewer'))")"

echo "== 2. register + install /spaces/$SPACE/Apps/hello"
SRC="/spaces/$SPACE/Apps/hello"
while IFS= read -r rel; do
  expect 200 "$(put_text owner "$SRC/$rel" <"$FIXTURE/$rel")" "upload $rel"
done < <(cd "$FIXTURE" && find . -type f | sed 's|^\./||' | sort)
r="$(post_json owner /apps "{\"source_path\":\"$SRC\"}")"
expect 201 "$r" "register"
APP_ID="$(field "$r" 'd["app"]["id"]')"
r="$(post_json owner "/spaces/$S_ID/instances" "{\"app_id\":\"$APP_ID\"}")"
expect 201 "$r" "install"
IID="$(field "$r" 'd["id"]')"
INST="/data/spaces/$S_ID/apps/$IID"

echo "== 3. migrate schema.sql"
r="$(post_json owner "/apps/instances/$IID/migrate" '{}')"
expect 200 "$r" "migrate"
[[ "$(field "$r" 'd["status"], sorted(d["summary"].items()), [s["op"] for s in d["steps"]]')" == \
  "applied [('additive', 1), ('destructive', 0), ('safe', 0)] ['create_table']" ]] ||
  fail "first migrate: $(body_of "$r")"
r="$(post_json owner "/apps/instances/$IID/migrate" '{}')"
[[ "$(field "$r" 'd["status"]')" == up_to_date ]] || fail "second migrate: $(body_of "$r")"
echo "ok   applied, then up_to_date"

echo "== 4. RPC + events"
r="$(rpc owner '{"op":"run","sql":"INSERT INTO greetings (text) VALUES (?)","params":["hello"]}')"
expect 200 "$r" "run (owner)"
[[ "$(field "$r" 'd["changes"], d["lastInsertRowId"]')" == "1 1" ]] || fail "run: $(body_of "$r")"
r="$(rpc owner '{"op":"action","name":"addGreeting","params":{"text":"hi there"}}')"
expect 200 "$r" "action addGreeting (owner)"
[[ "$(field "$r" 'd["changes"], d["lastInsertRowId"]')" == "1 2" ]] || fail "action: $(body_of "$r")"
r="$(rpc viewer '{"op":"getAll","sql":"SELECT id, text FROM greetings ORDER BY id"}')"
expect 200 "$r" "getAll (viewer)"
[[ "$(field "$r" 'd["rows"]')" == "[{'id': 1, 'text': 'hello'}, {'id': 2, 'text': 'hi there'}]" ]] ||
  fail "getAll: $(body_of "$r")"
r="$(rpc viewer '{"op":"getFirst","sql":"SELECT count(*) AS n FROM greetings"}')"
[[ "$(field "$r" 'd["row"]')" == "{'n': 2}" ]] || fail "getFirst: $(body_of "$r")"
echo "ok   getFirst (viewer) -> 2 rows"
# The viewer's socket and the owner's write, from inside the platform container
# (its venv has the `websockets` client).
docker compose exec -T -e VIEWER="${IDENT[viewer]}" -e OWNER="${IDENT[owner]}" -e IID="$IID" \
  platform python - <<'EOF' || fail "db_changed event"
import json, os, urllib.request
from websockets.sync.client import connect

base = "localhost:8100"
with connect(f"ws://{base}/ws/platform/events",
             additional_headers={"X-HomeAI-Identity": os.environ["VIEWER"]}) as ws:
    assert json.loads(ws.recv(timeout=5)) == {"type": "ready"}
    req = urllib.request.Request(
        f"http://{base}/api/platform/apps/instances/{os.environ['IID']}/rpc",
        data=json.dumps({"op": "run", "sql": "INSERT INTO greetings (text) VALUES ('ws')"}).encode(),
        headers={"X-HomeAI-Identity": os.environ["OWNER"], "Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=10).read()
    event = json.loads(ws.recv(timeout=5))
    assert event == {"type": "db_changed", "instance_id": os.environ["IID"]}, event
    print("ok   viewer's /ws/platform/events got", event)
with connect(f"ws://{base}/ws/platform/events") as ws:
    try:
        ws.recv(timeout=5)
    except Exception as exc:
        assert getattr(exc, "rcvd", None) and exc.rcvd.code == 4401, exc
        print("ok   no credential -> close 4401")
    else:
        raise AssertionError("an unauthenticated socket got a message")
EOF

echo "== 5. schema changes"
sed 's/  text TEXT NOT NULL/  text TEXT NOT NULL,\n  lang TEXT/' "$FIXTURE/schema.sql" |
  put_text owner "$SRC/schema.sql" >/dev/null
r="$(post_json owner "/apps/instances/$IID/migrate" '{}')"
expect 200 "$r" "migrate (add column)"
[[ "$(field "$r" 'd["status"], d["summary"]["additive"], d["needs_approval"], bool(d["snapshot"])')" == \
  "applied 1 False True" ]] || fail "additive: $(body_of "$r")"
echo "ok   additive: $(field "$r" 'd["steps"][0]["reason"]') (snapshot $(field "$r" 'd["snapshot"]'))"
put_text owner "$SRC/schema.sql" <"$FIXTURE/schema.sql" >/dev/null
r="$(post_json owner "/apps/instances/$IID/migrate" '{}')"
expect 200 "$r" "migrate (drop column)"
[[ "$(field "$r" 'd["status"], d["summary"]["destructive"], d["needs_approval"]')" == \
  "pending 1 True" ]] || fail "destructive: $(body_of "$r")"
MID="$(field "$r" 'd["id"]')"
echo "ok   destructive: $(field "$r" 'd["steps"][0]["reason"]') -> pending"
r="$(rpc viewer '{"op":"getAll","sql":"SELECT name FROM pragma_table_info('"'"'greetings'"'"')"}')"
[[ "$(field "$r" '[c["name"] for c in d["rows"]]')" == "['id', 'text', 'lang']" ]] ||
  fail "column dropped before approval: $(body_of "$r")"
echo "ok   still has 'lang' until approved"
expect 403 "$(post_json viewer "/apps/instances/$IID/migrations/$MID/approve" '{}')" \
  "approve (viewer)" insufficient_role
r="$(post_json owner "/apps/instances/$IID/migrations/$MID/approve" '{}')"
expect 200 "$r" "approve (owner)"
[[ "$(field "$r" 'd["status"]')" == applied ]] || fail "approve: $(body_of "$r")"
SNAP="$(field "$r" 'd["snapshot"]')"
disk test -f "$INST/snapshots/$SNAP" || fail "snapshot $SNAP not on disk"
echo "ok   applied; snapshots/$SNAP on disk"
r="$(rpc viewer '{"op":"getAll","sql":"SELECT count(*) AS n FROM greetings"}')"
[[ "$(field "$r" 'd["rows"][0]["n"]')" == 3 ]] || fail "rows after migrations: $(body_of "$r")"
echo "ok   3 rows kept"

echo "== 6. roles and scope"
expect 403 "$(rpc viewer '{"op":"run","sql":"DELETE FROM greetings"}')" "run (viewer)" insufficient_role
expect 403 "$(rpc viewer '{"op":"action","name":"addGreeting","params":{"text":"x"}}')" \
  "action (viewer)" insufficient_role
expect 422 "$(rpc viewer '{"op":"getAll","sql":"DELETE FROM greetings RETURNING id"}')" \
  "write inside getAll (viewer)" sql_not_allowed
expect 403 "$(post_json viewer "/apps/instances/$IID/migrate" '{}')" "migrate (viewer)" insufficient_role
expect 404 "$(rpc outsider '{"op":"getAll","sql":"SELECT 1"}')" "getAll (outsider)" not_found
expect 404 "$(post_json outsider "/apps/instances/$IID/migrate" '{}')" "migrate (outsider)" not_found
expect 404 "$(as outsider "$API/apps/instances/$IID/migrations" </dev/null)" "migrations (outsider)" not_found
expect 422 "$(rpc owner '{"op":"run","sql":"ATTACH '"'"'/data/platform/x.db'"'"' AS x"}')" \
  "ATTACH (owner)" sql_not_allowed
expect 422 "$(rpc owner '{"op":"run","sql":"VACUUM INTO '"'"'/tmp/x.db'"'"'"}')" \
  "VACUUM INTO (owner)" sql_not_allowed

echo "== 7. on disk, and the read-only copy for exec"
read -r owner mode < <(disk stat -c '%u:%g %a' "$INST")
[[ "$owner $mode" == "0:$S_GID 2750" ]] || fail "instance dir is $owner $mode, want 0:$S_GID 2750"
echo "ok   apps/$IID is $owner $mode"
read -r owner mode < <(disk stat -c '%u:%g %a' "$INST/data.sqlite")
[[ "$owner $mode" == "0:$S_GID 600" ]] || fail "data.sqlite is $owner $mode, want 0:$S_GID 600"
echo "ok   data.sqlite is $owner $mode"
sleep 2 # at most one publish per second, trailing
read -r owner mode < <(disk stat -c '%u:%g %a' "$INST/ro/data.sqlite")
[[ "$owner $mode" == "0:$S_GID 444" ]] || fail "ro/data.sqlite is $owner $mode, want 0:$S_GID 444"
echo "ok   ro/data.sqlite is $owner $mode"
got="$(docker run --rm --network none --read-only --cap-drop ALL \
  --user "$VIEWER_UID:$S_GID" \
  --mount "type=bind,source=$SPACES_DIR/$S_ID/apps/$IID/ro,target=/app-data,readonly" \
  "$EXEC_IMAGE" python -c "
import sqlite3
con = sqlite3.connect('file:/app-data/data.sqlite?mode=ro&immutable=1', uri=True)
print(con.execute('SELECT count(*) FROM greetings').fetchone()[0])
try:
    open('/app-data/x', 'w')
    print('writable')
except OSError:
    print('read-only')
")"
[[ "$got" == $'3\nread-only' ]] || fail "exec-shaped reader got: $got"
echo "ok   exec-shaped container (uid $VIEWER_UID, gid $S_GID, ro/ only) reads 3 rows, can't write"

echo "== 8. build -> data"
columns() {
  field "$(rpc viewer '{"op":"getAll","sql":"SELECT name FROM pragma_table_info('"'"'greetings'"'"')"}')" \
    '[c["name"] for c in d["rows"]]'
}
sed 's/  text TEXT NOT NULL/  text TEXT NOT NULL,\n  mood TEXT/' "$FIXTURE/schema.sql" |
  put_text owner "$SRC/schema.sql" >/dev/null
# The build from inside the platform container, with the viewer's socket open.
docker compose exec -T -e VIEWER="${IDENT[viewer]}" -e OWNER="${IDENT[owner]}" -e IID="$IID" \
  -e APP_ID="$APP_ID" platform python - <<'PY' || fail "build -> additive migration + app_built"
import json, os, urllib.request
from websockets.sync.client import connect

base = "localhost:8100"
with connect(f"ws://{base}/ws/platform/events",
             additional_headers={"X-HomeAI-Identity": os.environ["VIEWER"]}) as ws:
    assert json.loads(ws.recv(timeout=5)) == {"type": "ready"}
    req = urllib.request.Request(
        f"http://{base}/api/platform/apps/{os.environ['APP_ID']}/build", data=b"{}",
        headers={"X-HomeAI-Identity": os.environ["OWNER"], "Content-Type": "application/json"},
    )
    body = json.loads(urllib.request.urlopen(req, timeout=600).read())
    assert body["ok"] is True, body["diagnostics"]
    [result] = body["migrations"]
    migration = result["migration"]
    assert result["instance_id"] == os.environ["IID"] and result["error"] is None, result
    assert (migration["status"], [s["reason"] for s in migration["steps"]]) == \
        ("applied", ["new column mood"]), migration
    print(f"ok   build {body['build']['id']} -> ok; instance migration applied (new column mood)")
    while (event := json.loads(ws.recv(timeout=10)))["type"] != "app_built":
        assert event == {"type": "db_changed", "instance_id": os.environ["IID"]}, event
    assert event == {"type": "app_built", "app_id": os.environ["APP_ID"], "version": "1.0.0"}, event
    print("ok   viewer's /ws/platform/events got", event)
PY
[[ "$(columns)" == "['id', 'text', 'mood']" ]] || fail "columns after build: $(columns)"
echo "ok   instance DB has 'mood'"
put_text owner "$SRC/schema.sql" <"$FIXTURE/schema.sql" >/dev/null
r="$(docker run --rm -i --network "$NETWORK" "$CURL_IMAGE" -sS -i --max-time 600 \
  -H "X-HomeAI-Identity: ${IDENT[owner]}" -H 'Content-Type: application/json' -d '{}' \
  "$API/apps/$APP_ID/build" </dev/null)"
expect 200 "$r" "build (drop column)"
m='d["migrations"][0]["migration"]'
[[ "$(field "$r" "d['ok'], ${m}['status'], ${m}['summary']['destructive']")" == "True pending 1" ]] ||
  fail "destructive build: $(body_of "$r")"
MID="$(field "$r" "${m}['id']")"
echo "ok   destructive: $(field "$r" "${m}['steps'][0]['reason']") -> pending in the build response"
r="$(as owner "$API/apps/instances/$IID/migrations" </dev/null)"
[[ "$(field "$r" 'd["migrations"][0]["id"], d["migrations"][0]["status"]')" == "$MID pending" ]] ||
  fail "migration list: $(body_of "$r")"
echo "ok   listed as the instance's pending migration"
[[ "$(columns)" == "['id', 'text', 'mood']" ]] || fail "column dropped without approval: $(columns)"
echo "ok   'mood' kept until approved"

echo "PASS: platform app data smoke"
