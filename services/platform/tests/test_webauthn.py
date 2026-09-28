"""M15-04 passkeys: virtual authenticator against a real ephemeral Postgres."""

import time
from collections.abc import AsyncIterator

import pytest

from app.core import totp
from app.core import webauthn as webauthn_core
from app.main import create_app
from tests.conftest import Platform, make_settings, running
from tests.helpers import (
    NATIVE,
    PASSWORD,
    bearer,
    create_user,
    delegation,
    identity,
    login,
    step_up,
    stepped_up_admin,
)
from tests.webauthn_device import SoftWebAuthnDevice

ORIGIN = "http://localhost"
PUBLIC = {"X-Forwarded-For": "8.8.8.8"}


@pytest.fixture
async def pk(pg_database, tmp_path) -> AsyncIterator[Platform]:
    app = create_app(make_settings(pg_database, tmp_path, webauthn_rp_id="localhost"))
    async with running(app, base_url=ORIGIN) as client:
        yield Platform(app, client, pg_database, tmp_path)


async def _enroll(platform: Platform, username: str, device: SoftWebAuthnDevice | None = None):
    device = device or SoftWebAuthnDevice()
    headers = await identity(platform, await login(platform, username))
    begin = await platform.client.post("/api/platform/me/passkeys/register/begin", headers=headers)
    assert begin.status_code == 200, begin.text
    cred = device.create(begin.json(), ORIGIN)
    finish = await platform.client.post(
        "/api/platform/me/passkeys/register/finish",
        json={"credential": cred, "name": "Laptop"},
        headers=headers,
    )
    assert finish.status_code == 200, finish.text
    return device, headers, finish.json()


# --- status / domain_required -------------------------------------------------


async def test_status_passkeys_off_without_rp_id(platform):
    status = (await platform.client.get("/api/auth/status")).json()
    assert status["webauthn"]["origin_ok"] is False
    assert status["webauthn"].get("rp_id") in (None, "")


async def test_status_passkeys_on_when_origin_matches(pk):
    status = (await pk.client.get("/api/auth/status")).json()
    assert status["webauthn"] == {"rp_id": "localhost", "origin_ok": True}


async def test_domain_required_when_rp_id_empty(platform):
    await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    begin = await platform.client.post("/api/platform/me/passkeys/register/begin", headers=headers)
    assert (begin.status_code, begin.json()) == (409, {"detail": "domain_required"})
    listed = await platform.client.get("/api/platform/me/passkeys", headers=headers)
    assert (listed.status_code, listed.json()) == (409, {"detail": "domain_required"})
    login_begin = await platform.client.post(
        "/api/auth/passkey/login/begin", json={"username": "alice"}
    )
    assert (login_begin.status_code, login_begin.json()) == (409, {"detail": "domain_required"})
    token = await login(platform, "alice")
    step_begin = await platform.client.post(
        "/api/auth/passkey/step-up/begin", headers=bearer(token)
    )
    assert (step_begin.status_code, step_begin.json()) == (409, {"detail": "domain_required"})


async def test_homeai_local_is_not_an_rp_id(pg_database, tmp_path):
    app = create_app(make_settings(pg_database, tmp_path, homeai_domain="homeai.local"))
    async with running(app, base_url="http://homeai.local") as client:
        status = (await client.get("/api/auth/status")).json()
        assert status["webauthn"]["origin_ok"] is False


# --- register / login / step-up ----------------------------------------------


