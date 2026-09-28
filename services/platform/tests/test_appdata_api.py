"""App data: `/api/platform/apps/instances/{id}/{rpc,migrate,migrations...}` and `/ws/platform/events`.

World (`tests/files_world.py`): alice owns /spaces/family, bob edits it,
carol views it, dave isn't a member. The `hello` app lives in
/spaces/family/Apps/hello and is installed there (tracks the working copy).
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.core import appdata, appdb, events
from tests.app_packages import FILES as PACKAGE_FILES
from tests.app_packages import write_package
from tests.files_world import API, World
from tests.helpers import sql
from tests.ws import WsClient, WsClosed

SCHEMA = """CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  done INTEGER NOT NULL DEFAULT 0
);
"""
ADD_ITEM = "INSERT INTO items (name) VALUES (:name);\nSELECT id, name FROM items WHERE id = last_insert_rowid();\n"


@pytest.fixture
async def inst(world: World) -> dict:
    files = {**PACKAGE_FILES, "schema.sql": SCHEMA, "actions/addItem.sql": ADD_ITEM}
    write_package(world.family_root / "Apps" / "hello", files=files)
    r = await world.client.post(
        f"{API}/apps", json={"source_path": "/spaces/family/Apps/hello"},
        headers=world.headers["alice"],
    )  # fmt: skip
    assert r.status_code == 201, r.text
    r = await world.client.post(
        f"{API}/spaces/{world.family['id']}/instances",
        json={"app_id": r.json()["app"]["id"]}, headers=world.headers["alice"],
    )  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


def _base(inst: dict) -> str:
    return f"{API}/apps/instances/{inst['id']}"


def _dir(world: World, inst: dict) -> Path:
    return world.platform.app.state.storage.instance_dir(world.family["id"], inst["id"])


def _schema(world: World, text: str) -> None:
    (world.family_root / "Apps" / "hello" / "schema.sql").write_text(text)


async def _rpc(world: World, inst: dict, user: str, body: dict, *, agent: bool = False):
    headers = await world.agent(user) if agent else world.headers[user]
    return await world.client.post(f"{_base(inst)}/rpc", json=body, headers=headers)


async def _migrate(world: World, inst: dict, user: str = "alice"):
    return await world.client.post(f"{_base(inst)}/migrate", headers=world.headers[user])


async def _ready(world: World, inst: dict) -> None:
    r = await _migrate(world, inst)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"


# --- migrations -----------------------------------------------------------------------------


async def test_first_migrate_creates_the_schema(world, inst) -> None:
    r = await _migrate(world, inst)
    assert r.status_code == 200, r.text
    m = r.json()
    assert (m["status"], m["needs_approval"], m["summary"], m["snapshot"]) == (
        "applied", False, {"additive": 1, "safe": 0, "destructive": 0}, None,
    )  # fmt: skip
    assert m["steps"][0]["op"] == "create_table"
    assert m["created_by"] == m["decided_by"] == str(world.users["alice"]["id"])
    again = (await _migrate(world, inst)).json()
    assert (again["id"], again["status"], again["steps"]) == (None, "up_to_date", [])
    history = await world.client.get(f"{_base(inst)}/migrations", headers=world.headers["carol"])
    assert [h["id"] for h in history.json()["migrations"]] == [m["id"]]
    live = sqlite3.connect(_dir(world, inst) / "data.sqlite")
    assert live.execute("PRAGMA journal_mode").fetchone() == ("wal",)
    live.close()


async def test_additive_change_applies_with_a_snapshot(world, inst) -> None:
    await _ready(world, inst)
    await _rpc(world, inst, "bob", {"op": "run", "sql": "INSERT INTO items (name) VALUES ('Milk')"})
    _schema(world, SCHEMA + "CREATE INDEX items_name ON items (name);\n"
            "CREATE TABLE tags (id INTEGER PRIMARY KEY, label TEXT);\n")  # fmt: skip
    m = (await _migrate(world, inst, "bob")).json()
    assert (m["status"], m["summary"]) == ("applied", {"additive": 2, "safe": 0, "destructive": 0})
    assert m["snapshot"].endswith(f"-{m['id']}.sqlite")
    snap = sqlite3.connect(_dir(world, inst) / "snapshots" / m["snapshot"])
    assert snap.execute("SELECT name FROM items").fetchall() == [("Milk",)]
    assert snap.execute("SELECT count(*) FROM sqlite_master WHERE name = 'tags'").fetchone() == (0,)
    snap.close()


async def test_destructive_change_waits_for_approval(world, inst) -> None:
    await _ready(world, inst)
    await _rpc(
        world, inst, "alice", {"op": "run", "sql": "INSERT INTO items (name) VALUES ('Milk')"}
    )
    _schema(world, "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL);\n")
    r = await _migrate(world, inst, "bob")
    m = r.json()
    assert (m["status"], m["needs_approval"], m["summary"]["destructive"]) == ("pending", True, 1)
    assert m["steps"][0]["reason"] == "drops column done and its data"
    columns = await _rpc(world, inst, "carol", {"op": "getAll", "sql": "PRAGMA table_info(items)"})
    assert [c["name"] for c in columns.json()["rows"]] == ["id", "name", "done"]

    approve = f"{_base(inst)}/migrations/{m['id']}/approve"
    assert (await world.client.post(approve, headers=world.headers["carol"])).status_code == 403
    assert (await world.client.post(approve, headers=world.headers["dave"])).status_code == 404
    r = await world.client.post(approve, headers=world.headers["alice"])
    assert r.status_code == 200, r.text
    done = r.json()
    assert (done["id"], done["status"], done["decided_by"]) == (
        m["id"], "applied", str(world.users["alice"]["id"]),
    )  # fmt: skip
    assert (_dir(world, inst) / "snapshots" / done["snapshot"]).is_file()
    rows = await _rpc(world, inst, "carol", {"op": "getAll", "sql": "SELECT * FROM items"})
    assert rows.json()["rows"] == [{"id": 1, "name": "Milk"}]
    r = await world.client.post(approve, headers=world.headers["alice"])
    assert (r.status_code, r.json()) == (409, {"detail": "migration_not_pending"})


async def test_a_newer_plan_supersedes_and_a_changed_database_is_refused(world, inst) -> None:
    await _ready(world, inst)
    _schema(world, "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL);\n")
    first = (await _migrate(world, inst)).json()
    _schema(world, "CREATE TABLE items (id INTEGER PRIMARY KEY, done INTEGER);\n")
    second = (await _migrate(world, inst)).json()
    statuses = sql(
        world.platform, "SELECT id::text, status FROM app_migrations ORDER BY created_at"
    )
    assert [s["status"] for s in statuses][-2:] == ["superseded", "pending"]
    r = await world.client.post(
        f"{_base(inst)}/migrations/{first['id']}/approve", headers=world.headers["alice"]
    )
    assert (r.status_code, r.json()) == (409, {"detail": "migration_not_pending"})

    # The database moved on behind the pending plan (another migration applied it).
    live = sqlite3.connect(_dir(world, inst) / "data.sqlite")
    live.execute("ALTER TABLE items DROP COLUMN name")
    live.commit()
    live.close()
    r = await world.client.post(
        f"{_base(inst)}/migrations/{second['id']}/approve", headers=world.headers["alice"]
    )
    assert (r.status_code, r.json()) == (409, {"detail": "plan_changed"})
    (row,) = sql(world.platform, "SELECT status FROM app_migrations WHERE id = %s", (second["id"],))
    assert row["status"] == "superseded"


async def test_reject(world, inst) -> None:
    await _ready(world, inst)
    _schema(world, "")
    m = (await _migrate(world, inst)).json()
    assert m["status"] == "pending"
    r = await world.client.post(
        f"{_base(inst)}/migrations/{m['id']}/reject", headers=world.headers["bob"]
    )
    assert (r.status_code, r.json()["status"]) == (200, "rejected")
    rows = await _rpc(world, inst, "carol", {"op": "getAll", "sql": "SELECT * FROM items"})
    assert rows.status_code == 200


async def test_failed_apply_rolls_back_and_keeps_the_snapshot(world, inst) -> None:
    await _ready(world, inst)
    await _rpc(
        world, inst, "alice", {"op": "run", "sql": "INSERT INTO items (name) VALUES ('Milk')"}
    )
    _schema(
        world,
        SCHEMA.replace(
            "done INTEGER NOT NULL DEFAULT 0",
            "done INTEGER NOT NULL DEFAULT 0,\n  qty INTEGER NOT NULL",
        ),
    )
    m = (await _migrate(world, inst)).json()
    assert m["status"] == "pending"
    r = await world.client.post(
        f"{_base(inst)}/migrations/{m['id']}/approve", headers=world.headers["alice"]
    )
    assert r.status_code == 422
    body = r.json()
    assert body["detail"] == "migration_failed"
    failed = body["migration"]
    assert failed["status"] == "failed" and "NOT NULL" in failed["error"]
    assert (_dir(world, inst) / "snapshots" / failed["snapshot"]).is_file()
    rows = await _rpc(world, inst, "carol", {"op": "getAll", "sql": "SELECT * FROM items"})
    assert rows.json()["rows"] == [{"id": 1, "name": "Milk", "done": 0}]


@pytest.mark.parametrize(
    ("schema", "fragment"),
    [
        ("CREATE TABLE t (a); INSERT INTO t VALUES (1);", "not authorized"),
        ("ATTACH '/etc/passwd' AS x;", "not authorized"),
        ("CREATE TABLE _homeai_x (a);", "reserved"),
        (None, "missing"),
    ],
)
async def test_bad_schema_is_422_invalid_schema(world, inst, schema, fragment) -> None:
    path = world.family_root / "Apps" / "hello" / "schema.sql"
    if schema is None:
        path.unlink()
    else:
        path.write_text(schema)
    r = await _migrate(world, inst)
    assert r.status_code == 422
    body = r.json()
    assert body["detail"] == "invalid_schema"
    ((diag),) = body["diagnostics"]
    assert diag["file"] == "schema.sql" and fragment in diag["message"]


async def test_symlinked_schema_is_not_followed(world, inst, tmp_path) -> None:
    secret = tmp_path / "secret.sql"
    secret.write_text(SCHEMA)
    path = world.family_root / "Apps" / "hello" / "schema.sql"
    path.unlink()
    path.symlink_to(secret)
    r = await _migrate(world, inst)
    assert (r.status_code, r.json()["detail"]) == (422, "invalid_schema")


@pytest.mark.parametrize(
    ("user", "status"), [("alice", 200), ("bob", 200), ("carol", 403), ("dave", 404)]
)
async def test_migrate_needs_write(world, inst, user, status) -> None:
    assert (await _migrate(world, inst, user)).status_code == status


# --- RPC ------------------------------------------------------------------------------------


async def test_rpc_ops(world, inst) -> None:
    await _ready(world, inst)
    r = await _rpc(world, inst, "bob", {"op": "run", "sql": "INSERT INTO items (name) VALUES (?)",
                                        "params": ["Milk"]})  # fmt: skip
    assert r.json() == {"changes": 1, "lastInsertRowId": 1}
    r = await _rpc(world, inst, "bob", {
        "op": "transaction",
        "statements": [
            {"sql": "INSERT INTO items (name) VALUES ($n)", "params": {"$n": "Eggs"}},
            {"sql": "UPDATE items SET done = 1 WHERE name = :n", "params": {"n": "Milk"}},
        ],
    })  # fmt: skip
    assert r.json() == {"results": [{"changes": 1, "lastInsertRowId": 2},
                                    {"changes": 1, "lastInsertRowId": 2}]}  # fmt: skip
    r = await _rpc(
        world, inst, "carol", {"op": "getAll", "sql": "SELECT name, done FROM items ORDER BY id"}
    )
    assert r.json() == {"rows": [{"name": "Milk", "done": 1}, {"name": "Eggs", "done": 0}]}
    r = await _rpc(
        world, inst, "carol", {"op": "getFirst", "sql": "SELECT count(*) AS n FROM items"}
    )
    assert r.json() == {"row": {"n": 2}}
    r = await _rpc(world, inst, "carol", {"op": "getFirst", "sql": "SELECT * FROM items WHERE 0"})
    assert r.json() == {"row": None}


async def test_action_with_params(world, inst) -> None:
    await _ready(world, inst)
    r = await _rpc(
        world, inst, "bob", {"op": "action", "name": "addItem", "params": {"name": "Tea"}}
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"changes": 1, "lastInsertRowId": 1, "rows": [{"id": 1, "name": "Tea"}]}
    r = await _rpc(world, inst, "bob", {"op": "action", "name": "addItem", "params": {}})
    assert r.status_code == 422
    assert (r.json()["detail"], r.json()["index"]) == ("sql_error", 0)
    r = await _rpc(world, inst, "bob", {"op": "action", "name": "nope"})
    assert (r.status_code, r.json()) == (404, {"detail": "unknown_action"})
    r = await _rpc(world, inst, "bob", {"op": "action", "name": "../schema"})
    assert (r.status_code, r.json()) == (422, {"detail": "invalid_action"})


async def test_actions_are_transactional(world, inst) -> None:
    await _ready(world, inst)
    (world.family_root / "Apps" / "hello" / "actions" / "twoItems.sql").write_text(
        "INSERT INTO items (name) VALUES (:a);\nINSERT INTO items (name) VALUES (:b);\n"
    )
    r = await _rpc(world, inst, "bob", {"op": "action", "name": "twoItems",
                                        "params": {"a": "x", "b": None}})  # fmt: skip
    assert (r.status_code, r.json()["detail"], r.json()["index"]) == (422, "sql_error", 1)
    r = await _rpc(world, inst, "bob", {"op": "getAll", "sql": "SELECT * FROM items"})
    assert r.json() == {"rows": []}


async def test_symlinked_action_is_not_followed(world, inst, tmp_path) -> None:
    await _ready(world, inst)
    bait = tmp_path / "bait.sql"
    bait.write_text("INSERT INTO items (name) VALUES ('evil');")
    (world.family_root / "Apps" / "hello" / "actions" / "evil.sql").symlink_to(bait)
    r = await _rpc(world, inst, "bob", {"op": "action", "name": "evil"})
    assert (r.status_code, r.json()) == (404, {"detail": "unknown_action"})


@pytest.mark.parametrize("agent", [False, True])
@pytest.mark.parametrize(
    ("user", "read", "write"),
    [("alice", 200, 200), ("bob", 200, 200), ("carol", 200, 403), ("dave", 404, 404)],
)
async def test_role_matrix(world, inst, user, read, write, agent) -> None:
    await _ready(world, inst)
    get = {"op": "getAll", "sql": "SELECT * FROM items"}
    assert (await _rpc(world, inst, user, get, agent=agent)).status_code == read
    for body in [
        {"op": "run", "sql": "INSERT INTO items (name) VALUES ('x')"},
        {"op": "transaction", "statements": [{"sql": "DELETE FROM items"}]},
        {"op": "action", "name": "addItem", "params": {"name": "x"}},
    ]:
        r = await _rpc(world, inst, user, body, agent=agent)
        assert r.status_code == write, (body, r.text)
        if write != 200:
            assert r.json()["detail"] == ("insufficient_role" if write == 403 else "not_found")


async def test_viewer_cannot_write_through_a_read(world, inst) -> None:
    await _ready(world, inst)
    for s in ["INSERT INTO items (name) VALUES ('x')", "DELETE FROM items",
              "UPDATE items SET done = 1", "INSERT INTO items (name) VALUES ('x') RETURNING id"]:  # fmt: skip
        r = await _rpc(world, inst, "carol", {"op": "getAll", "sql": s})
        assert (r.status_code, r.json()["detail"]) == (422, "sql_not_allowed"), s


async def test_unknown_or_uninstalled_instance_is_404(world, inst) -> None:
    r = await world.client.post(
        f"{API}/apps/instances/{uuid4()}/rpc", json={"op": "getAll", "sql": "SELECT 1"},
        headers=world.headers["alice"],
    )  # fmt: skip
    assert (r.status_code, r.json()) == (404, {"detail": "not_found"})
    r = await world.client.delete(
        f"{API}/spaces/{world.family['id']}/instances/{inst['id']}", headers=world.headers["alice"]
    )
    assert r.status_code == 204
    r = await _rpc(world, inst, "alice", {"op": "getAll", "sql": "SELECT 1"})
    assert r.status_code == 404


@pytest.mark.parametrize(
    "sql_text",
    [
        "ATTACH '/etc/passwd' AS x",
        "ATTACH ':memory:' AS x",
        "VACUUM INTO '/tmp/leak.sqlite'",
        "DROP TABLE items",
        "CREATE TABLE t (a)",
        "PRAGMA writable_schema = 1",
        "SELECT load_extension('x')",
        "REINDEX",
    ],
)
async def test_scope_is_this_database_only(world, inst, sql_text) -> None:
    await _ready(world, inst)
    r = await _rpc(world, inst, "alice", {"op": "run", "sql": sql_text})
    assert (r.status_code, r.json()["detail"]) == (422, "sql_not_allowed"), r.text
    assert not Path("/tmp/leak.sqlite").exists()


async def test_invalid_requests(world, inst) -> None:
    await _ready(world, inst)
    for body in [
        {"op": "drop", "sql": "SELECT 1"},
        {"op": "getAll"},
        {"op": "run", "sql": "SELECT ?", "params": [[1]]},
        {"op": "transaction", "statements": []},
    ]:
        r = await _rpc(world, inst, "alice", body)
        assert (r.status_code, r.json()["detail"]) == (422, "invalid_request"), body
    r = await _rpc(world, inst, "alice", {"op": "getAll", "sql": "SELEC 1"})
    assert r.status_code == 422
    assert r.json()["detail"] == "sql_error" and "syntax error" in r.json()["message"]


# --- the read-only copy for exec ------------------------------------------------------------------


async def test_writes_publish_the_read_only_copy(world, inst) -> None:
    await _ready(world, inst)
    await _rpc(world, inst, "bob", {"op": "run", "sql": "INSERT INTO items (name) VALUES ('Milk')"})
    await world.platform.app.state.appdata.flush()
    ro = _dir(world, inst) / "ro" / "data.sqlite"
    assert os.stat(ro).st_mode & 0o777 == 0o444
    reader = sqlite3.connect(f"file:{ro}?mode=ro&immutable=1", uri=True)
    assert reader.execute("SELECT name FROM items").fetchall() == [("Milk",)]
    reader.close()


async def test_publishing_is_debounced(world, inst, monkeypatch) -> None:
    await _ready(world, inst)
    service = world.platform.app.state.appdata
    await service.flush()
    calls = []
    real = appdb.publish
    monkeypatch.setattr(appdb, "publish", lambda con, fd: (calls.append(1), real(con, fd)))
    monkeypatch.setattr(appdata, "PUBLISH_INTERVAL_S", 0.3)
    for i in range(5):
        await _rpc(
            world, inst, "bob", {"op": "run", "sql": f"INSERT INTO items (name) VALUES ('{i}')"}
        )
    await service.flush()
    assert len(calls) == 1
    ro = _dir(world, inst) / "ro" / "data.sqlite"
    reader = sqlite3.connect(f"file:{ro}?mode=ro&immutable=1", uri=True)
    assert reader.execute("SELECT count(*) FROM items").fetchone() == (5,)
    reader.close()


# --- events -------------------------------------------------------------------------------


async def _socket(world: World, headers: dict) -> WsClient:
    ws = WsClient(world.platform.app, "/ws/platform/events", headers)
    await ws.connect()
    return ws


async def test_events_need_a_credential(world) -> None:
    ws = await _socket(world, {})
    with pytest.raises(WsClosed) as e:
        await ws.recv()
    assert e.value.code == 4401
    ws = await _socket(world, {"X-HomeAI-Identity": "forged"})
    with pytest.raises(WsClosed) as e:
        await ws.recv()
    assert e.value.code == 4401


async def test_db_changed_reaches_members_only(world, inst) -> None:
    await _ready(world, inst)
    sockets = {u: await _socket(world, world.headers[u]) for u in ("carol", "dave")}
    sockets["agent"] = await _socket(world, await world.agent("bob"))
    for ws in sockets.values():
        assert await ws.recv() == {"type": "ready"}
    await _rpc(world, inst, "bob", {"op": "run", "sql": "INSERT INTO items (name) VALUES ('x')"})
    want = {"type": "db_changed", "instance_id": inst["id"]}
    assert await sockets["carol"].recv() == want
    assert await sockets["agent"].recv() == want
    with pytest.raises(TimeoutError):
        await sockets["dave"].recv(timeout=0.3)
    # Reads and no-op writes don't announce anything.
    await _rpc(world, inst, "bob", {"op": "getAll", "sql": "SELECT * FROM items"})
    await _rpc(world, inst, "bob", {"op": "run", "sql": "DELETE FROM items WHERE 0"})
    with pytest.raises(TimeoutError):
        await sockets["carol"].recv(timeout=0.3)
    for ws in sockets.values():
        await ws.close()


async def test_subscribers_coalesce_per_key() -> None:
    sub = events.Subscriber(uuid4(), uuid4())
    a, b = uuid4(), uuid4()
    sub.spaces = frozenset({a})
    hub = events.EventHub()
    hub.add(sub)
    i1, i2 = uuid4(), uuid4()
    for _ in range(3):
        hub.db_changed(a, i1)
    hub.db_changed(a, i2)
    hub.db_changed(b, uuid4())
    hub.db_changed(a, i1)
    got = await sub.next(1)
    assert got == [
        {"type": "db_changed", "instance_id": str(i2)},
        {"type": "db_changed", "instance_id": str(i1)},
    ]
    assert await sub.next(0.05) == []
    hub.remove(sub)
    hub.db_changed(a, i1)
    assert await sub.next(0.05) == []


async def test_events_follow_migrations_and_builds(world, inst) -> None:
    ws = await _socket(world, world.headers["carol"])
    assert await ws.recv() == {"type": "ready"}
    await _ready(world, inst)
    assert await ws.recv() == {"type": "db_changed", "instance_id": inst["id"]}
    world.platform.app.state.events.app_built([UUID(world.family["id"])], inst["app_id"], "1.0.1")
    assert await ws.recv() == {"type": "app_built", "app_id": inst["app_id"], "version": "1.0.1"}
    await ws.close()


async def test_membership_and_session_changes_reach_an_open_socket(
    world, inst, monkeypatch
) -> None:
    monkeypatch.setattr(events, "REFRESH_S", 0.2)
    from app.api.external import events as events_api

    monkeypatch.setattr(events_api, "REFRESH_S", 0.2)
    await _ready(world, inst)
    ws = await _socket(world, world.headers["carol"])
    assert await ws.recv() == {"type": "ready"}
    r = await world.client.delete(
        f"{API}/spaces/{world.family['id']}/members/{world.users['carol']['id']}",
        headers=world.headers["alice"],
    )
    assert r.status_code == 204, r.text
    await asyncio.sleep(0.5)
    await _rpc(world, inst, "bob", {"op": "run", "sql": "INSERT INTO items (name) VALUES ('x')"})
    with pytest.raises(TimeoutError):
        await ws.recv(timeout=0.3)
    r = await world.client.post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {world.tokens['carol']}"}
    )
    assert r.status_code in (200, 204), r.text
    with pytest.raises(WsClosed) as e:
        await ws.recv(timeout=2)
    assert e.value.code == 4401


# --- uninstall -----------------------------------------------------------------------------


async def test_uninstall_waits_for_a_running_write_and_later_writes_are_404(world, inst) -> None:
    await _ready(world, inst)
    appdata_ = world.platform.app.state.appdata
    uninstall = f"{API}/spaces/{world.family['id']}/instances/{inst['id']}"
    lock = appdata_._lock(UUID(inst["id"]))

    await lock.acquire()
    try:
        task = asyncio.create_task(world.client.delete(uninstall, headers=world.headers["alice"]))
        await asyncio.sleep(0.2)
        assert not task.done()
        assert _dir(world, inst).is_dir()
    finally:
        lock.release()
    assert (await task).status_code == 204
    assert not _dir(world, inst).exists()

    for body in (
        {"op": "run", "sql": "INSERT INTO items (name) VALUES ('late')"},
        {"op": "getAll", "sql": "SELECT * FROM items"},
    ):
        assert (await _rpc(world, inst, "bob", body)).status_code == 404
    assert (await _migrate(world, inst)).status_code == 404
    assert not _dir(world, inst).exists()


async def test_a_write_that_passed_its_check_before_uninstall_doesnt_recreate_the_dir(
    world, inst
) -> None:
    await _ready(world, inst)
    appdata_ = world.platform.app.state.appdata
    lock = appdata_._lock(UUID(inst["id"]))

    await lock.acquire()
    try:
        write = asyncio.create_task(
            _rpc(world, inst, "bob", {"op": "run", "sql": "INSERT INTO items (name) VALUES ('x')"})
        )
        await asyncio.sleep(0.2)
        assert not write.done()
        # What an uninstall that didn't take the lock would do.
        sql(world.platform, "UPDATE app_instances SET uninstalled_at = now() WHERE id = %s",
            (inst["id"],))  # fmt: skip
        world.platform.app.state.storage.trash_instance(
            UUID(world.family["id"]), world.family["gid"], UUID(inst["id"]), "stamp"
        )
    finally:
        lock.release()

    response = await write
    assert (response.status_code, response.json()["detail"]) == (404, "not_found")
    assert not _dir(world, inst).exists()
