"""The caller's address behind a proxy, one rule for every route that needs it.

`X-Forwarded-For` is only believed when the socket peer is a trusted proxy, and then it is read
right to left: each trusted proxy appended the address it saw, so the first hop that is NOT a
trusted proxy is the client. Anything to the left of it is what the client typed and is ignored.
A malformed hop stops the walk at the last trusted address. When every hop is trusted (an
internal caller) the left-most one is returned.

Trusted proxies default to loopback + private ranges, which is where nginx, the Next server and
the docker bridge sit in this deploy (documentation/plans/security/
PLAN-trusted-proxy-client-ip-10oct.md). `TRUSTED_PROXIES` (comma-separated CIDRs) replaces the
default when the topology differs, e.g. a CDN in front.
"""
from __future__ import annotations

import ipaddress
from functools import lru_cache

from fastapi import Request

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network

_DEFAULT_TRUSTED = (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
    "::1/128", "fc00::/7", "fe80::/10",
)


@lru_cache(maxsize=8)
def _networks(configured: str) -> tuple[_Network, ...]:
    entries = [e.strip() for e in configured.split(",") if e.strip()] or list(_DEFAULT_TRUSTED)
    return tuple(ipaddress.ip_network(e, strict=False) for e in entries)


def _parse(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def client_ip(request: Request) -> str | None:
    """The real client address: the right-most untrusted `X-Forwarded-For` hop when the peer is a
    trusted proxy, else the socket peer."""
    from app.config import settings

    peer = request.client.host if request.client else None
    if peer is None:
        return None
    trusted = _networks(settings.trusted_proxies or "")

    def _is_trusted(ip) -> bool:
        return any(ip in net for net in trusted if ip.version == net.version)

    peer_ip = _parse(peer)
    if peer_ip is None or not _is_trusted(peer_ip):
        return peer

    hops = [h for value in request.headers.getlist("x-forwarded-for") for h in value.split(",")]
    current = str(peer_ip)
    for hop in reversed(hops):
        ip = _parse(hop)
        if ip is None:
            break
        current = str(ip)
        if not _is_trusted(ip):
            break
    return current
