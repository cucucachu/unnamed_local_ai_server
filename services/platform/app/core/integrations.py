"""Integrations registry (M18-03, docs/INTEGRATIONS.md, docs/PLATFORM.md §4 "Integrations").

An integration is one MCP server an admin installed for the household:
where it is, how it's signed in to, and what the integrations gateway last
discovered (tools, resources, prompts). Its tools follow the agent tool
permissions (`app.core.tool_permissions`) under the setting key
`mcp:<integration id>:<tool name>`, with two differences: a tool the gateway
discovers starts off for the admin, and every user's own setting starts at
ask (the server's annotations never change a default).

Credentials are ciphertext only. The gateway encrypts them (bound to the
integration and user) and is the only one that can decrypt them. The only
reader is the gateway's grant for a call (`grant`); no person-facing view
returns any of it.

Who may use an integration is `usable_by`, the one check every path uses
(everyone, for now; #340 limits it to specific people).
"""

from __future__ import annotations

import base64
import ipaddress
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.core import audit, tool_permissions
from app.core.errors import Conflict, InvalidInput, NotFound, Unavailable
from app.core.integrations_gateway import Gateway
from app.core.principal import Principal
from app.core.tool_permissions import Tool

CredentialModel = Literal["per_user", "shared", "none"]
Status = Literal["ready", "connected", "not_connected", "needs_reconnecting"]
KEY_PREFIX = "mcp"

_COLUMNS = (
    "id, slug, name, url, description, credential_model, auth_method, cert_fingerprint, "
    "private_address, token_header, server_info, protocol_version, instructions, discovered_at, "
    "installed_by, created_at, updated_at"
)


def setting_key(integration_id: UUID, tool: str) -> str:
    return f"{KEY_PREFIX}:{integration_id}:{tool}"


def _key_prefix(integration_id: UUID) -> str:
    return f"{KEY_PREFIX}:{integration_id}:"


# --- addresses ----------------------------------------------------------------------


@dataclass(frozen=True)
class Address:
    scheme: str
    host: str
    port: int


def check_url(url: str, private_address: bool) -> Address:
    """The install rules for a server's address (docs/INTEGRATIONS.md §4).

    `private_address` is the admin's explicit exception, for this host and
    port only. A name is taken as public here; the egress proxy refuses a
    name that resolves to a private address without the exception.
    """
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise InvalidInput("invalid_url") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise InvalidInput("invalid_url")
    if parts.username is not None or parts.password is not None or parts.fragment:
        raise InvalidInput("invalid_url")
    host = parts.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        raise InvalidInput("loopback_refused")
    if host.endswith(".local"):
        raise InvalidInput("mdns_name")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_loopback or ip.is_unspecified or ip.is_multicast:
            raise InvalidInput("loopback_refused")
        if (ip.is_private or ip.is_link_local) and not private_address:
            raise InvalidInput("private_address_needs_exception")
    if parts.scheme == "http" and not private_address:
        raise InvalidInput("https_required")
    return Address(parts.scheme, host, port or (443 if parts.scheme == "https" else 80))


# Headers a token may not go in: HTTP's own framing and hop-by-hop headers,
# the egress proxy's credential, and the ones the MCP client sets itself.
RESERVED_HEADERS = frozenset(
    {
        "accept",
        "accept-encoding",
        "connection",
        "content-encoding",
        "content-length",
        "content-type",
        "cookie",
        "host",
        "keep-alive",
        "last-event-id",
        "mcp-protocol-version",
        "mcp-session-id",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "user-agent",
    }
)


def check_token_header(name: str | None) -> str | None:
    """The header a token goes in, if not `Authorization: Bearer`; None for that default."""
    if name is None or name.lower() == "authorization":
        return None
    lowered = name.lower()
    if lowered in RESERVED_HEADERS or lowered.startswith(("proxy-", "sec-")):
        raise InvalidInput("reserved_header")
    return name


