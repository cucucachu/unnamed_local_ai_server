"""Identity test doubles (M10-04).

- `FixedIdentityVerifier`: every request is `TEST_USER`, header or not — for
  the many tests that are about something other than authentication.
- `AutoCreateThreadStore`: any thread id `TEST_USER` asks for already exists
  and is theirs, standing in for the pre-M10-04 "WS connect creates the
  thread" behavior the WS suite's plain string ids (`plain-thread`, ...)
  were written against.
- `LocalKeyPair`: an Ed25519 key pair playing the platform, so tests run the
  real `JwksIdentityVerifier` with no network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from jwt.algorithms import OKPAlgorithm

from app.core.identity import AUDIENCE, ISSUER, Identity, JwksIdentityVerifier
from app.db.threads import InMemoryThreadStore, ThreadRecord

TEST_USER_ID = "00000000-0000-4000-8000-000000000001"
TEST_USER = Identity(user_id=TEST_USER_ID, session_id=None, role="member")


class FixedIdentityVerifier:
    def __init__(self, identity: Identity = TEST_USER) -> None:
        self.identity = identity

    async def verify(self, token: str | None) -> Identity:
        return self.identity


class AutoCreateThreadStore(InMemoryThreadStore):
    async def get(self, thread_id: str, owner_user_id: str) -> ThreadRecord | None:
        if thread_id not in self._rows:
            self.insert(thread_id, owner_user_id)
        return await super().get(thread_id, owner_user_id)


class LocalKeyPair:
    def __init__(self) -> None:
        self.private_key = Ed25519PrivateKey.generate()
        jwk = json.loads(OKPAlgorithm.to_jwk(self.private_key.public_key()))
        self.kid = f"test-{jwk['x'][:8]}"
        self.jwk = {**jwk, "kid": self.kid, "use": "sig", "alg": "EdDSA"}
        self.fetches = 0
        self.fail_fetch = False

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.jwk]}

    async def fetch(self) -> dict[str, Any]:
        self.fetches += 1
        if self.fail_fetch:
            raise ConnectionError("platform unreachable")
        return self.jwks()

    def verifier(self, **kwargs: Any) -> JwksIdentityVerifier:
        return JwksIdentityVerifier(self.fetch, **kwargs)

    def token(
        self,
        sub: str,
        *,
        act: str = "user",
        aud: str = AUDIENCE,
        iss: str = ISSUER,
        ttl: timedelta = timedelta(minutes=5),
        kid: str | None = None,
        signing_key: Ed25519PrivateKey | None = None,
        drop: tuple[str, ...] = (),
        **extra: Any,
    ) -> str:
        now = datetime.now(UTC)
        claims = {
            "iss": iss,
            "aud": aud,
            "sub": sub,
            "sid": "11111111-1111-4111-8111-111111111111",
            "role": "member",
            "act": act,
            "iat": int(now.timestamp()),
            "exp": int((now + ttl).timestamp()),
            **extra,
        }
        for name in drop:
            claims.pop(name, None)
        return jwt.encode(
            claims,
            signing_key or self.private_key,
            algorithm="EdDSA",
            headers={"kid": kid or self.kid},
        )

    def headers(self, sub: str, **kwargs: Any) -> dict[str, str]:
        return {"X-HomeAI-Identity": self.token(sub, **kwargs)}
