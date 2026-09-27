"""`/api/auth/*` and `/internal/auth/verify` against a real ephemeral Postgres."""

import hashlib
import stat
from datetime import UTC, datetime, timedelta

import pytest

from app.core import totp
from app.main import create_app
from tests.conftest import make_settings, running
from tests.helpers import (
    NATIVE,
    PASSWORD,
    bearer,
    bootstrap_admin,
    create_user,
    identity,
    login,
    setup_code,
    sql,
    step_up,
    stepped_up_admin,
)

# --- bootstrap / setup code -------------------------------------------------


def _setup_body(code: str, username: str = "root") -> dict:
    return {"setup_code": code, "username": username, "display_name": "Root", "password": PASSWORD}


async def test_setup_code_flow(platform):
    client = platform.client
    code_file = platform.data_dir / "setup-code"
    assert stat.S_IMODE(code_file.stat().st_mode) == 0o600
    status = (await client.get("/api/auth/status")).json()
    assert status == {"setup_required": True, "authenticated": False}

    wrong = await client.post("/api/auth/setup", json=_setup_body("AAAA-AAAA-AAAA-AAAA"))
    assert (wrong.status_code, wrong.json()) == (401, {"detail": "invalid_setup_code"})

    # Case and separators don't matter.
    typed = setup_code(platform).replace("-", " ").lower()
    response = await client.post("/api/auth/setup", json=_setup_body(typed))
    assert response.status_code == 200, response.text
    body = response.json()
    assert "session_token" not in body
    assert body["user"]["username"] == "root"
    assert body["user"]["role"] == "admin"
    assert "homeai_session=" in response.headers["set-cookie"]
    assert not code_file.exists()

    (state,) = sql(platform, "SELECT value FROM platform_state WHERE key = 'bootstrap_admin_id'")
    assert state["value"] == body["user"]["id"]

    status = (await client.get("/api/auth/status")).json()
    assert status["setup_required"] is False
    assert status["authenticated"] is True
    assert status["user"]["username"] == "root"

    client.cookies.clear()
    reuse = await client.post("/api/auth/setup", json=_setup_body(typed, "root2"))
    assert (reuse.status_code, reuse.json()) == (409, {"detail": "setup_complete"})


async def test_setup_stays_closed_across_restart(pg_database, tmp_path):
    async with running(create_app(make_settings(pg_database, tmp_path))) as client:
        code = (tmp_path / "setup-code").read_text().strip()
        assert (await client.post("/api/auth/setup", json=_setup_body(code))).status_code == 200
    async with running(create_app(make_settings(pg_database, tmp_path))) as client:
        assert not (tmp_path / "setup-code").exists()
        assert (await client.get("/api/auth/status")).json()["setup_required"] is False
        response = await client.post("/api/auth/setup", json=_setup_body(code, "other"))
        assert response.json() == {"detail": "setup_complete"}


async def test_setup_code_survives_restart_until_used(pg_database, tmp_path, caplog):
    codes = []
    for _ in range(2):
        async with running(create_app(make_settings(pg_database, tmp_path))):
            codes.append((tmp_path / "setup-code").read_text().strip())
    assert codes[0] == codes[1]
    assert codes[0] in caplog.text


async def test_cli_style_users_do_not_complete_bootstrap(platform):
    await create_user(platform, "root", role="admin")
    assert (await platform.client.get("/api/auth/status")).json()["setup_required"] is True

    taken = await platform.client.post("/api/auth/setup", json=_setup_body(setup_code(platform)))
    assert (taken.status_code, taken.json()) == (409, {"detail": "username_taken"})
    assert (platform.data_dir / "setup-code").exists()
    ok = await platform.client.post(
        "/api/auth/setup", json=_setup_body(setup_code(platform), "owner")
    )
    assert ok.status_code == 200


@pytest.mark.parametrize(
    ("override", "detail"),
    [
        ({"username": "Bad Name!"}, "invalid_username"),
        ({"username": "-leading"}, "invalid_username"),
        ({"password": "short"}, "weak_password"),
        ({"display_name": "   "}, "invalid_display_name"),
    ],
)
async def test_setup_validation(platform, override, detail):
    body = {**_setup_body(setup_code(platform)), **override}
    response = await platform.client.post("/api/auth/setup", json=body)
    assert (response.status_code, response.json()) == (422, {"detail": detail})
    assert (await platform.client.get("/api/auth/status")).json()["setup_required"] is True


