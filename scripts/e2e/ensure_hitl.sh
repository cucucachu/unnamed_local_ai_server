#!/usr/bin/env bash
# Set whether the agent asks before using its tools, for the e2e user
# (`/api/platform/tools`, #342).
#
#   false     every tool the user can allow runs without asking
#   true      every tool that isn't read-only asks (the defaults)
#   <saved>   put back what an earlier call printed
#
# Prints the previous permissions (one line of JSON) on stdout so callers can
# restore them. Logs go to stderr. Used by e2e scripts whose steps would
# otherwise stall on approval_request. Tools an admin turned off or set to
# always ask are left alone.
#
# `curl` is not installed on this host — uses Python urllib.
#
# Settings are per user: this changes the user whose session cookie is in
# E2E_AUTH_COOKIE (see `lib/auth.sh`).
#
# Usage:
#   prev="$(scripts/e2e/ensure_hitl.sh false)"
#   scripts/e2e/ensure_hitl.sh "$prev" >/dev/null

set -euo pipefail

if [ -z "${1:-}" ]; then
  echo "usage: $0 true|false|<saved>" >&2
  exit 2
fi

if [ -z "${E2E_AUTH_COOKIE:-}" ]; then
  echo "ensure_hitl: E2E_AUTH_COOKIE is not set (source lib/auth.sh and run e2e_auth_begin)" >&2
  exit 2
fi

API_BASE="${API_BASE:-http://localhost/api}"

python3 - "$API_BASE" "$1" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

api_base, want = sys.argv[1], sys.argv[2]
url = api_base.rstrip("/") + "/platform/tools"


def request(method, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    headers["Cookie"] = os.environ["E2E_AUTH_COOKIE"]
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        raise SystemExit(f"ensure_hitl: {method} {url} -> {e.code}: {raw}") from e


tools = [t for t in request("GET")["tools"] if t["enabled"] and not t["permission_locked"]]
print(json.dumps({t["name"]: t["permission"] for t in tools}, separators=(",", ":")))

if want == "false":
    target = {t["name"]: "allow" for t in tools}
elif want == "true":
    target = {t["name"]: "allow" if t["read_only"] else "ask" for t in tools}
else:
    saved = json.loads(want)
    target = {t["name"]: saved[t["name"]] for t in tools if t["name"] in saved}

changes = [
    {"name": t["name"], "permission": target[t["name"]]}
    for t in tools
    if t["name"] in target and target[t["name"]] != t["permission"]
]
if changes:
    after = {t["name"]: t["permission"] for t in request("PATCH", {"changes": changes})["tools"]}
    wrong = [c["name"] for c in changes if after.get(c["name"]) != c["permission"]]
    if wrong:
        raise SystemExit(f"ensure_hitl: PATCH did not change {wrong}")
PY
