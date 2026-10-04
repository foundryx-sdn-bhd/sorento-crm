"""The caller's address behind a proxy, one rule for every public route that needs it."""
from fastapi import Request


def client_ip(request: Request) -> str | None:
    """The left-most `X-Forwarded-For` entry (the original client; the rest are hops), else the
    socket peer. Client-influenced, so it is provenance or defence in depth, never an
    authority."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None
