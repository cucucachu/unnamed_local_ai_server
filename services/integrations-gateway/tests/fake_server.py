"""A small MCP server we control: a home with lights, in either spec version."""

from __future__ import annotations

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
