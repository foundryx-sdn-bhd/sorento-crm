"""The caller's address behind a proxy, one rule for every route that needs it.

`X-Forwarded-For` is only believed when the socket peer is a trusted proxy, and then it is read
right to left: each trusted proxy appended the address it saw, so the first hop that is NOT a
trusted proxy is the client. Anything to the left of it is what the client typed and is ignored.
A malformed hop stops the walk at the last trusted address. When every hop is trusted (an
internal caller) the left-most one is returned.

Trust is opt-in. With `TRUSTED_PROXIES` unset no proxy is trusted and the socket peer is the
answer: behind nginx that is one shared address per proxy, but nothing a client sends can change
it. That is the safe state while the edge nginx is not confirmed to append the address it saw
(`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;`); if it passed the header through
untouched, trusting it would let the client pick its own key. Once it is confirmed, set
`TRUSTED_PROXIES` to the proxies' CIDRs (`RECOMMENDED_TRUSTED_PROXIES` for this deploy: loopback,
where the host nginx connects from, plus the private ranges the docker network uses). See
documentation/plans/security/PLAN-trusted-proxy-client-ip-10oct.md.
"""
from __future__ import annotations

import ipaddress
from functools import lru_cache

from fastapi import Request

_Network = ipaddress.IPv4Network | ipaddress.IPv6Network

RECOMMENDED_TRUSTED_PROXIES = (
    "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128,fc00::/7,fe80::/10"
)


@lru_cache(maxsize=8)
def _networks(configured: str) -> tuple[_Network, ...]:
    entries = [e.strip() for e in configured.split(",") if e.strip()]
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
    if not trusted:
        return peer

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
