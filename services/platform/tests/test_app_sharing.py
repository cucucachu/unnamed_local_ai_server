"""M14-01: publish, catalog, pinned install, update, fork.

World: alice owns /spaces/family, bob edits it, carol views it, dave is out.
Apps start in alice's personal space so catalog listing is how bob/carol see them.
"""

from __future__ import annotations

import json
from uuid import uuid4

from tests.app_packages import manifest, write_package
from tests.files_world import API, World
from tests.helpers import sql
from tests.test_apps_api import APPS, _install, _personal_pkg, _registered

SCHEMA_V1 = """CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);
"""
SCHEMA_V2 = """CREATE TABLE items (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  note TEXT
);
"""
BUNDLE = b"__homeai_define(function(){});"


def _instances(world: World, space_id: str) -> str:
    return f"{API}/spaces/{space_id}/instances"


def _seed_bundle(world: World, app: dict) -> str:
    app_id = app["id"]
    build_id = "a" * 32
    dest = world.platform.app.state.settings.platform_data_dir / "app-bundles" / app_id / build_id
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "app.js").write_bytes(BUNDLE)
    rel = f"app-bundles/{app_id}/{build_id}/app.js"
    sql(
        world.platform,
        "UPDATE app_versions SET bundle_path = %s WHERE app_id = %s AND kind = 'working'",
        (rel, app_id),
    )
    return rel


async def _publish(world: World, headers: dict, app_id: str, space_ids: list[str]):
    return await world.client.post(
        f"{APPS}/{app_id}/publish", json={"space_ids": space_ids}, headers=headers
    )


async def _personal_app(world: World, *, version: str = "1.0.0", schema: str = SCHEMA_V1) -> dict:
    write_package(
        world.home("alice") / "Apps" / "hello",
        doc=manifest(version=version),
        files={
            "AGENT.md": "# Hello\n",
            "schema.sql": schema,
            "app/_layout.tsx": "import { Stack } from 'expo-router';\nexport default Stack;\n",
            "app/index.tsx": "export default function Index() { return null; }\n",
            "actions/addItem.sql": "INSERT INTO items (name) VALUES (:name);\n",
        },
    )
    app = await _registered(world, source="/personal/Apps/hello")
    app["working_version"]["bundle_path"] = _seed_bundle(world, app)
    return app


# --- publish / catalog ----------------------------------------------------------------


async def test_publish_lists_the_app_in_chosen_spaces(world: World) -> None:
    app = await _personal_app(world)
    response = await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["version"]["kind"] == "published"
    assert body["version"]["version"] == "1.0.0"
    assert body["version"]["bundle_path"] == app["working_version"]["bundle_path"]
    assert body["space_ids"] == [world.family["id"]]
    snap = sql(
        world.platform,
        "SELECT source_snapshot FROM app_versions WHERE id = %s",
        (body["version"]["id"],),
    )
    assert snap[0]["source_snapshot"].startswith("app-releases/")
    data_dir = world.platform.app.state.settings.platform_data_dir
    assert (data_dir / snap[0]["source_snapshot"] / "schema.sql").read_text() == SCHEMA_V1

    catalog = await world.client.get(
        f"{API}/spaces/{world.family['id']}/catalog", headers=world.headers["bob"]
    )
    assert catalog.status_code == 200, catalog.text
    (entry,) = catalog.json()["entries"]
    assert entry["app"]["slug"] == "hello"
    assert entry["version"]["id"] == body["version"]["id"]
    assert entry["installed"] is False

    seen = (await world.client.get(APPS, headers=world.headers["bob"])).json()["apps"]
    assert [(a["id"], a["source_path"], a["working_version"]) for a in seen] == [
        (app["id"], None, None)
    ]
    dave = await world.client.get(
        f"{API}/spaces/{world.family['id']}/catalog", headers=world.headers["dave"]
    )
    assert dave.status_code == 404


async def test_publish_needs_a_bundle_and_write_on_source_and_spaces(world: World) -> None:
    _personal_pkg(world, "alice")
    app = await _registered(world, source="/personal/Apps/hello")
    response = await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])
    assert (response.status_code, response.json()) == (422, {"detail": "not_built"})
    _seed_bundle(world, app)
    assert (
        await _publish(world, world.headers["bob"], app["id"], [world.family["id"]])
    ).status_code == 404
    assert (
        await _publish(world, world.headers["alice"], app["id"], [str(uuid4())])
    ).status_code == 404


async def test_publish_refuses_a_duplicate_version(world: World) -> None:
    app = await _personal_app(world)
    first = await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])
    assert first.status_code == 200
    again = await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])
    assert (again.status_code, again.json()) == (409, {"detail": "version_exists"})


# --- install from catalog -------------------------------------------------------------


