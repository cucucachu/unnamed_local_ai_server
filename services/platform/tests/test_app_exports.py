"""M14-04: cross-app exports/reads, merged views, and exported actions.

World (`tests/files_world.py`): alice owns /spaces/family, bob edits it,
carol views it, dave isn't a member. Calendar is installed in alice's
personal space and in family; planner is installed in family with a granted
read of calendar's `events` export.
"""

from __future__ import annotations

from uuid import UUID

from tests.app_packages import manifest, write_package
from tests.files_world import API, World
from tests.test_app_build import FakeBuilder
from tests.test_apps_api import APPS, _install, _register

CAL_SCHEMA = """CREATE TABLE events (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL
);
"""
CAL_ADD = (
    "INSERT INTO events (title) VALUES (:title);\n"
    "SELECT id, title FROM events WHERE id = last_insert_rowid();\n"
)
PLAN_SCHEMA = """CREATE TABLE notes (
  id INTEGER PRIMARY KEY,
  body TEXT NOT NULL
);
"""

UI = {
    "AGENT.md": "# App\n",
    "app/_layout.tsx": "import { Stack } from 'expo-router';\nexport default Stack;\n",
    "app/index.tsx": "export default function Index() { return null; }\n",
}


def _cal_doc(slug: str = "calendar") -> dict:
    doc = manifest(slug, name="Calendar")
    doc["homeai"]["exports"] = [
        {"name": "events", "version": "1", "tables": ["events"], "actions": ["addEvent"]}
    ]
    return doc


def _plan_doc(*, reads: bool = True) -> dict:
    doc = manifest("planner", name="Planner")
    if reads:
        doc["homeai"]["reads"] = [{"app": "calendar", "export": "events", "version": "1"}]
    return doc


def _write_calendar(folder) -> None:
    write_package(
        folder,
        doc=_cal_doc(folder.name),
        files={**UI, "schema.sql": CAL_SCHEMA, "actions/addEvent.sql": CAL_ADD},
    )


def _write_planner(folder, *, reads: bool = True) -> None:
    write_package(
        folder,
        doc=_plan_doc(reads=reads),
        files={**UI, "schema.sql": PLAN_SCHEMA},
    )


def _rpc_url(instance_id: str) -> str:
    return f"{API}/apps/instances/{instance_id}/rpc"


async def _rpc(world: World, instance_id: str, user: str, body: dict):
    return await world.client.post(_rpc_url(instance_id), json=body, headers=world.headers[user])


async def _migrate(world: World, instance_id: str, user: str = "alice"):
    return await world.client.post(
        f"{API}/apps/instances/{instance_id}/migrate", headers=world.headers[user]
    )


async def _setup(world: World) -> dict[str, dict]:
    """Calendar in personal + family; planner in family (granted reads, working)."""
    _write_calendar(world.home("alice") / "Apps" / "calendar")
    _write_calendar(world.family_root / "Apps" / "calendar")
    _write_planner(world.family_root / "Apps" / "planner")
    personal = await _register(world, world.headers["alice"], "/personal/Apps/calendar")
    family_cal_app = await _register(world, world.headers["alice"], "/spaces/family/Apps/calendar")
    family_plan_app = await _register(world, world.headers["alice"], "/spaces/family/Apps/planner")
    assert personal.status_code == 201, personal.text
    assert family_cal_app.status_code == 201, family_cal_app.text
    assert family_plan_app.status_code == 201, family_plan_app.text
    personal_app = personal.json()["app"]
    family_cal = family_cal_app.json()["app"]
    family_plan = family_plan_app.json()["app"]
    personal_cal = (
        await _install(
            world, world.headers["alice"], world.personal("alice")["id"], personal_app["id"]
        )
    ).json()
    family_cal_inst = (
        await _install(world, world.headers["alice"], world.family["id"], family_cal["id"])
    ).json()
    planner = (
        await _install(world, world.headers["alice"], world.family["id"], family_plan["id"])
    ).json()
    for inst in (personal_cal, family_cal_inst, planner):
        r = await _migrate(world, inst["id"])
        assert r.status_code == 200 and r.json()["status"] == "applied", r.text
    add = await _rpc(
        world,
        personal_cal["id"],
        "alice",
        {"op": "action", "name": "addEvent", "params": {"title": "Personal"}},
    )
    assert add.status_code == 200, add.text
    add = await _rpc(
        world,
        family_cal_inst["id"],
        "alice",
        {"op": "action", "name": "addEvent", "params": {"title": "Family"}},
    )
    assert add.status_code == 200, add.text
    return {
        "personal_cal": personal_cal,
        "family_cal": family_cal_inst,
        "planner": planner,
        "planner_app": family_plan,
    }


