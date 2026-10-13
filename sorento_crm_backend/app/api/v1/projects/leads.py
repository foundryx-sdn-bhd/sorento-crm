"""Lead API (S2c, UAC Group O; P1, UAC Group A).

Three actions get their own endpoints rather than riding the generic PUT, because each
does work the field alone cannot express:

- **qualify** runs the registration clash check and creates a project (AC-O4). It is
  the only place a lead touches the exclusivity lock.
- **disqualify** validates its reason against a lookup set, so the conversion report
  has buckets rather than free text (AC-O6).
- **status** refuses the two terminal rungs, which belong to the two actions above.

A blocked qualify returns 409 and leaves the lead OPEN: the recourse is join-or-dispute
on the incumbent project, and the lead is the user's record of why they were asking.

Three more join them for the handover handshake (D7): **assign**, **accept** and
**decline**. Same reasoning -- none of them is a field edit. Assignment starts a clock,
acceptance is the only thing that confers ownership, and a decline hands the lead back
to the pool with a reason on it. Every one of them tells the other person, after the
commit and best-effort: the handover is already recorded, and a notification backend
that is down must not 500 an assignment that happened.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.projects._common import acting_company_id, permission_slugs
from app.database import get_db
from app.dependencies import require_permission
from app.schemas.common import MAX_PAGE_LIMIT, ListResponse
from app.schemas.projects import (
    ClashPreviewResponse,
    CustomerPortfolioResponse,
    ProjectLeadAssignRequest,
    ProjectLeadAwaitingAcceptanceRow,
    ProjectLeadConversionMetrics,
    ProjectLeadCreate,
    ProjectLeadDeclineRequest,
    ProjectLeadDisqualifyRequest,
    ProjectLeadQualifyRequest,
    ProjectLeadReasonOption,
    ProjectLeadResponse,
    ProjectLeadStatusChangeRequest,
    ProjectLeadUpdate,
    ProjectResponse,
)
from app.services import project_lead_service as svc
from app.services import project_service as projects
from app.services.error_handler import handle_internal_error
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()

VIEW = "projects.projects.view"
# Creating and qualifying a lead take EDIT, the grant that registers a project (owner, 13 Oct):
# a new project needs its lead, so whoever may register one must be able to create the lead.
# They used to take "projects.projects.create", which no permission registry defines, so only
# admins passed.
EDIT = "projects.projects.edit"
# Putting a lead or a project in SOMEBODY ELSE'S name is a manager act, as on register
# (projects.py, project_owner_assign_forbidden). Kept explicit here because EDIT now reaches
# these routes for every salesperson (security review, 13 Oct).
MANAGE = "projects.projects.manage"
DELETE = "projects.projects.delete"


def _one(db: Session, lead, current_user: dict) -> dict:
    return svc.serialize_leads(
        db,
        [lead],
        actor_user_id=current_user["id"],
        permissions=permission_slugs(db, current_user["id"]),
    )[0]


# --------------------------------------------------------------------- read


@router.get("", response_model=ListResponse[ProjectLeadResponse])
async def list_leads(
    query: Optional[str] = Query(None, description="Matches the title or the lead code."),
    outcome: Optional[List[str]] = Query(None),
    status_id: Optional[List[str]] = Query(None),
    owner_user_id: Optional[List[str]] = Query(None),
    customer_id: Optional[List[str]] = Query(None),
    source: Optional[List[str]] = Query(None),
    acceptance_state: Optional[List[str]] = Query(
        None, description="assigned | accepted | declined (AC-A7)."
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    sort: str = Query("created_at"),
    dir: str = Query("desc", pattern="^(asc|desc)$"),
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Every lead in the company. Rows carry informational duplicate hints (AC-O3)."""
    try:
        result = svc.list_leads(
            db,
            company_id=acting_company_id(db),
            actor_user_id=current_user["id"],
            permissions=permission_slugs(db, current_user["id"]),
            query=query,
            outcome=outcome,
            status_id=status_id,
            owner_user_id=owner_user_id,
            customer_id=customer_id,
            source=source,
            acceptance_state=acceptance_state,
            page=page,
            limit=limit,
            sort=sort,
            dir=dir,
        )
        return {
            "data": result["data"],
            "pagination": {
                "total": result["total"],
                "page": result["page"],
                "limit": result["limit"],
            },
            "empty": result["total"] == 0,
        }
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/disqualify-reasons", response_model=List[ProjectLeadReasonOption])
async def list_disqualify_reasons(
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """The configured reasons (AC-O6). Empty means an admin has not set any up, and
    disqualify will refuse until they do."""
    try:
        return svc.disqualify_reasons(db)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/awaiting-acceptance",
    response_model=ListResponse[ProjectLeadAwaitingAcceptanceRow],
)
async def list_awaiting_acceptance(
    owner_user_id: Optional[List[str]] = Query(None),
    min_hours: float = Query(
        0, ge=0, description="Only leads that have been waiting at least this long."
    ),
    query: Optional[str] = Query(None, description="Matches the title or the lead code."),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """Marketing's worklist: which leads has nobody taken (AC-A7).

    Declared BEFORE /{lead_id} so "awaiting-acceptance" is not read as a lead id.

    Newest assignment first, each row carrying ``hours_since_assigned``. No `sort`
    parameter: the order IS the answer this screen exists to give, and letting the grid
    re-sort it by created_at would hide the handover that has been waiting since Tuesday
    behind fifty older leads.
    """
    try:
        result = svc.awaiting_acceptance(
            db,
            company_id=acting_company_id(db),
            actor_user_id=current_user["id"],
            permissions=permission_slugs(db, current_user["id"]),
            owner_user_id=owner_user_id,
            min_hours=min_hours,
            query=query,
            page=page,
            limit=limit,
        )
        return {
            "data": result["data"],
            "pagination": {
                "total": result["total"],
                "page": result["page"],
                "limit": result["limit"],
            },
            "empty": result["total"] == 0,
        }
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/metrics", response_model=ProjectLeadConversionMetrics)
async def lead_conversion_metrics(
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        return svc.conversion_metrics(db, company_id=acting_company_id(db))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/by-customer/{customer_id}/portfolio", response_model=CustomerPortfolioResponse
)
async def customer_portfolio(
    customer_id: str,
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """The account view (AC-O9): what this customer has told us about, and what it
    turned into.

    Declared BEFORE /{lead_id} so "by-customer" is not swallowed as a lead id.
    """
    try:
        validate_uuid_path(customer_id, resource="Customer")
        return svc.customer_portfolio(db, customer_id=customer_id)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/{lead_id}", response_model=ProjectLeadResponse)
async def get_lead(
    lead_id: str,
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(lead_id, resource="Lead")
        return _one(db, svc.get_lead(db, lead_id), current_user)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


# -------------------------------------------------------------------- write


@router.post("", response_model=ProjectLeadResponse, status_code=status.HTTP_201_CREATED)
async def create_lead(
    payload: ProjectLeadCreate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Record a sighting. No clash check, deliberately (AC-O3).

    The BUYER is optional (AC-A1): marketing works BCI and panel channels, where on day
    one nobody knows who the buyer is, because the trading house only exists once a
    contractor is awarded. Who told us goes on the informant fields instead, and is
    never written to ``customers``.

    ``customer_id`` or a ``new_customer`` block is accepted for the case where the buyer
    IS already known and not on file. Neither given means no buyer yet, which is the
    normal shape of a BCI lead and not an error.
    """
    try:
        company_id = acting_company_id(db)
        body = payload.model_dump(exclude_unset=True)
        # Handing a lead to someone else goes through assign (the acceptance clock, the
        # notification, the active-user check), never through create.
        owner = body.get("owner_user_id")
        if (
            owner
            and owner != current_user["id"]
            and MANAGE not in permission_slugs(db, current_user["id"])
        ):
            from app.services.error_handler import AppException

            raise AppException(
                status_code=403,
                message="Create the lead, then assign it: only a sales manager can create it in someone else's name.",
                code="lead_owner_assign_forbidden",
            )
        new_customer = body.pop("new_customer", None)
        if body.get("customer_id") or new_customer:
            customer = svc.select_or_create_customer(
                db,
                company_id=company_id,
                actor_user_id=current_user["id"],
                customer_id=body.get("customer_id"),
                new_customer=new_customer,
            )
            body["customer_id"] = customer.id
        lead = svc.create_lead(
            db,
            company_id=company_id,
            actor_user_id=current_user["id"],
            payload=body,
        )
        db.commit()
        db.refresh(lead)
        return _one(db, lead, current_user)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.put("/{lead_id}", response_model=ProjectLeadResponse)
async def update_lead(
    lead_id: str,
    payload: ProjectLeadUpdate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(lead_id, resource="Lead")
        lead = svc.get_lead(db, lead_id)
        svc.assert_can_edit_lead(
            lead, current_user["id"], permission_slugs(db, current_user["id"])
        )
        svc.update_lead(db, lead, payload.model_dump(exclude_unset=True))
        db.commit()
        db.refresh(lead)
        return _one(db, lead, current_user)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/{lead_id}/status", response_model=ProjectLeadResponse)
async def change_lead_status(
    lead_id: str,
    payload: ProjectLeadStatusChangeRequest,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Move a rung. Qualified and Disqualified are refused here (422) and pointed at
    their own actions, which do the work those rungs mean."""
    try:
        validate_uuid_path(lead_id, resource="Lead")
        lead = svc.get_lead(db, lead_id)
        svc.assert_can_edit_lead(
            lead, current_user["id"], permission_slugs(db, current_user["id"])
        )
        svc.change_lead_status(db, lead, payload.to_status_id)
        db.commit()
        db.refresh(lead)
        return _one(db, lead, current_user)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/{lead_id}/assign", response_model=ProjectLeadResponse)
async def assign_lead(
    lead_id: str,
    payload: ProjectLeadAssignRequest,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Hand the lead to a salesperson and start the acceptance clock (AC-A4).

    Re-assigning an already-assigned lead is allowed and resets the clock. The lead is
    NOT owned by the assignee until they accept.
    """
    try:
        validate_uuid_path(lead_id, resource="Lead")
        lead = svc.get_lead(db, lead_id)
        svc.assert_can_assign_lead(
            lead, current_user["id"], permission_slugs(db, current_user["id"])
        )
        svc.assign_lead(db, lead=lead, owner_user_id=payload.owner_user_id)
        db.commit()
        db.refresh(lead)
        # After the commit, and best-effort inside: the handover is recorded, and the
        # retry for a 500 raised here would assign a lead that is already assigned.
        svc.notify_lead_assigned(
            db, lead=lead, actor_user_id=current_user["id"], note=payload.note
        )
        return _one(db, lead, current_user)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/{lead_id}/accept", response_model=ProjectLeadResponse)
async def accept_lead(
    lead_id: str,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Take on a lead that was handed to you (AC-A5). 409 when nobody assigned it.

    Only the assignee, or a manager acting for them, may accept: the point of the
    handshake is that nothing is ever silently owned by somebody who never opened it.
    """
    try:
        validate_uuid_path(lead_id, resource="Lead")
        lead = svc.get_lead(db, lead_id)
        svc.accept_lead(
            db,
            lead=lead,
            actor_user_id=current_user["id"],
            permissions=permission_slugs(db, current_user["id"]),
        )
        db.commit()
        db.refresh(lead)
        return _one(db, lead, current_user)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/{lead_id}/decline", response_model=ProjectLeadResponse)
async def decline_lead(
    lead_id: str,
    payload: ProjectLeadDeclineRequest,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Refuse the handover with a reason (AC-A5). The lead returns to the pool.

    Whoever assigned it is told, so it does not die in either tray.
    """
    try:
        validate_uuid_path(lead_id, resource="Lead")
        lead = svc.get_lead(db, lead_id)
        svc.decline_lead(
            db,
            lead=lead,
            reason=payload.reason,
            actor_user_id=current_user["id"],
            permissions=permission_slugs(db, current_user["id"]),
        )
        db.commit()
        db.refresh(lead)
        svc.notify_lead_declined(db, lead=lead, actor_user_id=current_user["id"])
        return _one(db, lead, current_user)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/{lead_id}/qualify-preview", response_model=ClashPreviewResponse)
async def preview_qualify(
    lead_id: str,
    title: Optional[str] = Query(None, description="Defaults to the lead's title."),
    developer_party_id: Optional[str] = Query(None),
    current_user: dict = Depends(require_permission(VIEW)),
    db: Session = Depends(get_db),
):
    """What qualifying would hit, before the user commits (AC-O4).

    Same matcher and thresholds as registration, so the preview cannot disagree with
    the decision that follows it.
    """
    try:
        validate_uuid_path(lead_id, resource="Lead")
        lead = svc.get_lead(db, lead_id)
        preview = svc.preview_qualify_clashes(
            db,
            lead=lead,
            company_id=acting_company_id(db),
            title=title,
            developer_party_id=developer_party_id,
        )
        return {
            "candidates": projects.serialize_clash_candidates(
                db, preview["candidates"]
            ),
            "would_block": preview["would_block"],
        }
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/{lead_id}/qualify",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def qualify_lead(
    lead_id: str,
    payload: ProjectLeadQualifyRequest,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Convert to a project. 409 when somebody already holds the development.

    Requires ``.edit``: this is a registration, and it is the same grant that registering
    directly (``POST /projects``) needs. The per-lead edit check below still applies.
    """
    try:
        validate_uuid_path(lead_id, resource="Lead")
        lead = svc.get_lead(db, lead_id)
        permissions = permission_slugs(db, current_user["id"])
        svc.assert_can_edit_lead(lead, current_user["id"], permissions)
        # The register route's rule: a project lands in someone else's name only by a
        # manager. The lead's own owner is not "someone else" here, it is the default.
        owner = payload.owner_user_id
        if (
            owner
            and owner not in (current_user["id"], lead.owner_user_id)
            and MANAGE not in permissions
        ):
            from app.services.error_handler import AppException

            raise AppException(
                status_code=403,
                message="Only a sales manager can register a project for someone else.",
                code="project_owner_assign_forbidden",
            )
        project = svc.qualify_lead(
            db,
            lead=lead,
            actor_user_id=current_user["id"],
            company_id=acting_company_id(db),
            project_payload=payload.model_dump(exclude_unset=True),
        )
        db.commit()
        db.refresh(project)
        return projects.serialize_projects(
            db,
            [project],
            actor_user_id=current_user["id"],
            permissions=permission_slugs(db, current_user["id"]),
        )[0]
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/{lead_id}/disqualify", response_model=ProjectLeadResponse)
async def disqualify_lead(
    lead_id: str,
    payload: ProjectLeadDisqualifyRequest,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(lead_id, resource="Lead")
        lead = svc.get_lead(db, lead_id)
        svc.assert_can_edit_lead(
            lead, current_user["id"], permission_slugs(db, current_user["id"])
        )
        svc.disqualify_lead(db, lead=lead, reason=payload.reason)
        db.commit()
        db.refresh(lead)
        return _one(db, lead, current_user)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/{lead_id}/reopen", response_model=ProjectLeadResponse)
async def reopen_lead(
    lead_id: str,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Undo a disqualification. Refused on a qualified lead, which has a project
    behind it that reopening would orphan."""
    try:
        validate_uuid_path(lead_id, resource="Lead")
        lead = svc.get_lead(db, lead_id)
        svc.assert_can_edit_lead(
            lead, current_user["id"], permission_slugs(db, current_user["id"])
        )
        svc.reopen_lead(db, lead)
        db.commit()
        db.refresh(lead)
        return _one(db, lead, current_user)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.delete("/{lead_id}")
async def delete_lead(
    lead_id: str,
    current_user: dict = Depends(require_permission(DELETE)),
    db: Session = Depends(get_db),
):
    """Hard delete, per the CRUD standard. Any project qualified out of it survives:
    ``projects.lead_id`` is ON DELETE SET NULL."""
    try:
        validate_uuid_path(lead_id, resource="Lead")
        lead = svc.get_lead(db, lead_id)
        svc.assert_can_edit_lead(
            lead, current_user["id"], permission_slugs(db, current_user["id"])
        )
        svc.delete_lead(db, lead)
        db.commit()
        return {"success": True}
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))
