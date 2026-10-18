"""CSV export and import of app data (M22-08, docs/APP_TEMPLATES.md §5.3, docs/PLATFORM.md §7).

Export writes every table of an instance's database as `<table>.csv` (a header
row, then one line per row, the platform's `_created_by` ... columns included)
plus the app's `schema.sql` into a new folder in Files, so the data stays
readable without the app. NULL is an empty field, a BLOB is base64.

Import reads one CSV file into one table in one transaction, through the same
authorizer and attribution as any RPC write (the platform stamps each row). A
header maps to a column by `mapping` or, without one, by name (any case). A
row's empty fields are left out of its INSERT, so the column's default applies
(or its NOT NULL fails, naming the column). Any failure rolls back everything
and names the CSV line. `dry_run` does all of it and rolls back, to check a
file and see the mapping before importing.

The functions here are blocking: the caller runs them in a worker thread.
"""

from __future__ import annotations

import base64
import csv
import io
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.core import appdb, appschema
from app.core.errors import ImportFailed, InvalidInput

MAX_EXPORT_BYTES = 64 * 1024 * 1024
MAX_IMPORT_BYTES = 16 * 1024 * 1024
MAX_IMPORT_ROWS = 50_000
IMPORT_TIMEOUT_S = 60.0
DELIMITERS = (",", ";", "\t")

_CONSTRAINT_COLUMN = re.compile(r"constraint failed: ([^\s,]+)\.([^\s,]+)")


class _DryRun(Exception):
    pass


# --- export -----------------------------------------------------------------------------


def user_tables(con: sqlite3.Connection) -> list[str]:
    """The app's tables in creation order, the platform's own left out."""
    return [
        name
        for (name,) in con.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY rowid"
        )
        if not name.lower().startswith(appschema.RESERVED_PREFIXES)
    ]


def live_schema(con: sqlite3.Connection) -> str:
    """The database's own DDL, for an instance whose package `schema.sql` can't be read."""
    rows = con.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL "
        "AND type IN ('table', 'index', 'view', 'trigger') ORDER BY rowid"
    ).fetchall()
    return "".join(
        f"{sql};\n"
        for name, tbl, sql in rows
        if not name.lower().startswith(appschema.RESERVED_PREFIXES)
        and not tbl.lower().startswith(appschema.RESERVED_PREFIXES)
    )


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    if isinstance(value, float):
        return repr(value)
    return str(value)


def file_name(table: str) -> str:
    """`<table>.csv`, made safe as a file name."""
    name = re.sub(r"[/\x00]", "_", table)
    return ("_" + name if name.startswith(".") else name) + ".csv"


@dataclass(frozen=True)
class TableCsv:
    table: str
    file: str
    rows: int
    data: bytes


def export_tables(con: sqlite3.Connection) -> list[TableCsv]:
    out: list[TableCsv] = []
    total = 0
    for table in user_tables(con):
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\r\n")
        cur = con.execute(f"SELECT * FROM {appschema.quote(table)}")
        writer.writerow([d[0] for d in cur.description])
        rows = 0
        for row in cur:
            writer.writerow([_cell(v) for v in row])
            rows += 1
            if total + buf.tell() > MAX_EXPORT_BYTES:
                raise InvalidInput("export_too_large")
        data = buf.getvalue().encode()
        total += len(data)
        out.append(TableCsv(table, file_name(table), rows, data))
    return out


# --- import -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Parsed:
    headers: list[str]
    # (the CSV line the record starts on, its fields)
    records: list[tuple[int, list[str]]]


def parse(data: bytes, delimiter: str = ",") -> Parsed:
    if len(data) > MAX_IMPORT_BYTES:
        raise InvalidInput("file_too_large")
    if delimiter not in DELIMITERS:
        raise InvalidInput("invalid_delimiter")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportFailed("the file isn't UTF-8 text") from exc
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    records: list[tuple[int, list[str]]] = []
    headers: list[str] | None = None
    start = 1
    try:
        for fields in reader:
            line, start = start, reader.line_num + 1
            if headers is None:
                headers = [h.strip() for h in fields]
                if not any(headers):
                    raise ImportFailed("the first line must name the columns", line)
                continue
            if not any(f.strip() for f in fields):
                continue
            if len(fields) != len(headers):
                raise ImportFailed(f"{len(fields)} fields, but the header has {len(headers)}", line)
            records.append((line, fields))
            if len(records) > MAX_IMPORT_ROWS:
                raise ImportFailed(f"more than {MAX_IMPORT_ROWS} rows", line)
    except csv.Error as exc:
        raise ImportFailed(f"not valid CSV: {exc}", start) from exc
    if headers is None:
        raise ImportFailed("the file is empty")
    return Parsed(headers, records)


