"""First-admin bootstrap via a one-time setup code (docs/PLATFORM.md §4).

While `platform_state.bootstrap_admin_id` is unset, startup makes sure a
setup code exists: it reuses `<data_dir>/setup-code` if present (so a
restart doesn't invalidate a code someone already copied), otherwise
generates one, and logs it. `complete()` creates the admin, records
`bootstrap_admin_id`, and deletes the code - after which setup is closed for
good. Users created any other way (CLI, invites) never complete bootstrap.

The code is held in memory as well as on disk; that and the rate limiter
are why the service runs a single worker.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.core import sessions, users
from app.core.errors import Conflict, Unauthorized
from app.core.storage import SpaceStorage

logger = logging.getLogger(__name__)

STATE_KEY = "bootstrap_admin_id"
CODE_FILENAME = "setup-code"
# No 0/O, 1/I/L: the code is read off a terminal and typed by hand.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
GROUPS, GROUP_LEN = 4, 4

# Arbitrary but fixed bigint ("homeai" + 0x0003) serializing concurrent setups.
SETUP_LOCK_KEY = 0x686F6D6561690003

_CODE_RE = re.compile(rf"^[{ALPHABET}]{{{GROUPS * GROUP_LEN}}}$")

Row = dict[str, Any]


def generate_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))


def normalize_code(code: str) -> str:
    return re.sub(r"[\s-]", "", code).upper()


def format_code(code: str) -> str:
    return "-".join(code[i : i + GROUP_LEN] for i in range(0, len(code), GROUP_LEN))


async def bootstrap_admin_id(conn: AsyncConnection) -> str | None:
    cur = await conn.execute("SELECT value FROM platform_state WHERE key = %s", (STATE_KEY,))
    row = await cur.fetchone()
    return None if row is None else row["value"]


class Bootstrap:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / CODE_FILENAME
        self._code: str | None = None

    @property
    def setup_required(self) -> bool:
        return self._code is not None

    async def prepare(self, conn: AsyncConnection) -> None:
        """Called at startup: load or create the code, or clean up once bootstrapped."""
        if await bootstrap_admin_id(conn) is not None:
            self._code = None
            self.path.unlink(missing_ok=True)
            return

        existing = normalize_code(self.path.read_text()) if self.path.exists() else ""
        self._code = existing if _CODE_RE.fullmatch(existing) else generate_code()
        if self._code != existing:
            self._write(format_code(self._code))
        banner = "=" * 64
        logger.warning(
            "\n%s\n  HOME AI SETUP CODE: %s\n  Enter it on the setup screen to create the first "
            "admin.\n  Also in %s inside the platform container.\n%s",
            banner, format_code(self._code), self.path, banner,
        )  # fmt: skip

    def _write(self, text: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=".setup-code.")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(text + "\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    async def complete(
        self,
        conn: AsyncConnection,
        *,
        setup_code: str,
        username: str,
        display_name: str,
        password: str,
        storage: SpaceStorage,
        device_label: str | None = None,
    ) -> tuple[Row, str]:
        """Create the bootstrap admin and log them in; returns (user, session token)."""
        if self._code is None:
            raise Conflict("setup_complete")
        if not hmac.compare_digest(normalize_code(setup_code).encode(), self._code.encode()):
            raise Unauthorized("invalid_setup_code")

        new_user = await users.prepare_user(username, display_name, password, "admin")
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(%s)", (SETUP_LOCK_KEY,))
            if await bootstrap_admin_id(conn) is not None:
                raise Conflict("setup_complete")
            user = await users.insert_user(conn, new_user, storage)
            await conn.execute(
                "INSERT INTO platform_state (key, value) VALUES (%s, %s)",
                (STATE_KEY, Jsonb(str(user["id"]))),
            )
            session_token, _ = await sessions.create_session(conn, user["id"], device_label)

        self._code = None
        self.path.unlink(missing_ok=True)
        logger.info("bootstrap complete: admin %s", user["username"])
        return user, session_token
