"""In-memory WebAuthn authenticator for pytest (ES256, packed `none` attestation).

Not a skip: tests construct a credential, register it, then assert.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from base64 import urlsafe_b64decode, urlsafe_b64encode

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature


def b64url(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def from_b64url(value: str) -> bytes:
    pad = "=" * ((4 - len(value) % 4) % 4)
    return urlsafe_b64decode(value + pad)


def _raw_es256(der: bytes) -> bytes:
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


class SoftWebAuthnDevice:
    """Platform authenticator with a resident ES256 key and a counter."""

    def __init__(self) -> None:
        self.private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.sign_count = 0
        self.user_handle: bytes | None = None

    def create(self, options: dict, origin: str) -> dict:
        challenge_b64 = options["challenge"]
        rp_id = options["rp"]["id"]
        self.user_handle = from_b64url(options["user"]["id"])
        self.sign_count = 0
        client_data = json.dumps(
            {"type": "webauthn.create", "challenge": challenge_b64, "origin": origin, "crossOrigin": False},
            separators=(",", ":"),
        ).encode()
        auth_data = self._auth_data(rp_id, attested=True, sign_count=0)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["internal"],
            },
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
        }

    def get(self, options: dict, origin: str, *, decrement_count: bool = False) -> dict:
        challenge_b64 = options["challenge"]
        rp_id = options.get("rpId") or options.get("rp", {}).get("id")
        if decrement_count:
            self.sign_count = max(0, self.sign_count - 1)
        else:
            self.sign_count += 1
        client_data = json.dumps(
            {"type": "webauthn.get", "challenge": challenge_b64, "origin": origin, "crossOrigin": False},
            separators=(",", ":"),
        ).encode()
        auth_data = self._auth_data(rp_id, attested=False, sign_count=self.sign_count)
        signature = _raw_es256(
            self.private_key.sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": b64url(self.user_handle) if self.user_handle else None,
            },
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
        }

    def _auth_data(self, rp_id: str, *, attested: bool, sign_count: int) -> bytes:
        flags = 0x05  # UP + UV
        if attested:
            flags |= 0x40  # AT
        body = hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", sign_count)
        if not attested:
            return body
        numbers = self.private_key.public_key().public_numbers()
        cose = cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )
        return body + bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + cose
