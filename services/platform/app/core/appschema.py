"""Computed migrations for app databases (docs/PLATFORM.md §7 "Data", D11).

`plan(live, schema_sql)` runs the desired `schema.sql` into a scratch
in-memory database under an authorizer that permits only CREATE TABLE /
CREATE INDEX, then compares it with the live database (`PRAGMA table_xinfo`
/ `foreign_key_list` / `index_list`, plus each table's CREATE text split into
normalized column definitions and table constraints) and returns classified
steps:

  additive     CREATE TABLE, ADD COLUMN (when SQLite allows it), CREATE INDEX
  safe         DROP / replace INDEX, a rebuild that only relaxes (default change, drop NOT NULL)
  destructive  DROP TABLE / COLUMN, a rebuild that retypes or tightens (type, NOT NULL,
               UNIQUE, CHECK, FK, PK), renames (drop + add)

`apply(live, steps, schema_sql)` runs every step in one `BEGIN IMMEDIATE`
transaction with foreign keys off (table rebuilds need it), then requires
`PRAGMA foreign_key_check` to be clean and a re-diff to come back empty
before it commits; anything else rolls back and raises `MigrationError`.

Ported from the M12-01 spike (`spikes/app_runtime/schema/pydiff.py`, which
classified its 15-case matrix correctly where sqlite3def didn't).
Standard library only.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import asdict, dataclass

MAX_SCHEMA_BYTES = 256 * 1024
MAX_MIGRATION_S = 60.0
RESERVED_PREFIXES = ("_homeai_", "sqlite_")
_REBUILD_PREFIX = "_homeai_new_"

KINDS = ("additive", "safe", "destructive")

_ALLOWED = {
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_REINDEX,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_TRANSACTION,
}
# INSERT/UPDATE are only SQLite's own bookkeeping for a CREATE.
_SCHEMA_TABLES = {"sqlite_master", "sqlite_temp_master", "sqlite_sequence"}


class SchemaError(Exception):
    """`schema.sql` isn't a set of CREATE TABLE / CREATE INDEX statements SQLite accepts."""


class MigrationError(Exception):
    """Applying a plan failed and was rolled back."""


@dataclass(frozen=True)
class Step:
    kind: str
    op: str
    table: str
    sql: list[str]
    reason: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Plan:
    steps: list[Step]

    @property
    def summary(self) -> dict[str, int]:
        return {k: sum(s.kind == k for s in self.steps) for k in KINDS}

    @property
    def needs_approval(self) -> bool:
        return any(s.kind == "destructive" for s in self.steps)

    def as_dicts(self) -> list[dict]:
        return [s.as_dict() for s in self.steps]


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _schema_authorizer(action, arg1, arg2, dbname, source):
    if action not in _ALLOWED:
        return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE) and arg1 not in _SCHEMA_TABLES:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _deadline_handler(seconds: float):
    deadline = time.monotonic() + seconds
    return lambda: int(time.monotonic() > deadline)


def scratch_from(schema_sql: str) -> sqlite3.Connection:
    """`schema_sql` executed into a fresh `:memory:` database, or `SchemaError`."""
    if len(schema_sql.encode()) > MAX_SCHEMA_BYTES:
        raise SchemaError(f"schema.sql is larger than {MAX_SCHEMA_BYTES // 1024} KiB")
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.set_authorizer(_schema_authorizer)
    con.set_progress_handler(_deadline_handler(10.0), 1000)
    try:
        con.executescript(schema_sql)
    except sqlite3.Error as exc:
        con.close()
        raise SchemaError(
            f"schema.sql: {exc} (only CREATE TABLE / CREATE INDEX statements are allowed)"
        ) from exc
    con.set_authorizer(None)
    con.set_progress_handler(None, 0)
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE sql IS NOT NULL")]
    reserved = [n for n in names if n.lower().startswith(RESERVED_PREFIXES)]
    if reserved:
        con.close()
        raise SchemaError(f"schema.sql: names starting with _homeai_ are reserved: {reserved}")
    return con


# --- CREATE TABLE text -> column defs + table constraints ---------------------------