async def test_planner_reads_both_calendars_with_space(world: World) -> None:
    ids = await _setup(world)
    r = await _rpc(
        world,
        ids["planner"]["id"],
        "alice",
        {"op": "getAll", "sql": "SELECT title, _space FROM calendar_events ORDER BY title"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [
        {"title": "Family", "_space": "/spaces/family"},
        {"title": "Personal", "_space": "/personal"},
    ]
    assert ids["planner"]["granted_reads"] == [
        {"app": "calendar", "export": "events", "version": "1"}
    ]


async def test_planner_cannot_write_attached_calendar(world: World) -> None:
    ids = await _setup(world)
    planner, family_cal = ids["planner"]["id"], ids["family_cal"]["id"]
    denied = await _rpc(
        world,
        planner,
        "alice",
        {
            "op": "run",
            "sql": "INSERT INTO calendar_events (title, _space) VALUES ('x', '/spaces/family')",
        },
    )
    assert denied.status_code == 422, denied.text
    assert denied.json()["detail"] == "sql_not_allowed"
    schema = appdb_schema(ids["family_cal"]["id"])
    attached = await _rpc(
        world,
        planner,
        "alice",
        {"op": "run", "sql": f"INSERT INTO {schema}.events (title) VALUES ('x')"},
    )
    assert attached.status_code == 422
    assert attached.json()["detail"] == "sql_not_allowed"
    own = await _rpc(
        world,
        family_cal,
        "alice",
        {"op": "run", "sql": "INSERT INTO events (title) VALUES ('Direct')"},
    )
    assert own.status_code == 200, own.text
    assert own.json()["changes"] == 1


def appdb_schema(instance_id: str) -> str:
    from app.core import appdb

    return appdb.attach_schema_name(UUID(instance_id))


async def test_ungranted_planner_cannot_see_calendar(world: World) -> None:
    await _setup(world)
    write_package(
        world.family_root / "Apps" / "plain",
        doc=manifest("plain", name="Plain"),
        files={**UI, "schema.sql": PLAN_SCHEMA},
    )
    app = (await _register(world, world.headers["alice"], "/spaces/family/Apps/plain")).json()[
        "app"
    ]
    inst = (await _install(world, world.headers["alice"], world.family["id"], app["id"])).json()
    r = await _migrate(world, inst["id"])
    assert r.status_code == 200, r.text
    missing = await _rpc(
        world,
        inst["id"],
        "alice",
        {"op": "getAll", "sql": "SELECT * FROM calendar_events"},
    )
    assert missing.status_code == 422, missing.text
    assert missing.json()["detail"] == "sql_error"


async def test_reads_added_after_install_are_granted_by_the_build(world: World) -> None:
    await _setup(world)
    world.platform.app.state.builder = FakeBuilder(world.platform.app.state.builds.root)
    folder = world.family_root / "Apps" / "later"
    write_package(
        folder, doc=manifest("later", name="Later"), files={**UI, "schema.sql": PLAN_SCHEMA}
    )
    app = (await _register(world, world.headers["alice"], "/spaces/family/Apps/later")).json()[
        "app"
    ]
    inst = (await _install(world, world.headers["alice"], world.family["id"], app["id"])).json()
    assert inst["granted_reads"] == []

    doc = manifest("later", name="Later")
    doc["homeai"]["reads"] = [{"app": "calendar", "export": "events", "version": "1"}]
    write_package(folder, doc=doc, files={**UI, "schema.sql": PLAN_SCHEMA})
    built = await world.client.post(f"{APPS}/{app['id']}/build", headers=world.headers["alice"])
    assert built.status_code == 200 and built.json()["ok"], built.text

    r = await _rpc(
        world,
        inst["id"],
        "alice",
        {"op": "getAll", "sql": "SELECT title FROM calendar_events ORDER BY title"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == [{"title": "Family"}, {"title": "Personal"}]


async def test_viewer_reads_merged_view_but_cannot_export_action(world: World) -> None:
    ids = await _setup(world)
    planner, family_cal = ids["planner"]["id"], ids["family_cal"]["id"]
    seen = await _rpc(
        world,
        planner,
        "carol",
        {"op": "getAll", "sql": "SELECT title, _space FROM calendar_events ORDER BY title"},
    )
    assert seen.status_code == 200, seen.text
    assert seen.json()["rows"] == [{"title": "Family", "_space": "/spaces/family"}]
    write = await _rpc(
        world,
        planner,
        "carol",
        {
            "op": "exportAction",
            "instance": family_cal,
            "export": "events",
            "name": "addEvent",
            "params": {"title": "Nope"},
        },
    )
    assert write.status_code == 403, write.text
    assert write.json() == {"detail": "insufficient_role"}
    ok = await _rpc(
        world,
        planner,
        "bob",
        {
            "op": "exportAction",
            "instance": family_cal,
            "export": "events",
            "name": "addEvent",
            "params": {"title": "FromPlanner"},
        },
    )
    assert ok.status_code == 200, ok.text
    rows = await _rpc(
        world, family_cal, "alice", {"op": "getAll", "sql": "SELECT title FROM events ORDER BY id"}
    )
    assert "FromPlanner" in [r["title"] for r in rows.json()["rows"]]


async def test_outsider_does_not_see_planner(world: World) -> None:
    ids = await _setup(world)
    r = await _rpc(
        world,
        ids["planner"]["id"],
        "dave",
        {"op": "getAll", "sql": "SELECT * FROM calendar_events"},
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "not_found"}
    action = await _rpc(
        world,
        ids["planner"]["id"],
        "dave",
        {
            "op": "exportAction",
            "instance": ids["family_cal"]["id"],
            "export": "events",
            "name": "addEvent",
            "params": {"title": "x"},
        },
    )
    assert action.status_code == 404


async def test_ungranted_export_action_is_not_found(world: World) -> None:
    ids = await _setup(world)
    write_package(
        world.family_root / "Apps" / "plain",
        doc=manifest("plain", name="Plain"),
        files={**UI, "schema.sql": PLAN_SCHEMA},
    )
    app = (await _register(world, world.headers["alice"], "/spaces/family/Apps/plain")).json()[
        "app"
    ]
    inst = (await _install(world, world.headers["alice"], world.family["id"], app["id"])).json()
    r = await _rpc(
        world,
        inst["id"],
        "alice",
        {
            "op": "exportAction",
            "instance": ids["family_cal"]["id"],
            "export": "events",
            "name": "addEvent",
            "params": {"title": "x"},
        },
    )
    assert r.status_code == 404
    assert r.json() == {"detail": "unknown_export"}


async def test_pinned_install_requires_granted_reads(world: World) -> None:
    from tests.test_app_sharing import _seed_bundle

    _write_planner(world.home("alice") / "Apps" / "planner")
    app = (await _register(world, world.headers["alice"], "/personal/Apps/planner")).json()["app"]
    _seed_bundle(world, app)
    published = await world.client.post(
        f"{APPS}/{app['id']}/publish",
        json={"space_ids": [world.family["id"]]},
        headers=world.headers["alice"],
    )
    assert published.status_code == 200, published.text
    version_id = published.json()["version"]["id"]
    missing = await world.client.post(
        f"{API}/spaces/{world.family['id']}/instances",
        json={"app_id": app["id"], "tracks": version_id},
        headers=world.headers["bob"],
    )
    assert (missing.status_code, missing.json()) == (422, {"detail": "reads_required"})
    wrong = await world.client.post(
        f"{API}/spaces/{world.family['id']}/instances",
        json={
            "app_id": app["id"],
            "tracks": version_id,
            "granted_reads": [{"app": "other", "export": "events", "version": "1"}],
        },
        headers=world.headers["bob"],
    )
    assert (wrong.status_code, wrong.json()) == (422, {"detail": "reads_mismatch"})
    ok = await world.client.post(
        f"{API}/spaces/{world.family['id']}/instances",
        json={
            "app_id": app["id"],
            "tracks": version_id,
            "granted_reads": [{"app": "calendar", "export": "events", "version": "1"}],
        },
        headers=world.headers["bob"],
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["granted_reads"] == [{"app": "calendar", "export": "events", "version": "1"}]
