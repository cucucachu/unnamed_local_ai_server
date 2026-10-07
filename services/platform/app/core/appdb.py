"""One app instance's SQLite database (docs/PLATFORM.md §7 "Data", D10-D12).

    apps/<instance_id>/data.sqlite           the live database (WAL); only the platform opens it
    apps/<instance_id>/snapshots/<stamp>-<migration_id>.sqlite   taken before each migration
    apps/<instance_id>/ro/data.sqlite        a published copy (0444) for exec, never the live file

The instance dir is opened by fd (`SpaceStorage.open_instance`: each
component `O_NOFOLLOW` from its parent, fixed to root:<gid> 2750) and SQLite
is handed `/proc/self/fd/<fd>/data.sqlite`. SQLite resolves that to the
dir's path and later opens `-wal`/`-shm` by path, so it relies on `apps/`
and below being writable by root alone (`storage.APPS_MODE`); a database or
side file that is anything but a regular file is refused.

RPC statements run on a connection that has only this file open (plus
read-only `ATTACH`es the platform adds for granted exports), under an
allow-list authorizer: reads may SELECT from `main` and granted export
schemas (plus a few introspection pragmas on `main`/`temp`), writes may
INSERT / UPDATE / DELETE rows of the app's own tables on `main` only. DDL,
ATTACH / DETACH (and so VACUUM, which SQLite authorizes as an ATTACH),
transaction control, other pragmas and `load_extension` are refused, and
`SQLITE_LIMIT_ATTACHED` is 0 except for the granted ATTACHes (and the
`VACUUM INTO` bump in `publish()`). Every op has a wall-clock budget
(progress handler), and results are capped in rows and bytes.

Attribution (`appschema.STAMP_COLUMNS`): before a write op, `stamp()` puts
the caller in `temp._homeai_actor` and adds TEMP triggers on every table
that has the columns, setting `_created_*` and `_updated_*` after each
INSERT and `_updated_*` after each UPDATE. Only those triggers may UPDATE
the columns or read the actor table (the authorizer checks the trigger
name it is given), and a value an INSERT supplies is overwritten, so
neither app code nor an agent's SQL can attribute a row to someone else.

Standard library only.
"""

from __future__ import annotations

import base64
import math
import os
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core import appschema

DB_NAME = "data.sqlite"
RO_DIR = "ro"
SNAPSHOT_DIR = "snapshots"
SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
KEEP_SNAPSHOTS = 10

OP_TIMEOUT_S = 5.0
BUSY_TIMEOUT_MS = 5000
MAX_ROWS = 10_000
MAX_RESULT_BYTES = 8 * 1024 * 1024
MAX_VALUE_BYTES = 16 * 1024 * 1024
MAX_SQL_BYTES = 100 * 1024
MAX_STATEMENTS = 100

_OPEN_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_OPEN_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC

_READ_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
)
_WRITE_ACTIONS = _READ_ACTIONS | {
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
}
_ROW_WRITES = frozenset({sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE})
# Query-only pragmas: none of them takes a value that changes anything.
_PRAGMAS = frozenset(
    {"table_info", "table_xinfo", "table_list", "index_list", "index_info", "index_xinfo",
     "foreign_key_list"}
)  # fmt: skip
_DENIED_FUNCTIONS = frozenset({"load_extension"})
# Checked before the authorizer: SQLite never consults it for a bare `REINDEX`.
_STATEMENT_KEYWORDS = frozenset(
    {"select", "values", "with", "insert", "replace", "update", "delete", "pragma", "explain"}
)
_OTHER_STATEMENTS = frozenset(
    {"alter", "analyze", "attach", "begin", "commit", "create", "detach", "drop", "end",
     "reindex", "release", "rollback", "savepoint", "vacuum"}
)  # fmt: skip


STAMP_TRIGGER_PREFIX = "_homeai_stamp_"
ACTOR_TABLE = "_homeai_actor"
_STAMPS = frozenset(appschema.STAMP_COLUMNS)


class InstanceStorageError(Exception):
    """The instance dir holds something other than regular database files."""


