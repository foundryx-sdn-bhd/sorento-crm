"""HTTP client for CRM API calls."""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Mapping, MutableMapping

import httpx

from sorento_crm_mcp.settings import Settings

logger = logging.getLogger(__name__)


def http_error_body(text: str, *, status_code: int, path: str, method: str) -> str:
    """A non-2xx answer, as the JSON the model and the presenters read.

    CHATBOT-SELFREF-SCOPE B3 (production, 30 Sep 2026): the route's 403 body used to ride
    through verbatim, and `presenters._sales_analysis_envelope` rendered it as "Could not
    run the sales report right now." with `has_result: true` and no `error`, so the turn
    trace showed a successful tool call with nothing to explain the refusal. The body's
    own keys are kept (`detail`, `message`, `code`, an `error` the route already named)
    and the HTTP status is stamped beside them: `status_code` (int), `error` (set only
    when the body carried none) and `http_error` (always, the one-line summary a trace
    reader wants). `present_response` turns a body carrying `status_code >= 400` into an
    error envelope for every presenter tool.
    """
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        parsed = None
    detail: Any = parsed if isinstance(parsed, (dict, list)) else (text or "")[:1000]
    body: dict[str, Any] = dict(parsed) if isinstance(parsed, dict) else {"detail": detail}
    reason = ""
    if isinstance(parsed, dict):
        reason = str(parsed.get("code") or parsed.get("message") or parsed.get("detail") or parsed.get("error") or "")
    summary = f"{method} {path} returned HTTP {status_code}" + (f": {reason[:200]}" if reason else "")
    body.setdefault("error", summary)
    body["http_error"] = summary
    body["status_code"] = int(status_code)
    body["path"] = path
    body["method"] = method
    return json.dumps(body)


class CRMClient:
    """Thin async client with X-API-Key and response size guard."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base = settings.crm_base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base,
            headers={"X-API-Key": settings.external_api_key, "Accept": "application/json"},
            timeout=httpx.Timeout(settings.request_timeout_seconds),
            # Avoid stale keep-alive sockets causing first-request read timeouts.
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=0),
            follow_redirects=True,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(
        self,
        path: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        query: MutableMapping[str, Any] | None = None,
        tool_name: str | None = None,
    ) -> str:
        """Perform GET; return JSON text or error message for the model."""
        return await self.request("GET", path, path_params=path_params, query=query, tool_name=tool_name)

    async def post(
        self,
        path: str,
        *,
        path_params: Mapping[str, Any] | None = None,
        query: MutableMapping[str, Any] | None = None,
        body: Any = None,
        tool_name: str | None = None,
    ) -> str:
        """Perform POST; return JSON text or error message for the model."""
        return await self.request("POST", path, path_params=path_params, query=query, body=body, tool_name=tool_name)

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
        """Perform HTTP request; return JSON text or error message for the model."""
        def _preview(value: Any, *, limit: int = 1000) -> str:
            try:
                text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            except Exception:
                text = repr(value)
            text = (text or "").replace("\n", " ")
            return text if len(text) <= limit else (text[:limit] + "...<truncated>")

        rendered = path
        if path_params:
            for k, v in path_params.items():
                rendered = rendered.replace("{" + k + "}", str(v))
        params = {k: v for k, v in (query or {}).items() if v is not None}
        method_upper = method.upper()

        # Attribution for the CRM's api_call_log. Without these the backend
        # cannot tell MCP from n8n at all - both authenticate with the same
        # shared EXTERNAL_API_KEY and send nothing else to distinguish them.
        # correlation_id joins our elapsed_ms below to the server-side span, so
        # network time and server time can be told apart.
        correlation_id = str(uuid.uuid4())
        call_headers = {
            "X-Source": "mcp",
            "X-Correlation-Id": correlation_id,
        }
        if tool_name:
            call_headers["X-Tool-Name"] = tool_name

        started = time.perf_counter()
        logger.info(
            "CRM request start: tool=%s %s %s path_params=%s params=%s body=%s timeout=%.1fs",
            tool_name or "-",
            method_upper,
            rendered,
            _preview(path_params or {}),
            params,
            _preview(body),
            float(self._settings.request_timeout_seconds),
        )
        try:
            r = await self._client.request(
                method_upper, rendered, params=params, json=body, headers=call_headers
            )
        except httpx.RequestError as e:
            err_type = type(e).__name__
            detail = str(e) or repr(e)
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.warning(
                "CRM request failed: tool=%s corr=%s %s %s params=%s body=%s elapsed_ms=%.1f - %s: %s",
                tool_name or "-",
                correlation_id,
                method_upper,
                rendered,
                params,
                _preview(body),
                elapsed_ms,
                err_type,
                detail,
            )
            return json.dumps(
                {
                    "error": "request_failed",
                    "error_type": err_type,
                    "detail": detail,
                    "path": rendered,
                    "method": method_upper,
                }
            )

        body = r.content
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "CRM request done: tool=%s corr=%s %s %s status=%s elapsed_ms=%.1f bytes=%s response_preview=%s",
            tool_name or "-",
            correlation_id,
            method_upper,
            rendered,
            r.status_code,
            elapsed_ms,
            len(body),
            _preview(body.decode("utf-8", errors="replace")),
        )
        if len(body) > self._settings.max_response_bytes:
            return json.dumps(
                {
                    "error": "response_too_large",
                    "path": rendered,
                    "max_bytes": self._settings.max_response_bytes,
                }
            )

        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            return json.dumps({"error": "invalid_utf8", "path": rendered, "status_code": r.status_code})

        if r.status_code >= 400:
            logger.info(
                "CRM tool=%s %s %s -> %s (first 400 chars: %s)",
                tool_name or "-",
                method.upper(),
                rendered,
                r.status_code,
                text[:400].replace("\n", " "),
            )
            return http_error_body(text, status_code=r.status_code, path=rendered, method=method_upper)

        return text
