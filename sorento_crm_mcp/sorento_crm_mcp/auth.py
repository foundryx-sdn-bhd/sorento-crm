"""Bearer-token gate in front of the MCP app (MCP-LOCKDOWN, 9 Oct 2026).

Every request must carry `Authorization: Bearer <MCP_AUTH_TOKEN>`; anything else gets a 401
before FastMCP sees it. `/healthz` is the one exempt path, so a probe can tell "up" from
"refusing" without holding the token.

`enforce=False` (MCP_AUTH_ENFORCE=false) is rollout phase 1 only: a request without the right
token is let through and logged (never the token itself), so the server logs show which callers
still need the header before enforcement is switched on.
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


class BearerAuthMiddleware:
    def __init__(self, app: ASGIApp, token: str, *, enforce: bool = True) -> None:
        self._app = app
        self._token = token.encode()
        self._enforce = enforce

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            # lifespan must pass through so FastMCP's session manager starts.
            await self._app(scope, receive, send)
            return
        if scope["path"] == HEALTH_PATH:
            await PlainTextResponse("ok")(scope, receive, send)
            return
        header = Headers(scope=scope).get("authorization", "")
        if not self._authorized(header):
            if self._enforce:
                response = PlainTextResponse(
                    "Unauthorized", status_code=401, headers={"WWW-Authenticate": "Bearer"}
                )
                await response(scope, receive, send)
                return
                client = scope.get("client") or ("?", 0)
                logger.warning(
                    "mcp auth: %s bearer token from %s on %s %s, allowed because MCP_AUTH_ENFORCE=false",
                    "invalid" if header else "missing",
                    client[0],
                    scope.get("method", ""),
                    scope["path"],
                )
        await self._app(scope, receive, send)

    def _authorized(self, header: str) -> bool:
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer":
            return False
        return hmac.compare_digest(presented.strip().encode(), self._token)
