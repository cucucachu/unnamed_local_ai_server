"""LAN / VPN / public client-origin classification (M15-02).

Enrollment, invite accept, and admin routes refuse a *public* origin
(`403 public_origin`). LAN and VPN are privileged and allowed.

The address is `client_ip()`: last `X-Forwarded-For` hop (Caddy's), else
the TCP peer. Earlier XFF entries are client-supplied and ignored.

VPN is matched first so the WireGuard tunnel (`10.13.13.0/24` by default)
is not classified as LAN even though it is RFC1918.
"""

from __future__ import annotations

from collections.abc import Sequence
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
from typing import Literal

from fastapi import Request

from app.api.session_http import client_ip
from app.core.config import Settings
from app.core.errors import Forbidden

Origin = Literal["lan", "vpn", "public"]

IpNetwork = IPv4Network | IPv6Network

# Defaults match `Settings.origin_*_subnets`. VPN first: 10.13.13.0/24 ⊂ 10.0.0.0/8.
DEFAULT_VPN_SUBNETS = "10.13.13.0/24"
DEFAULT_LAN_SUBNETS = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.0/8,::1,fc00::/7,fe80::/10"


def parse_cidrs(value: str) -> tuple[IpNetwork, ...]:
    """Comma-separated CIDRs. Empty parts are skipped; host bits are allowed."""
    nets: list[IpNetwork] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        nets.append(ip_network(part, strict=False))
    return tuple(nets)


def _as_address(ip: str) -> IPv4Address | IPv6Address | None:
    text = ip.strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    try:
        addr = ip_address(text)
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped if mapped is not None else addr


def _in_any(addr: IPv4Address | IPv6Address, networks: Sequence[IpNetwork]) -> bool:
    return any(addr.version == net.version and addr in net for net in networks)


def classify(
    ip: str,
    *,
    vpn: Sequence[IpNetwork] | None = None,
    lan: Sequence[IpNetwork] | None = None,
) -> Origin:
    """`vpn` if the address is in the VPN list, else `lan`, else `public`."""
    addr = _as_address(ip)
    if addr is None:
        return "public"
    vpn_nets = tuple(vpn) if vpn is not None else parse_cidrs(DEFAULT_VPN_SUBNETS)
    lan_nets = tuple(lan) if lan is not None else parse_cidrs(DEFAULT_LAN_SUBNETS)
    if _in_any(addr, vpn_nets):
        return "vpn"
    if _in_any(addr, lan_nets):
        return "lan"
    return "public"


def privileged(origin: Origin) -> bool:
    return origin in ("lan", "vpn")


def request_origin(request: Request) -> Origin:
    settings: Settings = request.app.state.settings
    return classify(
        client_ip(request),
        vpn=parse_cidrs(settings.origin_vpn_subnets),
        lan=parse_cidrs(settings.origin_lan_subnets),
    )


def require_privileged_origin(request: Request) -> None:
    """Refuse a public origin. FastAPI dependency or call from a handler."""
    if not privileged(request_origin(request)):
        raise Forbidden("public_origin")