class SqlError(Exception):
    """A statement failed; `code` is the API's detail, `index` the statement in a batch."""

    def __init__(self, code: str, message: str, index: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.index = index


# --- opening -------------------------------------------------------------------------


def _check_regular(dir_fd: int, name: str) -> bool:
    """Whether `name` exists; raises if it isn't a regular file."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(st.st_mode):
        raise InstanceStorageError(f"{name} is not a regular file")
    return True


def _path(dir_fd: int, name: str) -> str:
    return f"/proc/self/fd/{dir_fd}/{name}"


def _set_attached(con: sqlite3.Connection, n: int) -> None:
    con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, n)


def _attached_cap(con: sqlite3.Connection) -> int:
    """The compile-time maximum number of ATTACHed databases (typically 10)."""
    con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 125)
    return con.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED)


def _limit(con: sqlite3.Connection) -> None:
    con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
    con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_BYTES)


@contextmanager
def connect(inst_fd: int) -> Iterator[sqlite3.Connection]:
    """The instance's live database (created, WAL, if missing), in autocommit mode."""
    for suffix in SIDE_SUFFIXES:
        _check_regular(inst_fd, DB_NAME + suffix)
    if not _check_regular(inst_fd, DB_NAME):
        try:
            os.close(
                os.open(
                    DB_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600, dir_fd=inst_fd,
                )
            )  # fmt: skip
        except FileExistsError:
            _check_regular(inst_fd, DB_NAME)
    con = sqlite3.connect(
        f"file:{_path(inst_fd, DB_NAME)}?mode=rw", uri=True, isolation_level=None,
        timeout=BUSY_TIMEOUT_MS / 1000,
    )  # fmt: skip
    try:
        if con.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
            con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA trusted_schema = OFF")
        _set_attached(con, 0)
        _limit(con)
        yield con
    finally:
        _ATTACH_STATE.pop(id(con), None)
        con.close()


# --- values --------------------------------------------------------------------------

Params = Sequence[Any] | Mapping[str, Any] | None


def _bind_value(value: Any) -> Any:
    if value is None or isinstance(value, str | float):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise SqlError("invalid_params", f"integer out of range: {value}")
        return value
    raise SqlError("invalid_params", "parameters must be strings, numbers, booleans or null")


def bind(params: Params) -> Sequence[Any] | dict[str, Any]:
    """expo-sqlite-shaped params: a list for `?`, or an object whose keys may carry `:`/`$`/`@`."""
    if params is None:
        return ()
    if isinstance(params, Mapping):
        return {str(k).lstrip(":$@"): _bind_value(v) for k, v in params.items()}
    return tuple(_bind_value(v) for v in params)


def _out(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$blob": base64.b64encode(value).decode()}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _size(value: Any) -> int:
    return len(value) if isinstance(value, str | bytes) else 8


# --- running statements ---------------------------------------------------------------


# --- granted exports (M14-04) -------------------------------------------------------


@dataclass(frozen=True)
class AttachGrant:
    """One exporter instance the platform ATTACHes onto a reader connection."""

    inst_fd: int
    instance_id: UUID
    app_slug: str
    export_name: str
    tables: tuple[str, ...]
    space_path: str


@dataclass(frozen=True)
class _AttachState:
    schemas: frozenset[str]
    tables: dict[str, frozenset[str]]
    views: frozenset[str]


_ATTACH_STATE: dict[int, _AttachState] = {}


def attach_schema_name(instance_id: UUID) -> str:
    return "exp" + instance_id.hex


def merged_view_name(app_slug: str, export_name: str, table: str, tables: Sequence[str]) -> str:
    """Stable TEMP VIEW name: `{app}_{export}` when the export has one table."""
    if len(tables) == 1:
        return f"{app_slug}_{export_name}"
    return f"{app_slug}_{export_name}_{table}"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def attach_exports(con: sqlite3.Connection, grants: Sequence[AttachGrant]) -> None:
    """ATTACH granted exporter DBs read-only and create merged TEMP views.

    Missing or unreadable exporters are skipped. `SQLITE_LIMIT_ATTACHED` is
    raised only far enough for the databases that actually attached.
    """
    if not grants:
        _set_attached(con, 0)
        return
    cap = _attached_cap(con)
    attached: list[tuple[AttachGrant, str]] = []
    for grant in grants:
        if len(attached) >= cap:
            break
        try:
            for suffix in SIDE_SUFFIXES:
                _check_regular(grant.inst_fd, DB_NAME + suffix)
            if not _check_regular(grant.inst_fd, DB_NAME):
                continue
        except InstanceStorageError:
            continue
        schema = attach_schema_name(grant.instance_id)
        uri = f"file:{_path(grant.inst_fd, DB_NAME)}?mode=ro"
        try:
            con.execute(f"ATTACH DATABASE ? AS {appschema.quote(schema)}", (uri,))
        except sqlite3.Error:
            continue
        attached.append((grant, schema))
    _set_attached(con, len(attached))
    if not attached:
        return
    groups: dict[tuple[str, str], list[tuple[AttachGrant, str]]] = {}
    tables_by_schema: dict[str, frozenset[str]] = {}
    schemas: set[str] = set()
    for grant, schema in attached:
        groups.setdefault((grant.app_slug, grant.export_name), []).append((grant, schema))
        schemas.add(schema)
        tables_by_schema[schema] = frozenset(grant.tables)
    views: set[str] = set()
    for (slug, export_name), copies in groups.items():
        tables = copies[0][0].tables
        for table in tables:
            view = merged_view_name(slug, export_name, table, tables)
            selects = [
                (
                    f"SELECT *, {_sql_str(grant.space_path)} AS {appschema.quote('_space')} "
                    f"FROM {appschema.quote(schema)}.{appschema.quote(table)}"
                )
                for grant, schema in copies
            ]
            sql = f"CREATE TEMP VIEW {appschema.quote(view)} AS " + " UNION ALL ".join(selects)
            try:
                con.execute(sql)
            except sqlite3.Error:
                continue
            views.add(view)
    _ATTACH_STATE[id(con)] = _AttachState(
        schemas=frozenset(schemas), tables=tables_by_schema, views=frozenset(views)
    )


def _authorizer(write: bool, scope: _AttachState | None = None, denied: list[str] | None = None):
    """`denied` gets a reason when a refusal deserves a more specific message."""
    allowed = _WRITE_ACTIONS if write else _READ_ACTIONS
    extra_tables = scope.tables if scope is not None else {}
    views = scope.views if scope is not None else frozenset()

    def check(action, arg1, arg2, dbname, source):
        db = (dbname or "main").lower()
        stamping = (source or "").startswith(STAMP_TRIGGER_PREFIX)
        if action == sqlite3.SQLITE_UPDATE and (arg2 or "").lower() in _STAMPS and not stamping:
            if denied is not None:
                denied.append(
                    f"{arg2} is set by the platform: {', '.join(appschema.STAMP_COLUMNS)} "
                    "can be read but not written"
                )
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and db == "temp" and arg1 == ACTOR_TABLE:
            return sqlite3.SQLITE_OK if stamping else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA:
            ok = (arg1 or "").lower() in _PRAGMAS and db in ("main", "temp")
            return sqlite3.SQLITE_OK if ok else sqlite3.SQLITE_DENY
        if action not in allowed:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _DENIED_FUNCTIONS:
            return sqlite3.SQLITE_DENY
        if action in _ROW_WRITES:
            if db != "main" or (arg1 or "").lower().startswith("sqlite_"):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table = arg1 or ""
            if db == "main":
                return sqlite3.SQLITE_OK
            if db == "temp" and table in views:
                return sqlite3.SQLITE_OK
            if table in extra_tables.get(db, ()):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return check


def _first_keyword(sql: str) -> str:
    for token in appschema._TOKEN.findall(sql):
        if not appschema._is_noise(token):
            return token.lower()
    return ""


def _error(
    exc: sqlite3.Error, index: int | None, write: bool, reason: str | None = None
) -> SqlError:
    message = str(exc)
    if isinstance(exc, sqlite3.DatabaseError) and "not authorized" in message:
        if reason:
            return SqlError("sql_not_allowed", f"statement not allowed: {reason}", index)
        allowed = (
            "SELECT, INSERT, UPDATE and DELETE on the app's tables"
            if write
            else "SELECT (this is a read-only operation)"
        )
        return SqlError("sql_not_allowed", f"statement not allowed: only {allowed}", index)
    if message == "interrupted":
        return SqlError("sql_timeout", f"statement ran longer than {OP_TIMEOUT_S:g} s", index)
    if isinstance(exc, sqlite3.OperationalError) and "locked" in message:
        return SqlError("db_busy", message, index)
    return SqlError("sql_error", message, index)


@dataclass
class _Budget:
    rows: int = 0
    bytes: int = 0


@dataclass
class Result:
    rows: list[dict[str, Any]] = field(default_factory=list)
    changes: int = 0
    last_insert_row_id: int = 0


class Session:
    """RPC statements on one connection: one authorizer, one deadline, one result budget."""

    def __init__(self, con: sqlite3.Connection, *, write: bool) -> None:
        _limit(con)
        self.con = con
        self.write = write
        self.budget = _Budget()
        self._deadline = time.monotonic() + OP_TIMEOUT_S
        self._scope = _ATTACH_STATE.get(id(con))
        self._denied: list[str] = []

    @contextmanager
    def _guarded(self) -> Iterator[None]:
        self._denied.clear()
        self.con.set_progress_handler(lambda: int(time.monotonic() > self._deadline), 1000)
        self.con.set_authorizer(_authorizer(self.write, self._scope, self._denied))
        try:
            yield
        finally:
            self.con.set_authorizer(None)
            self.con.set_progress_handler(None, 0)

    def execute(self, sql: str, params: Params, *, index: int | None = None,
                limit: int | None = None) -> Result:  # fmt: skip
        """One statement; with `limit`, stop after that many rows."""
        if len(sql.encode()) > MAX_SQL_BYTES:
            raise SqlError("sql_error", f"statement is longer than {MAX_SQL_BYTES // 1024} KiB")
        keyword = _first_keyword(sql)
        if keyword not in _STATEMENT_KEYWORDS:
            if keyword in _OTHER_STATEMENTS:
                raise _error(sqlite3.DatabaseError("not authorized"), index, self.write)
            raise SqlError("sql_error", f'near "{keyword}": syntax error', index)
        values = bind(params)
        before = self.con.total_changes
        out = Result()
        try:
            with self._guarded():
                cur = self.con.execute(sql, values)
                names = [d[0] for d in cur.description] if cur.description else None
                while names is not None and (limit is None or len(out.rows) < limit):
                    row = cur.fetchone()
                    if row is None:
                        break
                    self.budget.rows += 1
                    self.budget.bytes += sum(_size(v) for v in row)
                    if self.budget.rows > MAX_ROWS:
                        raise SqlError("too_many_rows", f"more than {MAX_ROWS} rows", index)
                    if self.budget.bytes > MAX_RESULT_BYTES:
                        raise SqlError(
                            "result_too_large",
                            f"result is larger than {MAX_RESULT_BYTES // 1024 // 1024} MiB",
                            index,
                        )
                    out.rows.append({n: _out(v) for n, v in zip(names, row, strict=True)})
                cur.close()
        except sqlite3.Warning as exc:
            raise SqlError("sql_error", str(exc), index) from exc
        except sqlite3.Error as exc:
            raise _error(exc, index, self.write, self._denied[0] if self._denied else None) from exc
        if self.con.total_changes != before:
            # The statement's own rows: total_changes also counts the stamp triggers'.
            out.changes = self.con.execute("SELECT changes()").fetchone()[0]
        out.last_insert_row_id = cur.lastrowid or 0
        return out

    @contextmanager
    def transaction(self, before_commit: BeforeCommit | None = None) -> Iterator[dict]:
        """Yields a dict for the result; `before_commit(result)` runs last, inside the
        transaction, so if it raises nothing is committed."""
        self.con.execute("BEGIN IMMEDIATE")
        result: dict = {}
        try:
            yield result
            if before_commit is not None:
                before_commit(result)
        except BaseException:
            if self.con.in_transaction:
                self.con.execute("ROLLBACK")
            raise
        self.con.execute("COMMIT")


# Called with a write's result just before it commits (the audit log, M19).
BeforeCommit = Callable[[dict], None]


def run_result(result: Result) -> dict[str, int]:
    return {"changes": result.changes, "lastInsertRowId": result.last_insert_row_id}


def get_all(con: sqlite3.Connection, sql: str, params: Params) -> list[dict[str, Any]]:
    return Session(con, write=False).execute(sql, params).rows


def get_first(con: sqlite3.Connection, sql: str, params: Params) -> dict[str, Any] | None:
    rows = Session(con, write=False).execute(sql, params, limit=1).rows
    return rows[0] if rows else None


def run(
    con: sqlite3.Connection, sql: str, params: Params, before_commit: BeforeCommit | None = None
) -> dict[str, int]:
    session = Session(con, write=True)
    with session.transaction(before_commit) as out:
        out.update(run_result(session.execute(sql, params)))
    return out


def transaction(
    con: sqlite3.Connection,
    statements: Sequence[tuple[str, Params]],
    before_commit: BeforeCommit | None = None,
) -> list[dict]:
    """Every statement or none; a failure names its `index`."""
    if len(statements) > MAX_STATEMENTS:
        raise SqlError("sql_error", f"more than {MAX_STATEMENTS} statements")
    session = Session(con, write=True)
    with session.transaction(before_commit) as out:
        out["results"] = [
            run_result(session.execute(sql, params, index=i))
            for i, (sql, params) in enumerate(statements)
        ]
    return out["results"]


def split_statements(sql: str) -> list[str]:
    """`sql` cut at each `;` that ends a complete statement (not inside strings or triggers)."""
    out, start = [], 0
    for i, ch in enumerate(sql):
        if ch == ";" and sqlite3.complete_statement(sql[start : i + 1]):
            out.append(sql[start : i + 1])
            start = i + 1
    if sql[start:].strip():
        out.append(sql[start:])
    return [s for s in out if _has_code(s)]


def _has_code(stmt: str) -> bool:
    return any(not appschema._is_noise(t) and t != ";" for t in appschema._TOKEN.findall(stmt))


def action(
    con: sqlite3.Connection,
    sql: str,
    params: Mapping[str, Any],
    before_commit: BeforeCommit | None = None,
) -> dict[str, Any]:
    """An `actions/<name>.sql` file: its statements in one transaction, `:named` params shared.

    Returns the rows of the last statement that produced any (e.g. `RETURNING`).
    """
    statements = split_statements(sql)
    if not statements:
        raise SqlError("sql_error", "the action has no statements")
    if len(statements) > MAX_STATEMENTS:
        raise SqlError("sql_error", f"the action has more than {MAX_STATEMENTS} statements")
    session = Session(con, write=True)
    changes, last_id, rows = 0, 0, []
    with session.transaction(before_commit) as out:
        for i, stmt in enumerate(statements):
            result = session.execute(stmt, dict(params), index=i)
            changes += result.changes
            last_id = result.last_insert_row_id
            if result.rows:
                rows = result.rows
        out.update({"changes": changes, "lastInsertRowId": last_id, "rows": rows})
    return out


# --- attribution ------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def stamp(con: sqlite3.Connection, user_id: str) -> None:
    """Attribute this connection's next writes to `user_id` (see the module docstring)."""
    con.execute(f"CREATE TEMP TABLE IF NOT EXISTS {ACTOR_TABLE} (user_id TEXT, at TEXT)")
    con.execute(f"DELETE FROM temp.{ACTOR_TABLE}")
    con.execute(f"INSERT INTO temp.{ACTOR_TABLE} VALUES (?, ?)", (user_id, _now()))
    for (name,) in con.execute(
        "SELECT name FROM temp.sqlite_master WHERE type = 'trigger' AND name LIKE ? ESCAPE '\\'",
        (STAMP_TRIGGER_PREFIX.replace("_", "\\_") + "%",),
    ).fetchall():
        con.execute(f"DROP TRIGGER temp.{appschema.quote(name)}")
    tables = con.execute(
        "SELECT name, wr FROM pragma_table_list WHERE schema = 'main' AND type = 'table' "
        "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\'"
    ).fetchall()
    actor = f"(SELECT user_id FROM {ACTOR_TABLE})", f"(SELECT at FROM {ACTOR_TABLE})"
    for i, (name, without_rowid) in enumerate(tables):
        info = con.execute(f"PRAGMA main.table_xinfo({appschema.quote(name)})").fetchall()
        if not _STAMPS <= {r[1].lower() for r in info}:
            continue
        if without_rowid:
            keys = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
            match = " AND ".join(f"{appschema.quote(k)} IS NEW.{appschema.quote(k)}" for k in keys)
        else:
            match = "rowid = NEW.rowid"
        q = appschema.quote(name)
        updated = f"_updated_by = {actor[0]}, _updated_at = {actor[1]}"
        con.execute(
            f"CREATE TEMP TRIGGER {STAMP_TRIGGER_PREFIX}i{i} AFTER INSERT ON main.{q} BEGIN "
            f"UPDATE {q} SET _created_by = {actor[0]}, _created_at = {actor[1]}, {updated} "
            f"WHERE {match}; END"
        )
        con.execute(
            f"CREATE TEMP TRIGGER {STAMP_TRIGGER_PREFIX}u{i} AFTER UPDATE ON main.{q} BEGIN "
            f"UPDATE {q} SET {updated} WHERE {match}; END"
        )


# --- migrations -------------------------------------------------------------------------


def has_tables(con: sqlite3.Connection) -> bool:
    return (
        con.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        > 0
    )


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _seal(dir_fd: int, name: str, mode: int) -> None:
    fd = os.open(name, _OPEN_FILE, dir_fd=dir_fd)
    try:
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)


def snapshot(con: sqlite3.Connection, inst_fd: int, tag: str) -> str:
    """A backup-API copy in `snapshots/`; returns its name. Only the newest ten are kept."""
    snap_fd = os.open(SNAPSHOT_DIR, _OPEN_DIR, dir_fd=inst_fd)
    try:
        name = f"{_stamp()}-{tag}.sqlite"
        tmp = f".{name}.tmp"
        for stale in (tmp, tmp + "-journal"):
            try:
                os.unlink(stale, dir_fd=snap_fd)
            except FileNotFoundError:
                pass
        target = sqlite3.connect(_path(snap_fd, tmp))
        try:
            con.backup(target)
        finally:
            target.close()
        _seal(snap_fd, tmp, 0o600)
        os.rename(tmp, name, src_dir_fd=snap_fd, dst_dir_fd=snap_fd)
        os.fsync(snap_fd)
        kept = sorted(n for n in os.listdir(snap_fd) if n.endswith(".sqlite") and n[0] != ".")
        for old in kept[:-KEEP_SNAPSHOTS]:
            os.unlink(old, dir_fd=snap_fd)
        return name
    finally:
        os.close(snap_fd)


def migrate(
    con: sqlite3.Connection, inst_fd: int, steps: list[appschema.Step], schema_sql: str, tag: str
) -> tuple[str | None, str | None]:
    """Snapshot (if there are any tables), then apply: (snapshot name, error if rolled back)."""
    taken = snapshot(con, inst_fd, tag) if has_tables(con) else None
    try:
        appschema.apply(con, steps, schema_sql)
    except appschema.MigrationError as exc:
        return taken, str(exc)
    return taken, None


# --- the read-only copy for exec -------------------------------------------------------


def publish(con: sqlite3.Connection, inst_fd: int) -> None:
    """`ro/data.sqlite`: a `VACUUM INTO` copy, fsynced, 0444, renamed into place atomically.

    Exec opens it with `mode=ro&immutable=1`; it never sees the live file.
    """
    ro_fd = os.open(RO_DIR, _OPEN_DIR, dir_fd=inst_fd)
    try:
        tmp = f".{DB_NAME}.tmp"
        try:
            os.unlink(tmp, dir_fd=ro_fd)
        except FileNotFoundError:
            pass
        prev = con.getlimit(sqlite3.SQLITE_LIMIT_ATTACHED)
        try:
            con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, max(prev, 1))
            con.execute("VACUUM INTO ?", (_path(ro_fd, tmp),))
        finally:
            con.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, prev)
        _seal(ro_fd, tmp, 0o444)
        os.rename(tmp, DB_NAME, src_dir_fd=ro_fd, dst_dir_fd=ro_fd)
        os.fsync(ro_fd)
    finally:
        os.close(ro_fd)
