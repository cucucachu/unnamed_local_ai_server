"""`/api/platform/*`: principal resolution, self-service, admin users, invites."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest

from app.core.principal import IDENTITY_TTL
from app.core.tokens import TokenService, load_or_create_signing_key
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
    stepped_up_admin,
)


async def _claims(platform, token: str) -> dict:
    headers = await identity(platform, token)
    return platform.app.state.tokens.verify_token(headers["X-HomeAI-Identity"], act="user")


def _delegation(platform, claims: dict, **overrides) -> dict[str, str]:
    """An act=agent token for the same session, as the delegation endpoint mints."""
    payload = {
        "sub": claims["sub"],
        "sid": claims["sid"],
        "role": claims["role"],
        "act": "agent",
        "thr": "thread-1",
        **overrides,
    }
    return bearer(platform.app.state.tokens.issue_token(payload, timedelta(minutes=15)))


# --- principal resolution -----------------------------------------------------


async def test_requires_a_credential(platform):
    for path in ("/api/platform/me", "/api/platform/admin/users"):
        response = await platform.client.get(path)
        assert (response.status_code, response.json()) == (401, {"detail": "unauthenticated"})


async def test_session_token_is_not_accepted_directly(platform):
    await create_user(platform, "alice")
    token = await login(platform, "alice")
    response = await platform.client.get("/api/platform/me", headers=bearer(token))
    assert response.status_code == 401


async def test_forged_or_wrong_identity_rejected(platform, tmp_path):
    await create_user(platform, "alice")
    claims = await _claims(platform, await login(platform, "alice"))
    other = TokenService(load_or_create_signing_key(tmp_path / "other"))
    forged = other.issue_token(_strip(claims), IDENTITY_TTL)
    agent_in_header = platform.app.state.tokens.issue_token(
        {**_strip(claims), "act": "agent"}, IDENTITY_TTL
    )
    expired = platform.app.state.tokens.issue_token(
        _strip(claims), IDENTITY_TTL, now=datetime.now(UTC) - timedelta(minutes=10)
    )
    for value in (forged, agent_in_header, expired, "garbage"):
        response = await platform.client.get(
            "/api/platform/me", headers={"X-HomeAI-Identity": value}
        )
        assert response.status_code == 401


def _strip(claims: dict) -> dict:
    return {k: v for k, v in claims.items() if k not in {"iss", "aud", "iat", "exp"}}


async def test_identity_header_wins_over_bearer(platform):
    await create_user(platform, "alice")
    await create_user(platform, "bob")
    alice = await _claims(platform, await login(platform, "alice"))
    bob_headers = await identity(platform, await login(platform, "bob"))

    headers = {**bob_headers, **_delegation(platform, alice)}
    assert (await platform.client.get("/api/platform/me", headers=headers)).json()[
        "username"
    ] == "bob"

    # A bad identity header is final; the bearer isn't tried.
    headers = {"X-HomeAI-Identity": "garbage", **_delegation(platform, alice)}
    assert (await platform.client.get("/api/platform/me", headers=headers)).status_code == 401


async def test_delegation_rechecks_session_and_user(platform):
    alice = await create_user(platform, "alice")
    token = await login(platform, "alice")
    claims = await _claims(platform, token)
    headers = _delegation(platform, claims)

    me = await platform.client.get("/api/platform/me", headers=headers)
    assert me.json()["username"] == "alice"

    sql(platform, "UPDATE users SET disabled_at = now() WHERE id = %s", (alice["id"],))
    assert (await platform.client.get("/api/platform/me", headers=headers)).status_code == 401
    sql(platform, "UPDATE users SET disabled_at = NULL WHERE id = %s", (alice["id"],))
    assert (await platform.client.get("/api/platform/me", headers=headers)).status_code == 200

    await platform.client.post("/api/auth/logout", headers=bearer(token))
    assert (await platform.client.get("/api/platform/me", headers=headers)).status_code == 401


async def test_delegation_cannot_name_another_users_session(platform):
    await create_user(platform, "alice")
    bob = await create_user(platform, "bob")
    alice_claims = await _claims(platform, await login(platform, "alice"))
    headers = _delegation(platform, alice_claims, sub=str(bob["id"]))
    assert (await platform.client.get("/api/platform/me", headers=headers)).status_code == 401
    headers = _delegation(platform, alice_claims, sid=str(uuid4()))
    assert (await platform.client.get("/api/platform/me", headers=headers)).status_code == 401


async def test_identity_role_comes_from_database(platform):
    """A demoted admin loses admin routes immediately, not when their JWT expires."""
    token = await bootstrap_admin(platform)
    await create_user(platform, "backup", role="admin")
    await step_up(platform, token)
    headers = await identity(platform, token)
    assert (
        await platform.client.get("/api/platform/admin/users", headers=headers)
    ).status_code == 200
    sql(platform, "UPDATE users SET role = 'member' WHERE username = 'root'")
    response = await platform.client.get("/api/platform/admin/users", headers=headers)
    assert response.json() == {"detail": "admin_required"}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/platform/admin/users", None),
        ("PATCH", f"/api/platform/admin/users/{uuid4()}", {"role": "member"}),
        ("GET", "/api/platform/admin/invites", None),
        ("POST", "/api/platform/admin/invites", {}),
        ("DELETE", f"/api/platform/admin/invites/{uuid4()}", None),
        ("GET", "/api/platform/admin/spaces", None),
        ("POST", "/api/platform/spaces", {"slug": "agents", "name": "Agents"}),
        ("GET", "/api/platform/users/directory", None),
        ("PATCH", "/api/platform/me", {"display_name": "Agent Was Here"}),
        ("GET", "/api/platform/me/sessions", None),
        ("DELETE", f"/api/platform/me/sessions/{uuid4()}", None),
        ("POST", "/api/platform/me/totp/enroll", {"password": PASSWORD}),
        ("POST", "/api/platform/me/totp/confirm", {"code": "123456"}),
        ("POST", "/api/platform/me/totp/disable", {"password": PASSWORD}),
        ("GET", "/api/platform/me/wireguard-devices", None),
        ("POST", "/api/platform/me/wireguard-devices", {"name": "phone"}),
        ("DELETE", f"/api/platform/me/wireguard-devices/{uuid4()}", None),
    ],
)
async def test_agent_act_rejected_even_for_stepped_up_admin(platform, method, path, body):
    token = await bootstrap_admin(platform)
    await step_up(platform, token)
    headers = _delegation(platform, await _claims(platform, token))
    response = await platform.client.request(method, path, json=body, headers=headers)
    assert (response.status_code, response.json()) == (403, {"detail": "agent_not_allowed"})


# --- self-service ---------------------------------------------------------------


async def test_me_profile_and_password_change(platform):
    await create_user(platform, "alice")
    token, other = await login(platform, "alice"), await login(platform, "alice")
    headers = await identity(platform, token)
    client = platform.client

    me = await client.get("/api/platform/me", headers=headers)
    assert me.json()["display_name"] == "Alice"
    renamed = await client.patch(
        "/api/platform/me", json={"display_name": "  Alice A.  "}, headers=headers
    )
    assert renamed.json()["display_name"] == "Alice A."

    cases = [
        ({"password": "new password 1"}, 422, "current_password_required"),
        ({"password": "new password 1", "current_password": "nope nope"}, 403, "invalid_password"),
        ({"password": "short", "current_password": PASSWORD}, 422, "weak_password"),
    ]
    for body, code, detail in cases:
        response = await client.patch("/api/platform/me", json=body, headers=headers)
        assert (response.status_code, response.json()) == (code, {"detail": detail})

    changed = await client.patch(
        "/api/platform/me",
        json={"password": "new password 1", "current_password": PASSWORD},
        headers=headers,
    )
    assert changed.status_code == 200
    verify = "/internal/auth/verify"
    assert (await client.get(verify, headers=bearer(token))).status_code == 200
    assert (await client.get(verify, headers=bearer(other))).status_code == 401
    await login(platform, "alice", "new password 1")


async def test_my_sessions(platform):
    await create_user(platform, "alice")
    await create_user(platform, "bob")
    token = await login(platform, "alice", device_label="Laptop")
    phone = await login(platform, "alice", device_label="Phone")
    bob = await login(platform, "bob")
    headers = await identity(platform, token)
    client = platform.client

    listed = (await client.get("/api/platform/me/sessions", headers=headers)).json()["sessions"]
    assert sorted((s["device_label"], s["current"]) for s in listed) == [
        ("Laptop", True),
        ("Phone", False),
    ]
    phone_id = next(s["id"] for s in listed if s["device_label"] == "Phone")
    bob_sid = (await _claims(platform, bob))["sid"]

    response = await client.delete(f"/api/platform/me/sessions/{bob_sid}", headers=headers)
    assert (response.status_code, response.json()) == (404, {"detail": "not_found"})
    assert (await client.get("/internal/auth/verify", headers=bearer(bob))).status_code == 200

    response = await client.delete(f"/api/platform/me/sessions/{phone_id}", headers=headers)
    assert response.status_code == 204
    assert (await client.get("/internal/auth/verify", headers=bearer(phone))).status_code == 401
    again = await client.delete(f"/api/platform/me/sessions/{phone_id}", headers=headers)
    assert again.status_code == 404


# --- admin users ------------------------------------------------------------------


async def test_admin_lists_and_patches_users(platform):
    headers = await stepped_up_admin(platform)
    alice = await create_user(platform, "alice")
    client = platform.client

    listed = (await client.get("/api/platform/admin/users", headers=headers)).json()["users"]
    assert [u["username"] for u in listed] == ["root", "alice"]
    assert set(listed[0]) == {
        "id",
        "username",
        "display_name",
        "role",
        "totp_enabled",
        "require_passkeys",
        "disabled_at",
        "created_at",
    }

    promoted = await client.patch(
        f"/api/platform/admin/users/{alice['id']}", json={"role": "admin"}, headers=headers
    )
    assert promoted.json()["role"] == "admin"

    for user_id, body, code, detail in [
        (uuid4(), {"role": "member"}, 404, "not_found"),
        (alice["id"], {"role": "owner"}, 422, "invalid_request"),
    ]:
        response = await client.patch(
            f"/api/platform/admin/users/{user_id}", json=body, headers=headers
        )
        assert (response.status_code, response.json()["detail"]) == (code, detail)


async def test_last_admin_protection(platform):
    token = await bootstrap_admin(platform)
    await step_up(platform, token)
    headers = await identity(platform, token)
    root_id = (await _claims(platform, token))["sub"]
    client = platform.client

    for body in ({"role": "member"}, {"disabled": True}):
        response = await client.patch(
            f"/api/platform/admin/users/{root_id}", json=body, headers=headers
        )
        assert (response.status_code, response.json()) == (409, {"detail": "last_admin"})

    # A disabled admin doesn't count as the remaining one.
    backup = await create_user(platform, "backup", role="admin")
    sql(platform, "UPDATE users SET disabled_at = now() WHERE id = %s", (backup["id"],))
    response = await client.patch(
        f"/api/platform/admin/users/{root_id}", json={"role": "member"}, headers=headers
    )
    assert response.json() == {"detail": "last_admin"}

    sql(platform, "UPDATE users SET disabled_at = NULL WHERE id = %s", (backup["id"],))
    response = await client.patch(
        f"/api/platform/admin/users/{root_id}", json={"role": "member"}, headers=headers
    )
    assert response.json()["role"] == "member"


# --- invites --------------------------------------------------------------------


async def _invite(platform, headers, **body) -> dict:
    response = await platform.client.post(
        "/api/platform/admin/invites",
        json=body,
        headers={**headers, "Host": "homeai.local", "X-Forwarded-Proto": "https"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _accept_body(token: str, username: str) -> dict:
    return {"token": token, "username": username, "display_name": "Guest", "password": PASSWORD}


async def test_invite_lifecycle(platform):
    headers = await stepped_up_admin(platform)
    client = platform.client
    invite = await _invite(platform, headers, label="for Alice")
    assert invite["status"] == "pending"
    assert invite["label"] == "for Alice"
    assert invite["token"].startswith("hi_")
    url = urlparse(invite["accept_url"])
    assert (url.scheme, url.netloc, url.path) == ("https", "homeai.local", "/invite")
    assert parse_qs(url.query) == {"token": [invite["token"]]}
    expires = datetime.fromisoformat(invite["expires_at"])
    assert timedelta(days=6, hours=23) < expires - datetime.now(UTC) <= timedelta(days=7)

    listed = (await client.get("/api/platform/admin/invites", headers=headers)).json()["invites"]
    assert [i["id"] for i in listed] == [invite["id"]]
    assert "token" not in listed[0]

    accepted = await client.post(
        "/api/auth/invite/accept", json=_accept_body(invite["token"], "alice"), headers=NATIVE
    )
    assert accepted.status_code == 200
    assert accepted.json()["user"]["role"] == "member"
    new_token = accepted.json()["session_token"]
    assert (await client.get("/internal/auth/verify", headers=bearer(new_token))).status_code == 200

    reuse = await client.post(
        "/api/auth/invite/accept", json=_accept_body(invite["token"], "alice2")
    )
    assert (reuse.status_code, reuse.json()) == (401, {"detail": "invalid_invite"})

    (listed,) = (await client.get("/api/platform/admin/invites", headers=headers)).json()["invites"]
    assert listed["status"] == "used"
    assert listed["used_by"] == accepted.json()["user"]["id"]


async def test_invite_expiry_and_revocation(platform):
    headers = await stepped_up_admin(platform)
    client = platform.client
    expired, revoked = await _invite(platform, headers), await _invite(platform, headers)
    sql(
        platform,
        "UPDATE invites SET expires_at = now() - interval '1 second' WHERE id = %s",
        (expired["id"],),
    )
    response = await client.delete(f"/api/platform/admin/invites/{revoked['id']}", headers=headers)
    assert response.status_code == 204

    for invite in (expired, revoked):
        response = await client.post(
            "/api/auth/invite/accept", json=_accept_body(invite["token"], "late")
        )
        assert (response.status_code, response.json()) == (401, {"detail": "invalid_invite"})

    statuses = {
        i["id"]: i["status"]
        for i in (await client.get("/api/platform/admin/invites", headers=headers)).json()[
            "invites"
        ]
    }
    assert statuses == {expired["id"]: "expired", revoked["id"]: "revoked"}
    missing = await client.delete(f"/api/platform/admin/invites/{uuid4()}", headers=headers)
    assert missing.status_code == 404


async def test_invite_survives_taken_username(platform):
    headers = await stepped_up_admin(platform)
    invite = await _invite(platform, headers)
    taken = await platform.client.post(
        "/api/auth/invite/accept", json=_accept_body(invite["token"], "root")
    )
    assert (taken.status_code, taken.json()) == (409, {"detail": "username_taken"})
    ok = await platform.client.post(
        "/api/auth/invite/accept", json=_accept_body(invite["token"], "alice")
    )
    assert ok.status_code == 200
    assert "homeai_session=hs_" in ok.headers["set-cookie"]