def on_this_machine(url: str, lan_addresses: frozenset[str]) -> bool | None:
    """Whether the server is this machine (by its configured LAN addresses); None if unknown.

    Such a server is reached through the host firewall, which blocks it
    until the admin allows that port from the egress proxy's network.
    """
    if not lan_addresses:
        return None
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(host)) in lan_addresses
    except ValueError:
        return None


def plain_http(url: str) -> bool:
    """Whether a credential for this server would cross the network unencrypted."""
    return urlsplit(url).scheme == "http"


# --- who may use it -------------------------------------------------------------------


def usable_by(integration: dict[str, Any], user_id: UUID) -> bool:
    """Whether `user_id` may use `integration` at all. Every user, until #340."""
    return True


# --- reading ----------------------------------------------------------------------------


async def _all(conn: AsyncConnection) -> list[dict[str, Any]]:
    cur = await conn.execute(f"SELECT {_COLUMNS} FROM integrations ORDER BY name, slug")
    return await cur.fetchall()


async def get(conn: AsyncConnection, integration_id: UUID) -> dict[str, Any]:
    cur = await conn.execute(
        f"SELECT {_COLUMNS} FROM integrations WHERE id = %s", (integration_id,)
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("integration_not_found")
    return row


async def by_slug(conn: AsyncConnection, slug: str) -> dict[str, Any]:
    cur = await conn.execute(f"SELECT {_COLUMNS} FROM integrations WHERE slug = %s", (slug,))
    row = await cur.fetchone()
    if row is None:
        raise NotFound("integration_not_found")
    return row


async def _tools(conn: AsyncConnection, integration_id: UUID) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT name, title, description, input_schema, annotations FROM integration_tools "
        "WHERE integration_id = %s ORDER BY position",
        (integration_id,),
    )
    return await cur.fetchall()


async def _resources(conn: AsyncConnection, integration_id: UUID) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT uri, name, title, description, mime_type FROM integration_resources "
        "WHERE integration_id = %s ORDER BY position",
        (integration_id,),
    )
    return await cur.fetchall()


async def _prompts(conn: AsyncConnection, integration_id: UUID) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT name, title, description, arguments FROM integration_prompts "
        "WHERE integration_id = %s ORDER BY position",
        (integration_id,),
    )
    return await cur.fetchall()


async def tool_names(conn: AsyncConnection, integration_id: UUID) -> set[str]:
    return {row["name"] for row in await _tools(conn, integration_id)}


def _as_tool(integration: dict[str, Any], row: dict[str, Any]) -> Tool:
    """An integration tool in the permissions rule's terms (never read-only by default)."""
    return Tool(
        setting_key(integration["id"], row["name"]),
        (row["title"] or row["name"])[:80],
        row["description"][:500],
        integration["slug"],
        False,
    )


async def _credential_row(
    conn: AsyncConnection, integration_id: UUID, user_id: UUID | None
) -> dict[str, Any] | None:
    cur = await conn.execute(
        "SELECT user_id, ciphertext, status, updated_at FROM integration_credentials "
        "WHERE integration_id = %s AND user_id IS NOT DISTINCT FROM %s",
        (integration_id, user_id),
    )
    return await cur.fetchone()


def _credential_owner(integration: dict[str, Any], user_id: UUID) -> UUID | None:
    """Whose credential a call by `user_id` uses: theirs, or the shared one (None)."""
    return user_id if integration["credential_model"] == "per_user" else None


def _status(integration: dict[str, Any], credential: dict[str, Any] | None) -> Status:
    if integration["credential_model"] == "none":
        return "ready"
    if credential is None:
        return "not_connected"
    return credential["status"]


def _summary_out(integration: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": integration["id"],
        "slug": integration["slug"],
        "name": integration["name"],
        "description": integration["description"],
        "credential_model": integration["credential_model"],
        "auth_method": integration["auth_method"],
        "plain_http": plain_http(integration["url"]),
    }


