"""The audit log (docs/PLATFORM.md §4 "Audit log", `audit_events`).

Any part of the platform records what it did on someone's behalf with
`record(conn, principal, kind, ...)`: who (the verified principal - the user
in person, or their agent with its chat thread), what (`kind`, a dotted
name such as `app_data.write`; `summary`, one human-readable line; `detail`,
JSON), and where (`space_id`, `target_type` + `target_id`). Callers record
in the same Postgres transaction as their own change, or - for app data,
which lives in SQLite - just before committing it, so nothing is changed
without its event.

The table is append-only (triggers refuse UPDATE / DELETE / TRUNCATE).
`detail` is capped at `MAX_DETAIL_BYTES`: long strings are cut, and a
detail that still doesn't fit is replaced by a note saying so.

Reading (`list_events`): members of a space see its events; admins see
everything, including events with no space. Agents read with their user's
rights; nobody writes except through `record`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.core import spaces
from app.core.errors import Forbidden
from app.core.principal import Principal

Row = dict[str, Any]

KIND_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
MAX_SUMMARY = 500
MAX_DETAIL_BYTES = 16 * 1024
MAX_STRING = 2000
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

_COLUMNS = (
    "id, at, kind, actor_kind, actor_user_id, actor_name, session_id, thread_id, space_id, "
    "target_type, target_id, summary, detail"
)


@dataclass(frozen=True)
class Actor:
    """Who an event is attributed to, from a verified principal (or the platform itself)."""

    kind: str
    user_id: UUID | None = None
    name: str | None = None
    session_id: UUID | None = None
    thread_id: str | None = None

    @classmethod
    def of(cls, principal: Principal | None) -> Actor:
        if principal is None:
            return cls("system")
        return cls(
            "agent" if principal.is_agent else "user",
            principal.user_id,
            principal.display_name or principal.username,
            principal.session_id,
            principal.thread_id,
        )


def _trim(value: Any, limit: int) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"… ({len(value)} chars)"
    if isinstance(value, dict):
        return {str(k): _trim(v, limit) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_trim(v, limit) for v in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return str(value)


def capped_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    """`detail` as stored: JSON-safe, long strings cut, at most `MAX_DETAIL_BYTES`."""
    out = _trim(detail or {}, MAX_STRING)
    for limit in (500, 100):
        if len(json.dumps(out, default=str)) <= MAX_DETAIL_BYTES:
            return out
        out = _trim(out, limit)
    if len(json.dumps(out, default=str)) <= MAX_DETAIL_BYTES:
        return out
    return {"truncated": f"detail was larger than {MAX_DETAIL_BYTES // 1024} KiB"}


async def record(
    conn: AsyncConnection,
    actor: Principal | Actor | None,
    kind: str,
    summary: str,
    *,
    space_id: UUID | None = None,
    target_type: str | None = None,
    target_id: UUID | str | None = None,
    detail: dict[str, Any] | None = None,
) -> int:
    """Append one event; returns its id. Raises (and so fails the caller) if it can't."""
    if not KIND_RE.fullmatch(kind):
        raise ValueError(f"bad audit kind: {kind!r}")
    who = actor if isinstance(actor, Actor) else Actor.of(actor)
    cur = await conn.execute(
        "INSERT INTO audit_events (kind, actor_kind, actor_user_id, actor_name, session_id, "
        "thread_id, space_id, target_type, target_id, summary, detail) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            kind, who.kind, who.user_id, who.name, who.session_id, who.thread_id, space_id,
            target_type, None if target_id is None else str(target_id),
            summary[:MAX_SUMMARY], Jsonb(capped_detail(detail)),
        ),
    )  # fmt: skip
    return (await cur.fetchone())["id"]


async def list_events(
    conn: AsyncConnection,
    principal: Principal,
    *,
    space_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    actor_user_id: UUID | None = None,
    kind: str | None = None,
    before: int | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[Row]:
    """Newest first. Without `space_id`, only admins (in person or via their agent) may ask."""
    if space_id is not None:
        await spaces.authorize_space(conn, principal, space_id, "read")
    elif principal.role != "admin" or principal.space_scope is not None:
        raise Forbidden("admin_required")
    where, args = [], {}
    for column, value in (
        ("space_id", space_id),
        ("target_type", target_type),
        ("target_id", target_id),
        ("actor_user_id", actor_user_id),
    ):
        if value is not None:
            where.append(f"{column} = %({column})s")
            args[column] = value
    if kind is not None:
        # `app_data` matches `app_data.write`; a full kind matches only itself.
        where.append("(kind = %(kind)s OR kind LIKE %(kind_prefix)s)")
        args["kind"], args["kind_prefix"] = kind, kind.replace("_", r"\_") + ".%"
    if before is not None:
        where.append("id < %(before)s")
        args["before"] = before
    sql = f"SELECT {_COLUMNS} FROM audit_events"
    if where:
        sql += " WHERE " + " AND ".join(where)
    cur = await conn.execute(sql + " ORDER BY id DESC LIMIT %(limit)s", {**args, "limit": limit})
    return await cur.fetchall()
