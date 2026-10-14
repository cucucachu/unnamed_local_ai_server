"""OAuth 2.1 sign-in for integrations (docs/INTEGRATIONS.md §5.1).

The gateway does all of it: discovery from the MCP server's own metadata,
client registration, the sign-in state and PKCE verifier (kept here, in
memory), the code exchange, refresh and revocation. Every request goes
through the egress proxy like MCP traffic; before the gateway asks a host
the server's metadata named, it tells the platform, whose egress rules then
include it.

A stored OAuth credential (sealed like a token) is
`{"kind": "oauth", "access_token", "refresh_token", "expires_at", "client_id",
"issuer", "token_endpoint", "revocation_endpoint", "resource", "issued_at",
"generation"}`; `generation` counts refreshes, so the newest copy is known.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import ssl
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

import httpx2

logger = logging.getLogger(__name__)

MAX_BODY = 64 * 1024
CLIENT_NAME = "HomeAI"
# The page HomeAI serves as its client ID, for servers that take client ID URLs.
CLIENT_ID_PATH = "/api/platform/integrations/oauth/client"
# Refresh this long before the access token runs out.
REFRESH_EARLY_S = 60.0
# The proxy refetches its rules at most this often for an unknown destination.
RULES_MISS_WAIT_S = 1.1
_PARAM = re.compile(r'([A-Za-z_]+)\s*=\s*"([^"]*)"')
_REFUSED = {"invalid_grant", "invalid_client", "unauthorized_client", "invalid_token"}
_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "homeai-integrations-gateway", "version": "1"},
    },
}


class OAuthError(Exception):
    """Sign-in can't go on; `code` is for the platform (a 422 with it as the detail)."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class RefreshRefused(Exception):
    """The provider refused the refresh token: the user has to sign in again."""


@dataclass
class Metadata:
    resource: str
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    revocation_endpoint: str | None
    scope: str | None
    sends_iss: bool
    client_id_urls: bool
    # Every URL the gateway itself fetched or will post to, for the egress rules.
    endpoints: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """What the admin sees: which hosts sign-in and tokens use, how HomeAI registers."""
        if self.registration_endpoint:
            registration = "dynamic"
        elif self.client_id_urls:
            registration = "client_id_url"
        else:
            registration = "unsupported"
        return {
            "issuer": self.issuer,
            "sign_in_host": urlsplit(self.authorization_endpoint).hostname,
            "token_host": urlsplit(self.token_endpoint).hostname,
            "registration": registration,
            "revocable": self.revocation_endpoint is not None,
        }


@dataclass
class Pending:
    integration: dict[str, Any]
    owner: UUID | None
    started_by: dict[str, Any]
    client: dict[str, Any]
    metadata: Metadata
    verifier: str
    expires: float


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, (parts.hostname or "").lower(), port


def _clean_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2000:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.fragment:
        return None
    if parts.username or parts.password:
        return None
    return value


def endpoint_allowed(url: str, integration: dict[str, Any]) -> bool:
    """https, or the integration's own scheme, host and port (its private exception goes too)."""
    if _origin(url) == _origin(integration["url"]):
        return True
    return urlsplit(url).scheme == "https"


def _challenge_params(header: str) -> dict[str, str]:
    scheme, _, rest = header.partition(" ")
    if scheme.lower() != "bearer":
        return {}
    return {k.lower(): v for k, v in _PARAM.findall(rest)}


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


