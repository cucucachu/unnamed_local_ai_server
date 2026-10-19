"""M22-12: `GET /apps/changes`, the indexed app rows changed since a time (`appindex.changes`).

World: alice owns /spaces/family, bob edits it, carol views it, dave is out.
The notes template indexes `notes` (title, body).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.main import create_app
from tests.conftest import Platform, make_settings, running
from tests.files_world import API, World, build_world
from tests.helpers import bearer, identity, sql
from tests.test_app_build import FakeBuilder
from tests.test_app_search import _add, _notes, _run, index
from tests.test_file_search import FakeEmbedder

AGENT_TOKEN = "agent-secret"
ROUTINE = "4c0e9b5e-2f7d-4f43-9a51-7f3a3f2d1e10"


@pytest.fixture
async def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
async def world(pg_database, tmp_path, embedder):
    settings = make_settings(
        pg_database,
        tmp_path,
        file_index_enabled=True,
        file_index_scan_interval_s=3600,
        platform_agent_token=AGENT_TOKEN,
    )
    app = create_app(settings, embedder_override=embedder)
    async with running(app) as client:
        yield await build_world(Platform(app, client, pg_database, tmp_path))


@pytest.fixture(autouse=True)
def builder(world: World) -> FakeBuilder:
    fake = FakeBuilder(world.platform.app.state.builds.root)
    world.platform.app.state.builder = fake
    return fake


async def _routine_run(world: World, username: str, space: str = "/personal") -> dict[str, str]:
    """A routine run's delegation, scoped to `space` like the Memory routine's."""
    headers = await identity(world.platform, world.tokens[username])
    response = await world.client.post(
        "/internal/routine-grants",
        json={
            "identity_token": headers["X-HomeAI-Identity"],
            "routine_id": ROUTINE,
            "space": space,
            "label": "Memory",
        },
        headers=bearer(AGENT_TOKEN),
    )
    assert response.status_code == 200, response.text
    response = await world.client.post(
        "/internal/routine-grants/exchange",
        json={"grant": response.json()["grant"], "routine_id": ROUTINE, "thread_id": "run-1"},
        headers=bearer(AGENT_TOKEN),
    )
    assert response.status_code == 200, response.text
    return bearer(response.json()["token"])


async def _changes(world: World, who: str | dict, since: datetime | str, **params) -> dict:
    headers = world.headers[who] if isinstance(who, str) else who
    since = since if isinstance(since, str) else since.isoformat()
    response = await world.client.get(
        f"{API}/apps/changes", params={"since": since, **params}, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def _titles(body: dict) -> list[str]:
    return [row["title"] for row in body["rows"]]


async def _seed(world: World) -> tuple[dict, dict]:
    """alice's personal notes, and the family's notes with a row from bob and one from alice."""
    mine = await _notes(world, "alice", world.personal("alice")["id"])
    family = await _notes(world, "alice", world.family["id"])
    await _add(world, "alice", mine, "Allergy", "I'm allergic to penicillin.")
    await _add(world, "bob", family, "Bob's shift", "Bob works nights in November.")
    await _add(world, "alice", family, "Alice's pick", "Alice is collecting the kids on Fridays.")
    await index(world).drain()
    return mine, family


async def test_rows_changed_since_come_oldest_first_with_who_wrote_them(world) -> None:
    start = datetime.now(UTC) - timedelta(seconds=1)
    mine, family = await _seed(world)

    body = await _changes(world, "alice", start)
    assert body["total"] == 3
    assert _titles(body) == ["Allergy", "Bob's shift", "Alice's pick"]
    allergy, shift, pick = body["rows"]
    assert allergy["instance_id"] == mine["id"]
    assert allergy["app"] == {"slug": "notes", "name": "Notes"}
    assert (allergy["space"]["kind"], allergy["table"], allergy["row_id"]) == (
        "personal",
        "notes",
        1,
    )
    assert allergy["text"] == "Allergy\nI'm allergic to penicillin."
    assert allergy["created_by"]["you"] and allergy["updated_by"]["you"]
    assert shift["space"]["slug"] == "family"
    assert shift["created_by"]["username"] == "bob" and not shift["created_by"]["you"]
    assert pick["updated_by"]["you"]
    assert allergy["updated_at"] < shift["updated_at"] < pick["updated_at"]

    # Paging, and `since` is exclusive.
    page = await _changes(world, "alice", start, offset=1, limit=1)
    assert (page["total"], page["offset"], _titles(page)) == (3, 1, ["Bob's shift"])
    after_shift = datetime.fromisoformat(shift["updated_at"])
    assert _titles(await _changes(world, "alice", after_shift)) == ["Alice's pick"]

    # An edit moves the row to the end, now last changed by alice, still created by bob.
    await asyncio.sleep(0.01)
    await _run(world, "alice", family, "UPDATE notes SET body = ? WHERE id = 1", ["Days now."])
    await index(world).drain()
    body = await _changes(world, "alice", start)
    edited = body["rows"][-1]
    assert (edited["title"], edited["text"]) == ("Bob's shift", "Bob's shift\nDays now.")
    assert edited["created_by"]["username"] == "bob" and edited["updated_by"]["you"]


async def test_each_user_sees_only_their_spaces(world) -> None:
    start = datetime.now(UTC) - timedelta(seconds=1)
    await _seed(world)
    assert _titles(await _changes(world, "bob", start)) == ["Bob's shift", "Alice's pick"]
    assert _titles(await _changes(world, "carol", start)) == ["Bob's shift", "Alice's pick"]
    assert (await _changes(world, "dave", start))["rows"] == []
    agent = await world.agent("bob")
    assert _titles(await _changes(world, agent, start)) == ["Bob's shift", "Alice's pick"]


async def test_a_routine_run_sees_only_the_users_own_rows_outside_its_space(world) -> None:
    start = datetime.now(UTC) - timedelta(seconds=1)
    _, family = await _seed(world)
    run = await _routine_run(world, "alice")
    assert _titles(await _changes(world, run, start)) == ["Allergy", "Alice's pick"]

    # bob edits alice's family row: it's his now, so her run no longer sees it.
    await _run(world, "bob", family, "UPDATE notes SET body = ? WHERE id = 2", ["Bob will."])
    await index(world).drain()
    assert _titles(await _changes(world, run, start)) == ["Allergy"]

    # A run scoped to the family space sees all of that space, and alice's personal rows.
    family_run = await _routine_run(world, "alice", "/spaces/family")
    assert _titles(await _changes(world, family_run, start)) == [
        "Allergy",
        "Bob's shift",
        "Alice's pick",
    ]


async def test_rows_indexed_before_attribution_are_rendered_again(world, embedder) -> None:
    start = datetime.now(UTC) - timedelta(seconds=1)
    await _seed(world)
    await index(world).scan()
    embedded = len(embedder.documents)
    sql(
        world.platform,
        "UPDATE indexed_app_rows SET text = '', created_by = NULL, updated_by = NULL, "
        "attributed = false",
    )
    await index(world).scan()
    body = await _changes(world, "alice", start)
    assert [row["text"].split("\n")[0] for row in body["rows"]] == _titles(body)
    assert all(row["updated_by"] for row in body["rows"])
    assert len(embedder.documents) == embedded  # same content, embeddings reused


async def test_since_is_required_and_a_naive_time_is_utc(world) -> None:
    await _seed(world)
    response = await world.client.get(f"{API}/apps/changes", headers=world.headers["alice"])
    assert response.status_code == 422
    naive = (datetime.now(UTC) - timedelta(minutes=5)).replace(tzinfo=None).isoformat()
    assert (await _changes(world, "alice", naive))["total"] == 3
    assert (await _changes(world, "alice", datetime.now(UTC) + timedelta(minutes=5)))["total"] == 0
