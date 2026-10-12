"""Opening an MCP client session to an integration (docs/INTEGRATIONS.md §10).

One short-lived session per gateway request: spec 2026-07-28 is stateless,
and the older handshake is cheap enough for v1. Every failure to reach or
talk to the server becomes a standard error here, so the rest of the gateway
deals only in `GatewayError`.
"""

from __future__ import annotations

import logging
import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx2
import mcp_types as types
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from pydantic import ValidationError

from app.errors import GatewayError

logger = logging.getLogger(__name__)

MODERN = "2026-07-28"
CLIENT_INFO = types.Implementation(name="homeai-integrations-gateway", version="1")


def connect_mode(protocol_version: str | None) -> str:
    """How to connect, from the version discovery recorded: older servers get the old handshake."""
    if protocol_version is None or protocol_version >= MODERN:
        return "auto"
    return "legacy"


async def _decline(context: Any, params: Any) -> types.ElicitResult:
    # Declared to modern servers only, which ask through `InputRequiredResult`
    # instead (handled by the caller); this answers anything else.
    return types.ElicitResult(action="decline")


@dataclass
class Session:
    """A connected client plus what the HTTP layer saw (for telling errors apart)."""

    client: Client
    statuses: list[int] = field(default_factory=list)
    list_changed: set[str] = field(default_factory=set)


class Connector(Protocol):
    def session(
        self, integration: dict[str, Any], headers: dict[str, str], *, elicitation: bool
    ) -> Any:
        """An async context manager yielding a `Session`."""


def _leaves(exc: BaseException) -> list[BaseException]:
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for e in exc.exceptions for leaf in _leaves(e)]
    return [exc]


def classify(exc: BaseException, statuses: list[int]) -> GatewayError:
    """The standard error for a failure while talking to a server."""
    if isinstance(exc, GatewayError):
        return exc
    leaves = _leaves(exc)
    gateway_errors = [e for e in leaves if isinstance(e, GatewayError)]
    if gateway_errors:
        return gateway_errors[0]
    if any(s in (401, 403) for s in statuses):
        return GatewayError("auth_expired")
    if any(isinstance(e, (TimeoutError, httpx2.TimeoutException)) for e in leaves):
        return GatewayError("timeout")
    if any(isinstance(e, (httpx2.TransportError, OSError)) for e in leaves):
        return GatewayError("unreachable")
    errors = [s for s in statuses if s >= 400]
    if errors:
        if errors[-1] >= 500:
            return GatewayError("server_error", f"HTTP {errors[-1]}.")
        return GatewayError("protocol_error", f"HTTP {errors[-1]}: is the URL the MCP endpoint?")
    for e in leaves:
        if isinstance(e, MCPError):
            return GatewayError("server_error", f"The server said (untrusted): {str(e)[:500]}")
        if isinstance(e, (ValidationError, RuntimeError)):
            return GatewayError("protocol_error")
    logger.exception("unexpected failure talking to an integration", exc_info=exc)
    return GatewayError("protocol_error")


@asynccontextmanager
async def guarded(statuses: list[int]) -> AsyncIterator[None]:
    """Map anything raised inside to a standard error (cancellation passes through)."""
    try:
        yield
    except GatewayError:
        raise
    except Exception as exc:
        raise classify(exc, statuses) from exc


class HttpConnector:
    """Streamable HTTP through the egress proxy."""

    def __init__(
        self,
        proxy_url: str,
        ca_bundle: str,
        connect_timeout_s: float,
        read_timeout_s: float,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._proxy = proxy_url or None
        self._verify: ssl.SSLContext | bool = (
            ssl.create_default_context(cafile=ca_bundle) if ca_bundle else True
        )
        self._timeout = httpx2.Timeout(read_timeout_s, connect=connect_timeout_s)
        self._transport = transport

    @asynccontextmanager
    async def session(
        self, integration: dict[str, Any], headers: dict[str, str], *, elicitation: bool
    ) -> AsyncIterator[Session]:
        statuses: list[int] = []
        changed: set[str] = set()

        async def record(response: httpx2.Response) -> None:
            statuses.append(response.status_code)

        async def on_message(message: Any) -> None:
            method = getattr(message, "method", "")
            if isinstance(method, str) and method.endswith("/list_changed"):
                changed.add(method.split("/")[1])

        http = httpx2.AsyncClient(
            headers=headers,
            proxy=self._proxy,
            verify=self._verify,
            timeout=self._timeout,
            transport=self._transport,
            trust_env=False,
            follow_redirects=False,
            event_hooks={"response": [record]},
        )
        mode = connect_mode(integration.get("protocol_version"))
        client = Client(
            streamable_http_client(integration["url"], http_client=http),
            mode=mode,
            client_info=CLIENT_INFO,
            cache=None,
            message_handler=on_message,
            elicitation_callback=_decline if elicitation and mode != "legacy" else None,
        )
        async with http, guarded(statuses), client:
            yield Session(client=client, statuses=statuses, list_changed=changed)
