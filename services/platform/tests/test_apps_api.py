"""The app registry: `/api/platform/apps*`, `/api/platform/spaces/{id}/instances*`,
and the files API's reserved `Apps` folder.

World (`tests/files_world.py`): alice owns /spaces/family, bob edits it,
carol views it, dave isn't a member. Instance-dir chowns are recorded by
the `chowns` fixture; `test_storage.py` checks them for real as root.
"""

from __future__ import annotations

import json
import shutil
import stat
from pathlib import Path

import pytest

from app.core import storage as storage_mod
from tests.app_packages import manifest, write_package
from tests.files_world import API, FILES, World
from tests.helpers import sql

APPS = f"{API}/apps"
FAMILY_SRC = "/spaces/family/Apps/hello"


def _instances(world: World, space_id: str) -> str:
    return f"{API}/spaces/{space_id}/instances"


def _family_pkg(world: World, slug: str = "hello", **kw) -> Path:
    return write_package(world.family_root / "Apps" / slug, **kw)


def _personal_pkg(world: World, user: str, slug: str = "hello", **kw) -> Path:
    return write_package(world.home(user) / "Apps" / slug, **kw)


async def _register(world: World, headers: dict, source_path: str):
    return await world.client.post(APPS, json={"source_path": source_path}, headers=headers)


async def _install(world: World, headers: dict, space_id: str, app_id: str, tracks="working"):
    return await world.client.post(
        _instances(world, space_id), json={"app_id": app_id, "tracks": tracks}, headers=headers
    )


async def _registered(world: World, user: str = "alice", source: str = FAMILY_SRC) -> dict:
    response = await _register(world, world.headers[user], source)
    assert response.status_code == 201, response.text
    return response.json()["app"]


# --- schema -------------------------------------------------------------------------


async def test_schema_is_published(world: World) -> None:
    response = await world.client.get(f"{APPS}/schema", headers=world.headers["dave"])
    assert response.status_code == 200
    body = response.json()
    assert body["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert body["properties"]["homeai"]["properties"]["sdk"]["enum"] == ["1"]
    assert (await world.client.get(f"{APPS}/schema")).status_code == 401


# --- registration --------------------------------------------------------------------


async def test_register_in_a_personal_space(world: World) -> None:
    _personal_pkg(world, "alice")
    response = await _register(world, world.headers["alice"], "/personal/Apps/hello/")
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["valid"], body["diagnostics"]) == (True, [])
    app = body["app"]
    assert (app["slug"], app["name"], app["source_path"]) == (
        "hello",
        "Hello",
        "/personal/Apps/hello",
    )
    assert app["source_space_id"] == str(world.personal("alice")["id"])
    assert app["created_by"] == str(world.users["alice"]["id"])
    working = app["working_version"]
    assert (working["kind"], working["version"], working["commit"], working["bundle_path"]) == (
        "working", "1.0.0", None, None,
    )  # fmt: skip
    assert working["manifest"] == manifest()
    (row,) = sql(world.platform, "SELECT count(*) AS n FROM app_versions")
    assert row["n"] == 1


@pytest.mark.parametrize(
    ("user", "status", "detail"),
    [
        ("alice", 201, None),
        ("bob", 201, None),
        ("carol", 403, "insufficient_role"),
        ("dave", 404, "not_found"),
    ],
)
async def test_register_needs_write_on_the_space(world, user, status, detail) -> None:
    _family_pkg(world)
    response = await _register(world, world.headers[user], FAMILY_SRC)
    assert response.status_code == status, response.text
    if detail:
        assert response.json() == {"detail": detail}


@pytest.mark.parametrize(("user", "status"), [("bob", 201), ("carol", 403), ("dave", 404)])
async def test_agents_register_with_their_users_rights(world, user, status) -> None:
    _family_pkg(world)
    response = await _register(world, await world.agent(user), FAMILY_SRC)
    assert response.status_code == status, response.text


@pytest.mark.parametrize(
    "source",
    [
        "/personal/hello",
        "/personal/Apps",
        "/personal/apps/hello",
        "/personal/Apps/hello/extra",
        "/personal/Apps/Hello",
        "/personal/Apps/hel_lo",
        "/spaces/family/hello",
        "/spaces/family/Apps",
        "/",
        "/elsewhere/Apps/hello",
    ],
)
async def test_source_must_be_an_apps_slug_folder(world, source) -> None:
    response = await _register(world, world.headers["alice"], source)
    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "invalid_source_path"}


