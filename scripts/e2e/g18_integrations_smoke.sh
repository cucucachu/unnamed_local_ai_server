#!/usr/bin/env bash
# M18-09 (GATE G18) scenario: an MCP integration on the live stack.
#
# Starts the fake MCP server (services/integrations-gateway/tests/fake_server.py,
# a `homeai.test` container from the gateway image on homeai-net) with one
# token per test user, then as throwaway `e2e-g18-*` users
# (`g18_integrations_smoke.py`):
#
#   gateway phase (no model): an admin installs it by container IP with the
#     private-address exception, connects, discovers and sets ceilings; a
#     user connects with their own token, a second user doesn't. Through the
#     gateway with each user's delegation: tools off for the admin or the
#     user are invisible, an admin ceiling of ask overrides the user's
#     stored allow, an ask call without a marker is refused, allow runs, the
#     unconnected user gets `not_connected`, and no token appears in any API
#     response, the audit log or a service's logs.
#   oauth phase (no model): a second fake, an OAuth provider in front of the
#     same server (the Home Assistant shape: a client ID URL, everything on
#     its own address). The check says OAuth; the admin signs in from this
#     server's LAN address, the browser's part played against the fake; the
#     return page (no session) stores it and finds the tools; a call works;
#     an expired token is refreshed and written back; disconnecting revokes
#     it; no issued token appears anywhere.
#   agent phase (the real model): the agent opens the integration and turns
#     a light on with no card; an ask tool stops with a card in chat (approved,
#     the light goes off) and in a routine run; the server's request for
#     input (which room?) goes to the user and back.
#
# G18_PHASE=gateway|oauth|agent|all (default all). Never completes bootstrap.
# Never recreates model-runner or postgres. Never touches other
# integrations: only `e2e-` ones are installed, and only their cards are
# approved. Removes the integration, the users, the container and the
# runner on exit. Takes `/tmp/homeai-stack.lock` if the caller hasn't.
#
# Usage: scripts/e2e/g18_integrations_smoke.sh

set -euo pipefail

if [ -z "${HOMEAI_STACK_LOCK:-}" ]; then
  exec flock /tmp/homeai-stack.lock env HOMEAI_STACK_LOCK=1 "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

export COMPOSE_PROJECT_NAME=homeai

# Worktrees don't get the gitignored .env; compose still needs it. Never cat it.
if [ ! -f .env ] && [ -f /home/cody/code/unnamed_local_ai_server/.env ]; then
  ln -s /home/cody/code/unnamed_local_ai_server/.env .env
fi

# shellcheck source=lib/auth.sh
source "$SCRIPT_DIR/lib/auth.sh"
# shellcheck source=lib/internal.sh
source "$SCRIPT_DIR/lib/internal.sh"

FAKE="e2e-g18-fake-mcp-$$"
OAUTH_FAKE="e2e-g18-fake-oauth-$$"
RUNNER="e2e-g18-runner-$$"
GATEWAY_IMAGE="${G18_GATEWAY_IMAGE:-homeai-integrations-gateway}"

cleanup() {
  set +e
  if [ -n "${G18_ADMIN_COOKIE:-}" ]; then
    uvx --quiet --from websockets python3 "$SCRIPT_DIR/g18_integrations_smoke.py" cleanup || true
  fi
  # Whatever the API couldn't remove (credentials and tool settings cascade).
  _e2e_psql homeai_platform "DELETE FROM integrations WHERE slug LIKE 'e2e-g18-%'" >/dev/null 2>&1
  e2e_auth_end
  internal_runner_stop
  docker rm -f "$FAKE" "$OAUTH_FAKE" >/dev/null 2>&1
}
trap cleanup EXIT

net_name() {
  docker compose config --format json | python3 -c "
import json, sys
print(json.load(sys.stdin)['networks']['$1']['name'])
"
}

G18_SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
G18_TOKENS="$(openssl rand -hex 16),$(openssl rand -hex 16)"
export G18_SINCE G18_TOKENS FAKE_MCP_TOKENS="$G18_TOKENS"

docker run -d --rm --name "$FAKE" --label homeai.test=g18 --network "$(net_name homeai-net)" \
  -e FAKE_MCP_TOKENS -v "$REPO_ROOT/services/integrations-gateway/tests:/app/tests:ro" -w /app \
  --entrypoint /app/.venv/bin/python "$GATEWAY_IMAGE" -m tests.fake_server >/dev/null
G18_FAKE_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$FAKE")"
export G18_FAKE="$FAKE" G18_FAKE_IP
echo "[g18] fake MCP server $FAKE at $G18_FAKE_IP:8000"

docker run -d --rm --name "$OAUTH_FAKE" --label homeai.test=g18 --network "$(net_name homeai-net)" \
  -e FAKE_OAUTH=ha -v "$REPO_ROOT/services/integrations-gateway/tests:/app/tests:ro" -w /app \
  --entrypoint /app/.venv/bin/python "$GATEWAY_IMAGE" -m tests.fake_server >/dev/null
G18_OAUTH_IP="$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$OAUTH_FAKE")"
# A sign-in returns to one of this server's own addresses; the first LAN one.
G18_LAN="$(_e2e_env_value HOMEAI_LAN_ADDRESSES "" | cut -d, -f1 | tr -d ' ')"
export G18_OAUTH_FAKE="$OAUTH_FAKE" G18_OAUTH_IP G18_LAN
echo "[g18] fake OAuth provider $OAUTH_FAKE at $G18_OAUTH_IP:8000; sign-ins return to $G18_LAN"

internal_runner_start "$RUNNER" || { echo "[g18] FAIL: runner didn't start" >&2; exit 1; }
export G18_RUNNER="$RUNNER"
PLATFORM_AGENT_TOKEN="$(_e2e_env_value PLATFORM_AGENT_TOKEN "")"
G18_PG_USER="$(_e2e_env_value POSTGRES_USER homeai)"
export PLATFORM_AGENT_TOKEN G18_PG_USER

ADMIN_PASSWORD="$(openssl rand -hex 16)"
G18_ADMIN="e2e-g18-admin-$(openssl rand -hex 4)"
_E2E_AUTH_CREATED+=("$G18_ADMIN")
printf '%s\n' "$ADMIN_PASSWORD" | _e2e_compose exec -T platform \
  python -m app.cli create-user "$G18_ADMIN" --display-name "E2E g18 admin" --role admin \
  --password-stdin >/dev/null
G18_ADMIN_COOKIE="$(e2e_auth_login "$G18_ADMIN" "$ADMIN_PASSWORD")"
G18_ADMIN_PASSWORD="$ADMIN_PASSWORD"

e2e_auth_create_user g18
G18_USER="$E2E_NEW_USER"
G18_USER_COOKIE="$(e2e_auth_login "$E2E_NEW_USER" "$E2E_NEW_PASSWORD")"
e2e_auth_create_user g18
G18_OTHER_COOKIE="$(e2e_auth_login "$E2E_NEW_USER" "$E2E_NEW_PASSWORD")"
export G18_ADMIN_COOKIE G18_ADMIN_PASSWORD G18_USER G18_USER_COOKIE G18_OTHER_COOKIE E2E_BASE
echo "[g18] admin $G18_ADMIN, user $G18_USER, unconnected $E2E_NEW_USER"

uvx --quiet --from websockets python3 "$SCRIPT_DIR/g18_integrations_smoke.py" "${G18_PHASE:-all}"