async def test_malformed_body_does_not_echo_input(platform):
    response = await platform.client.post(
        "/api/auth/login", json={"username": "x", "password": ["hunter2-secret"]}
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid_request"
    assert "hunter2-secret" not in response.text


# --- login / logout / cookies -----------------------------------------------


async def test_login_native_and_web(platform):
    await create_user(platform, "alice")
    client = platform.client

    native = await client.post(
        "/api/auth/login",
        json={"username": "Alice", "password": PASSWORD, "device_label": "Pixel 9"},
        headers=NATIVE,
    )
    assert native.status_code == 200
    assert native.json()["session_token"].startswith("hs_")
    assert "set-cookie" not in native.headers

    web = await client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    assert "session_token" not in web.json()
    cookie = web.headers["set-cookie"].lower()
    for part in ("homeai_session=hs_", "httponly", "samesite=lax", "path=/", "max-age=2592000"):
        assert part in cookie
    assert "secure" not in cookie

    https = await client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert "; secure" in https.headers["set-cookie"].lower()


async def test_login_failures(platform):
    await create_user(platform, "alice")
    for body in (
        {"username": "alice", "password": "wrong password"},
        {"username": "nobody", "password": PASSWORD},
    ):
        response = await platform.client.post("/api/auth/login", json=body)
        assert (response.status_code, response.json()) == (401, {"detail": "invalid_credentials"})


async def test_logout_revokes_and_clears_cookie(platform):
    await create_user(platform, "alice")
    token = await login(platform, "alice")
    assert (
        await platform.client.get("/internal/auth/verify", headers=bearer(token))
    ).status_code == 200

    response = await platform.client.post("/api/auth/logout", headers=bearer(token))
    assert response.status_code == 204
    assert 'homeai_session=""' in response.headers["set-cookie"]
    assert (
        await platform.client.get("/internal/auth/verify", headers=bearer(token))
    ).status_code == 401
    # Idempotent.
    assert (
        await platform.client.post("/api/auth/logout", headers=bearer(token))
    ).status_code == 204
    assert (await platform.client.post("/api/auth/logout")).status_code == 204


async def test_status_refreshes_web_cookie(platform):
    await create_user(platform, "alice")
    await platform.client.post("/api/auth/login", json={"username": "alice", "password": PASSWORD})
    response = await platform.client.get("/api/auth/status")
    assert response.json()["authenticated"] is True
    assert "homeai_session=hs_" in response.headers["set-cookie"]


# --- TOTP ---------------------------------------------------------------------


async def test_totp_enroll_confirm_login_disable(platform):
    await create_user(platform, "alice")
    token = await login(platform, "alice")
    headers = await identity(platform, token)
    client = platform.client

    bad = await client.post(
        "/api/platform/me/totp/enroll", json={"password": "nope nope"}, headers=headers
    )
    assert (bad.status_code, bad.json()) == (403, {"detail": "invalid_password"})
    enroll = await client.post(
        "/api/platform/me/totp/enroll", json={"password": PASSWORD}, headers=headers
    )
    assert enroll.status_code == 200
    secret = enroll.json()["secret"]
    assert enroll.json()["otpauth_uri"].startswith("otpauth://totp/HomeAI:alice?secret=")

    # Pending enrollment doesn't affect login yet.
    await login(platform, "alice")

    wrong = await client.post(
        "/api/platform/me/totp/confirm", json={"code": _wrong_code(secret)}, headers=headers
    )
    assert (wrong.status_code, wrong.json()) == (403, {"detail": "invalid_totp"})
    confirm = await client.post(
        "/api/platform/me/totp/confirm",
        json={"code": totp.code_for(secret, now=_now() - 30)},
        headers=headers,
    )
    assert confirm.status_code == 200
    assert confirm.json()["totp_enabled"] is True

    async def attempt(**extra):
        return await client.post(
            "/api/auth/login", json={"username": "alice", "password": PASSWORD, **extra}
        )

    response = await attempt()
    assert (response.status_code, response.json()) == (401, {"detail": "totp_required"})
    response = await attempt(totp_code=_wrong_code(secret))
    assert response.json() == {"detail": "invalid_totp"}
    code = totp.code_for(secret)
    assert (await attempt(totp_code=code)).status_code == 200
    replay = await attempt(totp_code=code)
    assert (replay.status_code, replay.json()) == (401, {"detail": "invalid_totp"})

    platform.app.state.limiter._hits.clear()
    disable = await client.post(
        "/api/platform/me/totp/disable", json={"password": PASSWORD}, headers=headers
    )
    assert disable.json()["totp_enabled"] is False
    assert (await attempt()).status_code == 200
    again = await client.post(
        "/api/platform/me/totp/disable", json={"password": PASSWORD}, headers=headers
    )
    assert (again.status_code, again.json()) == (409, {"detail": "totp_not_enabled"})


def _now() -> float:
    return datetime.now(UTC).timestamp()


def _wrong_code(secret: str) -> str:
    valid = {totp.code_for(secret, now=_now() + k * 30) for k in (-1, 0, 1)}
    return next(c for c in ("000000", "111111", "222222") if c not in valid)


# --- /internal/auth/verify ----------------------------------------------------


async def test_verify_issues_identity_token(platform):
    user = await create_user(platform, "alice")
    token = await login(platform, "alice")
    client = platform.client

    for headers in (bearer(token), {"Cookie": f"homeai_session={token}"}):
        response = await client.get("/internal/auth/verify", headers=headers)
        assert response.status_code == 200
        claims = platform.app.state.tokens.verify_token(
            response.headers["X-HomeAI-Identity"], act="user"
        )
        assert claims["sub"] == str(user["id"])
        assert claims["role"] == "member"
        assert claims["act"] == "user"
        assert claims["exp"] - claims["iat"] == 300
        (session,) = sql(platform, "SELECT id FROM sessions")
        assert claims["sid"] == str(session["id"])

    for headers in ({}, bearer("hs_not-a-real-token"), bearer("eyJ.not.session")):
        response = await client.get("/internal/auth/verify", headers=headers)
        assert (response.status_code, response.json()) == (401, {"detail": "unauthenticated"})


async def test_verify_rejects_revoked_and_expired(platform):
    await create_user(platform, "alice")
    revoked, expired = await login(platform, "alice"), await login(platform, "alice")
    sql(platform, "UPDATE sessions SET revoked_at = now() WHERE token_hash = %s", (_h(revoked),))
    sql(
        platform,
        "UPDATE sessions SET expires_at = now() - interval '1 second' WHERE token_hash = %s",
        (_h(expired),),
    )
    for token in (revoked, expired):
        response = await platform.client.get("/internal/auth/verify", headers=bearer(token))
        assert response.status_code == 401


async def test_verify_slides_expiry(platform):
    await create_user(platform, "alice")
    token = await login(platform, "alice")
    sql(
        platform,
        "UPDATE sessions SET last_seen_at = now() - interval '10 days', "
        "expires_at = now() + interval '1 hour'",
    )
    assert (
        await platform.client.get("/internal/auth/verify", headers=bearer(token))
    ).status_code == 200
    (row,) = sql(platform, "SELECT expires_at, last_seen_at FROM sessions")
    assert row["expires_at"] - datetime.now(UTC) > timedelta(days=29)
    assert datetime.now(UTC) - row["last_seen_at"] < timedelta(minutes=1)


def _h(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


async def test_disabled_user(platform):
    admin = await bootstrap_admin(platform)
    await step_up(platform, admin)
    admin_headers = await identity(platform, admin)
    alice = await create_user(platform, "alice")
    token = await login(platform, "alice")

    response = await platform.client.patch(
        f"/api/platform/admin/users/{alice['id']}", json={"disabled": True}, headers=admin_headers
    )
    assert response.json()["disabled_at"] is not None
    assert (
        await platform.client.get("/internal/auth/verify", headers=bearer(token))
    ).status_code == 401

    wrong = await platform.client.post(
        "/api/auth/login", json={"username": "alice", "password": "wrong password"}
    )
    assert wrong.json() == {"detail": "invalid_credentials"}
    right = await platform.client.post(
        "/api/auth/login", json={"username": "alice", "password": PASSWORD}
    )
    assert (right.status_code, right.json()) == (403, {"detail": "account_disabled"})

    # Re-enabling doesn't resurrect the old session.
    await platform.client.patch(
        f"/api/platform/admin/users/{alice['id']}", json={"disabled": False}, headers=admin_headers
    )
    assert (
        await platform.client.get("/internal/auth/verify", headers=bearer(token))
    ).status_code == 401
    await login(platform, "alice")


# --- step-up --------------------------------------------------------------------


async def test_step_up_window(platform):
    token = await bootstrap_admin(platform)
    headers = await identity(platform, token)
    client = platform.client

    response = await client.get("/api/platform/admin/users", headers=headers)
    assert (response.status_code, response.json()) == (403, {"detail": "step_up_required"})

    wrong = await client.post(
        "/api/auth/step-up", json={"password": "wrong password"}, headers=bearer(token)
    )
    assert (wrong.status_code, wrong.json()) == (403, {"detail": "invalid_password"})
    no_session = await client.post("/api/auth/step-up", json={"password": PASSWORD})
    assert (no_session.status_code, no_session.json()) == (401, {"detail": "unauthenticated"})

    ok = await client.post("/api/auth/step-up", json={"password": PASSWORD}, headers=bearer(token))
    until = datetime.fromisoformat(ok.json()["stepped_up_until"])
    assert timedelta(minutes=4) < until - datetime.now(UTC) <= timedelta(minutes=5)
    assert (await client.get("/api/platform/admin/users", headers=headers)).status_code == 200

    sql(platform, "UPDATE sessions SET stepped_up_until = now() - interval '1 second'")
    response = await client.get("/api/platform/admin/users", headers=headers)
    assert response.json() == {"detail": "step_up_required"}


async def test_step_up_is_per_session(platform):
    first = await bootstrap_admin(platform)
    second = await login(platform, "root")
    await step_up(platform, first)
    response = await platform.client.get(
        "/api/platform/admin/users", headers=await identity(platform, second)
    )
    assert response.json() == {"detail": "step_up_required"}


async def test_member_cannot_use_admin_routes(platform):
    await create_user(platform, "alice")
    token = await login(platform, "alice")
    await step_up(platform, token)
    response = await platform.client.get(
        "/api/platform/admin/users", headers=await identity(platform, token)
    )
    assert (response.status_code, response.json()) == (403, {"detail": "admin_required"})


# --- rate limiting --------------------------------------------------------------


async def test_login_rate_limited_per_username(platform):
    await create_user(platform, "alice")
    client = platform.client
    for i in range(5):
        response = await client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong password"},
            headers={"X-Forwarded-For": f"10.0.0.{i}"},
        )
        assert response.status_code == 401
    blocked = await client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers={"X-Forwarded-For": "10.0.0.99"},
    )
    assert (blocked.status_code, blocked.json()) == (429, {"detail": "rate_limited"})
    assert 1 <= int(blocked.headers["Retry-After"]) <= 60


