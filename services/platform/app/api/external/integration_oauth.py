"""OAuth sign-in for integrations (docs/INTEGRATIONS.md §5.1, `app.core.integration_oauth`).

`POST /integrations/{id}/oauth/start` is the user's Connect (the admin's,
for a shared integration, is under `/admin/integrations`). The two `GET`
pages are for the provider's sign-in and need no session (Caddy lets
exactly these two paths through): the browser that comes back may be an
Android browser with no HomeAI session, and the gateway's single-use
`state` is the binding.
"""

from __future__ import annotations

import html
import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from app.api.schemas import DiscoveryIn, OAuthStartOut
from app.api.session_http import is_https
from app.core import audit, integration_oauth, integrations
from app.core.errors import InvalidInput, NotFound, PlatformError, Unavailable
from app.core.integrations_gateway import decode_ciphertext
from app.core.principal import HumanUser, Principal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations")

PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}

FAILURES = {
    "oauth_state_invalid": "This sign-in link was already used or has expired. Start again.",
    "oauth_denied": "The sign-in was cancelled or refused, so nothing was connected.",
    "oauth_issuer_mismatch": "The sign-in came back from a different server. Nothing was stored.",
    "oauth_exchange_failed": "The server didn't accept the sign-in. Start again.",
    "credential_rejected": "The server refused the new sign-in. Start again.",
}
FAILURE = "Something went wrong finishing the sign-in. Start again from HomeAI."


def _host(request: Request) -> str:
    return request.headers.get("x-forwarded-host") or request.headers.get("host") or ""


def _started_by(principal: Principal, app: bool) -> dict[str, Any]:
    return {
        "user_id": str(principal.user_id),
        "name": principal.display_name or principal.username,
        "session_id": str(principal.session_id),
        "app": app,
    }


async def start(
    request: Request, principal: Principal, integration_id: UUID, owner: UUID | None, app: bool
) -> OAuthStartOut:
    """Ask the gateway for the provider's sign-in URL; keep a new client registration.

    `app`: started from the phone app, so the return page links back into it.
    """
    state = request.app.state
    return_url = integration_oauth.return_url(state.settings, _host(request), is_https(request))
    async with state.db_pool.connection() as conn:
        integration = await integrations.get(conn, integration_id)
        if integration["auth_method"] != "oauth":
            raise InvalidInput("auth_method_mismatch")
        client = await integration_oauth.client_for(conn, integration_id, return_url)
    started = await state.gateway.oauth_start(
        integrations.gateway_view(integration),
        owner,
        _started_by(principal, app),
        return_url,
        dict(client) if client else None,
    )
    url = started.get("authorize_url")
    if not isinstance(url, str) or not url.startswith(("https://", "http://")):
        raise Unavailable("gateway_unavailable")
    if started.get("client"):
        async with state.db_pool.connection() as conn:
            await integration_oauth.save_client(conn, integration_id, started["client"])
    return OAuthStartOut(authorize_url=url)


@router.post("/{integration_id}/oauth/start", response_model=OAuthStartOut)
async def start_mine(
    integration_id: UUID, request: Request, principal: HumanUser, app: bool = False
) -> OAuthStartOut:
    """Connect with sign-in: open `authorize_url` in a browser; the return page finishes it."""
    async with request.app.state.db_pool.connection() as conn:
        integration = await integrations.get(conn, integration_id)
    if not integrations.usable_by(integration, principal.user_id):
        raise NotFound("integration_not_found")
    if integration["credential_model"] != "per_user":
        raise InvalidInput("wrong_credential_model")
    return await start(request, principal, integration_id, principal.user_id, app)


def _page(title: str, message: str, links: list[tuple[str, str]], status: int) -> HTMLResponse:
    items = "".join(
        f'<p><a href="{html.escape(href, quote=True)}">{html.escape(label)}</a></p>'
        for label, href in links
    )
    body = (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>"
        "body{font-family:system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem;"
        "line-height:1.5}a{font-size:1.1rem}</style></head><body>"
        f"<h1>{html.escape(title)}</h1><p>{html.escape(message)}</p>{items}</body></html>"
    )
    return HTMLResponse(body, status_code=status, headers=PAGE_HEADERS)


def _back_links(
    integration_id: UUID | None, manage: bool, app: bool | None
) -> list[tuple[str, str]]:
    """`app` None: not known who started it, so both ways back."""
    if integration_id is None:
        path = "settings/integrations"
    elif manage:
        path = f"settings/integrations/manage/{integration_id}"
    else:
        path = f"settings/integrations/{integration_id}"
    if app is None:
        return [("Back to HomeAI", f"/{path}"), ("Open the HomeAI app", f"homeai://{path}")]
    return [("Back to HomeAI", f"homeai://{path}" if app else f"/{path}")]


