"""Backend client that acts as the signed-in user (card section 4.1).

Same get/post/request interface as ``sorento_crm_mcp.http_client.CRMClient`` (the compiled catalog
tools call it), but the credential is the caller's own bearer, read per request from a contextvar the
auth gate sets, plus the internal key. ``X-API-Key`` is never sent.

Path params are percent-encoded with ``safe=""`` (and a bare ``.``/``..``/empty value is refused), so a
tool argument can never add a path segment, a dot segment or a query string. The n8n client renders
them raw; its behaviour is left alone.
"""
from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any, Mapping, MutableMapping, Optional
from urllib.parse import quote

import httpx
from sorento_crm_mcp.http_client import CRMClient

from sorento_analytics_mcp.settings import AnalyticsSettings

KEY_HEADER = "X-Analytics-Connector-Key"
current_bearer: ContextVar[Optional[str]] = ContextVar("analytics_bearer", default=None)


class UserBearerClient(CRMClient):
    def __init__(self, settings: AnalyticsSettings) -> None:  # noqa: D107 - CRMClient.__init__ would set X-API-Key
        self._settings = settings  # type: ignore[assignment]
        self._base = settings.crm_base_url.rstrip("/")
        key = settings.internal_key

        async def _auth(request: httpx.Request) -> None:
            token = current_bearer.get()
            if token:
                request.headers["Authorization"] = f"Bearer {token}"
            request.headers[KEY_HEADER] = key
            request.headers.pop("X-API-Key", None)

        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={"Accept": "application/json"},
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=0),
            follow_redirects=False,
            event_hooks={"request": [_auth]},
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        query: MutableMapping[str, Any] | None = None,
        body: Any = None,
        tool_name: str | None = None,
    ) -> str:
        encoded: dict[str, str] = {}
        for k, v in (path_params or {}).items():
            raw = str(v)
            if raw.strip(".") == "":
                return json.dumps({"error": "invalid_path_param", "param": k, "path": path, "method": method.upper()})
            encoded[k] = quote(raw, safe="")
        return await super().request(
            method, path, path_params=encoded or None, query=query, body=body, tool_name=tool_name
        )