async def test_login_rate_limited_per_ip(platform):
    client = platform.client
    for i in range(5):
        response = await client.post(
            "/api/auth/login", json={"username": f"user{i}", "password": "wrong password"}
        )
        assert response.status_code == 401
    blocked = await client.post(
        "/api/auth/login", json={"username": "someone-else", "password": "x"}
    )
    assert blocked.status_code == 429
    # Only the last X-Forwarded-For hop (Caddy's) counts; a spoofed prefix doesn't help.
    spoofed = await client.post(
        "/api/auth/login",
        json={"username": "fresh", "password": "x"},
        headers={"X-Forwarded-For": "1.2.3.4, 127.0.0.1"},
    )
    assert spoofed.status_code == 429


async def test_successful_logins_do_not_count(platform):
    await create_user(platform, "alice")
    for _ in range(8):
        await login(platform, "alice")


async def test_setup_and_step_up_rate_limited(platform):
    client = platform.client
    for _ in range(5):
        response = await client.post("/api/auth/setup", json=_setup_body("AAAA-AAAA-AAAA-AAAA"))
        assert response.status_code == 401
    response = await client.post("/api/auth/setup", json=_setup_body(setup_code(platform)))
    assert response.status_code == 429

    platform.app.state.limiter._hits.clear()
    token = await bootstrap_admin(platform)
    for _ in range(5):
        response = await client.post(
            "/api/auth/step-up", json={"password": "wrong password"}, headers=bearer(token)
        )
        assert response.status_code == 403
    response = await client.post(
        "/api/auth/step-up", json={"password": PASSWORD}, headers=bearer(token)
    )
    assert response.status_code == 429


# --- storage ----------------------------------------------------------------------


async def test_tokens_and_passwords_never_stored_in_plaintext(platform):
    admin_headers = await stepped_up_admin(platform)
    session_token = await login(platform, "root")
    invite = await platform.client.post(
        "/api/platform/admin/invites", json={}, headers=admin_headers
    )
    invite_token = invite.json()["token"]

    dump = " ".join(
        str(row)
        for table in ("users", "sessions", "invites", "platform_state")
        for row in sql(platform, f"SELECT * FROM {table}")
    )
    for secret in (session_token, invite_token, PASSWORD):
        assert secret not in dump
        assert secret.encode().hex() not in dump
    assert sql(platform, "SELECT 1 FROM sessions WHERE token_hash = %s", (_h(session_token),))
    assert sql(platform, "SELECT 1 FROM invites WHERE token_hash = %s", (_h(invite_token),))
    (user,) = sql(platform, "SELECT password_hash FROM users")
    assert user["password_hash"].startswith("$argon2id$")
