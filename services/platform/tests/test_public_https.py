"""M15-05 public HTTPS: flag, passkey-only login, docker-bridge origin, rate limits."""

from collections.abc import AsyncIterator

import pytest

from app.core.origin import classify
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
    setup_code,
    stepped_up_admin,
)
from tests.webauthn_device import SoftWebAuthnDevice

PUBLIC = {"X-Forwarded-For": "8.8.8.8"}
LAN = {"X-Forwarded-For": "192.168.1.10"}
VPN = {"X-Forwarded-For": "10.13.13.5"}
DOCKER = {"X-Forwarded-For": "172.17.0.1"}
ORIGIN = "http://localhost"


@pytest.fixture
async def pk(pg_database, tmp_path) -> AsyncIterator[Platform]:
    app = create_app(make_settings(pg_database, tmp_path, webauthn_rp_id="localhost"))
    async with running(app, base_url=ORIGIN) as client:
        yield Platform(app, client, pg_database, tmp_path)


async def _set_public_https(platform, enabled: bool) -> None:
    from app.core import platform_settings

    async with platform.app.state.db_pool.connection() as conn:
        await platform_settings.set_public_https(conn, enabled)
    platform.app.state.public_https = enabled


# --- classifier (no app) ------------------------------------------------------


def test_docker_bridge_is_lan_while_flag_off():
    assert classify("172.17.0.1") == "lan"
    assert classify("172.16.0.1") == "lan"
    assert classify("172.31.255.255") == "lan"


def test_docker_bridge_is_public_while_flag_on():
    assert classify("172.17.0.1", public_https=True) == "public"
    assert classify("192.168.1.10", public_https=True) == "lan"
    assert classify("10.0.0.8", public_https=True) == "lan"
    assert classify("10.13.13.5", public_https=True) == "vpn"
    assert classify("8.8.8.8", public_https=True) == "public"


def test_via_vpn_only_when_flag_on():
    assert classify("172.17.0.1", public_https=True, via="vpn") == "vpn"
    assert classify("172.17.0.1", public_https=True, via="VPN") == "vpn"
    # Flag off: RFC1918-as-LAN; a spoofed Via does not elevate a WAN IP.
    assert classify("8.8.8.8", public_https=False, via="vpn") == "public"
    assert classify("172.17.0.1", public_https=False, via="vpn") == "lan"


# --- flag off: password from public still allowed (M15-04 stays valid) --------


async def test_flag_off_public_password_is_not_passkey_required(pk):
    await create_user(pk, "alice")
    ok = await pk.client.post(
        "/api/auth/login", json={"username": "alice", "password": PASSWORD}, headers=PUBLIC
    )
    assert ok.status_code == 200, ok.text
    wrong = await pk.client.post(
        "/api/auth/login", json={"username": "alice", "password": "nope nope nope"}, headers=PUBLIC
    )
    assert (wrong.status_code, wrong.json()) == (401, {"detail": "invalid_credentials"})


async def test_flag_off_enrollment_from_public_still_public_origin(pk):
    await create_user(pk, "alice")
    headers = await identity(pk, await login(pk, "alice"))
    refused = await pk.client.post(
        "/api/platform/me/passkeys/register/begin", headers={**headers, **PUBLIC}
    )
    assert (refused.status_code, refused.json()) == (403, {"detail": "public_origin"})


async def test_status_public_https_false_by_default(platform):
    status = (await platform.client.get("/api/auth/status", headers=PUBLIC)).json()
    assert status["public_https"] is False
    assert status["origin"] == "public"


# --- flag on: passkey-only from public ----------------------------------------


async def test_flag_on_public_password_is_passkey_required_even_if_wrong(pk):
    await create_user(pk, "alice")
    await _set_public_https(pk, True)

    right = await pk.client.post(
        "/api/auth/login", json={"username": "alice", "password": PASSWORD}, headers=PUBLIC
    )
    assert (right.status_code, right.json()) == (403, {"detail": "passkey_required"})
    wrong = await pk.client.post(
        "/api/auth/login", json={"username": "alice", "password": "nope nope nope"}, headers=PUBLIC
    )
    assert (wrong.status_code, wrong.json()) == (403, {"detail": "passkey_required"})


