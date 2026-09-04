"""Unit tests for `policy.py` (M7-02).

Two layers, matching the ticket's own "table-driven pure functions +
addon wired against tflow" split:

- `TestMethodDenialReason` / `TestDestinationDenialReason`: pure decision
  functions, table-driven, no mitmproxy flow objects involved.
- `TestRequestHook` / `TestResponseHeadersHook` / `TestResponseHook`: the
  actual `request()`/`responseheaders()`/`response()` addon entry points,
  driven with `mitmproxy.test.tflow.tflow()` / `tutils.treq()` /
  `tutils.tresp()` — the same helpers mitmproxy's own addon test suite
  uses (see module docstring in `policy.py`).
"""

from __future__ import annotations

import json

import pytest
from mitmproxy.test import tflow, tutils

import policy

# --------------------------------------------------------------------------
# Pure functions: method allowlist
# --------------------------------------------------------------------------


class TestMethodDenialReason:
    @pytest.mark.parametrize(
        "method",
        ["GET", "get", "Get", "HEAD", "head", "Head"],
    )
    def test_allowed_methods_pass(self, method: str) -> None:
        assert policy.method_denial_reason(method) is None

    @pytest.mark.parametrize(
        "method",
        ["POST", "PUT", "DELETE", "PATCH", "OPTIONS", "CONNECT", "TRACE", ""],
    )
    def test_disallowed_methods_denied(self, method: str) -> None:
        assert policy.method_denial_reason(method) == policy.METHOD_DENIAL_REASON


# --------------------------------------------------------------------------
# Pure functions: destination guard
# --------------------------------------------------------------------------


class TestDestinationDenialReason:
    @pytest.mark.parametrize(
        ("host", "port", "resolved_ips"),
        [
            ("example.com", 443, ["93.184.216.34"]),
            ("example.com", 80, ["93.184.216.34"]),
            ("example.com", 443, ["2606:2800:220:1:248:1893:25c8:1946"]),
            ("sub.example.com", 443, ["93.184.216.34"]),
        ],
    )
    def test_public_host_allowed(self, host: str, port: int, resolved_ips: list[str]) -> None:
        assert policy.destination_denial_reason(host, port, resolved_ips) is None

    @pytest.mark.parametrize(
        ("host", "port", "resolved_ips", "case"),
        [
            # Non-80/443 port.
            ("example.com", 8443, ["93.184.216.34"], "non-standard port"),
            ("example.com", 22, ["93.184.216.34"], "ssh port"),
            # Bare hostname (no dot) - Docker service names, etc.
            ("agent-server", 80, ["172.20.0.5"], "bare hostname"),
            ("localhost", 80, ["127.0.0.1"], "bare hostname 'localhost'"),
            # Denied TLDs.
            ("printer.local", 80, ["192.168.1.50"], ".local TLD"),
            ("db.internal", 443, ["10.0.0.5"], ".internal TLD"),
            ("db.internal.", 443, ["10.0.0.5"], ".internal TLD with trailing dot"),
            # Loopback.
            ("host.example", 80, ["127.0.0.1"], "IPv4 loopback"),
            ("host.example", 80, ["::1"], "IPv6 loopback"),
            # RFC1918 private.
            ("host.example", 80, ["10.1.2.3"], "RFC1918 10/8"),
            ("host.example", 80, ["172.16.5.4"], "RFC1918 172.16/12"),
            ("host.example", 80, ["192.168.1.1"], "RFC1918 192.168/16"),
            # Link-local.
            ("host.example", 80, ["169.254.1.1"], "IPv4 link-local"),
            ("host.example", 80, ["fe80::1"], "IPv6 link-local"),
            # CGNAT.
            ("host.example", 80, ["100.64.0.1"], "CGNAT 100.64/10 low end"),
            ("host.example", 80, ["100.127.255.254"], "CGNAT 100.64/10 high end"),
            # IPv6 ULA.
            ("host.example", 80, ["fc00::1"], "IPv6 ULA fc00::/7 low end"),
            ("host.example", 80, ["fdff::1"], "IPv6 ULA fc00::/7 high end"),
            # Multicast / unspecified / reserved.
            ("host.example", 80, ["224.0.0.1"], "IPv4 multicast"),
            ("host.example", 80, ["0.0.0.0"], "IPv4 unspecified"),
            ("host.example", 80, ["::"], "IPv6 unspecified"),
            # DNS resolution failed entirely (fail closed).
            ("host.example", 80, [], "empty resolved_ips (DNS failure)"),
            # Multiple resolved IPs, only one of which is bad - still denied
            # (an attacker only needs ONE bad answer among several to reach
            # something internal; allowing any-good-IP would defeat the guard).
            ("host.example", 80, ["93.184.216.34", "127.0.0.1"], "mixed public+loopback"),
        ],
    )
    def test_denied_destinations(
        self, host: str, port: int, resolved_ips: list[str], case: str
    ) -> None:
        assert (
            policy.destination_denial_reason(host, port, resolved_ips)
            == policy.DESTINATION_DENIAL_REASON
        ), case


