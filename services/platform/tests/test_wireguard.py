"""WireGuard peer/config generation and `/me/wireguard-devices` (M15-01)."""

from ipaddress import IPv4Address
from uuid import uuid4

from app.core import wireguard as wg
from tests.helpers import (
    PASSWORD,
    create_user,
    delegation,
    identity,
    login,
    sql,
)


def test_generate_keypair_round_trip():
    private, public = wg.generate_keypair()
    assert wg.public_key_of(private) == public
    assert len(private) == 44  # 32 bytes, standard base64 with padding
    other_priv, other_pub = wg.generate_keypair()
    assert private != other_priv
    assert public != other_pub


def test_client_config_text():
    text = wg.client_config(
        private_key="CLIENTPRIV",
        address="10.13.13.4",
        server_public_key="SERVERPUB",
        endpoint="homeai.local:51820",
    )
    assert "[Interface]" in text
    assert "PrivateKey = CLIENTPRIV" in text
    assert "Address = 10.13.13.4/32" in text
    assert "DNS = 10.13.13.1" in text
    assert "[Peer]" in text
    assert "PublicKey = SERVERPUB" in text
    assert "AllowedIPs = 10.13.13.0/24" in text
    assert "Endpoint = homeai.local:51820" in text
    assert "PersistentKeepalive = 25" in text


def test_server_config_text_includes_peers_not_dns():
    text = wg.server_config(
        private_key="SERVERPRIV",
        peers=[("PUBA", "10.13.13.2"), ("PUBB", "10.13.13.3/32")],
    )
    assert "Address = 10.13.13.1/24" in text
    assert "ListenPort = 51820" in text
    assert "PrivateKey = SERVERPRIV" in text
    assert "PublicKey = PUBA" in text
    assert "AllowedIPs = 10.13.13.2/32" in text
    assert "PublicKey = PUBB" in text
    assert "AllowedIPs = 10.13.13.3/32" in text
    assert "DNS" not in text


def test_server_config_empty_peers():
    text = wg.server_config(private_key="SERVERPRIV", peers=[])
    assert text.count("[Peer]") == 0
    assert "ListenPort = 51820" in text


