"""App photos (#468): `/api/platform/apps/instances/{id}/images[/{image_id}]`.

World (`tests/files_world.py`): alice owns /spaces/family, bob edits it,
carol views it, dave isn't a member.
"""

from __future__ import annotations

import base64
import os
import stat

import pytest

from app.core import appimages
from tests.app_packages import write_package
from tests.files_world import API, World

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"\x00" * 64


async def _install(world: World, app_id: str) -> dict:
    r = await world.client.post(
        f"{API}/spaces/{world.family['id']}/instances",
        json={"app_id": app_id}, headers=world.headers["alice"],
    )  # fmt: skip
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
async def inst(world: World) -> dict:
    write_package(world.family_root / "Apps" / "hello")
    r = await world.client.post(
        f"{API}/apps", json={"source_path": "/spaces/family/Apps/hello"},
        headers=world.headers["alice"],
    )  # fmt: skip
    assert r.status_code == 201, r.text
    return await _install(world, r.json()["app"]["id"])


def _url(inst: dict, image_id: str = "") -> str:
    return f"{API}/apps/instances/{inst['id']}/images" + (f"/{image_id}" if image_id else "")


async def _upload(world: World, inst: dict, user: str, data: bytes):
    body = {"data": base64.b64encode(data).decode()}
    return await world.client.post(_url(inst), json=body, headers=world.headers[user])


@pytest.mark.parametrize(("data", "ext", "mime"), [(JPEG, "jpg", "image/jpeg"), (PNG, "png", "image/png"),
                                                   (WEBP, "webp", "image/webp")])  # fmt: skip
async def test_store_read_and_delete(world, inst, data, ext, mime) -> None:
    r = await _upload(world, inst, "bob", data)
    assert r.status_code == 201, r.text
    image_id = r.json()["id"]
    assert image_id.endswith(f".{ext}") and len(image_id) == 33 + len(ext)
    r = await world.client.get(_url(inst, image_id), headers=world.headers["carol"])
    assert r.status_code == 200, r.text
    assert r.json() == {"data_url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}
    path = (
        world.platform.app.state.storage.instance_dir(world.family["id"], inst["id"])
        / "images"
        / image_id
    )
    assert stat.S_IMODE(os.stat(path).st_mode) == appimages.FILE_MODE
    r = await world.client.delete(_url(inst, image_id), headers=world.headers["bob"])
    assert r.status_code == 204
    r = await world.client.get(_url(inst, image_id), headers=world.headers["alice"])
    assert (r.status_code, r.json()) == (404, {"detail": "not_found"})


@pytest.mark.parametrize(
    ("user", "read", "write"),
    [("alice", 200, 201), ("bob", 200, 201), ("carol", 200, 403), ("dave", 404, 404)],
)
async def test_role_matrix(world, inst, user, read, write) -> None:
    image_id = (await _upload(world, inst, "alice", JPEG)).json()["id"]
    assert (
        await world.client.get(_url(inst, image_id), headers=world.headers[user])
    ).status_code == read
    assert (await _upload(world, inst, user, JPEG)).status_code == write
    r = await world.client.delete(_url(inst, image_id), headers=world.headers[user])
    assert r.status_code == (204 if write == 201 else write)


async def test_rejects_what_isnt_a_small_image(world, inst) -> None:
    r = await _upload(world, inst, "alice", b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    assert (r.status_code, r.json()["detail"]) == (422, "not_an_image")
    r = await _upload(world, inst, "alice", JPEG + b"\x00" * (appimages.MAX_BYTES - len(JPEG) + 1))
    assert (r.status_code, r.json()["detail"]) == (422, "image_too_large")
    r = await world.client.post(
        _url(inst), json={"data": "not base64!"}, headers=world.headers["alice"]
    )
    assert (r.status_code, r.json()["detail"]) == (422, "not_an_image")
    r = await world.client.post(
        _url(inst), json={"data": "A" * 800_000}, headers=world.headers["alice"]
    )
    assert (r.status_code, r.json()["detail"]) == (422, "invalid_request")
    assert not (
        world.platform.app.state.storage.instance_dir(world.family["id"], inst["id"]) / "images"
    ).exists()


@pytest.mark.parametrize(
    "image_id", ["..%2Fdata.sqlite", "data.sqlite", "0" * 32 + ".svg", "x.jpg"]
)
async def test_ids_name_only_stored_images(world, inst, image_id) -> None:
    await _upload(world, inst, "alice", JPEG)
    r = await world.client.get(_url(inst, image_id), headers=world.headers["alice"])
    assert r.status_code == 404
    r = await world.client.delete(_url(inst, image_id), headers=world.headers["alice"])
    assert r.status_code == 404


async def test_another_install_cannot_see_them(world, inst) -> None:
    image_id = (await _upload(world, inst, "alice", JPEG)).json()["id"]
    write_package(world.family_root / "Apps" / "other")
    r = await world.client.post(
        f"{API}/apps", json={"source_path": "/spaces/family/Apps/other"},
        headers=world.headers["alice"],
    )  # fmt: skip
    assert r.status_code == 201, r.text
    other = await _install(world, r.json()["app"]["id"])
    r = await world.client.get(_url(other, image_id), headers=world.headers["alice"])
    assert (r.status_code, r.json()) == (404, {"detail": "not_found"})
