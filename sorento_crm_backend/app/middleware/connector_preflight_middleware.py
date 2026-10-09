"""claude.ai connector preflight (CLAUDE-CONNECTOR contract section 9.4).

For a request carrying ``Authorization: Connector <JWT>`` only: the token is verified BEFORE any
body is read or parsed, and a body over ``MAX_BODY_BYTES`` is refused 413, so an unauthenticated
caller cannot make the app buffer or JSON-parse a large payload. The verified claims ride on the
request state; ``access.connector_call_gate`` then checks ``req_hash`` against the buffered body and
does the rest. Every other request passes straight through.
"""
from __future__ import annotations

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.services.analytics_connector.access import ConnectorError, verify_token

MAX_BODY_BYTES = 1024 * 1024


def _connector_token(scope: Scope) -> str | None:
    for name, value in scope.get("headers") or ():
        if name == b"authorization":
            scheme, _, token = value.decode("latin-1").partition(" ")
            return token.strip() if scheme.lower() == "connector" else None
    return None


class ConnectorPreflightMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = _connector_token(scope) if scope["type"] == "http" else None
        if token is None:
            await self.app(scope, receive, send)
            return
        try:
            claims = verify_token(token)
        except ConnectorError as exc:
            await JSONResponse(exc.detail, status_code=exc.status_code)(scope, receive, send)
            return
        too_large = JSONResponse(
            {"error": {"code": "payload_too_large", "message": "The request body is over 1 MB."}}, status_code=413
        )
        for name, value in scope.get("headers") or ():
            if name == b"content-length" and value.isdigit() and int(value) > MAX_BODY_BYTES:
                await too_large(scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            body.extend(message.get("body", b""))
            if len(body) > MAX_BODY_BYTES:
                await too_large(scope, receive, send)
                return
            if not message.get("more_body", False):
                break
        scope.setdefault("state", {})["connector_claims"] = claims
        sent = False

        async def replay() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
