"""WireGuard server keys, peer records, and the wg0.conf the sidecar applies.

The platform is the source of truth (docs/PLATFORM.md §8, M15-01):

- Server X25519 key pair: `/data/platform/wireguard/server.key` (0600) and
  `server.pub`. Generated on first start; never logged.
- Peers: `wireguard_peers` (public key + tunnel address only). The peer
  private key is returned once in the client config and discarded.
- Live config: `/data/wireguard/wg0.conf` (0600) on the shared volume the
  `wireguard` sidecar polls and `wg syncconf`s. Atomic replace so the
  sidecar never sees a truncated file.

Tunnel: 10.13.13.0/24, server 10.13.13.1, UDP 51820. Client DNS is the
tunnel gateway (10.13.13.1), which the sidecar answers for `homeai.local`.
"""

from __future__ import annotations

import base64
import ipaddress
import logging
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from psycopg import AsyncConnection

from app.core.config import Settings
from app.core.errors import Conflict, InvalidInput

logger = logging.getLogger(__name__)

# Serialize peer allocate/insert/conf write (homeai + 0x0008).
ADVISORY_LOCK_KEY = 0x686F6D6561690008

Row = dict[str, Any]

SUBNET = ipaddress.IPv4Network("10.13.13.0/24")
SERVER_HOST = ipaddress.IPv4Address("10.13.13.1")
LISTEN_PORT = 51820
MAX_NAME_LENGTH = 64
MAX_PEERS_PER_USER = 32
KEY_DIRNAME = "wireguard"
SERVER_KEY_NAME = "server.key"
SERVER_PUB_NAME = "server.pub"
CONF_NAME = "wg0.conf"

# Interface stanza for a client; `AllowedIPs` is the tunnel only (split
# tunnel). Full-tunnel / split-DNS is M15-03.
CLIENT_ALLOWED_IPS = "10.13.13.0/24"
CLIENT_DNS = "10.13.13.1"
KEEPALIVE = 25


