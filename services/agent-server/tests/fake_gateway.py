"""A fake integrations gateway (M18-04's agent API), for the integration tools' tests.

It behaves like the real one where the agent depends on it: the bearer is
the user's delegation (checked against the fake platform), arguments are
checked before approval, a tool that asks runs only with a marker the
platform minted for exactly that call (spent once), and answers to a
`needs_input` handle are taken only after a new user message.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tests.fake_platform.scripting import FakePlatform

MESSAGES = {
    "not_found": "No such integration, tool, resource or prompt, or the tool is turned off.",
    "invalid_arguments": "The arguments don't match the tool's input schema. Fix them and retry.",
    "approval_required": "This tool asks first: the call needs the user's approval on its card.",
    "needs_input": "The server needs input from the user. Ask them, then call again with the handle.",
    "not_connected": "The user hasn't connected this integration.",
    "refused_by_policy": "Only the user can answer: ask them and wait for their reply.",
}


def _error(code: str, **extra: Any) -> dict:
    return {"ok": False, "error": {"code": code, "message": MESSAGES[code], **extra}}


@dataclass
class FakeTool:
    permission: str = "ask"
    required: tuple[str, ...] = ()
    # Asks the user for input the first time it's called.
    asks_for: dict | None = None


@dataclass
class FakeGateway:
    platform: FakePlatform
    base_url: str = ""
    slug: str = "home"
    status: str = "connected"
    tools: dict[str, FakeTool] = field(
        default_factory=lambda: {
            "get_state": FakeTool("allow"),
            "turn_on": FakeTool("ask", ("entity",)),
        }
    )
    # Every request: (path, body).
    requests: list[tuple[str, dict]] = field(default_factory=list)
    # Calls that reached the server: (tool, arguments, approval, answers).
    executed: list[tuple[str, dict, str, dict | None]] = field(default_factory=list)
    # handle -> (thread_id, tool, arguments, asked_after)
    handles: dict[str, tuple[str, str, dict, str | None]] = field(default_factory=dict)
    _counter: itertools.count = field(default_factory=itertools.count)


def create_fake_gateway_app(fake: FakeGateway) -> FastAPI:
    app = FastAPI()

    async def _who(request: Request):
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        return fake.platform.principal(bearer)

    @app.post("/v1/open")
    async def open_(request: Request) -> JSONResponse:
        body = await request.json()
        fake.requests.append(("/v1/open", body))
        if await _who(request) is None:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        if body["integration"] != fake.slug:
            return JSONResponse(_error("not_found"))
        return JSONResponse(
            {
                "ok": True,
                "integration": fake.slug,
                "instructions": "Use turn_on for lights.",
                "tools": [
                    {
                        "name": name,
                        "description": f"{name} things",
                        "input_schema": {"type": "object", "required": list(t.required)},
                        "permission": t.permission,
                    }
                    for name, t in fake.tools.items()
                ],
                "resources": [{"uri": "home://rooms", "name": "Rooms", "description": ""}],
                "prompts": [],
            }
        )

    @app.post("/v1/call")
    async def call(request: Request) -> JSONResponse:
        body = await request.json()
        fake.requests.append(("/v1/call", body))
        who = await _who(request)
        if who is None:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        if body["integration"] != fake.slug:
            return JSONResponse(_error("not_found"))
        if fake.status == "not_connected":
            return JSONResponse(_error("not_connected"))
        name, args = body["tool"], body.get("arguments") or {}
        spec = fake.tools.get(name)
        if spec is None:
            return JSONResponse(_error("not_found"))
        given = body.get("input")
        if given is not None:
            held = fake.handles.get(given["handle"])
            latest = body.get("latest_user_message")
            if held is None or held[0] != who.thread_id or held[1] != name:
                return JSONResponse(_error("not_found"))
            if latest is None or latest == held[3]:
                return JSONResponse(_error("refused_by_policy"))
            del fake.handles[given["handle"]]
            fake.executed.append((name, held[2], "inherited", given.get("responses")))
            return JSONResponse({"ok": True, "is_error": False, "text": f"{name} finished"})
        missing = [k for k in spec.required if k not in args]
        if missing:
            return JSONResponse(
                _error("invalid_arguments", errors=[f"missing {k}" for k in missing])
            )
        if spec.permission == "allow":
            approval = "allowed"
        else:
            marker = fake.platform.integration_markers.pop(body.get("approval") or "", None)
            if marker != (who.thread_id, fake.slug, name, args):
                return JSONResponse(_error("approval_required"))
            approval = "approved"
        if spec.asks_for is not None:
            handle = f"in-{next(fake._counter)}"
            fake.handles[handle] = (who.thread_id, name, args, body.get("latest_user_message"))
            return JSONResponse(_error("needs_input", handle=handle, requests=spec.asks_for))
        fake.executed.append((name, args, approval, None))
        return JSONResponse(
            {"ok": True, "is_error": False, "text": f"{name} ran", "trimmed": False}
        )

    @app.post("/v1/read")
    async def read(request: Request) -> JSONResponse:
        body = await request.json()
        fake.requests.append(("/v1/read", body))
        if await _who(request) is None:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return JSONResponse({"ok": True, "text": "Kitchen, Hall", "trimmed": True})

    @app.post("/v1/prompt")
    async def prompt(request: Request) -> JSONResponse:
        body = await request.json()
        fake.requests.append(("/v1/prompt", body))
        if await _who(request) is None:
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return JSONResponse({"ok": True, "text": "user: Dim the lights", "trimmed": False})

    return app
