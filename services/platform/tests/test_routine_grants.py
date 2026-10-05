"""`/internal/routine-grants*`: the identity a scheduled routine run acts with (M17-03)."""

from pathlib import Path

import pytest

from app.main import create_app
from tests.conftest import Platform, make_settings, running
from tests.files_world import API, FILES, World, build_world
from tests.helpers import PASSWORD, bearer, identity, stepped_up_admin

AGENT_TOKEN = "agent-secret"
EXEC_TOKEN = "exec-secret"
HOST_DIR = "/srv/homeai/spaces"
ROUTINE = "4c0e9b5e-2f7d-4f43-9a51-7f3a3f2d1e10"
THREAD = "run-thread-1"


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


@pytest.fixture
async def world(platform) -> World:
    return await build_world(platform)


async def _issue(world: World, username: str, space="/spaces/family", routine=ROUTINE, **kw):
    headers = await identity(world.platform, world.tokens[username])
    return await world.client.post(
        "/internal/routine-grants",
        json={
            "identity_token": headers["X-HomeAI-Identity"],
            "routine_id": routine,
            "space": space,
            "label": "Morning brief",
        }
        | kw,
        headers=bearer(AGENT_TOKEN),
    )


async def _grant(world: World, username: str = "bob", **kw) -> str:
    response = await _issue(world, username, **kw)
    assert response.status_code == 200, response.text
    assert response.json()["grant"].startswith("hr_")
    return response.json()["grant"]


async def _exchange(world: World, grant: str, routine=ROUTINE, thread=THREAD):
    return await world.client.post(
        "/internal/routine-grants/exchange",
        json={"grant": grant, "routine_id": routine, "thread_id": thread},
        headers=bearer(AGENT_TOKEN),
    )


async def _delegation(world: World, grant: str) -> dict[str, str]:
    response = await _exchange(world, grant)
    assert response.status_code == 200, response.text
    return bearer(response.json()["token"])


async def _ls(world: World, headers: dict[str, str], path: str):
    return await world.client.get(FILES, params={"path": path}, headers=headers)


async def test_run_delegation_reaches_only_the_routine_space(world):
    agent = await _delegation(world, await _grant(world))
    claims = world.platform.app.state.tokens.verify_token(
        agent["Authorization"].removeprefix("Bearer "), act="agent"
    )
    assert (claims["sub"], claims["thr"]) == (str(world.users["bob"]["id"]), THREAD)

    assert (await _ls(world, agent, "/spaces/family")).status_code == 200
    assert (await _ls(world, agent, "/personal")).status_code == 404
    listed = await _ls(world, agent, "/spaces")
    assert [e["name"] for e in listed.json()["entries"]] == ["family"]
    spaces = await world.client.get(f"{API}/spaces", headers=agent)
    assert [s["slug"] for s in spaces.json()["spaces"]] == ["family"]

    # The user's own chat delegation still sees everything.
    chat = await world.agent("bob")
    assert (await _ls(world, chat, "/personal")).status_code == 200


async def test_personal_routine_cannot_reach_shared_spaces(world):
    agent = await _delegation(world, await _grant(world, "alice", space="/personal"))
    assert (await _ls(world, agent, "/personal")).status_code == 200
    assert (await _ls(world, agent, "/spaces/family")).status_code == 404