async def test_the_personal_space_is_not_addressable_by_slug(world) -> None:
    slug = world.personal("alice")["slug"]
    _personal_pkg(world, "alice")
    response = await _register(world, world.headers["alice"], f"/spaces/{slug}/Apps/hello")
    assert (response.status_code, response.json()) == (404, {"detail": "not_found"})


async def test_invalid_package_is_422_with_diagnostics(world) -> None:
    doc = manifest("other")
    doc["homeai"]["exports"] = ["items"]
    _family_pkg(world, doc=doc)
    (world.family_root / "Apps" / "hello" / "AGENT.md").unlink()
    response = await _register(world, world.headers["bob"], FAMILY_SRC)
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "invalid_app"
    assert [(d["file"], d["path"]) for d in body["diagnostics"]] == [
        ("app.json", "/homeai/exports"),
        ("app.json", "/slug"),
        ("AGENT.md", ""),
    ]
    assert all(d["message"] for d in body["diagnostics"])
    assert sql(world.platform, "SELECT * FROM apps") == []


async def test_missing_source_creates_the_apps_folder(world, chowns) -> None:
    apps_dir = world.home("bob") / "Apps"
    assert not apps_dir.exists()
    response = await _register(world, world.headers["bob"], "/personal/Apps/hello")
    assert response.status_code == 422
    (diag,) = response.json()["diagnostics"]
    assert "doesn't exist" in diag["message"]
    assert apps_dir.is_dir()
    assert stat.S_IMODE(apps_dir.stat().st_mode) == 0o2770
    assert chowns[apps_dir] == (world.users["bob"]["uid"], world.personal("bob")["gid"])


async def test_apps_folder_in_the_way(world) -> None:
    (world.home("alice") / "Apps").write_text("not a folder")
    response = await _register(world, world.headers["alice"], "/personal/Apps/hello")
    assert (response.status_code, response.json()) == (
        409,
        {"detail": "apps_folder_not_a_directory"},
    )


async def test_symlinked_source_is_refused(world, tmp_path) -> None:
    real = _personal_pkg(world, "alice", "real", doc=manifest("hello"))
    (world.home("alice") / "Apps" / "hello").symlink_to(real)
    response = await _register(world, world.headers["alice"], "/personal/Apps/hello")
    assert (response.status_code, response.json()) == (422, {"detail": "invalid_source_path"})


async def test_registering_twice_is_409(world) -> None:
    _family_pkg(world)
    await _registered(world)
    response = await _register(world, world.headers["bob"], FAMILY_SRC)
    assert (response.status_code, response.json()) == (409, {"detail": "app_exists"})


async def test_same_slug_in_two_spaces(world) -> None:
    _family_pkg(world)
    _personal_pkg(world, "alice")
    a = await _registered(world)
    b = await _registered(world, source="/personal/Apps/hello")
    assert a["id"] != b["id"] and a["slug"] == b["slug"]


# --- listing and visibility ---------------------------------------------------------


async def test_visibility(world) -> None:
    _family_pkg(world)
    _personal_pkg(world, "alice", "mine", doc=manifest("mine", name="Mine"))
    family_app = await _registered(world)
    mine = await _registered(world, source="/personal/Apps/mine")

    async def names(user: str, agent: bool = False) -> list[str]:
        headers = await world.agent(user) if agent else world.headers[user]
        response = await world.client.get(APPS, headers=headers)
        assert response.status_code == 200
        return [a["slug"] for a in response.json()["apps"]]

    assert await names("alice") == ["hello", "mine"]
    assert await names("carol") == ["hello"]
    assert await names("bob", agent=True) == ["hello"]
    assert await names("dave") == []
    for user, app, status in (
        ("carol", family_app, 200),
        ("bob", mine, 404),
        ("dave", family_app, 404),
    ):
        response = await world.client.get(f"{APPS}/{app['id']}", headers=world.headers[user])
        assert response.status_code == status


