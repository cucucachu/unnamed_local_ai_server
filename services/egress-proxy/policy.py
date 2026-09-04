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
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import socket

from mitmproxy import http

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


def _deny(flow: http.HTTPFlow, reason: str) -> None:
    flow.response = http.Response.make(
        403,
        json.dumps({"error": reason}).encode(),
        {"Content-Type": "application/json"},
    )


def request(flow: http.HTTPFlow) -> None:
    """mitmproxy `request` event hook."""
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