# --------------------------------------------------------------------------
# resolve_host
# --------------------------------------------------------------------------


class TestResolveHost:
    def test_resolution_failure_returns_empty_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import socket

        def raise_gaierror(*args, **kwargs):
            raise socket.gaierror("mock resolution failure")

        monkeypatch.setattr(socket, "getaddrinfo", raise_gaierror)
        assert policy.resolve_host("nonexistent.example.invalid") == []

    def test_resolution_success_returns_sorted_unique_ips(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import socket

        def fake_getaddrinfo(host, port):
            return [
                (socket.AF_INET, None, None, "", ("93.184.216.34", 0)),
                (socket.AF_INET, None, None, "", ("93.184.216.34", 0)),  # duplicate
                (socket.AF_INET6, None, None, "", ("2606:2800:220:1::1", 0, 0, 0)),
            ]

        monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
        result = policy.resolve_host("example.com")
        assert result == sorted({"93.184.216.34", "2606:2800:220:1::1"})


# --------------------------------------------------------------------------
# max_bytes (env var parsing)
# --------------------------------------------------------------------------


class TestMaxBytes:
    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("EGRESS_MAX_BYTES", raising=False)
        assert policy.max_bytes() == policy.DEFAULT_MAX_BYTES

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EGRESS_MAX_BYTES", "1024")
        assert policy.max_bytes() == 1024

    def test_invalid_env_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EGRESS_MAX_BYTES", "not-a-number")
        assert policy.max_bytes() == policy.DEFAULT_MAX_BYTES


# --------------------------------------------------------------------------
# request() hook, wired against real mitmproxy flow objects
# --------------------------------------------------------------------------


class TestRequestHook:
    def _flow_for(self, *, method: bytes, host: str, port: int) -> tflow.tflow:
        req = tutils.treq(method=method, host=host, port=port)
        return tflow.tflow(req=req)

    def test_disallowed_method_denied_with_403_json(self) -> None:
        f = self._flow_for(method=b"POST", host="example.com", port=443)

        policy.request(f)

        assert f.response is not None
        assert f.response.status_code == 403
        body = json.loads(f.response.content)
        assert body == {"error": policy.METHOD_DENIAL_REASON}

    def test_get_to_disallowed_port_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(policy, "resolve_host", lambda host: ["93.184.216.34"])
        f = self._flow_for(method=b"GET", host="example.com", port=8443)

        policy.request(f)

        assert f.response is not None
        assert f.response.status_code == 403
        assert json.loads(f.response.content) == {"error": policy.DESTINATION_DENIAL_REASON}

    def test_get_to_private_ip_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(policy, "resolve_host", lambda host: ["192.168.1.1"])
        f = self._flow_for(method=b"GET", host="192.168.1.1", port=80)

        policy.request(f)

        assert f.response is not None
        assert f.response.status_code == 403
        assert json.loads(f.response.content) == {"error": policy.DESTINATION_DENIAL_REASON}

    def test_get_to_bare_hostname_denied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(policy, "resolve_host", lambda host: ["172.20.0.5"])
        f = self._flow_for(method=b"GET", host="agent-server", port=80)

        policy.request(f)

        assert f.response is not None
        assert f.response.status_code == 403
        assert json.loads(f.response.content) == {"error": policy.DESTINATION_DENIAL_REASON}

    def test_allowed_request_passes_through_untouched(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(policy, "resolve_host", lambda host: ["93.184.216.34"])
        f = self._flow_for(method=b"GET", host="example.com", port=443)

        policy.request(f)

        assert f.response is None

    def test_identity_headers_stripped_on_allowed_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(policy, "resolve_host", lambda host: ["93.184.216.34"])
        req = tutils.treq(method=b"GET", host="example.com", port=443)
        req.headers["Cookie"] = "session=abc123"
        req.headers["Authorization"] = "Bearer secret-token"
        req.headers["Accept"] = "text/html"
        f = tflow.tflow(req=req)

        policy.request(f)

        assert f.response is None  # allowed through
        assert "Cookie" not in f.request.headers
        assert "Authorization" not in f.request.headers
        assert f.request.headers["Accept"] == "text/html"

    def test_denied_request_still_has_identity_headers_removed_path_not_taken(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Denial happens before the header-stripping step for method
        # denials - headers are irrelevant since nothing is ever forwarded,
        # but confirm the deny short-circuit doesn't crash on their presence.
        req = tutils.treq(method=b"POST", host="example.com", port=443)
        req.headers["Cookie"] = "session=abc123"
        f = tflow.tflow(req=req)

        policy.request(f)

        assert f.response.status_code == 403


# --------------------------------------------------------------------------
# responseheaders() hook
# --------------------------------------------------------------------------


class TestResponseHeadersHook:
    def test_oversized_response_is_killed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from mitmproxy.flow import Error

        monkeypatch.setenv("EGRESS_MAX_BYTES", "1000")
        resp = tutils.tresp(headers=[(b"content-length", b"5000")])
        f = tflow.tflow(resp=resp)
        assert f.killable is True  # sanity: tflow() defaults to a killable flow

        policy.responseheaders(f)

        assert f.killable is False  # kill() fired -> no longer killable/live
        assert f.error is not None
        assert f.error.msg == Error.KILLED_MESSAGE

    def test_undersized_response_is_marked_streaming(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EGRESS_MAX_BYTES", "1000")
        resp = tutils.tresp(headers=[(b"content-length", b"5")])
        f = tflow.tflow(resp=resp)

        policy.responseheaders(f)

        assert f.response.stream is True

    def test_response_with_no_content_length_is_marked_streaming(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("EGRESS_MAX_BYTES", "1000")
        resp = tutils.tresp(headers=[])
        f = tflow.tflow(resp=resp)

        policy.responseheaders(f)

        assert f.response.stream is True

    def test_no_response_is_a_noop(self) -> None:
        f = tflow.tflow()
        assert f.response is None

        policy.responseheaders(f)  # must not raise

        assert f.response is None


# --------------------------------------------------------------------------
# response() hook (logging) - just confirm it doesn't raise and reads the
# expected fields; log content itself isn't asserted line-for-line (that's
# testing the logging module, not this addon's logic).
# --------------------------------------------------------------------------


class TestResponseHook:
    def test_logs_without_raising_for_normal_response(self) -> None:
        f = tflow.tflow(resp=True)
        policy.response(f)  # must not raise

    def test_logs_without_raising_when_no_response(self) -> None:
        f = tflow.tflow()
        assert f.response is None
        policy.response(f)  # must not raise

    def test_truncates_long_paths(self) -> None:
        long_path = "/" + ("a" * 500)
        truncated = policy._truncate_path(long_path, limit=200)
        assert len(truncated) == 200
        assert truncated.endswith("...")
