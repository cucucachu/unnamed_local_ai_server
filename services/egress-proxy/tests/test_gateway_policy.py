"""M18-05: the integrations gateway's identity and per-integration rules."""

from __future__ import annotations

import asyncio
import base64

import pytest
from mitmproxy.test import tflow, tutils

import policy

TOKEN = "egress-gateway-secret"
PUBLIC = ["93.184.216.34"]


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def rule(**overrides) -> policy.Rule:
    fields = {
        "integration": "home",
        "scheme": "https",
        "host": "ha.example.com",
        "port": 443,
        "private_address": False,
        "cert_fingerprint": None,
    }
    return policy.Rule(**{**fields, **overrides})


@pytest.fixture(autouse=True)
def gateway_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("EGRESS_GATEWAY_TOKEN", TOKEN)
    policy.GATEWAY_CONNECTIONS.clear()
    policy.CONNECT_RULES.clear()
    policy.CERTIFICATE_FAILURES.clear()
    yield


@pytest.fixture
def rules(monkeypatch: pytest.MonkeyPatch):
    current: list[policy.Rule] = []
    monkeypatch.setattr(policy, "RULES", policy.RuleSource(fetch=lambda: list(current)))
    return current


def resolves_to(monkeypatch: pytest.MonkeyPatch, ips: list[str]) -> None:
    monkeypatch.setattr(policy, "resolve_host", lambda host: ips)


def flow(method: str, host: str, port: int, scheme: str = "https", credential: str | None = None):
    req = tutils.treq(method=method.encode(), host=host, port=port, scheme=scheme.encode())
    if credential is not None:
        req.headers["Proxy-Authorization"] = credential
    return tflow.tflow(req=req)


def denied(f) -> str | None:
    if f.response is None:
        return None
    return f.response.headers.get(policy.DENIED_HEADER, f"status {f.response.status_code}")


# --- pure decisions -------------------------------------------------------------------------


class TestCredential:
    def test_gateway_credential(self) -> None:
        assert policy.gateway_credential(basic("gateway", TOKEN), TOKEN) is True

    @pytest.mark.parametrize(
        "header",
        [basic("gateway", "nope"), basic("someone", TOKEN), "Bearer " + TOKEN, "Basic !!!"],
    )
    def test_wrong_credentials(self, header: str) -> None:
        assert policy.gateway_credential(header, TOKEN) is False

    def test_absent(self) -> None:
        assert policy.gateway_credential(None, TOKEN) is None

    def test_unset_token_never_matches(self) -> None:
        assert policy.gateway_credential(basic("gateway", ""), "") is False


class TestGatewayDenial:
    def test_registered_public_host(self) -> None:
        assert policy.gateway_denial(rule(), "https", PUBLIC) is None

    def test_unregistered(self) -> None:
        assert policy.gateway_denial(None, "https", PUBLIC)[0] == "not_registered"

    def test_find_rule_is_exact(self) -> None:
        rules = [rule()]
        assert policy.find_rule(rules, "https", "HA.example.com.", 443) == rules[0]
        assert policy.find_rule(rules, "https", "ha.example.com", 8443) is None
        assert policy.find_rule(rules, "http", "ha.example.com", 443) is None
        assert policy.find_rule(rules, "https", "other.example.com", 443) is None

    def test_name_resolving_private_needs_the_exception(self) -> None:
        assert policy.gateway_denial(rule(), "https", ["192.168.0.50"])[0] == "destination"
        assert policy.gateway_denial(rule(private_address=True), "https", ["192.168.0.50"]) is None

    @pytest.mark.parametrize("ip", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "0.0.0.0", "224.0.0.1"])
    def test_loopback_refused_even_with_the_exception(self, ip: str) -> None:
        assert policy.gateway_denial(rule(private_address=True), "https", [ip])[0] == "destination"

    def test_plain_http_only_with_the_exception(self) -> None:
        assert policy.gateway_denial(rule(scheme="http"), "http", PUBLIC)[0] == "destination"
        lan = rule(scheme="http", private_address=True)
        assert policy.gateway_denial(lan, "http", ["10.0.0.5"]) is None

    def test_dns_failure_fails_closed(self) -> None:
        assert policy.gateway_denial(rule(), "https", [])[0] == "destination"


class TestRuleSource:
    def test_cached_then_refetched_for_an_unknown_destination(self) -> None:
        now = [0.0]
        calls = []
        current = [rule()]

        def fetch():
            calls.append(now[0])
            return list(current)

        source = policy.RuleSource(fetch=fetch, clock=lambda: now[0])

        async def run():
            assert await source.lookup("https", "ha.example.com", 443) is not None
            now[0] = 0.5
            assert await source.lookup("https", "new.example.com", 443) is None
            current.append(rule(host="new.example.com"))
            now[0] = 1.5
            assert await source.lookup("https", "new.example.com", 443) is not None
            now[0] = 2.0
            await source.lookup("https", "ha.example.com", 443)
            current.clear()
            now[0] = 7.0
            assert await source.lookup("https", "ha.example.com", 443) is None

        asyncio.run(run())
        assert calls == [0.0, 1.5, 7.0]

    def test_failed_fetch_keeps_the_last_rules(self) -> None:
        now = [0.0]
        answers = [[rule()]]

        def fetch():
            if not answers:
                raise OSError("platform down")
            return answers.pop()

        source = policy.RuleSource(fetch=fetch, clock=lambda: now[0])

        async def run():
            assert await source.lookup("https", "ha.example.com", 443) is not None
            now[0] = 10.0
            assert await source.lookup("https", "ha.example.com", 443) is not None

        asyncio.run(run())

    def test_nothing_before_the_first_fetch_succeeds(self) -> None:
        def fetch():
            raise OSError("platform down")

        source = policy.RuleSource(fetch=fetch)
        assert asyncio.run(source.lookup("https", "ha.example.com", 443)) is None


