"""App rows in search (M22-07, docs/APP_TEMPLATES.md §5.2, docs/PLATFORM.md §5 "File search").

An app's `homeai.index` names the tables whose rows search should find:
`[{table, title, text?, fields?}]`, checked against `schema.sql` when the
package is validated (`manifest.index_diagnostics`). Each row is rendered as
its title, then `name: value` for each field, then the text columns, and
chunked like a text file into `app_row_chunks` (migration 0022), next to the
files' chunks; `fileindex.search` ranks both together.

`FileIndex` drives this: an instance's write (`AppData`'s `db_changed`), a
build and an uninstall queue it, and the periodic scan reconciles every
instance. `reconcile` compares each declared table's `(rowid, _updated_at)`
with what was indexed: new and changed rows are rendered again, rows that
are gone are dropped, and a changed declaration re-indexes the instance from
scratch. An instance that's uninstalled, or whose app or space is archived,
loses its rows.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import anyio.to_thread
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core import appdb, appschema, fileindex, manifest
from app.core.storage import SpaceStorage

logger = logging.getLogger(__name__)

MAX_ROWS_PER_TABLE = 20_000
MAX_ROW_CHARS = 64 * 1024
MAX_TITLE_CHARS = 200
FETCH_BATCH = 500
_ROWID = "__homeai_rowid"

# Live instances: installed, app and space not archived, with the tracked version's manifest.
_LIVE = """
SELECT i.id, i.space_id, s.gid, v.manifest
FROM app_instances i
JOIN apps a ON a.id = i.app_id AND a.archived_at IS NULL
JOIN spaces s ON s.id = i.space_id AND s.archived_at IS NULL
LEFT JOIN app_versions v ON v.id = i.version_id
    OR (i.version_id IS NULL AND v.app_id = i.app_id AND v.kind = 'working')
WHERE i.uninstalled_at IS NULL
"""


def declaration_hash(entries: list[dict[str, Any]]) -> str:
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def _text(value: Any) -> str:
    if value is None or isinstance(value, bytes):
        return ""
    return str(value).strip()


def render(entry: dict[str, Any], row: dict[str, Any]) -> tuple[str, str]:
    """(title, text) of one row: the title, `name: value` per field, then the text columns."""
    title = _text(row.get(entry["title"]))[:MAX_TITLE_CHARS] or f"{entry['table']} #{row[_ROWID]}"
    lines = [title]
    for name in entry.get("fields") or []:
        value = _text(row.get(name))
        if value:
            lines.append(f"{name}: {value}")
    for name in entry.get("text") or []:
        value = _text(row.get(name))
        if value:
            lines.append(value)
    return title, "\n".join(lines)[:MAX_ROW_CHARS]


@dataclass(frozen=True)
class RowDoc:
    tbl: str
    row_id: int
    updated_at: str | None
    title: str
    chunks: list[fileindex.Chunk]


@dataclass(frozen=True)
class Snapshot:
    # (table, rowid) -> _updated_at of every row of every declared table
    rows: dict[tuple[str, int], str | None]
    # the new and changed ones, rendered
    docs: list[RowDoc]


def _columns(con: Any, table: str) -> set[str] | None:
    q = appschema.quote(table)
    exists = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if exists is None:
        return None
    return {r[1] for r in con.execute(f"PRAGMA table_info({q})")}


def read_instance(
    storage: SpaceStorage,
    space_id: UUID,
    gid: int,
    instance_id: UUID,
    entries: list[dict[str, Any]],
    known: dict[tuple[str, int], str | None],
) -> Snapshot | None:
    """Blocking: the declared tables' rows, rendering those that aren't in `known` as they are.

    None if the instance's database can't be read (so nothing is dropped). A declared
    table or column the database doesn't have (yet) is skipped.
    """
    try:
        fd = storage.open_instance(space_id, gid, instance_id, create=False)
    except FileNotFoundError:
        return Snapshot({}, [])
    except OSError as exc:
        logger.warning("app index: instance %s: %r", instance_id, exc)
        return None
    rows: dict[tuple[str, int], str | None] = {}
    docs: list[RowDoc] = []
    try:
        try:
            os.stat(appdb.DB_NAME, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return Snapshot({}, [])
        with appdb.connect(fd) as con:
            for entry in entries:
                table = entry["table"]
                columns = _columns(con, table)
                if columns is None:
                    continue
                q = appschema.quote(table)
                stamp = '"_updated_at"' if "_updated_at" in columns else "NULL"
                found = con.execute(
                    f"SELECT rowid, {stamp} FROM {q} ORDER BY rowid LIMIT {MAX_ROWS_PER_TABLE}"
                ).fetchall()
                changed = []
                for row_id, updated_at in found:
                    rows[(table, row_id)] = updated_at
                    if (table, row_id) not in known or known[(table, row_id)] != updated_at:
                        changed.append(row_id)
                wanted = [entry["title"], *(entry.get("fields") or []), *(entry.get("text") or [])]
                select = ", ".join(
                    appschema.quote(c) for c in dict.fromkeys(wanted) if c in columns
                )
                for start in range(0, len(changed), FETCH_BATCH):
                    batch = changed[start : start + FETCH_BATCH]
                    marks = ", ".join("?" * len(batch))
                    cur = con.execute(
                        f"SELECT rowid AS {_ROWID}, {stamp} AS _stamp"
                        + (f", {select}" if select else "")
                        + f" FROM {q} WHERE rowid IN ({marks})",
                        batch,
                    )
                    names = [d[0] for d in cur.description]
                    for values in cur.fetchall():
                        row = dict(zip(names, values, strict=True))
                        title, text = render(entry, row)
                        docs.append(
                            RowDoc(
                                table,
                                row[_ROWID],
                                row["_stamp"],
                                title,
                                fileindex.chunk_text(text, markdown=False),
                            )
                        )
    except Exception as exc:  # noqa: BLE001 - one broken database never stops the indexer
        logger.warning("app index: instance %s unreadable: %r", instance_id, exc)
        return None
    finally:
        os.close(fd)
    return Snapshot(rows, docs)


async def apps_of(conn: AsyncConnection, instance_ids: list[UUID]) -> dict[UUID, dict[str, Any]]:
    """{instance id: {slug, name}} of the instances' apps."""
    if not instance_ids:
        return {}
    cur = await conn.execute(
        "SELECT i.id, a.slug, a.name FROM app_instances i JOIN apps a ON a.id = i.app_id "
        "WHERE i.id = ANY(%s)",
        (instance_ids,),
    )
    return {r["id"]: {"slug": r["slug"], "name": r["name"]} for r in await cur.fetchall()}


