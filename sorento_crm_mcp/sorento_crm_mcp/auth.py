"""Bearer-token gate in front of the MCP app (MCP-LOCKDOWN, 9 Oct 2026).

Every request must carry `Authorization: Bearer <MCP_AUTH_TOKEN>`; anything else gets a 401
before FastMCP sees it. `/healthz` is the one exempt path, so a probe can tell "up" from
"refusing" without holding the token.
"""
from __future__ import annotations

import hmac

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

HEALTH_PATH = "/healthz"
MIN_TOKEN_LENGTH = 32


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode()

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
        if not self._authorized(Headers(scope=scope).get("authorization", "")):
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
