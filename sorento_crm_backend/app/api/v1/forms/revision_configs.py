"""Per-portal-type revision policy (UAC A2 / A6).

Mounted at ``/api/v1/forms-management/revision-configs`` - the forms router mounts
at ``/forms-management``, not ``/forms``, so the earlier draft's ``/api/v1/forms/...``
would 404. See the S6 correction in ``PLAN-portal-submission-revisions.md``.

The globals (``portal_revisions_enabled`` / ``portal_max_revisions``) live on the
``system_settings`` singleton and save through the general-settings setattr path.
Only the per-type overrides live here.

GET lists **one row per portal submission type**, synthesising a disabled row for a
type with no config yet: a missing row means disabled (A3, fail closed), so the
synthesised row states the truth AND gives the settings table something to edit
without a migration. PUT upserts by ``source_entity_type``.
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import (
    get_current_user_or_api_key,
    require_permission,
    require_permission_with_api_key,
)
from app.models.portal import PortalRevisionConfig
from app.services.error_handler import handle_validation_error
from app.services.portal_revision_service import PortalRevisionService
from app.services.portal_service import GRANTABLE_PORTAL_FORM_TYPES

router = APIRouter()

# GRANTABLE_PORTAL_FORM_TYPES minus sales_opportunity, customer_asks and conversation (no form
# at all): sales_opportunity has no revision-engine
# wiring at all (no PortalRevisionService dispatch, no revise/save-draft route), so a
# settings row for it would configure nothing - exactly the trap the SUPPORTED_TYPES
# comment in portal_service.py warns about. Add it back here the day it gets one.
# sample_request (SR-CORE #1718) is out for the same reason: the portal revise route
# refuses it (no RevisionAdapter in portal_revision_service.ADAPTERS).
# new_product_application (NEW-PRODUCT-APP) likewise: its Revise is its own move (#1698 style),
# not the portal revision engine.
REVISABLE_PORTAL_FORM_TYPES = tuple(
    kind
    for kind in GRANTABLE_PORTAL_FORM_TYPES
    if kind
    not in (
        "sales_opportunity",
        "sample_request",
        "new_product_application",
        "customer_asks",
        "conversation",
    )
)


class PortalRevisionConfigResponse(BaseModel):
    """Every field the settings table reads.

    A ``response_model`` silently drops anything it does not name, so the field
    list here IS the contract with ``portalRevisionConfigService.ts``.
    """

    source_entity_type: str
    is_enabled: bool
    # NULL inherits system_settings.portal_max_revisions.
    max_revisions: Optional[int] = None
    allowed_statuses: list[str] = []
    # NULL restarts at the first stage of this type's form-SLA chain (UAC J4).
    restart_stage_code: Optional[str] = None


class PortalRevisionConfigListResponse(BaseModel):
    items: list[PortalRevisionConfigResponse]


class PortalRevisionEnabledMapResponse(BaseModel):
    """`{type: effective enabled}` - one boolean per portal submission type."""

    types: dict[str, bool]


class PortalRevisionConfigUpdate(BaseModel):
    is_enabled: bool = False
    # Same bounds as the global cap on SystemSettingUpdate, so a per-type override
    # cannot be set to something the global field would refuse.
    max_revisions: Optional[int] = Field(None, ge=0, le=50)
    allowed_statuses: list[str] = []
    restart_stage_code: Optional[str] = None


def _serialize(row: PortalRevisionConfig) -> dict:
    return {
        "source_entity_type": row.source_entity_type,
        "is_enabled": bool(row.is_enabled),
        "max_revisions": row.max_revisions,
        "allowed_statuses": [str(s) for s in (row.allowed_statuses or [])],
        "restart_stage_code": row.restart_stage_code,
    }


def _disabled_placeholder(source_entity_type: str) -> dict:
    """What a type with no row actually resolves to: disabled, no statuses."""
    return {
        "source_entity_type": source_entity_type,
        "is_enabled": False,
        "max_revisions": None,
        "allowed_statuses": [],
        "restart_stage_code": None,
    }


def _check_type(source_entity_type: str) -> str:
    kind = (source_entity_type or "").strip().lower()
    if kind not in REVISABLE_PORTAL_FORM_TYPES:
        raise handle_validation_error(
            f"Unsupported submission type: {source_entity_type!r}. "
            f"Allowed: {', '.join(REVISABLE_PORTAL_FORM_TYPES)}."
        )
    return kind


def _clean_statuses(values: Optional[list[str]]) -> list[str]:
    """Normalised, de-duplicated, order-preserving. The policy resolver lowercases
    before comparing, so storing anything else only makes the settings table lie."""
    out: list[str] = []
    for value in values or []:
        status = str(value or "").strip().lower()
        if status and status not in out:
            out.append(status)
    return out


@router.get("/revision-configs", response_model=PortalRevisionConfigListResponse)
async def list_portal_revision_configs(
    current_user: dict = Depends(require_permission_with_api_key("user_management.settings.view")),
    db: Session = Depends(get_db),
):
    """One entry per portal submission type, in the portal's own type order."""
    rows = {
        row.source_entity_type: row
        for row in db.query(PortalRevisionConfig).all()
    }
    items = [
        _serialize(rows[kind]) if kind in rows else _disabled_placeholder(kind)
        for kind in REVISABLE_PORTAL_FORM_TYPES
    ]
    # A type that is no longer a portal submission type still shows, so a stale row
    # is visible (and editable back to disabled) rather than silently orphaned.
    items.extend(
        _serialize(row) for kind, row in rows.items() if kind not in REVISABLE_PORTAL_FORM_TYPES
    )
    return {"items": items}