async def user_view(conn: AsyncConnection, user_id: UUID) -> list[dict[str, Any]]:
    """The integrations a user may use: their connection and effective tool settings."""
    admin, mine = (
        await tool_permissions.admin_rows(conn),
        await tool_permissions.user_rows(conn, user_id),
    )
    out = []
    for integration in await _all(conn):
        if not usable_by(integration, user_id):
            continue
        credential = await _credential_row(
            conn, integration["id"], _credential_owner(integration, user_id)
        )
        tools = []
        for row in await _tools(conn, integration["id"]):
            tool = _as_tool(integration, row)
            effective = tool_permissions.evaluate(tool, admin.get(tool.name), mine.get(tool.name))
            tools.append(
                {
                    "name": row["name"],
                    "title": row["title"],
                    "description": row["description"],
                    "annotations": row["annotations"],
                    "enabled": effective.available,
                    "permission": effective.permission,
                    "enabled_locked": effective.enabled_locked,
                    "permission_locked": effective.permission_locked,
                }
            )
        out.append(
            {
                **_summary_out(integration),
                "status": _status(integration, credential),
                "tools": tools,
            }
        )
    return out


async def admin_view(conn: AsyncConnection) -> list[dict[str, Any]]:
    admin = await tool_permissions.admin_rows(conn)
    out = []
    for integration in await _all(conn):
        iid = integration["id"]
        tools = []
        for row in await _tools(conn, iid):
            setting = admin.get(setting_key(iid, row["name"])) or {}
            tools.append(
                {
                    "name": row["name"],
                    "title": row["title"],
                    "description": row["description"],
                    "annotations": row["annotations"],
                    "enabled": setting.get("enabled") is not False,
                    "ceiling": setting.get("ceiling") or "allow",
                }
            )
        cur = await conn.execute(
            "SELECT c.user_id, u.username, u.display_name, c.status, c.updated_at "
            "FROM integration_credentials c LEFT JOIN users u ON u.id = c.user_id "
            "WHERE c.integration_id = %s ORDER BY u.username NULLS FIRST",
            (iid,),
        )
        connections = await cur.fetchall()
        shared = next((c for c in connections if c["user_id"] is None), None)
        out.append(
            {
                **_summary_out(integration),
                "url": integration["url"],
                "cert_fingerprint": integration["cert_fingerprint"],
                "private_address": integration["private_address"],
                "token_header": integration["token_header"],
                "server_info": integration["server_info"],
                "protocol_version": integration["protocol_version"],
                "discovered_at": integration["discovered_at"],
                "installed_by": integration["installed_by"],
                "created_at": integration["created_at"],
                "shared_credential": (
                    _status(integration, shared)
                    if integration["credential_model"] == "shared"
                    else None
                ),
                "connections": [
                    {
                        "user_id": c["user_id"],
                        "username": c["username"],
                        "display_name": c["display_name"],
                        "status": c["status"],
                        "updated_at": c["updated_at"],
                    }
                    for c in connections
                    if c["user_id"] is not None
                ],
                "tools": tools,
                "resources": await _resources(conn, iid),
                "prompts": await _prompts(conn, iid),
            }
        )
    return out


# --- installing and editing ---------------------------------------------------------------


def _check_models(credential_model: str, auth_method: str, token_header: str | None) -> None:
    if (credential_model == "none") != (auth_method == "none"):
        raise InvalidInput("auth_method_mismatch")
    if token_header is not None and auth_method != "token":
        raise InvalidInput("token_header_needs_token_auth")


