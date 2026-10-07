"""Row attribution: the platform's `_created_by` / `_created_at` / `_updated_by` /
`_updated_at` columns on every app table (`appschema.STAMP_COLUMNS`, `appdb.stamp`).

Unit tests on a temporary instance dir, then the API: writes through the
UI and through an agent are stamped with the verified user, and nothing an
app or agent sends can say otherwise.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from app.core import appdb, appschema
from app.core.appdb import SqlError
from tests.app_packages import FILES as PACKAGE_FILES
from tests.app_packages import write_package
from tests.files_world import API, World

SCHEMA = (
    "CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT NOT NULL);\n"
    "CREATE INDEX messages_by ON messages (_created_by);\n"
    "CREATE TABLE tags (name TEXT PRIMARY KEY, n INTEGER NOT NULL DEFAULT 0) WITHOUT ROWID;\n"
)
ALICE, BOB = "alice-id", "bob-id"


@pytest.fixture
def con(tmp_path):
    d = tmp_path / "inst"
    d.mkdir()
    for sub in ("ro", "snapshots"):
        (d / sub).mkdir()
    fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with appdb.connect(fd) as c:
            appschema.apply(c, appschema.plan(c, SCHEMA).steps, SCHEMA)
            yield c
    finally:
        os.close(fd)


def _stamps(con, table: str = "messages") -> list[tuple]:
    return con.execute(
        f"SELECT _created_by, _updated_by, _created_at IS NOT NULL, _updated_at IS NOT NULL "
        f"FROM {table} ORDER BY 1"
    ).fetchall()


# --- schema -----------------------------------------------------------------------------


def test_every_table_gets_the_columns_and_they_can_be_indexed(con) -> None:
    for table in ("messages", "tags"):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        assert cols[-4:] == list(appschema.STAMP_COLUMNS)
    assert con.execute("SELECT name FROM sqlite_master WHERE name = 'messages_by'").fetchone()
    assert appschema.plan(con, SCHEMA).steps == []


def test_existing_tables_get_them_as_an_additive_migration(tmp_path) -> None:
    live = sqlite3.connect(":memory:", isolation_level=None)
    live.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
    live.execute("INSERT INTO messages (body) VALUES ('old')")
    plan = appschema.plan(
        live, "CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT NOT NULL);"
    )
    assert [(s.kind, s.op) for s in plan.steps] == [("additive", "add_column")] * 4
    assert not plan.needs_approval
    appschema.apply(
        live, plan.steps, "CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT NOT NULL);"
    )
    assert live.execute("SELECT body, _created_by FROM messages").fetchall() == [("old", None)]


def test_schema_sql_cannot_declare_them() -> None:
    with pytest.raises(appschema.SchemaError, match="the platform adds _created_by"):
        appschema.scratch_from("CREATE TABLE t (id INTEGER PRIMARY KEY, _created_by TEXT);")


# --- writes -----------------------------------------------------------------------------


def test_inserts_and_updates_are_stamped_with_the_caller(con) -> None:
    appdb.stamp(con, ALICE)
    appdb.run(con, "INSERT INTO messages (body) VALUES ('hi')", None)
    assert _stamps(con) == [(ALICE, ALICE, 1, 1)]
    appdb.stamp(con, BOB)
    appdb.run(con, "UPDATE messages SET body = 'hey'", None)
    assert _stamps(con) == [(ALICE, BOB, 1, 1)]
    appdb.action(
        con, "INSERT INTO tags (name) VALUES (:n);\nUPDATE tags SET n = n + 1;", {"n": "x"}
    )
    assert _stamps(con, "tags") == [(BOB, BOB, 1, 1)]


def test_a_value_an_insert_supplies_is_overwritten(con) -> None:
    appdb.stamp(con, BOB)
    appdb.run(
        con,
        "INSERT INTO messages (body, _created_by, _updated_by) VALUES ('forged', :who, :who)",
        {"who": ALICE},
    )
    appdb.run(con, "INSERT OR REPLACE INTO tags (name, _created_by) VALUES ('a', 'alice-id')", None)
    assert _stamps(con) == [(BOB, BOB, 1, 1)]
    assert _stamps(con, "tags") == [(BOB, BOB, 1, 1)]


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE messages SET _created_by = 'alice-id'",
        "UPDATE messages SET body = 'x', _updated_at = '2000-01-01'",
        "INSERT INTO messages (id, body) VALUES (1, 'x') "
        "ON CONFLICT (id) DO UPDATE SET _created_by = 'alice-id'",
    ],
)
def test_the_columns_cannot_be_written(con, sql) -> None:
    appdb.stamp(con, BOB)
    appdb.run(con, "INSERT INTO messages (body) VALUES ('hi')", None)
    with pytest.raises(SqlError) as err:
        appdb.run(con, sql, None)
    assert err.value.code == "sql_not_allowed"
    assert "is set by the platform" in err.value.message
    assert _stamps(con) == [(BOB, BOB, 1, 1)]


def test_the_actor_table_is_not_readable_or_writable(con) -> None:
    appdb.stamp(con, BOB)
    with pytest.raises(SqlError, match="prohibited"):
        appdb.get_all(con, f"SELECT * FROM temp.{appdb.ACTOR_TABLE}", None)
    with pytest.raises(SqlError, match="not allowed"):
        appdb.run(con, f"UPDATE temp.{appdb.ACTOR_TABLE} SET user_id = 'alice-id'", None)


def test_tables_without_the_columns_still_take_writes(tmp_path) -> None:
    d = tmp_path / "inst"
    d.mkdir()
    fd = os.open(d, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with appdb.connect(fd) as c:
            c.execute("CREATE TABLE legacy (id INTEGER PRIMARY KEY, v TEXT)")
            appdb.stamp(c, BOB)
            assert appdb.run(c, "INSERT INTO legacy (v) VALUES ('x')", None)["changes"] == 1
    finally:
        os.close(fd)


def test_stamping_twice_on_one_connection_keeps_one_set_of_triggers(con) -> None:
    appdb.stamp(con, ALICE)
    appdb.stamp(con, BOB)
    appdb.run(con, "INSERT INTO messages (body) VALUES ('hi')", None)
    assert _stamps(con) == [(BOB, BOB, 1, 1)]
    names = con.execute("SELECT name FROM temp.sqlite_master WHERE type = 'trigger'").fetchall()
    assert len(names) == 4


# --- through the API ----------------------------------------------------------------------


@pytest.fixture
async def inst(world: World) -> dict:
    files = {
        **PACKAGE_FILES,
        "schema.sql": SCHEMA,
        "actions/send.sql": "INSERT INTO messages (body) VALUES (:body);\n",
    }
    write_package(world.family_root / "Apps" / "hello", files=files)
    r = await world.client.post(
        f"{API}/apps", json={"source_path": "/spaces/family/Apps/hello"},
        headers=world.headers["alice"],
    )  # fmt: skip
    r = await world.client.post(
        f"{API}/spaces/{world.family['id']}/instances",
        json={"app_id": r.json()["app"]["id"]}, headers=world.headers["alice"],
    )  # fmt: skip
    instance = r.json()
    r = await world.client.post(
        f"{API}/apps/instances/{instance['id']}/migrate", headers=world.headers["alice"]
    )
    assert r.json()["status"] == "applied", r.text
    return instance


async def test_ui_and_agent_writes_carry_the_verified_user(world, inst) -> None:
    base = f"{API}/apps/instances/{inst['id']}/rpc"
    r = await world.client.post(
        base, json={"op": "action", "name": "send", "params": {"body": "from bob"}},
        headers=world.headers["bob"],
    )  # fmt: skip
    assert r.status_code == 200, r.text
    alice_id = str(world.users["alice"]["id"])
    r = await world.client.post(
        base,
        json={"op": "run", "sql": "INSERT INTO messages (body, _created_by) VALUES ('agent', ?)",
              "params": [str(world.users["bob"]["id"])]},
        headers=await world.agent("alice"),
    )  # fmt: skip
    assert r.status_code == 200, r.text
    r = await world.client.post(
        base, json={"op": "getAll", "sql": "SELECT body, _created_by FROM messages ORDER BY id"},
        headers=world.headers["carol"],
    )  # fmt: skip
    assert r.json()["rows"] == [
        {"body": "from bob", "_created_by": str(world.users["bob"]["id"])},
        {"body": "agent", "_created_by": alice_id},
    ]
    r = await world.client.post(
        base, json={"op": "run", "sql": "UPDATE messages SET _created_by = ?", "params": [alice_id]},
        headers=world.headers["bob"],
    )  # fmt: skip
    assert r.status_code == 422
    assert r.json()["detail"] == "sql_not_allowed"
