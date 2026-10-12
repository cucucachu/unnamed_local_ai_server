"""M7-02 egress policy — mitmproxy addon loaded by `mitmdump -s /app/policy.py`
(this service's `Dockerfile`/`docker-compose.yml` `command:`).

Read-only guarantee, enforced here instead of trusted from the fetcher's own
code (docs/ARCHITECTURE.md §5 "Security model"):

- `request()`: method allowlist (`GET`/`HEAD` only) + destination guard
  (loopback/RFC1918/link-local/CGNAT/`.local`/`.internal`/bare-hostname/
  non-80/443 all denied), then strips client-identity headers.
- `responseheaders()`: kills the flow if `Content-Length` exceeds
  `EGRESS_MAX_BYTES`; otherwise marks the response for streaming.
- `response()`: one log line per request that actually got a response
  (method, host, path truncated, status, bytes) — the completion path for
  every allowed request AND every request denied inside `request()` (mitmproxy
  still runs the response hooks against the synthesized 403 `flow.response`
  set there). Requests killed in `responseheaders()` (oversized bodies) log
  their own line there instead, since a killed flow never reaches `response()`.

Every function whose name doesn't take a `flow`/mitmproxy object is a pure
decision function, unit-tested directly (table-driven) in
`tests/test_policy.py`; the `flow`-taking functions are thin hooks wired
against mitmproxy's own `mitmproxy.test.tflow`/`tutils` helpers in the same
file, so the 403 paths are tested through the real addon entry points too,
not just the pure helpers in isolation.

**The integrations gateway (M18-05, docs/INTEGRATIONS.md §3)** is the one
client with other rules. It identifies itself with a proxy credential
(`gateway:EGRESS_GATEWAY_TOKEN`, Basic `Proxy-Authorization`); a connection
that presents it may use the methods MCP needs (`GATEWAY_METHODS`), but only
to the exact scheme, host and port of an installed integration, from the
platform's `GET /internal/egress-rules`. Private addresses only where the
admin made the exception, loopback never, plain http only with the
exception. A pinned certificate replaces CA and hostname checks with an
exact SHA-256 match on the server's certificate. Gateway denials carry
`X-Egress-Denied: <code>` so the gateway can tell them from a server's own
403. Everyone else's rules are unchanged.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import socket
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from mitmproxy import ctx, http, tls

logger = logging.getLogger("egress_policy")

ALLOWED_METHODS = frozenset({"GET", "HEAD"})
ALLOWED_PORTS = frozenset({80, 443})
# Headers that carry client identity — read-only browsing never needs them
# (spec §2), and forwarding them would leak agent-server's own session state
# to whatever public host is on the other end of the request.
IDENTITY_HEADERS = ("Cookie", "Authorization")
# TLDs that only make sense for private/LAN-local names, never a public
# destination — checked as a plain suffix match on the lower-cased,
# trailing-dot-stripped host.
DENIED_HOST_SUFFIXES = (".local", ".internal")

DEFAULT_MAX_BYTES = 20 * 1024 * 1024  # 20 MB

METHOD_DENIAL_REASON = "method not allowed by egress policy"
DESTINATION_DENIAL_REASON = "destination not allowed by egress policy"


def max_bytes() -> int:
    """`EGRESS_MAX_BYTES` env var (bytes), default 20 MB — read live rather
    than cached at import time so tests can monkeypatch `os.environ` freely."""
    raw = os.environ.get("EGRESS_MAX_BYTES")
    if raw is None or raw == "":
        return DEFAULT_MAX_BYTES
    try:
        return int(raw)
    except ValueError:
        return DEFAULT_MAX_BYTES


def method_denial_reason(method: str) -> str | None:
    """None if `method` is allowed (GET/HEAD, case-insensitive); otherwise
    the 403 reason string."""
    if method.upper() not in ALLOWED_METHODS:
        return METHOD_DENIAL_REASON
    return None


def _is_disallowed_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for loopback / RFC1918 / link-local / CGNAT (100.64/10) / IPv6
    ULA+loopback+link-local / multicast / reserved / unspecified — i.e.
    anything that is not an ordinary routable public address.

    `ipaddress`'s own `is_private` already covers RFC1918 (IPv4) and ULA
    `fc00::/7` (IPv6, since Python 3.9), plus loopback/link-local for both
    families and IPv6-mapped-IPv4 — it does NOT cover IPv4 CGNAT
    (100.64.0.0/10, RFC 6598), which is checked separately below.
    """
    if ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_reserved:
        return True
    if ip.is_multicast or ip.is_unspecified:
        return True
    return isinstance(ip, ipaddress.IPv4Address) and ip in ipaddress.ip_network("100.64.0.0/10")


