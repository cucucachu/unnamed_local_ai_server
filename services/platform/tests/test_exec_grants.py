"""`/internal/exec-grants`: a delegation in, its exec container's uid/gids/mounts out (§6)."""

from datetime import timedelta
from pathlib import Path

import pytest

from app.main import create_app
from tests.conftest import Platform, make_settings, running
from tests.files_world import API, World, build_world
from tests.helpers import bearer, sql

AGENT_TOKEN = "agent-secret"
EXEC_TOKEN = "exec-secret"
HOST_DIR = "/srv/homeai/spaces"


@pytest.fixture
async def platform(pg_database, tmp_path: Path):
    settings = make_settings(
        pg_database,
        tmp_path,
        platform_agent_token=AGENT_TOKEN,
        platform_exec_token=EXEC_TOKEN,
        spaces_host_dir=HOST_DIR,
    )
    app = create_app(settings)
    async with running(app) as client:
        yield Platform(app, client, pg_database, tmp_path)


async def _delegation(world: World, username: str) -> str:
    return (await world.agent(username))["Authorization"].removeprefix("Bearer ")


async def _grants(world: World, delegation: str, service: str = EXEC_TOKEN):
    return await world.client.post(
        "/internal/exec-grants", json={"delegation": delegation}, headers=bearer(service)
    )


def _host(space: dict) -> str:
    return f"{HOST_DIR}/{space['id']}/files"


async def test_owner_and_editor_get_both_spaces_writable(world):
    for username in ("alice", "bob"):
        response = await _grants(world, await _delegation(world, username))
        assert response.status_code == 200, response.text
        personal = world.personal(username)
        assert response.json() == {
            "uid": world.users[username]["uid"],
            "gid": personal["gid"],
            "gids": [personal["gid"], world.family["gid"]],
            "mounts": [
                {
                    "host_path": _host(personal),
                    "container_path": "/files/personal",
                    "read_only": False,
                },
                {
                    "host_path": _host(world.family),
                    "container_path": "/files/spaces/family",
                    "read_only": False,
                },
            ],
        }


async def test_viewer_space_is_read_only(world):
    response = await _grants(world, await _delegation(world, "carol"))
    assert response.status_code == 200
    mounts = {m["container_path"]: m["read_only"] for m in response.json()["mounts"]}
    assert mounts == {"/files/personal": False, "/files/spaces/family": True}
    assert world.family["gid"] in response.json()["gids"]


async def test_non_member_spaces_are_absent(world):
    response = await _grants(world, await _delegation(world, "dave"))
    assert response.status_code == 200
    body = response.json()
    personal = world.personal("dave")
    assert body["gids"] == [personal["gid"]]
    assert [m["container_path"] for m in body["mounts"]] == ["/files/personal"]
    assert all(str(world.family["id"]) not in m["host_path"] for m in body["mounts"])


async def test_personal_spaces_never_cross_users(world):
    alice = (await _grants(world, await _delegation(world, "alice"))).json()
    bob = (await _grants(world, await _delegation(world, "bob"))).json()
    assert alice["uid"] != bob["uid"]
    assert alice["mounts"][0]["host_path"] != bob["mounts"][0]["host_path"]
    assert alice["gid"] not in bob["gids"]


async def test_membership_changes_show_up_on_the_next_call(world):
    delegation = await _delegation(world, "bob")
    response = await world.client.delete(
        f"{API}/spaces/{world.family['id']}/members/{world.users['bob']['id']}",
        headers=world.headers["alice"],
    )
    assert response.status_code == 204, response.text
    body = (await _grants(world, delegation)).json()
    assert [m["container_path"] for m in body["mounts"]] == ["/files/personal"]
    assert world.family["gid"] not in body["gids"]


async def test_identity_token_is_refused(world):
    identity = world.headers["alice"]["X-HomeAI-Identity"]
    response = await _grants(world, identity)
    assert (response.status_code, response.json()) == (401, {"detail": "unauthenticated"})


@pytest.mark.parametrize("service", [AGENT_TOKEN, "", "exec-secre", EXEC_TOKEN + "x"])
async def test_requires_the_exec_service_token(world, service):
    response = await _grants(world, await _delegation(world, "alice"), service=service)
    assert (response.status_code, response.json()) == (401, {"detail": "unauthenticated"})


async def test_forged_expired_and_threadless_delegations_are_refused(world):
    tokens = world.platform.app.state.tokens
    good = await _delegation(world, "alice")
    claims = tokens.verify_token(good, act="agent")
    base = {k: claims[k] for k in ("sub", "sid", "role", "act")}
    expired = tokens.issue_token(base | {"thr": "t-1"}, timedelta(seconds=-1))
    threadless = tokens.issue_token(base, timedelta(minutes=5))
    header, payload, signature = good.split(".")
    tampered = f"{header}.{payload}.{signature[:-2]}AA"
    for delegation in (expired, threadless, tampered, "not-a-jwt"):
        response = await _grants(world, delegation)
        assert response.status_code == 401, delegation


async def test_revoked_session_is_refused(world):
    delegation = await _delegation(world, "alice")
    sql(world.platform, "UPDATE sessions SET revoked_at = now() WHERE user_id = %s",
        (world.users["alice"]["id"],))  # fmt: skip
    response = await _grants(world, delegation)
    assert response.status_code == 401


async def test_disabled_user_is_refused(world):
    delegation = await _delegation(world, "bob")
    sql(world.platform, "UPDATE users SET disabled_at = now() WHERE id = %s",
        (world.users["bob"]["id"],))  # fmt: skip
    response = await _grants(world, delegation)
    assert response.status_code == 401


async def test_a_files_dir_swapped_for_a_symlink_is_not_mounted(world):
    root = world.platform.app.state.storage.space_dir(world.family["id"])
    (root / "files").rename(root / "files.real")
    (root / "files").symlink_to(world.home("alice"))
    body = (await _grants(world, await _delegation(world, "bob"))).json()
    assert [m["container_path"] for m in body["mounts"]] == ["/files/personal"]


async def test_unconfigured_host_dir_refuses(pg_database, tmp_path):
    settings = make_settings(
        pg_database, tmp_path, platform_agent_token=AGENT_TOKEN, platform_exec_token=EXEC_TOKEN
    )
    app = create_app(settings)
    async with running(app) as client:
        world = await build_world(Platform(app, client, pg_database, tmp_path))
        response = await _grants(world, await _delegation(world, "alice"))
    assert (response.status_code, response.json()) == (500, {"detail": "exec_unconfigured"})
