from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer

from app.config import Settings
from app.connector import CLIENT_INFO, Session, _decline, connect_mode, guarded
from app.crypto import CredentialKey
from app.errors import GatewayError, Unauthorized
from app.handles import InputHandles
from app.service import Gateway
from tests import fake_server

DELEGATION = "delegation-alice"
USER = UUID("11111111-1111-1111-1111-111111111111")
INTEGRATION = UUID("22222222-2222-2222-2222-222222222222")
TOKEN = "ha-token-very-secret-123"


class InProcessConnector:
    """Connects straight to an in-process MCPServer, in the version discovery recorded."""

    def __init__(self, server: MCPServer) -> None:
        self.server = server
        self.headers: list[dict[str, str]] = []
        self.versions: list[str] = []
        self.capabilities: list[Any] = []

    @asynccontextmanager
    async def session(
        self, integration: dict[str, Any], headers: dict[str, str], *, elicitation: bool
    ) -> AsyncIterator[Session]:
        self.headers.append(headers)
        mode = connect_mode(integration.get("protocol_version"))
        client = Client(
            self.server,
            mode=mode,
            client_info=CLIENT_INFO,
            cache=None,
            elicitation_callback=_decline if elicitation and mode != "legacy" else None,
        )
        statuses: list[int] = []
        async with guarded(statuses), client:
            self.versions.append(client.protocol_version)
            self.capabilities.append(client.session._build_capabilities(client.protocol_version))
            yield Session(client=client, statuses=statuses)


def digest(arguments: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class FakePlatform:
    """The platform's internal endpoints, with the same marker rules as `app.core.hitl`."""

    def __init__(self, key: CredentialKey, protocol_version: str | None) -> None:
        self.key = key
        self.status = "connected"
        self.credential_status = "connected"
        self.credential_model = "per_user"
        self.protocol_version = protocol_version
        self.thread_id: str | None = "thread-1"
        self.ciphertext = base64.b64encode(
            key.seal(INTEGRATION, USER, {"kind": "token", "value": TOKEN})
        ).decode()
        self.tools = {
            "list_lights": {"available": True, "permission": "allow", "can_always_allow": True},
            "turn_on": {"available": True, "permission": "ask", "can_always_allow": True},
            "hidden_tool": {"available": False, "permission": "ask", "can_always_allow": True},
            "broken": {"available": True, "permission": "allow", "can_always_allow": True},
            "slow": {"available": True, "permission": "allow", "can_always_allow": True},
            "big": {"available": True, "permission": "allow", "can_always_allow": True},
            "set_scene": {"available": True, "permission": "allow", "can_always_allow": True},
        }
        self.markers: dict[str, tuple[str, str, str]] = {}
        self.audits: list[dict[str, Any]] = []
        self.reconnect: list[tuple[UUID, UUID | None]] = []
        self.discoveries: list[dict[str, Any]] = []

    def mint(self, tool: str, arguments: dict[str, Any]) -> str:
        token = f"marker-{uuid4().hex}"
        self.markers[token] = ("home", tool, digest(arguments))
        return token

    async def grant(self, delegation: str, integration: str) -> dict[str, Any]:
        if delegation != DELEGATION:
            raise Unauthorized
        if integration != "home":
            raise GatewayError("not_found")
        credential_user = USER if self.credential_model == "per_user" else None
        return {
            "user_id": str(USER),
            "session_id": str(uuid4()),
            "thread_id": self.thread_id,
            "integration": {
                "id": str(INTEGRATION),
                "slug": "home",
                "url": "http://fake/mcp",
                "auth_method": "token",
                "credential_model": self.credential_model,
                "cert_fingerprint": None,
                "private_address": True,
                "protocol_version": self.protocol_version,
            },
            "status": self.status,
            "credential": {
                "user_id": str(credential_user) if credential_user else None,
                "ciphertext": self.ciphertext,
                "status": self.credential_status,
            },
            "tools": self.tools,
        }

    async def consume_approval(
        self, delegation: str, token: str, integration: str, tool: str, arguments: dict[str, Any]
    ) -> bool:
        subject = self.markers.pop(token, None)
        return subject == (integration, tool, digest(arguments))

    async def audit(self, delegation: str, event: dict[str, Any]) -> None:
        self.audits.append(event)

    async def store_discovery(self, integration_id: UUID, discovery: dict[str, Any]) -> None:
        self.discoveries.append(discovery)

    async def mark_needs_reconnecting(self, integration_id: UUID, user_id: UUID | None) -> None:
        self.reconnect.append((integration_id, user_id))


@pytest.fixture
def key(tmp_path: Path) -> CredentialKey:
    return CredentialKey.load_or_create(tmp_path / "keys" / "credentials.key")


@pytest.fixture(params=["2026-07-28", "2025-11-25"])
def protocol_version(request: pytest.FixtureRequest) -> str:
    return request.param


@pytest.fixture
def settings() -> Settings:
    return Settings(call_timeout_s=2.0, result_max_chars=3000, input_handle_ttl_s=60)


@pytest.fixture
def platform(key: CredentialKey, protocol_version: str) -> FakePlatform:
    return FakePlatform(key, protocol_version)


@pytest.fixture
def connector() -> InProcessConnector:
    fake_server.LIGHTS.clear()
    return InProcessConnector(fake_server.build())


@pytest.fixture
def gateway(
    settings: Settings, key: CredentialKey, platform: FakePlatform, connector: InProcessConnector
) -> Gateway:
    return Gateway(settings, key, platform, connector, InputHandles(settings.input_handle_ttl_s))
