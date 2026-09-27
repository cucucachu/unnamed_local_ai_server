"""RFC 6238 TOTP (SHA-1, 6 digits, 30 s) - the parameters every authenticator app supports."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

DIGITS = 6
PERIOD_S = 30
# Accept the previous and next step too, for clock drift between phone and box.
WINDOW = 1
ISSUER = "HomeAI"


def generate_secret() -> str:
    """160 random bits, base32 without padding (the form authenticator apps expect)."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // PERIOD_S)


def code_for(secret: str, now: float | None = None) -> str:
    return _code_at(secret, current_step(now))


def match_step(secret: str, code: str, *, after_step: int | None = None, now: float | None = None):
    """Return the time step `code` is valid for, or None.

    Steps at or before `after_step` (the last one accepted) are refused, so
    each code works once.
    """
    code = code.strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - WINDOW, step + WINDOW + 1):
        if after_step is not None and candidate <= after_step:
            continue
        if hmac.compare_digest(_code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, username: str) -> str:
    label = quote(f"{ISSUER}:{username}", safe=":@")
    query = urlencode(
        {
            "secret": secret,
            "issuer": ISSUER,
            "algorithm": "SHA1",
            "digits": DIGITS,
            "period": PERIOD_S,
        }
    )
    return f"otpauth://totp/{label}?{query}"
