"""Audit logs API routes."""
from datetime import datetime
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session
from typing import Optional
from app.database import get_db
from app.dependencies import get_current_user_or_api_key
from app.services.audit_actor_label import actor_label as _actor_label
from app.services.audit_service import _is_uuid, list_audit_logs
from app.schemas.audit import AuditLogResponse
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT
from app.models.user import User
from app.models.access import RespondContact
from app.services.user_service import UserPermissionService
from app.services.error_handler import AppException
from app.models.base import get_company_scope

router = APIRouter()

# The audit log holds every audited change, its before/after values and the actor's
# IP address, so reading it is a superadmin/admin act (#1281): the menu entries were
# superadmin-only, the routes were open to any login or API key. The one exception is
# a detail page's history panel, which reads ONE record's history and takes the
# same view permission as the record's own page. Every FE reader of
# `getAuditLogs({ entity_type, entity_id })` needs its entity type here.
_PER_RECORD_VIEW_PERMISSION: dict[str, str] = {
    "complaint": "complaint_management.complaints.view",
    "stock_inquiry": "procurement.stock_inquiries.view",
    "purchase_request": "procurement.purchase_requests.view",
    "product": "master_data.products.view",
    "project_sample_requests": "projects.sample_requests.view",
    # Packing List detail, Timeline tab (usePackingLists.ts, R17).
    "inbound_shipments": "procurement.packing_lists.view",
    # Product Specifications record page, History tab.
    "product_spec_registry": "master_data.spec_registry.view",
}


def _is_audit_admin(db: Session, user_id: str) -> bool:
    slugs = UserPermissionService(db).get_user_role_slugs(user_id)
    return bool(slugs & {UserPermissionService.SUPERADMIN_ROLE_SLUG, "admin"})


def _forbidden() -> AppException:
    return AppException(
        status_code=status.HTTP_403_FORBIDDEN,
        message="Superadmin access is required to read the audit log.",
        code="audit_admin_required",
    )


def require_audit_admin(
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
) -> dict:
    """Superadmin/admin only, whether the caller is a login or an API key's act-as user."""
    if not _is_audit_admin(db, str(current_user["id"])):
        raise _forbidden()
    return current_user


def _authorize_log_read(
    db: Session, current_user: dict, entity_type: Optional[str], entity_id: Optional[str]
) -> None:
    user_id = str(current_user["id"])
    if _is_audit_admin(db, user_id):
        return
    slug = _PER_RECORD_VIEW_PERMISSION.get(entity_type or "")
    if not (slug and entity_id and UserPermissionService(db).check_user_has_permission(user_id, slug)):
        raise _forbidden()
    # The only company check downstream is admin_listing_company_filter, which is not
    # fail-closed: UNSET or an empty set means "every company", safe only for an admin.
    # A non-admin with no active company (no grant left after offboarding, a deleted
    # company) must not read another company's record through this branch. None stays
    # open: it is the deliberate all-companies principal (an X-API-Key, AC-F1).
    scope = get_company_scope(db)
    if scope is not None and (not isinstance(scope, frozenset) or not scope):
        raise AppException(
            status_code=status.HTTP_403_FORBIDDEN,
            message="An active company is required to read this record's history.",
            code="audit_company_required",
        )


def _user_display_names(db: Session, user_ids: list[str]) -> dict[str, str]:
    """Return map of user_id -> name (or email fallback) for given ids."""
    ids = [uid for uid in user_ids if uid]
    if not ids:
        return {}
    users = db.query(User.id, User.name, User.email).filter(User.id.in_(ids)).all()
    # Name, else email, else nothing: never the id (a phone-only user has no email).
    return {
        str(u.id): (u.name.strip() if u.name and u.name.strip() else (u.email or ""))
        for u in users
    }


def _contact_display_names(db: Session, contact_ids: list[str]) -> dict[str, str]:
    """Return map of respond_contacts.id -> display name (name, else first+last, else phone)."""
    ids = [cid for cid in contact_ids if cid]
    if not ids:
        return {}
    rows = db.query(
        RespondContact.id, RespondContact.name,
        RespondContact.first_name, RespondContact.last_name,
        RespondContact.phone_number,
    ).filter(RespondContact.id.in_(ids)).all()
    out: dict[str, str] = {}
    for r in rows:
        name = (r.name or "").strip()
        if not name:
            name = " ".join(p for p in ((r.first_name or "").strip(), (r.last_name or "").strip()) if p).strip()
        out[str(r.id)] = name or (r.phone_number or str(r.id))
    return out


def _scheduled_task_names(db: Session, keys: list[str]) -> dict[str, str]:
    keys = [k for k in keys if k]
    if not keys:
        return {}
    from app.models.scheduled_task import ScheduledTask

    rows = db.query(ScheduledTask.key, ScheduledTask.name).filter(ScheduledTask.key.in_(keys)).all()
    return {r.key: r.name for r in rows if r.name}


def _integration_names(db: Session, integration_ids: list[str]) -> dict[str, str]:
    ids = [i for i in integration_ids if i]
    if not ids:
        return {}
    from app.models.integration import Integration

    rows = db.query(Integration.id, Integration.name).filter(Integration.id.in_(ids)).all()
    return {str(r.id): r.name for r in rows}