async def install(conn: AsyncConnection, principal: Principal, fields: dict[str, Any]) -> UUID:
    check_url(fields["url"], fields.get("private_address", False))
    token_header = check_token_header(fields.get("token_header"))
    _check_models(fields["credential_model"], fields["auth_method"], token_header)
    async with conn.transaction():
        cur = await conn.execute("SELECT 1 FROM integrations WHERE slug = %s", (fields["slug"],))
        if await cur.fetchone():
            raise Conflict("slug_taken")
        cur = await conn.execute(
            "INSERT INTO integrations (slug, name, url, description, credential_model, "
            "auth_method, cert_fingerprint, private_address, token_header, installed_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                fields["slug"],
                fields["name"],
                fields["url"],
                fields.get("description", ""),
                fields["credential_model"],
                fields["auth_method"],
                fields.get("cert_fingerprint"),
                fields.get("private_address", False),
                token_header,
                principal.user_id,
            ),
        )
        integration_id = (await cur.fetchone())["id"]
        await audit.record(
            conn,
            principal,
            "integrations.installed",
            f"Installed integration {fields['name']} ({fields['url']})",
            target_type="integration",
            target_id=integration_id,
            detail={k: v for k, v in fields.items() if k != "description"},
        )
    return integration_id


async def edit(
    conn: AsyncConnection, principal: Principal, integration_id: UUID, changes: dict[str, Any]
) -> None:
    """Change an integration's settings.

    A new URL, or a new credential model, deletes every stored credential:
    a credential is only ever sent to the server it was entered for, and
    only under the model it was entered for. A new URL also clears the
    pinned certificate unless the change sets one.
    """
    async with conn.transaction():
        current = await get(conn, integration_id)
        merged = {**current, **changes}
        if (
            "url" in changes
            and changes["url"] != current["url"]
            and "cert_fingerprint" not in changes
        ):
            merged["cert_fingerprint"] = None
        if merged["auth_method"] != "token" and "token_header" not in changes:
            merged["token_header"] = None
        check_url(merged["url"], merged["private_address"])
        merged["token_header"] = check_token_header(merged["token_header"])
        _check_models(merged["credential_model"], merged["auth_method"], merged["token_header"])
        if merged["slug"] != current["slug"]:
            cur = await conn.execute(
                "SELECT 1 FROM integrations WHERE slug = %s AND id <> %s",
                (merged["slug"], integration_id),
            )
            if await cur.fetchone():
                raise Conflict("slug_taken")
        wipe = (
            merged["url"] != current["url"]
            or merged["credential_model"] != current["credential_model"]
        )
        await conn.execute(
            "UPDATE integrations SET slug = %s, name = %s, url = %s, description = %s, "
            "credential_model = %s, auth_method = %s, cert_fingerprint = %s, "
            "private_address = %s, token_header = %s, updated_at = now() WHERE id = %s",
            (
                merged["slug"],
                merged["name"],
                merged["url"],
                merged["description"],
                merged["credential_model"],
                merged["auth_method"],
                merged["cert_fingerprint"],
                merged["private_address"],
                merged["token_header"],
                integration_id,
            ),
        )
        if wipe:
            await conn.execute(
                "DELETE FROM integration_credentials WHERE integration_id = %s", (integration_id,)
            )
        await audit.record(
            conn,
            principal,
            "integrations.changed",
            f"Changed integration {merged['name']}"
            + (" (stored credentials deleted)" if wipe else ""),
            target_type="integration",
            target_id=integration_id,
            detail={"changes": changes, "credentials_deleted": wipe},
        )


async def uninstall(conn: AsyncConnection, principal: Principal, integration_id: UUID) -> None:
    """Delete it, its admin and user tool settings, and every stored credential."""
    async with conn.transaction():
        integration = await get(conn, integration_id)
        pattern = _key_prefix(integration_id) + "%"
        await conn.execute("DELETE FROM agent_tool_admin_settings WHERE tool LIKE %s", (pattern,))
        await conn.execute("DELETE FROM agent_tool_user_settings WHERE tool LIKE %s", (pattern,))
        await conn.execute("DELETE FROM integrations WHERE id = %s", (integration_id,))
        await audit.record(
            conn,
            principal,
            "integrations.uninstalled",
            f"Uninstalled integration {integration['name']}",
            target_type="integration",
            target_id=integration_id,
        )