_TOKEN = re.compile(
    r"""\s+|--[^\n]*|/\*.*?\*/|'(?:[^']|'')*'|"(?:[^"]|"")*"|`[^`]*`|\[[^\]]*\]|[(),]|[^\s(),'"`\[]+""",
    re.DOTALL,
)
_CONSTRAINT_KW = ("constraint", "primary", "unique", "check", "foreign")
_CREATE_TABLE = re.compile(
    r"""(?is)^\s*create\s+table\s+(if\s+not\s+exists\s+)?("(?:[^"]|"")+"|`[^`]+`|\[[^\]]+\]|[^\s(]+)"""
)


def _is_noise(token: str) -> bool:
    return not token.strip() or token.startswith(("--", "/*"))


def _norm(tokens: list[str]) -> str:
    out = []
    for t in tokens:
        if _is_noise(t):
            continue
        if t[0] in '"`[':
            out.append(t[1:-1].replace('""', '"'))
        elif t[0] == "'":
            out.append(t)
        else:
            out.append(t.lower())
    return " ".join(out).replace("( ", "(").replace(" )", ")").replace(" ,", ",")


def parse_table(sql: str) -> dict:
    toks = _TOKEN.findall(sql)
    start = toks.index("(")
    depth, parts, cur, raw, raw_parts, end = 0, [], [], [], [], len(toks)
    for i in range(start + 1, len(toks)):
        t = toks[i]
        if t == "(":
            depth += 1
        elif t == ")":
            if depth == 0:
                end = i
                break
            depth -= 1
        if t == "," and depth == 0:
            parts.append(cur)
            raw_parts.append("".join(raw).strip())
            cur, raw = [], []
            continue
        cur.append(t)
        raw.append(t)
    parts.append(cur)
    raw_parts.append("".join(raw).strip())
    columns, constraints = {}, []
    for p, r in zip(parts, raw_parts, strict=True):
        words = [w for w in p if not _is_noise(w)]
        if not words:
            continue
        if words[0].lower() in _CONSTRAINT_KW:
            constraints.append(_norm(p))
        else:
            columns[_norm([words[0]])] = {"norm": _norm(p), "text": r}
    return {
        "columns": columns,
        "constraints": sorted(constraints),
        "options": _norm(toks[end + 1 :]),
    }


# --- introspection -------------------------------------------------------------------


def describe(con: sqlite3.Connection) -> dict:
    tables, indexes = {}, {}
    rows = con.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite\\_%' "
        "ESCAPE '\\'"
    ).fetchall()
    for name, sql in rows:
        q = quote(name)
        info = {
            r[1]: {"type": (r[2] or "").lower(), "notnull": r[3], "dflt": r[4], "pk": r[5],
                   "hidden": r[6]}
            for r in con.execute(f"PRAGMA table_xinfo({q})")
        }  # fmt: skip
        fks = sorted(tuple(r[2:5]) for r in con.execute(f"PRAGMA foreign_key_list({q})"))
        uniques = [r[1] for r in con.execute(f"PRAGMA index_list({q})") if r[3] in ("u", "pk")]
        tables[name] = {
            "sql": sql, "parsed": parse_table(sql), "cols": info, "fks": fks,
            "auto_unique": len(uniques),
        }  # fmt: skip
    for name, tbl, sql in con.execute(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
    ):
        indexes[name] = {"table": tbl, "sql": sql, "norm": _norm(_TOKEN.findall(sql))}
    return {"tables": tables, "indexes": indexes}


def _can_add_column(col_norm: str, info: dict) -> tuple[bool, str]:
    if info["pk"]:
        return False, "new PRIMARY KEY column"
    if re.search(r"\bunique\b", col_norm):
        return False, "new UNIQUE column"
    if info["notnull"] and info["dflt"] in (None, "NULL"):
        return False, "NOT NULL without a default"
    if info["dflt"] and re.match(r"(?i)^\(|current_(time|date|timestamp)", info["dflt"]):
        return False, "non-constant default"
    if info["hidden"] in (2, 3) and "stored" in col_norm:
        return False, "STORED generated column"
    if re.search(r"\breferences\b", col_norm) and info["dflt"] not in (None, "NULL"):
        return False, "REFERENCES with a non-NULL default"
    return True, ""


