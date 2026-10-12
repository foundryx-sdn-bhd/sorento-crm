"""FastAPI dependencies: require module(s) enabled for current tenant."""
from __future__ import annotations

from typing import Callable, List, Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user, get_current_user_or_api_key
from app.modules.runtime.installer import (
    DEFAULT_TENANT_ID,
    is_module_enabled,
    tenant_has_any_module_row,
)

PUBLIC_VIEW_LINKS_MODULE_KEY = "public_view_links"
from app.services.user_service import UserPermissionService


def _tenant_id_for_request() -> str:
    """Until multi-tenant auth exists, use default tenant."""
    return DEFAULT_TENANT_ID


def _all_modules_effectively_enabled(db: Session, tenant_id: str) -> bool:
    """No rows yet => treat as full suite (migration not run or legacy)."""
    return not tenant_has_any_module_row(db, tenant_id)


def _roles_bypass_module(db: Session, user_id: str) -> bool:
    svc = UserPermissionService(db)
    return bool(
        svc.get_user_role_slugs(user_id) & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"}
    )


def module_blocked(db: Session, user_id: str, module_key: Optional[str]) -> bool:
    """The router guard's own decision, callable per request for a route that serves
    several modules (the reports kernel checks each definition's own module).

    A missing module key is BLOCKED (fail closed): a report that names no owner could
    otherwise only ever be gated by whichever router it happened to be mounted under.
    """
    if not module_key:
        return True
    if not getattr(settings, "module_guard_strict", False):
        return False
    if user_id == "system" or _roles_bypass_module(db, user_id):
        return False
    tenant_id = _tenant_id_for_request()
    if _all_modules_effectively_enabled(db, tenant_id):
        return False
    return not is_module_enabled(db, tenant_id, module_key)


def require_module_enabled(module_key: str) -> Callable:
    """403 if strict mode and module disabled for tenant."""

    async def _guard(
        db: Session = Depends(get_db),
        _user: dict = Depends(get_current_user),
    ) -> None:
        if not getattr(settings, "module_guard_strict", False):
            return
        if _roles_bypass_module(db, _user["id"]):
            return
        tenant_id = _tenant_id_for_request()
        if _all_modules_effectively_enabled(db, tenant_id):
            return
        if not is_module_enabled(db, tenant_id, module_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Module not enabled: {module_key}",
            )

    return _guard


def require_module_enabled_with_api_key(module_key: str) -> Callable:
    """
    Like require_module_enabled but accepts JWT or X-API-Key (system user).
    External API callers skip the module gate so integrations keep working when strict mode is on.
    """

    async def _guard(
        db: Session = Depends(get_db),
        current_user: dict = Depends(get_current_user_or_api_key),
    ) -> None:
        if not module_allowed_with_api_key(db, current_user, module_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Module not enabled: {module_key}",
            )

    return _guard


def module_allowed_with_api_key(db: Session, current_user: dict, module_key: str) -> bool:
    """The decision `require_module_enabled_with_api_key` refuses on, for a route that reads a
    second module's data inside its own answer and must leave that part out rather than refuse
    the whole call (CHAT-FIND: `/products/find` prints stock only when `inventory` is on)."""
    if not getattr(settings, "module_guard_strict", False):
        return True
    uid = str(current_user.get("id") or "")
    if uid == "system":
        return True
    if _roles_bypass_module(db, uid):
        return True
    tenant_id = _tenant_id_for_request()
    if _all_modules_effectively_enabled(db, tenant_id):
        return True
    return is_module_enabled(db, tenant_id, module_key)


def ensure_public_view_links_allowed(
    db: Session,
    *,
    current_user_id: str | None = None,
) -> None:
    """
    Enforce App Store toggle for tokenized public view links.

    Unlike require_module_enabled(), this always checks tenant module rows when present
    (not gated on module_guard_strict), so disabling public_view_links actually
    stops link creation and public view fetches.

    Legacy: if the tenant has no module rows yet, allow (same as other guards).
    Admins/superadmins may still use view links when the module is off (support).
    """
    tenant_id = _tenant_id_for_request()
    if not tenant_has_any_module_row(db, tenant_id):
        return
    if is_module_enabled(db, tenant_id, PUBLIC_VIEW_LINKS_MODULE_KEY):
        return
    if current_user_id and _roles_bypass_module(db, current_user_id):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Module not enabled: {PUBLIC_VIEW_LINKS_MODULE_KEY}",
    )


def require_public_view_links_enabled() -> Callable:
    """403 when public_view_links is off (see ensure_public_view_links_allowed)."""

    async def _guard(
        db: Session = Depends(get_db),
        _user: dict = Depends(get_current_user),
    ) -> None:
        ensure_public_view_links_allowed(db, current_user_id=str(_user.get("id") or ""))

    return _guard


def require_any_module_enabled(*module_keys: str) -> Callable:
    """403 if strict mode and none of the listed modules are enabled."""

    keys = list(module_keys)

    async def _guard(
        db: Session = Depends(get_db),
        _user: dict = Depends(get_current_user),
    ) -> None:
        if not getattr(settings, "module_guard_strict", False):
            return
        if _roles_bypass_module(db, _user["id"]):
            return
        tenant_id = _tenant_id_for_request()
        if _all_modules_effectively_enabled(db, tenant_id):
            return
        for k in keys:
            if is_module_enabled(db, tenant_id, k):
                return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"One of these modules must be enabled: {', '.join(keys)}",
        )

    return _guard
