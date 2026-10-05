"""`/api/routines` (M17-02) on the in-memory stores, two users sharing one app."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from langgraph.checkpoint.memory import MemorySaver
from starlette.testclient import TestClient

from app.api import routines as routines_api
from app.core.identity import IDENTITY_HEADER, Identity, IdentityError
from app.db.settings import InMemorySettingsStore
from app.db.threads import InMemoryThreadStore
from app.main import create_app
from tests.fake_model.scripting import FakeModel, TextTurn
from tests.fake_platform.scripting import FakePlatform

ALICE = "00000000-0000-4000-8000-0000000000a1"
BOB = "00000000-0000-4000-8000-0000000000b2"
USERS = {"alice-token": ALICE, "bob-token": BOB}
NOW = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)  # Mon 07:00 PDT


class HeaderIdentityVerifier:
    async def verify(self, token: str | None) -> Identity:
        if token not in USERS:
            raise IdentityError("unknown")
        return Identity(user_id=USERS[token], session_id=f"s-{token}", role="member")


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch) -> None:
    monkeypatch.setattr(routines_api, "_now", lambda: NOW)


@pytest.fixture
def client(fake_model: FakeModel, fake_platform: FakePlatform):
    for token, user_id in USERS.items():
        fake_platform.identities[token] = (user_id, f"s-{token}")
    fake_platform.add_space("family", {ALICE: "editor", BOB: "viewer"})
    thread_store = InMemoryThreadStore()
    app = create_app(
        fake_model.settings(platform_url=fake_platform.base_url),
        checkpointer_override=MemorySaver(),
        thread_store_override=thread_store,
        settings_store_override=InMemorySettingsStore(),
        identity_verifier_override=HeaderIdentityVerifier(),
        delegation_client_override=fake_platform.client(),
    )
    with TestClient(app) as test_client:
        yield test_client


def _as(token: str) -> dict[str, str]:
    return {IDENTITY_HEADER: token}


ALICE_H, BOB_H = _as("alice-token"), _as("bob-token")


def _routine(**overrides) -> dict:
    return {
        "name": "Morning brief",
        "prompt": "Summarize my calendar.",
        "schedule": {"kind": "weekdays", "time": "08:30"},
        "timezone": "America/Los_Angeles",
        **overrides,
    }


def _create(client: TestClient, headers=ALICE_H, **overrides) -> dict:
    response = client.post("/api/routines", json=_routine(**overrides), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_computes_the_next_run(client) -> None:
    routine = _create(client)
    assert routine["space"] == "/personal"
    assert routine["enabled"] is True
    assert routine["schedule"] == {"kind": "weekdays", "time": "08:30:00"}
    assert routine["next_run_at"] == "2026-10-05T15:30:00Z"
    assert routine["last_run_at"] is None


def test_crud_round_trip(client) -> None:
    first = _create(client, name="b second")
    second = _create(client, name="A first")
    listing = client.get("/api/routines", headers=ALICE_H).json()
    assert [r["id"] for r in listing] == [second["id"], first["id"]]
    assert client.get(f"/api/routines/{first['id']}", headers=ALICE_H).json() == first

    patched = client.patch(
        f"/api/routines/{first['id']}",
        json={"name": "Renamed", "schedule": {"kind": "monthly", "day": 31, "time": "09:00"}},
        headers=ALICE_H,
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["name"] == "Renamed"
    assert body["next_run_at"] == "2026-10-31T16:00:00Z"

    disabled = client.patch(
        f"/api/routines/{first['id']}", json={"enabled": False}, headers=ALICE_H
    ).json()
    assert disabled["enabled"] is False and disabled["next_run_at"] is None
    enabled = client.patch(
        f"/api/routines/{first['id']}", json={"enabled": True}, headers=ALICE_H
    ).json()
    assert enabled["next_run_at"] == "2026-10-31T16:00:00Z"

    assert client.delete(f"/api/routines/{first['id']}", headers=ALICE_H).status_code == 204
    assert client.get(f"/api/routines/{first['id']}", headers=ALICE_H).status_code == 404
    assert client.delete(f"/api/routines/{first['id']}", headers=ALICE_H).status_code == 404


def test_other_users_cant_see_or_touch_a_routine(client) -> None:
    routine = _create(client)
    path = f"/api/routines/{routine['id']}"
    assert client.get("/api/routines", headers=BOB_H).json() == []
    assert client.get(path, headers=BOB_H).status_code == 404
    assert client.patch(path, json={"name": "x"}, headers=BOB_H).status_code == 404
    assert client.post(f"{path}/run", headers=BOB_H).status_code == 404
    assert client.get(f"{path}/runs", headers=BOB_H).status_code == 404
    assert client.delete(path, headers=BOB_H).status_code == 404
    assert client.get(path, headers=ALICE_H).status_code == 200
    assert client.get("/api/routines").status_code == 401


def test_space_needs_edit_rights(client) -> None:
    shared = _create(client, space="spaces/Family/")
    assert shared["space"] == "/spaces/family"

    viewer = client.post("/api/routines", json=_routine(space="/spaces/family"), headers=BOB_H)
    assert viewer.status_code == 403
    unknown = client.post("/api/routines", json=_routine(space="/spaces/nope"), headers=ALICE_H)
    assert unknown.status_code == 422

    mine = _create(client, headers=BOB_H)
    moved = client.patch(
        f"/api/routines/{mine['id']}", json={"space": "/spaces/family"}, headers=BOB_H
    )
    assert moved.status_code == 403


@pytest.mark.parametrize(
    "overrides",
    [
        {"timezone": "Mars/Olympus"},
        {"schedule": {"kind": "daily"}},
        {"schedule": {"kind": "once", "at": "2026-10-01T08:00"}},
        {"name": "  "},
        {"prompt": ""},
        {"owner_user_id": BOB},
    ],
)
def test_invalid_routines_are_refused(client, overrides) -> None:
    response = client.post("/api/routines", json=_routine(**overrides), headers=ALICE_H)
    assert response.status_code == 422, response.text


def test_a_past_one_shot_may_be_saved_disabled(client) -> None:
    routine = _create(client, schedule={"kind": "once", "at": "2026-10-01T08:00"}, enabled=False)
    assert routine["next_run_at"] is None
    response = client.patch(
        f"/api/routines/{routine['id']}", json={"enabled": True}, headers=ALICE_H
    )
    assert response.status_code == 422


def _wait_for_reply(client: TestClient, thread_id: str) -> list[dict]:
    deadline = time.monotonic() + 10
    while client.get(f"/api/threads/{thread_id}/state", headers=ALICE_H).json()["running"]:
        assert time.monotonic() < deadline
        time.sleep(0.05)
    return client.get(f"/api/threads/{thread_id}/messages", headers=ALICE_H).json()


def test_run_now_starts_a_run_thread(client, fake_model: FakeModel, fake_platform) -> None:
    routine = _create(client, space="/spaces/family")
    fake_model.queue(TextTurn("first brief"), TextTurn("second brief"))

    first = client.post(f"/api/routines/{routine['id']}/run", headers=ALICE_H)
    assert first.status_code == 202, first.text
    messages = _wait_for_reply(client, first.json()["thread_id"])
    assert messages[0]["role"] == "user"
    assert "(space /spaces/family)" in messages[0]["content"]
    assert messages[0]["content"].endswith("Summarize my calendar.")
    assert messages[-1]["content"] == "first brief"
    # The run acts as Alice, through a delegation for its own thread.
    assert fake_platform.exchanges[-1] == ("alice-token", first.json()["thread_id"])

    second = client.post(f"/api/routines/{routine['id']}/run", headers=ALICE_H).json()
    _wait_for_reply(client, second["thread_id"])

    runs = client.get(f"/api/routines/{routine['id']}/runs", headers=ALICE_H).json()
    assert [r["id"] for r in runs] == [second["thread_id"], first.json()["thread_id"]]
    assert all(r["routine_id"] == routine["id"] for r in runs)
    assert runs[0]["title"].startswith("Morning brief · ")

    filtered = client.get(f"/api/threads?routine_id={routine['id']}", headers=ALICE_H).json()
    assert [t["id"] for t in filtered] == [r["id"] for r in runs]
    assert client.get(f"/api/routines/{routine['id']}", headers=ALICE_H).json()["last_run_at"]

    # Deleting the routine keeps its runs as ordinary chats.
    client.delete(f"/api/routines/{routine['id']}", headers=ALICE_H)
    threads = client.get("/api/threads", headers=ALICE_H).json()
    assert {t["id"] for t in threads} >= {r["id"] for r in runs}
    assert all(t["routine_id"] is None for t in threads)


def test_run_now_rechecks_the_space(client, fake_platform) -> None:
    routine = _create(client, space="/spaces/family")
    fake_platform.members["family"][ALICE] = "viewer"
    response = client.post(f"/api/routines/{routine['id']}/run", headers=ALICE_H)
    assert response.status_code == 403
    assert client.get("/api/threads", headers=ALICE_H).json() == []