async def test_archived_space_hides_its_apps(world) -> None:
    _family_pkg(world)
    await _registered(world)
    sql(
        world.platform, "UPDATE spaces SET archived_at = now() WHERE id = %s", (world.family["id"],)
    )
    response = await world.client.get(APPS, headers=world.headers["alice"])
    assert response.json()["apps"] == []


async def test_installed_elsewhere_is_visible_without_its_source(world) -> None:
    """Pinned installs arrive with M14; seed one to check the visibility rule now."""
    _personal_pkg(world, "alice")
    app = await _registered(world, source="/personal/Apps/hello")
    sql(
        world.platform,
        "WITH v AS (INSERT INTO app_versions (app_id, version, kind, manifest, published_at) "
        "SELECT app_id, version, 'published', manifest, now() FROM app_versions "
        "WHERE app_id = %s RETURNING id, app_id) "
        "INSERT INTO app_instances (app_id, space_id, tracks, version_id) "
        "SELECT app_id, %s, 'version', id FROM v",
        (app["id"], world.family["id"]),
    )
    (seen,) = (await world.client.get(APPS, headers=world.headers["carol"])).json()["apps"]
    assert (seen["id"], seen["source_path"], seen["working_version"]) == (app["id"], None, None)
    response = await world.client.post(f"{APPS}/{app['id']}/validate", headers=world.headers["bob"])
    assert response.status_code == 404


# --- validate ---------------------------------------------------------------------------


