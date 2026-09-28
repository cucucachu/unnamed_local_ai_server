"""Hardware-backed host-app pairing (M15-06, docs/PLATFORM.md §4 / §8).

Algorithm (must match Android Keystore `EC` / `secp256r1` + `SHA256withECDSA`):

- Curve: NIST P-256 (secp256r1).
- Public key: X.509 SubjectPublicKeyInfo DER, JSON as unpadded base64url.
- Signature: DER-encoded ECDSA over SHA-256 of the raw challenge bytes
  (same as Java `Signature.getInstance("SHA256withECDSA")`).
- Challenge: 32 random bytes, JSON as unpadded base64url.
- Enroll token: `hd_` + 32 random bytes (urlsafe); only its SHA-256 is stored.

Pytest uses software keys (`cryptography` P-256). The phone holds the
private key in Android Keystore (or iOS Secure Enclave, later) and never
sends it. Do not log private keys, enroll tokens, or raw signatures.
"""

from __future__ import annotations

import base64
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import EllipticCurvePublicKey
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation

from app.core import sessions
from app.core.errors import Conflict, InvalidInput, Unauthorized

logger = logging.getLogger(__name__)

TOKEN_PREFIX = "hd_"
KIND = "homeai-host-pair"
CHALLENGE_BYTES = 32
CHALLENGE_TTL = timedelta(minutes=5)
MAX_NAME_LENGTH = 64
QR_VERSION = 1

Row = dict[str, Any]


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    if not isinstance(text, str) or not text.strip():
        raise InvalidInput("invalid_public_key")
    padded = text.strip() + "=" * (-len(text.strip()) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise InvalidInput("invalid_public_key") from exc


def _decode_signature(text: str) -> bytes:
    if not isinstance(text, str) or not text.strip():
        raise InvalidInput("invalid_signature")
    padded = text.strip() + "=" * (-len(text.strip()) % 4)
    try:
        data = base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, TypeError) as exc:
        raise InvalidInput("invalid_signature") from exc
    if not data:
        raise InvalidInput("invalid_signature")
    return data