def destination_denial_reason(host: str, port: int, resolved_ips: list[str]) -> str | None:
    """Pure decision function for the destination guard (spec §2). `host`
    is the request's target hostname (never itself parsed as an IP guess —
    the caller is expected to have already resolved it to `resolved_ips`,
    since that's the only thing that can't be spoofed by a client-controlled
    string). `resolved_ips` is the list of A/AAAA results for `host` — pass
    an empty list for "DNS resolution failed", which this treats as denied
    (fail closed, not open).

    None if the destination is allowed; otherwise the 403 reason string.
    Order matches the spec bullet list: port, then bare-hostname/denied-TLD
    (string-only checks, no DNS needed), then resolved-IP checks last.
    """
    if port not in ALLOWED_PORTS:
        return DESTINATION_DENIAL_REASON

    host_norm = host.lower().rstrip(".")
    if "." not in host_norm:
        return DESTINATION_DENIAL_REASON  # bare hostname, e.g. "agent-server"
    if host_norm.endswith(DENIED_HOST_SUFFIXES):
        return DESTINATION_DENIAL_REASON

    if not resolved_ips:
        return DESTINATION_DENIAL_REASON  # DNS resolution failed — fail closed

    for raw_ip in resolved_ips:
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            return DESTINATION_DENIAL_REASON  # unparseable "IP" — fail closed
        if _is_disallowed_ip(ip):
            return DESTINATION_DENIAL_REASON

    return None


def resolve_host(host: str) -> list[str]:
    """The one impure helper: real DNS resolution, split out so
    `destination_denial_reason` above stays a pure, table-driven-testable
    function. Returns [] on any resolution failure (NXDOMAIN, timeout,
    etc.) — `destination_denial_reason` treats that as denied.

    This is a deliberate TOCTOU tradeoff: resolving here and using the
    resolved IPs only for the allow/deny decision (not for the actual
    connection mitmproxy itself makes moments later, which re-resolves via
    its own connection machinery) means a DNS answer that changes between
    this check and mitmproxy's own connect (attacker-controlled DNS
    rebinding) could in principle slip a private IP through after this
    check passed on a public one. Full DNS-rebinding protection would need
    pinning the resolved IP for mitmproxy's own upstream connection too
    (out of scope here — see the ticket's own scope: allow/deny domain
    lists, rate limiting, and caching are explicitly out; this addon closes
    the "obviously wrong destination" gap the spec asks for, not the full
    TOCTOU surface). See the report for how this tradeoff was made explicit.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return []
    return sorted({info[4][0] for info in infos})


def _deny(flow: http.HTTPFlow, reason: str, code: str | None = None, status: int = 403) -> None:
    headers = {"Content-Type": "application/json"}
    if code is not None:
        headers[DENIED_HEADER] = code
    flow.response = http.Response.make(status, json.dumps({"error": reason}).encode(), headers)


# --- the integrations gateway (M18-05) ------------------------------------------------

GATEWAY_USER = "gateway"
# Streamable HTTP: POST (messages), GET (the server's stream), DELETE (end a session).
GATEWAY_METHODS = frozenset({"GET", "HEAD", "POST", "DELETE"})
DENIED_HEADER = "X-Egress-Denied"
CERTIFICATE_HEADER = "X-Egress-Certificate"
RULES_TTL_S = 5.0
# A destination missing from the cached rules refetches, at most this often:
# an integration installed a moment ago works on its first call.
RULES_MISS_INTERVAL_S = 1.0


@dataclass(frozen=True)
class Rule:
    integration: str
    scheme: str
    host: str
    port: int
    private_address: bool
    cert_fingerprint: str | None


def gateway_token() -> str:
    return os.environ.get("EGRESS_GATEWAY_TOKEN", "")


def gateway_credential(header: str | None, token: str) -> bool | None:
    """None: no proxy credential; True: the gateway's; False: presented but wrong."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        user, _, password = base64.b64decode(value.strip(), validate=True).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return bool(token) and user == GATEWAY_USER and hmac.compare_digest(password, token)


def _norm_host(host: str) -> str:
    return host.lower().rstrip(".").strip("[]")


def find_rule(rules: list[Rule], scheme: str, host: str, port: int) -> Rule | None:
    host = _norm_host(host)
    for rule in rules:
        if (rule.scheme, rule.host, rule.port) == (scheme, host, port):
            return rule
    return None


