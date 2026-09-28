"""Verifying the delegation every session call carries (docs/PLATFORM.md §4, §6).

agent-server sends the run's delegation (`act=agent`, `thr=<thread_id>`) as
`Authorization: Bearer`. It's checked here against the platform's JWKS -
EdDSA signature, `iss`, `aud`, `exp`, `act` - before anything else, and its
`thr` must name the session being touched, so one thread's delegation can't
reach another thread's container. Whether the session behind it is still
active is the platform's call, made when the grants are fetched.

The JWKS is fetched on first use and cached; an unknown `kid` refetches, at
most once per `refetch_interval_s`. A fetch failure is `KeysUnavailable`
(-> 503), not a bad token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jwt.algorithms import OKPAlgorithm

logger = logging.getLogger(__name__)

ISSUER = "homeai-platform"
AUDIENCE = "homeai"
ALGORITHM = "EdDSA"
_REQUIRED_CLAIMS = ["iss", "aud", "iat", "exp", "sub", "act", "thr"]


@dataclass(frozen=True)
class Delegation:
    token: str
    user_id: str
    thread_id: str

    def __repr__(self) -> str:
        return f"Delegation(user_id={self.user_id!r}, thread_id={self.thread_id!r})"


class DelegationError(Exception):
    """Missing, forged, expired, or not a delegation."""


class KeysUnavailable(Exception):
    """The platform's JWKS couldn't be fetched, so no token can be checked right now."""


class DelegationVerifier(Protocol):
    async def verify(self, token: str | None) -> Delegation: ...


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


class JwksDelegationVerifier:
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
                logger.warning("delegation: JWKS fetch failed: %s", exc)
                raise KeysUnavailable(str(exc)) from exc
            return self._keys.get(kid)

    async def verify(self, token: str | None) -> Delegation:
        if not token:
            raise DelegationError("missing delegation")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise DelegationError(f"malformed token: {exc}") from exc
        kid = header.get("kid")
        if not isinstance(kid, str):
            raise DelegationError("token has no kid")

        key = await self._key_for(kid)
        if key is None:
            raise DelegationError(f"unknown kid: {kid!r}")
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
            raise DelegationError(str(exc)) from exc

        if claims["act"] != "agent":
            raise DelegationError(f"act {claims['act']!r} is not a delegation")
        thread_id = claims["thr"]
        if not isinstance(thread_id, str) or not thread_id:
            raise DelegationError("thr is not a thread id")
        try:
            user_id = str(uuid.UUID(str(claims["sub"])))
        except ValueError as exc:
            raise DelegationError("sub is not a user id") from exc
        return Delegation(token=token, user_id=user_id, thread_id=thread_id)
