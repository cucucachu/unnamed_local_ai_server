"""OAuth sign-in for integrations (docs/INTEGRATIONS.md §5.1).

The gateway does the protocol: discovery, client registration, PKCE, the
code exchange, refreshing. The platform keeps what it reports (the hosts
the server's metadata names, which join the egress rules), the client
HomeAI registered per return address, and the sealed credential, and
serves the two pages the provider's sign-in needs without a session: the
return page and the client ID page.
"""

from __future__ import annotations

import base64
import ipaddress
import logging
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.core.config import Settings
from app.core.errors import InvalidInput, NotFound, Unavailable
from app.core.integrations import check_url, gateway_view, get, origin_of
from app.core.integrations_gateway import Gateway

logger = logging.getLogger(__name__)

RETURN_PATH = "/api/platform/integrations/oauth/return"
CLIENT_PATH = "/api/platform/integrations/oauth/client"
MAX_ENDPOINTS = 8


def check_endpoints(integration: dict[str, Any], endpoints: list[str]) -> list[str]:
    """The OAuth endpoints the gateway may reach for this server.

    Each is `https://` and public, or on the integration's own scheme, host
    and port (which keeps its private exception).
    """
    if len(endpoints) > MAX_ENDPOINTS:
        raise InvalidInput("too_many_endpoints")
    own = origin_of(integration["url"])
    for url in endpoints:
        try:
            parts = urlsplit(url)
            _ = parts.port
        except ValueError as exc:
            raise InvalidInput("invalid_endpoint") from exc
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise InvalidInput("invalid_endpoint")
        if parts.username is not None or parts.password is not None or parts.fragment:
            raise InvalidInput("invalid_endpoint")
        if origin_of(url) == own:
            continue
        try:
            check_url(url, private_address=False)
        except InvalidInput as exc:
            raise InvalidInput("invalid_endpoint") from exc
    return endpoints


async def store_discovered(
    conn: AsyncConnection,
    integration_id: UUID,
    summary: dict[str, Any] | None,
    endpoints: list[str],
) -> None:
    """Kept whatever the integration's sign-in; only an OAuth one reaches the endpoints.

    Without a summary it's discovery so far (the metadata documents to read
    next): added to what's kept, so tokens being refreshed meanwhile still
    reach their endpoint.
    """
    integration = await get(conn, integration_id)
    check_endpoints(integration, endpoints)
    kept = integration.get("oauth") or {}
    if summary is None and kept:
        merged = list(dict.fromkeys([*(kept.get("endpoints") or []), *endpoints]))
        if len(merged) <= MAX_ENDPOINTS:
            endpoints = merged
        summary = kept.get("summary")
    await conn.execute(
        "UPDATE integrations SET oauth = %s WHERE id = %s",
        (Jsonb({"summary": summary, "endpoints": endpoints}), integration_id),
    )


# --- clients --------------------------------------------------------------------------


async def client_for(
    conn: AsyncConnection, integration_id: UUID, redirect_uri: str
) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT client_id, redirect_uri, issuer, kind FROM integration_oauth_clients "
        "WHERE integration_id = %s AND redirect_uri = %s",
        (integration_id, redirect_uri),
    )
    return await cur.fetchone()


async def save_client(conn: AsyncConnection, integration_id: UUID, client: dict[str, Any]) -> None:
    if client.get("kind") not in ("dynamic", "client_id_url"):
        raise InvalidInput("invalid_client")
    await conn.execute(
        "INSERT INTO integration_oauth_clients (integration_id, redirect_uri, issuer, client_id, "
        "kind) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (integration_id, redirect_uri) DO UPDATE "
        "SET issuer = EXCLUDED.issuer, client_id = EXCLUDED.client_id, kind = EXCLUDED.kind, "
        "created_at = now()",
        (
            integration_id,
            client["redirect_uri"],
            client["issuer"],
            client["client_id"],
            client["kind"],
        ),
    )


async def forget(conn: AsyncConnection, integration_id: UUID) -> None:
    """A new address or sign-in: what was discovered and registered no longer applies."""
    await conn.execute("UPDATE integrations SET oauth = NULL WHERE id = %s", (integration_id,))
    await conn.execute(
        "DELETE FROM integration_oauth_clients WHERE integration_id = %s", (integration_id,)
    )


# --- refreshed credentials --------------------------------------------------------------


async def store_refreshed(
    conn: AsyncConnection, integration_id: UUID, user_id: UUID | None, ciphertext: bytes
) -> None:
    """The gateway refreshed a token: replace the stored ciphertext, never add one."""
    cur = await conn.execute(
        "UPDATE integration_credentials SET ciphertext = %s, status = 'connected', "
        "updated_at = now() WHERE integration_id = %s AND user_id IS NOT DISTINCT FROM %s",
        (ciphertext, integration_id, user_id),
    )
    if not cur.rowcount:
        raise NotFound("credential_not_found")


async def revoke(
    conn: AsyncConnection, gateway: Gateway, integration: dict[str, Any], owner: UUID | None
) -> None:
    """Before an OAuth credential is deleted: sign HomeAI out at the provider, best effort."""
    if integration["auth_method"] != "oauth":
        return
    cur = await conn.execute(
        "SELECT ciphertext FROM integration_credentials "
        "WHERE integration_id = %s AND user_id IS NOT DISTINCT FROM %s",
        (integration["id"], owner),
    )
    row = await cur.fetchone()
    if row is None:
        return
    credential = {"user_id": owner, "ciphertext": base64.b64encode(row["ciphertext"]).decode()}
    try:
        await gateway.oauth_revoke(gateway_view(integration), credential)
    except (InvalidInput, Unavailable) as exc:
        logger.warning("oauth token not revoked: %s", exc.code)


# --- the return address -------------------------------------------------------------------


def _split_host(raw: str) -> tuple[str, int | None]:
    try:
        parts = urlsplit(f"//{raw}")
        return (parts.hostname or "").lower().rstrip("."), parts.port
    except ValueError:
        return "", None


def own_hosts(settings: Settings) -> set[str]:
    """This server's own names: LAN addresses, VPN address, Caddy's local name, domain.

    Loopback names too: a browser on this machine reaches HomeAI there, and
    some providers only send the browser back to a loopback address.
    """
    hosts = set(settings.lan_addresses) | {"homeai.local", "localhost", "127.0.0.1", "::1"}
    for part in settings.origin_vpn_subnets.split(","):
        if part.strip():
            network = ipaddress.ip_network(part.strip(), strict=False)
            hosts.add(str(next(network.hosts())))
    if settings.homeai_domain.strip():
        hosts.add(settings.homeai_domain.strip().lower().rstrip("."))
    return hosts


def return_url(settings: Settings, host_header: str, https: bool) -> str:
    """Where the provider sends the browser back: the address this request came in on.

    Only one of this server's own addresses; anything else is
    `unknown_address` (a sign-in can't be started from there).
    """
    raw = host_header.split(",")[0].strip()
    host, port = _split_host(raw)
    if not host or host not in own_hosts(settings):
        raise InvalidInput("unknown_address")
    scheme = "https" if https else "http"
    shown = f"[{host}]" if ":" in host else host
    if port is not None and port != (443 if https else 80):
        shown = f"{shown}:{port}"
    return f"{scheme}://{shown}{RETURN_PATH}"


def client_page_url(return_to: str) -> str:
    parts = urlsplit(return_to)
    return f"{parts.scheme}://{parts.netloc}{CLIENT_PATH}"