async def test_family_member_installs_a_published_version(world: World) -> None:
    app = await _personal_app(world)
    published = (
        await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])
    ).json()
    version_id = published["version"]["id"]
    response = await _install(
        world, world.headers["bob"], world.family["id"], app["id"], version_id
    )
    assert response.status_code == 201, response.text
    inst = response.json()
    assert inst["tracks"] == version_id
    assert inst["granted_permissions"] == {}
    assert inst["update"] is None
    listing = await world.client.get(
        f"{API}/spaces/{world.family['id']}/catalog", headers=world.headers["carol"]
    )
    assert listing.json()["entries"][0]["installed"] is True
    viewer = await _install(
        world, world.headers["carol"], world.family["id"], app["id"], version_id
    )
    assert viewer.status_code == 403


async def test_pinned_install_outside_source_needs_the_catalog(world: World) -> None:
    app = await _personal_app(world)
    _seed_bundle(world, app)
    # A published row with no catalog entry (the pre-M14 seed path).
    (version,) = sql(
        world.platform,
        "INSERT INTO app_versions (app_id, version, kind, manifest, published_at) "
        "SELECT app_id, '9.9.9', 'published', manifest, now() FROM app_versions "
        "WHERE app_id = %s AND kind = 'working' RETURNING id",
        (app["id"],),
    )
    response = await _install(
        world, world.headers["alice"], world.family["id"], app["id"], str(version["id"])
    )
    assert (response.status_code, response.json()) == (422, {"detail": "not_in_catalog"})


async def test_install_prompts_when_permissions_are_not_empty(world: World) -> None:
    app = await _personal_app(world)
    published = (
        await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])
    ).json()
    version_id = published["version"]["id"]
    sql(
        world.platform,
        "UPDATE app_versions SET manifest = jsonb_set(manifest, '{homeai,permissions}', %s::jsonb) "
        "WHERE id = %s",
        (json.dumps({"net": ["example.com"]}), version_id),
    )
    missing = await _install(world, world.headers["bob"], world.family["id"], app["id"], version_id)
    assert (missing.status_code, missing.json()) == (422, {"detail": "permissions_required"})
    wrong = await world.client.post(
        _instances(world, world.family["id"]),
        json={
            "app_id": app["id"],
            "tracks": version_id,
            "granted_permissions": {"net": ["other.example"]},
        },
        headers=world.headers["bob"],
    )
    assert (wrong.status_code, wrong.json()) == (422, {"detail": "permissions_mismatch"})
    ok = await world.client.post(
        _instances(world, world.family["id"]),
        json={
            "app_id": app["id"],
            "tracks": version_id,
            "granted_permissions": {"net": ["example.com"]},
        },
        headers=world.headers["bob"],
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["granted_permissions"] == {"net": ["example.com"]}


# --- update ---------------------------------------------------------------------------


async def test_author_update_then_member_approves(world: World) -> None:
    app = await _personal_app(world)
    v1 = (await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])).json()[
        "version"
    ]
    inst = (
        await _install(world, world.headers["bob"], world.family["id"], app["id"], v1["id"])
    ).json()
    write_package(
        world.home("alice") / "Apps" / "hello",
        doc=manifest(version="1.1.0"),
        files={
            "AGENT.md": "# Hello\n",
            "schema.sql": SCHEMA_V2,
            "app/_layout.tsx": "import { Stack } from 'expo-router';\nexport default Stack;\n",
            "app/index.tsx": "export default function Index() { return null; }\n",
            "actions/addItem.sql": "INSERT INTO items (name) VALUES (:name);\n",
        },
    )
    validated = await world.client.post(
        f"{APPS}/{app['id']}/validate", headers=world.headers["alice"]
    )
    assert validated.status_code == 200 and validated.json()["valid"] is True
    _seed_bundle(world, app)
    v2 = (await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])).json()[
        "version"
    ]
    listing = await world.client.get(
        _instances(world, world.family["id"]), headers=world.headers["bob"]
    )
    found = listing.json()["instances"][0]
    assert found["update"]["id"] == v2["id"]
    assert found["update"]["version"] == "1.1.0"
    updated = await world.client.post(
        f"{_instances(world, world.family['id'])}/{inst['id']}/update",
        json={"version_id": v2["id"]},
        headers=world.headers["bob"],
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["instance"]["tracks"] == v2["id"]
    assert body["instance"]["update"] is None
    assert body["migration"]["status"] in ("applied", "up_to_date", "pending")


async def test_update_reprompts_when_permissions_change(world: World) -> None:
    app = await _personal_app(world)
    v1 = (await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])).json()[
        "version"
    ]
    inst = (
        await _install(world, world.headers["bob"], world.family["id"], app["id"], v1["id"])
    ).json()
    write_package(
        world.home("alice") / "Apps" / "hello",
        doc=manifest(version="1.1.0"),
    )
    await world.client.post(f"{APPS}/{app['id']}/validate", headers=world.headers["alice"])
    _seed_bundle(world, app)
    v2 = (await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])).json()[
        "version"
    ]
    sql(
        world.platform,
        "UPDATE app_versions SET manifest = jsonb_set(manifest, '{homeai,permissions}', %s::jsonb) "
        "WHERE id = %s",
        (json.dumps({"net": ["example.com"]}), v2["id"]),
    )
    missing = await world.client.post(
        f"{_instances(world, world.family['id'])}/{inst['id']}/update",
        json={"version_id": v2["id"]},
        headers=world.headers["bob"],
    )
    assert (missing.status_code, missing.json()) == (422, {"detail": "permissions_changed"})
    ok = await world.client.post(
        f"{_instances(world, world.family['id'])}/{inst['id']}/update",
        json={"version_id": v2["id"], "granted_permissions": {"net": ["example.com"]}},
        headers=world.headers["bob"],
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["instance"]["granted_permissions"] == {"net": ["example.com"]}


async def test_working_instance_is_not_updatable(world: World) -> None:
    _personal_pkg(world, "alice")
    app = await _registered(world, source="/personal/Apps/hello")
    inst = (
        await _install(world, world.headers["alice"], world.personal("alice")["id"], app["id"])
    ).json()
    response = await world.client.post(
        f"{_instances(world, world.personal('alice')['id'])}/{inst['id']}/update",
        json={"version_id": str(uuid4())},
        headers=world.headers["alice"],
    )
    assert (response.status_code, response.json()) == (422, {"detail": "working_not_updatable"})


# --- fork -----------------------------------------------------------------------------


async def test_fork_copies_source_into_another_space(world: World) -> None:
    app = await _personal_app(world)
    await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])
    bob_personal = world.personal("bob")
    response = await world.client.post(
        f"{APPS}/{app['id']}/fork",
        json={"space_id": str(bob_personal["id"])},
        headers=world.headers["bob"],
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["app"]["slug"] == "hello"
    assert body["app"]["source_space_id"] == str(bob_personal["id"])
    assert body["app"]["source_path"] == "/personal/Apps/hello"
    assert body["instance"]["tracks"] == "working"
    assert (world.home("bob") / "Apps" / "hello" / "schema.sql").read_text() == SCHEMA_V1
    assert (world.home("alice") / "Apps" / "hello" / "AGENT.md").read_text() == "# Hello\n"
    again = await world.client.post(
        f"{APPS}/{app['id']}/fork",
        json={"space_id": str(bob_personal["id"])},
        headers=world.headers["bob"],
    )
    assert (again.status_code, again.json()) == (409, {"detail": "app_exists"})


async def test_fork_needs_write_on_the_destination(world: World) -> None:
    app = await _personal_app(world)
    await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])
    response = await world.client.post(
        f"{APPS}/{app['id']}/fork",
        json={"space_id": world.family["id"]},
        headers=world.headers["carol"],
    )
    assert response.status_code == 403