async def test_validate_updates_the_working_version(world) -> None:
    folder = _family_pkg(world)
    app = await _registered(world)
    (folder / "app.json").write_text(json.dumps(manifest(name="Hello 2", version="1.1.0")))
    response = await world.client.post(f"{APPS}/{app['id']}/validate", headers=world.headers["bob"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["valid"], body["diagnostics"]) == (True, [])
    assert (body["app"]["name"], body["app"]["working_version"]["version"]) == ("Hello 2", "1.1.0")


async def test_validate_reports_problems_and_keeps_the_last_good_manifest(world) -> None:
    folder = _family_pkg(world)
    app = await _registered(world)
    (folder / "app.json").write_text(json.dumps(manifest(version="two")))
    (folder / "app" / "(tabs)").mkdir()
    response = await world.client.post(
        f"{APPS}/{app['id']}/validate", headers=world.headers["alice"]
    )
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert [(d["file"], d["path"]) for d in body["diagnostics"]] == [
        ("app.json", "/version"),
        ("app/(tabs)", ""),
    ]
    assert body["app"]["working_version"]["version"] == "1.0.0"


@pytest.mark.parametrize(("user", "status"), [("carol", 403), ("dave", 404)])
async def test_validate_needs_write(world, user, status) -> None:
    _family_pkg(world)
    app = await _registered(world)
    response = await world.client.post(f"{APPS}/{app['id']}/validate", headers=world.headers[user])
    assert response.status_code == status


# --- install / uninstall ---------------------------------------------------------------


async def test_install_creates_the_instance_dir(world, chowns) -> None:
    _family_pkg(world)
    app = await _registered(world)
    response = await _install(world, world.headers["bob"], world.family["id"], app["id"])
    assert response.status_code == 201, response.text
    inst = response.json()
    assert (inst["app_id"], inst["space_id"], inst["tracks"]) == (
        app["id"], world.family["id"], "working",
    )  # fmt: skip
    assert inst["installed_by"] == str(world.users["bob"]["id"])
    assert inst["granted_permissions"] == {}
    assert inst["app"] == {
        "id": app["id"], "slug": "hello", "name": "Hello", "version": "1.0.0",
        "icon": "happy-outline",
    }  # fmt: skip
    storage = world.platform.app.state.storage
    path = storage.instance_dir(world.family["id"], inst["id"])
    assert path.is_dir() and stat.S_IMODE(path.stat().st_mode) == 0o2750
    for sub in ("ro", "snapshots"):
        assert stat.S_IMODE((path / sub).stat().st_mode) == 0o2750
    assert chowns[path] == (0, world.family["gid"])

    listing = await world.client.get(
        _instances(world, world.family["id"]), headers=world.headers["carol"]
    )
    assert [i["id"] for i in listing.json()["instances"]] == [inst["id"]]
    assert (
        await world.client.get(_instances(world, world.family["id"]), headers=world.headers["dave"])
    ).status_code == 404


@pytest.mark.parametrize(
    ("user", "status", "detail"),
    [("carol", 403, "insufficient_role"), ("dave", 404, "not_found")],
)
async def test_install_needs_write(world, user, status, detail) -> None:
    _family_pkg(world)
    app = await _registered(world)
    response = await _install(world, world.headers[user], world.family["id"], app["id"])
    assert (response.status_code, response.json()) == (status, {"detail": detail})


async def test_agents_install_and_uninstall(world) -> None:
    _family_pkg(world)
    app = await _registered(world)
    agent = await world.agent("bob")
    response = await _install(world, agent, world.family["id"], app["id"])
    assert response.status_code == 201
    iid = response.json()["id"]
    url = f"{_instances(world, world.family['id'])}/{iid}"
    assert (await world.client.delete(url, headers=await world.agent("carol"))).status_code == 403
    assert (await world.client.delete(url, headers=agent)).status_code == 204


async def test_working_copy_only_installs_in_its_source_space(world) -> None:
    _personal_pkg(world, "alice")
    app = await _registered(world, source="/personal/Apps/hello")
    response = await _install(world, world.headers["alice"], world.family["id"], app["id"])
    assert (response.status_code, response.json()) == (
        422, {"detail": "working_requires_source_space"},
    )  # fmt: skip


async def test_install_rejects_unknown_tracks_and_apps(world) -> None:
    _family_pkg(world)
    app = await _registered(world)
    space = world.family["id"]
    for tracks, detail in (
        ("latest", "invalid_tracks"),
        ("00000000-0000-0000-0000-000000000000", "unknown_version"),
        (app["working_version"]["id"], "unknown_version"),
    ):
        response = await _install(world, world.headers["alice"], space, app["id"], tracks)
        assert (response.status_code, response.json()) == (422, {"detail": detail})
    _personal_pkg(world, "dave")
    daves = await _registered(world, "dave", "/personal/Apps/hello")
    response = await _install(world, world.headers["alice"], space, daves["id"])
    assert (response.status_code, response.json()) == (404, {"detail": "not_found"})


async def test_install_pinned_published_version(world) -> None:
    _family_pkg(world)
    app = await _registered(world)
    (version,) = sql(
        world.platform,
        "INSERT INTO app_versions (app_id, version, kind, manifest, published_at) "
        "SELECT app_id, version, 'published', manifest, now() FROM app_versions "
        "WHERE app_id = %s RETURNING id",
        (app["id"],),
    )
    response = await _install(
        world, world.headers["alice"], world.family["id"], app["id"], str(version["id"])
    )
    assert response.status_code == 201, response.text
    assert response.json()["tracks"] == str(version["id"])


async def test_one_instance_per_app_and_space(world) -> None:
    _family_pkg(world)
    app = await _registered(world)
    assert (
        await _install(world, world.headers["alice"], world.family["id"], app["id"])
    ).status_code == 201
    response = await _install(world, world.headers["bob"], world.family["id"], app["id"])
    assert (response.status_code, response.json()) == (409, {"detail": "already_installed"})


async def test_uninstall_moves_the_dir_to_trash_and_allows_reinstall(world) -> None:
    _family_pkg(world)
    app = await _registered(world)
    space = world.family["id"]
    inst = (await _install(world, world.headers["alice"], space, app["id"])).json()
    storage = world.platform.app.state.storage
    data = storage.instance_dir(space, inst["id"]) / "data.sqlite"
    data.write_text("rows")

    url = f"{_instances(world, space)}/{inst['id']}"
    assert (await world.client.delete(url, headers=world.headers["dave"])).status_code == 404
    assert (await world.client.delete(url, headers=world.headers["carol"])).status_code == 403
    assert (await world.client.delete(url, headers=world.headers["bob"])).status_code == 204
    assert (await world.client.delete(url, headers=world.headers["bob"])).status_code == 404

    apps_dir = storage.space_dir(space) / "apps"
    assert not (apps_dir / inst["id"]).exists()
    (kept,) = (apps_dir / storage_mod.TRASH_DIR).iterdir()
    assert kept.name.startswith(f"{inst['id']}-")
    assert (kept / "data.sqlite").read_text() == "rows"
    (row,) = sql(
        world.platform, "SELECT uninstalled_at FROM app_instances WHERE id = %s", (inst["id"],)
    )
    assert row["uninstalled_at"] is not None

    listing = await world.client.get(_instances(world, space), headers=world.headers["alice"])
    assert listing.json()["instances"] == []
    again = await _install(world, world.headers["alice"], space, app["id"])
    assert again.status_code == 201 and again.json()["id"] != inst["id"]


async def test_uninstall_needs_the_instance_in_that_space(world) -> None:
    _family_pkg(world)
    _personal_pkg(world, "alice")
    fam = await _registered(world)
    inst = (await _install(world, world.headers["alice"], world.family["id"], fam["id"])).json()
    personal = world.personal("alice")["id"]
    response = await world.client.delete(
        f"{_instances(world, personal)}/{inst['id']}", headers=world.headers["alice"]
    )
    assert response.status_code == 404


async def test_uninstall_with_the_dir_already_gone(world) -> None:
    _family_pkg(world)
    app = await _registered(world)
    inst = (await _install(world, world.headers["alice"], world.family["id"], app["id"])).json()
    shutil.rmtree(world.platform.app.state.storage.instance_dir(world.family["id"], inst["id"]))
    url = f"{_instances(world, world.family['id'])}/{inst['id']}"
    assert (await world.client.delete(url, headers=world.headers["alice"])).status_code == 204


# --- the reserved Apps folder ---------------------------------------------------------


async def _files(world: World, headers: dict, method: str, route: str = "", **kw):
    return await world.client.request(method, f"{FILES}{route}", headers=headers, **kw)


@pytest.mark.parametrize("root", ["/personal", "/spaces/family"])
@pytest.mark.parametrize("as_agent", [False, True])
async def test_apps_folder_cant_be_deleted_renamed_or_moved(world, root, as_agent) -> None:
    headers = await world.agent("alice") if as_agent else world.headers["alice"]
    response = await _files(world, headers, "POST", "/mkdir", json={"path": f"{root}/Apps"})
    assert response.status_code == 201
    for method, route, kw in (
        ("DELETE", "", {"params": {"path": f"{root}/Apps"}}),
        ("DELETE", "", {"params": {"path": f"{root}/Apps/"}}),
        ("POST", "/rename", {"json": {"path": f"{root}/Apps", "name": "Old apps"}}),
        ("POST", "/move", {"json": {"src": f"{root}/Apps", "dst": f"{root}/elsewhere"}}),
        ("POST", "/move", {"json": {"src": f"{root}/Apps", "dst": "/personal/Apps2"}}),
    ):
        response = await _files(world, headers, method, route, **kw)
        assert (response.status_code, response.json()) == (403, {"detail": "reserved"}), (
            method,
            kw,
        )
    base = world.home("alice") if root == "/personal" else world.family_root
    assert (base / "Apps").is_dir()


async def test_things_inside_apps_and_copies_are_ordinary(world) -> None:
    _personal_pkg(world, "alice")
    headers = world.headers["alice"]
    response = await _files(
        world, headers, "POST", "/copy", json={"src": "/personal/Apps", "dst": "/personal/Backup"}
    )
    assert response.status_code == 200
    response = await _files(
        world, headers, "POST", "/rename", json={"path": "/personal/Apps/hello", "name": "hi"}
    )
    assert response.status_code == 200
    response = await _files(world, headers, "DELETE", params={"path": "/personal/Apps/hi"})
    assert response.status_code == 204
    # Only the top level is reserved.
    await _files(world, headers, "POST", "/mkdir", json={"path": "/personal/Backup/Apps"})
    response = await _files(world, headers, "DELETE", params={"path": "/personal/Backup/Apps"})
    assert response.status_code == 204


async def test_a_file_squatting_on_apps_can_be_removed(world) -> None:
    (world.home("alice") / "Apps").write_text("oops")
    response = await _files(
        world, world.headers["alice"], "DELETE", params={"path": "/personal/Apps"}
    )
    assert response.status_code == 204


async def test_viewer_still_gets_insufficient_role(world) -> None:
    (world.family_root / "Apps").mkdir()
    response = await _files(
        world, world.headers["carol"], "DELETE", params={"path": "/spaces/family/Apps"}
    )
    assert (response.status_code, response.json()) == (403, {"detail": "insufficient_role"})
