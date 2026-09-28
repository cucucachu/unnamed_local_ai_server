"""Browser passkeys (WebAuthn) for domain mode (M15-04).

RP ID is `WEBAUTHN_RP_ID` if set, else `HOMEAI_DOMAIN`. Both empty, or the
unsafe `homeai.local`, means passkeys are off (`409 domain_required`).
`http://localhost` is a valid secure context: tests set `WEBAUTHN_RP_ID=
localhost` without setting a domain. Challenges are stored in Postgres
(5-minute TTL) so they are not tied to `--workers 1` memory.
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Any, NoReturn
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from fastapi import Request
from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.api.session_http import is_https
from app.core.config import Settings
from app.core.errors import Conflict, Forbidden, InvalidInput, Unauthorized
from app.core.principal import Principal

logger = logging.getLogger(__name__)

RP_NAME = "Home AI"
CHALLENGE_TTL = timedelta(minutes=5)
# `homeai.local` is not a safe WebAuthn RP ID (no public suffix). Never
# fall back to it. `localhost` is allowed when set explicitly (secure context).
UNSAFE_RP_IDS = frozenset({"homeai.local"})

Row = dict[str, Any]


def rp_id(settings: Settings) -> str | None:
    """The RP ID passkey ceremonies use, or None if passkeys are off."""
    value = (settings.webauthn_rp_id or settings.homeai_domain or "").strip().lower()
    if not value or value in UNSAFE_RP_IDS:
        return None
    return value


def require_rp_id(settings: Settings) -> str:
    value = rp_id(settings)
    if value is None:
        raise Conflict("domain_required")
    return value


def request_hostname(request: Request) -> str:
    """Hostname from `X-Forwarded-Host` then `Host`, without a port."""
    raw = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    raw = raw.split(",")[0].strip()
    host, _port = _split_host_port(raw)
    return host.lower()


def expected_origin(request: Request) -> str:
    """Browser origin for this request (scheme + host, default ports omitted)."""
    raw = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    raw = raw.split(",")[0].strip()
    host, port = _split_host_port(raw)
    scheme = "https" if is_https(request) else "http"
    default = "443" if scheme == "https" else "80"
    if port and port != default:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host.lower()}"


def origin_ok(request: Request, settings: Settings) -> bool:
    rid = rp_id(settings)
    if rid is None:
        return False
    return request_hostname(request) == rid


def require_matching_origin(request: Request, rid: str) -> str:
    """The origin string to verify against, or `422 passkey_rp_mismatch`."""
    if request_hostname(request) != rid:
        raise InvalidInput("passkey_rp_mismatch")
    return expected_origin(request)


def _split_host_port(raw: str) -> tuple[str, str | None]:
    if not raw:
        return "", None
    if raw.startswith("[") and "]" in raw:
        host, _, rest = raw[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else None
        return host, port or None
    # hostname:port — but don't split IPv4-looking nothing; last colon.
    if raw.count(":") == 1:
        host, _, port = raw.partition(":")
        if port.isdigit():
            return host, port
    return raw, None


def _options_dict(options: Any) -> dict[str, Any]:
    return json.loads(options_to_json(options))


def _transports(values: list[str] | None) -> list[AuthenticatorTransport] | None:
    if not values:
        return None
    out: list[AuthenticatorTransport] = []
    for value in values:
        try:
            out.append(AuthenticatorTransport(value))
        except ValueError:
            continue
    return out or None


async def _purge_expired(conn: AsyncConnection) -> None:
    await conn.execute("DELETE FROM webauthn_challenges WHERE expires_at <= now()")


async def _store_challenge(
    conn: AsyncConnection, purpose: str, challenge: bytes, user_id: UUID | None
) -> None:
    await _purge_expired(conn)
    if user_id is not None:
        await conn.execute(
            "DELETE FROM webauthn_challenges WHERE purpose = %s AND user_id = %s",
            (purpose, user_id),
        )
    await conn.execute(
        "INSERT INTO webauthn_challenges (purpose, user_id, challenge, expires_at) "
        "VALUES (%s, %s, %s, now() + %s)",
        (purpose, user_id, challenge, CHALLENGE_TTL),
    )


async def _take_challenge(
    conn: AsyncConnection, purpose: str, user_id: UUID | None
) -> bytes | None:
    if user_id is None:
        return None
    cur = await conn.execute(
        "DELETE FROM webauthn_challenges "
        "WHERE id = ("
        "  SELECT id FROM webauthn_challenges "
        "  WHERE purpose = %s AND user_id = %s AND expires_at > now() "
        "  ORDER BY expires_at DESC LIMIT 1"
        " FOR UPDATE) RETURNING challenge",
        (purpose, user_id),
    )
    row = await cur.fetchone()
    return None if row is None else bytes(row["challenge"])


async def list_credentials(conn: AsyncConnection, user_id: UUID) -> list[Row]:
    cur = await conn.execute(
        "SELECT id, name, transports, created_at, last_used_at "
        "FROM webauthn_credentials WHERE user_id = %s ORDER BY created_at",
        (user_id,),
    )
    return await cur.fetchall()


async def _descriptors(conn: AsyncConnection, user_id: UUID) -> list[PublicKeyCredentialDescriptor]:
    cur = await conn.execute(
        "SELECT credential_id, transports FROM webauthn_credentials WHERE user_id = %s",
        (user_id,),
    )
    out: list[PublicKeyCredentialDescriptor] = []
    for row in await cur.fetchall():
        out.append(
            PublicKeyCredentialDescriptor(
                id=bytes(row["credential_id"]),
                transports=_transports(row["transports"]),
            )
        )
    return out


async def begin_register(conn: AsyncConnection, principal: Principal, rid: str) -> dict[str, Any]:
    exclude = await _descriptors(conn, principal.user_id)
    options = generate_registration_options(
        rp_id=rid,
        rp_name=RP_NAME,
        user_id=principal.user_id.bytes,
        user_name=principal.username,
        user_display_name=principal.display_name,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=exclude or None,
    )
    await _store_challenge(conn, "register", options.challenge, principal.user_id)
    return _options_dict(options)


async def finish_register(
    conn: AsyncConnection,
    principal: Principal,
    rid: str,
    origin: str,
    credential: dict[str, Any],
    name: str | None,
) -> Row:
    challenge = await _take_challenge(conn, "register", principal.user_id)
    if challenge is None:
        raise InvalidInput("invalid_passkey")
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rid,
            expected_origin=origin,
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as exc:
        raise InvalidInput("invalid_passkey") from exc
    transports = credential.get("response", {}).get("transports")
    if not isinstance(transports, list):
        transports = None
    label = None if name is None else name.strip()[:64] or None
    try:
        cur = await conn.execute(
            "INSERT INTO webauthn_credentials "
            "(user_id, credential_id, public_key, sign_count, transports, name) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "RETURNING id, name, transports, created_at, last_used_at",
            (
                principal.user_id,
                bytes(verification.credential_id),
                bytes(verification.credential_public_key),
                int(verification.sign_count),
                transports,
                label,
            ),
        )
    except UniqueViolation as exc:
        raise Conflict("passkey_exists") from exc
    return await cur.fetchone()


async def begin_login(conn: AsyncConnection, user_id: UUID, rid: str) -> dict[str, Any]:
    allow = await _descriptors(conn, user_id)
    if not allow:
        raise Unauthorized("invalid_credentials")
    options = generate_authentication_options(
        rp_id=rid,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    await _store_challenge(conn, "login", options.challenge, user_id)
    return _options_dict(options)


async def begin_step_up(conn: AsyncConnection, user_id: UUID, rid: str) -> dict[str, Any]:
    allow = await _descriptors(conn, user_id)
    if not allow:
        raise Conflict("no_passkey")
    options = generate_authentication_options(
        rp_id=rid,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    await _store_challenge(conn, "step_up", options.challenge, user_id)
    return _options_dict(options)


async def _credential_for(conn: AsyncConnection, credential_id: bytes) -> Row | None:
    cur = await conn.execute(
        "SELECT id, user_id, public_key, sign_count FROM webauthn_credentials "
        "WHERE credential_id = %s",
        (credential_id,),
    )
    return await cur.fetchone()


def _raw_id(credential: dict[str, Any]) -> bytes | None:
    raw = credential.get("rawId") or credential.get("id")
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, str):
        try:
            return base64url_to_bytes(raw)
        except ValueError:
            return None
    return None


def _p1363_to_der(signature: bytes) -> bytes:
    """WebAuthn ES256/384/512 assertions are IEEE P1363 (`r||s`); cryptography wants DER.

    Do not special-case a leading `0x30`: a P1363 blob can start with that
    byte, and a 64-byte DER SEQUENCE would then be recoded incorrectly.
    Callers try the assertion as sent first, then this recoding.
    """
    if len(signature) in (64, 96, 132):
        half = len(signature) // 2
        r = int.from_bytes(signature[:half], "big")
        s = int.from_bytes(signature[half:], "big")
        return encode_dss_signature(r, s)
    return signature


def _normalize_assertion(credential: dict[str, Any]) -> dict[str, Any]:
    """Copy the assertion and convert a P1363 ECDSA signature to DER for py_webauthn."""
    response = credential.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("signature"), str):
        return credential
    try:
        der = _p1363_to_der(base64url_to_bytes(response["signature"]))
    except ValueError:
        return credential
    return {**credential, "response": {**response, "signature": bytes_to_base64url(der)}}


def _verify_assertion(
    credential: dict[str, Any],
    *,
    challenge: bytes,
    rid: str,
    origin: str,
    public_key: bytes,
    sign_count: int,
):
    """Try the assertion as sent, then with a P1363 ECDSA signature recoded as DER."""
    last: InvalidAuthenticationResponse | None = None
    seen: set[str] = set()
    for candidate in (credential, _normalize_assertion(credential)):
        sig = ""
        response = candidate.get("response") if isinstance(candidate.get("response"), dict) else {}
        if isinstance(response.get("signature"), str):
            sig = response["signature"]
        if sig in seen:
            continue
        seen.add(sig)
        try:
            return verify_authentication_response(
                credential=candidate,
                expected_challenge=challenge,
                expected_rp_id=rid,
                expected_origin=origin,
                credential_public_key=public_key,
                credential_current_sign_count=sign_count,
                require_user_verification=True,
            )
        except InvalidAuthenticationResponse as exc:
            last = exc
    if last is None:
        raise InvalidAuthenticationResponse("invalid assertion")
    raise last


def _assertion_fail(purpose: str, cause: Exception | None = None) -> NoReturn:
    if purpose == "login":
        exc: Exception = Unauthorized("invalid_credentials")
    else:
        exc = Forbidden("invalid_passkey")
    if cause is not None:
        raise exc from cause
    raise exc


async def finish_assertion(
    conn: AsyncConnection,
    *,
    purpose: str,
    expected_user_id: UUID,
    rid: str,
    origin: str,
    credential: dict[str, Any],
) -> UUID:
    """Verify an assertion for `expected_user_id`. Returns that user id.

    Wrong-user credentials and a decreasing sign_count fail closed.
    """
    challenge = await _take_challenge(conn, purpose, expected_user_id)
    if challenge is None:
        logger.warning("webauthn %s assertion failed: no challenge", purpose)
        _assertion_fail(purpose)
    raw_id = _raw_id(credential)
    if raw_id is None:
        logger.warning("webauthn %s assertion failed: missing credential id", purpose)
        _assertion_fail(purpose)
    stored = await _credential_for(conn, raw_id)
    if stored is None or stored["user_id"] != expected_user_id:
        logger.warning("webauthn %s assertion failed: unknown or wrong-user credential", purpose)
        _assertion_fail(purpose)
    try:
        verification = _verify_assertion(
            credential,
            challenge=challenge,
            rid=rid,
            origin=origin,
            public_key=bytes(stored["public_key"]),
            sign_count=int(stored["sign_count"]),
        )
    except InvalidAuthenticationResponse as exc:
        sig = b""
        response = credential.get("response") if isinstance(credential.get("response"), dict) else {}
        if isinstance(response.get("signature"), str):
            try:
                sig = base64url_to_bytes(response["signature"])
            except ValueError:
                sig = b""
        logger.warning(
            "webauthn %s assertion failed: %s (sig_len=%s first=%s)",
            purpose,
            exc,
            len(sig),
            sig[:1].hex() if sig else "",
        )
        _assertion_fail(purpose, exc)
    cur = await conn.execute(
        "UPDATE webauthn_credentials SET sign_count = %s, last_used_at = now() "
        "WHERE id = %s AND sign_count <= %s",
        (int(verification.new_sign_count), stored["id"], int(verification.new_sign_count)),
    )
    if cur.rowcount != 1:
        logger.warning("webauthn %s assertion failed: sign_count race", purpose)
        _assertion_fail(purpose)
    return expected_user_id


async def delete_credential(conn: AsyncConnection, user_id: UUID, credential_pk: UUID) -> bool:
    cur = await conn.execute(
        "DELETE FROM webauthn_credentials WHERE id = %s AND user_id = %s",
        (credential_pk, user_id),
    )
    return cur.rowcount == 1
