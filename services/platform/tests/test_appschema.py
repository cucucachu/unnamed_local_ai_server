"""`app/core/appschema.py`: classification, apply + rollback, and what `schema.sql` may contain.

The matrix is the M12-01 spike's (`spikes/app_runtime/schema/cases.py`): the
live database is always BASE with SEED rows in it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.core import appschema
from app.core.appschema import MigrationError, SchemaError

BASE = """
CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  done INTEGER NOT NULL DEFAULT 0,
  note TEXT
);
CREATE INDEX items_name ON items (name);
CREATE TABLE lists (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL
);
"""
SEED = """
INSERT INTO items (name, done, note) VALUES ('Milk', 0, '2%'), ('Eggs', 1, NULL), ('Bread', 0, 'rye');
INSERT INTO lists (title) VALUES ('Weekly');
"""
IDX = "CREATE INDEX items_name ON items (name);"
LISTS = "CREATE TABLE lists (\n  id INTEGER PRIMARY KEY,\n  title TEXT NOT NULL\n);"


def items(extra: str = "", done: str = "done INTEGER NOT NULL DEFAULT 0", note: str = "note TEXT",
          name: str = "name TEXT NOT NULL") -> str:  # fmt: skip
    return f"CREATE TABLE items (\n  id INTEGER PRIMARY KEY,\n  {name},\n  {done},\n  {note}{extra}\n);"


# (case, kinds of the steps in order, applies once approved, rows kept in items)
CASES = [
    ("noop", [], f"{items()}\n{IDX}\n{LISTS}", True),
    ("add_table", ["additive"],
     f"{items()}\n{IDX}\n{LISTS}\nCREATE TABLE tags (id INTEGER PRIMARY KEY, label TEXT NOT NULL);",
     True),
    ("add_column_nullable", ["additive"], f"{items(',\n  qty INTEGER')}\n{IDX}\n{LISTS}", True),
    ("add_column_notnull_default", ["additive"],
     f"{items(',\n  qty INTEGER NOT NULL DEFAULT 1')}\n{IDX}\n{LISTS}", True),
    ("add_column_notnull_no_default", ["destructive"],
     f"{items(',\n  qty INTEGER NOT NULL')}\n{IDX}\n{LISTS}", False),
    ("add_column_fk", ["additive"],
     f"{items(',\n  list_id INTEGER REFERENCES lists (id)')}\n{IDX}\n{LISTS}", True),
    ("add_index", ["additive"], f"{items()}\n{IDX}\nCREATE INDEX items_done ON items (done);\n{LISTS}",
     True),
    ("drop_index", ["safe"], f"{items()}\n{LISTS}", True),
    ("drop_column", ["destructive"],
     ("CREATE TABLE items (\n  id INTEGER PRIMARY KEY,\n  name TEXT NOT NULL,\n"
      f"  done INTEGER NOT NULL DEFAULT 0\n);\n{IDX}\n{LISTS}"), True),
    ("drop_table", ["destructive"], f"{items()}\n{IDX}", True),
    ("type_change", ["destructive"],
     f"{items(done="done TEXT NOT NULL DEFAULT 'no'")}\n{IDX}\n{LISTS}", True),
    ("rename_column", ["destructive"],
     f"{items(name='title TEXT NOT NULL')}\nCREATE INDEX items_name ON items (title);\n{LISTS}",
     False),
    ("add_unique", ["destructive"], f"{items(name='name TEXT NOT NULL UNIQUE')}\n{IDX}\n{LISTS}",
     True),
    ("nullable_to_notnull", ["destructive"],
     f"{items(note="note TEXT NOT NULL DEFAULT ''")}\n{IDX}\n{LISTS}", False),
    ("change_default", ["safe"], f"{items(done='done INTEGER NOT NULL DEFAULT 1')}\n{IDX}\n{LISTS}",
     True),
]  # fmt: skip


@pytest.fixture
def live(tmp_path) -> sqlite3.Connection:
    con = sqlite3.connect(tmp_path / "live.sqlite", isolation_level=None)
    con.execute("PRAGMA journal_mode = WAL")
    con.executescript(BASE + SEED)
    yield con
    con.close()


def _rows(con: sqlite3.Connection) -> dict:
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    return {t: con.execute(f'SELECT * FROM "{t}" ORDER BY 1').fetchall() for t in tables}


@pytest.mark.parametrize(("case", "kinds", "desired", "applies"), CASES, ids=[c[0] for c in CASES])
def test_matrix(live, case, kinds, desired, applies) -> None:
    plan = appschema.plan(live, desired)
    assert [s.kind for s in plan.steps] == kinds
    assert plan.needs_approval == ("destructive" in kinds)
    before = _rows(live)
    if applies:
        appschema.apply(live, plan.steps, desired)
        assert appschema.plan(live, desired).steps == []
        if case in ("noop", "add_index", "drop_index", "change_default", "add_unique"):
            assert _rows(live) == before
    else:
        with pytest.raises(MigrationError):
            appschema.apply(live, plan.steps, desired)
        assert _rows(live) == before
        assert not live.in_transaction


def test_reasons_are_readable(live) -> None:
    (step,) = appschema.plan(live, CASES[11][2]).steps
    assert step.op == "rebuild"
    assert "renames are drop+add" in step.reason
    (step,) = appschema.plan(live, CASES[4][2]).steps
    assert "NOT NULL without a default" in step.reason


def test_rebuild_keeps_rows_and_foreign_keys(live) -> None:
    desired = f"{items(done="done TEXT NOT NULL DEFAULT 'no'")}\n{IDX}\n{LISTS}"
    appschema.apply(live, appschema.plan(live, desired).steps, desired)
    assert live.execute("SELECT name, done FROM items ORDER BY id").fetchall() == [
        ("Milk", "0"), ("Eggs", "1"), ("Bread", "0"),
    ]  # fmt: skip
    assert live.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert [r[1] for r in live.execute("PRAGMA index_list(items)")] == ["items_name"]


def test_foreign_key_violation_rolls_back(live) -> None:
    live.execute("INSERT INTO items (name, note) VALUES ('Orphan', '9')")
    desired = f"{items(note='note INTEGER REFERENCES lists (id)')}\n{IDX}\n{LISTS}"
    plan = appschema.plan(live, desired)
    assert [s.kind for s in plan.steps] == ["destructive"]
    before = _rows(live)
    with pytest.raises(MigrationError, match="foreign_key_check"):
        appschema.apply(live, plan.steps, desired)
    assert _rows(live) == before


def test_quoted_and_odd_names(tmp_path) -> None:
    con = sqlite3.connect(tmp_path / "odd.sqlite", isolation_level=None)
    con.execute('CREATE TABLE "my ""items""" (id INTEGER PRIMARY KEY, "a b" TEXT)')
    con.execute('INSERT INTO "my ""items""" ("a b") VALUES (\'x\')')
    desired = 'CREATE TABLE "my ""items"""(id INTEGER PRIMARY KEY, "a b" INTEGER);'
    plan = appschema.plan(con, desired)
    assert [(s.kind, s.op) for s in plan.steps] == [("destructive", "rebuild")]
    appschema.apply(con, plan.steps, desired)
    assert con.execute('SELECT "a b" FROM "my ""items"""').fetchall() == [("x",)]
    assert appschema.plan(con, desired).steps == []


