"""Bearer-token gate in front of the MCP app (SEC-MCP-AUTH, 16 Oct 2026; ported from PR #1548).

Every request must carry `Authorization: Bearer <MCP_AUTH_TOKEN>`; anything else gets a 401
before FastMCP sees it. `/healthz` is the one exempt path, so a probe can tell "up" from
"refusing" without holding the token.

`MCP_AUTH_MODE=permit` is the one-release rollout mode: a request with NO Authorization header
passes and logs a warning (method and path only, never a header value), so callers can move over
without an outage. A wrong token is refused in both modes.
"""
from __future__ import annotations

import hmac
import logging

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

HEALTH_PATH = "/healthz"
MIN_TOKEN_LENGTH = 32
MODES = ("enforce", "permit")
# Request headers that mean something only once the caller proved it holds the token.
AUTHENTICATED_ONLY_HEADERS = (b"x-mcp-scope", b"x-mcp-staff-user")


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str, *, mode: str = "enforce") -> None:
        self._app = app
        self._token = token.encode()
        self._permit = mode == "permit"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            # lifespan must pass through so FastMCP's session manager starts.
            await self._app(scope, receive, send)
            return
        if scope["type"] != "http":
            # No websocket route exists; refuse anything that is not plain HTTP outright.
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            return
        if scope["path"] == HEALTH_PATH:
            await PlainTextResponse("ok")(scope, receive, send)
            return
        header = Headers(scope=scope).get("authorization")
        if header is None and self._permit:
            logger.warning(
                "unauthenticated MCP request allowed (permit mode): %s %s",
                scope.get("method", "-"),
                scope["path"],
            )
            # The staff scope opt-out (`scope.py`) is honoured only on an authenticated
            # request, so an unauthenticated one loses those headers before FastMCP sees it.
            scope = dict(scope)
            scope["headers"] = [
                (k, v) for k, v in scope["headers"] if k.lower() not in AUTHENTICATED_ONLY_HEADERS
            ]
        elif header is None or not self._authorized(header):
            response = PlainTextResponse(
                "Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)

    def _authorized(self, header: str) -> bool:
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(presented.strip().encode(), self._token)
