"""M15-02 origin classifier and route enforcement (`403 public_origin`)."""

from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.origin import classify, parse_cidrs, privileged
from app.main import create_app
from tests.conftest import make_settings, running
from tests.helpers import (
    NATIVE,
    PASSWORD,
    bearer,
    bootstrap_admin,
    create_user,
    delegation,
    identity,
    login,
    setup_code,
    step_up,
    stepped_up_admin,
)

PUBLIC = {"X-Forwarded-For": "8.8.8.8"}
LAN = {"X-Forwarded-For": "192.168.1.10"}
VPN = {"X-Forwarded-For": "10.13.13.5"}

_SETUP = {
    "username": "root",
    "display_name": "Root",
    "password": PASSWORD,
}


def _setup_body(platform, **extra) -> dict:
    return {**_SETUP, "setup_code": setup_code(platform), **extra}


# --- classifier ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("ip", "origin"),
    [
        ("10.13.13.2", "vpn"),
        ("192.168.1.50", "lan"),
        ("10.0.0.8", "lan"),
        ("172.16.1.1", "lan"),
        ("127.0.0.1", "lan"),
        ("8.8.8.8", "public"),
        ("1.2.3.4", "public"),
        ("::1", "lan"),
        ("fc00::1", "lan"),
        ("fe80::1", "lan"),
        ("2001:4860:4860::8888", "public"),
        ("::ffff:8.8.8.8", "public"),
        ("unknown", "public"),
        ("", "public"),
        ("not-an-ip", "public"),
    ],
)
def test_classify_defaults(ip, origin):
    assert classify(ip) == origin
    assert privileged(origin) is (origin != "public")


def test_vpn_beats_overlapping_rfc1918():
    """10.13.13.0/24 is inside 10.0.0.0/8; VPN is matched first."""
    assert classify("10.13.13.1") == "vpn"
    assert classify("10.13.13.254") == "vpn"
    assert classify("10.13.14.1") == "lan"


def test_custom_subnets():
    vpn = parse_cidrs("198.51.100.0/24")
    lan = parse_cidrs("203.0.113.0/24")
    assert classify("198.51.100.9", vpn=vpn, lan=lan) == "vpn"
    assert classify("203.0.113.9", vpn=vpn, lan=lan) == "lan"
    assert classify("10.13.13.2", vpn=vpn, lan=lan) == "public"


def test_settings_reject_bad_cidr():
    with pytest.raises(ValidationError):
        Settings(origin_vpn_subnets="not-a-cidr", _env_file=None)


# --- last-hop XFF (spoofed prefixes ignored) ----------------------------------


async def test_xff_last_hop_is_lan_not_spoofed_public(platform):
    """`8.8.8.8, 192.168.1.10` is LAN (Caddy's last hop)."""
    headers = {"X-Forwarded-For": "8.8.8.8, 192.168.1.10"}
    response = await platform.client.post(
        "/api/auth/setup", json=_setup_body(platform), headers={**NATIVE, **headers}
    )
    assert response.status_code == 200, response.text


async def test_xff_last_hop_public_is_public_origin(platform):
    """`192.168.1.10, 8.8.8.8` is public (last hop is WAN)."""
    headers = {"X-Forwarded-For": "192.168.1.10, 8.8.8.8"}
    response = await platform.client.post(
        "/api/auth/setup", json=_setup_body(platform), headers={**NATIVE, **headers}
    )
    assert (response.status_code, response.json()) == (403, {"detail": "public_origin"})
    # Setup was not consumed.
    ok = await platform.client.post(
        "/api/auth/setup", json=_setup_body(platform), headers={**NATIVE, **LAN}
    )
    assert ok.status_code == 200, ok.text


# --- route matrix: public refused ---------------------------------------------


async def test_setup_from_public_refused_before_business_errors(platform):
    wrong = await platform.client.post(
        "/api/auth/setup",
        json={**_SETUP, "setup_code": "AAAA-AAAA-AAAA-AAAA"},
        headers=PUBLIC,
    )
    assert (wrong.status_code, wrong.json()) == (403, {"detail": "public_origin"})
    valid = await platform.client.post(
        "/api/auth/setup", json=_setup_body(platform), headers={**NATIVE, **PUBLIC}
    )
    assert (valid.status_code, valid.json()) == (403, {"detail": "public_origin"})
    lan = await platform.client.post(
        "/api/auth/setup", json=_setup_body(platform), headers={**NATIVE, **LAN}
    )
    assert lan.status_code == 200, lan.text


