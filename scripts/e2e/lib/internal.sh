# shellcheck shell=bash
# Calls to services that publish no host port (`platform:8100`,
# `code-exec-manager:8090`), made the way agent-server makes them (M11-04,
# extracted from verify_isolation.sh).
#
# A throwaway "runner" container (`python:3.12-slim`: Python, no curl — nor
# is there curl on the host) joins `homeai-internal` for the script's
# duration, and each call `docker exec`s a small urllib snippet into it.
# Secrets (PLATFORM_AGENT_TOKEN, PLATFORM_EXEC_TOKEN, session cookies,
# tokens) reach the snippet through its environment, never its argv.
#
#   source "$SCRIPT_DIR/lib/internal.sh"
#   internal_runner_start my-runner-$$ || exit 1   # stop it in the EXIT trap
#   internal_delegation "$COOKIE" "$THREAD_ID"      # prints a delegation token
#   internal_exec_manager_call "$COOKIE" POST "$SESSION" ensure delegation 30
#   internal_runner_stop

INTERNAL_RUNNER_IMAGE="${INTERNAL_RUNNER_IMAGE:-python:3.12-slim}"
INTERNAL_RUNNER=""

# Prints the compose network name of `homeai-internal`.
internal_network() {
  docker compose config --format json | python3 -c "
import json, sys
print(json.load(sys.stdin)['networks']['homeai-internal']['name'])
"
}

# $1 container name, $2 network (default: internal_network).
internal_runner_start() {
  local network="${2:-$(internal_network)}" tries=0
  docker run -d --rm --name "$1" --network "$network" "$INTERNAL_RUNNER_IMAGE" sleep infinity >/dev/null ||
    return 1
  INTERNAL_RUNNER="$1"
  while ! docker exec "$INTERNAL_RUNNER" true >/dev/null 2>&1; do
    tries=$((tries + 1))
    [ "$tries" -ge 30 ] && return 1
    sleep 0.5
  done
}

internal_runner_stop() {
  if [ -n "$INTERNAL_RUNNER" ]; then
    docker rm -f "$INTERNAL_RUNNER" >/dev/null 2>&1 || true
  fi
  INTERNAL_RUNNER=""
}

# $1 python source, then its argv. The variables named in INTERNAL_PY_ENV
# (space-separated) are passed through from this shell's environment.
internal_py() {
  local source="$1" name env_args=()
  shift
  for name in ${INTERNAL_PY_ENV:-}; do env_args+=(-e "$name"); done
  docker exec "${env_args[@]}" "$INTERNAL_RUNNER" python3 -c "$source" "$@"
}

# Shared by the snippets below: `delegation(thread)` mints a delegation from
# E2E_CALL_COOKIE the way agent-server does (session cookie -> identity token
# at /internal/auth/verify -> /internal/delegations with the service token).
_INTERNAL_DELEGATION_PY="$(cat <<'EOF'
import json, os, sys, urllib.error, urllib.request

PLATFORM = "http://platform:8100"


def call(url, data=None, method="GET", headers=None, t=30):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    return urllib.request.urlopen(req, timeout=t)


def identity():
    with call(f"{PLATFORM}/internal/auth/verify", headers={"Cookie": os.environ["E2E_CALL_COOKIE"]}) as r:
        return r.headers["X-HomeAI-Identity"]


def delegation(thread):
    payload = json.dumps({"identity_token": identity(), "thread_id": thread}).encode()
    headers = {
        "Authorization": f"Bearer {os.environ['PLATFORM_AGENT_TOKEN']}",
        "Content-Type": "application/json",
    }
    with call(f"{PLATFORM}/internal/delegations", payload, "POST", headers) as r:
        return json.loads(r.read())["token"]
EOF
)"

# $1 cookie, $2 thread id. Prints the delegation token. Needs PLATFORM_AGENT_TOKEN exported.
internal_delegation() {
  E2E_CALL_COOKIE="$1" INTERNAL_PY_ENV="E2E_CALL_COOKIE PLATFORM_AGENT_TOKEN" internal_py \
    "${_INTERNAL_DELEGATION_PY}
sys.stdout.write(delegation(sys.argv[1]))" "$2"
}

# argv: method session_id action(ensure|execute|delete) auth timeout [json body]
# auth: `delegation` (for session_id's own thread), `delegation-for:<thread>`,
# `tampered` (a real delegation with a broken signature), or `none`.
# Prints the manager's JSON response, `{"status": N}` for an empty 2xx, or
# `{"http_error": N, "body": ...}`.
_INTERNAL_EXEC_MANAGER_PY="${_INTERNAL_DELEGATION_PY}
$(cat <<'EOF'
MANAGER = "http://code-exec-manager:8090"
method, session_id, action, auth, timeout = sys.argv[1:6]
body = sys.argv[6] if len(sys.argv) > 6 else ""
timeout = int(timeout)

headers = {"Content-Type": "application/json"}
if auth == "delegation":
    headers["Authorization"] = f"Bearer {delegation(session_id)}"
elif auth.startswith("delegation-for:"):
    headers["Authorization"] = f"Bearer {delegation(auth.split(':', 1)[1])}"
elif auth == "tampered":
    head, claims, signature = delegation(session_id).split(".")
    flipped = "A" if signature[0] != "A" else "B"
    headers["Authorization"] = f"Bearer {head}.{claims}.{flipped}{signature[1:]}"

path = f"/sessions/{session_id}" + ("" if action == "delete" else f"/{action}")
data = body.encode() if body else (b"" if method == "POST" else None)
try:
    with call(MANAGER + path, data, method, headers, timeout + 20) as r:
        out = r.read().decode()
        sys.stdout.write(out if out else json.dumps({"status": r.status}))
except urllib.error.HTTPError as e:
    sys.stdout.write(json.dumps({"http_error": e.code, "body": e.read().decode()}))
except urllib.error.URLError as e:
    sys.stdout.write(json.dumps({"url_error": str(e.reason)}))
EOF
)"

# $1 cookie of the user whose session mints the delegation, then the argv above.
# Never fails: an unreachable runner prints nothing.
internal_exec_manager_call() {
  local cookie="$1"
  shift
  E2E_CALL_COOKIE="$cookie" INTERNAL_PY_ENV="E2E_CALL_COOKIE PLATFORM_AGENT_TOKEN" internal_py \
    "$_INTERNAL_EXEC_MANAGER_PY" "$@" 2>/dev/null || true
}
