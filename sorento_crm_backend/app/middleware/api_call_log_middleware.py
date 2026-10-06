"""Request telemetry for `/api/v1/external/*` - total coverage by construction.

Today only 3 of ~30 external endpoints log anything, because logging is opt-in
per endpoint. Middleware inverts that: a new external route is logged the day it
is added, with no per-endpoint code and no way to forget.

Pure ASGI, not BaseHTTPMiddleware, for the same reason as
`idempotency_middleware`: reading the request body in BaseHTTPMiddleware consumes
it and the downstream handler receives an empty body. Here the body is buffered
and replayed, and the response is captured as it streams past.

**Synchronous write, deliberately.** A buffered/async writer drops exactly the
records you need at the moment the process dies - i.e. the incident you are
trying to explain. The cost is per-request latency (measured, see the test
report). The write failing must never affect the response.
"""
from __future__ import annotations

import logging
import time

from app.config import settings
from app.services.api_call_log_service import summarize_validation_errors

logger = logging.getLogger(__name__)

# Strictly scoped. Widening this to all of /api/v1 would log the health
# dashboard's own polling and the log page's own reads back into the table - 
# a feedback loop that grows without bound and drowns the real traffic.
_LOGGED_PREFIX = "/api/v1/external"

# Bodies above this are not buffered at all (file uploads). The row is still
# written; the payload records the reason instead of megabytes of bytes.
_MAX_BUFFER_BYTES = 262144
_OVERSIZE_MARKER = "[body too large to log]"


def _is_sourced_call(headers: dict) -> bool:
    """True when the caller identified itself with `X-Source` (today: the MCP client).

    Needed because MCP tools mostly proxy ordinary CRM endpoints - the products
    catalogue lives at `/api/v1/master-data/*`, not under `/api/v1/external/*`.
    Scoping on the path prefix alone silently missed most MCP traffic even though
    the client was sending full attribution.

    Deliberately keyed on the header rather than on an endpoint list: any caller
    that identifies itself gets recorded, wherever it lands.
    """
    for key, value in headers.items():
        if key.strip().lower() == "x-source" and (value or "").strip():
            return True
    return False


def _should_log(path: str, headers: dict) -> bool:
    """External routes, plus any self-identifying (MCP) caller on any route.

    Internal UI traffic sends no `X-Source` and does not sit under the external
    prefix, so the health dashboard's polling and this table's own reads stay out
  - logging them would feed the table its own traffic.
    """
    return path.startswith(_LOGGED_PREFIX) or _is_sourced_call(headers)


def _decode_headers(raw) -> dict:
    out = {}
    for key, value in raw or []:
        try:
            out[key.decode("latin-1")] = value.decode("latin-1")
        except Exception:  # noqa: BLE001
            continue
    return out


def _actor_label(state) -> str | None:
    """`user:<id>`, `user:<real>/as:<effective>` under impersonation, or
    `integration:<id>/as:<act-as user>`, from the stamped audit actor (identity S0,
    plan 8.2: both the user and the integration)."""
    try:
        actor = (state or {}).get("audit_actor")
    except Exception:  # noqa: BLE001
        return None
    if actor is None:
        return None
    if actor.actor_type == "integration" and actor.integration_id:
        label = f"integration:{actor.integration_id}"
        return f"{label}/as:{actor.user_id}"[:128] if actor.user_id else label
    real = actor.real_user_id
    if real and actor.user_id and real != actor.user_id:
        return f"user:{real}/as:{actor.user_id}"[:128]
    if actor.user_id:
        return f"user:{actor.user_id}"
    return None


class ApiCallLogMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "") or ""
        method = scope.get("method", "GET")
        headers = _decode_headers(scope.get("headers"))

        if not _should_log(path, headers):
            return await self.app(scope, receive, send)
        if not getattr(settings, "api_call_log_enabled", True):
            return await self.app(scope, receive, send)

        # The request state dict is created HERE, before any inner middleware copies
        # the scope, so the audit actor the auth dependency stamps on
        # `request.state.audit_actor` lands in the dict this middleware reads back.
        state = scope.setdefault("state", {})

        # ---- buffer the request body so it can be logged AND replayed ----
        body = b""
        messages: list[dict] = []
        oversize = False
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if not oversize:
                body += chunk
                if len(body) > _MAX_BUFFER_BYTES:
                    oversize = True
                    body = b""
            more = message.get("more_body", False)
            messages.append(message)

        replay = iter(messages)

        async def receive_replay():
            try:
                return next(replay)
            except StopIteration:
                # Downstream may poll past the buffered messages.
                return {"type": "http.request", "body": b"", "more_body": False}

        status_code: int | None = None
        response_body = b""
        response_oversize = False

        async def send_capture(message):
            nonlocal status_code, response_body, response_oversize
            if message["type"] == "http.response.start":
                status_code = message.get("status")
            elif message["type"] == "http.response.body":
                if not response_oversize:
                    response_body += message.get("body", b"")
                    if len(response_body) > _MAX_BUFFER_BYTES:
                        response_oversize = True
                        response_body = b""
            await send(message)

        started = time.perf_counter()
        error_message = None
        try:
            await self.app(scope, receive_replay, send_capture)
        except Exception as exc:  # noqa: BLE001
            # Log the failure, then re-raise: the row is evidence about the
            # request, it does not get to swallow it.
            error_message = f"{type(exc).__name__}: {exc}"
            self._write(
                path=path,
                method=method,
                headers=headers,
                status_code=None,
                request_body=_OVERSIZE_MARKER if oversize else body,
                response_body=None,
                latency_ms=int((time.perf_counter() - started) * 1000),
                error_message=error_message,
                actor=_actor_label(state),
            )
            raise

        # A 422 never reaches the handler, so this row is the ONLY place the
        # refusal is recorded CRM-side. Name the field(s) in error_message (the
        # column the log screen shows and searches) rather than leaving the
        # reader to dig the `loc` out of the response payload.
        self._write(
            path=path,
            method=method,
            headers=headers,
            status_code=status_code,
            request_body=_OVERSIZE_MARKER if oversize else body,
            response_body=_OVERSIZE_MARKER if response_oversize else response_body,
            latency_ms=int((time.perf_counter() - started) * 1000),
            error_message=summarize_validation_errors(
                status_code, None if response_oversize else response_body
            ),
            actor=_actor_label(state),
        )

    def _write(
        self,
        *,
        path,
        method,
        headers,
        status_code,
        request_body,
        response_body,
        latency_ms,
        error_message,
        actor=None,
    ) -> None:
        """Persist one row. Wrapped so no telemetry failure reaches the caller."""
        try:
            from app.database import SessionLocal
            from app.services.api_call_log_service import (
                classify_outcome,
                resolve_correlation_id,
                resolve_source,
                resolve_tool_name,
                sanitize_body,
                write_call_log,
            )

            db = SessionLocal()
            try:
                write_call_log(
                    db,
                    endpoint=path[:512],
                    method=method[:10],
                    source=resolve_source(headers),
                    tool_name=resolve_tool_name(headers),
                    actor=actor,
                    status_code=status_code,
                    outcome=classify_outcome(status_code),
                    latency_ms=latency_ms,
                    correlation_id=resolve_correlation_id(headers),
                    request_payload=sanitize_body(request_body),
                    response_payload=sanitize_body(response_body),
                    error_message=error_message,
                )
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            logger.warning("api_call_log middleware write failed", exc_info=True)