# --- hooks ----------------------------------------------------------------------------------


class TestHooks:
    def test_gateway_connect_to_registered_host(self, monkeypatch, rules) -> None:
        rules.append(rule())
        resolves_to(monkeypatch, PUBLIC)
        f = flow("CONNECT", "ha.example.com", 443, credential=basic("gateway", TOKEN))
        asyncio.run(policy.http_connect(f))
        assert f.response is None
        assert "Proxy-Authorization" not in f.request.headers
        assert f.client_conn.id in policy.GATEWAY_CONNECTIONS

    @pytest.mark.parametrize("host,port", [("other.example.com", 443), ("ha.example.com", 8443)])
    def test_gateway_connect_elsewhere_refused(self, monkeypatch, rules, host, port) -> None:
        rules.append(rule())
        resolves_to(monkeypatch, PUBLIC)
        f = flow("CONNECT", host, port, credential=basic("gateway", TOKEN))
        asyncio.run(policy.http_connect(f))
        assert denied(f) == "not_registered"

    def test_wrong_credential_gets_407(self, monkeypatch, rules) -> None:
        rules.append(rule())
        f = flow("CONNECT", "ha.example.com", 443, credential=basic("gateway", "guess"))
        asyncio.run(policy.http_connect(f))
        assert f.response.status_code == 407
        assert f.client_conn.id not in policy.GATEWAY_CONNECTIONS

    def test_connect_without_credential_is_untouched(self, rules) -> None:
        f = flow("CONNECT", "anything.example.com", 443)
        asyncio.run(policy.http_connect(f))
        assert f.response is None
        assert f.client_conn.id not in policy.GATEWAY_CONNECTIONS

    def test_gateway_post_inside_its_tunnel(self, monkeypatch, rules) -> None:
        rules.append(rule())
        resolves_to(monkeypatch, PUBLIC)
        f = flow("POST", "ha.example.com", 443)
        f.request.headers["Authorization"] = "Bearer server-token"
        f.request.headers["Cookie"] = "a=b"
        policy.GATEWAY_CONNECTIONS.add(f.client_conn.id)
        asyncio.run(policy.request(f))
        assert f.response is None
        assert f.request.headers["Authorization"] == "Bearer server-token"
        assert "Cookie" not in f.request.headers

    def test_gateway_method_outside_mcps_refused(self, monkeypatch, rules) -> None:
        rules.append(rule())
        resolves_to(monkeypatch, PUBLIC)
        f = flow("PUT", "ha.example.com", 443)
        policy.GATEWAY_CONNECTIONS.add(f.client_conn.id)
        asyncio.run(policy.request(f))
        assert denied(f) == "method"

    def test_plain_http_to_a_private_exception_only_on_its_port(self, monkeypatch, rules) -> None:
        rules.append(rule(scheme="http", host="192.168.0.50", port=8123, private_address=True))
        resolves_to(monkeypatch, ["192.168.0.50"])
        ok = flow("POST", "192.168.0.50", 8123, scheme="http", credential=basic("gateway", TOKEN))
        asyncio.run(policy.request(ok))
        assert ok.response is None
        other = flow(
            "POST", "192.168.0.50", 8100, scheme="http", credential=basic("gateway", TOKEN)
        )
        asyncio.run(policy.request(other))
        assert denied(other) == "not_registered"

    def test_loopback_refused_even_if_registered(self, monkeypatch, rules) -> None:
        rules.append(rule(scheme="http", host="127.0.0.1", port=8100, private_address=True))
        resolves_to(monkeypatch, ["127.0.0.1"])
        f = flow("POST", "127.0.0.1", 8100, scheme="http", credential=basic("gateway", TOKEN))
        asyncio.run(policy.request(f))
        assert denied(f) == "destination"

    def test_certificate_failure_reported_on_the_request(self, rules) -> None:
        f = flow("POST", "ha.example.com", 443)
        policy.GATEWAY_CONNECTIONS.add(f.client_conn.id)
        policy.CERTIFICATE_FAILURES[f.client_conn.id] = ("certificate_changed", "ab" * 32)
        asyncio.run(policy.request(f))
        assert denied(f) == "certificate_changed"
        assert f.response.headers[policy.CERTIFICATE_HEADER] == "ab" * 32

    def test_everyone_else_keeps_todays_rules(self, monkeypatch, rules) -> None:
        rules.append(rule())
        resolves_to(monkeypatch, PUBLIC)
        f = flow("POST", "ha.example.com", 443)
        asyncio.run(policy.request(f))
        assert f.response.status_code == 403
        assert policy.DENIED_HEADER not in f.response.headers

    def test_disconnect_forgets_the_connection(self) -> None:
        f = flow("GET", "ha.example.com", 443)
        policy.GATEWAY_CONNECTIONS.add(f.client_conn.id)
        policy.CONNECT_RULES[f.client_conn.id] = rule()
        policy.client_disconnected(f.client_conn)
        assert f.client_conn.id not in policy.GATEWAY_CONNECTIONS
        assert f.client_conn.id not in policy.CONNECT_RULES