def _actor(started_by: Any) -> audit.Actor:
    if not isinstance(started_by, dict):
        raise InvalidInput("invalid_started_by")
    return audit.Actor(
        "user",
        UUID(str(started_by["user_id"])),
        str(started_by.get("name") or "")[:80] or None,
        UUID(str(started_by["session_id"])) if started_by.get("session_id") else None,
    )


@router.get("/oauth/return", response_class=HTMLResponse)
async def finish(
    request: Request,
    state: Annotated[str, Query(max_length=200)] = "",
    code: Annotated[str | None, Query(max_length=4096)] = None,
    iss: Annotated[str | None, Query(max_length=2000)] = None,
    error: Annotated[str | None, Query(max_length=200)] = None,
) -> HTMLResponse:
    """Where the provider sends the browser back. Stores the credential for whoever started it."""
    app_state = request.app.state
    if not state:
        return _page("Sign-in not finished", FAILURES["oauth_state_invalid"], [], 400)
    try:
        done = await app_state.gateway.oauth_finish(state, code, iss, error)
    except InvalidInput as exc:
        message = FAILURES.get(exc.code, FAILURE)
        return _page("Sign-in not finished", message, _back_links(None, False, None), 400)
    except Unavailable:
        return _page("Sign-in not finished", FAILURE, _back_links(None, False, None), 503)
    started_by = done.get("started_by")
    app = bool(started_by.get("app")) if isinstance(started_by, dict) else None
    try:
        integration_id = UUID(str(done["integration_id"]))
        owner = UUID(str(done["owner"])) if done.get("owner") else None
        actor = _actor(started_by)
        ciphertext = decode_ciphertext(done.get("ciphertext"))
        async with app_state.db_pool.connection() as conn:
            integration = await integrations.get(conn, integration_id)
            if integration["auth_method"] != "oauth":
                raise InvalidInput("auth_method_mismatch")
            if owner is not None and owner != actor.user_id:
                raise InvalidInput("owner_mismatch")
            await integrations.store_credential(conn, actor, integration_id, owner, ciphertext)
    except (PlatformError, KeyError, ValueError) as exc:
        logger.warning("oauth sign-in not stored: %s", getattr(exc, "code", type(exc).__name__))
        return _page("Sign-in not finished", FAILURE, _back_links(None, False, app), 400)
    found = integration["discovered_at"] is None and await _first_discovery(
        request, actor, integration, owner, done["ciphertext"]
    )
    if found:
        message = (
            f"Connected to {integration['name']} and found {found['tools']} tools. "
            "They start off: choose which to turn on in HomeAI."
        )
        return _page("Connected", message, _back_links(integration_id, found["admin"], app), 200)
    if done.get("checked") is False:
        message = (
            f"Connected to {integration['name']}, but it couldn't be tried just now. "
            "The first use will tell."
        )
    else:
        message = f"Connected to {integration['name']}. You can close this page."
    return _page("Connected", message, _back_links(integration_id, owner is None, app), 200)


async def _first_discovery(
    request: Request,
    actor: audit.Actor,
    integration: dict[str, Any],
    owner: UUID | None,
    ciphertext: str,
) -> dict[str, Any] | None:
    """An integration's first sign-in (the admin adding it): find its tools now. Best effort."""
    state = request.app.state
    try:
        found = await state.gateway.discover(
            integrations.gateway_view(integration), {"user_id": owner, "ciphertext": ciphertext}
        )
        discovery = DiscoveryIn.model_validate(found).model_dump()
        async with state.db_pool.connection() as conn:
            await integrations.store_discovery(conn, actor, integration["id"], discovery)
            cur = await conn.execute("SELECT role FROM users WHERE id = %s", (actor.user_id,))
            row = await cur.fetchone()
    except (PlatformError, ValidationError) as exc:
        logger.warning("first discovery after sign-in failed: %s", getattr(exc, "code", "invalid"))
        return None
    return {"tools": len(discovery["tools"]), "admin": bool(row and row["role"] == "admin")}


@router.get("/oauth/client")
async def client(request: Request):
    """HomeAI's client ID page, for servers that take a URL as the client ID (Home Assistant).

    HTML with a `redirect_uri` link, or the JSON client metadata document.
    """
    try:
        return_url = integration_oauth.return_url(
            request.app.state.settings, _host(request), is_https(request)
        )
    except InvalidInput:
        return _page("HomeAI", "This address isn't one of this HomeAI's own.", [], 404)
    client_id = integration_oauth.client_page_url(return_url)
    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(
            {
                "client_id": client_id,
                "client_name": "HomeAI",
                "redirect_uris": [return_url],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            headers={"Cache-Control": "no-store"},
        )
    escaped = html.escape(return_url, quote=True)
    body = (
        "<!doctype html><html lang=en><head><meta charset=utf-8><title>HomeAI</title>"
        f'<link rel="redirect_uri" href="{escaped}"></head>'
        '<body><div class="h-app"><span class="p-name">HomeAI</span></div></body></html>'
    )
    return HTMLResponse(body, headers=PAGE_HEADERS)
