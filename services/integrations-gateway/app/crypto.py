"""Credential encryption (docs/INTEGRATIONS.md §5).

AES-256-GCM with a key in a file only this container can read. Each
ciphertext is bound to its integration and user (the associated data), so a
row copied to another user or integration doesn't decrypt. The platform
stores the ciphertext and can never open it.

Format: one version byte, a 12-byte random nonce, then the AEAD output. The
plaintext is a small JSON object (`{"kind": "token", "value": ...}`), so
other kinds (OAuth tokens, M18-07) fit later.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = b"\x01"
NONCE_BYTES = 12
KEY_BYTES = 32


class CredentialError(Exception):
    """A ciphertext that doesn't open with this key, integration and user."""


def _aad(integration_id: UUID, user_id: UUID | None) -> bytes:
    owner = str(user_id) if user_id is not None else "shared"
    return f"homeai/integration-credential/v1/{integration_id}/{owner}".encode()


class CredentialKey:
    def __init__(self, key: bytes) -> None:
        if len(key) != KEY_BYTES:
            raise ValueError("credential key must be 32 bytes")
        self._aead = AESGCM(key)

    @classmethod
    def load_or_create(cls, path: Path) -> CredentialKey:
        """The key at `path`, made (0600) if there isn't one yet."""
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as f:
                f.write(os.urandom(KEY_BYTES))
        return cls(path.read_bytes())

    def seal(self, integration_id: UUID, user_id: UUID | None, secret: dict[str, Any]) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        plaintext = json.dumps(secret, separators=(",", ":")).encode()
        return VERSION + nonce + self._aead.encrypt(nonce, plaintext, _aad(integration_id, user_id))

    def open(self, ciphertext: bytes, integration_id: UUID, user_id: UUID | None) -> dict[str, Any]:
        if len(ciphertext) <= 1 + NONCE_BYTES or ciphertext[:1] != VERSION:
            raise CredentialError("bad format")
        nonce, body = ciphertext[1 : 1 + NONCE_BYTES], ciphertext[1 + NONCE_BYTES :]
        try:
            plaintext = self._aead.decrypt(nonce, body, _aad(integration_id, user_id))
        except InvalidTag as exc:
            raise CredentialError("doesn't open") from exc
        secret = json.loads(plaintext)
        if not isinstance(secret, dict):
            raise CredentialError("bad format")
        return secret


def auth_headers(secret: dict[str, Any]) -> dict[str, str]:
    """The headers a stored credential becomes on each request to the server."""
    if secret.get("kind") == "token" and isinstance(secret.get("value"), str):
        value = secret["value"].strip()
        if not value.lower().startswith("bearer "):
            value = f"Bearer {value}"
        return {"Authorization": value}
    raise CredentialError("unknown credential kind")
