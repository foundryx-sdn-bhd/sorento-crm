"""Who may use the connector: the session guard, the permission check and the audit row.

The guard runs inside ``app.dependencies._resolve_session_to_user`` for sessions whose
``auth_method`` is ``claude_connector`` and nothing else (card section 3, step 6).
"""
from __future__ import annotations

import hmac
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
            "args": args,
            "status": status_text,
            "ms": ms if ms is not None else int((time.perf_counter() - started) * 1000),
            "rows": rows,
            "chars": chars,
        },
    )
    db.commit()
