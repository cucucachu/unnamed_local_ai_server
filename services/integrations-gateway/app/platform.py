"""The gateway's calls to the platform (`/internal/*`, with `PLATFORM_GATEWAY_TOKEN`)."""

from __future__ import annotations

import logging
from typing import Any, Protocol
from uuid import UUID

import httpx2

from app.errors import GatewayError, PlatformUnavailable, Unauthorized
from app.oauth import OAuthError

logger = logging.getLogger(__name__)


class Platform(Protocol):
    async def grant(self, delegation: str, integration: str) -> dict[str, Any]: ...

    async def consume_approval(
        self, delegation: str, token: str, integration: str, tool: str, arguments: dict[str, Any]
    ) -> bool: ...

    async def audit(self, delegation: str, event: dict[str, Any]) -> None: ...

    async def store_discovery(self, integration_id: UUID, discovery: dict[str, Any]) -> None: ...

    async def mark_needs_reconnecting(self, integration_id: UUID, user_id: UUID | None) -> None: ...

    async def store_oauth(
        self, integration_id: UUID, summary: dict[str, Any] | None, endpoints: list[str]
    ) -> None: ...

    async def store_credential(
        self, integration_id: UUID, user_id: UUID | None, ciphertext: str
    ) -> None: ...


class HttpPlatform:
    def __init__(
        self, base_url: str, token: str, transport: httpx2.AsyncBaseTransport | None = None
    ) -> None:
        self._url = base_url.rstrip("/")
        self._token = token
        self._transport = transport

    async def _post(self, method: str, path: str, body: dict[str, Any]) -> httpx2.Response:
        try:
            async with httpx2.AsyncClient(timeout=15.0, transport=self._transport) as client:
                return await client.request(
                    method,
                    f"{self._url}{path}",
                    json=body,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
        except httpx2.HTTPError as exc:
            logger.warning("platform %s failed: %s", path, type(exc).__name__)
            raise PlatformUnavailable from exc

    async def grant(self, delegation: str, integration: str) -> dict[str, Any]:
        response = await self._post(
            "POST",
            "/internal/integration-grants",
            {"delegation": delegation, "integration": integration},
        )
        if response.status_code == 401:
            raise Unauthorized
        if response.status_code == 404:
            raise GatewayError("not_found", f"There's no integration named {integration!r}.")
        if response.status_code != 200:
            logger.warning("platform grant answered %d", response.status_code)
            raise PlatformUnavailable
        return response.json()

    async def consume_approval(
        self, delegation: str, token: str, integration: str, tool: str, arguments: dict[str, Any]
    ) -> bool:
        response = await self._post(
            "POST",
            "/internal/integration-approvals/consume",
            {
                "delegation": delegation,
                "token": token,
                "integration": integration,
                "tool": tool,
                "arguments": arguments,
            },
        )
        if response.status_code == 204:
            return True
        if response.status_code in (403, 404, 422):
            return False
        if response.status_code == 401:
            raise Unauthorized
        raise PlatformUnavailable

    async def audit(self, delegation: str, event: dict[str, Any]) -> None:
        """Best effort: an action already done outside can't be undone because the log failed."""
        try:
            response = await self._post(
                "POST", "/internal/integration-audit", {"delegation": delegation, **event}
            )
        except PlatformUnavailable:
            logger.error("audit not recorded: platform unavailable (%s)", event.get("action"))
            return
        if response.status_code != 204:
            logger.error("audit not recorded: platform answered %d", response.status_code)

    async def store_discovery(self, integration_id: UUID, discovery: dict[str, Any]) -> None:
        try:
            response = await self._post(
                "PUT", f"/internal/integrations/{integration_id}/discovery", discovery
            )
        except PlatformUnavailable:
            return
        if response.status_code != 204:
            logger.warning("rediscovery not stored: platform answered %d", response.status_code)

    async def mark_needs_reconnecting(self, integration_id: UUID, user_id: UUID | None) -> None:
        """Best effort: the caller still gets `auth_expired` either way."""
        try:
            response = await self._post(
                "POST",
                f"/internal/integrations/{integration_id}/credential-status",
                {"user_id": str(user_id) if user_id else None, "status": "needs_reconnecting"},
            )
        except PlatformUnavailable:
            return
        if response.status_code != 204:
            logger.warning(
                "credential status not stored: platform answered %d", response.status_code
            )

    async def store_oauth(
        self, integration_id: UUID, summary: dict[str, Any] | None, endpoints: list[str]
    ) -> None:
        """The OAuth hosts a server's metadata named; the egress rules follow. Must succeed."""
        response = await self._post(
            "PUT",
            f"/internal/integrations/{integration_id}/oauth",
            {"summary": summary, "endpoints": endpoints},
        )
        if response.status_code != 204:
            logger.warning("oauth endpoints not stored: platform answered %d", response.status_code)
            raise OAuthError("oauth_discovery_failed", "The platform refused the OAuth endpoints.")

    async def store_credential(
        self, integration_id: UUID, user_id: UUID | None, ciphertext: str
    ) -> None:
        """Best effort: a refreshed credential. Until it's stored, the gateway keeps the newest."""
        try:
            response = await self._post(
                "PUT",
                f"/internal/integrations/{integration_id}/credential",
                {"user_id": str(user_id) if user_id else None, "ciphertext": ciphertext},
            )
        except PlatformUnavailable:
            logger.error("refreshed credential not stored: platform unavailable")
            return
        if response.status_code != 204:
            logger.error(
                "refreshed credential not stored: platform answered %d", response.status_code
            )