def parse_public_key(text: str) -> bytes:
    """SPKI DER for a P-256 public key, or `422 invalid_public_key`."""
    try:
        der = b64url_decode(text)
    except InvalidInput as exc:
        raise InvalidInput("invalid_public_key") from exc
    try:
        key = serialization.load_der_public_key(der)
    except ValueError as exc:
        raise InvalidInput("invalid_public_key") from exc
    if not isinstance(key, EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise InvalidInput("invalid_public_key")
    return der


def verify_signature(public_key_der: bytes, challenge: bytes, signature: bytes) -> bool:
    try:
        key = serialization.load_der_public_key(public_key_der)
    except ValueError:
        return False
    if not isinstance(key, EllipticCurvePublicKey):
        return False
    try:
        key.verify(signature, challenge, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True


def _peer_name(name: str) -> str:
    cleaned = "".join(ch for ch in name.strip() if ch.isprintable())[:MAX_NAME_LENGTH]
    if not cleaned:
        raise InvalidInput("invalid_name")
    return cleaned


def _new_enroll_token() -> str:
    return sessions.new_token(TOKEN_PREFIX)


async def begin_enroll(conn: AsyncConnection, user_id: UUID, username: str) -> dict[str, Any]:
    """Mint a single-use enroll challenge. Previous unused enrolls for this user are dropped."""
    token = _new_enroll_token()
    challenge = secrets.token_bytes(CHALLENGE_BYTES)
    await conn.execute(
        "DELETE FROM device_challenges "
        "WHERE purpose = 'enroll' AND user_id = %s AND used_at IS NULL",
        (user_id,),
    )
    cur = await conn.execute(
        "INSERT INTO device_challenges "
        "(purpose, user_id, token_hash, challenge, expires_at) "
        "VALUES ('enroll', %s, %s, %s, now() + %s) RETURNING expires_at",
        (user_id, sessions.hash_token(token), challenge, CHALLENGE_TTL),
    )
    expires_at = (await cur.fetchone())["expires_at"]
    return {
        "v": QR_VERSION,
        "kind": KIND,
        "token": token,
        "challenge": b64url_encode(challenge),
        "user": username,
        "expires_at": expires_at,
    }


async def finish_enroll(
    conn: AsyncConnection,
    *,
    token: str,
    public_key: str,
    name: str,
    signature: str,
) -> Row:
    """Consume an enroll token and store the device public key.

    `409 already_used` on replay. `422 invalid_token` for unknown/expired.
    `422 invalid_signature` / `invalid_public_key` / `invalid_name` otherwise.
    """
    if not token.startswith(TOKEN_PREFIX):
        raise InvalidInput("invalid_token")
    name = _peer_name(name)
    public_der = parse_public_key(public_key)
    sig = _decode_signature(signature)
    token_hash = sessions.hash_token(token)
    async with conn.transaction():
        cur = await conn.execute(
            "SELECT id, user_id, challenge, expires_at, used_at "
            "FROM device_challenges WHERE token_hash = %s AND purpose = 'enroll' "
            "FOR UPDATE",
            (token_hash,),
        )
        row = await cur.fetchone()
        if row is None:
            raise InvalidInput("invalid_token")
        if row["used_at"] is not None:
            raise Conflict("already_used")
        if row["expires_at"] <= datetime.now(UTC):
            raise InvalidInput("invalid_token")
        if not verify_signature(public_der, bytes(row["challenge"]), sig):
            raise InvalidInput("invalid_signature")
        try:
            cur = await conn.execute(
                "INSERT INTO device_pairs (user_id, public_key, name) "
                "VALUES (%s, %s, %s) "
                "RETURNING id, name, created_at, last_used_at",
                (row["user_id"], public_der, name),
            )
        except UniqueViolation as exc:
            raise Conflict("already_enrolled") from exc
        device = await cur.fetchone()
        await conn.execute(
            "UPDATE device_challenges SET used_at = now() WHERE id = %s",
            (row["id"],),
        )
    logger.info("device_pairs: enrolled %s for user %s", device["id"], row["user_id"])
    return device


async def begin_login(conn: AsyncConnection, device_id: UUID) -> dict[str, Any]:
    """Issue a login challenge for an enrolled device.

    Unknown `device_id` is `401 invalid_credentials` (do not leak existence).
    """
    cur = await conn.execute(
        "SELECT id FROM device_pairs WHERE id = %s",
        (device_id,),
    )
    if await cur.fetchone() is None:
        raise Unauthorized("invalid_credentials")
    challenge = secrets.token_bytes(CHALLENGE_BYTES)
    await conn.execute(
        "DELETE FROM device_challenges "
        "WHERE purpose = 'login' AND device_id = %s AND used_at IS NULL",
        (device_id,),
    )
    cur = await conn.execute(
        "INSERT INTO device_challenges "
        "(purpose, device_id, challenge, expires_at) "
        "VALUES ('login', %s, %s, now() + %s) RETURNING expires_at",
        (device_id, challenge, CHALLENGE_TTL),
    )
    expires_at = (await cur.fetchone())["expires_at"]
    return {"challenge": b64url_encode(challenge), "expires_at": expires_at}


async def finish_login(conn: AsyncConnection, device_id: UUID, signature: str) -> Row:
    """Verify a signed login challenge. Returns the device + user credentials.

    Unknown device, wrong key, expired or replayed challenge: `401
    invalid_credentials` (same body; do not leak). The caller then applies
    TOTP / disabled / session mint in the same order as password login.
    """
    sig = _decode_signature(signature)
    async with conn.transaction():
        cur = await conn.execute(
            "SELECT d.id, d.user_id, d.public_key, d.name "
            "FROM device_pairs d WHERE d.id = %s FOR UPDATE",
            (device_id,),
        )
        device = await cur.fetchone()
        if device is None:
            raise Unauthorized("invalid_credentials")
        cur = await conn.execute(
            "SELECT id, challenge, expires_at, used_at "
            "FROM device_challenges "
            "WHERE purpose = 'login' AND device_id = %s "
            "ORDER BY expires_at DESC LIMIT 1 FOR UPDATE",
            (device_id,),
        )
        challenge_row = await cur.fetchone()
        if (
            challenge_row is None
            or challenge_row["used_at"] is not None
            or challenge_row["expires_at"] <= datetime.now(UTC)
        ):
            raise Unauthorized("invalid_credentials")
        if not verify_signature(
            bytes(device["public_key"]), bytes(challenge_row["challenge"]), sig
        ):
            raise Unauthorized("invalid_credentials")
        await conn.execute(
            "UPDATE device_challenges SET used_at = now() WHERE id = %s",
            (challenge_row["id"],),
        )
        await conn.execute(
            "UPDATE device_pairs SET last_used_at = now() WHERE id = %s",
            (device_id,),
        )
        cur = await conn.execute(
            "SELECT id, password_hash, totp_secret, totp_last_step, disabled_at, require_passkeys "
            "FROM users WHERE id = %s",
            (device["user_id"],),
        )
        creds = await cur.fetchone()
        if creds is None:
            raise Unauthorized("invalid_credentials")
    creds["host_device_id"] = device["id"]
    creds["device_name"] = device["name"]
    return creds


async def list_pairs(conn: AsyncConnection, user_id: UUID) -> list[Row]:
    cur = await conn.execute(
        "SELECT id, name, created_at, last_used_at FROM device_pairs "
        "WHERE user_id = %s ORDER BY created_at",
        (user_id,),
    )
    return await cur.fetchall()


async def revoke_pair(conn: AsyncConnection, user_id: UUID, pair_id: UUID) -> bool:
    """Delete the pair and revoke sessions tagged with `host_device_id`.

    Revoke tagged sessions *before* DELETE (`ON DELETE SET NULL` would
    otherwise untag them and leave them active), same as WireGuard peers.
    """
    async with conn.transaction():
        cur = await conn.execute(
            "SELECT id FROM device_pairs WHERE id = %s AND user_id = %s",
            (pair_id, user_id),
        )
        if await cur.fetchone() is None:
            return False
        await conn.execute(
            "UPDATE sessions SET revoked_at = now() "
            "WHERE host_device_id = %s AND revoked_at IS NULL",
            (pair_id,),
        )
        await conn.execute(
            "DELETE FROM device_pairs WHERE id = %s AND user_id = %s",
            (pair_id, user_id),
        )
    return True
