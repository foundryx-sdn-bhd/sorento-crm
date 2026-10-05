"""Respond.io contacts: who we are allowed to message.

Backs System Management -> Respond.io Contacts, the screen for
`respond_contacts.outbound_enabled`. Until now the switch could only be flipped
from `scripts/set_contact_outbound.py`, which meant nobody could SEE who was
silenced without shell access.

Every write delegates to `app.services.respond_outbound_service` - the same
functions the CLI calls - so the screen and the script can never disagree.
Permissions ride the sibling Respond.io admin slugs (`system.respond_workspaces.*`):
reading needs `.view`, and every write needs `.edit`, so a read-only admin can
look but cannot silence a customer.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission
from app.models.access import RespondContact
from app.schemas.common import MAX_PAGE_LIMIT
from app.schemas.respond_contact_outbound import (
    RespondContactOutboundBulkRequest,
    RespondContactOutboundBulkResponse,
    RespondContactOutboundListResponse,
    RespondContactOutboundRow,
    RespondContactOutboundUpdate,
)
from app.services.error_handler import AppException
from app.services.respond_outbound_service import (
    find_contact,
    set_all_outbound,
    set_contact_outbound,
    status_counts,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/respond-contacts", tags=["respond-contacts"])

VIEW_PERMISSION = "system.respond_workspaces.view"
EDIT_PERMISSION = "system.respond_workspaces.edit"


@router.get("", response_model=RespondContactOutboundListResponse)
def list_respond_contacts(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None, description="Name, phone or Respond.io id"),
    outbound: Optional[str] = Query(
        None, description="enabled | disabled (omit for all)"
    ),
    ai: Optional[str] = Query(None, description="allowed | blocked (omit for all)"),
    _user: dict = Depends(require_permission(VIEW_PERMISSION)),
    db: Session = Depends(get_db),
):
    """The contacts, plus the whole-table enabled/disabled counts.

    The counts are NOT filtered by `query`/`outbound`: the line above the grid
    answers "how much of the customer base is silenced right now", which a
    count of the current page cannot.
    """
    q = db.query(RespondContact)

    term = (query or "").strip()
    if term:
        like = f"%{term}%"
        q = q.filter(
            or_(
                RespondContact.name.ilike(like),
                RespondContact.phone_number.ilike(like),
                RespondContact.respond_io_id.ilike(like),
            )
        )

    wanted = (outbound or "").strip().lower()
    if wanted == "enabled":
        q = q.filter(RespondContact.outbound_enabled.is_(True))
    elif wanted == "disabled":
        q = q.filter(RespondContact.outbound_enabled.is_(False))
    elif wanted:
        raise AppException(
            status_code=status.HTTP_400_BAD_REQUEST,
            message="outbound must be 'enabled' or 'disabled'.",
            code="INVALID_OUTBOUND_FILTER",
        )

    wanted_ai = (ai or "").strip().lower()
    if wanted_ai == "allowed":
        q = q.filter(RespondContact.chatbot_allowed.is_(True))
    elif wanted_ai == "blocked":
        q = q.filter(RespondContact.chatbot_allowed.is_(False))
    elif wanted_ai:
        raise AppException(
            status_code=status.HTTP_400_BAD_REQUEST,
            message="ai must be 'allowed' or 'blocked'.",
            code="INVALID_AI_FILTER",
        )

    total = q.count()
    rows = (
        # A NULL name must not sink to the bottom of a name sort, so order by the
        # switch first (the silenced are what an admin came here to find), then name.
        q.order_by(
            RespondContact.outbound_enabled.asc(),
            func.lower(func.coalesce(RespondContact.name, RespondContact.phone_number)),
        )
        .offset((page - 1) * limit)
        .limit(limit)
        .all()
    )

    return {
        "data": [RespondContactOutboundRow.model_validate(r) for r in rows],
        "pagination": {"total": total, "page": page, "limit": limit},
        "empty": total == 0,
        "counts": status_counts(db),
    }


@router.post("/{contact_id}/outbound", response_model=RespondContactOutboundRow)
def set_one_contact_outbound(
    contact_id: str,
    payload: RespondContactOutboundUpdate,
    _user: dict = Depends(require_permission(EDIT_PERMISSION)),
    db: Session = Depends(get_db),
):
    """Switch one contact on or off. Setting the value it already holds is a no-op, not an error."""
    contact, _changed = set_contact_outbound(db, contact_id, enabled=payload.enabled)
    return RespondContactOutboundRow.model_validate(contact)


@router.post("/outbound/bulk", response_model=RespondContactOutboundBulkResponse)
def set_bulk_outbound(
    payload: RespondContactOutboundBulkRequest,
    _user: dict = Depends(require_permission(EDIT_PERMISSION)),
    db: Session = Depends(get_db),
):
    """Switch a selection, or every contact (`all: true` - the production kill switch).

    The selection path is all-or-nothing: every id is resolved BEFORE anything is
    written, so an unknown id 404s with no contact half-silenced.
    """
    if payload.all:
        total = db.query(RespondContact).count()
        changed = set_all_outbound(db, enabled=payload.enabled)
        logger.warning(
            "Outbound messaging switched %s for ALL %s Respond contacts (%s changed).",
            "ON" if payload.enabled else "OFF",
            total,
            changed,
        )
        return {"requested": total, "changed": changed, "counts": status_counts(db)}

    # Preserve the caller's order while collapsing duplicates.
    ids = list(dict.fromkeys(payload.contact_ids or []))
    missing = [cid for cid in ids if find_contact(db, cid) is None]
    if missing:
        raise AppException(
            status_code=status.HTTP_404_NOT_FOUND,
            message=f"{len(missing)} of {len(ids)} contacts were not found.",
            detail="Nothing was changed. Refresh the list and try again.",
            code="CONTACT_NOT_FOUND",
        )

    changed = 0
    for cid in ids:
        _contact, did_change = set_contact_outbound(db, cid, enabled=payload.enabled)
        changed += 1 if did_change else 0

    return {"requested": len(ids), "changed": changed, "counts": status_counts(db)}
