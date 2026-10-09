"""The ASGI app: OAuth forwarders, discovery documents, the auth gate and the MCP endpoint."""
from __future__ import annotations

import html
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional
from urllib.parse import parse_qsl, urlsplit

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from sorento_analytics_mcp.client import KEY_HEADER, UserBearerClient, current_bearer
from sorento_analytics_mcp.settings import AnalyticsSettings
from sorento_analytics_mcp.tools import BE, register_chat_tools, register_reused_tools

logger = logging.getLogger(__name__)

MCP_PATH = "/analytics/mcp"
# Claude's one registered callback; the backend only ever names a redirect it verified, and this is a
# second check so the container can never become an open redirect.
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
SCOPE = "analytics.read"


def _build_mcp(settings: AnalyticsSettings) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_app: FastMCP) -> AsyncIterator[dict[str, Any]]:
        client = UserBearerClient(settings)
        try:
            yield {"client": client, "settings": settings}
        finally:
            await client.aclose()

    mcp = FastMCP(
        "Sorento CRM analytics (read-only)",
        instructions=(
            "Read-only access to Sorento CRM data and WhatsApp chat history, as the signed-in staff member. "
            "For chat questions start with chat_list_datasets, then aggregate with chat_query before pulling text."
        ),
        stateless_http=True,
        streamable_http_path=MCP_PATH,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        lifespan=lifespan,
    )
    register_reused_tools(mcp)
    register_chat_tools(mcp)
    return mcp


class App:
    """Outer ASGI app: the 401 gate sits BEFORE the MCP SDK, whose tool errors would be 200s."""

    def __init__(self, settings: AnalyticsSettings) -> None:
        self.s = settings
        self.mcp_asgi = _build_mcp(settings).streamable_http_app()
        self.www_authenticate = (
            f'Bearer resource_metadata="{settings.public_base_url}/.well-known/oauth-protected-resource", '
            f'scope="{SCOPE}"'
        )
        protected = [
            "/analytics/.well-known/oauth-protected-resource",
            "/.well-known/oauth-protected-resource/analytics/mcp",
        ]
        as_docs = [
            "/.well-known/oauth-authorization-server/analytics",
            "/analytics/.well-known/oauth-authorization-server",
            "/analytics/.well-known/openid-configuration",
        ]
        routes = [Route(p, self.protected_resource) for p in protected]
        routes += [Route(p, self.as_metadata) for p in as_docs]
        routes += [
            Route("/analytics/healthz", self.healthz),
            Route("/analytics/oauth/register", self.register, methods=["POST"]),
            Route("/analytics/oauth/authorize", self.authorize, methods=["GET"]),
            Route("/analytics/oauth/token", self.token, methods=["POST"]),
        ]
        self.routes_app = Starlette(routes=routes)

    # ------------------------------------------------------------------ dispatch

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and (scope["path"] == MCP_PATH or scope["path"].startswith(MCP_PATH + "/")):
            await self.gate(scope, receive, send)
        elif scope["type"] == "lifespan":
            await self.mcp_asgi(scope, receive, send)  # starts the MCP session manager
        else:
            await self.routes_app(scope, receive, send)

    async def gate(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = self._bearer(scope)
        if token is None or not await self._me_ok(token):
            resp = Response(status_code=401, headers={"WWW-Authenticate": self.www_authenticate})
            await resp(scope, receive, send)
            return
        reset = current_bearer.set(token)
        try:
            await self.mcp_asgi(scope, receive, send)
        finally:
            current_bearer.reset(reset)

    @staticmethod
    def _bearer(scope: Scope) -> Optional[str]:
        for name, value in scope["headers"]:
            if name == b"authorization":
                parts = value.decode("latin-1").split(" ")
                if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
                    return parts[1].strip()
        return None

    async def _me_ok(self, token: str) -> bool:
        try:
            async with httpx.AsyncClient(base_url=self.s.crm_base_url, timeout=15.0) as c:
                r = await c.get(f"{BE}/me", headers={"Authorization": f"Bearer {token}", KEY_HEADER: self.s.internal_key})
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    # ------------------------------------------------------------------ documents

    async def healthz(self, _request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    async def protected_resource(self, _request: Request) -> Response:
        base = self.s.public_base_url
        return JSONResponse({
            "resource": f"{base}/mcp",
            "authorization_servers": [base],
            "scopes_supported": [SCOPE],
            "bearer_methods_supported": ["header"],
        })

    async def as_metadata(self, _request: Request) -> Response:
        base = self.s.public_base_url
        return JSONResponse({
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "token_endpoint_auth_methods_supported": ["none"],
            "code_challenge_methods_supported": ["S256"],
            "scopes_supported": [SCOPE],
        })

    # ------------------------------------------------------------------ OAuth forwarders

    async def _forward(self, path: str, **kwargs: Any) -> httpx.Response:
        async with httpx.AsyncClient(base_url=self.s.crm_base_url, timeout=30.0) as c:
            return await c.post(f"{BE}{path}", headers={KEY_HEADER: self.s.internal_key}, **kwargs)

    @staticmethod
    def _passthrough(r: httpx.Response, **headers: str) -> Response:
        return Response(r.content, status_code=r.status_code,
                        media_type=r.headers.get("content-type", "application/json"), headers=headers)

    async def register(self, request: Request) -> Response:
        try:
            r = await self._forward("/oauth/register", json=await request.json())
        except (httpx.HTTPError, ValueError):
            return JSONResponse({"error": "server_error"}, status_code=502)
        return self._passthrough(r)

    async def token(self, request: Request) -> Response:
        form = dict(parse_qsl((await request.body()).decode()))
        try:
            r = await self._forward("/oauth/token", data=form)
        except httpx.HTTPError:
            return JSONResponse({"error": "server_error"}, status_code=502, headers={"Cache-Control": "no-store"})
        return self._passthrough(r, **{"Cache-Control": "no-store", "Pragma": "no-cache"})

    async def authorize(self, request: Request) -> Response:
        try:
            r = await self._forward("/oauth/authorize", json=dict(request.query_params))
        except httpx.HTTPError:
            return self._error_page("The sign-in service is unavailable. Close this window and try again.", 502)
        try:
            body = r.json()
        except ValueError:
            body = {}
        if r.status_code == 200 and body.get("request_id"):
            parts = urlsplit(self.s.public_base_url)
            from urllib.parse import quote

            return RedirectResponse(
                f"{parts.scheme}://{parts.netloc}/connect/claude?request={quote(str(body['request_id']))}",
                status_code=302)
        back = body.get("redirect_to") if isinstance(body, dict) else None
        if r.status_code == 400 and isinstance(back, str) and back.startswith(CLAUDE_CALLBACK):
            return RedirectResponse(back, status_code=302)
        message = "This sign-in link is not valid. Close this window and start again from Claude."
        return self._error_page(message, 400 if r.status_code == 400 else 502)

    @staticmethod
    def _error_page(message: str, status_code: int) -> Response:
        return HTMLResponse(
            f"<!doctype html><title>Sorento CRM</title><p>{html.escape(message)}</p>", status_code=status_code)


def create_app() -> App:
    return App(AnalyticsSettings.from_env())
