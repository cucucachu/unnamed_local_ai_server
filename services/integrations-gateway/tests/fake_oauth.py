"""A fake OAuth provider in front of an MCP server (docs/INTEGRATIONS.md §5.1, §13).

One ASGI app answering for several hosts, like the two providers M18-07 was
designed against:

- `robinhood`: issuer with a path (`https://mcp.example/mcp`), dynamic
  registration, tokens on another host (`tokens.example`), `iss` with the
  code, no revocation, rotating refresh tokens.
- `ha`: issuer at the root, client ID URLs (the return address on the client
  ID's scheme and host), everything on the MCP host, a revocation endpoint.

`authorize(url)` plays the browser and the user saying yes: it checks what a
real provider checks and returns where the provider sends the browser. In
live mode, `GET /authorize` does the same with a redirect.

Live (`tests.fake_server` with `FAKE_OAUTH=ha`), `mcp` is empty and becomes
the origin the first request names (the container's address), and
`GET /oauth-state` / `POST /expire` let the gate see and force what
happened.

`allowed(host)` stands in for the egress proxy: a host it refuses gets the
proxy's own `403` with `X-Egress-Denied: not_registered`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit


@dataclass
class Code:
    client_id: str
    redirect_uri: str
    challenge: str
    resource: str


@dataclass
class FakeProvider:
    mode: str = "robinhood"
    mcp: str = "https://mcp.example"
    tokens_host: str = "https://tokens.example"
    access_ttl: int = 1800
    allowed: Callable[[str], bool] = lambda host: True
    refuse_refresh: bool = False
    clients: dict[str, list[str]] = field(default_factory=dict)
    codes: dict[str, Code] = field(default_factory=dict)
    access: set[str] = field(default_factory=set)
    refresh: dict[str, str] = field(default_factory=dict)
    revoked: list[str] = field(default_factory=list)
    token_requests: list[dict[str, str]] = field(default_factory=list)
    issued: set[str] = field(default_factory=set)

    @property
    def issuer(self) -> str:
        return f"{self.mcp}/mcp" if self.mode == "robinhood" else self.mcp

    @property
    def token_endpoint(self) -> str:
        if self.mode == "robinhood":
            return f"{self.tokens_host}/oauth2/token/"
        return f"{self.mcp}/auth/token"

    def metadata(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.mcp}/authorize",
            "token_endpoint": self.token_endpoint,
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "response_types_supported": ["code"],
            "token_endpoint_auth_methods_supported": ["none"],
        }
        if self.mode == "robinhood":
            doc["registration_endpoint"] = f"{self.mcp}/oauth/register"
            doc["authorization_response_iss_parameter_supported"] = True
        else:
            doc["revocation_endpoint"] = f"{self.mcp}/auth/revoke"
            doc["client_id_metadata_document_supported"] = True
        return doc

    # --- the browser's part ---------------------------------------------------------------

    def _redirect_ok(self, client_id: str, redirect_uri: str) -> bool:
        if self.mode == "robinhood":
            return redirect_uri in self.clients.get(client_id, [])
        c, r = urlsplit(client_id), urlsplit(redirect_uri)
        return (c.scheme, c.netloc) == (r.scheme, r.netloc)

    def authorize(self, url: str, approve: bool = True) -> str:
        """Where the provider sends the browser after the user answers."""
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        assert url.startswith(f"{self.mcp}/authorize?"), url
        assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
        assert self._redirect_ok(q["client_id"], q["redirect_uri"]), "redirect not allowed"
        back: dict[str, str] = {"state": q["state"]}
        if approve:
            code = secrets.token_urlsafe(16)
            self.codes[code] = Code(
                q["client_id"], q["redirect_uri"], q["code_challenge"], q["resource"]
            )
            back["code"] = code
        else:
            back["error"] = "access_denied"
        if self.mode == "robinhood":
            back["iss"] = self.issuer
        return f"{q['redirect_uri']}?{urlencode(back)}"

    # --- tokens ---------------------------------------------------------------------------

    def _issue(self, client_id: str) -> dict[str, Any]:
        access = "at-" + secrets.token_urlsafe(16)
        refresh = "rt-" + secrets.token_urlsafe(16)
        self.access.add(access)
        self.refresh[refresh] = client_id
        self.issued |= {access, refresh}
        return {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": self.access_ttl,
            "refresh_token": refresh,
        }

    def token(self, form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        self.token_requests.append(form)
        if form.get("grant_type") == "authorization_code":
            code = self.codes.pop(form.get("code", ""), None)
            if code is None or code.client_id != form.get("client_id"):
                return 400, {"error": "invalid_grant"}
            if code.redirect_uri != form.get("redirect_uri"):
                return 400, {"error": "invalid_grant"}
            verifier = form.get("code_verifier", "")
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            if digest.rstrip(b"=").decode() != code.challenge:
                return 400, {"error": "invalid_grant", "error_description": "PKCE"}
            return 200, self._issue(code.client_id)
        if form.get("grant_type") == "refresh_token":
            client = self.refresh.get(form.get("refresh_token", ""))
            if self.refuse_refresh or client is None or client != form.get("client_id"):
                return 400, {"error": "invalid_grant"}
            issued = self._issue(client)
            if self.mode == "robinhood":
                del self.refresh[form["refresh_token"]]
            else:
                del self.refresh[issued["refresh_token"]]
                issued.pop("refresh_token")
            return 200, issued
        return 400, {"error": "unsupported_grant_type"}

    def expire_access(self) -> None:
        self.access.clear()

    def state(self) -> dict[str, Any]:
        """For the live gate: what was asked for, and every token issued (to look for leaks)."""
        grants = [r.get("grant_type") for r in self.token_requests]
        return {
            "codes": grants.count("authorization_code"),
            "refreshes": grants.count("refresh_token"),
            "revoked": len(self.revoked),
            "issued": sorted(self.issued),
        }

    # --- HTTP -----------------------------------------------------------------------------

    async def __call__(self, scope, receive, send) -> None:
        assert scope["type"] == "http"
        headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
        host = headers.get("host", "")
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        method, path = scope["method"], scope["path"]
        if not self.allowed(host):
            await _send(send, 403, {"error": "denied"}, {"x-egress-denied": "not_registered"})
            return
        if not self.mcp:
            self.mcp = f"http://{host}"
        origin = f"{urlsplit(self.mcp).scheme}://{host}"
        if origin == self.mcp:
            await self._mcp_host(send, method, path, headers, body, scope)
        elif origin == self.tokens_host and path == "/oauth2/token/" and method == "POST":
            status, doc = self.token(_form(body))
            await _send(send, status, doc)
        else:
            await _send(send, 404, {"error": "not_found"})

    async def _mcp_host(self, send, method, path, headers, body, scope) -> None:
        if method == "POST" and path == "/mcp":
            bearer = headers.get("authorization", "").removeprefix("Bearer ")
            if bearer in self.access:
                await _send(send, 200, {"jsonrpc": "2.0", "id": 1, "result": {}})
                return
            path_part = "/mcp" if self.mode == "robinhood" else ""
            challenge = (
                'Bearer error="invalid_token", resource_metadata='
                f'"{self.mcp}/.well-known/oauth-protected-resource{path_part}"'
            )
            await _send(send, 401, {"error": "unauthorized"}, {"www-authenticate": challenge})
            return
        if method == "GET" and path in (
            "/.well-known/oauth-protected-resource/mcp",
            "/.well-known/oauth-protected-resource",
        ):
            resource = f"{self.mcp}/mcp" if self.mode == "robinhood" else self.mcp
            await _send(
                send,
                200,
                {
                    "resource": resource,
                    "authorization_servers": [self.issuer],
                    "scopes_supported": ["internal"] if self.mode == "robinhood" else [],
                },
            )
            return
        well_known = (
            "/.well-known/oauth-authorization-server/mcp"
            if self.mode == "robinhood"
            else "/.well-known/oauth-authorization-server"
        )
        if method == "GET" and path == well_known:
            await _send(send, 200, self.metadata())
            return
        if method == "POST" and path == "/oauth/register" and self.mode == "robinhood":
            doc = json.loads(body or b"{}")
            client_id = "client-" + secrets.token_urlsafe(8)
            self.clients[client_id] = list(doc.get("redirect_uris") or [])
            await _send(
                send,
                201,
                {
                    "client_id": client_id,
                    "redirect_uris": self.clients[client_id],
                    "token_endpoint_auth_method": "none",
                },
            )
            return
        if method == "POST" and path == "/auth/token" and self.mode == "ha":
            status, doc = self.token(_form(body))
            await _send(send, status, doc)
            return
        if method == "POST" and path == "/auth/revoke" and self.mode == "ha":
            token = _form(body).get("token", "")
            self.revoked.append(token)
            self.refresh.pop(token, None)
            await _send(send, 200, {})
            return
        if method == "GET" and path == "/oauth-state":
            await _send(send, 200, self.state())
            return
        if method == "POST" and path == "/expire":
            self.expire_access()
            await _send(send, 200, {})
            return
        if method == "GET" and path == "/authorize":
            query = scope.get("query_string", b"").decode()
            location = self.authorize(f"{self.mcp}/authorize?{query}")
            await _send(send, 302, {}, {"location": location})
            return
        await _send(send, 404, {"error": "not_found"})


def _form(body: bytes) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(body.decode()).items()}


async def _send(send, status: int, body: object, extra: dict[str, str] | None = None) -> None:
    raw = json.dumps(body).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
    headers += [(k.encode(), v.encode()) for k, v in (extra or {}).items()]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": raw})