async def _drop(conn: AsyncConnection, instance_id: UUID) -> None:
    await conn.execute("DELETE FROM indexed_instances WHERE instance_id = %s", (instance_id,))


async def drop_dead(conn: AsyncConnection) -> None:
    """Rows of instances that aren't live any more (uninstalled, archived app or space)."""
    await conn.execute(
        f"DELETE FROM indexed_instances WHERE instance_id NOT IN (SELECT id FROM ({_LIVE}) live)"
    )


async def instances_to_scan(conn: AsyncConnection) -> list[UUID]:
    """Live instances that declare an index, and any that have indexed rows."""
    cur = await conn.execute(
        f"SELECT id FROM ({_LIVE}) live "
        "WHERE jsonb_array_length(COALESCE(manifest -> 'homeai' -> 'index', '[]'::jsonb)) > 0 "
        "UNION SELECT instance_id FROM indexed_instances"
    )
    return [r["id"] for r in await cur.fetchall()]


async def reconcile(pool: AsyncConnectionPool, storage: SpaceStorage, instance_id: UUID) -> None:
    """Bring one instance's indexed rows up to date with its database and declaration."""
    async with pool.connection() as conn:
        cur = await conn.execute(_LIVE + " AND i.id = %s", (instance_id,))
        live = await cur.fetchone()
        entries = manifest.homeai_index(live["manifest"]) if live else []
        if not entries:
            await _drop(conn, instance_id)
            return
        digest = declaration_hash(entries)
        cur = await conn.execute(
            "SELECT declaration_hash FROM indexed_instances WHERE instance_id = %s", (instance_id,)
        )
        stored = await cur.fetchone()
        if stored is not None and stored["declaration_hash"] != digest:
            await _drop(conn, instance_id)
            stored = None
        known: dict[tuple[str, int], str | None] = {}
        if stored is not None:
            cur = await conn.execute(
                "SELECT tbl, row_id, row_updated_at FROM indexed_app_rows WHERE instance_id = %s",
                (instance_id,),
            )
            known = {(r["tbl"], r["row_id"]): r["row_updated_at"] for r in await cur.fetchall()}
    snap = await anyio.to_thread.run_sync(
        read_instance, storage, live["space_id"], live["gid"], instance_id, entries, known
    )
    if snap is None:
        return
    gone = [key for key in known if key not in snap.rows]
    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO indexed_instances (instance_id, declaration_hash) VALUES (%s, %s) "
            "ON CONFLICT (instance_id) DO UPDATE SET declaration_hash = EXCLUDED.declaration_hash, "
            "indexed_at = now()",
            (instance_id, digest),
        )
        if gone:
            async with conn.cursor() as cur:
                await cur.executemany(
                    "DELETE FROM indexed_app_rows WHERE instance_id = %s AND tbl = %s "
                    "AND row_id = %s",
                    [(instance_id, tbl, row_id) for tbl, row_id in gone],
                )
        for doc in snap.docs:
            await _store(conn, instance_id, live["space_id"], doc)


async def _store(conn: AsyncConnection, instance_id: UUID, space_id: UUID, doc: RowDoc) -> None:
    key = (instance_id, doc.tbl, doc.row_id)
    cur = await conn.execute(
        "SELECT content_hash, model, embedding::text AS embedding FROM app_row_chunks "
        "WHERE instance_id = %s AND tbl = %s AND row_id = %s AND model IS NOT NULL",
        key,
    )
    reuse = {r["content_hash"]: (r["model"], r["embedding"]) for r in await cur.fetchall()}
    await conn.execute(
        "INSERT INTO indexed_app_rows (instance_id, space_id, tbl, row_id, row_updated_at, title) "
        "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (instance_id, tbl, row_id) DO UPDATE SET "
        "row_updated_at = EXCLUDED.row_updated_at, title = EXCLUDED.title, indexed_at = now()",
        (*key[:1], space_id, *key[1:], doc.updated_at, doc.title),
    )
    await conn.execute(
        "DELETE FROM app_row_chunks WHERE instance_id = %s AND tbl = %s AND row_id = %s", key
    )
    rows = []
    for chunk_no, chunk in enumerate(doc.chunks):
        model, embedding = reuse.get(chunk.content_hash, (None, None))
        rows.append((
            instance_id, space_id, doc.tbl, doc.row_id, chunk_no,
            chunk.content, chunk.content_hash, model, embedding,
        ))  # fmt: skip
    if rows:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO app_row_chunks (instance_id, space_id, tbl, row_id, chunk_no, "
                "content, content_hash, model, embedding) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)",
                rows,
            )
