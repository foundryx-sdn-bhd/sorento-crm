"""Purchase requests / sponsorship forms API routes."""
from fastapi import APIRouter, Depends, Query, HTTPException, status, Request, Body
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from app.database import get_db
from app.services.uuid_path_param import validate_uuid_path
from app.dependencies import (
    get_current_user,
    get_current_user_or_api_key,
    require_permission,
    require_permission_with_api_key,
)
from app.services.procurement_service import PurchaseRequestService
from app.schemas.procurement import (
    PurchaseRequestHeaderCreate,
    PurchaseRequestHeaderUpdate,
    PurchaseRequestUpdateAndReply,
    PurchaseRequestHeaderResponse,
    PurchaseRequestHeaderListResponse,
    PurchaseRequestAttachmentLinkRequest,
    SendApprovalLinkRequest,
    SendApprovalLinkResponse,
    RejectSubmittedRequest,
    ViewLinkRequest,
    ViewLinkResponse,
    BulkDeletePurchaseRequestsRequest,
)
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT, FormVoidRequest
from app.schemas.download import PdfExportOptions
from app.services.error_handler import AppException, handle_internal_error
from app.services.handling_lock_service import assert_can_act_on_form
from app.services.revision_fence import require_current_revision
from app.services.document_number import display_document_number
from app.config import settings
from app.modules.runtime.guards import require_public_view_links_enabled

router = APIRouter()


def _respond_user_id_from_current_user(current_user: dict) -> str:
    """Get respond_user_id for update-and-reply; fallback to user id."""
    rid = (current_user or {}).get("respond_user_id") or (current_user or {}).get("respondUserId")
    if rid and str(rid).strip():
        return str(rid).strip()
    uid = (current_user or {}).get("id")
    if uid and str(uid).strip():
        return str(uid).strip()
    raise HTTPException(status_code=400, detail="User respond_user_id or id is required for Update & Reply.")


