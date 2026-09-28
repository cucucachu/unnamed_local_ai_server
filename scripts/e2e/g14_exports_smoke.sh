#!/usr/bin/env bash
# M14-06 (GATE G14) scenario: family installs a published planner; it reads
# calendar exports from personal and family, through Caddy.
#
# Throwaway `e2e-g14-*` owner + family editor and a shared space
# `e2e-g14-*`:
#   1. Upload/register/build calendar (export `events` v1) in Personal and
#      in the family space; install working copies; migrate.
#   2. Upload/register/build planner (reads that export) in Personal;
#      publish it into the family catalog.
#   3. The editor's pinned install without `granted_reads` is 422
#      `reads_required`; with `granted_reads` it installs.
#   4. Seed an event in each calendar; planner RPC `getAll` on
#      `calendar_events` sees both `_space`s.
#   5. A write to the view is `sql_not_allowed`.
#   6. An ungranted app in the family space cannot see the view (`sql_error`).
#
# Everything (users, personal spaces, the shared space, apps, instances,
# bundles, git repos, app-releases) is deleted on exit. Never completes
# bootstrap. Never recreates model-runner or postgres.
#
# Needs the live stack (caddy + platform + builder image). REST only
# (`lib/auth.sh`); no browser, no GPU. Takes `/tmp/homeai-stack.lock`
# itself if the caller hasn't.
#
# Usage: scripts/e2e/g14_exports_smoke.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"
# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"

G14_SPACE_SLUG="e2e-g14-$(openssl rand -hex 3)"
G14_STATE="$(mktemp -t g14-state.XXXXXX)"
export G14_SPACE_SLUG G14_STATE

log() { echo "[g14] $(date '+%H:%M:%S') $*"; }