async def test_create_lists_revoke_and_config_file(platform):
    await create_user(platform, "alice")
    token = await login(platform, "alice")
    headers = await identity(platform, token)
    client = platform.client

    listed = await client.get("/api/platform/me/wireguard-devices", headers=headers)
    assert listed.status_code == 200
    assert listed.json() == {"devices": []}

    created = await client.post(
        "/api/platform/me/wireguard-devices",
        json={"name": "  Phone  "},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Phone"
    assert IPv4Address(body["address"]) in wg.SUBNET
    assert body["address"] != str(wg.SERVER_HOST)
    config = body["config"]
    assert f"Address = {body['address']}/32" in config
    assert "DNS = 10.13.13.1" in config
    assert "AllowedIPs = 10.13.13.0/24" in config
    assert "Endpoint = homeai.local:51820" in config
    assert "PrivateKey = " in config
    assert "ListenPort" not in config

    pub_file = (platform.data_dir / "wireguard" / "server.pub").read_text().strip()
    assert f"PublicKey = {pub_file}" in config
    key_path = platform.data_dir / "wireguard" / "server.key"
    assert key_path.stat().st_mode & 0o777 == 0o600
    private = key_path.read_text().strip()
    assert private not in str(body["id"])
    # The sidecar conf holds the server private key; it is 0600 and not in the API body.
    conf_path = platform.data_dir / "wireguard-config" / "wg0.conf"
    assert conf_path.stat().st_mode & 0o777 == 0o600
    conf = conf_path.read_text()
    assert "PrivateKey = " + private in conf
    assert f"AllowedIPs = {body['address']}/32" in conf
    assert "ListenPort = 51820" in conf

    listed = (await client.get("/api/platform/me/wireguard-devices", headers=headers)).json()
    assert [d["id"] for d in listed["devices"]] == [body["id"]]
    assert "config" not in listed["devices"][0]
    assert "PrivateKey" not in str(listed)

    second = await client.post(
        "/api/platform/me/wireguard-devices", json={"name": "Tablet"}, headers=headers
    )
    assert second.status_code == 201
    assert second.json()["address"] != body["address"]

    revoked = await client.delete(
        f"/api/platform/me/wireguard-devices/{body['id']}", headers=headers
    )
    assert revoked.status_code == 204
    conf_after = conf_path.read_text()
    assert body["address"] not in conf_after
    assert second.json()["address"] in conf_after
    remaining = (await client.get("/api/platform/me/wireguard-devices", headers=headers)).json()[
        "devices"
    ]
    assert [d["id"] for d in remaining] == [second.json()["id"]]

    missing = await client.delete(
        f"/api/platform/me/wireguard-devices/{body['id']}", headers=headers
    )
    assert (missing.status_code, missing.json()) == (404, {"detail": "not_found"})


async def test_blank_name_rejected(platform):
    await create_user(platform, "alice")
    headers = await identity(platform, await login(platform, "alice"))
    response = await platform.client.post(
        "/api/platform/me/wireguard-devices", json={"name": "   "}, headers=headers
    )
    assert (response.status_code, response.json()) == (422, {"detail": "invalid_name"})


async def test_outsider_cannot_revoke_someone_elses_device(platform):
    await create_user(platform, "alice")
    await create_user(platform, "bob")
    alice_h = await identity(platform, await login(platform, "alice"))
    bob_h = await identity(platform, await login(platform, "bob"))
    created = await platform.client.post(
        "/api/platform/me/wireguard-devices", json={"name": "Alice phone"}, headers=alice_h
    )
    assert created.status_code == 201
    device_id = created.json()["id"]

    listed = await platform.client.get("/api/platform/me/wireguard-devices", headers=bob_h)
    assert listed.json() == {"devices": []}
    stolen = await platform.client.delete(
        f"/api/platform/me/wireguard-devices/{device_id}", headers=bob_h
    )
    assert (stolen.status_code, stolen.json()) == (404, {"detail": "not_found"})
    still = await platform.client.get("/api/platform/me/wireguard-devices", headers=alice_h)
    assert [d["id"] for d in still.json()["devices"]] == [device_id]


async def test_agent_act_rejected_on_wireguard_routes(platform):
    await create_user(platform, "alice")
    token = await login(platform, "alice")
    agent = await delegation(platform, token)
    client = platform.client
    cases = [
        ("GET", "/api/platform/me/wireguard-devices", None),
        ("POST", "/api/platform/me/wireguard-devices", {"name": "x"}),
        ("DELETE", f"/api/platform/me/wireguard-devices/{uuid4()}", None),
    ]
    for method, path, body in cases:
        response = await client.request(method, path, json=body, headers=agent)
        assert (response.status_code, response.json()) == (403, {"detail": "agent_not_allowed"}), (
            method,
            path,
        )


async def test_revoke_drops_tagged_sessions_only(platform):
    await create_user(platform, "alice")
    lan = await login(platform, "alice", device_label="laptop")
    headers = await identity(platform, lan)
    created = await platform.client.post(
        "/api/platform/me/wireguard-devices", json={"name": "phone"}, headers=headers
    )
    device_id = created.json()["id"]
    # Native login with device_id tags the session to the peer.
    tagged = await platform.client.post(
        "/api/auth/login",
        json={
            "username": "alice",
            "password": PASSWORD,
            "device_label": "phone",
            "device_id": device_id,
        },
        headers={"X-HomeAI-Client": "native"},
    )
    assert tagged.status_code == 200, tagged.text
    tagged_token = tagged.json()["session_token"]
    unknown = await platform.client.post(
        "/api/auth/login",
        json={"username": "alice", "password": PASSWORD, "device_id": str(uuid4())},
        headers={"X-HomeAI-Client": "native"},
    )
    assert (unknown.status_code, unknown.json()) == (422, {"detail": "unknown_device"})

    assert (await platform.client.delete(
        f"/api/platform/me/wireguard-devices/{device_id}", headers=headers
    )).status_code == 204

    verify = "/internal/auth/verify"
    assert (await platform.client.get(verify, headers={"Authorization": f"Bearer {lan}"})).status_code == 200
    assert (
        await platform.client.get(verify, headers={"Authorization": f"Bearer {tagged_token}"})
    ).status_code == 401
    rows = sql(platform, "SELECT count(*) AS n FROM wireguard_peers WHERE id = %s", (device_id,))
    assert rows[0]["n"] == 0