# --- pinned instance data -------------------------------------------------------------


async def test_pinned_instance_reads_schema_and_actions_from_the_snapshot(world: World) -> None:
    app = await _personal_app(world)
    published = (
        await _publish(world, world.headers["alice"], app["id"], [world.family["id"]])
    ).json()
    inst = (
        await _install(
            world, world.headers["bob"], world.family["id"], app["id"], published["version"]["id"]
        )
    ).json()
    # Editing the live source must not affect the pinned install.
    (world.home("alice") / "Apps" / "hello" / "schema.sql").write_text(
        "CREATE TABLE gone (id INT);"
    )
    (world.home("alice") / "Apps" / "hello" / "actions" / "addItem.sql").write_text(
        "INSERT INTO gone (id) VALUES (1);\n"
    )
    migrated = await world.client.post(
        f"{API}/apps/instances/{inst['id']}/migrate", headers=world.headers["bob"]
    )
    assert migrated.status_code == 200, migrated.text
    assert migrated.json()["status"] == "applied"
    rpc = await world.client.post(
        f"{API}/apps/instances/{inst['id']}/rpc",
        json={"op": "action", "name": "addItem", "params": {"name": "milk"}},
        headers=world.headers["bob"],
    )
    assert rpc.status_code == 200, rpc.text
    rows = await world.client.post(
        f"{API}/apps/instances/{inst['id']}/rpc",
        json={"op": "getAll", "sql": "SELECT name FROM items"},
        headers=world.headers["bob"],
    )
    assert rows.json()["rows"] == [{"name": "milk"}]