@router.get("/", response_model=ListResponse[PurchaseRequestHeaderListResponse])
async def get_purchase_requests(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    request_type: Optional[str] = Query(None, description="purchase_request or sponsorship_form"),
    approval_status: Optional[str] = Query(None, description="draft, pending, approved, rejected"),
    assigned_to: Optional[str] = Query(None, description="users.id of the latest unresolved SLA assignee, or __unassigned__"),
    sort: Optional[str] = Query("submitted_at"),
    dir: Optional[str] = Query("desc"),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """List purchase requests and sponsorship forms with pagination."""
    try:
        service = PurchaseRequestService(db)
        result = service.list_requests(
            page=page,
            limit=limit,
            query=query,
            contact_id=None,
            space_id=None,
            request_type=request_type,
            approval_status=approval_status,
            assigned_to=assigned_to,
            sort_field=sort or "submitted_at",
            sort_dir=dir or "desc",
        )
        return result
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/by-number/{number}", response_model=PurchaseRequestHeaderResponse)
async def get_purchase_request_by_number(
    number: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Get a purchase request by its request number, with lines and attachments (read-only).
    A number carried by several requests answers the newest."""
    import re

    from sqlalchemy import func

    from app.models.procurement import PurchaseRequestHeader

    folded = re.sub(r"[-\s]+", "", number).lower()
    row = (
        db.query(PurchaseRequestHeader.id)
        .filter(func.lower(func.regexp_replace(PurchaseRequestHeader.request_number, r"[-\s]+", "", "g")) == folded)
        .order_by(PurchaseRequestHeader.created_at.desc())
        .first()
    )
    if row is None:
        raise AppException(status_code=404, message="Purchase request not found")
    return await get_purchase_request(str(row.id), current_user=current_user, db=db)


@router.get("/lookups/customers")
async def lookup_customers_for_request(
    q: str = Query(""),
    limit: int = Query(20, ge=1, le=MAX_PAGE_LIMIT),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Customer picker for the PR / SF form (same item shape as the portal lookup)."""
    from app.services.debtor_type import lookup_customers

    return lookup_customers(db, q, limit)


@router.get("/{request_id}", response_model=PurchaseRequestHeaderResponse)
async def get_purchase_request(
    request_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Get a purchase request or sponsorship form by ID with lines and attachments."""
    try:
        validate_uuid_path(request_id, resource="Request")
        from app.models.user import User

        service = PurchaseRequestService(db)
        header = service.get_request(
            request_id,
            contact_id=None,
            space_id=None,
        )
        if getattr(header, "approver_user_id", None):
            user = db.query(User).filter(User.id == header.approver_user_id).first()
            if user:
                setattr(
                    header,
                    "approver_display_name",
                    (user.name and user.name.strip()) or user.email or "",
                )
        # Void banner (BAN-1): resolve voided_by -> display name; wa phone null
        # (no form-banner-person-links resolver on this branch).
        setattr(
            header,
            "voided_by_name",
            service._resolve_actor_display_name(getattr(header, "voided_by", None)) or None
            if getattr(header, "voided_by", None)
            else None,
        )
        setattr(header, "voided_by_wa_phone", None)
        links = service.entity_attachment_service.list_links("purchase_request", request_id)
        setattr(
            header,
            "attachments",
            [
                service.entity_attachment_service.serialize_link(
                    link,
                    entity_key="purchase_request_id",
                    link_type="purchase_request_attachment",
                )
                for link in links
            ],
        )
        if getattr(header, "request_type", None) == "sponsorship_form" and header.lines:
            from decimal import Decimal
            grand = Decimal("0")
            for line in header.lines:
                line_total = getattr(line, "total", None)
                if line_total is not None:
                    grand += line_total
                else:
                    qty = getattr(line, "quantity", None)
                    up = getattr(line, "unit_price", None)
                    if qty is not None and up is not None:
                        grand += Decimal(str(qty)) * Decimal(str(up))
            setattr(header, "grand_total", grand)
        service.attach_rejection_person(header)
        return header
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{request_id}/revisions")
async def get_purchase_request_revisions(
    request_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Revision lineage for the office Revisions tab (UAC H2/H3).

    Serves BOTH purchase requests and sponsorship forms: they share this router and
    this table, and the revision rows are keyed on the header's own ``request_type``,
    so the caller never has to say which it is.
    """
    try:
        validate_uuid_path(request_id, resource="Request")
        from app.services.portal_revision_service import PortalRevisionService

        header = PurchaseRequestService(db).get_request(
            request_id, contact_id=None, space_id=None
        )
        entity_type = str(getattr(header, "request_type", "") or "purchase_request")
        return {
            "items": PortalRevisionService(db).list_revisions(entity_type, request_id)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{request_id}/conversation")
async def get_purchase_request_conversation(
    request_id: str,
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    cursor: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Get Respond.io conversation messages for this purchase request or sponsorship form."""
    try:
        validate_uuid_path(request_id, resource="Request")
        from app.services.integration_service import RespondClient
        from app.models.access import RespondContact
        service = PurchaseRequestService(db)
        header = service.get_request(request_id)
        identifier = service._identifier_from_respond_inbox_url(getattr(header, "respond_inbox_url", None))
        contact_meta: Optional[dict] = None
        contact_id_val = getattr(header, "contact_id", None)
        if contact_id_val:
            row = (
                db.query(RespondContact)
                .filter(
                    (RespondContact.id == contact_id_val)
                    | (RespondContact.respond_io_id == contact_id_val)
                )
                .first()
            )
            if row is not None:
                contact_meta = {"name": row.name, "phone": row.phone_number}
        if not identifier:
            return {
                "items": [],
                "pagination": {},
                "error": "No Respond.io contact linked",
                "contact": contact_meta,
            }
        client = RespondClient()
        data = client.list_messages(identifier, limit=limit, cursor=cursor)
        if isinstance(data, dict):
            data["contact"] = contact_meta
        return data
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=PurchaseRequestHeaderResponse, status_code=status.HTTP_201_CREATED)
async def create_purchase_request(
    data: PurchaseRequestHeaderCreate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a purchase request or sponsorship form."""
    try:
        service = PurchaseRequestService(db)
        header = service.create_request(data)
        db.commit()
        return header
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put(
    "/{request_id}",
    response_model=PurchaseRequestHeaderResponse,
    dependencies=[Depends(require_current_revision("purchase_request", "request_id"))],
)
async def update_purchase_request(
    request_id: str,
    data: PurchaseRequestHeaderUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a purchase request or sponsorship form."""
    try:
        validate_uuid_path(request_id, resource="Request")
        service = PurchaseRequestService(db)
        header = service.update_request(request_id, data)
        db.commit()
        return header
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{request_id}/update-and-reply",
    response_model=PurchaseRequestHeaderResponse,
    dependencies=[Depends(require_current_revision("purchase_request", "request_id"))],
)
async def update_purchase_request_and_reply(
    request_id: str,
    data: PurchaseRequestUpdateAndReply,
    request: Request,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update purchase request (e.g. form number) and send a reply to the conversation via Respond.io.
    Either send reply_message or set request_number to auto-send 'Your request has been assigned form number: X'."""
    try:
        validate_uuid_path(request_id, resource="Request")
        assert_can_act_on_form(db, request_id, current_user)
        respond_user_id = _respond_user_id_from_current_user(current_user)
        service = PurchaseRequestService(db)
        header = service.update_request_and_reply(
            request_id,
            data,
            respond_user_id=respond_user_id,
            request_url=str(request.url) if request else "",
            crm_sender_user_id=current_user.get("id"),
        )
        db.commit()
        if getattr(header, "approver_user_id", None):
            from app.models.user import User
            user = db.query(User).filter(User.id == header.approver_user_id).first()
            if user:
                setattr(
                    header,
                    "approver_display_name",
                    (user.name and user.name.strip()) or user.email or "",
                )
        return header
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


def _dispatch_form_action(
    db,
    current_user: dict,
    request: Request,
    *,
    request_id: str,
    action_key: str,
    payload: dict,
    event_name: str,
):
    """Route a PR/SF action through the form-action dispatcher.

    With no grace configured (the shipped default) this runs the wrapped service method
    exactly as before and returns its result. With a grace window AND a browser session,
    it parks the action and the caller gets a 202 so the UI can offer an Undo before
    anything is written or sent. See PLAN-form-sla-undo.md.
    """
    from app.services.form_action_dispatch import dispatch_or_defer

    service = PurchaseRequestService(db)
    header = service.get_request(request_id)
    entity_type = getattr(header, "request_type", None) or "purchase_request"
    return dispatch_or_defer(
        db,
        current_user,
        request,
        action_key=action_key,
        entity_type=entity_type,
        entity_id=request_id,
        payload={"request_id": request_id, **payload},
        event_name=event_name,
    )


@router.post(
    "/{request_id}/set-pending-approval",
    response_model=PurchaseRequestHeaderResponse,
    dependencies=[Depends(require_current_revision("purchase_request", "request_id"))],
)
async def set_pending_approval(
    request_id: str,
    request: Request,
    current_user: dict = Depends(require_permission_with_api_key("procurement.purchase_requests.send_for_approval")),
    db: Session = Depends(get_db),
):
    """Set request to pending approval (e.g. from draft or to resend after approved). Clears previous approval data.

    Triage action, so it requires `send_for_approval` - the same permission as
    Reject-at-submitted. It was previously open to any authenticated user, which meant
    anyone who could view a request could push it into the approval queue."""
    import logging
    logger = logging.getLogger(__name__)
    assert_can_act_on_form(db, request_id, current_user)
    try:
        validate_uuid_path(request_id, resource="Request")
        service = PurchaseRequestService(db)
        header = _dispatch_form_action(
            db,
            current_user,
            request,
            request_id=request_id,
            action_key="pr.send_for_approval",
            payload={"actor_user_id": current_user.get("id")},
            event_name="send_for_approval",
        )
        if isinstance(header, JSONResponse):
            return header
        if getattr(header, "approver_user_id", None):
            from app.models.user import User
            user = db.query(User).filter(User.id == header.approver_user_id).first()
            if user:
                setattr(
                    header,
                    "approver_display_name",
                    (user.name and user.name.strip()) or user.email or "",
                )
        return header
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in set_pending_approval for {request_id}: {type(e).__name__}: {str(e)}", exc_info=True)
        raise handle_internal_error(str(e))


@router.post(
    "/{request_id}/reject-submitted",
    response_model=PurchaseRequestHeaderResponse,
    dependencies=[Depends(require_current_revision("purchase_request", "request_id"))],
)
async def reject_submitted_purchase_request(
    request_id: str,
    body: RejectSubmittedRequest,
    request: Request,
    current_user: dict = Depends(require_permission_with_api_key("procurement.purchase_requests.send_for_approval")),
    db: Session = Depends(get_db),
):
    """Reject a submitted PR / sponsorship form before sending for approval. Sends a Respond.io update message to the contact with the rejection reason. Same permission as Send for Approval."""
    assert_can_act_on_form(db, request_id, current_user)
    try:
        validate_uuid_path(request_id, resource="Request")
        service = PurchaseRequestService(db)
        header = _dispatch_form_action(
            db,
            current_user,
            request,
            request_id=request_id,
            action_key="pr.reject_submitted",
            payload={
                "rejection_reason": body.rejection_reason,
                "actor_user_id": current_user.get("id"),
            },
            event_name="reject_submitted",
        )
        if isinstance(header, JSONResponse):
            return header
        if getattr(header, "approver_user_id", None):
            from app.models.user import User
            user = db.query(User).filter(User.id == header.approver_user_id).first()
            if user:
                setattr(
                    header,
                    "approver_display_name",
                    (user.name and user.name.strip()) or user.email or "",
                )
        service.attach_rejection_person(header)
        return header
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


class ApprovalDecisionRequest(BaseModel):
    action: str  # "approved" | "rejected"
    comments: Optional[str] = None


@router.post(
    "/{request_id}/approval-decision",
    response_model=PurchaseRequestHeaderResponse,
    dependencies=[Depends(require_current_revision("purchase_request", "request_id"))],
)
async def decide_purchase_request_approval(
    request_id: str,
    body: ApprovalDecisionRequest,
    request: Request,
    current_user: dict = Depends(require_permission_with_api_key("procurement.purchase_requests.approve")),
    db: Session = Depends(get_db),
):
    """Approve or reject a PR / sponsorship form IN-SYSTEM (the form's Approve/Reject
    buttons), as an alternative to the emailed approval link. Behaves identically to
    the public approval submit: same status transition, notifications, form-SLA event,
    and approval automation. Requires the request to be pending approval."""
    assert_can_act_on_form(db, request_id, current_user)
    try:
        validate_uuid_path(request_id, resource="Request")
        from app.models.user import User

        service = PurchaseRequestService(db)
        approver_name = None
        uid = current_user.get("id")
        if uid:
            u = db.query(User).filter(User.id == uid).first()
            if u:
                approver_name = (u.name and u.name.strip()) or u.email or None
        # Route the decision through the shared dispatcher glue - the same helper the
        # other four PR/SF endpoints use. This was the one endpoint that hand-inlined
        # the 202 contract, and it had already drifted from the shared copy.
        event_name = "approved" if body.action == "approved" else "approval_rejected"
        outcome = _dispatch_form_action(
            db,
            current_user,
            request,
            request_id=request_id,
            action_key="pr.approval_decision",
            payload={
                "action": body.action,
                "approved_by": approver_name,
                "approval_comments": body.comments,
                "actor_user_id": uid,
            },
            event_name=event_name,
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        header = outcome
        if getattr(header, "approver_user_id", None):
            user = db.query(User).filter(User.id == header.approver_user_id).first()
            if user:
                setattr(
                    header,
                    "approver_display_name",
                    (user.name and user.name.strip()) or user.email or "",
                )
        service.attach_rejection_person(header)
        return header
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


class CsFinalizeRequest(BaseModel):
    note: Optional[str] = None


@router.post(
    "/{request_id}/process",
    response_model=PurchaseRequestHeaderResponse,
    dependencies=[Depends(require_current_revision("purchase_request", "request_id"))],
)
async def process_request_by_cs(
    request_id: str,
    payload: CsFinalizeRequest,
    request: Request,
    current_user: dict = Depends(require_permission("procurement.purchase_requests.process")),
    db: Session = Depends(get_db),
):
    """Mark an approved purchase request / sponsorship form as processed by customer service.

    Sets status='processed_by_cs', closes the customer-service form-SLA stage, and
    sends a status-update message (+ optional note) to the contact via Respond.io.
    """
    assert_can_act_on_form(db, request_id, current_user)
    try:
        validate_uuid_path(request_id, resource="Request")
        respond_user_id = _respond_user_id_from_current_user(current_user)
        service = PurchaseRequestService(db)
        header = _dispatch_form_action(
            db,
            current_user,
            request,
            request_id=request_id,
            action_key="pr.finalize",
            payload={
                "new_status": "processed_by_cs",
                "note": payload.note,
                "respond_user_id": respond_user_id,
                "crm_sender_user_id": current_user.get("id"),
            },
            event_name="resolved",
        )
        return header
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{request_id}/close",
    response_model=PurchaseRequestHeaderResponse,
    dependencies=[Depends(require_current_revision("purchase_request", "request_id"))],
)
async def close_request_by_cs(
    request_id: str,
    payload: CsFinalizeRequest,
    request: Request,
    current_user: dict = Depends(require_permission("procurement.purchase_requests.close")),
    db: Session = Depends(get_db),
):
    """Close an approved purchase request / sponsorship form that can't be fulfilled (status='closed').

    Closes the customer-service form-SLA stage and sends a status-update message
    (+ optional note) to the contact via Respond.io.
    """
    assert_can_act_on_form(db, request_id, current_user)
    try:
        validate_uuid_path(request_id, resource="Request")
        respond_user_id = _respond_user_id_from_current_user(current_user)
        service = PurchaseRequestService(db)
        header = _dispatch_form_action(
            db,
            current_user,
            request,
            request_id=request_id,
            action_key="pr.finalize",
            payload={
                "new_status": "closed",
                "note": payload.note,
                "respond_user_id": respond_user_id,
                "crm_sender_user_id": current_user.get("id"),
            },
            event_name="resolved",
        )
        return header
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{request_id}/void",
    response_model=PurchaseRequestHeaderResponse,
    dependencies=[Depends(require_current_revision("purchase_request", "request_id"))],
)
async def void_purchase_request(
    request_id: str,
    payload: FormVoidRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Void a purchase request / sponsorship form (irreversible).

    Requires a free-text reason and the void permission
    ``procurement.purchase_requests.void`` (PR + SF share the router, the detail
    component, and this slug). Allowed only from a non-terminal state. Sets
    status='voided', emits the 'voided' form-SLA event, and best-effort notifies
    assignee + handler (in-app) and the salesperson (WhatsApp).
    """
    try:
        validate_uuid_path(request_id, resource="Request")
        from app.services.user_service import UserPermissionService

        service = PurchaseRequestService(db)
        header = service.get_request(request_id)
        slug = "procurement.purchase_requests.void"
        if not UserPermissionService(db).check_user_has_permission(current_user["id"], slug):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Permission required: {slug}")
        try:
            respond_user_id = _respond_user_id_from_current_user(current_user)
        except HTTPException:
            respond_user_id = None
        header = service.void_request(
            request_id,
            void_reason=payload.void_reason,
            actor_user_id=current_user.get("id"),
            respond_user_id=respond_user_id,
        )
        setattr(
            header,
            "voided_by_name",
            service._resolve_actor_display_name(getattr(header, "voided_by", None)) or None,
        )
        setattr(header, "voided_by_wa_phone", None)
        return header
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/bulk", status_code=status.HTTP_200_OK)
async def bulk_delete_purchase_requests(
    body: BulkDeletePurchaseRequestsRequest = Body(...),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Bulk delete purchase requests and/or sponsorship forms by ID."""
    try:
        service = PurchaseRequestService(db)
        return service.bulk_delete_requests(body.ids)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete(
    "/{request_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_current_revision("purchase_request", "request_id"))],
)
async def delete_purchase_request(
    request_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a purchase request or sponsorship form."""
    try:
        validate_uuid_path(request_id, resource="Request")
        service = PurchaseRequestService(db)
        service.delete_request(request_id)
        return {"message": "Purchase request deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{request_id}/send-approval-link",
    response_model=SendApprovalLinkResponse,
    dependencies=[Depends(require_current_revision("purchase_request", "request_id"))],
)
async def send_approval_link(
    request_id: str,
    data: SendApprovalLinkRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create one-time approval token and return public approval URL; optionally send link by email to approver."""
    if not data.approver_email and not data.approver_user_id:
        raise HTTPException(status_code=400, detail="Provide approver_email or approver_user_id.")
    try:
        validate_uuid_path(request_id, resource="Request")
        service = PurchaseRequestService(db)
        base_url = (data.base_url or getattr(settings, "frontend_base_url", None) or "").strip().rstrip("/")
        expires_hours = data.expires_hours or 24
        approval_token, approval_url = service.create_approval_token(
            request_id,
            approver_email=data.approver_email,
            approver_user_id=data.approver_user_id,
            requested_by_user_id=current_user.get("id"),
            expires_hours=expires_hours,
            base_url=base_url,
        )
        email_sent = None
        email_error = None
        if data.send_email:
            header_for_email = service.get_request(request_id)
            to_email = (data.approver_email or "").strip() or (
                getattr(header_for_email, "approver_email", None) or ""
            ).strip()
            if not to_email:
                email_sent = False
                email_error = "No approver email available to send to."
            else:
                from app.services.email_outbox_service import enqueue as enqueue_email
                from app.services.email_template_service import EmailTemplateService

                header = service.get_request(request_id)
                type_label = "Purchase Request" if getattr(header, "request_type", None) == "purchase_request" else "Sponsorship Form"
                full_url = approval_url if approval_url.startswith("http") else f"{base_url.rstrip('/')}{approval_url if approval_url.startswith('/') else '/' + approval_url}"
                rendered = EmailTemplateService(db).render_code(
                    "purchase_request_approval_link",
                    {
                        "purchase_request": {
                            "type_label": type_label,
                            "request_number": display_document_number(header) or "N/A",
                            "project_title": getattr(header, "project_title", None) or "N/A",
                        },
                        "approval_url": full_url,
                    },
                )
                try:
                    enqueue_email(
                        db,
                        event_key="purchase_request_approval_link",
                        to=to_email,
                        subject=rendered["subject"],
                        body_text=rendered["body_text"],
                        body_html=rendered["body_html"],
                        metadata={
                            "request_id": request_id,
                            "approval_token_id": str(approval_token.id),
                        },
                    )
                    db.commit()
                    email_sent = True
                    email_error = None
                except Exception as e:
                    email_sent = False
                    email_error = f"Enqueue failed: {e}"
        expires_at = getattr(approval_token, "expires")
        return SendApprovalLinkResponse(
            approval_url=approval_url,
            expires_at=expires_at,
            token_id=str(approval_token.id),
            email_sent=email_sent,
            email_error=email_error,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/attachments/{link_id}", status_code=status.HTTP_200_OK)
async def delete_purchase_request_attachment(
    link_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Unlink an attachment from a purchase request or sponsorship form."""
    try:
        service = PurchaseRequestService(db)
        service.delete_request_attachment(link_id)
        return {"message": "Attachment unlinked successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{request_id}/attachments",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_current_revision("purchase_request", "request_id"))],
)
async def link_attachment_to_purchase_request(
    request_id: str,
    body: PurchaseRequestAttachmentLinkRequest,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Link an existing attachment to a purchase request or sponsorship form."""
    try:
        validate_uuid_path(request_id, resource="Request")
        service = PurchaseRequestService(db)
        created_by = (current_user.get("id") or None) if isinstance(current_user.get("id"), str) and len(str(current_user.get("id"))) == 36 else None
        link = service.link_attachment_to_request(
            request_id=request_id,
            attachment_id=body.attachment_id,
            created_by=created_by,
        )
        return {
            "id": str(link.id),
            "purchase_request_id": str(link.entity_id),
            "attachment_id": str(link.attachment_id),
            "message": "Attachment linked successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{request_id}/view-link",
    response_model=ViewLinkResponse,
    dependencies=[Depends(require_public_view_links_enabled())],
)
async def get_or_create_view_link(
    request_id: str,
    data: Optional[ViewLinkRequest] = Body(None),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get or create a shareable view link (no login required). Use in Update & Reply to send to Respond."""
    try:
        validate_uuid_path(request_id, resource="Request")
        service = PurchaseRequestService(db)
        service.get_request(request_id)  # ensure exists and user can access
        token = service.get_or_create_view_token(request_id)
        db.commit()
        base = ((data.base_url if data else None) or getattr(settings, "frontend_base_url", "") or "").rstrip("/")
        view_url = f"{base}/view/request?token={token}" if base else f"/view/request?token={token}"
        return ViewLinkResponse(view_token=token, view_url=view_url)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


def _resolve_purchase_request_chat_contact(db: Session, request_id: str):
    """(respond_io identifier, internal respond_contact_id) for a purchase request."""
    service = PurchaseRequestService(db)
    header = service.get_request(request_id)
    identifier = service._identifier_from_respond_inbox_url(
        getattr(header, "respond_inbox_url", None)
    )
    return identifier, getattr(header, "contact_id", None)


def _resolve_purchase_request_chat_use_case(db: Session, request_id: str) -> str:
    """Sponsorship-form rows ride the PR panel + PurchaseRequestHeader but need
    their own chat template use case; everything else is a purchase request."""
    header = PurchaseRequestService(db).get_request(request_id)
    if (getattr(header, "request_type", None) or "") == "sponsorship_form":
        return "sponsorship_form_chat"
    return "purchase_request_chat"


def _build_purchase_request_chat_context(db: Session, request_id: str) -> dict:
    """Template context (portal_url / view_url) for a PR / sponsorship chat send."""
    service = PurchaseRequestService(db)
    header = service.get_request(request_id)
    return {
        "portal_url": service._purchase_request_portal_or_view_url(header, str(request_id)),
        "view_url": (service._build_request_view_url(str(request_id)) or "").strip(),
    }


from app.api.v1._respond_chat_template_routes import build_chat_template_router

router.include_router(
    build_chat_template_router(
        business_table="purchase_requests",
        resolver=_resolve_purchase_request_chat_contact,
        chat_use_case_resolver=_resolve_purchase_request_chat_use_case,
        context_builder=_build_purchase_request_chat_context,
    )
)


@router.post("/{request_id}/export/pdf")
def export_purchase_request_pdf(
    request_id: str,
    options: Optional[PdfExportOptions] = Body(None),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Queue an async PDF export of the printable Purchase Request / Sponsorship Form.

    Exists because the only export was Excel, where a long delivery address
    stretched a cell to an unusable width when printed. Creates a UserDownload row
    and enqueues generation; the result appears in the My Downloads drawer.
    Decoupled from the request path so a slow/failed render (attachments are
    downloaded and embedded) never blocks the caller. Mirrors
    POST /procurement/stock-inquiries/{id}/export/pdf.

    The body is optional (PLAN-portal-submission-revisions 6.3/6.4): no body is
    the export as it has always behaved, ``{"revision_id": ...}`` prints that one
    stored version, and ``{"include_revisions": true}`` appends the whole lineage
    behind the current form. The two are mutually exclusive (400). Sponsorship
    forms ride this same route, and their lineage is read under their own
    ``request_type``.
    """
    from app.services.download_service import DownloadService
    from app.services.pdf_revision_support import validate_export_request
    from app.services.purchase_request_pdf_service import filename_stem
    from app.services.queue_service import enqueue_job
    from app.tasks.export_tasks import generate_purchase_request_pdf

    try:
        validate_uuid_path(request_id, resource="Request")
        service = PurchaseRequestService(db)
        header = service.get_request(request_id, contact_id=None, space_id=None)  # 404 if missing

        is_sponsorship = (getattr(header, "request_type", None) or "") == "sponsorship_form"
        # Filename carries the revision, same as the document body (UAC N5) - a
        # single-revision export is named after THAT version's own number, which
        # is why the composer is shared with the service rather than repeated.
        number = display_document_number(header) or request_id

        revision_id = (options.revision_id if options else None) or None
        include_revisions = bool(options.include_revisions if options else False)
        # Validated BEFORE the download row exists: an unknown revision must be a
        # 404 the caller can act on, not a failed row in their drawer.
        filename = validate_export_request(
            db,
            "sponsorship_form" if is_sponsorship else "purchase_request",
            str(request_id),
            revision_id=revision_id,
            include_revisions=include_revisions,
            label="sponsorship form" if is_sponsorship else "purchase request",
            stem=filename_stem(getattr(header, "request_type", None)),
            number=number,
            number_field="request_number",
        )

        download = DownloadService(db).create(
            user_id=str(current_user["id"]),
            kind="purchase_request_pdf",
            source_entity_type="purchase_request",
            source_entity_id=str(request_id),
            filename=filename,
        )
        try:
            enqueue_job(
                generate_purchase_request_pdf,
                str(download.id),
                str(request_id),
                str(current_user["id"]),
                # By KEYWORD, and the task's own parameters have defaults: a
                # job queued by an older release carries three positional args
                # and must keep running against the new task.
                revision_id=revision_id,
                include_revisions=include_revisions,
                queue_name="imports",
                job_timeout=600,
            )
        except Exception as e:
            # Enqueue failed (e.g. Redis down): mark the row failed so the drawer shows it.
            DownloadService(db).mark_failed(
                str(download.id), f"Could not queue PDF generation: {e}"
            )
            raise handle_internal_error(f"Could not queue PDF generation: {e}")

        return {"download_id": str(download.id), "status": "queued"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))
