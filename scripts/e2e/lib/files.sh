# shellcheck shell=bash
# The signed-in e2e user's personal space, through Caddy and the platform
# files API (M11-02): where the agent's file tools read and write now,
# instead of `FILES_DIR` on the host. Needs `lib/auth.sh`'s
# `E2E_AUTH_COOKIE` (so `e2e_auth_begin` first).
#
#   e2e_personal_status <name>        HTTP status of stat /personal/<name>
#   e2e_personal_cat <name>           its content on stdout (non-zero if missing)
#   e2e_personal_put <name> <text>    write it (parents created)
#   e2e_personal_rm <name>            delete it, recursively; best effort
#   e2e_personal_ls <dir>             entry names under /personal/<dir>, one per line
#
# <name> is relative to /personal ("gate-m2.txt", "reports/q1.md").

_e2e_personal() {
  python3 - "${E2E_BASE:-http://localhost}" "$@" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

base, op, name, *rest = sys.argv[1:]
vpath = "/personal/" + name.strip("/") if name.strip("/") else "/personal"
route, method, data = {
    "status": ("/stat", "GET", None),
    "cat": ("/download", "GET", None),
    "put": ("/content", "PUT", (rest[0] if rest else "").encode()),
    "rm": ("", "DELETE", None),
    "ls": ("", "GET", None),
}[op]
url = f"{base}/api/platform/files{route}?" + urllib.parse.urlencode({"path": vpath})
req = urllib.request.Request(url, data=data, method=method)
req.add_header("Cookie", os.environ["E2E_AUTH_COOKIE"])
if data is not None:
    req.add_header("Content-Type", "application/octet-stream")
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        status, body = resp.status, resp.read()
except urllib.error.HTTPError as e:
    status, body = e.code, e.read()
if op == "status":
    print(status)
elif op == "cat":
    if status != 200:
        sys.exit(1)
    sys.stdout.write(body.decode("utf-8", "replace"))
elif op == "ls":
    if status != 200:
        sys.exit(1)
    for entry in json.loads(body)["entries"]:
        print(entry["name"])
elif status >= 300 and op == "put":
    sys.exit(f"e2e_personal_put {vpath}: HTTP {status} {body!r}")
PY
}

e2e_personal_status() { _e2e_personal status "$1"; }
e2e_personal_cat() { _e2e_personal cat "$1"; }
e2e_personal_put() { _e2e_personal put "$1" "$2"; }
e2e_personal_rm() { _e2e_personal rm "$1" >/dev/null 2>&1 || true; }
e2e_personal_ls() { _e2e_personal ls "${1:-}"; }