cleanup() {
  local id ids
  if [ -f "$G14_STATE" ]; then
    while read -r id || [ -n "$id" ]; do
      [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf \
        "/data/platform/app-bundles/$id" "/data/platform/app-git/$id.git" \
        "/data/platform/app-releases/$id" </dev/null || true
    done <"$G14_STATE"
    rm -f "$G14_STATE"
  fi
  ids="$(_e2e_psql homeai_platform "DELETE FROM spaces WHERE slug = '$G14_SPACE_SLUG'
    AND kind = 'shared' RETURNING id" 2>/dev/null || true)"
  for id in $ids; do
    [[ "$id" =~ ^[0-9a-f-]{36}$ ]] && _e2e_compose exec -T platform rm -rf "/data/spaces/$id" || true
  done
  e2e_auth_end
}
trap cleanup EXIT

e2e_auth_create_user g14
G14_OWNER="$E2E_NEW_USER"
G14_OWNER_PASSWORD="$E2E_NEW_PASSWORD"
e2e_auth_create_user g14-editor
G14_EDITOR="$E2E_NEW_USER"
G14_EDITOR_PASSWORD="$E2E_NEW_PASSWORD"
G14_OWNER_COOKIE="$(e2e_auth_login "$G14_OWNER" "$G14_OWNER_PASSWORD")"
G14_EDITOR_COOKIE="$(e2e_auth_login "$G14_EDITOR" "$G14_EDITOR_PASSWORD")"
export G14_OWNER G14_EDITOR G14_OWNER_COOKIE G14_EDITOR_COOKIE E2E_BASE
E2E_AUTH_COOKIE="$G14_OWNER_COOKIE"
export E2E_AUTH_COOKIE

log "owner ${G14_OWNER}; editor ${G14_EDITOR}; space ${G14_SPACE_SLUG}"
python3 - "$SCRIPT_DIR" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_DIR = Path(sys.argv[1])
BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
OWNER = os.environ["G14_OWNER_COOKIE"]
EDITOR = os.environ["G14_EDITOR_COOKIE"]
SLUG = os.environ["G14_SPACE_SLUG"]
STATE = Path(os.environ["G14_STATE"])
CAL = SCRIPT_DIR / "fixtures/apps/calendar"
PLAN = SCRIPT_DIR / "fixtures/apps/planner"
HELLO = SCRIPT_DIR / "fixtures/apps/hello"
READS = [{"app": "calendar", "export": "events", "version": "1"}]


def log(msg: str) -> None:
    print(f"[g14] {msg}", flush=True)


def fail(msg: str) -> None:
    raise SystemExit(f"FAIL: {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}", flush=True)


def call(cookie: str, method: str, path: str, *, json_body=None, raw=None, timeout=30):
    url = f"{BASE}{path}"
    headers = {"Cookie": cookie}
    data = None
    if raw is not None:
        headers["Content-Type"] = "application/octet-stream"
        data = raw if isinstance(raw, (bytes, bytearray)) else raw.encode()
    elif json_body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(json_body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            doc = json.loads(body) if body else None
        except json.JSONDecodeError:
            doc = body
        return e.code, doc


def expect(status, doc, want, what, detail=None):
    if status != want:
        fail(f"{what}: expected HTTP {want}, got {status}: {doc}")
    if detail is not None:
        got = doc.get("detail") if isinstance(doc, dict) else None
        if got != detail:
            fail(f"{what}: expected detail {detail!r}, got {doc}")
    ok(f"{what} -> {status}" + (f" {detail}" if detail else ""))
    return doc


def remember(app_id: str) -> None:
    with STATE.open("a") as f:
        f.write(f"{app_id}\n")


def rel_files(folder: Path) -> list[str]:
    out = []
    for path in sorted(folder.rglob("*")):
        if path.is_file():
            out.append(str(path.relative_to(folder).as_posix()))
    return out


def upload(cookie: str, folder: Path, dest: str) -> None:
    for rel in rel_files(folder):
        q = urllib.parse.urlencode({"path": f"{dest}/{rel}"})
        status, doc = call(
            cookie, "PUT", f"/api/platform/files/content?{q}", raw=folder.joinpath(rel).read_bytes()
        )
        expect(status, doc, 200, f"upload {dest}/{rel}")


def register(cookie: str, source_path: str) -> dict:
    status, doc = call(cookie, "POST", "/api/platform/apps", json_body={"source_path": source_path})
    expect(status, doc, 201, f"register {source_path}")
    app = doc["app"]
    remember(app["id"])
    return app


def install(cookie: str, space_id: str, app_id: str, body: dict, want=201, detail=None) -> dict | None:
    status, doc = call(cookie, "POST", f"/api/platform/spaces/{space_id}/instances", json_body=body)
    expect(status, doc, want, f"install {app_id[:8]} in {space_id[:8]}", detail=detail)
    return doc if status == 201 else None


def build(cookie: str, app_id: str, label: str) -> None:
    status, doc = call(cookie, "POST", f"/api/platform/apps/{app_id}/build", json_body={}, timeout=300)
    expect(status, doc, 200, f"build {label}")
    if not doc.get("ok") or doc.get("diagnostics"):
        fail(f"build {label} failed: {json.dumps(doc.get('diagnostics'), indent=2)}")
    ok(f"built {label} ({doc.get('build', {}).get('bundle_bytes')} B)")


def migrate(cookie: str, instance_id: str, label: str) -> None:
    status, doc = call(cookie, "POST", f"/api/platform/apps/instances/{instance_id}/migrate", json_body={})
    expect(status, doc, 200, f"migrate {label}")
    if doc.get("status") not in ("applied", "up_to_date"):
        fail(f"migrate {label}: {doc}")


def rpc(cookie: str, instance_id: str, body: dict, timeout=30):
    return call(cookie, "POST", f"/api/platform/apps/instances/{instance_id}/rpc", json_body=body, timeout=timeout)


def working_app(cookie: str, folder: Path, space: dict) -> tuple[dict, dict]:
    root = "/personal" if space["kind"] == "personal" else f"/spaces/{space['slug']}"
    slug = json.loads((folder / "app.json").read_text())["slug"]
    dest = f"{root}/Apps/{slug}"
    upload(cookie, folder, dest)
    app = register(cookie, dest)
    inst = install(cookie, space["id"], app["id"], {"app_id": app["id"]})
    build(cookie, app["id"], f"{slug} in {space['name']}")
    migrate(cookie, inst["id"], f"{slug} in {space['name']}")
    return app, inst


# --- 1. spaces ----------------------------------------------------------------
status, doc = call(OWNER, "GET", "/api/platform/spaces")
expect(status, doc, 200, "list spaces")
personal = next(s for s in doc["spaces"] if s["kind"] == "personal")
status, family = call(OWNER, "POST", "/api/platform/spaces", json_body={"slug": SLUG, "name": f"E2E G14 {os.getpid()}"})
expect(status, family, 201, f"create space {SLUG}")
status, directory = call(OWNER, "GET", "/api/platform/users/directory")
expect(status, directory, 200, "user directory")
editor = next(u for u in directory["users"] if u["username"] == os.environ["G14_EDITOR"])
status, doc = call(
    OWNER,
    "POST",
    f"/api/platform/spaces/{family['id']}/members",
    json_body={"user_id": editor["id"], "role": "editor"},
)
expect(status, doc, 201, "add family editor")

# --- 2. calendar in personal + family -----------------------------------------
log("calendar in personal and family")
_, personal_cal = working_app(OWNER, CAL, personal)
_, family_cal = working_app(OWNER, CAL, family)

# --- 3. planner in personal, publish, pinned family install -------------------
log("planner: build in personal, publish, pinned install in family")
root = "/personal/Apps/planner"
upload(OWNER, PLAN, root)
planner_app = register(OWNER, root)
build(OWNER, planner_app["id"], "planner in Personal")
status, published = call(
    OWNER, "POST", f"/api/platform/apps/{planner_app['id']}/publish", json_body={"space_ids": [family["id"]]}
)
expect(status, published, 200, "publish planner into family catalog")
version_id = published["version"]["id"]
status, catalog = call(EDITOR, "GET", f"/api/platform/spaces/{family['id']}/catalog")
expect(status, catalog, 200, "family catalog as editor")
entry = next((e for e in catalog["entries"] if e["app"]["slug"] == "planner"), None)
if entry is None or entry["version"]["id"] != version_id:
    fail(f"planner not in family catalog: {catalog}")
ok("planner listed in family catalog")

install(
    EDITOR,
    family["id"],
    planner_app["id"],
    {"app_id": planner_app["id"], "tracks": version_id},
    want=422,
    detail="reads_required",
)
planner = install(
    EDITOR,
    family["id"],
    planner_app["id"],
    {"app_id": planner_app["id"], "tracks": version_id, "granted_reads": READS},
)
if planner["granted_reads"] != READS:
    fail(f"granted_reads: {planner['granted_reads']}")
if planner["tracks"] != version_id:
    fail(f"tracks: {planner['tracks']}")
ok("pinned family install of planner with granted_reads")
migrate(OWNER, planner["id"], "planner in family")

# --- 4. seed events, merged view, write denied, ungranted denied --------------
log("seed events and RPC the merged view")
for inst, title in ((personal_cal, "Personal"), (family_cal, "Family")):
    status, doc = rpc(OWNER, inst["id"], {"op": "action", "name": "addEvent", "params": {"title": title}})
    expect(status, doc, 200, f"addEvent {title}")

status, doc = rpc(
    OWNER,
    planner["id"],
    {"op": "getAll", "sql": "SELECT title, _space FROM calendar_events ORDER BY title"},
)
expect(status, doc, 200, "planner getAll calendar_events")
want = [
    {"title": "Family", "_space": f"/spaces/{SLUG}"},
    {"title": "Personal", "_space": "/personal"},
]
if doc.get("rows") != want:
    fail(f"calendar_events rows: {doc.get('rows')} != {want}")
ok("planner sees both spaces' events")

status, doc = rpc(
    OWNER,
    planner["id"],
    {
        "op": "run",
        "sql": "INSERT INTO calendar_events (title, _space) VALUES ('x', '/personal')",
    },
)
expect(status, doc, 422, "write to calendar_events view", detail="sql_not_allowed")

log("ungranted hello in family cannot see the view")
hello_root = f"/spaces/{SLUG}/Apps/hello"
upload(OWNER, HELLO, hello_root)
hello_app = register(OWNER, hello_root)
hello = install(OWNER, family["id"], hello_app["id"], {"app_id": hello_app["id"]})
migrate(OWNER, hello["id"], "hello in family")
status, doc = rpc(OWNER, hello["id"], {"op": "getAll", "sql": "SELECT * FROM calendar_events"})
expect(status, doc, 422, "ungranted getAll calendar_events", detail="sql_error")

PY
echo "G14 EXPORTS SMOKE: PASS"
