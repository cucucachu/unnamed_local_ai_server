"""Image-shipped system apps (M14-03): list + Files privileged actions."""

from __future__ import annotations

from tests.app_packages import manifest, write_package
from tests.files_world import World

API = "/api/platform/system-apps"


async def test_list_includes_the_four_native_system_apps(world: World) -> None:
    response = await world.client.get(API, headers=world.headers["alice"])
    assert response.status_code == 200, response.text
    slugs = [a["slug"] for a in response.json()["apps"]]
    assert slugs == ["home", "chat", "files", "routines", "settings"]
    files = next(a for a in response.json()["apps"] if a["slug"] == "files")
    assert files["native"] and files["read_only_source"]
    assert files["privileged"] == ["files"]
    assert {a["name"] for a in files["actions"]} == {"moveToSpace", "copyToSpace"}
    assert "moveToSpace" in files["agent_md"]
    chat = next(a for a in response.json()["apps"] if a["slug"] == "chat")
    assert chat["actions"] == [] and chat["privileged"] == []


async def test_privileged_capability_cannot_be_declared_by_a_user_app(world: World) -> None:
    doc = manifest("hello")
    doc["homeai"]["permissions"] = {"privileged": ["files"]}
    write_package(world.home("alice") / "Apps" / "hello", doc=doc)
    response = await world.client.post(
        "/api/platform/apps",
        json={"source_path": "/personal/Apps/hello"},
        headers=world.headers["alice"],
    )
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["detail"] == "invalid_app"
    assert any(
        d["path"] == "/homeai/permissions/privileged"
        or "privileged" in d["message"]
        or "no permissions" in d["message"]
        for d in body["diagnostics"]
    )


async def test_reserved_files_slug_cannot_be_registered(world: World) -> None:
    write_package(world.home("alice") / "Apps" / "files", doc=manifest("files"))
    response = await world.client.post(
        "/api/platform/apps",
        json={"source_path": "/personal/Apps/files"},
        headers=world.headers["alice"],
    )
    assert response.status_code == 422, response.text
    assert any("reserved" in d["message"] for d in response.json()["diagnostics"])


async def test_files_move_to_space_has_parity_with_the_files_api(world: World) -> None:
    (world.home("alice") / "notes.txt").write_text("hello")
    response = await world.client.post(
        f"{API}/files/actions/moveToSpace",
        json={"params": {"src": "/personal/notes.txt", "dst": "/spaces/family/notes.txt"}},
        headers=world.headers["alice"],
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"] == {
        "src": "/personal/notes.txt",
        "dst": "/spaces/family/notes.txt",
    }
    assert not (world.home("alice") / "notes.txt").exists()
    assert (world.family_root / "notes.txt").read_text() == "hello"


async def test_files_copy_to_space(world: World) -> None:
    (world.home("alice") / "notes.txt").write_text("keep")
    response = await world.client.post(
        f"{API}/files/actions/copyToSpace",
        json={"params": {"src": "/personal/notes.txt", "dst": "/spaces/family/notes.txt"}},
        headers=world.headers["alice"],
    )
    assert response.status_code == 200, response.text
    assert (world.home("alice") / "notes.txt").read_text() == "keep"
    assert (world.family_root / "notes.txt").read_text() == "keep"


async def test_viewer_cannot_call_files_move(world: World) -> None:
    (world.family_root / "fam.txt").write_text("fam")
    response = await world.client.post(
        f"{API}/files/actions/moveToSpace",
        json={"params": {"src": "/spaces/family/fam.txt", "dst": "/personal/fam.txt"}},
        headers=world.headers["carol"],
    )
    assert response.status_code == 403
    assert (world.family_root / "fam.txt").exists()


async def test_unknown_system_action_is_404(world: World) -> None:
    response = await world.client.post(
        f"{API}/files/actions/notAThing",
        json={"params": {}},
        headers=world.headers["alice"],
    )
    assert (response.status_code, response.json()) == (404, {"detail": "unknown_action"})


async def test_chat_has_no_actions(world: World) -> None:
    response = await world.client.post(
        f"{API}/chat/actions/moveToSpace",
        json={"params": {"src": "/personal/a", "dst": "/personal/b"}},
        headers=world.headers["alice"],
    )
    assert (response.status_code, response.json()) == (404, {"detail": "unknown_action"})
