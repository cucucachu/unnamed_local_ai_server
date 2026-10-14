"""The platform's calls to the integrations gateway (docs/PLATFORM.md §4 "Integrations").

The gateway holds the credential key and is the only one that talks to MCP
servers. The platform asks it for two things, with
`INTEGRATIONS_GATEWAY_TOKEN` as the bearer:

- `POST /encrypt` `{"integration_id", "user_id": uuid|null, "secret"}` →
  `{"ciphertext": base64}`: a credential sealed to that integration and
  user (null: the shared one). The secret is never stored or logged here.
- `POST /discover` `{"integration": {...}, "credential": {"user_id",
  "ciphertext"}|null}` → what the server offers (`schemas.DiscoveryIn`).
- `POST /check` `{"integration": {...}}` → whether the server answers without
  a credential and what sign-in it wants (`schemas.IntegrationCheckOut`).
- OAuth sign-in (docs/INTEGRATIONS.md §5.1): `POST /oauth/start`
  `{"integration", "owner", "started_by", "return_url", "client"}` →
  `{"authorize_url", "client": new registration|null, "oauth": summary}`;
  `POST /oauth/finish` `{"state", "code", "iss", "error"}` → the sealed
  credential and who it's for; `POST /oauth/revoke` `{"integration",
  "credential"}` → `{"revoked": bool}`, best effort.

Unconfigured or unreachable is `503 gateway_unavailable`; an error the
gateway reports (`{"detail": code}` with a 4xx) is passed on as a 422 with
its code, e.g. `unreachable` or `certificate_changed`.
"""

from __future__ import annotations

import base64
import binascii
import logging
from typing import Any, Protocol
from uuid import UUID

import httpx

from app.core.errors import InvalidInput, Unavailable

logger = logging.getLogger(__name__)

MAX_CIPHERTEXT = 16 * 1024


class Gateway(Protocol):
    async def encrypt(self, integration_id: UUID, user_id: UUID | None, secret: str) -> bytes: ...

    async def discover(
        self, integration: dict[str, Any], credential: dict[str, Any] | None
    ) -> dict[str, Any]: ...

    async def check(self, integration: dict[str, Any]) -> dict[str, Any]: ...

    async def oauth_start(
        self,
        integration: dict[str, Any],
        owner: UUID | None,
        started_by: dict[str, Any],
        return_url: str,
        client: dict[str, Any] | None,
    ) -> dict[str, Any]: ...

    async def oauth_finish(
        self, state: str, code: str | None, iss: str | None, error: str | None
    ) -> dict[str, Any]: ...

    async def oauth_revoke(
        self, integration: dict[str, Any], credential: dict[str, Any]
    ) -> dict[str, Any]: ...


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


class HttpGateway:
    def __init__(
        self,
        base_url: str,
        token: str,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._url = base_url.rstrip("/")
        self._token = token
        self._timeout_s = timeout_s
        self._transport = transport

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self._url or not self._token:
            raise Unavailable("gateway_unavailable")
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._url}{path}",
                    json=_jsonable(body),
                    headers={"Authorization": f"Bearer {self._token}"},
                )
        except httpx.HTTPError as exc:
            logger.warning("integrations gateway %s failed: %s", path, type(exc).__name__)
            raise Unavailable("gateway_unavailable") from exc
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if 400 <= response.status_code < 500 and isinstance(payload, dict):
            code = payload.get("detail")
            if isinstance(code, str) and code.replace("_", "").isalnum():
                raise InvalidInput(code)
        if response.status_code != 200 or not isinstance(payload, dict):
            logger.warning("integrations gateway %s answered %d", path, response.status_code)
            raise Unavailable("gateway_unavailable")
        return payload

    async def encrypt(self, integration_id: UUID, user_id: UUID | None, secret: str) -> bytes:
        payload = await self._post(
            "/encrypt", {"integration_id": integration_id, "user_id": user_id, "secret": secret}
        )
        return decode_ciphertext(payload.get("ciphertext"), Unavailable("gateway_unavailable"))

    async def discover(
        self, integration: dict[str, Any], credential: dict[str, Any] | None
    ) -> dict[str, Any]:
        return await self._post("/discover", {"integration": integration, "credential": credential})

    async def check(self, integration: dict[str, Any]) -> dict[str, Any]:
        return await self._post("/check", {"integration": integration})

    async def oauth_start(
        self,
        integration: dict[str, Any],
        owner: UUID | None,
        started_by: dict[str, Any],
        return_url: str,
        client: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return await self._post(
            "/oauth/start",
            {
                "integration": integration,
                "owner": owner,
                "started_by": started_by,
                "return_url": return_url,
                "client": client,
            },
        )

    async def oauth_finish(
        self, state: str, code: str | None, iss: str | None, error: str | None
    ) -> dict[str, Any]:
        return await self._post(
            "/oauth/finish", {"state": state, "code": code, "iss": iss, "error": error}
        )

    async def oauth_revoke(
        self, integration: dict[str, Any], credential: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._post(
            "/oauth/revoke", {"integration": integration, "credential": credential}
        )


def decode_ciphertext(value: Any, error: Exception | None = None) -> bytes:
    """Base64 ciphertext, as it travels between the gateway and the platform."""
    error = error or InvalidInput("invalid_ciphertext")
    if not isinstance(value, str) or not value:
        raise error
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise error from exc
    if not raw or len(raw) > MAX_CIPHERTEXT:
        raise error
    return raw
