"""The gateway rules inside a real `mitmdump`, against local self-signed HTTPS servers.

What the flow-object tests can't show: the proxy credential on a real
CONNECT, certificate pinning in the real TLS handshake, and the denial
headers reaching a real client. Rules come from the test (not the platform),
and every name resolves to a public address, so the servers here on
127.0.0.1 count as public: loopback refusal is covered in
`test_gateway_policy.py`.
"""

from __future__ import annotations

import base64
import datetime
import http.client
import http.server
import ipaddress
import json
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

TOKEN = "live-gateway-secret"
APP_DIR = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("mitmdump") is None and not (Path(sys.executable).parent / "mitmdump").exists(),
    reason="mitmdump not installed",
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _self_signed(tmp: Path) -> tuple[Path, Path, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "homeai-test")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp / "server.pem", tmp / "server.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    fingerprint = cert.fingerprint(hashes.SHA256()).hex()
    return cert_path, key_path, fingerprint


class _Echo(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps(
            {
                "authorization": self.headers.get("Authorization"),
                "cookie": self.headers.get("Cookie"),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        pass


def _serve(cert: Path, key: Path) -> int:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


@pytest.fixture(scope="module")
def live(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("live")
    cert, key, fingerprint = _self_signed(tmp)
    pinned, wrong_pin, unpinned = (_serve(cert, key) for _ in range(3))
    rules = [
        {"integration": "pinned", "port": pinned, "cert_fingerprint": fingerprint},
        {"integration": "changed", "port": wrong_pin, "cert_fingerprint": "00" * 32},
        {"integration": "unpinned", "port": unpinned, "cert_fingerprint": None},
    ]
    rules = [{"scheme": "https", "host": "127.0.0.1", "private_address": False, **r} for r in rules]
    addon = tmp / "live_addon.py"
    addon.write_text(
        f"""
import sys
sys.path.insert(0, {str(APP_DIR)!r})
import policy
from policy import client_disconnected, http_connect, request, response, responseheaders, tls_start_server

_rules = [policy.Rule(**r) for r in {rules!r}]
policy.RULES = policy.RuleSource(fetch=lambda: list(_rules))
policy.resolve_host = lambda host: ["93.184.216.34"]
"""
    )
    port = _free_port()
    confdir = tmp / "mitm"
    mitmdump = shutil.which("mitmdump") or str(Path(sys.executable).parent / "mitmdump")
    proc = subprocess.Popen(
        [mitmdump, "--listen-port", str(port), "--set", f"confdir={confdir}", "-s", str(addon)],
        env={"EGRESS_GATEWAY_TOKEN": TOKEN, "PATH": "/usr/bin:/bin"},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 30
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), 0.2).close()
            break
        except OSError:
            assert proc.poll() is None, proc.stdout.read().decode()
            assert time.monotonic() < deadline, "mitmdump didn't start"
            time.sleep(0.1)
    ca = ssl.create_default_context(cafile=str(confdir / "mitmproxy-ca-cert.pem"))
    yield {
        "proxy": port,
        "ca": ca,
        "pinned": pinned,
        "changed": wrong_pin,
        "unpinned": unpinned,
        "fingerprint": fingerprint,
    }
    proc.terminate()
    proc.wait(10)


def _post(live, port: int, credential: str | None, headers: dict | None = None):
    conn = http.client.HTTPSConnection("127.0.0.1", live["proxy"], context=live["ca"], timeout=10)
    tunnel = {"Proxy-Authorization": credential} if credential else {}
    conn.set_tunnel("127.0.0.1", port, headers=tunnel)
    try:
        conn.request("POST", "/mcp", body=b"{}", headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def gateway() -> str:
    return "Basic " + base64.b64encode(f"gateway:{TOKEN}".encode()).decode()


def test_gateway_reaches_a_pinned_server_with_its_credential(live):
    status, _, body = _post(
        live, live["pinned"], gateway(), {"Authorization": "Bearer server-token", "Cookie": "x=1"}
    )
    assert status == 200, body
    assert json.loads(body) == {"authorization": "Bearer server-token", "cookie": None}


def test_changed_certificate_refused(live):
    status, headers, _ = _post(live, live["changed"], gateway())
    assert status == 403
    assert headers["X-Egress-Denied"] == "certificate_changed"
    assert headers["X-Egress-Certificate"] == live["fingerprint"]


def test_unpinned_self_signed_server_untrusted(live):
    status, headers, _ = _post(live, live["unpinned"], gateway())
    assert status == 403
    assert headers["X-Egress-Denied"] == "certificate_untrusted"


def test_unregistered_port_refused_at_connect(live):
    with pytest.raises(OSError, match="403"):
        _post(live, _free_port(), gateway())


def test_wrong_credential_refused_at_connect(live):
    bad = "Basic " + base64.b64encode(b"gateway:guess").decode()
    with pytest.raises(OSError, match="407"):
        _post(live, live["pinned"], bad)


def test_without_the_credential_post_is_still_refused(live):
    status, headers, body = _post(live, live["pinned"], None)
    assert status == 403
    assert b"method not allowed" in body
    assert "X-Egress-Denied" not in headers
