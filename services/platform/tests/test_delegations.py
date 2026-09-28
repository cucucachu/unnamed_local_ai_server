"""`/internal/delegations*`: agent-run tokens minted from a user's identity (docs/PLATFORM.md §4)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.core.delegations import DELEGATION_TTL, REFRESH_GRACE
from app.core.principal import IDENTITY_TTL
from app.main import create_app
from tests.conftest import Platform, make_settings, running
from tests.helpers import (
    NATIVE,
    PASSWORD,
    bearer,
    bootstrap_admin,
    create_user,
    identity,
    login,
    sql,
    step_up,
)

AGENT_TOKEN = "agent-secret"
EXEC_TOKEN = "exec-secret"
THREAD = "0b7c8f3e-5d7a-4e4b-9f6e-2a1d3c4b5e6f"


@pytest.fixture
async def platform(pg_database, tmp_path: Path):
    settings = make_settings(
        pg_database, tmp_path, platform_agent_token=AGENT_TOKEN, platform_exec_token=EXEC_TOKEN
    )
    app = create_app(settings)
    async with running(app) as client:
        yield Platform(app, client, pg_database, tmp_path)


async def _identity_token(platform, session: str) -> str:
    return (await identity(platform, session))["X-HomeAI-Identity"]


async def _exchange(platform, identity_token: str, thread_id: str = THREAD, service=AGENT_TOKEN):
    return await platform.client.post(
        "/internal/delegations",
        json={"identity_token": identity_token, "thread_id": thread_id},
        headers=bearer(service),
    )


async def _refresh(platform, token: str, service=AGENT_TOKEN):
    return await platform.client.post(
        "/internal/delegations/refresh", json={"token": token}, headers=bearer(service)
    )


async def _delegation(platform, session: str) -> str:
    response = await _exchange(platform, await _identity_token(platform, session))
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _claims(platform, token: str) -> dict:
    return platform.app.state.tokens.verify_token(token, act="agent")


def _strip(claims: dict) -> dict:
    return {k: v for k, v in claims.items() if k not in {"iss", "aud", "iat", "exp"}}


# --- exchange -------------------------------------------------------------------


async def test_exchange_mints_an_agent_token_for_the_session(platform):
    alice = await create_user(platform, "alice")
    session = await login(platform, "alice")
    identity_claims = platform.app.state.tokens.verify_token(
        await _identity_token(platform, session), act="user"
    )

    response = await _exchange(platform, await _identity_token(platform, session))
    assert response.status_code == 200
    body = response.json()
    claims = _claims(platform, body["token"])
    assert claims["act"] == "agent"
    assert claims["thr"] == THREAD
    assert claims["sub"] == str(alice["id"])
    assert claims["sid"] == identity_claims["sid"]
    assert claims["role"] == "member"
    assert claims["exp"] - claims["iat"] == DELEGATION_TTL.total_seconds()
    assert datetime.fromisoformat(body["expires_at"]).timestamp() == claims["exp"]

    me = await platform.client.get("/api/platform/me", headers=bearer(body["token"]))
    assert (me.status_code, me.json()["username"]) == (200, "alice")
    files = await platform.client.get(
        "/api/platform/files", params={"path": "/personal"}, headers=bearer(body["token"])
    )
    assert files.status_code == 200


@pytest.mark.parametrize("service", [None, EXEC_TOKEN, "agent-secre", "hs_whatever"])
async def test_exchange_and_refresh_need_the_agent_service_token(platform, service):
    await create_user(platform, "alice")
    session = await login(platform, "alice")
    headers = bearer(service) if service else {}
    body = {"identity_token": await _identity_token(platform, session), "thread_id": THREAD}
    response = await platform.client.post("/internal/delegations", json=body, headers=headers)
    assert (response.status_code, response.json()) == (401, {"detail": "unauthenticated"})

    token = await _delegation(platform, session)
    response = await platform.client.post(
        "/internal/delegations/refresh", json={"token": token}, headers=headers
    )
    assert response.status_code == 401


async def test_exchange_refuses_anything_but_a_live_identity(platform):
    alice = await create_user(platform, "alice")
    session = await login(platform, "alice")
    tokens = platform.app.state.tokens
    claims = _strip(tokens.verify_token(await _identity_token(platform, session), act="user"))
    expired = tokens.issue_token(
        claims, IDENTITY_TTL, now=datetime.now(UTC) - IDENTITY_TTL - timedelta(seconds=5)
    )
    agent = await _delegation(platform, session)
    for value in (expired, agent, session, "garbage"):
        response = await _exchange(platform, value)
        assert (response.status_code, response.json()) == (401, {"detail": "unauthenticated"})

    identity_token = await _identity_token(platform, session)
    sql(platform, "UPDATE users SET disabled_at = now() WHERE id = %s", (alice["id"],))
    assert (await _exchange(platform, identity_token)).status_code == 401
    sql(platform, "UPDATE users SET disabled_at = NULL WHERE id = %s", (alice["id"],))
    assert (await _exchange(platform, identity_token)).status_code == 200

    await platform.client.post("/api/auth/logout", headers=bearer(session))
    assert (await _exchange(platform, identity_token)).status_code == 401


@pytest.mark.parametrize("thread_id", ["", "a/b", "x" * 65, "t 1", "../x"])
async def test_exchange_validates_the_thread_id(platform, thread_id):
    await create_user(platform, "alice")
    session = await login(platform, "alice")
    response = await _exchange(platform, await _identity_token(platform, session), thread_id)
    assert (response.status_code, response.json()) == (422, {"detail": "invalid_thread_id"})


# --- refresh --------------------------------------------------------------------


async def test_refresh_renews_for_the_same_session_and_thread(platform):
    await create_user(platform, "alice")
    session = await login(platform, "alice")
    tokens = platform.app.state.tokens
    old_claims = _claims(platform, await _delegation(platform, session))
    older = tokens.issue_token(
        _strip(old_claims), DELEGATION_TTL, now=datetime.now(UTC) - timedelta(minutes=10)
    )

    response = await _refresh(platform, older)
    assert response.status_code == 200
    new_claims = _claims(platform, response.json()["token"])
    assert {k: new_claims[k] for k in ("sub", "sid", "thr", "act")} == {
        k: old_claims[k] for k in ("sub", "sid", "thr", "act")
    }
    assert new_claims["exp"] > tokens.verify_token(older, act="agent")["exp"]


async def test_refresh_rereads_the_role(platform):
    await create_user(platform, "alice")
    session = await login(platform, "alice")
    token = await _delegation(platform, session)
    sql(platform, "UPDATE users SET role = 'admin' WHERE username = 'alice'")
    refreshed = (await _refresh(platform, token)).json()["token"]
    assert _claims(platform, refreshed)["role"] == "admin"


async def test_refresh_accepts_a_just_expired_token_only(platform):
    await create_user(platform, "alice")
    session = await login(platform, "alice")
    tokens = platform.app.state.tokens
    claims = _strip(_claims(platform, await _delegation(platform, session)))
    ago = datetime.now(UTC) - DELEGATION_TTL
    recent = tokens.issue_token(claims, DELEGATION_TTL, now=ago - REFRESH_GRACE / 2)
    stale = tokens.issue_token(
        claims, DELEGATION_TTL, now=ago - REFRESH_GRACE - timedelta(seconds=5)
    )

    assert (await _refresh(platform, recent)).status_code == 200
    assert (await _refresh(platform, stale)).status_code == 401


async def test_revoked_session_can_no_longer_obtain_or_refresh(platform):
    await create_user(platform, "alice")
    session = await login(platform, "alice")
    identity_token = await _identity_token(platform, session)
    token = await _delegation(platform, session)
    assert (await _refresh(platform, token)).status_code == 200

    await platform.client.post("/api/auth/logout", headers=bearer(session))

    for response in (await _refresh(platform, token), await _exchange(platform, identity_token)):
        assert (response.status_code, response.json()) == (401, {"detail": "unauthenticated"})
    me = await platform.client.get("/api/platform/me", headers=bearer(token))
    assert me.status_code == 401


async def test_revoking_another_session_or_disabling_the_user_stops_refresh(platform):
    alice = await create_user(platform, "alice")
    phone = await login(platform, "alice", device_label="phone")
    laptop = await login(platform, "alice", device_label="laptop")
    token = await _delegation(platform, laptop)
    laptop_id = _claims(platform, token)["sid"]

    revoked = await platform.client.delete(
        f"/api/platform/me/sessions/{laptop_id}", headers=await identity(platform, phone)
    )
    assert revoked.status_code == 204
    assert (await _refresh(platform, token)).status_code == 401

    token = await _delegation(platform, phone)
    sql(platform, "UPDATE users SET disabled_at = now() WHERE id = %s", (alice["id"],))
    assert (await _refresh(platform, token)).status_code == 401


async def test_refresh_refuses_identity_tokens_and_garbage(platform):
    await create_user(platform, "alice")
    session = await login(platform, "alice")
    for value in (await _identity_token(platform, session), session, "garbage"):
        response = await _refresh(platform, value)
        assert response.status_code == 401


# --- a delegation is never an admin or an auth credential ----------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/platform/admin/users", None),
        ("POST", "/api/platform/admin/invites", {}),
        ("GET", "/api/platform/admin/spaces", None),
        ("PATCH", "/api/platform/me", {"display_name": "Agent Was Here"}),
        ("GET", "/api/platform/me/sessions", None),
        ("POST", "/api/platform/me/totp/enroll", {"password": PASSWORD}),
        ("POST", "/api/platform/spaces", {"slug": "agents", "name": "Agents"}),
    ],
)
async def test_minted_delegation_rejected_by_admin_and_account_routes(platform, method, path, body):
    session = await bootstrap_admin(platform)
    await step_up(platform, session)
    token = await _delegation(platform, session)
    response = await platform.client.request(method, path, json=body, headers=bearer(token))
    assert (response.status_code, response.json()) == (403, {"detail": "agent_not_allowed"})


async def test_minted_delegation_is_not_a_session_credential(platform):
    session = await bootstrap_admin(platform)
    token = await _delegation(platform, session)
    agent = {**bearer(token), **NATIVE}

    assert (await platform.client.get("/internal/auth/verify", headers=agent)).status_code == 401
    status = await platform.client.get("/api/auth/status", headers=agent)
    assert status.json()["authenticated"] is False
    step = await platform.client.post(
        "/api/auth/step-up", json={"password": PASSWORD}, headers=agent
    )
    assert step.status_code == 401
    await platform.client.post("/api/auth/logout", headers=agent)
    assert (
        await platform.client.get("/internal/auth/verify", headers=bearer(session))
    ).status_code == 200
    assert (await _refresh(platform, token)).status_code == 200


async def test_minted_delegation_never_manages_a_space(platform):
    await create_user(platform, "alice")
    session = await login(platform, "alice")
    created = await platform.client.post(
        "/api/platform/spaces",
        json={"slug": "family", "name": "Family"},
        headers=await identity(platform, session),
    )
    space_id = created.json()["id"]
    token = await _delegation(platform, session)
    response = await platform.client.patch(
        f"/api/platform/spaces/{space_id}", json={"name": "Mine now"}, headers=bearer(token)
    )
    assert (response.status_code, response.json()) == (403, {"detail": "agent_not_allowed"})
    listed = await platform.client.get(f"/api/platform/spaces/{space_id}", headers=bearer(token))
    assert listed.status_code == 200
