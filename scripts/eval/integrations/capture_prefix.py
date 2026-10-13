"""The agent's real first model request (tools and system message), for the eval.

Runs the agent once against agent-server's fake model and fake platform, with a
Home Assistant integration in the user's list, and writes the request's `tools` and
system message to the path in argv[1]. Run from services/agent-server with its venv
(run.sh does).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from app.agent.approvals import ToolPolicy
from tests.conftest import _UvicornThreadServer
from tests.fake_model.scripting import FakeModel, TextTurn
from tests.fake_model.server import create_fake_model_app
from tests.fake_platform.scripting import FakePlatform
from tests.fake_platform.server import create_fake_platform_app
from tests.test_chat_ws import _drain_turn, _make_client

HOME_ASSISTANT = {
    "slug": "home-assistant",
    "name": "Home Assistant",
    "description": "Lights, media players and lists at home.",
    "status": "connected",
    "tools": [
        {"name": t["name"], "enabled": True}
        for t in json.loads((Path(__file__).parent / "ha_tools.json").read_text())
    ],
}


def main() -> None:
    model = FakeModel()
    model_server = _UvicornThreadServer(create_fake_model_app(model))
    platform = FakePlatform()
    platform.integrations = [HOME_ASSISTANT]
    platform_server = _UvicornThreadServer(create_fake_platform_app(platform))
    model_server.start()
    platform_server.start()
    try:
        model.base_url = f"http://127.0.0.1:{model_server.port}/v1"
        platform.base_url = f"http://127.0.0.1:{platform_server.port}"
        model.queue(TextTurn("ok"))
        client = _make_client(model, platform, tool_policy=ToolPolicy.allow_all())
        with client, client.websocket_connect("/ws/chat/eval-prefix") as ws:
            ws.send_json({"type": "user_message", "content": "hello"})
            _drain_turn(ws)
    finally:
        model_server.stop()
        platform_server.stop()
    first = model.requests[0]
    system = first["messages"][0]
    if system.get("role") != "system" or "home-assistant" not in json.dumps(system):
        sys.exit("capture_prefix: the first request has no system message with the integration")
    Path(sys.argv[1]).write_text(json.dumps({"tools": first["tools"], "system": system}) + "\n")
    print(f"captured {len(first['tools'])} tools, system message {len(json.dumps(system))} chars")


if __name__ == "__main__":
    main()