async def test_run_exec_container_mounts_only_the_routine_space(world):
    agent = await _delegation(world, await _grant(world))
    response = await world.client.post(
        "/internal/exec-grants",
        json={"delegation": agent["Authorization"].removeprefix("Bearer ")},
        headers=bearer(EXEC_TOKEN),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gids"] == [world.family["gid"]]
    assert [m["container_path"] for m in body["mounts"]] == ["/files/spaces/family"]


async def test_refresh_keeps_the_thread_and_the_scope(world):
    agent = await _delegation(world, await _grant(world))
    refreshed = await world.client.post(
        "/internal/delegations/refresh",
        json={"token": agent["Authorization"].removeprefix("Bearer ")},
        headers=bearer(AGENT_TOKEN),
    )
    assert refreshed.status_code == 200, refreshed.text
    token = refreshed.json()["token"]
    assert world.platform.app.state.tokens.verify_token(token, act="agent")["thr"] == THREAD
    assert (await _ls(world, bearer(token), "/personal")).status_code == 404


@pytest.mark.parametrize(
    ("username", "space", "status", "detail"),
    [
        ("carol", "/spaces/family", 403, "insufficient_role"),
        ("dave", "/spaces/family", 404, "not_found"),
        ("bob", "/spaces/family/notes", 422, "invalid_space"),
    ],
)
async def test_issue_needs_edit_rights_on_a_space(world, username, space, status, detail):
    response = await _issue(world, username, space=space)
    assert (response.status_code, response.json()["detail"]) == (status, detail)


async def test_issue_rejects_bad_input_and_callers(world):
    assert (await _issue(world, "bob", routine="no spaces")).status_code == 422
    bad = await world.client.post(
        "/internal/routine-grants",
        json={"identity_token": "nope", "routine_id": ROUTINE, "space": "/personal"},
        headers=bearer(AGENT_TOKEN),
    )
    assert bad.status_code == 401
    headers = await identity(world.platform, world.tokens["bob"])
    wrong_service = await world.client.post(
        "/internal/routine-grants",
        json={
            "identity_token": headers["X-HomeAI-Identity"],
            "routine_id": ROUTINE,
            "space": "/personal",
        },
        headers=bearer(EXEC_TOKEN),
    )
    assert wrong_service.status_code in (401, 403)


async def test_grant_is_never_a_login(world):
    grant = await _grant(world)
    verify = await world.client.get("/internal/auth/verify", headers=bearer(grant))
    assert verify.status_code == 401
    assert (await _ls(world, bearer(grant), "/spaces/family")).status_code == 401


async def test_grant_is_bound_to_its_routine(world):
    grant = await _grant(world)
    other = "1b1b1b1b-2f7d-4f43-9a51-7f3a3f2d1e10"
    assert (await _exchange(world, grant, routine=other)).status_code == 401
    assert (await _exchange(world, "hr_made-up")).status_code == 401
    assert (await _exchange(world, grant, thread="../x")).status_code == 422
    assert (await _exchange(world, grant)).status_code == 200


async def test_reissuing_replaces_the_old_grant(world):
    first = await _grant(world)
    second = await _grant(world)
    assert (await _exchange(world, first)).status_code == 401
    assert (await _exchange(world, second)).status_code == 200
    other = await _grant(world, routine="another-routine")
    assert (await _exchange(world, second)).status_code == 200
    assert (await _exchange(world, other, routine="another-routine")).status_code == 200


async def _revoke_routine(world, grant):
    response = await world.client.post(
        "/internal/routine-grants/revoke", json={"grant": grant}, headers=bearer(AGENT_TOKEN)
    )
    assert response.status_code == 204


async def _disable_user(world, grant):
    admin = await stepped_up_admin(world.platform)
    response = await world.client.patch(
        f"{API}/admin/users/{world.users['bob']['id']}", json={"disabled": True}, headers=admin
    )
    assert response.status_code == 200, response.text


async def _change_password(world, grant):
    response = await world.client.patch(
        f"{API}/me",
        json={"password": PASSWORD + "-changed", "current_password": PASSWORD},
        headers=await identity(world.platform, world.tokens["bob"]),
    )
    assert response.status_code == 200, response.text


async def _demote(world, grant):
    response = await world.client.patch(
        f"{API}/spaces/{world.family['id']}/members/{world.users['bob']['id']}",
        json={"role": "viewer"},
        headers=world.headers["alice"],
    )
    assert response.status_code == 200, response.text


async def _remove(world, grant):
    response = await world.client.delete(
        f"{API}/spaces/{world.family['id']}/members/{world.users['bob']['id']}",
        headers=world.headers["alice"],
    )
    assert response.status_code == 204, response.text


async def _revoke_in_settings(world, grant):
    headers = await identity(world.platform, world.tokens["bob"])
    listed = (await world.client.get(f"{API}/me/sessions", headers=headers)).json()["sessions"]
    (routine,) = [s for s in listed if s["routine_id"] == ROUTINE]
    response = await world.client.delete(f"{API}/me/sessions/{routine['id']}", headers=headers)
    assert response.status_code == 204


@pytest.mark.parametrize(
    "revoke",
    [_revoke_routine, _disable_user, _change_password, _demote, _remove, _revoke_in_settings],
)
async def test_each_revocation_path_kills_the_next_run(world, revoke):
    grant = await _grant(world)
    running = await _delegation(world, grant)
    await revoke(world, grant)
    assert (await _exchange(world, grant)).status_code == 401
    # ...and the run already in flight loses access on its next request.
    assert (await _ls(world, running, "/spaces/family")).status_code == 401


async def test_lost_edit_rights_stay_lost(world):
    grant = await _grant(world)
    await _demote(world, grant)
    assert (await _exchange(world, grant)).status_code == 401
    promoted = await world.client.patch(
        f"{API}/spaces/{world.family['id']}/members/{world.users['bob']['id']}",
        json={"role": "editor"},
        headers=world.headers["alice"],
    )
    assert promoted.status_code == 200
    assert (await _exchange(world, grant)).status_code == 401


async def test_settings_lists_routine_grants(world):
    await _grant(world)
    headers = await identity(world.platform, world.tokens["bob"])
    listed = (await world.client.get(f"{API}/me/sessions", headers=headers)).json()["sessions"]
    by_kind = {s["routine_id"]: s for s in listed}
    assert by_kind[ROUTINE]["device_label"] == "Morning brief"
    assert by_kind[ROUTINE]["current"] is False
    assert by_kind[None]["current"] is True


async def _chat_delegation(world: World, username: str) -> str:
    headers = await identity(world.platform, world.tokens[username])
    response = await world.client.post(
        "/internal/delegations",
        json={"identity_token": headers["X-HomeAI-Identity"], "thread_id": "chat-thread"},
        headers=bearer(AGENT_TOKEN),
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


async def _issue_with(world: World, body: dict):
    return await world.client.post(
        "/internal/routine-grants",
        json={"routine_id": ROUTINE, "space": "/spaces/family", "label": "From chat"} | body,
        headers=bearer(AGENT_TOKEN),
    )


async def _access_with(world: World, body: dict):
    return await world.client.post(
        "/internal/space-access",
        json={"space": "/spaces/family"} | body,
        headers=bearer(AGENT_TOKEN),
    )


async def test_a_chat_delegation_can_save_a_routine(world):
    token = await _chat_delegation(world, "bob")
    access = await _access_with(world, {"delegation_token": token})
    assert access.status_code == 200, access.text
    assert access.json()["space"] == "/spaces/family"
    issued = await _issue_with(world, {"delegation_token": token})
    assert issued.status_code == 200, issued.text
    agent = await _delegation(world, issued.json()["grant"])
    assert (await _ls(world, agent, "/spaces/family")).status_code == 200


async def test_a_routine_run_cannot_make_routines(world):
    run = (await _delegation(world, await _grant(world)))["Authorization"].removeprefix("Bearer ")
    for response in (
        await _issue_with(world, {"delegation_token": run}),
        await _access_with(world, {"delegation_token": run}),
    ):
        assert (response.status_code, response.json()["detail"]) == (403, "routine_run")


async def test_exactly_one_token(world):
    headers = await identity(world.platform, world.tokens["bob"])
    both = {
        "identity_token": headers["X-HomeAI-Identity"],
        "delegation_token": await _chat_delegation(world, "bob"),
    }
    for body in (both, {}):
        assert (await _issue_with(world, body)).status_code == 422
        assert (await _access_with(world, body)).status_code == 422
    # An identity token isn't a delegation, nor the other way round.
    swapped = {"delegation_token": both["identity_token"]}
    assert (await _issue_with(world, swapped)).status_code == 401
    assert (
        await _access_with(world, {"identity_token": both["delegation_token"]})
    ).status_code == 401