async def test_register_login_step_up_and_sign_count(pk):
    await create_user(pk, "alice")
    device, headers, created = await _enroll(pk, "alice")
    assert created["name"] == "Laptop"

    listed = await pk.client.get("/api/platform/me/passkeys", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()["passkeys"]) == 1
    assert listed.json()["passkeys"][0]["id"] == created["id"]

    pk.client.cookies.clear()
    begin = await pk.client.post("/api/auth/passkey/login/begin", json={"username": "alice"})
    assert begin.status_code == 200, begin.text
    cred = device.get(begin.json(), ORIGIN)
    finish = await pk.client.post(
        "/api/auth/passkey/login/finish", json={"username": "alice", "credential": cred}
    )
    assert finish.status_code == 200, finish.text
    assert finish.json()["user"]["username"] == "alice"
    token = (await login(pk, "alice"))  # native still works
    headers = await identity(pk, token)

    from tests.helpers import sql

    (row,) = sql(pk, "SELECT sign_count FROM webauthn_credentials")
    assert row["sign_count"] == 1

    step_begin = await pk.client.post("/api/auth/passkey/step-up/begin", headers=bearer(token))
    assert step_begin.status_code == 200, step_begin.text
    step_cred = device.get(step_begin.json(), ORIGIN)
    stepped = await pk.client.post(
        "/api/auth/passkey/step-up/finish", json={"credential": step_cred}, headers=bearer(token)
    )
    assert stepped.status_code == 200, stepped.text
    assert "stepped_up_until" in stepped.json()
    (row,) = sql(pk, "SELECT sign_count FROM webauthn_credentials")
    assert row["sign_count"] == 2


async def test_decreasing_sign_count_rejected(pk):
    await create_user(pk, "alice")
    device, _, _ = await _enroll(pk, "alice")
    begin = await pk.client.post("/api/auth/passkey/login/begin", json={"username": "alice"})
    ok = await pk.client.post(
        "/api/auth/passkey/login/finish",
        json={"username": "alice", "credential": device.get(begin.json(), ORIGIN)},
    )
    assert ok.status_code == 200, ok.text
    begin = await pk.client.post("/api/auth/passkey/login/begin", json={"username": "alice"})
    cred = device.get(begin.json(), ORIGIN, decrement_count=True)
    refused = await pk.client.post(
        "/api/auth/passkey/login/finish", json={"username": "alice", "credential": cred}
    )
    assert (refused.status_code, refused.json()) == (401, {"detail": "invalid_credentials"})


async def test_wrong_user_credential_rejected(pk):
    await create_user(pk, "alice")
    await create_user(pk, "bob")
    device, _, _ = await _enroll(pk, "alice")
    begin = await pk.client.post("/api/auth/passkey/login/begin", json={"username": "bob"})
    # Bob has no credentials → 401 before we can even get options.
    assert (begin.status_code, begin.json()) == (401, {"detail": "invalid_credentials"})
    await _enroll(pk, "bob", SoftWebAuthnDevice())
    begin = await pk.client.post("/api/auth/passkey/login/begin", json={"username": "bob"})
    assert begin.status_code == 200, begin.text
    cred = device.get(begin.json(), ORIGIN)
    finish = await pk.client.post(
        "/api/auth/passkey/login/finish", json={"username": "bob", "credential": cred}
    )
    assert (finish.status_code, finish.json()) == (401, {"detail": "invalid_credentials"})


async def test_rp_mismatch(pk):
    await create_user(pk, "alice")
    headers = await identity(pk, await login(pk, "alice"))
    response = await pk.client.post(
        "/api/platform/me/passkeys/register/begin",
        headers={**headers, "Host": "evil.example"},
    )
    assert (response.status_code, response.json()) == (422, {"detail": "passkey_rp_mismatch"})


# --- origin / agent / require_passkeys / TOTP / native -----------------------


async def test_public_origin_refuses_register_list_allowed(pk):
    await create_user(pk, "alice")
    headers = await identity(pk, await login(pk, "alice"))
    refused = await pk.client.post(
        "/api/platform/me/passkeys/register/begin", headers={**headers, **PUBLIC}
    )
    assert (refused.status_code, refused.json()) == (403, {"detail": "public_origin"})
    listed = await pk.client.get("/api/platform/me/passkeys", headers={**headers, **PUBLIC})
    assert listed.status_code == 200, listed.text


async def test_agent_cannot_register_or_list(pk):
    await create_user(pk, "alice")
    agent = await delegation(pk, await login(pk, "alice"))
    for method, path in (
        ("POST", "/api/platform/me/passkeys/register/begin"),
        ("GET", "/api/platform/me/passkeys"),
        ("DELETE", "/api/platform/me/passkeys/00000000-0000-4000-8000-000000000000"),
    ):
        response = await pk.client.request(method, path, headers=agent)
        assert (response.status_code, response.json()) == (403, {"detail": "agent_not_allowed"}), path


