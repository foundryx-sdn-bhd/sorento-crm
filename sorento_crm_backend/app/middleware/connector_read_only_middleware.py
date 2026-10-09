"""Refuse writes that carry the analytics container's internal key, before routing.

Only the claude.ai analytics container sends ``X-Analytics-Connector-Key``. For its requests
the read-only rule (card section 3, step 6b) is enforced here too, so a write verb on a path
no route serves answers 403 ``connector_read_only`` instead of a 405 that says nothing. The
session guard in ``app.dependencies`` stays the authority for the key, the permission and
the same method rule on real routes.
"""
from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.services.analytics_connector.access import BASE, KEY_HEADER, READ_ONLY_POSTS

_KEY_HEADER_BYTES = KEY_HEADER.lower().encode()
# The container forwards these POSTs with the key and no session.
_OAUTH_PREFIX = f"{BASE}/oauth/"


class ConnectorReadOnlyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in ("GET", "HEAD", "OPTIONS"):
            if any(name == _KEY_HEADER_BYTES for name, _ in scope["headers"]):
                path = scope["path"].rstrip("/")
                if path not in READ_ONLY_POSTS and not (scope["method"] == "POST" and path.startswith(_OAUTH_PREFIX)):
                    await JSONResponse({"detail": "connector_read_only"}, status_code=403)(scope, receive, send)
                    return
        await self.app(scope, receive, send)
