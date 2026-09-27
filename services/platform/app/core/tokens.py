"""Ed25519 signing keys, JWKS, and platform JWT issue/verify (docs/PLATFORM.md §4).

Every token the platform mints (identity, delegation) is an EdDSA JWT with
`iss="homeai-platform"`, `aud="homeai"`, and a `kid` header naming the key
that signed it. Downstream services verify against `GET /internal/jwks`.

The private key lives at `<keys_dir>/signing-key.pem` (PKCS#8 PEM, mode
0600, dir 0700), generated on first start and reused on every later start so
`kid` - and therefore every outstanding token - survives restarts.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from jwt.algorithms import OKPAlgorithm

ISSUER = "homeai-platform"
AUDIENCE = "homeai"
ALGORITHM = "EdDSA"
KEY_FILENAME = "signing-key.pem"

# Claims `issue_token` sets itself; callers passing any of these is a bug.
_RESERVED_CLAIMS = frozenset({"iss", "aud", "iat", "exp", "nbf"})
_REQUIRED_CLAIMS = ["iss", "aud", "iat", "exp", "sub", "act"]


class TokenError(Exception):
    """Raised for any token that must not be trusted (bad signature, expired, ...)."""


def jwk_thumbprint(jwk: Mapping[str, str]) -> str:
    """RFC 7638 JWK thumbprint (SHA-256, base64url, unpadded) of an OKP public key."""
    members = {name: jwk[name] for name in ("crv", "kty", "x")}
    canonical = json.dumps(members, separators=(",", ":"), sort_keys=True)
    digest = hashlib.sha256(canonical.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@dataclass(frozen=True)
class SigningKey:
    private_key: Ed25519PrivateKey
    public_jwk: Mapping[str, str]
    kid: str

    @classmethod
    def from_private_key(cls, private_key: Ed25519PrivateKey) -> SigningKey:
        public_jwk = OKPAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
        return cls(private_key=private_key, public_jwk=public_jwk, kid=jwk_thumbprint(public_jwk))

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self.private_key.public_key()


def load_or_create_signing_key(keys_dir: Path) -> SigningKey:
    """Load the signing key from `keys_dir`, generating it on first call.

    Creation writes to a private temp file and hard-links it into place, so a
    concurrent starter can never observe a half-written key, and if two race,
    both end up using whichever key linked first.
    """
    keys_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(keys_dir, 0o700)
    path = keys_dir / KEY_FILENAME

    if not path.exists():
        pem = Ed25519PrivateKey.generate().private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        fd, tmp_name = tempfile.mkstemp(dir=keys_dir, prefix=".signing-key.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(pem)
                f.flush()
                os.fsync(f.fileno())
            try:
                os.link(tmp_name, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(tmp_name)

    os.chmod(path, 0o600)
    private_key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TokenError(f"{path} is not an Ed25519 private key")
    return SigningKey.from_private_key(private_key)


class TokenService:
    """Issues and verifies platform JWTs.

    Verification looks keys up by `kid`, so adding rotation later is a matter
    of passing retired public keys alongside the current signing key.
    """

    def __init__(self, signing_key: SigningKey) -> None:
        self._signing_key = signing_key
        self._verify_keys: dict[str, Ed25519PublicKey] = {signing_key.kid: signing_key.public_key}
        self._jwks_keys = [
            {**signing_key.public_jwk, "kid": signing_key.kid, "use": "sig", "alg": ALGORITHM}
        ]

    @property
    def kid(self) -> str:
        return self._signing_key.kid

    def jwks(self) -> dict[str, Any]:
        return {"keys": [dict(k) for k in self._jwks_keys]}

    def issue_token(
        self,
        claims: Mapping[str, Any],
        ttl: timedelta,
        *,
        now: datetime | None = None,
    ) -> str:
        """Sign `claims` (must include `sub` and `act`) valid for `ttl` from `now`."""
        reserved = _RESERVED_CLAIMS & claims.keys()
        if reserved:
            raise ValueError(f"issue_token sets {sorted(reserved)} itself")
        missing = {"sub", "act"} - claims.keys()
        if missing:
            raise ValueError(f"issue_token requires claims {sorted(missing)}")

        issued_at = now or datetime.now(UTC)
        payload = {
            **claims,
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": int(issued_at.timestamp()),
            "exp": int((issued_at + ttl).timestamp()),
        }
        return jwt.encode(
            payload,
            self._signing_key.private_key,
            algorithm=ALGORITHM,
            headers={"kid": self._signing_key.kid},
        )

    def verify_token(self, token: str, *, act: str | Collection[str]) -> dict[str, Any]:
        """Return the claims of a valid token whose `act` is (one of) `act`.

        Raises `TokenError` on anything else: bad signature, unknown `kid`,
        wrong algorithm/`iss`/`aud`, expired, missing claims, or wrong `act`.
        """
        allowed_acts = {act} if isinstance(act, str) else set(act)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenError(f"malformed token: {exc}") from exc

        kid = header.get("kid")
        key = self._verify_keys.get(kid) if isinstance(kid, str) else None
        if key is None:
            raise TokenError(f"unknown kid: {kid!r}")

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
            raise TokenError(str(exc)) from exc

        if not isinstance(claims["act"], str) or claims["act"] not in allowed_acts:
            raise TokenError(f"act {claims['act']!r} not in {sorted(allowed_acts)}")
        return claims
