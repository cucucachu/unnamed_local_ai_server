"""Host-app device pairing: ECDSA P-256 challenge/response (M15-06).

Software keys in pytest (`cryptography` SECP256R1). The phone uses Android
Keystore EC P-256 + SHA256withECDSA — same encoding (SPKI public key,
DER signature, unpadded base64url).
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.core import device_pairs as dp
from app.core import totp
from tests.helpers import (
    NATIVE,
    PASSWORD,
    create_user,
    delegation,
    identity,
    login,
    sql,
)

PUBLIC = {"X-Forwarded-For": "8.8.8.8"}
LAN = {"X-Forwarded-For": "192.168.1.10"}
HOST = {"X-HomeAI-Client": "host"}


def _software_key():
    private = ec.generate_private_key(ec.SECP256R1())
    public_der = private.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return private, dp.b64url_encode(public_der)


def _sign(private, challenge_b64: str) -> str:
    challenge = dp.b64url_decode(challenge_b64)
    signature = private.sign(challenge, ec.ECDSA(hashes.SHA256()))
    return dp.b64url_encode(signature)


async def _begin(platform, headers) -> dict:
    response = await platform.client.post("/api/platform/me/device-pairs/begin", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


async def _enroll(platform, payload, private, public_key, name="Pixel") -> dict:
    signature = _sign(private, payload["challenge"])
    response = await platform.client.post(
        "/api/auth/device/enroll",
        json={
            "token": payload["token"],
            "public_key": public_key,
            "name": name,
            "signature": signature,
        },
        headers=LAN,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _pair(platform, headers, name="Pixel"):
    private, public_key = _software_key()
    payload = await _begin(platform, headers)
    device = await _enroll(platform, payload, private, public_key, name=name)
    return private, device


def test_software_key_round_trip_matches_android_encoding():
    private, public_b64 = _software_key()
    challenge = b"\x01" * 32
    signature = private.sign(challenge, ec.ECDSA(hashes.SHA256()))
    assert dp.verify_signature(dp.parse_public_key(public_b64), challenge, signature)
    assert not dp.verify_signature(
        dp.parse_public_key(public_b64), challenge, signature[:-1] + bytes([signature[-1] ^ 1])
    )


async def test_enroll_login_list(platform):
    alice = await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    listed = await platform.client.get("/api/platform/me/device-pairs", headers=headers)
    assert listed.json() == {"devices": []}

    private, device = await _pair(platform, headers, name="  Pixel 8  ")
    assert device["name"] == "Pixel 8"
    assert device["id"]
    assert device["last_used_at"] is None

    listed = await platform.client.get("/api/platform/me/device-pairs", headers=headers)
    assert [d["id"] for d in listed.json()["devices"]] == [device["id"]]
    assert "public_key" not in listed.json()["devices"][0]
    assert listed.json()["devices"][0]["name"] == "Pixel 8"

    begin = await platform.client.post(
        "/api/auth/device/begin", json={"device_id": device["id"]}, headers=HOST
    )
    assert begin.status_code == 200, begin.text
    finish = await platform.client.post(
        "/api/auth/device/finish",
        json={
            "device_id": device["id"],
            "signature": _sign(private, begin.json()["challenge"]),
            "device_label": "Pixel 8",
        },
        headers=HOST,
    )
    assert finish.status_code == 200, finish.text
    assert finish.json()["session_token"].startswith("hs_")
    assert finish.json()["user"]["id"] == str(alice["id"])
    assert "set-cookie" not in finish.headers

    rows = sql(
        platform,
        "SELECT host_device_id FROM sessions WHERE token_hash = %s",
        (sha256(finish.json()["session_token"].encode()).digest(),),
    )
    assert str(rows[0]["host_device_id"]) == device["id"]


async def test_enroll_replay_rejected(platform):
    await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    private, public_key = _software_key()
    payload = await _begin(platform, headers)
    await _enroll(platform, payload, private, public_key)
    replay = await platform.client.post(
        "/api/auth/device/enroll",
        json={
            "token": payload["token"],
            "public_key": public_key,
            "name": "again",
            "signature": _sign(private, payload["challenge"]),
        },
        headers=LAN,
    )
    assert (replay.status_code, replay.json()) == (409, {"detail": "already_used"})


async def test_login_replay_rejected(platform):
    await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    private, device = await _pair(platform, headers)
    begin = await platform.client.post(
        "/api/auth/device/begin", json={"device_id": device["id"]}, headers=HOST
    )
    signature = _sign(private, begin.json()["challenge"])
    first = await platform.client.post(
        "/api/auth/device/finish",
        json={"device_id": device["id"], "signature": signature},
        headers=HOST,
    )
    assert first.status_code == 200, first.text
    replay = await platform.client.post(
        "/api/auth/device/finish",
        json={"device_id": device["id"], "signature": signature},
        headers=HOST,
    )
    assert (replay.status_code, replay.json()) == (401, {"detail": "invalid_credentials"})


async def test_revoke_and_tagged_sessions(platform):
    await create_user(platform, "alice")
    lan = await login(platform, "alice", device_label="laptop")
    headers = await identity(platform, lan)
    private, device = await _pair(platform, headers, name="phone")
    begin = await platform.client.post(
        "/api/auth/device/begin", json={"device_id": device["id"]}, headers=HOST
    )
    tagged = await platform.client.post(
        "/api/auth/device/finish",
        json={
            "device_id": device["id"],
            "signature": _sign(private, begin.json()["challenge"]),
        },
        headers=HOST,
    )
    assert tagged.status_code == 200, tagged.text
    tagged_token = tagged.json()["session_token"]

    revoked = await platform.client.delete(
        f"/api/platform/me/device-pairs/{device['id']}", headers=headers
    )
    assert revoked.status_code == 204
    remaining = (await platform.client.get("/api/platform/me/device-pairs", headers=headers)).json()
    assert remaining == {"devices": []}

    verify = "/internal/auth/verify"
    assert (
        await platform.client.get(verify, headers={"Authorization": f"Bearer {lan}"})
    ).status_code == 200
    assert (
        await platform.client.get(verify, headers={"Authorization": f"Bearer {tagged_token}"})
    ).status_code == 401

    begin_after = await platform.client.post(
        "/api/auth/device/begin", json={"device_id": device["id"]}, headers=HOST
    )
    assert (begin_after.status_code, begin_after.json()) == (401, {"detail": "invalid_credentials"})

    missing = await platform.client.delete(
        f"/api/platform/me/device-pairs/{device['id']}", headers=headers
    )
    assert (missing.status_code, missing.json()) == (404, {"detail": "not_found"})


async def test_public_origin_refuses_begin_and_enroll(platform):
    await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    listed = await platform.client.get(
        "/api/platform/me/device-pairs", headers={**headers, **PUBLIC}
    )
    assert listed.status_code == 200, listed.text

    refused_begin = await platform.client.post(
        "/api/platform/me/device-pairs/begin", headers={**headers, **PUBLIC}
    )
    assert (refused_begin.status_code, refused_begin.json()) == (403, {"detail": "public_origin"})

    payload = await _begin(platform, {**headers, **LAN})
    private, public_key = _software_key()
    refused_enroll = await platform.client.post(
        "/api/auth/device/enroll",
        json={
            "token": payload["token"],
            "public_key": public_key,
            "name": "phone",
            "signature": _sign(private, payload["challenge"]),
        },
        headers=PUBLIC,
    )
    assert (refused_enroll.status_code, refused_enroll.json()) == (403, {"detail": "public_origin"})

    ok = await _enroll(platform, payload, private, public_key)
    revoked = await platform.client.delete(
        f"/api/platform/me/device-pairs/{ok['id']}", headers={**headers, **PUBLIC}
    )
    assert revoked.status_code == 204


async def test_agent_act_rejected_on_device_pair_routes(platform):
    await create_user(platform, "alice")
    token = await login(platform, "alice")
    agent = await delegation(platform, token)
    cases = [
        ("GET", "/api/platform/me/device-pairs", None),
        ("POST", "/api/platform/me/device-pairs/begin", None),
        ("DELETE", f"/api/platform/me/device-pairs/{uuid4()}", None),
    ]
    for method, path, body in cases:
        response = await platform.client.request(method, path, json=body, headers=agent)
        assert (response.status_code, response.json()) == (403, {"detail": "agent_not_allowed"}), (
            method,
            path,
        )


async def test_totp_after_device_login(platform):
    await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    enroll = await platform.client.post(
        "/api/platform/me/totp/enroll", json={"password": PASSWORD}, headers=headers
    )
    secret = enroll.json()["secret"]
    confirm = await platform.client.post(
        "/api/platform/me/totp/confirm",
        json={"code": totp.code_for(secret, now=datetime.now(UTC).timestamp() - 30)},
        headers=headers,
    )
    assert confirm.status_code == 200, confirm.text

    private, device = await _pair(platform, headers)
    begin = await platform.client.post(
        "/api/auth/device/begin", json={"device_id": device["id"]}, headers=HOST
    )
    signature = _sign(private, begin.json()["challenge"])
    missing = await platform.client.post(
        "/api/auth/device/finish",
        json={"device_id": device["id"], "signature": signature},
        headers=HOST,
    )
    assert (missing.status_code, missing.json()) == (401, {"detail": "totp_required"})

    # Consumed the challenge; need a fresh one.
    begin = await platform.client.post(
        "/api/auth/device/begin", json={"device_id": device["id"]}, headers=HOST
    )
    signature = _sign(private, begin.json()["challenge"])
    wrong = await platform.client.post(
        "/api/auth/device/finish",
        json={"device_id": device["id"], "signature": signature, "totp_code": "000000"},
        headers=HOST,
    )
    assert (wrong.status_code, wrong.json()) == (401, {"detail": "invalid_totp"})

    begin = await platform.client.post(
        "/api/auth/device/begin", json={"device_id": device["id"]}, headers=HOST
    )
    signature = _sign(private, begin.json()["challenge"])
    ok = await platform.client.post(
        "/api/auth/device/finish",
        json={
            "device_id": device["id"],
            "signature": signature,
            "totp_code": totp.code_for(secret),
        },
        headers=HOST,
    )
    assert ok.status_code == 200, ok.text


async def test_wrong_user_key_is_invalid_credentials(platform):
    await create_user(platform, "alice")
    await create_user(platform, "bob")
    alice_h = await identity(platform, await login(platform, "alice"))
    bob_h = await identity(platform, await login(platform, "bob"))
    alice_priv, alice_dev = await _pair(platform, alice_h, name="alice-phone")
    bob_priv, bob_dev = await _pair(platform, bob_h, name="bob-phone")

    begin = await platform.client.post(
        "/api/auth/device/begin", json={"device_id": alice_dev["id"]}, headers=HOST
    )
    stolen = await platform.client.post(
        "/api/auth/device/finish",
        json={
            "device_id": alice_dev["id"],
            "signature": _sign(bob_priv, begin.json()["challenge"]),
        },
        headers=HOST,
    )
    assert (stolen.status_code, stolen.json()) == (401, {"detail": "invalid_credentials"})

    # Bob cannot list or revoke Alice's pair.
    listed = await platform.client.get("/api/platform/me/device-pairs", headers=bob_h)
    assert [d["id"] for d in listed.json()["devices"]] == [bob_dev["id"]]
    stolen_del = await platform.client.delete(
        f"/api/platform/me/device-pairs/{alice_dev['id']}", headers=bob_h
    )
    assert (stolen_del.status_code, stolen_del.json()) == (404, {"detail": "not_found"})

    # Signing Alice's enroll challenge with Bob's key but submitting Alice's
    # public key fails; submitting Bob's public key on Alice's token would
    # enroll Bob's key as Alice's device — that's the QR-holder's choice.
    # A signature that doesn't match the submitted public key is 422.
    payload = await _begin(platform, alice_h)
    mismatch = await platform.client.post(
        "/api/auth/device/enroll",
        json={
            "token": payload["token"],
            "public_key": dp.b64url_encode(
                alice_priv.public_key().public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                )
            ),
            "name": "nope",
            "signature": _sign(bob_priv, payload["challenge"]),
        },
        headers=LAN,
    )
    assert (mismatch.status_code, mismatch.json()) == (422, {"detail": "invalid_signature"})


async def test_unknown_device_and_bad_token(platform):
    await create_user(platform, "alice")
    unknown = await platform.client.post(
        "/api/auth/device/begin", json={"device_id": str(uuid4())}, headers=HOST
    )
    assert (unknown.status_code, unknown.json()) == (401, {"detail": "invalid_credentials"})
    finish = await platform.client.post(
        "/api/auth/device/finish",
        json={"device_id": str(uuid4()), "signature": dp.b64url_encode(b"nope")},
        headers=HOST,
    )
    assert (finish.status_code, finish.json()) == (401, {"detail": "invalid_credentials"})

    await login(platform, "alice")
    private, public_key = _software_key()
    bad = await platform.client.post(
        "/api/auth/device/enroll",
        json={
            "token": "hd_" + "A" * 43,
            "public_key": public_key,
            "name": "x",
            "signature": _sign(private, dp.b64url_encode(b"\x00" * 32)),
        },
        headers=LAN,
    )
    assert (bad.status_code, bad.json()) == (422, {"detail": "invalid_token"})


async def test_disabled_user_after_valid_signature(platform):
    alice = await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    private, device = await _pair(platform, headers)
    sql(platform, "UPDATE users SET disabled_at = now() WHERE id = %s", (alice["id"],))
    begin = await platform.client.post(
        "/api/auth/device/begin", json={"device_id": device["id"]}, headers=HOST
    )
    # Device still exists; signature can succeed; disabled is after.
    assert begin.status_code == 200, begin.text
    finish = await platform.client.post(
        "/api/auth/device/finish",
        json={
            "device_id": device["id"],
            "signature": _sign(private, begin.json()["challenge"]),
        },
        headers=HOST,
    )
    assert (finish.status_code, finish.json()) == (403, {"detail": "account_disabled"})


async def test_host_client_password_login_still_returns_token(platform):
    """LAN password fallback for the host app; Expo Go keeps `native`."""
    await create_user(platform, "alice")
    host = await platform.client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers=HOST,
    )
    assert host.status_code == 200, host.text
    assert host.json()["session_token"].startswith("hs_")
    native = await platform.client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD},
        headers=NATIVE,
    )
    assert native.json()["session_token"].startswith("hs_")


async def test_expired_enroll_token(platform):
    await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    private, public_key = _software_key()
    payload = await _begin(platform, headers)
    sql(
        platform,
        "UPDATE device_challenges SET expires_at = %s WHERE token_hash = %s",
        (
            datetime.now(UTC) - timedelta(minutes=1),
            sha256(payload["token"].encode()).digest(),
        ),
    )
    expired = await platform.client.post(
        "/api/auth/device/enroll",
        json={
            "token": payload["token"],
            "public_key": public_key,
            "name": "late",
            "signature": _sign(private, payload["challenge"]),
        },
        headers=LAN,
    )
    assert (expired.status_code, expired.json()) == (422, {"detail": "invalid_token"})


async def test_blank_name_rejected(platform):
    await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    private, public_key = _software_key()
    payload = await _begin(platform, headers)
    response = await platform.client.post(
        "/api/auth/device/enroll",
        json={
            "token": payload["token"],
            "public_key": public_key,
            "name": "   ",
            "signature": _sign(private, payload["challenge"]),
        },
        headers=LAN,
    )
    assert (response.status_code, response.json()) == (422, {"detail": "invalid_name"})