def generate_keypair() -> tuple[str, str]:
    """A WireGuard-ready X25519 pair as standard base64 (private, public)."""
    private = X25519PrivateKey.generate()
    priv_b64 = base64.b64encode(
        private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    ).decode()
    pub_b64 = base64.b64encode(
        private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode()
    return priv_b64, pub_b64


def public_key_of(private_b64: str) -> str:
    raw = base64.b64decode(private_b64, validate=True)
    if len(raw) != 32:
        raise ValueError("WireGuard private key must be 32 bytes")
    public = X25519PrivateKey.from_private_bytes(raw).public_key()
    return base64.b64encode(public.public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def client_config(
    *,
    private_key: str,
    address: str,
    server_public_key: str,
    endpoint: str,
    dns: str = CLIENT_DNS,
    allowed_ips: str = CLIENT_ALLOWED_IPS,
    keepalive: int = KEEPALIVE,
) -> str:
    """wg-quick client config text (also the QR payload)."""
    host = address.split("/", 1)[0]
    return (
        "[Interface]\n"
        f"PrivateKey = {private_key}\n"
        f"Address = {host}/32\n"
        f"DNS = {dns}\n"
        "\n"
        "[Peer]\n"
        f"PublicKey = {server_public_key}\n"
        f"AllowedIPs = {allowed_ips}\n"
        f"Endpoint = {endpoint}\n"
        f"PersistentKeepalive = {keepalive}\n"
    )


def server_config(*, private_key: str, peers: list[tuple[str, str]]) -> str:
    """wg0.conf for the sidecar: server interface + one [Peer] per device.

    `peers` is `(public_key, address)` with address a host (optionally /32).
    """
    lines = [
        "[Interface]",
        f"Address = {SERVER_HOST}/24",
        f"ListenPort = {LISTEN_PORT}",
        f"PrivateKey = {private_key}",
        "",
    ]
    for public_key, address in peers:
        host = address.split("/", 1)[0]
        lines.extend(
            [
                "[Peer]",
                f"PublicKey = {public_key}",
                f"AllowedIPs = {host}/32",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _peer_name(name: str) -> str:
    cleaned = "".join(ch for ch in name.strip() if ch.isprintable())[:MAX_NAME_LENGTH]
    if not cleaned:
        raise InvalidInput("invalid_name")
    return cleaned


def _atomic_write(path: Path, data: str, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
    os.chmod(path, mode)


class WireGuardService:
    """Server keys on disk + peer rows in Postgres + the sidecar's wg0.conf."""

    def __init__(self, settings: Settings) -> None:
        self._key_dir = settings.platform_data_dir / KEY_DIRNAME
        self._conf_dir = settings.wireguard_config_dir
        self._endpoint = settings.wireguard_endpoint.strip() or "homeai.local:51820"

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def conf_path(self) -> Path:
        return self._conf_dir / CONF_NAME

    @property
    def key_path(self) -> Path:
        return self._key_dir / SERVER_KEY_NAME

    @property
    def pub_path(self) -> Path:
        return self._key_dir / SERVER_PUB_NAME

    def ensure_server_keys(self) -> str:
        """Load or create the server private key; return the public key.

        Creation uses a private temp file + replace, matching the signing-key
        recipe in `tokens.py`, so a concurrent starter never sees a
        half-written key.
        """
        self._key_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._key_dir, 0o700)
        if not self.key_path.exists():
            private, public = generate_keypair()
            _atomic_write(self.key_path, private + "\n", 0o600)
            _atomic_write(self.pub_path, public + "\n", 0o644)
            logger.info("wireguard: generated server key pair")
        os.chmod(self.key_path, 0o600)
        public = self.pub_path.read_text().strip() if self.pub_path.exists() else ""
        if not public:
            public = public_key_of(self.key_path.read_text().strip())
            _atomic_write(self.pub_path, public + "\n", 0o644)
        return public

    def server_public_key(self) -> str:
        return self.ensure_server_keys()

    def _server_private_key(self) -> str:
        self.ensure_server_keys()
        return self.key_path.read_text().strip()

    async def write_server_config(self, conn: AsyncConnection) -> None:
        """Rewrite wg0.conf from current peers. Never logs the private key."""
        cur = await conn.execute(
            "SELECT public_key, host(address)::text AS address FROM wireguard_peers "
            "ORDER BY address"
        )
        rows = await cur.fetchall()
        public = self.ensure_server_keys()
        body = server_config(
            private_key=self._server_private_key(),
            peers=[(r["public_key"], r["address"]) for r in rows],
        )
        _atomic_write(self.conf_path, body, 0o600)
        logger.info("wireguard: wrote %s (%d peers, server public %s)", self.conf_path, len(rows), public)

    async def list_peers(self, conn: AsyncConnection, user_id: UUID) -> list[Row]:
        cur = await conn.execute(
            "SELECT id, name, host(address)::text AS address, created_at "
            "FROM wireguard_peers WHERE user_id = %s ORDER BY created_at",
            (user_id,),
        )
        return await cur.fetchall()

    async def get_peer(self, conn: AsyncConnection, user_id: UUID, peer_id: UUID) -> Row | None:
        cur = await conn.execute(
            "SELECT id, name, public_key, host(address)::text AS address, created_at "
            "FROM wireguard_peers WHERE id = %s AND user_id = %s",
            (peer_id, user_id),
        )
        return await cur.fetchone()

    async def peer_belongs_to_user(self, conn: AsyncConnection, peer_id: UUID, user_id: UUID) -> bool:
        cur = await conn.execute(
            "SELECT 1 FROM wireguard_peers WHERE id = %s AND user_id = %s",
            (peer_id, user_id),
        )
        return await cur.fetchone() is not None

    async def create_peer(self, conn: AsyncConnection, user_id: UUID, name: str) -> tuple[Row, str]:
        """Insert a peer; return `(row, client_config_text)`. Config includes the
        private key and is the only time it is available."""
        name = _peer_name(name)
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
            cur = await conn.execute(
                "SELECT count(*)::int AS n FROM wireguard_peers WHERE user_id = %s",
                (user_id,),
            )
            if (await cur.fetchone())["n"] >= MAX_PEERS_PER_USER:
                raise Conflict("too_many_devices")

            address = await self._allocate_address(conn)
            private, public = generate_keypair()
            cur = await conn.execute(
                "INSERT INTO wireguard_peers (user_id, name, public_key, address) "
                "VALUES (%s, %s, %s, %s) "
                "RETURNING id, name, host(address)::text AS address, created_at",
                (user_id, name, public, str(address)),
            )
            row = await cur.fetchone()
            await self.write_server_config(conn)
        config = client_config(
            private_key=private,
            address=row["address"],
            server_public_key=self.server_public_key(),
            endpoint=self._endpoint,
        )
        return row, config

    async def revoke_peer(self, conn: AsyncConnection, user_id: UUID, peer_id: UUID) -> bool:
        """Delete the peer, revoke sessions tagged to it, rewrite wg0.conf.

        Untagged sessions (typical LAN browsers) are left alone. Logins that
        sent `device_id` on `POST /api/auth/login` are the ones dropped.
        """
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADVISORY_LOCK_KEY,))
            cur = await conn.execute(
                "SELECT id FROM wireguard_peers WHERE id = %s AND user_id = %s",
                (peer_id, user_id),
            )
            if await cur.fetchone() is None:
                return False
            # Revoke tagged sessions *before* DELETE (ON DELETE SET NULL would
            # otherwise untag them and leave them active).
            await conn.execute(
                "UPDATE sessions SET revoked_at = now() "
                "WHERE device_id = %s AND revoked_at IS NULL",
                (peer_id,),
            )
            await conn.execute(
                "DELETE FROM wireguard_peers WHERE id = %s AND user_id = %s",
                (peer_id, user_id),
            )
            await self.write_server_config(conn)
        return True

    async def _allocate_address(self, conn: AsyncConnection) -> ipaddress.IPv4Address:
        cur = await conn.execute("SELECT host(address)::text AS address FROM wireguard_peers")
        used = {ipaddress.IPv4Address(r["address"]) for r in await cur.fetchall()}
        used.add(SERVER_HOST)
        for host in SUBNET.hosts():
            if host not in used:
                return host
        raise Conflict("peers_exhausted")