class OAuthHttp:
    """Plain HTTP to OAuth endpoints, through the egress proxy."""

    def __init__(
        self,
        proxy: str | None,
        ca_bundle: str,
        timeout_s: float,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._proxy = proxy or None
        self._verify: ssl.SSLContext | bool = (
            ssl.create_default_context(cafile=ca_bundle) if ca_bundle else True
        )
        self._timeout = timeout_s
        self._transport = transport

    async def request(
        self,
        method: str,
        url: str,
        *,
        form: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any, httpx2.Headers]:
        """(status, parsed JSON or None, headers). Retries once if the proxy hasn't got the rule yet."""
        for attempt in (1, 2):
            status, payload, got = await self._once(method, url, form, body, headers or {})
            if got.get("x-egress-denied") != "not_registered" or attempt == 2:
                break
            await asyncio.sleep(RULES_MISS_WAIT_S)
        denied = got.get("x-egress-denied")
        if denied:
            raise OAuthError(
                "address_refused" if denied in ("destination", "not_registered") else denied
            )
        return status, payload, got

    async def _once(
        self,
        method: str,
        url: str,
        form: dict[str, str] | None,
        body: dict[str, Any] | None,
        headers: dict[str, str],
    ) -> tuple[int, Any, httpx2.Headers]:
        try:
            async with (
                httpx2.AsyncClient(
                    proxy=self._proxy,
                    verify=self._verify,
                    timeout=self._timeout,
                    transport=self._transport,
                    trust_env=False,
                    follow_redirects=False,
                ) as client,
                client.stream(
                    method,
                    url,
                    data=form,
                    json=body,
                    headers={"Accept": "application/json", **headers},
                ) as response,
            ):
                raw = b""
                async for chunk in response.aiter_bytes():
                    raw += chunk
                    if len(raw) > MAX_BODY:
                        raise OAuthError("oauth_discovery_failed", "A response was too large.")
                status, got = response.status_code, response.headers
        except httpx2.TimeoutException as exc:
            raise OAuthError("timeout") from exc
        except httpx2.HTTPError as exc:
            raise OAuthError("unreachable") from exc
        try:
            payload = json.loads(raw) if raw else None
        except ValueError:
            payload = None
        return status, payload, got


class OAuth:
    def __init__(self, http: OAuthHttp, platform: Any, flow_ttl_s: float = 600.0) -> None:
        self.http = http
        self.platform = platform
        self.flow_ttl_s = flow_ttl_s
        self._flows: dict[str, Pending] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # The newest credential refreshed here, per (integration, owner): a call holding
        # an older copy (before the platform has it) uses this one instead.
        self._fresh: dict[tuple[str, str], dict[str, Any]] = {}

    # --- discovery ----------------------------------------------------------------------

    async def _report(self, integration: dict[str, Any], urls: list[str], summary=None) -> None:
        await self.platform.store_oauth(UUID(str(integration["id"])), summary, urls)

    async def discover(self, integration: dict[str, Any]) -> Metadata:
        """The server's protected-resource and authorization-server metadata, checked."""
        url = integration["url"]
        status, _, headers = await self.http.request(
            "POST",
            url,
            body=_INITIALIZE,
            headers={"Accept": "application/json, text/event-stream"},
        )
        if status != 401:
            raise OAuthError("oauth_unsupported", "The server didn't ask for sign-in.")
        params = _challenge_params(headers.get("www-authenticate", ""))
        scheme, host, port = _origin(url)
        base = urlunsplit((scheme, urlsplit(url).netloc, "", "", ""))
        path = urlsplit(url).path.rstrip("/")
        candidates = [
            params.get("resource_metadata"),
            f"{base}/.well-known/oauth-protected-resource{path}",
            f"{base}/.well-known/oauth-protected-resource",
        ]
        resource_doc, resource_url = None, None
        for candidate in candidates:
            candidate = _clean_url(candidate)
            if not candidate or _origin(candidate) != (scheme, host, port):
                continue
            got, doc, _ = await self.http.request("GET", candidate)
            if got == 200 and isinstance(doc, dict):
                resource_doc, resource_url = doc, candidate
                break
        if resource_doc is None:
            raise OAuthError("oauth_discovery_failed", "No protected-resource metadata.")
        resource = _clean_url(resource_doc.get("resource"))
        if not resource or _origin(resource) != (scheme, host, port):
            raise OAuthError("oauth_discovery_failed", "The metadata names another resource.")
        if not (url.rstrip("/") + "/").startswith(resource.rstrip("/") + "/"):
            raise OAuthError("oauth_discovery_failed", "The metadata names another resource.")
        servers = resource_doc.get("authorization_servers")
        issuer = _clean_url(servers[0]) if isinstance(servers, list) and servers else None
        if not issuer or not endpoint_allowed(issuer, integration):
            raise OAuthError("oauth_discovery_failed", "No usable authorization server.")

        issuer_parts = urlsplit(issuer)
        issuer_base = urlunsplit((issuer_parts.scheme, issuer_parts.netloc, "", "", ""))
        issuer_path = issuer_parts.path.rstrip("/")
        as_candidates = (
            [
                f"{issuer_base}/.well-known/oauth-authorization-server{issuer_path}",
                f"{issuer_base}/.well-known/openid-configuration{issuer_path}",
                f"{issuer.rstrip('/')}/.well-known/openid-configuration",
            ]
            if issuer_path
            else [
                f"{issuer_base}/.well-known/oauth-authorization-server",
                f"{issuer_base}/.well-known/openid-configuration",
            ]
        )
        known = [resource_url, *as_candidates]
        await self._report(integration, known)
        as_doc, as_url = None, None
        for candidate in as_candidates:
            got, doc, _ = await self.http.request("GET", candidate)
            if got == 200 and isinstance(doc, dict):
                as_doc, as_url = doc, candidate
                break
        if as_doc is None:
            raise OAuthError("oauth_discovery_failed", "No authorization-server metadata.")
        if str(as_doc.get("issuer", "")).rstrip("/") != issuer.rstrip("/"):
            raise OAuthError("oauth_discovery_failed", "The issuer doesn't match.")
        if "S256" not in (as_doc.get("code_challenge_methods_supported") or []):
            raise OAuthError("oauth_unsupported", "The server doesn't offer PKCE (S256).")
        if "code" not in (as_doc.get("response_types_supported") or ["code"]):
            raise OAuthError("oauth_unsupported", "The server doesn't offer the code flow.")

        def endpoint(name: str, required: bool) -> str | None:
            value = _clean_url(as_doc.get(name))
            if value and endpoint_allowed(value, integration):
                return value
            if required:
                raise OAuthError("oauth_discovery_failed", f"No usable {name}.")
            return None

        authorization = endpoint("authorization_endpoint", True)
        token = endpoint("token_endpoint", True)
        registration = endpoint("registration_endpoint", False)
        revocation = endpoint("revocation_endpoint", False)
        scopes = resource_doc.get("scopes_supported")
        scope = params.get("scope") or (
            " ".join(s for s in scopes if isinstance(s, str)) if isinstance(scopes, list) else None
        )
        metadata = Metadata(
            resource=resource,
            issuer=issuer,
            authorization_endpoint=authorization,
            token_endpoint=token,
            registration_endpoint=registration,
            revocation_endpoint=revocation,
            scope=scope or None,
            sends_iss=as_doc.get("authorization_response_iss_parameter_supported") is True,
            client_id_urls=as_doc.get("client_id_metadata_document_supported") is True,
            endpoints=[u for u in (resource_url, as_url, token, registration, revocation) if u],
        )
        await self._report(integration, metadata.endpoints, metadata.summary())
        return metadata

    # --- signing in ---------------------------------------------------------------------

    async def _register(self, metadata: Metadata, return_url: str) -> dict[str, Any]:
        if metadata.registration_endpoint:
            status, doc, _ = await self.http.request(
                "POST",
                metadata.registration_endpoint,
                body={
                    "client_name": CLIENT_NAME,
                    "redirect_uris": [return_url],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
            )
            client_id = doc.get("client_id") if isinstance(doc, dict) else None
            if status not in (200, 201) or not isinstance(client_id, str) or not client_id:
                raise OAuthError("oauth_registration_failed", f"HTTP {status}.")
            if doc.get("token_endpoint_auth_method", "none") != "none":
                raise OAuthError(
                    "oauth_unsupported", "The server registered a confidential client (#372)."
                )
            return {
                "client_id": client_id[:500],
                "redirect_uri": return_url,
                "issuer": metadata.issuer,
                "kind": "dynamic",
            }
        if metadata.client_id_urls:
            parts = urlsplit(return_url)
            return {
                "client_id": urlunsplit((parts.scheme, parts.netloc, CLIENT_ID_PATH, "", "")),
                "redirect_uri": return_url,
                "issuer": metadata.issuer,
                "kind": "client_id_url",
            }
        raise OAuthError(
            "oauth_unsupported",
            "The server offers neither dynamic registration nor client ID URLs (#372).",
        )

    def _sweep(self) -> None:
        now = time.monotonic()
        for state in [s for s, p in self._flows.items() if p.expires <= now]:
            del self._flows[state]

    async def start(
        self,
        integration: dict[str, Any],
        owner: UUID | None,
        started_by: dict[str, Any],
        return_url: str,
        client: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """The provider's sign-in URL, and a new client registration for the platform to keep."""
        metadata = await self.discover(integration)
        new_client = None
        if not (
            client
            and client.get("issuer") == metadata.issuer
            and client.get("redirect_uri") == return_url
        ):
            client = new_client = await self._register(metadata, return_url)
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(32)
        self._sweep()
        self._flows[state] = Pending(
            integration=integration,
            owner=owner,
            started_by=started_by,
            client=client,
            metadata=metadata,
            verifier=verifier,
            expires=time.monotonic() + self.flow_ttl_s,
        )
        query = {
            "response_type": "code",
            "client_id": client["client_id"],
            "redirect_uri": return_url,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "resource": metadata.resource,
        }
        if metadata.scope:
            query["scope"] = metadata.scope
        parts = urlsplit(metadata.authorization_endpoint)
        existing = parse_qsl(parts.query, keep_blank_values=True)
        url = urlunsplit(parts._replace(query=urlencode([*existing, *query.items()])))
        return {"authorize_url": url, "client": new_client, "oauth": metadata.summary()}

    async def finish(
        self,
        state: str,
        code: str | None,
        iss: str | None,
        error: str | None,
    ) -> tuple[Pending, dict[str, Any]]:
        """The sign-in's pending flow and the new credential (unsealed) for its owner."""
        self._sweep()
        pending = self._flows.pop(state, None)
        if pending is None:
            raise OAuthError("oauth_state_invalid")
        metadata = pending.metadata
        if error:
            raise OAuthError("oauth_denied", error[:100])
        if (iss or metadata.sends_iss) and (iss or "").rstrip("/") != metadata.issuer.rstrip("/"):
            raise OAuthError("oauth_issuer_mismatch")
        if not code:
            raise OAuthError("oauth_denied", "no code")
        status, doc, _ = await self.http.request(
            "POST",
            metadata.token_endpoint,
            form={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": pending.client["redirect_uri"],
                "client_id": pending.client["client_id"],
                "code_verifier": pending.verifier,
                "resource": metadata.resource,
            },
        )
        tokens = _tokens(status, doc)
        if tokens is None:
            raise OAuthError("oauth_exchange_failed", f"HTTP {status}.")
        return pending, {
            "kind": "oauth",
            **tokens,
            "client_id": pending.client["client_id"],
            "issuer": metadata.issuer,
            "token_endpoint": metadata.token_endpoint,
            "revocation_endpoint": metadata.revocation_endpoint,
            "resource": metadata.resource,
        }

    # --- using ----------------------------------------------------------------------------

    def _key(self, integration_id: UUID, owner: UUID | None) -> tuple[str, str]:
        return str(integration_id), str(owner) if owner else "shared"

    def freshest(
        self, integration_id: UUID, owner: UUID | None, secret: dict[str, Any]
    ) -> dict[str, Any]:
        fresh = self._fresh.get(self._key(integration_id, owner))
        if fresh and fresh.get("generation", 0) > secret.get("generation", 0):
            return fresh
        return secret

    @staticmethod
    def expiring(secret: dict[str, Any]) -> bool:
        expires = secret.get("expires_at")
        return isinstance(expires, (int, float)) and expires - time.time() < REFRESH_EARLY_S

    async def refreshed(
        self,
        integration_id: UUID,
        owner: UUID | None,
        secret: dict[str, Any],
        stale_access_token: str | None = None,
    ) -> dict[str, Any]:
        """A credential to use now: refreshed, unless another call already did.

        `stale_access_token` is one the server just refused (refresh unless
        there's already a different one); without it, refresh only if expiring.
        Raises `RefreshRefused` if the provider refused, `OAuthError` if it
        couldn't be asked.
        """
        key = self._key(integration_id, owner)
        async with self._locks.setdefault(key, asyncio.Lock()):
            current = self.freshest(integration_id, owner, secret)
            if stale_access_token is not None:
                if current.get("access_token") != stale_access_token:
                    return current
            elif not self.expiring(current):
                return current
            if not current.get("refresh_token"):
                raise RefreshRefused
            status, doc, _ = await self.http.request(
                "POST",
                current["token_endpoint"],
                form={
                    "grant_type": "refresh_token",
                    "refresh_token": current["refresh_token"],
                    "client_id": current["client_id"],
                    "resource": current["resource"],
                },
            )
            tokens = _tokens(status, doc)
            if tokens is None:
                code = doc.get("error") if isinstance(doc, dict) else None
                if status in (400, 401) and (code in _REFUSED or code is None):
                    raise RefreshRefused
                raise OAuthError("server_error", f"HTTP {status}.")
            if not tokens.get("refresh_token"):
                tokens["refresh_token"] = current["refresh_token"]
            fresh = {**current, **tokens, "generation": current.get("generation", 0) + 1}
            self._fresh[key] = fresh
            return fresh

    async def revoke(self, secret: dict[str, Any]) -> bool:
        """Best effort: whether the provider took the revocation."""
        endpoint = secret.get("revocation_endpoint")
        token = secret.get("refresh_token") or secret.get("access_token")
        if not endpoint or not token:
            return False
        hint = "refresh_token" if secret.get("refresh_token") else "access_token"
        try:
            status, _, _ = await self.http.request(
                "POST",
                endpoint,
                form={"token": token, "token_type_hint": hint, "client_id": secret["client_id"]},
            )
        except OAuthError as exc:
            logger.warning("revocation failed: %s", exc.code)
            return False
        return status == 200

    def forget(self, integration_id: UUID, owner: UUID | None) -> None:
        self._fresh.pop(self._key(integration_id, owner), None)


def _tokens(status: int, doc: Any) -> dict[str, Any] | None:
    if status != 200 or not isinstance(doc, dict):
        return None
    access = doc.get("access_token")
    if not isinstance(access, str) or not access or len(access) > 8192:
        return None
    if str(doc.get("token_type", "bearer")).lower() != "bearer":
        return None
    refresh = doc.get("refresh_token")
    expires_in = doc.get("expires_in")
    now = time.time()
    return {
        "access_token": access,
        "refresh_token": refresh if isinstance(refresh, str) and len(refresh) <= 8192 else None,
        "expires_at": now + expires_in if isinstance(expires_in, (int, float)) else None,
        "issued_at": now,
    }
