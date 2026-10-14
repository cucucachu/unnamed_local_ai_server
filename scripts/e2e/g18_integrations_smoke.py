#!/usr/bin/env python3
"""M18-09 (GATE G18): an MCP integration on the live stack. See g18_integrations_smoke.sh.

argv[1]: gateway | oauth | agent | all | cleanup.

Env (from the .sh): E2E_BASE; G18_ADMIN_COOKIE / G18_ADMIN_PASSWORD (an admin),
G18_USER_COOKIE (connected), G18_OTHER_COOKIE (never connected); G18_TOKENS (the
fake server's two tokens: the admin's, the user's); G18_FAKE (its container) and
G18_FAKE_IP; G18_RUNNER (a container on homeai-internal) and PLATFORM_AGENT_TOKEN
(for delegations); G18_SINCE (for the log and audit checks); G18_PG_USER; G18_OAUTH_FAKE
and G18_OAUTH_IP (the fake OAuth provider), G18_LAN (this server's LAN address).
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from websockets.asyncio.client import connect

BASE = os.environ.get("E2E_BASE", "http://localhost").rstrip("/")
ADMIN = os.environ.get("G18_ADMIN_COOKIE", "")
USER = os.environ.get("G18_USER_COOKIE", "")
OTHER = os.environ.get("G18_OTHER_COOKIE", "")
ADMIN_TOKEN, USER_TOKEN = os.environ.get("G18_TOKENS", ",").split(",")
TURN_TIMEOUT_S = 600
ADMIN_API = "/api/platform/admin/integrations"
MINE = "/api/platform/integrations"
SERVICES = ["platform", "integrations-gateway", "agent-server", "egress-proxy", "caddy"]

# Every response body the users saw, checked for the tokens at the end.
SEEN: list[str] = []
# Tokens the fake OAuth provider issued: never in a response, a log or the audit log.
ISSUED: list[str] = []

# Runs in the runner (homeai-internal): mints the cookie holder's delegation for a
# thread the way agent-server does, then calls the gateway's agent API with it.
GATEWAY_PY = r"""
import json, os, sys, urllib.error, urllib.request
PLATFORM = "http://platform:8100"
def call(url, data=None, method="GET", headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    return urllib.request.urlopen(req, timeout=60)
with call(f"{PLATFORM}/internal/auth/verify", headers={"Cookie": os.environ["G18_CALL_COOKIE"]}) as r:
    identity = r.headers["X-HomeAI-Identity"]
payload = json.dumps({"identity_token": identity, "thread_id": sys.argv[1]}).encode()
headers = {"Authorization": f"Bearer {os.environ['PLATFORM_AGENT_TOKEN']}", "Content-Type": "application/json"}
with call(f"{PLATFORM}/internal/delegations", payload, "POST", headers) as r:
    delegation = json.loads(r.read())["token"]
headers = {"Authorization": f"Bearer {delegation}", "Content-Type": "application/json"}
try:
    with call(f"http://integrations-gateway:8000{sys.argv[2]}", sys.argv[3].encode(), "POST", headers) as r:
        out = {"status": r.status, "body": json.loads(r.read())}
except urllib.error.HTTPError as e:
    out = {"status": e.code, "body": e.read().decode("utf-8", "replace")}
print(json.dumps(out))
"""


def log(msg: str) -> None:
    print(f"[g18] {time.strftime('%H:%M:%S')} {msg}", flush=True)


def fail(msg: str) -> None:
    log(f"FAIL: {msg}")
    raise SystemExit(1)


def api(
    cookie: str, method: str, path: str, body: Any = None, host: str | None = None
) -> tuple[int, Any]:
    headers = {"Cookie": cookie, "Content-Type": "application/json"}
    if host:
        headers["Host"] = host
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=None if body is None else json.dumps(body).encode(),
        method=method,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        status = e.code
    SEEN.append(raw)
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, raw


def ok(
    cookie: str, method: str, path: str, body: Any = None, want: int = 200, host: str | None = None
) -> Any:
    status, out = api(cookie, method, path, body, host)
    if status != want:
        fail(f"{method} {path}: HTTP {status} {out!r}")
    return out


def gateway(cookie: str, thread_id: str, path: str, body: dict) -> dict:
    env = os.environ | {"G18_CALL_COOKIE": cookie}
    done = subprocess.run(
        ["docker", "exec", "-e", "G18_CALL_COOKIE", "-e", "PLATFORM_AGENT_TOKEN",
         os.environ["G18_RUNNER"], "python3", "-c", GATEWAY_PY, thread_id, path, json.dumps(body)],
        check=False, env=env, capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    if done.returncode != 0:
        fail(f"gateway {path}: {done.stderr.strip()[-500:]}")
    out = json.loads(done.stdout)
    SEEN.append(json.dumps(out))
    if out["status"] != 200:
        fail(f"gateway {path}: HTTP {out['status']} {out['body']!r}")
    return out["body"]


def lights() -> dict[str, bool]:
    done = subprocess.run(
        ["docker", "exec", os.environ["G18_FAKE"], "/app/.venv/bin/python", "-c",
         "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/state').read().decode())"],
        capture_output=True, text=True, timeout=30, check=True,
    )  # fmt: skip
    return json.loads(done.stdout)


def new_thread(cookie: str) -> str:
    return ok(cookie, "POST", "/api/threads", {}, want=201)["id"]


# --- setup -------------------------------------------------------------------------------


def step_up() -> None:
    ok(ADMIN, "POST", "/api/auth/step-up", {"password": os.environ["G18_ADMIN_PASSWORD"]})


def install() -> dict:
    """The admin's whole Add flow, then the user's settings. Returns the installed integration."""
    step_up()
    slug = f"e2e-g18-{secrets.token_hex(3)}"
    fields = {
        "slug": slug,
        "name": f"E2E Home {slug[-6:]}",
        "url": f"http://{os.environ['G18_FAKE_IP']}:8000/mcp",
        "private_address": True,
    }
    found = ok(
        ADMIN, "POST", ADMIN_API, fields | {"credential_model": "none", "auth_method": "none"}, 201
    )
    iid = found["id"]
    check = ok(ADMIN, "POST", f"{ADMIN_API}/{iid}/check")
    if not check["reachable"] or check["auth"] != "token":
        fail(f"check: expected a token server, got {check!r}")
    ok(
        ADMIN,
        "PATCH",
        f"{ADMIN_API}/{iid}",
        {"credential_model": "per_user", "auth_method": "token"},
    )
    connected = ok(ADMIN, "PUT", f"{MINE}/{iid}/connection", {"secret": ADMIN_TOKEN})
    if connected != {"status": "connected", "checked": True, "problem": None}:
        fail(f"admin connection: {connected!r}")
    found = ok(ADMIN, "POST", f"{ADMIN_API}/{iid}/refresh")
    tools = {t["name"]: t for t in found["tools"]}
    if not {"list_lights", "turn_on", "turn_off", "set_scene", "hidden_tool"} <= tools.keys():
        fail(f"discovery found {sorted(tools)}")
    if any(t["enabled"] for t in tools.values()):
        fail("new tools should start off")
    on = ["list_lights", "turn_on", "turn_off", "set_scene", "broken"]
    changes = [{"name": n, "enabled": True, "ceiling": "allow"} for n in on]
    ok(ADMIN, "PATCH", f"{ADMIN_API}/{iid}/tools", {"changes": changes})

    bad = api(USER, "PUT", f"{MINE}/{iid}/connection", {"secret": "not-the-token"})
    if bad[0] != 422 or "credential_rejected" not in json.dumps(bad[1]):
        fail(f"a wrong token should be refused and not stored: {bad!r}")
    connected = ok(USER, "PUT", f"{MINE}/{iid}/connection", {"secret": USER_TOKEN})
    if connected["status"] != "connected":
        fail(f"user connection: {connected!r}")
    changes = [
        {"name": n, "permission": "allow"}
        for n in ("list_lights", "turn_on", "turn_off", "set_scene")
    ]
    changes.append({"name": "broken", "enabled": False})
    ok(USER, "PATCH", f"{MINE}/{iid}/tools", {"changes": changes})
    # After the user stored allow: the admin's lower ceiling has to win over it.
    ok(
        ADMIN,
        "PATCH",
        f"{ADMIN_API}/{iid}/tools",
        {"changes": [{"name": "turn_off", "ceiling": "ask"}]},
    )
    log(f"installed {slug} ({iid}); admin and user connected; ceilings set")
    return found


def cleanup() -> None:
    step_up()
    for i in ok(ADMIN, "GET", ADMIN_API)["integrations"]:
        if i["slug"].startswith("e2e-g18-"):
            api(ADMIN, "DELETE", f"{ADMIN_API}/{i['id']}")


# --- gateway phase -----------------------------------------------------------------------


def gateway_phase(integration: dict) -> None:
    slug = integration["slug"]
    threads = []
    try:
        thread = new_thread(USER)
        threads.append((USER, thread))
        opened = gateway(USER, thread, "/v1/open", {"integration": slug, "context": "chat"})
        if not opened.get("ok"):
            fail(f"open: {opened!r}")
        tools = {t["name"]: t["permission"] for t in opened["tools"]}
        if set(tools) != {"list_lights", "turn_on", "turn_off", "set_scene"}:
            fail(
                f"open lists {sorted(tools)}: hidden_tool (off for the admin) and broken (off for the user) must not show"
            )
        log("OK: tools off for the admin or the user are invisible")
        if tools["turn_off"] != "ask" or tools["turn_on"] != "allow":
            fail(f"permissions {tools!r}: the admin's ask must win over the user's allow")
        log("OK: the admin's ceiling of ask overrides the user's stored allow")

        call = {"integration": slug, "context": "chat"}
        ran = gateway(
            USER, thread, "/v1/call", call | {"tool": "turn_on", "arguments": {"room": "hall"}}
        )
        if (
            not ran.get("ok")
            or "hall on" not in ran.get("text", "")
            or lights().get("hall") is not True
        ):
            fail(f"allow on both sides should run: {ran!r}")
        log("OK: allow on both sides runs without a marker")
        refused = gateway(
            USER, thread, "/v1/call", call | {"tool": "turn_off", "arguments": {"room": "hall"}}
        )
        if (
            refused.get("ok")
            or refused["error"]["code"] != "approval_required"
            or not lights()["hall"]
        ):
            fail(f"an ask tool without a marker must be refused: {refused!r}")
        log("OK: the gateway refuses an ask call without a marker")
        forged = gateway(
            USER, thread, "/v1/call",
            call | {"tool": "turn_off", "arguments": {"room": "hall"}, "approval": "a" * 43},
        )  # fmt: skip
        if forged.get("ok") or not lights()["hall"]:
            fail(f"a forged marker must be refused: {forged!r}")
        log("OK: ... and one with a forged marker")
        hidden = gateway(USER, thread, "/v1/call", call | {"tool": "hidden_tool", "arguments": {}})
        if hidden.get("ok"):
            fail(f"a tool the admin left off must not run: {hidden!r}")

        other = new_thread(OTHER)
        threads.append((OTHER, other))
        none = gateway(OTHER, other, "/v1/open", {"integration": slug, "context": "chat"})
        if none.get("ok") or none["error"]["code"] != "not_connected":
            fail(f"the unconnected user should get not_connected: {none!r}")
        log("OK: the unconnected user gets not_connected")
    finally:
        for cookie, thread_id in threads:
            api(cookie, "DELETE", f"/api/threads/{thread_id}")
    no_secrets()


def no_secrets() -> None:
    tokens = [ADMIN_TOKEN, USER_TOKEN, *ISSUED]
    if any(t in raw for raw in SEEN for t in tokens):
        fail("a token appeared in an API response")
    logs = subprocess.run(
        ["docker", "compose", "-p", "homeai", "logs", "--no-color", "--since", os.environ["G18_SINCE"], *SERVICES],
        capture_output=True, text=True, timeout=120, check=True,
    ).stdout  # fmt: skip
    if any(t in logs for t in tokens):
        fail("a token appeared in a service's logs")
    audit = subprocess.run(
        ["docker", "compose", "-p", "homeai", "exec", "-T", "postgres", "psql", "-qtA",
         "-U", os.environ.get("G18_PG_USER", "homeai"), "-d", "homeai_platform", "-c",
         f"SELECT summary || ' ' || detail::text FROM audit_events WHERE at >= '{os.environ['G18_SINCE']}'"],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout  # fmt: skip
    if not audit.strip():
        fail("no audit events recorded since the start")
    if any(t in audit for t in tokens):
        fail("a token appeared in the audit log")
    log(f"OK: no token in {len(SEEN)} responses, {len(logs.splitlines())} log lines or "
        f"{len(audit.splitlines())} audit events")  # fmt: skip


# --- oauth phase -------------------------------------------------------------------------

# Runs in the fake OAuth provider's container: one request to it, redirects not followed.
FAKE_OAUTH_PY = r"""
import http.client, json, sys
conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=30)
conn.request(sys.argv[1], sys.argv[2], headers={"Host": sys.argv[3]})
r = conn.getresponse()
print(json.dumps({"status": r.status, "location": r.getheader("location"), "body": r.read().decode()}))
"""


def fake_oauth(method: str, path: str) -> dict:
    """Ask the fake provider as the gateway's address for it (the browser's part, or its state)."""
    done = subprocess.run(
        ["docker", "exec", os.environ["G18_OAUTH_FAKE"], "/app/.venv/bin/python", "-c",
         FAKE_OAUTH_PY, method, path, f"{os.environ['G18_OAUTH_IP']}:8000"],
        capture_output=True, text=True, timeout=60, check=True,
    )  # fmt: skip
    return json.loads(done.stdout)


def provider_state() -> dict:
    state = json.loads(fake_oauth("GET", "/oauth-state")["body"])
    ISSUED[:] = state["issued"]
    return state


def browse(path: str) -> tuple[int, str]:
    """A browser with no HomeAI session, on this server's LAN address."""
    req = urllib.request.Request(f"{BASE}{path}", headers={"Host": os.environ["G18_LAN"]})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            status = resp.status
    except urllib.error.HTTPError as e:
        raw, status = e.read().decode("utf-8", "replace"), e.code
    SEEN.append(raw)
    return status, raw