@router.get(
    "/revision-configs/enabled",
    response_model=PortalRevisionEnabledMapResponse,
)
async def get_portal_revision_enabled_map(
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Effective per-type enablement, for any authenticated principal.

    The office detail pages read this to decide whether a form gets a Revisions
    tab, so it is deliberately NOT admin-gated: the settings table above is an
    admin surface, this is a handler-facing yes/no carrying four booleans.

    Declared ABOVE the ``/{source_entity_type}`` route so "enabled" is matched as
    a literal path and never swallowed as a type name.

    **Known module coupling (documented, deliberately not fixed).** This router is
    mounted under ``require_module_enabled_with_api_key("forms")`` (see
    ``app/api/v1/__init__.py``), but its ONLY consumers are the PROCUREMENT detail
    pages - stock inquiry, purchase request, sponsorship form - through the
    frontend ``useRevisionEnabledMap``. A tenant with ``procurement`` enabled,
    ``forms`` DISABLED, ``module_guard_strict`` on, and a non-admin user therefore
    gets a 403 here: the map comes back undefined and the Revisions tab silently
    never renders instead of failing visibly. Moving the route to a procurement
    (or shared) mount was considered and DECLINED: it changes the URL and the
    frontend service for a failure needing three conditions at once, and the guard
    short-circuits entirely on installs with no module rows, which is the current
    state - so nobody is hitting this today. If a tenant ever does, the symptom is
    "the Revisions tab is missing on procurement pages", and the fix is enabling
    the forms module (or moving this endpoint).
    """
    return {"types": PortalRevisionService(db).enabled_type_map()}


@router.put(
    "/revision-configs/{source_entity_type}",
    response_model=PortalRevisionConfigResponse,
)
async def upsert_portal_revision_config(
    payload: PortalRevisionConfigUpdate,
    source_entity_type: str = Path(...),
    current_user: dict = Depends(require_permission("user_management.settings.edit")),
    db: Session = Depends(get_db),
):
    """Upsert one type's policy. Creates the row when the seed never ran.

    The settings edit slug, the one the Portal Revisions globals already save under
    (via `PUT /settings/general`): this checked only that the caller was signed in
    (SETTINGS-PERM-GATE)."""
    kind = _check_type(source_entity_type)
    row = (
        db.query(PortalRevisionConfig)
        .filter(PortalRevisionConfig.source_entity_type == kind)
        .first()
    )
    if row is None:
        row = PortalRevisionConfig(id=str(uuid.uuid4()), source_entity_type=kind)
        db.add(row)

    row.is_enabled = bool(payload.is_enabled)
    row.max_revisions = payload.max_revisions
    row.allowed_statuses = _clean_statuses(payload.allowed_statuses)
    row.restart_stage_code = (payload.restart_stage_code or "").strip() or None
    db.commit()
    db.refresh(row)
    return _serialize(row)
