"""Unit tests for `app/core/delegation.py`: the platform client and the per-connection holder."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from app.core.delegation import (
    Delegation,
    DelegationDenied,
    DelegationUnavailable,
    Grant,
    HttpDelegationClient,
)

BASE = "http://platform.test"
AGENT_TOKEN = "agent-service-token"
T0 = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)


def _grant_json(token: str, expires_at: datetime) -> dict:
    return {"token": token, "expires_at": expires_at.isoformat()}


# --- HttpDelegationClient ------------------------------------------------------------


async def test_exchange_posts_identity_with_service_auth() -> None:
    client = HttpDelegationClient(BASE, AGENT_TOKEN)
    with respx.mock(base_url=BASE) as platform:
        route = platform.post("/internal/delegations").respond(
            json=_grant_json("dlg-1", T0 + timedelta(minutes=15))
        )
        grant = await client.exchange("identity-jwt", "thread-1")

    assert grant == Grant("dlg-1", T0 + timedelta(minutes=15))
    request = route.calls.last.request
    assert request.headers["authorization"] == f"Bearer {AGENT_TOKEN}"
    assert json.loads(request.content) == {
        "identity_token": "identity-jwt",
        "thread_id": "thread-1",
    }


async def test_refresh_posts_the_current_token() -> None:
    client = HttpDelegationClient(BASE, AGENT_TOKEN)
    with respx.mock(base_url=BASE) as platform:
        route = platform.post("/internal/delegations/refresh").respond(
            json=_grant_json("dlg-2", T0 + timedelta(minutes=30))
        )
        grant = await client.refresh("dlg-1")

    assert grant.token == "dlg-2"
    assert json.loads(route.calls.last.request.content) == {"token": "dlg-1"}


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (httpx.Response(401, json={"detail": "unauthenticated"}), DelegationDenied),
        (httpx.Response(403, json={"detail": "forbidden"}), DelegationUnavailable),
        (httpx.Response(503), DelegationUnavailable),
        (httpx.ConnectError("refused"), DelegationUnavailable),
    ],
)
async def test_client_errors(reply, error) -> None:
    client = HttpDelegationClient(BASE, AGENT_TOKEN)
    with respx.mock(base_url=BASE) as platform:
        route = platform.post("/internal/delegations")
        if isinstance(reply, Exception):
            route.mock(side_effect=reply)
        else:
            route.mock(return_value=reply)
        with pytest.raises(error):
            await client.exchange("identity-jwt", "thread-1")


async def test_client_without_service_token_never_calls() -> None:
    client = HttpDelegationClient(BASE, "")
    with respx.mock(base_url=BASE) as platform, pytest.raises(DelegationUnavailable):
        await client.exchange("identity-jwt", "thread-1")
    assert not platform.calls


# --- Delegation ----------------------------------------------------------------------


class ScriptedClient:
    """Answers refreshes from a script: a `Grant`, or an exception to raise."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.refreshed: list[str] = []

    async def exchange(self, identity_token: str, thread_id: str) -> Grant:
        return Grant(f"dlg-{thread_id}", T0 + timedelta(minutes=15))

    async def refresh(self, token: str) -> Grant:
        self.refreshed.append(token)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += timedelta(seconds=seconds)


async def test_obtain_and_refresh() -> None:
    client = ScriptedClient(Grant("dlg-2", T0 + timedelta(minutes=30)))
    clock = Clock(T0)
    delegation = await Delegation.obtain(client, "identity-jwt", "t1")
    delegation._clock = clock

    assert delegation.token == "dlg-t1"
    await delegation.refresh()
    assert client.refreshed == ["dlg-t1"]
    assert delegation.token == "dlg-2"
    assert delegation.expires_at == T0 + timedelta(minutes=30)


async def test_expired_delegation_has_no_token() -> None:
    clock = Clock(T0)
    delegation = Delegation(ScriptedClient(), Grant("dlg", T0 + timedelta(seconds=1)), clock=clock)
    assert delegation.token == "dlg"
    clock.now += timedelta(seconds=1)
    assert delegation.token is None


async def test_denied_is_final() -> None:
    client = ScriptedClient(DelegationDenied("revoked"), Grant("never", T0 + timedelta(hours=1)))
    delegation = Delegation(client, Grant("dlg", T0 + timedelta(minutes=15)), clock=Clock(T0))

    with pytest.raises(DelegationDenied):
        await delegation.refresh()
    assert delegation.denied and delegation.token is None
    with pytest.raises(DelegationDenied):
        await delegation.refresh()
    assert client.refreshed == ["dlg"]


async def test_unavailable_keeps_the_current_grant() -> None:
    client = ScriptedClient(DelegationUnavailable("503"))
    delegation = Delegation(client, Grant("dlg", T0 + timedelta(minutes=15)), clock=Clock(T0))

    with pytest.raises(DelegationUnavailable):
        await delegation.refresh()
    assert delegation.token == "dlg" and not delegation.denied


async def test_keep_alive_refreshes_inside_the_margin_and_stops_when_denied() -> None:
    clock = Clock(T0)
    client = ScriptedClient(
        Grant("dlg-2", T0 + timedelta(minutes=25)),
        DelegationUnavailable("blip"),
        Grant("dlg-3", T0 + timedelta(minutes=35)),
        DelegationDenied("revoked"),
    )
    delegation = Delegation(
        client, Grant("dlg-1", T0 + timedelta(minutes=15)), clock=clock, sleep=clock.sleep
    )

    await delegation.keep_alive(margin=timedelta(minutes=5), retry_s=30)

    # Sleeps until 5 min before each expiry, retries 30 s after a failure.
    assert clock.sleeps == [600.0, 600.0, 30.0, 570.0]
    assert client.refreshed == ["dlg-1", "dlg-2", "dlg-2", "dlg-3"]
    assert delegation.denied and delegation.token is None


def test_copies_are_the_same_object_and_repr_hides_the_token() -> None:
    delegation = Delegation(ScriptedClient(), Grant("dlg-secret", T0))
    assert copy.copy(delegation) is delegation
    assert copy.deepcopy({"d": delegation})["d"] is delegation
    assert "dlg-secret" not in repr(delegation)
    assert "dlg-secret" not in str(delegation)
