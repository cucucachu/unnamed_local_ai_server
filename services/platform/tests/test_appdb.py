"""`app/core/appdb.py`: what an RPC statement can reach, limits, params, and the published copy.

Runs unprivileged on a temporary instance dir; `test_storage.py` checks the
real ownership and what a space member can do with it, as root.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from uuid import UUID

import pytest

from app.core import appdb
from app.core.appdb import SqlError

SCHEMA = "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL, qty INTEGER, pic BLOB);"


@pytest.fixture
def inst(tmp_path) -> Path:
    d = tmp_path / "inst"
    d.mkdir()
    for sub in ("ro", "snapshots"):
        (d / sub).mkdir()
    return d


@pytest.fixture
def db(inst):
    fd = os.open(inst, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with appdb.connect(fd) as con:
            con.executescript(SCHEMA)
            con.execute("INSERT INTO items (name, qty) VALUES ('Milk', 1), ('Eggs', 12)")
            yield con, fd
    finally:
        os.close(fd)


def test_connect_creates_a_wal_database(inst, db) -> None:
    con, _ = db
    assert con.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    assert con.execute("PRAGMA foreign_keys").fetchone() == (1,)
    assert stat.S_IMODE((inst / "data.sqlite").stat().st_mode) == 0o600
    assert [r[2] for r in con.execute("PRAGMA database_list")] == [str(inst / "data.sqlite")]


@pytest.mark.parametrize("name", ["data.sqlite", "data.sqlite-wal", "data.sqlite-shm"])
def test_a_symlinked_database_file_is_refused(inst, tmp_path, name) -> None:
    bait = tmp_path / "bait"
    bait.write_bytes(b"")
    (inst / name).symlink_to(bait)
    fd = os.open(inst, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(appdb.InstanceStorageError), appdb.connect(fd):
            pass
    finally:
        os.close(fd)
    assert bait.read_bytes() == b""


# --- reads and writes ---------------------------------------------------------------


def test_get_all_get_first_and_params(db) -> None:
    con, _ = db
    assert appdb.get_all(con, "SELECT name FROM items ORDER BY id", None) == [
        {"name": "Milk"}, {"name": "Eggs"},
    ]  # fmt: skip
    assert appdb.get_first(con, "SELECT qty FROM items WHERE name = ?", ["Eggs"]) == {"qty": 12}
    for key in ("name", ":name", "$name", "@name"):
        got = appdb.get_first(con, "SELECT qty FROM items WHERE name = $name", {key: "Milk"})
        assert got == {"qty": 1}
    assert appdb.get_first(con, "SELECT 1 FROM items WHERE 0", None) is None


def test_run_reports_changes_and_last_insert_row_id(db) -> None:
    con, _ = db
    assert appdb.run(con, "INSERT INTO items (name) VALUES (:n)", {"n": "Tea"}) == {
        "changes": 1, "lastInsertRowId": 3,
    }  # fmt: skip
    assert appdb.run(con, "UPDATE items SET qty = qty + 1", None)["changes"] == 3
    assert appdb.run(con, "DELETE FROM items WHERE name = ?", ["Tea"])["changes"] == 1


def test_values_round_trip(db) -> None:
    con, _ = db
    appdb.run(con, "INSERT INTO items (name, qty) VALUES (?, ?)", ["x", True])
    row = appdb.get_first(con, "SELECT qty, x'00ff' AS b, 1e999 AS inf, 2.5 AS f FROM items "
                               "WHERE name = 'x'", None)  # fmt: skip
    assert row == {"qty": 1, "b": {"$blob": "AP8="}, "inf": None, "f": 2.5}
    with pytest.raises(SqlError) as e:
        appdb.run(con, "INSERT INTO items (name) VALUES (?)", [[1, 2]])
    assert e.value.code == "invalid_params"
    with pytest.raises(SqlError) as e:
        appdb.run(con, "INSERT INTO items (name) VALUES (?)", [2**64])
    assert e.value.code == "invalid_params"


def test_sql_errors(db) -> None:
    con, _ = db
    for sql, params in [
        ("SELECT nope FROM items", None),
        ("INSERT INTO items (qty) VALUES (1)", None),  # NOT NULL
        ("SELECT 1; SELECT 2", None),
        ("SELECT ?", None),
    ]:
        with pytest.raises(SqlError) as e:
            appdb.run(con, sql, params)
        assert e.value.code == "sql_error", sql


def test_reads_cannot_write(db) -> None:
    con, _ = db
    for sql in ["INSERT INTO items (name) VALUES ('x')", "UPDATE items SET qty = 0",
                "DELETE FROM items", "INSERT INTO items (name) VALUES ('x') RETURNING id"]:  # fmt: skip
        with pytest.raises(SqlError) as e:
            appdb.get_all(con, sql, None)
        assert e.value.code == "sql_not_allowed", sql
    assert appdb.get_first(con, "SELECT count(*) AS n FROM items", None) == {"n": 2}


@pytest.mark.parametrize(
    "sql",
    [
        "ATTACH ':memory:' AS x",
        "ATTACH 'other.sqlite' AS x",
        "DETACH main",
        "VACUUM",
        "VACUUM INTO '/tmp/copy.sqlite'",
        "CREATE TABLE t (a)",
        "CREATE TEMP TABLE t (a)",
        "DROP TABLE items",
        "ALTER TABLE items ADD COLUMN x",
        "CREATE INDEX i ON items (qty)",
        "CREATE TRIGGER t AFTER INSERT ON items BEGIN DELETE FROM items; END",
        "CREATE VIEW v AS SELECT 1",
        "PRAGMA writable_schema = 1",
        "PRAGMA journal_mode = DELETE",
        "PRAGMA foreign_keys = OFF",
        "PRAGMA query_only = 0",
        "BEGIN",
        "COMMIT",
        "SAVEPOINT s",
        "ANALYZE",
        "REINDEX",
        "SELECT load_extension('/tmp/x.so')",
        "DELETE FROM sqlite_sequence",
        "UPDATE sqlite_master SET sql = ''",
    ],
)
def test_writes_are_confined_to_rows_of_this_database(db, inst, sql) -> None:
    con, _ = db
    with pytest.raises(SqlError) as e:
        appdb.run(con, sql, None)
    assert e.value.code in ("sql_not_allowed", "sql_error"), (e.value.code, e.value.message)
    assert not (inst / "other.sqlite").exists()
    assert not Path("/tmp/copy.sqlite").exists()
    assert [r[1] for r in con.execute("PRAGMA database_list")] == ["main"]
    assert con.execute("SELECT count(*) FROM items").fetchone() == (2,)
    assert not con.in_transaction


def test_attach_is_refused_by_the_limit_too(db) -> None:
    con, _ = db
    appdb.Session(con, write=True)
    with pytest.raises(sqlite3.OperationalError, match="too many attached"):
        con.execute("ATTACH ':memory:' AS x")


def test_introspection_pragmas_are_allowed(db) -> None:
    con, _ = db
    cols = appdb.get_all(con, "PRAGMA table_info(items)", None)
    assert [c["name"] for c in cols] == ["id", "name", "qty", "pic"]
    assert appdb.get_all(con, "SELECT name FROM pragma_table_info('items') LIMIT 1", None) == [
        {"name": "id"},
    ]  # fmt: skip
    tables = appdb.get_all(con, "SELECT name FROM sqlite_master WHERE type = 'table'", None)
    assert tables == [{"name": "items"}]


# --- limits -------------------------------------------------------------------------------


def test_timeout(db, monkeypatch) -> None:
    con, _ = db
    monkeypatch.setattr(appdb, "OP_TIMEOUT_S", 0.2)
    forever = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT max(x) FROM c"
    with pytest.raises(SqlError) as e:
        appdb.get_all(con, forever, None)
    assert e.value.code == "sql_timeout"


def test_row_and_size_caps(db, monkeypatch) -> None:
    con, _ = db
    monkeypatch.setattr(appdb, "MAX_ROWS", 5)
    many = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c LIMIT 6) SELECT x FROM c"
    with pytest.raises(SqlError) as e:
        appdb.get_all(con, many, None)
    assert e.value.code == "too_many_rows"
    assert appdb.get_first(con, many, None) == {"x": 1}
    monkeypatch.setattr(appdb, "MAX_RESULT_BYTES", 1000)
    with pytest.raises(SqlError) as e:
        appdb.get_all(con, "SELECT zeroblob(2000) AS b", None)
    assert e.value.code == "result_too_large"


def test_value_length_limit(db) -> None:
    con, _ = db
    with pytest.raises(SqlError) as e:
        appdb.get_all(con, f"SELECT randomblob({appdb.MAX_VALUE_BYTES + 1})", None)
    assert e.value.code == "sql_error"


# --- transactions and actions ---------------------------------------------------------------


def test_transaction_is_all_or_nothing(db) -> None:
    con, _ = db
    ok = appdb.transaction(
        con,
        [("INSERT INTO items (name) VALUES (?)", ["a"]), ("DELETE FROM items WHERE name = 'Milk'", None)],
    )  # fmt: skip
    assert ok == [{"changes": 1, "lastInsertRowId": 3}, {"changes": 1, "lastInsertRowId": 3}]
    with pytest.raises(SqlError) as e:
        appdb.transaction(
            con,
            [("INSERT INTO items (name) VALUES ('b')", None), ("INSERT INTO items (qty) VALUES (1)", None)],
        )  # fmt: skip
    assert e.value.index == 1
    assert [r["name"] for r in appdb.get_all(con, "SELECT name FROM items ORDER BY id", None)] == [
        "Eggs", "a",
    ]  # fmt: skip


def test_split_statements() -> None:
    sql = """
    -- add an item; keep the count
    INSERT INTO items (name) VALUES ('a;b');  /* ; */
    UPDATE items SET qty = 1 WHERE name = "x;y";
    SELECT 1
    ;
    -- trailing comment
    """
    parts = appdb.split_statements(sql)
    assert len(parts) == 3
    assert "'a;b'" in parts[0] and '"x;y"' in parts[1]


def test_action_named_params_and_rows(db) -> None:
    con, _ = db
    sql = (
        "INSERT INTO items (name, qty) VALUES (:name, :qty);\n"
        "UPDATE items SET qty = qty + :qty WHERE name = 'Milk';\n"
        "SELECT id, name FROM items WHERE name = :name;\n"
    )
    out = appdb.action(con, sql, {"name": "Tea", "qty": 2})
    assert out == {"changes": 2, "lastInsertRowId": 3, "rows": [{"id": 3, "name": "Tea"}]}
    with pytest.raises(SqlError) as e:
        appdb.action(con, sql, {"name": "Tea"})
    assert (e.value.code, e.value.index) == ("sql_error", 0)
    with pytest.raises(SqlError) as e:
        appdb.action(con, "INSERT INTO items (name) VALUES ('z'); DROP TABLE items;", {})
    assert (e.value.code, e.value.index) == ("sql_not_allowed", 1)
    assert appdb.get_first(con, "SELECT count(*) AS n FROM items WHERE name = 'z'", None) == {
        "n": 0
    }


# --- snapshots ------------------------------------------------------------------------------------


def test_snapshot_keeps_the_newest_ten(db, inst) -> None:
    con, fd = db
    names = [appdb.snapshot(con, fd, f"m{i:02}") for i in range(12)]
    kept = sorted(os.listdir(inst / "snapshots"))
    assert kept == sorted(names[-10:])
    copy = sqlite3.connect(inst / "snapshots" / kept[-1])
    assert copy.execute("SELECT count(*) FROM items").fetchone() == (2,)
    copy.close()


def test_publish_is_a_read_only_consistent_copy(db, inst) -> None:
    con, fd = db
    appdb.publish(con, fd)
    ro = inst / "ro" / "data.sqlite"
    assert stat.S_IMODE(ro.stat().st_mode) == 0o444
    assert os.listdir(inst / "ro") == ["data.sqlite"]
    reader = sqlite3.connect(f"file:{ro}?mode=ro&immutable=1", uri=True)
    assert reader.execute("SELECT count(*) FROM items").fetchone() == (2,)
    appdb.run(con, "INSERT INTO items (name) VALUES ('new')", None)
    appdb.publish(con, fd)
    reader.close()
    reader = sqlite3.connect(f"file:{ro}?mode=ro&immutable=1", uri=True)
    assert reader.execute("SELECT count(*) FROM items").fetchone() == (3,)
    reader.close()


def _instance_dir(path: Path) -> Path:
    path.mkdir()
    for sub in ("ro", "snapshots"):
        (path / sub).mkdir()
    return path


def test_platform_attach_is_read_only_and_user_attach_still_fails(tmp_path) -> None:
    reader = _instance_dir(tmp_path / "reader")
    exporter = _instance_dir(tmp_path / "exporter")
    exp_fd = os.open(exporter, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with appdb.connect(exp_fd) as eco:
            eco.executescript(
                "CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT);\n"
                "CREATE TABLE secrets (id INTEGER PRIMARY KEY, note TEXT);\n"
            )
            eco.execute("INSERT INTO events (title) VALUES ('Dentist')")
            eco.execute("INSERT INTO secrets (note) VALUES ('hidden')")
        rfd = os.open(reader, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with appdb.connect(rfd) as con:
                appdb.attach_exports(
                    con,
                    [
                        appdb.AttachGrant(
                            inst_fd=exp_fd,
                            instance_id=UUID(int=1),
                            app_slug="calendar",
                            export_name="events",
                            tables=("events",),
                            space_path="/personal",
                        )
                    ],
                )
                rows = appdb.get_all(con, "SELECT title, _space FROM calendar_events", None)
                assert rows == [{"title": "Dentist", "_space": "/personal"}]
                with pytest.raises(SqlError) as e:
                    appdb.run(
                        con, "INSERT INTO calendar_events (title, _space) VALUES ('x', '/p')", None
                    )
                assert e.value.code == "sql_not_allowed"
                schema = appdb.attach_schema_name(UUID(int=1))
                with pytest.raises(SqlError) as e:
                    appdb.run(con, f"INSERT INTO {schema}.events (title) VALUES ('x')", None)
                assert e.value.code == "sql_not_allowed"
                with pytest.raises(SqlError) as e:
                    appdb.get_all(con, f"SELECT note FROM {schema}.secrets", None)
                assert e.value.code in ("sql_not_allowed", "sql_error")
                appdb.Session(con, write=True)
                with pytest.raises(sqlite3.OperationalError, match="too many attached"):
                    con.execute("ATTACH ':memory:' AS x")
        finally:
            os.close(rfd)
    finally:
        os.close(exp_fd)