def _rebuild(name: str, cur: dict, want: dict, want_indexes: dict) -> Step:
    kept = [c for c in want["cols"] if c in cur["cols"] and not want["cols"][c]["hidden"]]
    dropped = [c for c in cur["cols"] if c not in want["cols"]]
    reasons, tighten = [], bool(dropped)
    if dropped:
        reasons.append(f"drops column(s) {dropped}")
    for c in kept:
        a, b = cur["cols"][c], want["cols"][c]
        if a["type"] != b["type"]:
            reasons.append(f"{c}: type {a['type']!r} -> {b['type']!r}")
            tighten = True
        if b["notnull"] and not a["notnull"]:
            reasons.append(f"{c}: adds NOT NULL")
            tighten = True
        if a["notnull"] and not b["notnull"]:
            reasons.append(f"{c}: drops NOT NULL")
        if a["dflt"] != b["dflt"]:
            reasons.append(f"{c}: default {a['dflt']} -> {b['dflt']}")
        if a["pk"] != b["pk"]:
            reasons.append(f"{c}: primary key change")
            tighten = True
        was = cur["parsed"]["columns"].get(c, {}).get("norm")
        now = want["parsed"]["columns"][c]["norm"]
        if was != now and not reasons:
            reasons.append(f"{c}: definition {was!r} -> {now!r}")
            tighten = True
    if cur["fks"] != want["fks"]:
        reasons.append("foreign keys change")
        tighten = True
    if (
        cur["parsed"]["constraints"] != want["parsed"]["constraints"]
        or cur["auto_unique"] < want["auto_unique"]
    ):
        reasons.append("table constraints change (UNIQUE/CHECK/PK/FK)")
        tighten = True
    if cur["parsed"]["options"] != want["parsed"]["options"]:
        reasons.append(
            f"table options {cur['parsed']['options']!r} -> {want['parsed']['options']!r}"
        )
        tighten = True
    tmp = quote(_REBUILD_PREFIX + name)
    new_sql = _CREATE_TABLE.sub(f"CREATE TABLE {tmp}", want["sql"], count=1)
    cols = ", ".join(quote(c) for c in kept)
    sql = [
        new_sql,
        f"INSERT INTO {tmp} ({cols}) SELECT {cols} FROM {quote(name)}",
        f"DROP TABLE {quote(name)}",
        f"ALTER TABLE {tmp} RENAME TO {quote(name)}",
    ]
    sql += [ix["sql"] for ix in want_indexes.values() if ix["table"] == name]
    added = [c for c in want["cols"] if c not in cur["cols"]]
    for c in added:
        ok, why = _can_add_column(want["parsed"]["columns"][c]["norm"], want["cols"][c])
        reasons.append(
            f"adds column {c}"
            + ("" if ok else f" that can't be added in place ({why}; fails if the table has "
                             "rows - add a DEFAULT)")
        )  # fmt: skip
        tighten = tighten or not ok
    if dropped and added:
        reasons.append(
            f"hint: if {dropped} -> {added} is a rename, data in {dropped} is lost; "
            "renames are drop+add"
        )
    kind = "destructive" if tighten else "safe"
    return Step(kind, "rebuild", name, sql, "; ".join(reasons) or "definition text changed")


_ORDER = {
    "create_table": 0, "add_column": 1, "rebuild": 2, "drop_column": 3, "replace_index": 4,
    "create_index": 5, "drop_index": 6, "drop_table": 7,
}  # fmt: skip


