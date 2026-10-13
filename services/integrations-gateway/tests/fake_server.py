"""A small MCP server we control: a home with lights, in either spec version.

The gateway's unit tests use it in-process. `python -m tests.fake_server` serves it over
Streamable HTTP at `/mcp` for the live gate (scripts/e2e/g18_*): each request needs
`Authorization: Bearer <one of FAKE_MCP_TOKENS>` (comma-separated, from the environment),
and `GET /state` returns the lights without a token, so the gate can see what a call did.
"""

from __future__ import annotations

import json
import os

import anyio
import mcp_types as types
from mcp.server.mcpserver import Context, MCPServer

LIGHTS: dict[str, bool] = {}


def build(big_size: int = 50_000) -> MCPServer:
    srv = MCPServer("fake-home", instructions="Lights are named by room.")

    @srv.tool(annotations=types.ToolAnnotations(read_only_hint=True))
    def list_lights() -> str:
        """Every light and whether it's on."""
        return ", ".join(f"{k}={'on' if v else 'off'}" for k, v in sorted(LIGHTS.items())) or "none"

    @srv.tool()
    def turn_on(room: str, brightness: int = 100) -> str:
        """Turn a room's light on."""
        LIGHTS[room] = True
        return f"{room} on at {brightness}%"

    @srv.tool()
    def turn_off(room: str) -> str:
        """Turn a room's light off."""
        LIGHTS[room] = False
        return f"{room} off"

    @srv.tool()
    def hidden_tool() -> str:
        """The admin turned this one off."""
        return "should never run"

    @srv.tool()
    def broken() -> str:
        """Always fails."""
        raise ValueError("bulb exploded")

    @srv.tool()
    async def slow() -> str:
        """Takes too long."""
        await anyio.sleep(30)
        return "done"

    @srv.tool()
    def big() -> str:
        """A huge result."""
        return "H" * 1000 + "x" * big_size + "T" * 1000

    @srv.tool()
    def set_scene(name: str, ctx: Context) -> str | types.InputRequiredResult:
        """Asks which room before setting a scene."""
        answer = (ctx.input_responses or {}).get("room")
        if answer is None:
            return types.InputRequiredResult(
                input_requests={
                    "room": types.ElicitRequest(
                        params=types.ElicitRequestFormParams(
                            message="Which room?",
                            requested_schema={
                                "type": "object",
                                "properties": {"room": {"type": "string"}},
                                "required": ["room"],
                            },
                        )
                    )
                },
                request_state="scene:" + name,
            )
        if answer.action != "accept":
            return f"scene {name} {answer.action}d"
        return f"scene {name} in {answer.content['room']} (state {ctx.request_state})"

    @srv.resource("home://rooms", description="The rooms in the house")
    def rooms() -> str:
        return "kitchen, office"

    @srv.prompt()
    def evening(room: str) -> str:
        """Set up a room for the evening."""
        return f"Dim the {room} lights to 30%."

    return srv


def http_app(tokens: set[str], host: str = "127.0.0.1"):
    # On 127.0.0.1 the SDK only accepts `Host: localhost`-style headers (DNS rebinding
    # protection); the live gate reaches it by container IP, so it serves on 0.0.0.0.
    inner = build().streamable_http_app(streamable_http_path="/mcp", host=host)
    allowed = {f"Bearer {t}".encode() for t in tokens if t}

    async def app(scope, receive, send):
        if scope["type"] == "http":
            if scope["path"] == "/state" and scope["method"] == "GET":
                await _send_json(send, 200, LIGHTS)
                return
            if dict(scope["headers"]).get(b"authorization") not in allowed:
                await _send_json(send, 401, {"error": "unauthorized"})
                return
        await inner(scope, receive, send)

    return app


async def _send_json(send, status: int, body: object) -> None:
    raw = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": raw})


if __name__ == "__main__":
    import uvicorn

    tokens = set(os.environ.get("FAKE_MCP_TOKENS", "").split(","))
    uvicorn.run(
        http_app(tokens, host="0.0.0.0"), host="0.0.0.0", port=8000, log_level="warning", lifespan="on"
    )