def test_fresh_database_is_all_additive(tmp_path) -> None:
    con = sqlite3.connect(tmp_path / "new.sqlite", isolation_level=None)
    plan = appschema.plan(con, BASE)
    assert plan.summary == {"additive": 3, "safe": 0, "destructive": 0}
    appschema.apply(con, plan.steps, BASE)
    assert appschema.plan(con, BASE).steps == []


def test_empty_schema_drops_everything_destructively(live) -> None:
    plan = appschema.plan(live, "")
    assert {(s.kind, s.op) for s in plan.steps} == {("destructive", "drop_table")}


@pytest.mark.parametrize(
    "bad",
    [
        "ATTACH '/tmp/x.db' AS x;",
        "CREATE TABLE t (a); INSERT INTO t VALUES (1);",
        "PRAGMA writable_schema = 1;",
        "CREATE TABLE t (a); CREATE TRIGGER tr AFTER INSERT ON t BEGIN SELECT 1; END;",
        "CREATE TABLE t (a); CREATE VIEW v AS SELECT * FROM t;",
        "CREATE TEMP TABLE t (a);",
        "CREATE VIRTUAL TABLE t USING fts5(a);",
        "CREATE TABLE t AS SELECT 1 AS a;",
        "VACUUM INTO '/tmp/x.db';",
        "CREATE TABLE t (a); DELETE FROM t;",
        "CREATE TABLE _homeai_new_t (a);",
        "CREATE TABLE t (a",
        "SELECT load_extension('x');",
    ],
)
def test_schema_may_only_create_tables_and_indexes(live, bad) -> None:
    with pytest.raises(SchemaError):
        appschema.plan(live, bad)


def test_oversized_schema(live) -> None:
    with pytest.raises(SchemaError, match="larger than"):
        appschema.plan(live, "-- " + "x" * appschema.MAX_SCHEMA_BYTES)


def test_module_is_stdlib_only() -> None:
    source = Path(appschema.__file__).read_text()
    imports = {line.split()[1].split(".")[0] for line in source.splitlines()
               if line.startswith(("import ", "from "))}  # fmt: skip
    assert imports <= {"__future__", "re", "sqlite3", "time", "dataclasses"}