# --- discovery --------------------------------------------------------------------------


async def store_discovery(
    conn: AsyncConnection,
    actor: Principal | None,
    integration_id: UUID,
    discovery: dict[str, Any],
) -> list[str]:
    """Replace what the server offers with what the gateway found; returns the new tools.

    A tool not seen before starts off for the admin. A tool that's gone
    loses its settings, so if it comes back it starts off again.
    """
    async with conn.transaction():
        integration = await get(conn, integration_id)
        before = {t["name"] for t in await _tools(conn, integration_id)}
        tools = discovery["tools"]
        after = [t["name"] for t in tools]
        added = [name for name in after if name not in before]
        gone = before - set(after)

        await conn.execute(
            "DELETE FROM integration_tools WHERE integration_id = %s", (integration_id,)
        )
        for position, t in enumerate(tools):
            await conn.execute(
                "INSERT INTO integration_tools (integration_id, name, title, description, "
                "input_schema, annotations, position) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    integration_id,
                    t["name"],
                    t.get("title"),
                    t.get("description") or "",
                    Jsonb(t["input_schema"]),
                    Jsonb(t.get("annotations") or {}),
                    position,
                ),
            )
        await conn.execute(
            "DELETE FROM integration_resources WHERE integration_id = %s", (integration_id,)
        )
        for position, r in enumerate(discovery["resources"]):
            await conn.execute(
                "INSERT INTO integration_resources (integration_id, uri, name, title, "
                "description, mime_type, position) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    integration_id,
                    r["uri"],
                    r["name"],
                    r.get("title"),
                    r.get("description") or "",
                    r.get("mime_type"),
                    position,
                ),
            )
        await conn.execute(
            "DELETE FROM integration_prompts WHERE integration_id = %s", (integration_id,)
        )
        for position, p in enumerate(discovery["prompts"]):
            await conn.execute(
                "INSERT INTO integration_prompts (integration_id, name, title, description, "
                "arguments, position) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    integration_id,
                    p["name"],
                    p.get("title"),
                    p.get("description") or "",
                    Jsonb(p.get("arguments") or []),
                    position,
                ),
            )

        gone_keys = [setting_key(integration_id, name) for name in gone]
        if gone_keys:
            await conn.execute(
                "DELETE FROM agent_tool_admin_settings WHERE tool = ANY(%s)", (gone_keys,)
            )
            await conn.execute(
                "DELETE FROM agent_tool_user_settings WHERE tool = ANY(%s)", (gone_keys,)
            )
        for name in added:
            await conn.execute(
                "INSERT INTO agent_tool_admin_settings (tool, enabled, ceiling, updated_by) "
                "VALUES (%s, false, NULL, NULL) ON CONFLICT (tool) DO UPDATE SET "
                "enabled = false, ceiling = NULL, updated_by = NULL, updated_at = now()",
                (setting_key(integration_id, name),),
            )

        server = discovery.get("server") or {}
        description = integration["description"] or (server.get("description") or "")[:500]
        await conn.execute(
            "UPDATE integrations SET server_info = %s, protocol_version = %s, instructions = %s, "
            "description = %s, discovered_at = now(), updated_at = now() WHERE id = %s",
            (
                Jsonb(server),
                discovery.get("protocol_version"),
                discovery.get("instructions") or "",
                description,
                integration_id,
            ),
        )
        await audit.record(
            conn,
            actor,
            "integrations.discovered",
            f"Discovered {len(tools)} tools, {len(discovery['resources'])} resources and "
            f"{len(discovery['prompts'])} prompts on {integration['name']}"
            + (f"; new (off): {', '.join(added)}" if added and before else ""),
            target_type="integration",
            target_id=integration_id,
            detail={"added": added, "removed": sorted(gone)},
        )
    return added


