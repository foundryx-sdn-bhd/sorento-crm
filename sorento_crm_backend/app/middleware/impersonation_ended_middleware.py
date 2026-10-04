"""Say so when a view-as header was ignored (SESSION-NEVER-STUCK).

``dependencies._maybe_apply_impersonation`` serves the admin's own data when it cannot
honour ``X-Impersonate-User-Id`` (view-as stopped elsewhere, admin role removed, target
deactivated). Without a signal the client kept its "viewing as X" banner over that data.
The dependency marks ``request.state.impersonation_ended``; this stamps
``X-Impersonation-Ended: 1`` on whatever response follows, handled errors (403, 404,
AppException) included, which a dependency's own ``Response`` parameter could not do. An
unhandled 500 is built by Starlette's ServerErrorMiddleware outside this layer and carries
no header; the next successful call does.
"""
from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

IMPERSONATION_ENDED_HEADER = "X-Impersonation-Ended"
IMPERSONATION_ENDED_STATE = "impersonation_ended"


class ImpersonationEndedMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send(message: Message) -> None:
            if message["type"] == "http.response.start" and (scope.get("state") or {}).get(
                IMPERSONATION_ENDED_STATE
            ):
                MutableHeaders(scope=message)[IMPERSONATION_ENDED_HEADER] = "1"
            await send(message)

        await self.app(scope, receive, _send)