# Human-readable one-liner for a status transition, e.g. "status: pending → approved".
# Derived at serialize time from the JSONB diff - no schema/storage change.
def _status_labels(db: Session, items) -> dict[str, str]:
    """Labels for every status-engine `status_id` an UPDATE row moved between, in one query,
    so the trail reads "Status: Draft -> Submitted" instead of two raw ids."""
    ids = set()
    for it in items:
        for values in (it.old_values or {}, it.new_values or {}):
            value = values.get("status_id")
            # Only real ids reach the query: one legacy or hand-written value must not turn
            # the whole trail into a 500.
            if value and _is_uuid(str(value)):
                ids.add(str(value))
    if not ids:
        return {}
    from app.models.status import Status

    rows = db.query(Status.id, Status.label).filter(Status.id.in_(list(ids))).all()
    return {str(r.id): r.label for r in rows}


def _derive_description(it, status_labels: Optional[dict[str, str]] = None) -> Optional[str]:
    if it.description:
        return it.description
    old = it.old_values or {}
    new = it.new_values or {}
    if status_labels and new.get("status_id") and new.get("status_id") != old.get("status_id"):
        to = status_labels.get(str(new.get("status_id")))
        if to:
            if not old.get("status_id"):
                return f"Status: ∅ → {to}"
            frm = status_labels.get(str(old.get("status_id")))
            # An old status that no longer exists is not "no status".
            return f"Status: {frm} → {to}" if frm else f"Status changed to {to}"
    # Attachment upload activity: surface the filename + what happened.
    if it.entity_type == "attachment":
        fname = new.get("original_filename") or old.get("original_filename")
        if it.action in ("CREATE", "INSERT"):
            return f"Uploaded {fname}" if fname else "Uploaded a file"
        if it.action == "DELETE":
            return f"Deleted {fname}" if fname else "Deleted a file"
        # UPDATE: prefer a rename, else a description edit, else generic.
        if new.get("stored_filename") and new.get("stored_filename") != old.get("stored_filename"):
            return f"Renamed to {new.get('stored_filename')}"
        if new.get("description") != old.get("description"):
            return f"Edited file details{f' - {fname}' if fname else ''}"
        return f"Updated {fname}" if fname else "Updated a file"
    for key in ("status", "approval_status", "order_status"):
        if key in new and new.get(key) != old.get(key):
            frm = old.get(key)
            to = new.get(key)
            return f"{key}: {frm if frm is not None else '∅'} → {to}"
    return None


@router.get("/", response_model=ListResponse[AuditLogResponse])
async def get_audit_logs(
    entity_type: Optional[str] = Query(None, description="Filter by entity type (e.g. complaint, stock_inquiry, purchase_request)"),
    entity_id: Optional[str] = Query(None, description="Filter by entity id"),
    user_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None, description="INSERT, UPDATE, or DELETE"),
    trace_id: Optional[str] = Query(None, description="Filter by request correlation id (groups a multi-row change)"),
    changed_from: Optional[datetime] = Query(None, description="Filter changed_at >= (ISO); e.g. a day bar drill-down"),
    changed_to: Optional[datetime] = Query(None, description="Filter changed_at <= (ISO)"),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """List audit log entries, optionally filtered by entity_type and entity_id (for per-record history)."""
    _authorize_log_read(db, current_user, entity_type, entity_id)
    items, total = list_audit_logs(
        db,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
        action=action,
        trace_id=trace_id,
        changed_from=changed_from,
        changed_to=changed_to,
        page=page,
        limit=limit,
    )
    user_ids = list(
        {str(it.user_id) for it in items if it.user_id is not None}
        | {str(it.real_user_id) for it in items if getattr(it, "real_user_id", None) is not None}
    )
    contact_ids = list({str(it.contact_id) for it in items if getattr(it, "contact_id", None) is not None})
    integration_ids = list(
        {str(it.integration_id) for it in items if getattr(it, "integration_id", None) is not None}
    )
    scheduled_keys = list(
        {it.job_id for it in items if getattr(it, "actor_type", None) == "scheduler" and getattr(it, "job_id", None)}
    )
    user_names = _user_display_names(db, user_ids)
    contact_names = _contact_display_names(db, contact_ids)
    integration_names = _integration_names(db, integration_ids)
    scheduled_names = _scheduled_task_names(db, scheduled_keys)
    from app.services.activity_service import entity_labels

    record_labels = entity_labels(db, items)
    status_labels = _status_labels(db, items)
    data = []
    for it in items:
        payload = AuditLogResponse.model_validate(it).model_dump()
        # Attribution precedence: acting contact -> staff user -> "System". Contact
        # first only on a contact or legacy row: a staff row also carries the user's
        # linked WhatsApp contact (plan 8.1), and there the user is the name.
        contact_first = (getattr(it, "actor_type", None) or "legacy") in ("contact", "legacy")
        if contact_first and getattr(it, "contact_id", None) is not None and contact_names.get(str(it.contact_id)):
            payload["user_display_name"] = contact_names[str(it.contact_id)]
        elif it.user_id is not None:
            payload["user_display_name"] = user_names.get(str(it.user_id)) or "System"
        else:
            payload["user_display_name"] = "System"
        payload["actor_label"] = _actor_label(
            it, user_names, contact_names, integration_names, payload["user_display_name"],
            scheduled_names=scheduled_names,
        )
        payload["description"] = _derive_description(it, status_labels)
        payload["entity_label"] = record_labels.get(str(it.id))
        data.append(AuditLogResponse(**payload))
    return {
        "data": data,
        "pagination": {"total": total, "page": page, "limit": limit},
        "empty": total == 0,
    }
