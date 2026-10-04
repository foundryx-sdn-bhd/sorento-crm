"""Suite-wide block on HTTP calls to LLM and messaging providers (NO-LIVE-LLM-TESTS).

The backend `.env` a developer runs pytest against carries real provider keys, and a test
that forgets its own stub would otherwise reach the provider and spend them. `install()`
wraps every HTTP client the venv can import (httpx and its `httpx2` fork, which the
openai and anthropic SDKs ship on, sync and async; urllib) plus a
`socket.getaddrinfo` backstop for anything else, so a request to a blocked host raises
`LiveExternalCallBlocked` before any connection opens. Every attempt is also appended to `BLOCKED_CALLS`, which `tests/conftest.py` turns
into a teardown failure, so app code that swallows the exception cannot pass silently.

Mock transports (`httpx.MockTransport`, Starlette's TestClient) are not `HTTPTransport`,
so they never reach these wrappers. `ENABLED` is flipped off by conftest only for a test
marked `live_external` while `CHATBOT_LIVE_LLM=1`.
"""
from __future__ import annotations

import socket
import urllib.parse
import urllib.request

# A host is blocked when it IS one of these or is a subdomain of one.
BLOCKED_DOMAINS = (
    "openai.com",
    "anthropic.com",
    "generativelanguage.googleapis.com",
    "respond.io",
    "graph.facebook.com",
)

LIVE_OPT_IN_ENV = "CHATBOT_LIVE_LLM"

BLOCKED_CALLS: list[str] = []
ENABLED = True
_INSTALLED: dict = {}


class LiveExternalCallBlocked(RuntimeError):
    pass


def is_blocked_host(host: str | None) -> bool:
    host = (host or "").strip().lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in BLOCKED_DOMAINS)


def check(method: str, url: str, host: str | None) -> None:
    if not ENABLED or not is_blocked_host(host):
        return
    call = f"{method} {url}"
    BLOCKED_CALLS.append(call)
    raise LiveExternalCallBlocked(
        f"Live external call blocked in tests: {call} (host {host}). Stub the provider "
        f"(e.g. monkeypatch get_provider / llm_call.chat, or an httpx.MockTransport), or "
        f"mark the test @pytest.mark.live_external and run with {LIVE_OPT_IN_ENV}=1."
    )


def fail_if_blocked() -> None:
    """Teardown half: fail the test if it attempted any blocked call, swallowed or not."""
    import pytest

    blocked = list(BLOCKED_CALLS)
    BLOCKED_CALLS.clear()
    if blocked:
        pytest.fail(
            "This test attempted live external call(s), blocked by tests/_live_call_guard.py: "
            + "; ".join(blocked)
            + f". Stub the provider, or mark the test @pytest.mark.live_external "
            f"(runs only with {LIVE_OPT_IN_ENV}=1).",
            pytrace=False,
        )


def _wrap(owner, attr: str, make) -> None:
    original = getattr(owner, attr)
    setattr(owner, attr, make(original))
    _INSTALLED[(owner, attr)] = original


def _httpx_like(module) -> None:
    def sync_wrapper(original):
        def handle_request(self, request):
            check(request.method, str(request.url), request.url.host)
            return original(self, request)

        return handle_request

    def async_wrapper(original):
        async def handle_async_request(self, request):
            check(request.method, str(request.url), request.url.host)
            return await original(self, request)

        return handle_async_request

    _wrap(module.HTTPTransport, "handle_request", sync_wrapper)
    _wrap(module.AsyncHTTPTransport, "handle_async_request", async_wrapper)


def _urllib() -> None:
    def wrapper(original):
        def opener_open(self, fullurl, *args, **kwargs):
            if isinstance(fullurl, urllib.request.Request):
                url, method = fullurl.full_url, fullurl.get_method()
            else:
                url, method = str(fullurl), "GET"
            check(method, url, urllib.parse.urlsplit(url).hostname)
            return original(self, fullurl, *args, **kwargs)

        return opener_open

    _wrap(urllib.request.OpenerDirector, "open", wrapper)


def _socket_backstop() -> None:
    """Any other client, connecting directly: refuse to resolve a blocked host.

    Behind an HTTP proxy a client resolves the PROXY, not the target, which is why the
    transport wrappers above are the primary layer and this is only the backstop.
    """
    def wrapper(original):
        def getaddrinfo(host, *args, **kwargs):
            name = host.decode() if isinstance(host, bytes) else str(host or "")
            check("CONNECT", name, name)
            return original(host, *args, **kwargs)

        return getaddrinfo

    _wrap(socket, "getaddrinfo", wrapper)


def install() -> None:
    """Wrap every HTTP client this venv can import. Idempotent; never uninstalled."""
    if _INSTALLED:
        return
    import importlib

    # `httpx2` is the fork the openai and anthropic SDKs ship on; `httpx` is the app's own.
    for name in ("httpx", "httpx2"):
        try:
            _httpx_like(importlib.import_module(name))
        except ImportError:
            pass
    _urllib()
    # requests / aiohttp are not used by app/; a direct connection from either is still
    # caught by the getaddrinfo backstop (no proxy). Wrap them here once app/ imports one.
    _socket_backstop()
