"""Who may use the connector: the session guard, the permission check and the audit row.

The guard runs inside ``app.dependencies._resolve_session_to_user`` for sessions whose
``auth_method`` is ``claude_connector`` and nothing else (card section 3, step 6).
"""
from __future__ import annotations

import hmac
import json
import time
from typing import Any, Optional

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings

KEY_HEADER = "X-Analytics-Connector-Key"
USE = "analytics.connector.use"
MANAGE = "analytics.connector.manage"
CHAT_VIEW = "system.chat_history.view"
BASE = "/api/v1/analytics-connector"

# POSTs that only read. Everything else but GET/HEAD is refused for a connector session.
READ_ONLY_POSTS = frozenset({"/api/v1/lookup/resolve", f"{BASE}/query", f"{BASE}/audit"})

# The only GET/HEAD routes a connector session may read: the reused tools' backend routes
# (sorento_analytics_mcp.tools.ANALYTICS_REUSED_TOOLS; a test keeps the two in sync) and the
# connector's own routes. Matched on a segment boundary, after the path is normalised.
CONNECTOR_GET_PREFIXES: tuple[str, ...] = (
    BASE,
    "/api/v1/master-data/brands",
    "/api/v1/master-data/certificates",
    "/api/v1/master-data/product-attachments",
    "/api/v1/master-data/product-categories",
    "/api/v1/master-data/products",
    "/api/v1/master-data/units-of-measure",
    "/api/v1/inventory/stock/balance",
    "/api/v1/inventory/warehouses",
    "/api/v1/incoming-stock/by-product",
    "/api/v1/incoming-stock/list",
    "/api/v1/incoming-stock/shipments",
    "/api/v1/order-management/orders",
    "/api/v1/order-management/outstanding-report",
    "/api/v1/order-management/sales-report",
    "/api/v1/order-management/top-selling",
    "/api/v1/procurement/purchase-orders/last-cost",
    "/api/v1/procurement/purchase-orders/placed",
    "/api/v1/procurement/spo-allocations/last-receipt",
    "/api/v1/marketing/promotion-attachments",
    "/api/v1/marketing/promotion-products",
    "/api/v1/marketing/promotions",
    "/api/v1/resource-management/attachments",
    "/api/v1/project-sales/projects",
    "/api/v1/project-sales/reports/forecast",
    "/api/v1/complaints-management/complaints/analytics",
    "/api/v1/forms-management/forms",
    "/api/v1/sla-management/conversation-sla-tracking/dashboard",
    "/api/v1/sla-management/conversation-sla-tracking/event-logs",
)

# Percent-escapes that could re-introduce a separator or a dot segment after decoding.
_SUSPECT_ESCAPES = ("%2f", "%5c", "%2e", "%00")


def connector_path_allowed(path: str, raw_path: str = "") -> bool:
    """Whether a connector GET may reach ``path`` (the decoded ASGI path). Refuses dot segments,
    backslashes, empty segments and encoded separators outright, before any prefix match."""
    raw = (raw_path or "").lower()
    if any(esc in raw for esc in _SUSPECT_ESCAPES) or "\\" in path:
        return False
    segments = path.rstrip("/").split("/")[1:]
    if any(seg in ("", ".", "..") for seg in segments):
        return False
    clean = "/" + "/".join(segments)
    return any(clean == p or clean.startswith(p + "/") for p in CONNECTOR_GET_PREFIXES)


def internal_key_ok(value: Optional[str]) -> bool:
    expected = settings.analytics_connector_internal_key
    if not expected or not value:
        return False
    return hmac.compare_digest(value.encode(), expected.encode())


def require_internal_key(request: Request) -> None:
    """Dependency for the routes only the analytics container may call (no session)."""
    if not internal_key_ok(request.headers.get(KEY_HEADER)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "connector_key_invalid"})


def has_connector_permission(db: Session, user_id: str, slug: str = USE) -> bool:
    """Whether a role the user holds grants ``slug``. Deliberately NOT
    ``check_user_has_permission``: no admin bypass (Q1) and no 30 s cache."""
    from app.models.user import UserPermission, UserRoleAssignment, UserRolePermission

    hit = (
        db.query(UserPermission.id)
        .join(UserRolePermission, UserRolePermission.permission_id == UserPermission.id)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == UserRolePermission.role_id)
        .filter(UserRoleAssignment.user_id == user_id, UserPermission.slug == slug)
        .first()
    )
    return hit is not None


def enforce_connector_guard(request: Request, db: Session, user: dict) -> None:
    """Card step 6 (a) to (c); (d), impersonation, is skipped in ``_maybe_apply_impersonation``."""
    if not internal_key_ok(request.headers.get(KEY_HEADER)):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "connector_key_invalid", "message": "Connector requests must come through the connector"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    method = request.method.upper()
    path = request.url.path.rstrip("/")
    if method not in ("GET", "HEAD") and not (method == "POST" and path in READ_ONLY_POSTS):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="connector_read_only")
    if method in ("GET", "HEAD"):
        raw_path = request.scope.get("raw_path") or b""
        if not connector_path_allowed(request.url.path, raw_path.decode("latin-1")):
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="connector_path_not_allowed")
    if not has_connector_permission(db, str(user["id"]), USE):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "session_revoked", "message": "Connector access was removed"},
            headers={"WWW-Authenticate": "Bearer"},
        )


def current_grant(db: Session, session_id: Optional[str]) -> Any:
    from app.models.analytics_connector import AnalyticsConnectorGrant

    if not session_id:
        return None
    return (
        db.query(AnalyticsConnectorGrant)
        .filter(AnalyticsConnectorGrant.current_session_id == str(session_id))
        .first()
    )


ARGS_CAP = 4096  # serialized chars of tool args kept on an audit row


def cap_args(args: dict) -> dict:
    """Args as stored on the audit row: unchanged up to ARGS_CAP, else a truncated preview."""
    raw = json.dumps(args, default=str)
    if len(raw) <= ARGS_CAP:
        return args
    keep = ARGS_CAP - 200
    while True:
        out = {"_truncated": True, "_chars": len(raw), "preview": raw[:keep]}
        if len(json.dumps(out)) <= ARGS_CAP or keep <= 0:
            return out
        keep //= 2


def record_tool_call(
    db: Session,
    request: Request,
    *,
    tool: str,
    args: dict,
    status_text: str,
    started: float,
    rows: Optional[int] = None,
    chars: Optional[int] = None,
    ms: Optional[int] = None,
) -> None:
    """One ``audit_logs`` EVENT per tool call (card section 6). The actor was stamped by
    ``get_current_user`` and carries ``auth_method='claude_connector'``."""
    from app.services import audit_service

    session_id = getattr(request.state, "session_id", None)
    grant = current_grant(db, session_id)
    audit_service.record(
        db,
        event="analytics_connector.tool_call",
        entity_type="analytics_connector_grant",
        entity_id=str(grant.id if grant is not None else session_id or "unknown"),
        new_values={
            "tool": tool,
            "args": cap_args(args),
            "status": status_text,
            "ms": ms if ms is not None else int((time.perf_counter() - started) * 1000),
            "rows": rows,
            "chars": chars,
        },
    )
    db.commit()
