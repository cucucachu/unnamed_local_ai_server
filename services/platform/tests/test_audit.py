"""The audit log: `audit_events`, `app.core.audit` and `GET /api/platform/audit`.

World (`tests/files_world.py`): alice owns /spaces/family, bob edits it,
carol views it, dave isn't a member. The `hello` app is installed there.
"""

from __future__ import annotations

import psycopg
import pytest

from app.core import audit
from tests.app_packages import FILES as PACKAGE_FILES
from tests.app_packages import write_package
from tests.files_world import API, World
from tests.helpers import create_user, identity, login, sql

SCHEMA = "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL);\n"
ADD_ITEM = "INSERT INTO items (name) VALUES (:name);\n"


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
    instance = r.json()
    r = await world.client.post(
        f"{API}/apps/instances/{instance['id']}/migrate", headers=world.headers["alice"]
    )
    assert r.json()["status"] == "applied", r.text
    return instance


async def _rpc(world: World, inst: dict, user: str, body: dict, *, agent: bool = False):
    headers = await world.agent(user) if agent else world.headers[user]
    return await world.client.post(
        f"{API}/apps/instances/{inst['id']}/rpc", json=body, headers=headers
    )


async def _events(world: World, user: str = "carol", **params) -> list[dict]:
    params.setdefault("space_id", world.family["id"])
    r = await world.client.get(f"{API}/audit", params=params, headers=world.headers[user])
    assert r.status_code == 200, r.text
    return r.json()["events"]


async def test_app_data_writes_are_attributed_to_the_user_or_their_agent(world, inst) -> None:
    r = await _rpc(
        world, inst, "bob", {"op": "run", "sql": "INSERT INTO items (name) VALUES ('Milk')"}
    )
    assert r.status_code == 200, r.text
    r = await _rpc(
        world,
        inst,
        "alice",
        {"op": "action", "name": "addItem", "params": {"name": "Eggs"}},
        agent=True,
    )
    assert r.status_code == 200, r.text
    writes = await _events(world, kind="app_data.write")
    assert [(e["actor_kind"], e["actor_user_id"], e["summary"]) for e in writes] == [
        ("agent", str(world.users["alice"]["id"]), "action addItem in hello: 1 row changed"),
        ("user", str(world.users["bob"]["id"]), "run in hello: 1 row changed"),
    ]
    agent_event, user_event = writes
    assert agent_event["thread_id"] is not None and user_event["thread_id"] is None
    assert (agent_event["target_type"], agent_event["target_id"]) == ("app_instance", inst["id"])
    assert agent_event["detail"] == {
        "instance_id": inst["id"], "app": "hello", "op": "action", "action": "addItem",
        "params": {"name": "Eggs"}, "changes": 1, "last_insert_row_id": 2,
    }  # fmt: skip
    assert user_event["detail"]["sql"] == "INSERT INTO items (name) VALUES ('Milk')"


async def test_writes_that_change_nothing_and_reads_are_not_logged(world, inst) -> None:
    await _rpc(world, inst, "bob", {"op": "run", "sql": "DELETE FROM items WHERE id = 99"})
    await _rpc(world, inst, "bob", {"op": "getAll", "sql": "SELECT * FROM items"})
    assert await _events(world, kind="app_data.write") == []


async def test_a_write_whose_event_cannot_be_recorded_is_rolled_back(
    world, inst, monkeypatch
) -> None:
    async def broken(*_a, **_k):
        raise RuntimeError("audit down")

    with monkeypatch.context() as m, pytest.raises(RuntimeError, match="audit down"):
        m.setattr(audit, "record", broken)
        await _rpc(
            world, inst, "bob", {"op": "run", "sql": "INSERT INTO items (name) VALUES ('Milk')"}
        )
    rows = await _rpc(world, inst, "carol", {"op": "getAll", "sql": "SELECT name FROM items"})
    assert rows.json()["rows"] == []


async def test_installs_migrations_and_membership_are_logged(world, inst) -> None:
    r = await world.client.patch(
        f"{API}/spaces/{world.family['id']}/members/{world.users['carol']['id']}",
        json={"role": "editor"}, headers=world.headers["alice"],
    )  # fmt: skip
    assert r.status_code == 200, r.text
    kinds = [(e["kind"], e["actor_user_id"]) for e in await _events(world)]
    alice = str(world.users["alice"]["id"])
    assert kinds[:3] == [
        ("space.member_role_changed", alice),
        ("app_data.migration", alice),
        ("app.installed", alice),
    ]
    assert ("space.member_added", alice) in kinds
    migration = (await _events(world, kind="app_data.migration"))[0]
    assert migration["summary"] == "migration applied in hello (1 additive)"
    assert migration["detail"]["steps"] == [
        {"kind": "additive", "op": "create_table", "table": "items"}
    ]


async def test_reading_needs_membership_and_admins_see_everything(world, inst) -> None:
    r = await world.client.get(
        f"{API}/audit", params={"space_id": world.family["id"]}, headers=world.headers["dave"]
    )
    assert r.status_code == 404
    r = await world.client.get(f"{API}/audit", headers=world.headers["alice"])
    assert (r.status_code, r.json()) == (403, {"detail": "admin_required"})
    await create_user(world.platform, "root", role="admin")
    admin = await identity(world.platform, await login(world.platform, "root"))
    r = await world.client.get(f"{API}/audit", headers=admin)
    assert r.status_code == 200 and r.json()["events"]


async def test_pages_newest_first(world, inst) -> None:
    for name in "abc":
        await _rpc(
            world, inst, "bob", {"op": "action", "name": "addItem", "params": {"name": name}}
        )
    r = await world.client.get(
        f"{API}/audit", params={"space_id": world.family["id"], "kind": "app_data.write", "limit": 2},
        headers=world.headers["bob"],
    )  # fmt: skip
    page = r.json()
    assert [e["detail"]["params"]["name"] for e in page["events"]] == ["c", "b"]
    older = await _events(world, "bob", kind="app_data.write", before=page["next_before"])
    assert [e["detail"]["params"]["name"] for e in older] == ["a"]


async def test_the_table_is_append_only(world, inst) -> None:
    for statement in ("UPDATE audit_events SET summary = 'x'", "DELETE FROM audit_events",
                      "TRUNCATE audit_events"):  # fmt: skip
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            sql(world.platform, statement)
    assert sql(world.platform, "SELECT count(*) AS n FROM audit_events")[0]["n"] > 0


def test_detail_is_capped() -> None:
    assert audit.capped_detail({"sql": "x" * 5000})["sql"].endswith("(5000 chars)")
    huge = {f"k{i}": "y" * 1000 for i in range(100)}
    capped = audit.capped_detail(huge)
    assert len(str(capped)) < 20_000
    assert audit.capped_detail({f"k{i}": "z" for i in range(5000)}) == {
        "truncated": "detail was larger than 16 KiB"
    }
