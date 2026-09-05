"""`TurnStatsStore`: per-turn duration/status persistence (M9-02).

Mirrors `app/db/threads.py` / `app/db/settings.py`: one `Protocol` with a
Postgres implementation and an in-memory one for the unit test suite.

`turn_stats` is written at every `turn_end` (completed / cancelled /
awaiting_approval) when the checkpointed state has a new final assistant
message id relative to the start of the turn. Hydration (`GET
/api/threads/{id}/messages`) attaches `{status, duration_ms}` to the
matching `MessageOut` row.

`thread_id` is `TEXT` (not `UUID`) so a turn driven over WS with a
legacy/manual id (`plain-thread`, `smoke-1`, …) still persists — the
checkpointer already keys on `thread_id` as text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from psycopg_pool import AsyncConnectionPool

TURN_STATS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS turn_stats (
    thread_id TEXT NOT NULL,
    final_message_id TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (thread_id, final_message_id)
);
"""


@dataclass(frozen=True)
class TurnStat:
    """One `turn_stats` row, decoupled from the DB row shape and the REST DTO."""

    thread_id: str
    final_message_id: str
    status: str
    duration_ms: int
    started_at: datetime


class TurnStatsStore(Protocol):
    """Everything `chat_ws.py` (write at turn end) and `chat.py` (hydrate) need."""

    async def upsert(self, stat: TurnStat) -> None: ...

    async def list_for_thread(self, thread_id: str) -> list[TurnStat]: ...

    async def delete_for_thread(self, thread_id: str) -> None: ...


class PgTurnStatsStore:
    """Raw-SQL `TurnStatsStore` against the `turn_stats` table (DDL above)."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def upsert(self, stat: TurnStat) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO turn_stats
                    (thread_id, final_message_id, status, duration_ms, started_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (thread_id, final_message_id) DO UPDATE
                    SET status = EXCLUDED.status,
                        duration_ms = EXCLUDED.duration_ms,
                        started_at = EXCLUDED.started_at
                """,
                (
                    stat.thread_id,
                    stat.final_message_id,
                    stat.status,
                    stat.duration_ms,
                    stat.started_at,
                ),
            )

    async def list_for_thread(self, thread_id: str) -> list[TurnStat]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                """
                SELECT thread_id, final_message_id, status, duration_ms, started_at
                FROM turn_stats
                WHERE thread_id = %s
                """,
                (thread_id,),
            )
            rows = await cur.fetchall()
        return [_stat_from_row(row) for row in rows]

    async def delete_for_thread(self, thread_id: str) -> None:
        async with self._pool.connection() as conn:
            await conn.execute("DELETE FROM turn_stats WHERE thread_id = %s", (thread_id,))


def _stat_from_row(row: dict) -> TurnStat:
    return TurnStat(
        thread_id=row["thread_id"],
        final_message_id=row["final_message_id"],
        status=row["status"],
        duration_ms=row["duration_ms"],
        started_at=row["started_at"],
    )


class InMemoryTurnStatsStore:
    """Dict-backed `TurnStatsStore` for the unit test suite (no real Postgres)."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], TurnStat] = {}

    async def upsert(self, stat: TurnStat) -> None:
        self._rows[(stat.thread_id, stat.final_message_id)] = stat

    async def list_for_thread(self, thread_id: str) -> list[TurnStat]:
        return [s for (tid, _mid), s in self._rows.items() if tid == thread_id]

    async def delete_for_thread(self, thread_id: str) -> None:
        for key in [k for k in self._rows if k[0] == thread_id]:
            del self._rows[key]