async def test_invite_accept_from_public_does_not_consume_invite(platform):
    admin = await stepped_up_admin(platform)
    created = await platform.client.post(
        "/api/platform/admin/invites", json={}, headers={**admin, **LAN}
    )
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    body = {
        "token": token,
        "username": "alice",
        "display_name": "Alice",
        "password": PASSWORD,
    }
    refused = await platform.client.post(
        "/api/auth/invite/accept", json=body, headers={**NATIVE, **PUBLIC}
    )
    assert (refused.status_code, refused.json()) == (403, {"detail": "public_origin"})
    accepted = await platform.client.post(
        "/api/auth/invite/accept", json=body, headers={**NATIVE, **VPN}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["user"]["username"] == "alice"


async def test_admin_users_from_public_is_public_origin(platform):
    token = await bootstrap_admin(platform)
    await step_up(platform, token)
    headers = await identity(platform, token)
    refused = await platform.client.get("/api/platform/admin/users", headers={**headers, **PUBLIC})
    assert (refused.status_code, refused.json()) == (403, {"detail": "public_origin"})
    ok = await platform.client.get("/api/platform/admin/users", headers={**headers, **LAN})
    assert ok.status_code == 200, ok.text
    vpn = await platform.client.get("/api/platform/admin/users", headers={**headers, **VPN})
    assert vpn.status_code == 200, vpn.text


async def test_admin_unauthenticated_is_401_even_from_public(platform):
    response = await platform.client.get("/api/platform/admin/users", headers=PUBLIC)
    assert (response.status_code, response.json()) == (401, {"detail": "unauthenticated"})


async def test_wireguard_post_from_public_refused_list_and_delete_allowed(platform):
    await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    listed = await platform.client.get(
        "/api/platform/me/wireguard-devices", headers={**headers, **PUBLIC}
    )
    assert listed.status_code == 200, listed.text
    refused = await platform.client.post(
        "/api/platform/me/wireguard-devices",
        json={"name": "phone"},
        headers={**headers, **PUBLIC},
    )
    assert (refused.status_code, refused.json()) == (403, {"detail": "public_origin"})
    created = await platform.client.post(
        "/api/platform/me/wireguard-devices",
        json={"name": "phone"},
        headers={**headers, **LAN},
    )
    assert created.status_code == 201, created.text
    device_id = created.json()["id"]
    revoked = await platform.client.delete(
        f"/api/platform/me/wireguard-devices/{device_id}",
        headers={**headers, **PUBLIC},
    )
    assert revoked.status_code == 204


async def test_device_pair_begin_from_public_refused_list_and_delete_allowed(platform):
    await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    listed = await platform.client.get(
        "/api/platform/me/device-pairs", headers={**headers, **PUBLIC}
    )
    assert listed.status_code == 200, listed.text
    refused = await platform.client.post(
        "/api/platform/me/device-pairs/begin", headers={**headers, **PUBLIC}
    )
    assert (refused.status_code, refused.json()) == (403, {"detail": "public_origin"})
    created = await platform.client.post(
        "/api/platform/me/device-pairs/begin", headers={**headers, **LAN}
    )
    assert created.status_code == 200, created.text
    # Enroll is unauthenticated but still LAN/VPN-only.
    enroll = await platform.client.post(
        "/api/auth/device/enroll",
        json={"token": created.json()["token"], "public_key": "x", "name": "x", "signature": "x"},
        headers=PUBLIC,
    )
    assert (enroll.status_code, enroll.json()) == (403, {"detail": "public_origin"})


async def test_wireguard_post_from_vpn_succeeds(platform):
    await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    created = await platform.client.post(
        "/api/platform/me/wireguard-devices",
        json={"name": "vpn-phone"},
        headers={**headers, **VPN},
    )
    assert created.status_code == 201, created.text


# --- not blocked: login, status; agent still agent_not_allowed ----------------


async def test_login_and_status_allowed_from_public(platform):
    await create_user(platform, "alice")
    status = await platform.client.get("/api/auth/status", headers=PUBLIC)
    assert status.status_code == 200
    logged_in = await platform.client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers={**NATIVE, **PUBLIC},
    )
    assert logged_in.status_code == 200, logged_in.text


async def test_admin_from_agent_is_still_agent_not_allowed(platform):
    token = await bootstrap_admin(platform)
    await step_up(platform, token)
    headers = await identity(platform, token)
    claims = platform.app.state.tokens.verify_token(headers["X-HomeAI-Identity"], act="user")
    agent = bearer(
        platform.app.state.tokens.issue_token(
            {k: claims[k] for k in ("sub", "sid", "role")} | {"act": "agent", "thr": "t-1"},
            timedelta(minutes=15),
        )
    )
    for extra in ({}, PUBLIC, LAN):
        response = await platform.client.get(
            "/api/platform/admin/users", headers={**agent, **extra}
        )
        assert (response.status_code, response.json()) == (
            403,
            {"detail": "agent_not_allowed"},
        ), extra


async def test_wireguard_post_from_agent_is_still_agent_not_allowed(platform):
    await create_user(platform, "alice")
    token = await login(platform, "alice")
    agent = await delegation(platform, token)
    response = await platform.client.post(
        "/api/platform/me/wireguard-devices",
        json={"name": "x"},
        headers={**agent, **PUBLIC},
    )
    assert (response.status_code, response.json()) == (403, {"detail": "agent_not_allowed"})


async def test_passkey_register_from_public_is_public_origin(pg_database, tmp_path):
    """Passkey enrollment is device enrollment: LAN/VPN only (M15-04)."""
    app = create_app(make_settings(pg_database, tmp_path, webauthn_rp_id="localhost"))
    async with running(app, base_url="http://localhost") as client:
        from tests.conftest import Platform

        platform = Platform(app, client, pg_database, tmp_path)
        await create_user(platform, "alice")
        headers = await identity(platform, await login(platform, "alice"))
        refused = await client.post(
            "/api/platform/me/passkeys/register/begin", headers={**headers, **PUBLIC}
        )
        assert (refused.status_code, refused.json()) == (403, {"detail": "public_origin"})
        ok = await client.post("/api/platform/me/passkeys/register/begin", headers=headers)
        assert ok.status_code == 200, ok.text
