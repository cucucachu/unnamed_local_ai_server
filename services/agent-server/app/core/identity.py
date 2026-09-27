"""Who is calling: verifying the platform's identity token (docs/PLATFORM.md §4).

Caddy's `forward_auth` has the platform check the session cookie/bearer and
attaches `X-HomeAI-Identity: <JWT act=user>` to every authenticated request
(after stripping any client-supplied copy). This module never trusts that
header unverified: it checks the EdDSA signature against the platform's
JWKS (`GET /internal/jwks`), plus `iss`, `aud`, `exp`, and `act="user"`.

The JWKS is fetched lazily on first use and cached; a token naming an
unknown `kid` triggers a refetch, rate-limited so forged `kid`s can't turn
into a request flood against the platform. A fetch failure is reported as
`KeysUnavailable` (-> 503), not as a bad token, so a platform outage doesn't
sign every client out.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Protocol

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Depends, HTTPException, Request
from jwt.algorithms import OKPAlgorithm

logger = logging.getLogger(__name__)

IDENTITY_HEADER = "X-HomeAI-Identity"
ISSUER = "homeai-platform"
AUDIENCE = "homeai"
ALGORITHM = "EdDSA"
_REQUIRED_CLAIMS = ["iss", "aud", "iat", "exp", "sub", "act"]


@dataclass(frozen=True)
class Identity:
    user_id: str
    session_id: str | None
    role: str | None


class IdentityError(Exception):
    """The request carries no usable identity (missing, forged, expired, wrong act, ...)."""


class KeysUnavailable(Exception):
    """The platform's JWKS couldn't be fetched, so no token can be checked right now."""


class IdentityVerifier(Protocol):
    async def verify(self, token: str | None) -> Identity: ...


JwksFetcher = Callable[[], Awaitable[dict[str, Any]]]


def http_jwks_fetcher(url: str, timeout_s: float = 3.0) -> JwksFetcher:
    async def fetch() -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()

    return fetch


def _parse_jwks(jwks: dict[str, Any]) -> dict[str, Ed25519PublicKey]:
    keys: dict[str, Ed25519PublicKey] = {}
    for jwk in jwks.get("keys", []):
        if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or not jwk.get("kid"):
            continue
        key = OKPAlgorithm.from_jwk(json.dumps(jwk))
        if isinstance(key, Ed25519PublicKey):
            keys[jwk["kid"]] = key
    return keys


class JwksIdentityVerifier:
    def __init__(
        self,
        fetch_jwks: JwksFetcher,
        *,
        refetch_interval_s: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch_jwks = fetch_jwks
        self._refetch_interval_s = refetch_interval_s
        self._clock = clock
        self._keys: dict[str, Ed25519PublicKey] = {}
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    async def _key_for(self, kid: str) -> Ed25519PublicKey | None:
        key = self._keys.get(kid)
        if key is not None:
            return key
        async with self._lock:
            key = self._keys.get(kid)
            if key is not None:
                return key
            now = self._clock()
            if self._fetched_at is not None and now - self._fetched_at < self._refetch_interval_s:
                if not self._keys:
                    raise KeysUnavailable("jwks fetch failed recently")
                return None
            self._fetched_at = now
            try:
                self._keys = _parse_jwks(await self._fetch_jwks())
            except Exception as exc:
                logger.warning("identity: JWKS fetch failed: %s", exc)
                raise KeysUnavailable(str(exc)) from exc
            return self._keys.get(kid)

    async def verify(self, token: str | None) -> Identity:
        if not token:
            raise IdentityError("missing identity token")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise IdentityError(f"malformed token: {exc}") from exc
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise IdentityError("token has no kid")

        key = await self._key_for(kid)
        if key is None:
            raise IdentityError(f"unknown kid: {kid!r}")
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                audience=AUDIENCE,
                issuer=ISSUER,
                options={"require": _REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise IdentityError(str(exc)) from exc

        if claims["act"] != "user":
            raise IdentityError(f"act {claims['act']!r} is not a user")
        try:
            user_id = str(uuid.UUID(str(claims["sub"])))
        except ValueError as exc:
            raise IdentityError("sub is not a user id") from exc
        sid, role = claims.get("sid"), claims.get("role")
        return Identity(
            user_id=user_id,
            session_id=sid if isinstance(sid, str) else None,
            role=role if isinstance(role, str) else None,
        )


async def current_user(request: Request) -> Identity:
    """FastAPI dependency: the verified caller, else `401` (or `503` without keys)."""
    verifier: IdentityVerifier = request.app.state.identity_verifier
    try:
        return await verifier.verify(request.headers.get(IDENTITY_HEADER))
    except KeysUnavailable as exc:
        raise HTTPException(status_code=503, detail="identity keys unavailable") from exc
    except IdentityError as exc:
        raise HTTPException(status_code=401, detail="unauthenticated") from exc


CurrentUser = Annotated[Identity, Depends(current_user)]