async def test_require_passkeys_blocks_browser_password_not_native(pk):
    headers = await stepped_up_admin(pk)
    alice = await create_user(pk, "alice")
    patched = await pk.client.patch(
        f"/api/platform/admin/users/{alice['id']}",
        json={"require_passkeys": True},
        headers=headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["require_passkeys"] is True

    browser = await pk.client.post(
        "/api/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert (browser.status_code, browser.json()) == (403, {"detail": "passkey_required"})
    # Wrong password would have been 401; we must not leak that.
    browser_wrong = await pk.client.post(
        "/api/auth/login", json={"username": "alice", "password": "nope nope nope"}
    )
    assert (browser_wrong.status_code, browser_wrong.json()) == (403, {"detail": "passkey_required"})

    native = await pk.client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers=NATIVE,
    )
    assert native.status_code == 200, native.text

    token = native.json()["session_token"]
    step = await pk.client.post(
        "/api/auth/step-up",
        json={"password": PASSWORD},
        headers={**bearer(token), **NATIVE},
    )
    # Native is exempt from require_passkeys, including step-up.
    assert step.status_code == 200, step.text


async def test_cannot_require_passkeys_without_rp_id(platform):
    headers = await stepped_up_admin(platform)
    alice = await create_user(platform, "alice")
    response = await platform.client.patch(
        f"/api/platform/admin/users/{alice['id']}",
        json={"require_passkeys": True},
        headers=headers,
    )
    assert (response.status_code, response.json()) == (422, {"detail": "domain_required"})


async def test_totp_still_required_after_passkey(pk):
    await create_user(pk, "alice")
    device, headers, _ = await _enroll(pk, "alice")
    enroll = await pk.client.post(
        "/api/platform/me/totp/enroll", json={"password": PASSWORD}, headers=headers
    )
    secret = enroll.json()["secret"]
    confirm = await pk.client.post(
        "/api/platform/me/totp/confirm",
        json={"code": totp.code_for(secret, now=time.time() - 30)},
        headers=headers,
    )
    assert confirm.json()["totp_enabled"] is True

    begin = await pk.client.post("/api/auth/passkey/login/begin", json={"username": "alice"})
    cred = device.get(begin.json(), ORIGIN)
    missing = await pk.client.post(
        "/api/auth/passkey/login/finish", json={"username": "alice", "credential": cred}
    )
    assert (missing.status_code, missing.json()) == (401, {"detail": "totp_required"})

    begin = await pk.client.post("/api/auth/passkey/login/begin", json={"username": "alice"})
    cred = device.get(begin.json(), ORIGIN)
    ok = await pk.client.post(
        "/api/auth/passkey/login/finish",
        json={"username": "alice", "credential": cred, "totp_code": totp.code_for(secret)},
    )
    assert ok.status_code == 200, ok.text


async def test_password_step_up_still_works_when_passkeys_optional(pk):
    await create_user(pk, "alice", role="admin")
    token = await login(pk, "alice")
    await step_up(pk, token)
    headers = await identity(pk, token)
    listed = await pk.client.get("/api/platform/admin/users", headers=headers)
    assert listed.status_code == 200


async def test_revoke_passkey(pk):
    await create_user(pk, "alice")
    _, headers, created = await _enroll(pk, "alice")
    deleted = await pk.client.delete(
        f"/api/platform/me/passkeys/{created['id']}", headers=headers
    )
    assert deleted.status_code == 204
    listed = await pk.client.get("/api/platform/me/passkeys", headers=headers)
    assert listed.json()["passkeys"] == []


def test_rp_id_prefers_webauthn_rp_id():
    from app.core.config import Settings

    settings = Settings(
        webauthn_rp_id="localhost",
        homeai_domain="example.duckdns.org",
        _env_file=None,
    )
    assert webauthn_core.rp_id(settings) == "localhost"
    settings = Settings(homeai_domain="example.duckdns.org", _env_file=None)
    assert webauthn_core.rp_id(settings) == "example.duckdns.org"
    settings = Settings(_env_file=None)
    assert webauthn_core.rp_id(settings) is None
