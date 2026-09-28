"""User rows: creation, lookup, role/disable changes, password and TOTP updates.

Every function takes an open connection from a `dict_row` autocommit pool
(`app/db/pool.py`) and returns plain dict rows shaped by `USER_COLUMNS`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation

from app.core import passwords, sessions, spaces
from app.core.errors import Conflict, InvalidInput, NotFound
from app.core.storage import SpaceStorage

ROLES = ("admin", "member")

# Arbitrary but fixed bigint ("homeai" + 0x0002) serializing admin-count checks.
ADMIN_LOCK_KEY = 0x686F6D6561690002

USER_COLUMNS = (
    "id, username, display_name, role, uid, (totp_secret IS NOT NULL) AS totp_enabled, "
    "require_passkeys, disabled_at, created_at"
)

Row = dict[str, Any]


@dataclass(frozen=True)
class NewUser:
    """A validated user with its password already hashed, ready to insert."""

    username: str
    display_name: str
    role: str
    password_hash: str


async def prepare_user(username: str, display_name: str, password: str, role: str) -> NewUser:
    if role not in ROLES:
        raise InvalidInput("invalid_role")
    username = passwords.normalize_username(username)
    display_name = passwords.normalize_display_name(display_name)
    passwords.check_password_policy(password)
    password_hash = await asyncio.to_thread(passwords.hash_password, password)
    return NewUser(username, display_name, role, password_hash)


async def insert_user(conn: AsyncConnection, new_user: NewUser, storage: SpaceStorage) -> Row:
    """Insert the user and their personal space, atomically. The only way users are created."""
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "INSERT INTO users (username, display_name, role, password_hash) "
                f"VALUES (%s, %s, %s, %s) RETURNING {USER_COLUMNS}",
                (new_user.username, new_user.display_name, new_user.role, new_user.password_hash),
            )
            user = await cur.fetchone()
            await spaces.create_personal_space(conn, user, storage)
            return user
    except UniqueViolation as exc:
        if exc.diag.constraint_name != "users_username_key":
            raise
        raise Conflict("username_taken") from exc


async def create_user(
    conn: AsyncConnection,
    *,
    username: str,
    display_name: str,
    password: str,
    role: str,
    storage: SpaceStorage,
) -> Row:
    new_user = await prepare_user(username, display_name, password, role)
    return await insert_user(conn, new_user, storage)


async def get_user(conn: AsyncConnection, user_id: UUID) -> Row:
    cur = await conn.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,))
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    return row


async def get_user_by_username(conn: AsyncConnection, username: str) -> Row:
    cur = await conn.execute(
        f"SELECT {USER_COLUMNS} FROM users WHERE username = %s", (username.strip().lower(),)
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    return row


async def list_users(conn: AsyncConnection) -> list[Row]:
    cur = await conn.execute(f"SELECT {USER_COLUMNS} FROM users ORDER BY created_at, username")
    return await cur.fetchall()


async def list_directory(conn: AsyncConnection) -> list[Row]:
    """Enabled users, for member pickers: identity only."""
    cur = await conn.execute(
        "SELECT id, username, display_name FROM users WHERE disabled_at IS NULL ORDER BY username"
    )
    return await cur.fetchall()


async def get_credentials(conn: AsyncConnection, username: str) -> Row | None:
    cur = await conn.execute(
        "SELECT id, password_hash, totp_secret, totp_last_step, disabled_at, require_passkeys "
        "FROM users WHERE username = %s",
        (username,),
    )
    return await cur.fetchone()


async def get_password_hash(conn: AsyncConnection, user_id: UUID) -> str:
    cur = await conn.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    return row["password_hash"]


async def check_password(conn: AsyncConnection, user_id: UUID, password: str) -> bool:
    password_hash = await get_password_hash(conn, user_id)
    return await asyncio.to_thread(passwords.verify_password, password_hash, password)


async def rehash_if_needed(conn: AsyncConnection, user_id: UUID, password: str) -> None:
    password_hash = await get_password_hash(conn, user_id)
    if passwords.needs_rehash(password_hash):
        new_hash = await asyncio.to_thread(passwords.hash_password, password)
        await conn.execute("UPDATE users SET password_hash = %s WHERE id = %s", (new_hash, user_id))


async def set_password(
    conn: AsyncConnection,
    user_id: UUID,
    password: str,
    *,
    keep_session_id: UUID | None = None,
    clear_totp: bool = False,
) -> None:
    """Replace the password and revoke every session except `keep_session_id`."""
    passwords.check_password_policy(password)
    password_hash = await asyncio.to_thread(passwords.hash_password, password)
    async with conn.transaction():
        totp_sql = ", totp_secret = NULL, totp_pending_secret = NULL" if clear_totp else ""
        cur = await conn.execute(
            f"UPDATE users SET password_hash = %s{totp_sql} WHERE id = %s",
            (password_hash, user_id),
        )
        if cur.rowcount == 0:
            raise NotFound("not_found")
        await sessions.revoke_user_sessions(conn, user_id, except_session_id=keep_session_id)


async def set_display_name(conn: AsyncConnection, user_id: UUID, display_name: str) -> None:
    display_name = passwords.normalize_display_name(display_name)
    await conn.execute("UPDATE users SET display_name = %s WHERE id = %s", (display_name, user_id))


async def update_user(
    conn: AsyncConnection,
    user_id: UUID,
    *,
    role: str | None = None,
    disabled: bool | None = None,
    require_passkeys: bool | None = None,
) -> Row:
    """Change role and/or disabled state; never leaves zero enabled admins.

    Disabling revokes all the user's sessions, so re-enabling later doesn't
    resurrect them.
    """
    if role is not None and role not in ROLES:
        raise InvalidInput("invalid_role")
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (ADMIN_LOCK_KEY,))
        cur = await conn.execute(
            "SELECT role, disabled_at, require_passkeys FROM users WHERE id = %s FOR UPDATE",
            (user_id,),
        )
        current = await cur.fetchone()
        if current is None:
            raise NotFound("not_found")

        is_admin_now = current["role"] == "admin" and current["disabled_at"] is None
        new_role = role or current["role"]
        new_disabled = (current["disabled_at"] is not None) if disabled is None else disabled
        new_require = (
            current["require_passkeys"] if require_passkeys is None else require_passkeys
        )
        if is_admin_now and (new_role != "admin" or new_disabled):
            cur = await conn.execute(
                "SELECT count(*) AS n FROM users "
                "WHERE role = 'admin' AND disabled_at IS NULL AND id <> %s",
                (user_id,),
            )
            if (await cur.fetchone())["n"] == 0:
                raise Conflict("last_admin")

        await conn.execute(
            "UPDATE users SET role = %s, require_passkeys = %s, disabled_at = CASE "
            "WHEN NOT %s THEN NULL WHEN disabled_at IS NULL THEN now() ELSE disabled_at END "
            "WHERE id = %s",
            (new_role, new_require, new_disabled, user_id),
        )
        if new_disabled:
            await sessions.revoke_user_sessions(conn, user_id)
    return await get_user(conn, user_id)


# --- TOTP -------------------------------------------------------------------


async def get_totp_state(conn: AsyncConnection, user_id: UUID) -> Row:
    cur = await conn.execute(
        "SELECT username, totp_secret, totp_pending_secret, totp_last_step "
        "FROM users WHERE id = %s",
        (user_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise NotFound("not_found")
    return row


async def set_pending_totp(conn: AsyncConnection, user_id: UUID, secret: str) -> None:
    await conn.execute("UPDATE users SET totp_pending_secret = %s WHERE id = %s", (secret, user_id))


async def activate_pending_totp(conn: AsyncConnection, user_id: UUID, step: int) -> None:
    await conn.execute(
        "UPDATE users SET totp_secret = totp_pending_secret, totp_pending_secret = NULL, "
        "totp_last_step = %s WHERE id = %s",
        (step, user_id),
    )


async def clear_totp(conn: AsyncConnection, user_id: UUID) -> None:
    await conn.execute(
        "UPDATE users SET totp_secret = NULL, totp_pending_secret = NULL, totp_last_step = NULL "
        "WHERE id = %s",
        (user_id,),
    )


async def consume_totp_step(conn: AsyncConnection, user_id: UUID, step: int) -> bool:
    """Record `step` as used; False if it (or a later one) already was - a replay."""
    cur = await conn.execute(
        "UPDATE users SET totp_last_step = %s "
        "WHERE id = %s AND (totp_last_step IS NULL OR totp_last_step < %s)",
        (step, user_id, step),
    )
    return cur.rowcount == 1