def _never(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_loopback or ip.is_unspecified or ip.is_multicast


def gateway_denial(
    rule: Rule | None, scheme: str, resolved_ips: list[str]
) -> tuple[str, str] | None:
    """None if the gateway may go there; else (code, reason). Pure, like the guard above."""
    if rule is None:
        return "not_registered", "destination is not a registered integration"
    if not resolved_ips:
        return "destination", DESTINATION_DENIAL_REASON
    for raw_ip in resolved_ips:
        try:
            ip = ipaddress.ip_address(raw_ip)
        except ValueError:
            return "destination", DESTINATION_DENIAL_REASON
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if _never(ip):
            return "destination", "loopback, unspecified and multicast are never allowed"
        if _is_disallowed_ip(ip) and not rule.private_address:
            return "destination", "private address without the admin's exception"
    if scheme == "http" and not rule.private_address:
        return "destination", "plain http only to an approved private address"
    return None


def certificate_fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def fetch_platform_rules() -> list[Rule]:
    """The platform's registry, as rules (`GET /internal/egress-rules`)."""
    url = os.environ.get("PLATFORM_URL", "http://platform:8100").rstrip("/")
    req = urllib.request.Request(
        f"{url}/internal/egress-rules",
        headers={"Authorization": f"Bearer {os.environ.get('PLATFORM_EGRESS_TOKEN', '')}"},
    )
    # Direct, never through a proxy (this process is the proxy).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=5) as resp:
        payload = json.loads(resp.read())
    return [
        Rule(
            integration=str(r["integration"]),
            scheme=str(r["scheme"]),
            host=_norm_host(str(r["host"])),
            port=int(r["port"]),
            private_address=bool(r["private_address"]),
            cert_fingerprint=(r.get("cert_fingerprint") or None),
        )
        for r in payload["rules"]
    ]


class RuleSource:
    """The rules, cached for a few seconds and refetched early for an unknown destination.

    A failed fetch keeps the last rules (nothing new is allowed meanwhile);
    before the first successful fetch there are none.
    """

    def __init__(
        self,
        fetch: Callable[[], list[Rule]] = fetch_platform_rules,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._clock = clock
        self._rules: list[Rule] = []
        self._fetched_at: float | None = None
        self._lock = asyncio.Lock()

    async def _refresh(self) -> None:
        self._fetched_at = self._clock()
        try:
            self._rules = await asyncio.to_thread(self._fetch)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("egress rules not refreshed: %s", type(exc).__name__)

    async def lookup(self, scheme: str, host: str, port: int) -> Rule | None:
        async with self._lock:
            age = None if self._fetched_at is None else self._clock() - self._fetched_at
            missing = find_rule(self._rules, scheme, host, port) is None
            if age is None or age >= RULES_TTL_S or (missing and age >= RULES_MISS_INTERVAL_S):
                await self._refresh()
            return find_rule(self._rules, scheme, host, port)


RULES = RuleSource()
# Per client connection: authenticated as the gateway (by its CONNECT, or its
# first plain-http request), the rule its CONNECT matched, and a server
# certificate that failed (the fingerprint it presented).
GATEWAY_CONNECTIONS: set[str] = set()
CONNECT_RULES: dict[str, Rule] = {}
CERTIFICATE_FAILURES: dict[str, tuple[str, str]] = {}


async def _gateway_check(flow: http.HTTPFlow, scheme: str) -> Rule | None:
    """Deny `flow` unless its destination is registered; returns the rule it matched."""
    host, port = flow.request.host, flow.request.port
    rule = await RULES.lookup(scheme, host, port)
    denial = gateway_denial(rule, scheme, await asyncio.to_thread(resolve_host, host))
    if denial is not None:
        code, reason = denial
        _deny(flow, reason, code)
        logger.info("gateway %s %s:%d denied (%s)", flow.request.method, host, port, code)
        return None
    return rule


def _authenticate(flow: http.HTTPFlow) -> bool | None:
    """The gateway's credential on this request, stripped; a wrong one gets 407."""
    credential = gateway_credential(
        flow.request.headers.get("Proxy-Authorization"), gateway_token()
    )
    if "Proxy-Authorization" in flow.request.headers:
        del flow.request.headers["Proxy-Authorization"]
    if credential is False:
        _deny(flow, "proxy credential not accepted", "proxy_auth", status=407)
    return credential


async def http_connect(flow: http.HTTPFlow) -> None:
    """mitmproxy `http_connect` hook: the gateway's tunnel, checked before anything connects."""
    credential = _authenticate(flow)
    if credential is not True:
        return
    client = flow.client_conn.id
    GATEWAY_CONNECTIONS.add(client)
    rule = await _gateway_check(flow, "https")
    if rule is not None:
        CONNECT_RULES[client] = rule


def tls_start_server(data: tls.TlsData) -> None:
    """mitmproxy `tls_start_server` hook: a pinned certificate for the gateway's servers.

    Script addons run before mitmproxy's own TLS setup, so this runs it first
    and then replaces the certificate check: with a pin, only the exact
    certificate passes (no CA or hostname checks: that's what pinning a
    self-signed server means); without one, the normal checks stand and a
    failure is remembered so the request gets a clear denial.
    """
    client = data.context.client.id
    if client not in GATEWAY_CONNECTIONS:
        return
    ctx.master.addons.get("tlsconfig").tls_start_server(data)
    rule = CONNECT_RULES.get(client)
    pin = rule.cert_fingerprint if rule else None
    install_certificate_check(data.ssl_conn, client, pin)


def install_certificate_check(ssl_conn, client: str, pin: str | None) -> None:
    from OpenSSL import SSL, crypto

    def check(_conn, x509, _errno, depth, ok) -> bool:
        if depth > 0:
            return True if pin else bool(ok)
        der = crypto.dump_certificate(crypto.FILETYPE_ASN1, x509)
        presented = certificate_fingerprint(der)
        if pin:
            if hmac.compare_digest(presented, pin.lower()):
                return True
            CERTIFICATE_FAILURES[client] = ("certificate_changed", presented)
            return False
        if not ok:
            CERTIFICATE_FAILURES[client] = ("certificate_untrusted", presented)
        return bool(ok)

    ssl_conn.set_verify(SSL.VERIFY_PEER, check)


def client_disconnected(client) -> None:
    """mitmproxy hook: forget per-connection state."""
    GATEWAY_CONNECTIONS.discard(client.id)
    CONNECT_RULES.pop(client.id, None)
    CERTIFICATE_FAILURES.pop(client.id, None)


async def _gateway_request(flow: http.HTTPFlow) -> None:
    failure = CERTIFICATE_FAILURES.get(flow.client_conn.id)
    if failure is not None:
        code, presented = failure
        _deny(
            flow,
            "the server's certificate doesn't match what the admin approved"
            if code == "certificate_changed"
            else "the server's certificate isn't trusted",
            code,
        )
        flow.response.headers[CERTIFICATE_HEADER] = presented
        return
    if flow.request.method.upper() not in GATEWAY_METHODS:
        _deny(flow, METHOD_DENIAL_REASON, "method")
        return
    if await _gateway_check(flow, flow.request.scheme) is None:
        return
    if "Cookie" in flow.request.headers:
        del flow.request.headers["Cookie"]


async def request(flow: http.HTTPFlow) -> None:
    """mitmproxy `request` event hook."""
    credential = _authenticate(flow)
    if credential is False:
        return
    if credential is True:
        GATEWAY_CONNECTIONS.add(flow.client_conn.id)
    if flow.client_conn.id in GATEWAY_CONNECTIONS:
        await _gateway_request(flow)
        return

    reason = method_denial_reason(flow.request.method)
    if reason is not None:
        _deny(flow, reason)
        return

    host = flow.request.host
    port = flow.request.port
    resolved_ips = resolve_host(host)
    reason = destination_denial_reason(host, port, resolved_ips)
    if reason is not None:
        _deny(flow, reason)
        return

    for header in IDENTITY_HEADERS:
        if header in flow.request.headers:
            del flow.request.headers[header]


def _truncate_path(path: str, limit: int = 200) -> str:
    return path if len(path) <= limit else path[: limit - 3] + "..."


def _log_line(flow: http.HTTPFlow, status: int, num_bytes: int, note: str = "") -> None:
    req = flow.request
    suffix = f" ({note})" if note else ""
    logger.info(
        "%s %s %s -> %s (%d bytes)%s",
        req.method,
        req.host,
        _truncate_path(req.path, 200),
        status,
        num_bytes,
        suffix,
    )


def _content_length(headers: http.Headers) -> int | None:
    raw = headers.get("Content-Length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def responseheaders(flow: http.HTTPFlow) -> None:
    """mitmproxy `responseheaders` event hook: kill oversized responses
    before the body is read, otherwise mark the response to stream rather
    than buffer the full body in memory (spec §3)."""
    if flow.response is None:
        return

    length = _content_length(flow.response.headers)
    if length is not None and length > max_bytes():
        _log_line(flow, flow.response.status_code, length, note="killed: over EGRESS_MAX_BYTES")
        flow.kill()
        return

    flow.response.stream = True


def response(flow: http.HTTPFlow) -> None:
    """mitmproxy `response` event hook: the one-line-per-request log for
    every request that got a response — including the 403s synthesized in
    `request()` above, which still flow through this hook (mitmproxy runs
    the response hooks against a request()-set `flow.response` too; only
    `flow.kill()` in `responseheaders()` above short-circuits before
    reaching here)."""
    if flow.response is None:
        return
    length = _content_length(flow.response.headers)
    if length is None:
        length = len(flow.response.raw_content or b"")
    _log_line(flow, flow.response.status_code, length)
