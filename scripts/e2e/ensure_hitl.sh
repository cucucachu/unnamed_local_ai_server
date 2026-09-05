#!/usr/bin/env bash
# PUT /api/settings hitl_enabled to $1 (`true` or `false`).
#
# Prints the previous value (`true`/`false`) on stdout so callers can
# restore it. Logs go to stderr. Used by mutating e2e scripts that predate
# HITL-on-by-default (M8-03) and would otherwise stall on approval_request.
#
# `curl` is not installed on this host — uses Python urllib.
#
# Usage:
#   prev="$(scripts/e2e/ensure_hitl.sh false)"
#   scripts/e2e/ensure_hitl.sh "$prev" >/dev/null

set -euo pipefail

if [ "${1:-}" != "true" ] && [ "${1:-}" != "false" ]; then
  echo "usage: $0 true|false" >&2
  exit 2
fi

API_BASE="${API_BASE:-http://localhost/api}"
WANT="$1"

python3 - "$API_BASE" "$WANT" <<'PY'
import json
import sys
import urllib.error
import urllib.request

api_base, want = sys.argv[1], sys.argv[2]
want_bool = want == "true"
url = api_base.rstrip("/") + "/settings"

def request(method, body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        raise SystemExit(f"ensure_hitl: {method} {url} -> {e.code}: {raw}") from e

status, body = request("GET")
if status != 200:
    raise SystemExit(f"ensure_hitl: GET {url} -> {status}: {body}")
prev = bool(body.get("hitl_enabled"))
print("true" if prev else "false")
if prev == want_bool:
    sys.exit(0)
status, body = request("PUT", {"hitl_enabled": want_bool})
if status != 200 or bool(body.get("hitl_enabled")) != want_bool:
    raise SystemExit(f"ensure_hitl: PUT did not set hitl_enabled={want}: {status} {body}")
PY