def ciphertext_digest(iid: str) -> str:
    return subprocess.run(
        ["docker", "compose", "-p", "homeai", "exec", "-T", "postgres", "psql", "-qtA",
         "-U", os.environ.get("G18_PG_USER", "homeai"), "-d", "homeai_platform", "-c",
         f"SELECT md5(ciphertext) FROM integration_credentials WHERE integration_id = '{iid}'"],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout.strip()  # fmt: skip


def oauth_phase() -> None:
    lan = os.environ["G18_LAN"]
    if not lan:
        fail("HOMEAI_LAN_ADDRESSES isn't set: a sign-in has nowhere to return to")
    step_up()
    slug = f"e2e-g18-o{secrets.token_hex(3)}"
    fields = {
        "slug": slug,
        "name": f"E2E OAuth {slug[-6:]}",
        "url": f"http://{os.environ['G18_OAUTH_IP']}:8000/mcp",
        "private_address": True,
    }
    iid = ok(
        ADMIN, "POST", ADMIN_API, fields | {"credential_model": "none", "auth_method": "none"}, 201
    )["id"]
    check = ok(ADMIN, "POST", f"{ADMIN_API}/{iid}/check")
    oauth = check.get("oauth") or {}
    if (
        check["auth"] != "oauth"
        or oauth.get("registration") != "client_id_url"
        or oauth.get("revocable") is not True
    ):
        fail(f"check: expected OAuth with a client ID URL and revocation, got {check!r}")
    log("OK: the check finds OAuth sign-in (a client ID URL, revocable)")
    ok(
        ADMIN,
        "PATCH",
        f"{ADMIN_API}/{iid}",
        {"credential_model": "per_user", "auth_method": "oauth"},
    )

    refused = api(ADMIN, "POST", f"{MINE}/{iid}/oauth/start", host="elsewhere.example")
    if refused[0] != 422 or "unknown_address" not in json.dumps(refused[1]):
        fail(f"a sign-in from an address that isn't this server's must be refused: {refused!r}")
    started = ok(ADMIN, "POST", f"{MINE}/{iid}/oauth/start", host=lan)
    authorize = urlsplit(started["authorize_url"])
    if authorize.netloc != f"{os.environ['G18_OAUTH_IP']}:8000":
        fail(f"sign-in URL on the wrong host: {started!r}")
    answered = fake_oauth("GET", f"{authorize.path}?{authorize.query}")
    back = urlsplit(answered.get("location") or "")
    if (
        answered["status"] != 302
        or back.netloc != lan
        or back.path != "/api/platform/integrations/oauth/return"
    ):
        fail(f"the provider should send the browser back to {lan}: {answered!r}")
    status, page = browse(f"{back.path}?{back.query}")
    if status != 200 or "found" not in page or f"settings/integrations/manage/{iid}" not in page:
        fail(f"the return page should store it and find the tools: {status} {page[:500]!r}")
    log(
        "OK: signed in from the LAN address; the return page (no session) stored it and found the tools"
    )
    status, page = browse(f"{back.path}?{back.query}")
    if status != 400 or "already used" not in page:
        fail(f"a used sign-in must not work twice: {status} {page[:300]!r}")
    log("OK: the same return link refused a second time")

    found = next(i for i in ok(ADMIN, "GET", ADMIN_API)["integrations"] if i["id"] == iid)
    if "list_lights" not in {t["name"] for t in found["tools"]} or len(found["connections"]) != 1:
        fail(f"discovery or the admin's connection missing: {found!r}")
    ok(ADMIN, "PATCH", f"{ADMIN_API}/{iid}/tools",
       {"changes": [{"name": "list_lights", "enabled": True, "ceiling": "allow"}]})  # fmt: skip
    ok(ADMIN, "PATCH", f"{MINE}/{iid}/tools",
       {"changes": [{"name": "list_lights", "permission": "allow"}]})  # fmt: skip

    thread = new_thread(ADMIN)
    try:
        call = {"integration": slug, "context": "chat", "tool": "list_lights", "arguments": {}}
        ran = gateway(ADMIN, thread, "/v1/call", call)
        if not ran.get("ok"):
            fail(f"a call with the signed-in token should run: {ran!r}")
        log("OK: a call runs with the signed-in token")
        before = ciphertext_digest(iid)
        fake_oauth("POST", "/expire")
        ran = gateway(ADMIN, thread, "/v1/call", call)
        state = provider_state()
        if not ran.get("ok") or state["refreshes"] < 1:
            fail(f"an expired token should be refreshed and the call retried: {ran!r} {state!r}")
        if ciphertext_digest(iid) == before:
            fail("the refreshed credential wasn't written back to the platform")
        log(
            f"OK: an expired token was refreshed ({state['refreshes']}x), the call retried, the new one stored"
        )
    finally:
        api(ADMIN, "DELETE", f"/api/threads/{thread}")

    ok(ADMIN, "DELETE", f"{MINE}/{iid}/connection", want=204)
    state = provider_state()
    if state["revoked"] < 1:
        fail(f"disconnecting should revoke at the provider: {state!r}")
    mine = next(i for i in ok(ADMIN, "GET", MINE)["integrations"] if i["id"] == iid)
    if mine["status"] != "not_connected":
        fail(f"still connected after disconnecting: {mine!r}")
    log("OK: disconnecting revoked the token at the provider and deleted it")
    no_secrets()


# --- agent phase -------------------------------------------------------------------------


async def turn(thread_id: str, content: str | None, slug: str, *, approve: bool = True,
               answer: dict | None = None) -> dict:  # fmt: skip
    """One chat turn (or the resume of `answer`, a pending approval). Approves only our cards."""
    frames: list[dict] = []
    cards: list[dict] = []
    ws_base = BASE.replace("http", "ws", 1)
    async with connect(f"{ws_base}/ws/chat/{thread_id}", additional_headers={"Cookie": USER}) as ws:
        if answer is not None:
            await ws.send(json.dumps(answer))
        else:
            await ws.send(json.dumps({"type": "user_message", "content": content}))
        async with asyncio.timeout(TURN_TIMEOUT_S):
            async for raw in ws:
                frame = json.loads(raw)
                frames.append(frame)
                if frame["type"] == "approval_request":
                    cards.extend(frame["actions"])
                    ours = all(
                        a["name"] == "use_integration" and a["args"].get("integration") == slug
                        for a in frame["actions"]
                    )
                    decision = "approve" if approve and ours else "reject"
                    await ws.send(json.dumps({
                        "type": "approval_response",
                        "interrupt_id": frame["interrupt_id"],
                        "decisions": [{"tool_call_id": a["tool_call_id"], "decision": decision}
                                      for a in frame["actions"]],
                    }))  # fmt: skip
                    if not ours:
                        fail(f"a card for something other than {slug}: {frame['actions']!r}")
                if frame["type"] == "error":
                    fail(f"turn error: {frame!r}")
                if frame["type"] == "turn_end" and frame["status"] != "awaiting_approval":
                    break
    tools = [f for f in frames if f["type"] == "tool_end"]
    reply = "".join(f.get("content") or "" for f in frames if f["type"] == "token")
    return {"cards": cards, "tools": tools, "reply": reply}


def integration_calls(result: dict) -> list[str]:
    return [t.get("result_preview", "") for t in result["tools"] if t["name"] == "use_integration"]


def summary(result: dict) -> str:
    tools = [(t["name"], (t.get("result_preview") or "")[:160]) for t in result["tools"]]
    return f"tools {tools!r}; reply {result['reply'][-300:]!r}"


def agent_phase(integration: dict) -> None:
    slug, name = integration["slug"], integration["name"]
    thread = new_thread(USER)
    routines: list[str] = []
    try:
        result = asyncio.run(
            turn(thread, f"Using the {name} integration, turn on the kitchen light.", slug)
        )
        names = [t["name"] for t in result["tools"]]
        if "open_integration" not in names or lights().get("kitchen") is not True:
            fail(
                f"the agent should open {slug} and turn the kitchen on: tools {names}, lights {lights()}"
            )
        if result["cards"]:
            fail(f"turn_on is allow on both sides, but a card came: {result['cards']!r}")
        log("OK: the agent opened the integration and turned a light on, without a card")

        result = asyncio.run(turn(thread, "Now turn the kitchen light off.", slug))
        if not any(c["args"].get("tool") == "turn_off" for c in result["cards"]):
            fail(f"turn_off is ask: expected its card, got {result['cards']!r}")
        if lights().get("kitchen") is not False:
            fail(f"approved, but the kitchen is still on ({integration_calls(result)})")
        log("OK: an ask tool stopped with a card in chat; approved, it ran")

        asked = asyncio.run(turn(thread, f"Set the movie scene with {name}.", slug))
        if not any("needs input from the user" in p for p in integration_calls(asked)):
            fail(f"set_scene should come back needing input: {summary(asked)}")
        answered = asyncio.run(turn(thread, "The office.", slug))
        if not any("scene movie in office" in p for p in integration_calls(answered)):
            fail(
                f"the answer should finish the call.\nasked: {summary(asked)}\nanswered: {summary(answered)}"
            )
        log("OK: the server's request for input went to the user and back")

        routine = ok(USER, "POST", "/api/routines", {
            "name": "e2e g18 lights",
            "prompt": f"Using the {name} integration, turn the office light off.",
            "schedule": {"kind": "daily", "time": "03:00"},
            "timezone": "UTC",
        }, 201)  # fmt: skip
        routines.append(routine["id"])
        run = ok(USER, "POST", f"/api/routines/{routine['id']}/run", want=202)
        run = wait_run(routine["id"], {"waiting_approval", "succeeded", "failed"})
        if run["status"] != "waiting_approval":
            fail(f"the routine run should stop on turn_off: {run['status']} {run.get('detail')}")
        state = ok(USER, "GET", f"/api/threads/{run['thread_id']}/state")
        pending = state["pending_approval"]
        if not any(a["args"].get("tool") == "turn_off" for a in pending["actions"]):
            fail(f"unexpected pending approval: {pending!r}")
        asyncio.run(turn(run["thread_id"], None, slug, answer={
            "type": "approval_response", "interrupt_id": pending["interrupt_id"],
            "decisions": [{"tool_call_id": a["tool_call_id"], "decision": "approve"} for a in pending["actions"]],
        }))  # fmt: skip
        run = wait_run(routine["id"], {"succeeded", "failed", "waiting_approval"})
        if run["status"] != "succeeded" or lights().get("office") is not False:
            fail(f"approved run ended {run['status']}; lights {lights()}")
        log("OK: an ask tool stopped a routine run; approved, it ran")
    finally:
        for routine_id in routines:
            for r in ok(USER, "GET", f"/api/routines/{routine_id}/runs") or []:
                api(USER, "DELETE", f"/api/threads/{r['thread_id']}")
            api(USER, "DELETE", f"/api/routines/{routine_id}")
        api(USER, "DELETE", f"/api/threads/{thread}")
    no_secrets()


def wait_run(routine_id: str, want: set[str]) -> dict:
    deadline = time.monotonic() + TURN_TIMEOUT_S
    while time.monotonic() < deadline:
        status, runs = api(USER, "GET", f"/api/routines/{routine_id}/runs")
        if status == 200 and runs and runs[0]["status"] in want:
            return runs[0]
        time.sleep(3)
    fail(f"run never reached {sorted(want)}")
    return {}


def main() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else "all"
    if phase == "cleanup":
        cleanup()
        return
    if phase == "oauth":
        oauth_phase()
        log("PASS")
        return
    integration = install()
    if phase in ("gateway", "all"):
        gateway_phase(integration)
    if phase == "all":
        oauth_phase()
    if phase in ("agent", "all"):
        agent_phase(integration)
    log("PASS")


if __name__ == "__main__":
    main()