async def discovery_credential(
    conn: AsyncConnection, integration: dict[str, Any], admin: Principal
) -> dict[str, Any] | None:
    """The credential an admin's Refresh discovers with: theirs, or the shared one."""
    if integration["credential_model"] == "none":
        return None
    owner = _credential_owner(integration, admin.user_id)
    row = await _credential_row(conn, integration["id"], owner)
    if row is None:
        return None
    return {"user_id": owner, "ciphertext": _b64(row["ciphertext"])}


def gateway_view(integration: dict[str, Any]) -> dict[str, Any]:
    """What the gateway needs to reach the server."""
    return {
        "id": integration["id"],
        "slug": integration["slug"],
        "url": integration["url"],
        "auth_method": integration["auth_method"],
        "credential_model": integration["credential_model"],
        "cert_fingerprint": integration["cert_fingerprint"],
        "private_address": integration["private_address"],
        "token_header": integration["token_header"],
        "protocol_version": integration["protocol_version"],
    }


async def egress_rules(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Where the gateway may go (M18-05): each integration's exact scheme, host and port."""
    rules = []
    for row in await _all(conn):
        try:
            address = check_url(row["url"], row["private_address"])
        except InvalidInput:
            continue
        rules.append(
            {
                "integration": row["slug"],
                "scheme": address.scheme,
                "host": address.host,
                "port": address.port,
                "private_address": row["private_address"],
                "cert_fingerprint": row["cert_fingerprint"],
            }
        )
    return rules


# --- tool settings ------------------------------------------------------------------------


async def _tool_map(conn: AsyncConnection, integration: dict[str, Any]) -> dict[str, Tool]:
    return {
        row["name"]: _as_tool(integration, row) for row in await _tools(conn, integration["id"])
    }


def _keyed(
    integration_id: UUID, tools: dict[str, Tool], changes: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Tool]]:
    for c in changes:
        if c["name"] not in tools:
            raise InvalidInput("unknown_tool")
    keyed = [{**c, "name": setting_key(integration_id, c["name"])} for c in changes]
    return keyed, {t.name: t for t in tools.values()}


async def change_my_tools(
    conn: AsyncConnection, principal: Principal, integration_id: UUID, changes: list[dict[str, Any]]
) -> None:
    integration = await get(conn, integration_id)
    if not usable_by(integration, principal.user_id):
        raise NotFound("integration_not_found")
    keyed, tools = _keyed(integration_id, await _tool_map(conn, integration), changes)
    await tool_permissions.change_mine(conn, principal, keyed, tools)


async def change_admin_tools(
    conn: AsyncConnection, principal: Principal, integration_id: UUID, changes: list[dict[str, Any]]
) -> None:
    integration = await get(conn, integration_id)
    keyed, tools = _keyed(integration_id, await _tool_map(conn, integration), changes)
    await tool_permissions.change_admin(conn, principal, keyed, tools)


# --- credentials ------------------------------------------------------------------------


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


async def check_credential(
    gateway: Gateway, integration: dict[str, Any], owner: UUID | None, ciphertext: bytes
) -> dict[str, Any]:
    """Ask the server, through the gateway, whether it takes a credential before it's stored.

    A credential the server refuses is `credential_rejected`. One that
    couldn't be checked (the server unreachable, ...) is reported as such and
    still kept: the first call tells.
    """
    try:
        await gateway.discover(
            gateway_view(integration), {"user_id": owner, "ciphertext": _b64(ciphertext)}
        )
    except InvalidInput as exc:
        if exc.code == "auth_expired":
            raise InvalidInput("credential_rejected") from exc
        return {"checked": False, "problem": exc.code}
    except Unavailable as exc:
        return {"checked": False, "problem": exc.code}
    return {"checked": True, "problem": None}


