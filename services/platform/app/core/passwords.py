"""Password hashing (argon2id) and the username/password/display-name rules."""

from __future__ import annotations

import re
from functools import cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.errors import InvalidInput

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 1024
MAX_DISPLAY_NAME_LENGTH = 64

# Mirrors the CHECK constraint on users.username (0002_accounts.sql).
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@cache
def _dummy_hash() -> str:
    return _hasher.hash("not-a-real-password")


def burn_verify(password: str) -> None:
    """Spend the same time as a real verify, for usernames that don't exist."""
    verify_password(_dummy_hash(), password)


def normalize_username(username: str) -> str:
    normalized = username.strip().lower()
    if not USERNAME_RE.fullmatch(normalized):
        raise InvalidInput("invalid_username")
    return normalized


def check_password_policy(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise InvalidInput("weak_password")


def normalize_display_name(display_name: str) -> str:
    normalized = display_name.strip()
    if not 1 <= len(normalized) <= MAX_DISPLAY_NAME_LENGTH or not normalized.isprintable():
        raise InvalidInput("invalid_display_name")
    return normalized