async def test_flag_on_native_password_from_public_still_works(pk):
    await create_user(pk, "alice")
    await _set_public_https(pk, True)
    native = await pk.client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers={**NATIVE, **PUBLIC},
    )
    assert native.status_code == 200, native.text


async def test_flag_on_lan_and_vpn_password_still_work(pk):
    await create_user(pk, "alice")
    await _set_public_https(pk, True)
    for extra in (LAN, VPN):
        response = await pk.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": PASSWORD},
            headers=extra,
        )
        assert response.status_code == 200, extra


async def test_flag_on_docker_bridge_last_hop_is_passkey_required(pk):
    await create_user(pk, "alice")
    await _set_public_https(pk, True)
    response = await pk.client.post(
        "/api/auth/login", json={"username": "alice", "password": PASSWORD}, headers=DOCKER
    )
    assert (response.status_code, response.json()) == (403, {"detail": "passkey_required"})


async def test_flag_off_docker_bridge_last_hop_is_lan(pk):
    await create_user(pk, "alice")
    response = await pk.client.post(
        "/api/auth/login", json={"username": "alice", "password": PASSWORD}, headers=DOCKER
    )
    assert response.status_code == 200, response.text


async def test_flag_on_via_header_keeps_docker_bridge_vpn(pk):
    await create_user(pk, "alice")
    await _set_public_https(pk, True)
    response = await pk.client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers={**DOCKER, "X-HomeAI-Via": "vpn"},
    )
    assert response.status_code == 200, response.text


async def test_flag_on_passkey_login_from_public_works(pk):
    await create_user(pk, "alice")
    device = SoftWebAuthnDevice()
    headers = await identity(pk, await login(pk, "alice"))
    begin = await pk.client.post("/api/platform/me/passkeys/register/begin", headers=headers)
    assert begin.status_code == 200, begin.text
    cred = device.create(begin.json(), ORIGIN)
    finish = await pk.client.post(
        "/api/platform/me/passkeys/register/finish",
        json={"credential": cred, "name": "Laptop"},
        headers=headers,
    )
    assert finish.status_code == 200, finish.text

    await _set_public_https(pk, True)
    pk.client.cookies.clear()
    login_begin = await pk.client.post(
        "/api/auth/passkey/login/begin", json={"username": "alice"}, headers=PUBLIC
    )
    assert login_begin.status_code == 200, login_begin.text
    login_cred = device.get(login_begin.json(), ORIGIN)
    logged_in = await pk.client.post(
        "/api/auth/passkey/login/finish",
        json={"username": "alice", "credential": login_cred},
        headers=PUBLIC,
    )
    assert logged_in.status_code == 200, logged_in.text


async def test_flag_on_public_still_refuses_enrollment_admin_wg(pk):
    await create_user(pk, "alice")
    await _set_public_https(pk, True)
    headers = await identity(pk, await login(pk, "alice"))

    setup = await pk.client.post(
        "/api/auth/setup",
        json={
            "setup_code": setup_code(pk),
            "username": "other",
            "display_name": "Other",
            "password": PASSWORD,
        },
        headers={**NATIVE, **PUBLIC},
    )
    assert (setup.status_code, setup.json()) == (403, {"detail": "public_origin"})

    admin = await stepped_up_admin(pk)
    created = await pk.client.post("/api/platform/admin/invites", json={}, headers={**admin, **LAN})
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    accept = await pk.client.post(
        "/api/auth/invite/accept",
        json={
            "token": token,
            "username": "bob",
            "display_name": "Bob",
            "password": PASSWORD,
        },
        headers={**NATIVE, **PUBLIC},
    )
    assert (accept.status_code, accept.json()) == (403, {"detail": "public_origin"})

    users = await pk.client.get("/api/platform/admin/users", headers={**admin, **PUBLIC})
    assert (users.status_code, users.json()) == (403, {"detail": "public_origin"})

    wg = await pk.client.post(
        "/api/platform/me/wireguard-devices",
        json={"name": "phone"},
        headers={**headers, **PUBLIC},
    )
    assert (wg.status_code, wg.json()) == (403, {"detail": "public_origin"})

    register = await pk.client.post(
        "/api/platform/me/passkeys/register/begin", headers={**headers, **PUBLIC}
    )
    assert (register.status_code, register.json()) == (403, {"detail": "public_origin"})