async def store_credential(
    conn: AsyncConnection,
    principal: Principal,
    integration_id: UUID,
    user_id: UUID | None,
    ciphertext: bytes,
) -> None:
    """Store a credential the gateway encrypted: a user's own, or the shared one (None)."""
    async with conn.transaction():
        integration = await get(conn, integration_id)
        want = "shared" if user_id is None else "per_user"
        if integration["credential_model"] != want:
            raise InvalidInput("wrong_credential_model")
        if user_id is not None and not usable_by(integration, user_id):
            raise NotFound("integration_not_found")
        await conn.execute(
            "INSERT INTO integration_credentials (integration_id, user_id, ciphertext, set_by) "
            "VALUES (%s, %s, %s, %s) ON CONFLICT (integration_id, user_id) DO UPDATE SET "
            "ciphertext = EXCLUDED.ciphertext, status = 'connected', set_by = EXCLUDED.set_by, "
            "updated_at = now()",
            (integration_id, user_id, ciphertext, principal.user_id),
        )
        await audit.record(
            conn,
            principal,
            "integrations.connected",
            (
                f"Set the shared credential for {integration['name']}"
                if user_id is None
                else f"Connected to {integration['name']}"
            ),
            target_type="integration",
            target_id=integration_id,
            detail={"shared": user_id is None},
        )


async def delete_credential(
    conn: AsyncConnection, principal: Principal, integration_id: UUID, user_id: UUID | None
) -> None:
    """Disconnect a user, or clear the shared credential (None). Absent is fine."""
    async with conn.transaction():
        integration = await get(conn, integration_id)
        cur = await conn.execute(
            "DELETE FROM integration_credentials "
            "WHERE integration_id = %s AND user_id IS NOT DISTINCT FROM %s",
            (integration_id, user_id),
        )
        if cur.rowcount:
            whom = (
                "the shared credential"
                if user_id is None
                else ("themselves" if user_id == principal.user_id else str(user_id))
            )
            await audit.record(
                conn,
                principal,
                "integrations.disconnected",
                f"Disconnected {whom} from {integration['name']}",
                target_type="integration",
                target_id=integration_id,
                detail={"user_id": str(user_id) if user_id else None},
            )


async def mark_needs_reconnecting(
    conn: AsyncConnection, integration_id: UUID, user_id: UUID | None
) -> None:
    """The gateway found the credential rejected (`auth_expired`)."""
    await get(conn, integration_id)
    cur = await conn.execute(
        "UPDATE integration_credentials SET status = 'needs_reconnecting', updated_at = now() "
        "WHERE integration_id = %s AND user_id IS NOT DISTINCT FROM %s",
        (integration_id, user_id),
    )
    if not cur.rowcount:
        raise NotFound("credential_not_found")


# --- the gateway's grant ------------------------------------------------------------------


async def grant(conn: AsyncConnection, agent: Principal, slug: str) -> dict[str, Any]:
    """What applies to one agent call: the server, the credential to use, and the tools."""
    integration = await by_slug(conn, slug)
    if not usable_by(integration, agent.user_id):
        raise NotFound("integration_not_found")
    owner = _credential_owner(integration, agent.user_id)
    credential = (
        None
        if integration["credential_model"] == "none"
        else await _credential_row(conn, integration["id"], owner)
    )
    admin = await tool_permissions.admin_rows(conn)
    mine = await tool_permissions.user_rows(conn, agent.user_id)
    tools = {}
    for row in await _tools(conn, integration["id"]):
        tool = _as_tool(integration, row)
        effective = tool_permissions.evaluate(tool, admin.get(tool.name), mine.get(tool.name))
        tools[row["name"]] = {
            "available": effective.available,
            "permission": effective.permission,
            "can_always_allow": not effective.permission_locked,
        }
    return {
        "user_id": agent.user_id,
        "session_id": agent.session_id,
        "thread_id": agent.thread_id,
        "integration": gateway_view(integration),
        "status": _status(integration, credential),
        "credential": (
            None
            if credential is None
            else {
                "user_id": owner,
                "ciphertext": _b64(credential["ciphertext"]),
                "status": credential["status"],
            }
        ),
        "tools": tools,
    }