def importable_columns(con: sqlite3.Connection, table: str) -> list[str]:
    """The columns an import may set: not the platform's stamps, not generated ones."""
    if table not in user_tables(con):
        raise InvalidInput("unknown_table")
    info = con.execute(f"PRAGMA table_xinfo({appschema.quote(table)})").fetchall()
    return [r[1] for r in info if r[6] == 0 and r[1].lower() not in appschema.STAMP_COLUMNS]


@dataclass(frozen=True)
class Plan:
    # CSV header -> table column, in the file's order
    mapping: dict[str, str]
    # headers left out: no mapping for them, or (without one) the platform's own columns
    skipped: list[str]
    # headers that match no column (without a mapping only)
    unmatched: list[str]


def plan(headers: list[str], columns: list[str], mapping: dict[str, str] | None) -> Plan:
    by_lower = {c.lower(): c for c in columns}
    if len(set(headers)) != len(headers):
        raise ImportFailed("the header names a column twice", 1)
    if mapping is not None:
        for header, column in mapping.items():
            if header not in headers:
                raise ImportFailed(f'no "{header}" column in the file', 1)
            if column.lower() not in by_lower:
                raise ImportFailed(f'the table has no column "{column}"', 1, column)
        used = {h: by_lower[c.lower()] for h, c in mapping.items()}
        ordered = {h: used[h] for h in headers if h in used}
        skipped = [h for h in headers if h not in used]
        unmatched: list[str] = []
    else:
        ordered, skipped, unmatched = {}, [], []
        for header in headers:
            if header.lower() in appschema.STAMP_COLUMNS:
                skipped.append(header)
            elif header.lower() in by_lower:
                ordered[header] = by_lower[header.lower()]
            else:
                unmatched.append(header)
    targets = list(ordered.values())
    if len({c.lower() for c in targets}) != len(targets):
        raise ImportFailed("two columns of the file go to the same column", 1)
    return Plan(ordered, skipped, unmatched)


def import_rows(
    con: sqlite3.Connection,
    table: str,
    parsed: Parsed,
    the_plan: Plan,
    before_commit: appdb.BeforeCommit | None,
    *,
    dry_run: bool,
) -> dict[str, Any]:
    """INSERT one row per record in one transaction; `dry_run` rolls it back."""
    if the_plan.unmatched:
        names = ", ".join(f'"{h}"' for h in the_plan.unmatched)
        raise ImportFailed(f"no column of {table} for {names}: give a mapping", 1)
    if not the_plan.mapping:
        raise ImportFailed("no column of the file goes to the table", 1)
    index = {h: i for i, h in enumerate(parsed.headers)}
    picks = [(index[h], c) for h, c in the_plan.mapping.items()]
    q = appschema.quote(table)
    session = appdb.Session(con, write=True)
    session.deadline_in(IMPORT_TIMEOUT_S)
    out: dict[str, Any] = {}

    def finish(result: dict) -> None:
        if dry_run:
            raise _DryRun
        if before_commit is not None:
            before_commit(result)

    line = 1
    try:
        with session.transaction(finish) as result:
            changes = 0
            for line, fields in parsed.records:
                given = [(c, fields[i]) for i, c in picks if fields[i] != ""]
                if given:
                    cols = ", ".join(appschema.quote(c) for c, _ in given)
                    marks = ", ".join("?" * len(given))
                    sql = f"INSERT INTO {q} ({cols}) VALUES ({marks})"
                else:
                    sql = f"INSERT INTO {q} DEFAULT VALUES"
                changes += session.execute(sql, [v for _, v in given]).changes
            result.update({"changes": changes, "rows": changes})
            out.update(result)
    except _DryRun:
        pass
    except appdb.SqlError as exc:
        found = _CONSTRAINT_COLUMN.search(exc.message)
        raise ImportFailed(exc.message, line, found.group(2) if found else None) from exc
    return {
        "table": table,
        "rows": out.get("rows", 0),
        "mapping": the_plan.mapping,
        "skipped": the_plan.skipped,
        "dry_run": dry_run,
    }


def run_import(
    con: sqlite3.Connection,
    table: str,
    data: bytes,
    mapping: dict[str, str] | None,
    delimiter: str,
    dry_run: bool,
    before_commit: Callable[[dict], None] | None,
) -> dict[str, Any]:
    parsed = parse(data, delimiter)
    columns = importable_columns(con, table)
    the_plan = plan(parsed.headers, columns, mapping)
    about = {"headers": parsed.headers, "columns": columns, "unmatched": the_plan.unmatched}
    if dry_run and the_plan.unmatched:
        # Nothing to try yet: say what's in the file, for a mapping.
        return {
            "table": table, "rows": len(parsed.records), "mapping": the_plan.mapping,
            "skipped": the_plan.skipped, "dry_run": True, **about,
        }  # fmt: skip
    return {**import_rows(con, table, parsed, the_plan, before_commit, dry_run=dry_run), **about}