def diff(live: sqlite3.Connection, desired: sqlite3.Connection) -> list[Step]:
    cur, want = describe(live), describe(desired)
    steps: list[Step] = []
    rebuilt = set()
    for name, w in want["tables"].items():
        if name not in cur["tables"]:
            steps.append(Step("additive", "create_table", name, [w["sql"]], "new table"))
            continue
        c = cur["tables"][name]
        added = [col for col in w["cols"] if col not in c["cols"]]
        dropped = [col for col in c["cols"] if col not in w["cols"]]
        common_same = all(
            c["cols"][col] == w["cols"][col]
            and c["parsed"]["columns"][col]["norm"] == w["parsed"]["columns"][col]["norm"]
            for col in w["cols"]
            if col in c["cols"]
        )
        rest_same = (
            c["parsed"]["constraints"] == w["parsed"]["constraints"]
            and c["parsed"]["options"] == w["parsed"]["options"]
        )
        fks_same = c["fks"] == sorted(fk for fk in w["fks"] if fk[1] not in added)
        same = common_same and rest_same and fks_same
        if same and not dropped and not added:
            continue
        if same and not dropped:
            checks = [
                _can_add_column(w["parsed"]["columns"][col]["norm"], w["cols"][col])[0]
                for col in added
            ]
            if all(checks):
                for col in added:
                    text = w["parsed"]["columns"][col]["text"]
                    steps.append(
                        Step(
                            "additive", "add_column", name,
                            [f"ALTER TABLE {quote(name)} ADD COLUMN {text}"], f"new column {col}",
                        )
                    )  # fmt: skip
                continue
        if same and not added and dropped:
            indexed = {
                r[2]
                for ixname, ix in cur["indexes"].items()
                if ix["table"] == name
                for r in live.execute(f"PRAGMA index_info({quote(ixname)})")
            }
            if not (set(dropped) & indexed) and not any(c["cols"][d]["pk"] for d in dropped):
                for col in dropped:
                    steps.append(
                        Step(
                            "destructive", "drop_column", name,
                            [f"ALTER TABLE {quote(name)} DROP COLUMN {quote(col)}"],
                            f"drops column {col} and its data",
                        )
                    )  # fmt: skip
                continue
        steps.append(_rebuild(name, c, w, want["indexes"]))
        rebuilt.add(name)
    for name in cur["tables"]:
        if name not in want["tables"]:
            steps.append(
                Step(
                    "destructive", "drop_table", name, [f"DROP TABLE {quote(name)}"],
                    f"drops table {name} and all its rows",
                )
            )  # fmt: skip
    for name, ix in want["indexes"].items():
        if ix["table"] in rebuilt:
            continue
        old = cur["indexes"].get(name)
        if old is None:
            steps.append(Step("additive", "create_index", ix["table"], [ix["sql"]],
                              f"new index {name}"))  # fmt: skip
        elif old["norm"] != ix["norm"]:
            steps.append(
                Step("safe", "replace_index", ix["table"], [f"DROP INDEX {quote(name)}", ix["sql"]],
                     f"index {name} changed")
            )  # fmt: skip
    for name, ix in cur["indexes"].items():
        if (
            name not in want["indexes"]
            and ix["table"] in want["tables"]
            and ix["table"] not in rebuilt
        ):
            steps.append(
                Step("safe", "drop_index", ix["table"], [f"DROP INDEX {quote(name)}"],
                     f"index {name} removed (no data loss)")
            )  # fmt: skip
    return sorted(steps, key=lambda s: _ORDER[s.op])


def plan(live: sqlite3.Connection, schema_sql: str) -> Plan:
    desired = scratch_from(schema_sql)
    try:
        return Plan(diff(live, desired))
    finally:
        desired.close()


def apply(live: sqlite3.Connection, steps: list[Step], schema_sql: str) -> None:
    """Run `steps` atomically on `live` (autocommit mode, no authorizer), or raise."""
    live.execute("PRAGMA foreign_keys = OFF")
    live.set_progress_handler(_deadline_handler(MAX_MIGRATION_S), 1000)
    try:
        live.execute("BEGIN IMMEDIATE")
        try:
            for step in steps:
                for stmt in step.sql:
                    live.execute(stmt)
            bad = live.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise MigrationError(f"foreign_key_check failed: {bad[:5]}")
            left = plan(live, schema_sql).steps
            if left:
                raise MigrationError(
                    f"the schema still differs after migrating: {[s.as_dict() for s in left]}"
                )
            live.execute("COMMIT")
        except BaseException as exc:
            live.set_progress_handler(None, 0)
            if live.in_transaction:
                live.execute("ROLLBACK")
            if isinstance(exc, sqlite3.Error):
                raise MigrationError(f"{type(exc).__name__}: {exc}") from exc
            raise
    finally:
        live.set_progress_handler(None, 0)
        live.execute("PRAGMA foreign_keys = ON")