async def test_flag_on_password_step_up_from_public_is_passkey_required(pk):
    await create_user(pk, "alice")
    token = await login(pk, "alice")
    await _set_public_https(pk, True)
    step = await pk.client.post(
        "/api/auth/step-up",
        json={"password": PASSWORD},
        headers={**bearer(token), **PUBLIC},
    )
    assert (step.status_code, step.json()) == (403, {"detail": "passkey_required"})


# --- PATCH toggle -------------------------------------------------------------


async def test_enable_without_rp_id_is_domain_required(platform):
    admin = await stepped_up_admin(platform)
    response = await platform.client.patch(
        "/api/platform/admin/settings", json={"public_https": True}, headers=admin
    )
    assert (response.status_code, response.json()) == (422, {"detail": "domain_required"})
    listed = await platform.client.get("/api/platform/settings", headers=admin)
    assert listed.status_code == 200, listed.text
    assert listed.json() == {"public_https": False, "domain_configured": False}


async def test_agent_cannot_patch_public_https(pk):
    admin_headers = await stepped_up_admin(pk)
    token = await login(pk, "root")
    agent = await delegation(pk, token)
    response = await pk.client.patch(
        "/api/platform/admin/settings",
        json={"public_https": True},
        headers={**agent, **LAN},
    )
    assert (response.status_code, response.json()) == (403, {"detail": "agent_not_allowed"})
    ok = await pk.client.patch(
        "/api/platform/admin/settings", json={"public_https": True}, headers=admin_headers
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["public_https"] is True


async def test_public_origin_cannot_patch_public_https(pk):
    admin = await stepped_up_admin(pk)
    refused = await pk.client.patch(
        "/api/platform/admin/settings",
        json={"public_https": True},
        headers={**admin, **PUBLIC},
    )
    assert (refused.status_code, refused.json()) == (403, {"detail": "public_origin"})


async def test_disable_always_allowed_even_without_rp_id(platform):
    admin = await stepped_up_admin(platform)
    response = await platform.client.patch(
        "/api/platform/admin/settings", json={"public_https": False}, headers=admin
    )
    assert response.status_code == 200, response.text
    assert response.json()["public_https"] is False


async def test_member_can_get_settings_not_patch(pk):
    await create_user(pk, "alice")
    headers = await identity(pk, await login(pk, "alice"))
    listed = await pk.client.get("/api/platform/settings", headers=headers)
    assert listed.status_code == 200, listed.text
    patched = await pk.client.patch(
        "/api/platform/admin/settings", json={"public_https": True}, headers=headers
    )
    assert (patched.status_code, patched.json()) == (403, {"detail": "admin_required"})


# --- public origin hits the stricter rate limit; LAN does not -----------------


async def test_public_origin_hits_stricter_rate_limit(platform):
    await create_user(platform, "alice")
    for _ in range(3):
        response = await platform.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong password"},
            headers=PUBLIC,
        )
        assert response.status_code == 401, response.text
    blocked = await platform.client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers=PUBLIC,
    )
    assert (blocked.status_code, blocked.json()) == (429, {"detail": "rate_limited"})
    retry = int(blocked.headers["Retry-After"])
    assert 1 <= retry <= 300


async def test_lan_keeps_five_per_minute(platform):
    await create_user(platform, "alice")
    for i in range(5):
        response = await platform.client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong password"},
            headers={"X-Forwarded-For": f"192.168.1.{i + 2}"},
        )
        assert response.status_code == 401, response.text
    blocked = await platform.client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers={"X-Forwarded-For": "192.168.1.99"},
    )
    assert (blocked.status_code, blocked.json()) == (429, {"detail": "rate_limited"})
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60
