"""HITL markers: an agent approves a destructive migration only after its user did (`app.core.hitl`).

World as in `test_appdata_api.py`: the `hello` app is installed in
/spaces/family (alice owns, bob edits, carol views, dave isn't a member),
and each test starts with a pending destructive migration.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app.core.hitl import HitlApprovals
from app.main import create_app
from tests.conftest import Platform, make_settings, running
from tests.files_world import World
from tests.helpers import bearer, identity, login
from tests.test_appdata_api import _base, _migrate, _ready, _rpc, _schema
from tests.test_appdata_api import inst as _inst

AGENT_TOKEN = "agent-secret"
EXEC_TOKEN = "exec-secret"


@pytest.fixture
async def platform(pg_database, tmp_path: Path):
    settings = make_settings(
        pg_database, tmp_path, platform_agent_token=AGENT_TOKEN, platform_exec_token=EXEC_TOKEN
    )
    app = create_app(settings)
    async with running(app) as client:
        yield Platform(app, client, pg_database, tmp_path)


inst = _inst


@pytest.fixture
async def pending(world: World, inst: dict) -> dict:
    await _ready(world, inst)
    await _rpc(
        world, inst, "alice", {"op": "run", "sql": "INSERT INTO items (name) VALUES ('Milk')"}
    )
    _schema(world, "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL);\n")
    m = (await _migrate(world, inst, "bob")).json()
    assert m["status"] == "pending"
    return m


def _delegation(world: World, username: str, thread: str = "t-1", *, sid: str | None = None) -> str:
    """An act=agent token for `username`'s login session on `thread`."""
    tokens = world.platform.app.state.tokens
    claims = tokens.verify_token(world.headers[username]["X-HomeAI-Identity"], act="user")
    payload = {"sub": claims["sub"], "sid": sid or claims["sid"], "role": claims["role"]}
    return tokens.issue_token(payload | {"act": "agent", "thr": thread}, timedelta(minutes=15))


async def _mint(world: World, delegation: str, inst: dict, migration_id: str, service=AGENT_TOKEN):
    return await world.client.post(
        "/internal/hitl-approvals",
        json={"delegation": delegation, "instance_id": inst["id"], "migration_id": migration_id},
        headers=bearer(service),
    )


async def _approve(world: World, inst: dict, migration_id: str, delegation: str, marker=None):
    headers = bearer(delegation)
    if marker is not None:
        headers["X-HomeAI-HITL-Approval"] = marker
    return await world.client.post(
        f"{_base(inst)}/migrations/{migration_id}/approve", headers=headers
    )


async def _status(world: World, inst: dict, migration_id: str) -> str:
    r = await world.client.get(f"{_base(inst)}/migrations", headers=world.headers["alice"])
    return next(m["status"] for m in r.json()["migrations"] if m["id"] == migration_id)


REFUSED = (403, {"detail": "hitl_approval_required"})


async def test_an_agent_without_a_marker_is_refused(world, inst, pending) -> None:
    r = await _approve(world, inst, pending["id"], _delegation(world, "bob"))
    assert (r.status_code, r.json()) == REFUSED
    assert await _status(world, inst, pending["id"]) == "pending"


async def test_the_human_approved_path_applies_it(world, inst, pending) -> None:
    delegation = _delegation(world, "bob")
    minted = await _mint(world, delegation, inst, pending["id"])
    assert minted.status_code == 200, minted.text
    marker = minted.json()["token"]
    assert marker.startswith("hitl_") and minted.json()["expires_in_s"] == 60
    r = await _approve(world, inst, pending["id"], delegation, marker)
    assert r.status_code == 200, r.text
    assert (r.json()["status"], r.json()["decided_by"]) == (
        "applied",
        str(world.users["bob"]["id"]),
    )


async def test_a_forged_marker_is_refused(world, inst, pending) -> None:
    delegation = _delegation(world, "bob")
    for marker in ("hitl_" + "A" * 43, "", "not-a-marker"):
        r = await _approve(world, inst, pending["id"], delegation, marker)
        assert (r.status_code, r.json()) == REFUSED
    assert await _status(world, inst, pending["id"]) == "pending"


async def test_a_marker_is_single_use(world, inst, pending) -> None:
    delegation = _delegation(world, "bob")
    marker = (await _mint(world, delegation, inst, pending["id"])).json()["token"]
    assert (await _approve(world, inst, pending["id"], delegation, marker)).status_code == 200
    r = await _approve(world, inst, pending["id"], delegation, marker)
    assert (r.status_code, r.json()) == REFUSED


