"""Default-deny company scope for catalog tools (SEC-MCP-AUTH, 16 Oct 2026).

The backend resolves an X-API-Key call with no `contact_id` + `space_id` to EVERY company
(`sorento_crm_backend/app/services/company_scope_resolver.py::_resolve_api_key_scope`), and with
both present through `resolve_contact_company_scope`, which is fail-closed. So the one decision
left to this server is whether to ask at all: a tool that declares the pair is company-scoped, and
a call to it without both values is refused here, before any backend request.

The in-app staff AI assistant has no contact and opts out explicitly with `X-MCP-Scope: staff`
(set by the backend's `MCPRuntimeClient(staff=True)`, only at the assistant's own call sites). The
header only matters on an authenticated request, and a caller holding the token already holds the
key that reads every company, so the opt-out grants nothing new.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

SCOPE_PARAMS = ("contact_id", "space_id")
STAFF_HEADER = "x-mcp-scope"
STAFF_VALUE = "staff"
# The staff user the assistant is answering, when the backend knows it. Logged only.
STAFF_USER_HEADER = "x-mcp-staff-user"


def is_scoped(spec: Any) -> bool:
    declared = set(spec.query_params) | set(spec.body_params)
    return all(p in declared for p in SCOPE_PARAMS)


def _request_headers(ctx: Any) -> Any:
    request = getattr(getattr(ctx, "request_context", None), "request", None)
    return getattr(request, "headers", None)


def is_staff_request(ctx: Any) -> bool:
    headers = _request_headers(ctx)
    if headers is None:
        return False
    return str(headers.get(STAFF_HEADER) or "").strip().lower() == STAFF_VALUE


def _missing_scope(query: dict[str, Any] | None, body: Any) -> list[str]:
    sources = [query or {}]
    if isinstance(body, dict):
        sources.append(body)
    return [p for p in SCOPE_PARAMS if not any(str(src.get(p) or "").strip() for src in sources)]


def check_scope(spec: Any, query: dict[str, Any] | None, body: Any, ctx: Any) -> str | None:
    """The one scope check every catalog tool call runs (`server._compile_tool`).

    Returns the refusal to answer with, or None to go ahead. A staff opt-out that skipped a
    refusal is logged (tool, staff user id) so the follow-up that scopes the assistant to the
    staff user's own companies has the call data."""
    staff = is_staff_request(ctx)
    if staff and is_scoped(spec) and _missing_scope(query, body):
        headers = _request_headers(ctx)
        logger.info(
            "staff-scope MCP call without a contact: tool=%s staff_user=%s",
            spec.name,
            str(headers.get(STAFF_USER_HEADER) or "-").strip()[:64] if headers is not None else "-",
        )
    return scope_refusal(spec, query, body, staff=staff)


def scope_refusal(spec: Any, query: dict[str, Any] | None, body: Any, *, staff: bool) -> str | None:
    """The refusal to return instead of calling the backend, or None to go ahead."""
    if staff or not is_scoped(spec):
        return None
    missing = _missing_scope(query, body)
    if not missing:
        return None
    return json.dumps(
        {
            "error": "scope_required",
            "detail": (
                f"Tool '{spec.name}' reads company-owned data and needs the calling contact: "
                "pass contact_id and space_id."
            ),
            "missing": missing,
            "data": [],
            "total": 0,
        }
    )