async def test_a_mismatched_use_spends_the_marker(world, inst, pending) -> None:
    delegation = _delegation(world, "bob")
    marker = (await _mint(world, delegation, inst, pending["id"])).json()["token"]
    other = "00000000-0000-4000-8000-000000000000"
    assert (await _approve(world, inst, other, delegation, marker)).status_code == 403
    r = await _approve(world, inst, pending["id"], delegation, marker)
    assert (r.status_code, r.json()) == REFUSED
    assert await _status(world, inst, pending["id"]) == "pending"


async def test_an_expired_marker_is_refused(world, inst, pending) -> None:
    now = [1000.0]
    world.platform.app.state.hitl = HitlApprovals(clock=lambda: now[0])
    delegation = _delegation(world, "bob")
    marker = (await _mint(world, delegation, inst, pending["id"])).json()["token"]
    now[0] += 61
    r = await _approve(world, inst, pending["id"], delegation, marker)
    assert (r.status_code, r.json()) == REFUSED
    assert await _status(world, inst, pending["id"]) == "pending"


async def test_a_marker_for_an_older_migration_is_refused(world, inst, pending) -> None:
    delegation = _delegation(world, "bob")
    marker = (await _mint(world, delegation, inst, pending["id"])).json()["token"]
    _schema(world, "CREATE TABLE items (id INTEGER PRIMARY KEY);\n")
    newer = (await _migrate(world, inst, "bob")).json()
    assert newer["status"] == "pending" and newer["id"] != pending["id"]
    r = await _approve(world, inst, newer["id"], delegation, marker)
    assert (r.status_code, r.json()) == REFUSED
    assert await _status(world, inst, newer["id"]) == "pending"


@pytest.mark.parametrize(
    ("used_by", "thread"),
    [("bob", "t-2"), ("alice", "t-1")],
    ids=["another-thread", "another-user"],
)
async def test_a_marker_is_bound_to_its_thread_and_user(
    world,
    inst,
    pending,
    used_by,
    thread,
) -> None:
    marker = (await _mint(world, _delegation(world, "bob"), inst, pending["id"])).json()["token"]
    r = await _approve(world, inst, pending["id"], _delegation(world, used_by, thread), marker)
    assert (r.status_code, r.json()) == REFUSED
    assert await _status(world, inst, pending["id"]) == "pending"


async def test_a_marker_is_bound_to_its_session(world, inst, pending) -> None:
    marker = (await _mint(world, _delegation(world, "bob"), inst, pending["id"])).json()["token"]
    second = await identity(world.platform, await login(world.platform, "bob"))
    sid = world.platform.app.state.tokens.verify_token(second["X-HomeAI-Identity"], act="user")[
        "sid"
    ]
    r = await _approve(world, inst, pending["id"], _delegation(world, "bob", sid=sid), marker)
    assert (r.status_code, r.json()) == REFUSED


async def test_people_approve_without_a_marker(world, inst, pending) -> None:
    headers = world.headers["alice"] | {"X-HomeAI-HITL-Approval": "ignored"}
    r = await world.client.post(
        f"{_base(inst)}/migrations/{pending['id']}/approve", headers=headers
    )
    assert (r.status_code, r.json()["status"]) == (200, "applied")


async def test_rejecting_needs_no_marker(world, inst, pending) -> None:
    r = await world.client.post(
        f"{_base(inst)}/migrations/{pending['id']}/reject",
        headers=bearer(_delegation(world, "bob")),
    )
    assert (r.status_code, r.json()["status"]) == (200, "rejected")


@pytest.mark.parametrize("service", [EXEC_TOKEN, "", AGENT_TOKEN + "x"])
async def test_minting_needs_the_agent_service_token(world, inst, pending, service) -> None:
    r = await _mint(world, _delegation(world, "bob"), inst, pending["id"], service=service)
    assert (r.status_code, r.json()) == (401, {"detail": "unauthenticated"})


async def test_minting_checks_the_delegation_and_the_migration(world, inst, pending) -> None:
    identity = world.headers["bob"]["X-HomeAI-Identity"]
    assert (await _mint(world, identity, inst, pending["id"])).status_code == 401
    assert (await _mint(world, "not-a-jwt", inst, pending["id"])).status_code == 401
    carol = await _mint(world, _delegation(world, "carol"), inst, pending["id"])
    assert (carol.status_code, carol.json()) == (403, {"detail": "insufficient_role"})
    dave = await _mint(world, _delegation(world, "dave"), inst, pending["id"])
    assert dave.status_code == 404
    unknown = await _mint(
        world, _delegation(world, "bob"), inst, "00000000-0000-4000-8000-000000000000"
    )
    assert unknown.status_code == 404
    await world.client.post(
        f"{_base(inst)}/migrations/{pending['id']}/reject", headers=world.headers["alice"]
    )
    done = await _mint(world, _delegation(world, "bob"), inst, pending["id"])
    assert (done.status_code, done.json()) == (409, {"detail": "migration_not_pending"})
