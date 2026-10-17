"""Stock inquiries API routes."""
import logging
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException, status, Request, Body, Response, File, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional
from pydantic import BaseModel

from app.database import get_db
from app.services.uuid_path_param import validate_uuid_path
from app.dependencies import get_current_user, get_current_user_or_api_key, require_permission
from app.services.procurement_service import StockInquiryService
from app.schemas.procurement import (
    StockInquiryCreate,
    StockInquiryUpdate,
    StockInquiryResponse,
    StockInquiryRejectReopenRequest,
    ViewLinkRequest,
    ViewLinkResponse,
)
from app.schemas.common import ListResponse, MAX_PAGE_LIMIT, FormVoidRequest
from app.schemas.download import PdfExportOptions
from app.services.error_handler import handle_internal_error
from app.services.handling_lock_service import assert_can_act_on_form
from app.services.revision_fence import require_current_revision
from app.services.document_number import display_document_number
from app.config import settings as app_settings
from app.modules.runtime.guards import require_public_view_links_enabled

logger = logging.getLogger(__name__)

router = APIRouter()


class BulkDeleteStockInquiriesRequest(BaseModel):
    ids: list[str]


class StockInquiryAttachmentLinkRequest(BaseModel):
    attachment_id: str


def _respond_user_id_from_current_user(current_user: dict) -> str:
    """Get respond_user_id for SLA/response tracking; fallback to user id."""
    rid = (current_user or {}).get("respond_user_id") or (current_user or {}).get("respondUserId")
    if rid and str(rid).strip():
        return str(rid).strip()
    uid = (current_user or {}).get("id")
    if uid and str(uid).strip():
        return str(uid).strip()
    raise HTTPException(status_code=400, detail="User respond_user_id or id is required for Update & Reply.")


@router.get("/", response_model=ListResponse[StockInquiryResponse])
async def get_stock_inquiries(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    query: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="Comma-separated status values to filter by"),
    sort: Optional[str] = Query("created_at"),
    dir: Optional[str] = Query("desc"),
    project_id: Optional[UUID] = Query(None, description="Only inquiries linked to this project"),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get stock inquiries with pagination and search."""
    try:
        service = StockInquiryService(db)
        sort_field = (sort and sort.strip()) or "created_at"
        sort_dir = (dir and dir.strip()) or "desc"
        statuses = [s.strip() for s in status.split(",") if s and s.strip()] if status else None
        result = service.list_inquiries(
            page=page,
            limit=limit,
            query=query,
            sort_field=sort_field,
            sort_dir=sort_dir,
            contact_id=None,
            space_id=None,
            statuses=statuses,
            viewer_user_id=(current_user or {}).get("id"),
            project_id=str(project_id) if project_id else None,
        )
        return result
    except Exception as e:
        import logging
        import traceback
        logger = logging.getLogger(__name__)
        logger.error(f"Error in get_stock_inquiries: {str(e)}")
        logger.error(traceback.format_exc())
        raise handle_internal_error(str(e))


@router.get("/{inquiry_id}", response_model=StockInquiryResponse)
async def get_stock_inquiry(
    inquiry_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Get a single stock inquiry by ID."""
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        service = StockInquiryService(db)
        return service.get_inquiry_for_response(
            inquiry_id,
            contact_id=None,
            space_id=None,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{inquiry_id}/revisions")
async def get_stock_inquiry_revisions(
    inquiry_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Revision lineage for the office Revisions tab (UAC H2/H3).

    The same payload the portal history reads - one service, one shape, so the two
    sides cannot disagree about what changed. Read-only: the office never creates,
    edits or deletes a revision (UAC H5).
    """
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        from app.services.portal_revision_service import PortalRevisionService

        # 404 for an unknown id before any history is read.
        StockInquiryService(db).get_inquiry(inquiry_id)
        return {
            "items": PortalRevisionService(db).list_revisions("stock_inquiry", inquiry_id)
        }
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.get("/{inquiry_id}/conversation")
async def get_stock_inquiry_conversation(
    inquiry_id: str,
    limit: int = Query(50, ge=1, le=MAX_PAGE_LIMIT),
    cursor: Optional[str] = Query(None),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Get Respond.io conversation messages for this stock inquiry (contact from respond_inbox_url)."""
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        from app.services.integration_service import RespondClient
        from app.models.access import RespondContact
        service = StockInquiryService(db)
        inquiry = service.get_inquiry(inquiry_id)
        respond_inbox_url = getattr(inquiry, "respond_inbox_url", None)
        identifier = service._identifier_from_respond_inbox_url(
            str(respond_inbox_url) if respond_inbox_url is not None else None
        )
        contact_meta: Optional[dict] = None
        contact_id_val = getattr(inquiry, "contact_id", None)
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


@router.delete("/attachments/{link_id}", status_code=status.HTTP_200_OK)
async def delete_stock_inquiry_attachment(
    link_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Unlink an attachment from a stock inquiry."""
    try:
        service = StockInquiryService(db)
        service.delete_inquiry_attachment(link_id)
        return {"message": "Attachment unlinked successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{inquiry_id}/attachments",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_current_revision("stock_inquiry", "inquiry_id"))],
)
async def link_attachment_to_stock_inquiry(
    inquiry_id: str,
    body: StockInquiryAttachmentLinkRequest,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Link an existing attachment to a stock inquiry."""
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        service = StockInquiryService(db)
        created_by = (current_user.get("id") or None) if isinstance(current_user.get("id"), str) and len(str(current_user.get("id"))) == 36 else None
        link = service.link_attachment_to_inquiry(
            inquiry_id=inquiry_id,
            attachment_id=body.attachment_id,
            created_by=created_by,
        )
        return {"message": "Attachment linked successfully", "link_id": link.id}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{inquiry_id}/response-attachments",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_current_revision("stock_inquiry", "inquiry_id"))],
)
async def upload_stock_inquiry_response_attachment(
    inquiry_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Upload a staff response attachment onto the stock inquiry, staged in the
    "Edit purchasing response" modal alongside the reply text. Uses the
    response_attachment type (its own quota, independent of the contact's
    portal_submission cap - UAC C4/D9) and stamps uploader_kind='user'."""
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        service = StockInquiryService(db)
        inquiry = service.get_inquiry(inquiry_id)
        contents = await file.read()
        from app.services.entity_attachment_service import create_response_attachment

        return create_response_attachment(
            db,
            entity_type="stock_inquiry",
            entity_id=str(inquiry.id),
            contents=contents,
            filename=file.filename,
            content_type=file.content_type,
            uploaded_by=(current_user or {}).get("id"),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/response-attachments/{link_id}", status_code=status.HTTP_200_OK)
async def delete_stock_inquiry_response_attachment(
    link_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db),
):
    """Hard-unlink a staff-uploaded response attachment from a stock inquiry (UAC F4)."""
    try:
        service = StockInquiryService(db)
        service.delete_inquiry_attachment(link_id)
        return {"message": "Attachment unlinked successfully"}
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{inquiry_id}/view-link",
    response_model=ViewLinkResponse,
    dependencies=[Depends(require_public_view_links_enabled())],
)
async def get_or_create_stock_inquiry_view_link(
    inquiry_id: str,
    data: Optional[ViewLinkRequest] = Body(None),
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get or create a shareable view link for this stock inquiry (no login required to view)."""
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        service = StockInquiryService(db)
        service.get_inquiry(inquiry_id)  # ensure exists and user can access
        token = service.get_or_create_view_token(inquiry_id)
        db.commit()
        base = ((data.base_url if data else None) or getattr(app_settings, "frontend_base_url", "") or "").rstrip("/")
        view_url = f"{base}/view/stock-inquiry?token={token}" if base else f"/view/stock-inquiry?token={token}"
        return ViewLinkResponse(view_token=token, view_url=view_url)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/{inquiry_id}/export/pdf")
async def export_stock_inquiry_pdf(
    inquiry_id: str,
    options: Optional[PdfExportOptions] = Body(None),
    current_user: dict = Depends(require_permission("procurement.stock_inquiries.view")),
    db: Session = Depends(get_db),
):
    """Queue an async PDF export of the PRODUCT INQUIRY FORM (printable copy).

    The document keeps the form's own heading, which is what the detail page has
    always rendered, so the file is named ``product-inquiry-<number>.pdf``.

    Creates a UserDownload row and enqueues generation; the result appears in the
    My Downloads drawer. Decoupled from the request path so a slow/failed render
    (attachments are downloaded and embedded) never blocks the caller. Mirrors
    POST /complaints-management/complaints/{id}/export/pdf.

    The body is optional (PLAN-portal-submission-revisions 6.3/6.4): no body is
    the export as it has always behaved, ``{"revision_id": ...}`` prints that one
    stored version, and ``{"include_revisions": true}`` appends the whole lineage
    behind the current form. The two are mutually exclusive (400).
    """
    from app.schemas.download import DownloadResponse
    from app.services.download_service import DownloadService
    from app.services.pdf_revision_support import validate_export_request
    from app.services.queue_service import enqueue_job
    from app.services.stock_inquiry_pdf_service import FILENAME_STEM
    from app.tasks.export_tasks import generate_stock_inquiry_pdf

    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        service = StockInquiryService(db)
        inquiry = service.get_inquiry(inquiry_id)  # 404 if missing

        revision_id = (options.revision_id if options else None) or None
        include_revisions = bool(options.include_revisions if options else False)

        # Filename carries the revision, same as the document body (UAC N5) - a
        # single-revision export is named after THAT version's own number, which
        # is why the composer is shared with the service rather than repeated.
        number = display_document_number(inquiry) or inquiry_id
        # Validated BEFORE the download row exists: an unknown revision must be a
        # 404 the caller can act on, not a failed row in their drawer.
        filename = validate_export_request(
            db,
            "stock_inquiry",
            str(inquiry_id),
            revision_id=revision_id,
            include_revisions=include_revisions,
            label="stock inquiry",
            stem=FILENAME_STEM,
            number=number,
            number_field="inquiry_number",
        )
        download = DownloadService(db).create(
            user_id=str(current_user["id"]),
            kind="stock_inquiry_pdf",
            source_entity_type="stock_inquiry",
            source_entity_id=str(inquiry_id),
            filename=filename,
        )
        try:
            enqueue_job(
                generate_stock_inquiry_pdf,
                str(download.id),
                str(inquiry_id),
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
            DownloadService(db).mark_failed(str(download.id), f"Could not queue PDF generation: {e}")
            raise handle_internal_error("Could not queue PDF generation. Please try again.")

        return DownloadResponse.model_validate(DownloadService(db).get(str(download.id)))
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post("/", response_model=StockInquiryResponse, status_code=status.HTTP_201_CREATED)
async def create_stock_inquiry(
    inquiry_data: StockInquiryCreate,
    request: Request,
    response: Response,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Create a new stock inquiry. If ``inquiry_number`` matches a rejected inquiry, that row is updated (200). Notifies project sales team when status is new or pending_project_sales."""
    try:
        service = StockInquiryService(db)
        inquiry, outcome = service.create_inquiry(inquiry_data)
        if outcome == "resubmitted":
            response.status_code = status.HTTP_200_OK
        db.commit()
        if getattr(inquiry, "status", None) in ("new", "pending_project_sales"):
            try:
                if outcome == "resubmitted":
                    service._notify_team_stock_inquiry(
                        inquiry_id=str(inquiry.id),
                        agent_code="lead_time_enquiries",
                        team_assignment_code="project_sales",
                        title="Stock Inquiry updated (resubmitted)",
                        intro_plain="Dear Project Sales Team,\n\nA previously rejected stock inquiry has been updated and resubmitted for your review.",
                        intro_html="Dear Project Sales Team,<br /><br />A previously rejected stock inquiry has been updated and resubmitted for your review.",
                        event_type="resubmitted",
                    )
                else:
                    service._notify_team_stock_inquiry(
                        inquiry_id=str(inquiry.id),
                        agent_code="lead_time_enquiries",
                        team_assignment_code="project_sales",
                        title="New Stock Inquiry created",
                        intro_plain="Dear Project Sales Team,\n\nA new stock inquiry has been created and requires your review.",
                        intro_html="Dear Project Sales Team,<br /><br />A new stock inquiry has been created and requires your review.",
                        event_type="created",
                    )
            except Exception as e:
                logger.warning(
                    "Stock inquiry project_sales notify failed for inquiry %s: %s",
                    getattr(inquiry, "id", None),
                    e,
                    exc_info=True,
                )
        return inquiry
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.put(
    "/{inquiry_id}",
    response_model=StockInquiryResponse,
    dependencies=[Depends(require_current_revision("stock_inquiry", "inquiry_id"))],
)
async def update_stock_inquiry(
    inquiry_id: str,
    inquiry_data: StockInquiryUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Update a stock inquiry."""
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        service = StockInquiryService(db)
        inquiry = service.update_inquiry(inquiry_id, inquiry_data)
        db.commit()
        return inquiry
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{inquiry_id}/update-and-reply",
    response_model=StockInquiryResponse,
    dependencies=[Depends(require_current_revision("stock_inquiry", "inquiry_id"))],
)
async def update_stock_inquiry_and_reply(
    inquiry_id: str,
    inquiry_data: StockInquiryUpdate,
    request: Request,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Send the purchasing response to the contact via Respond.io and record it.

    This is the RESPONSE path and is gated on status (UAC O1): allowed at
    pending_purchasing or responded, 422 anywhere else. Plain messaging is a
    separate endpoint (``/{inquiry_id}/conversation/send-message``) that never
    touches the inquiry and keeps working at any status, closed included (UAC O2).
    """
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        assert_can_act_on_form(db, inquiry_id, current_user, source_entity_type="stock_inquiry")
        respond_user_id = _respond_user_id_from_current_user(current_user)
        from app.services.form_action_dispatch import dispatch_or_defer

        outcome = dispatch_or_defer(
            db,
            current_user,
            request,
            action_key="si.purchasing_respond",
            entity_type="stock_inquiry",
            entity_id=inquiry_id,
            payload={
                "inquiry_id": inquiry_id,
                # Parked in JSONB, so it must go in as a plain dict; the runner
                # rehydrates it back into StockInquiryUpdate before calling the service.
                "inquiry_data": inquiry_data.model_dump(mode="json", exclude_unset=True),
                "respond_user_id": respond_user_id,
                "request_url": str(request.url) if request else "",
                "crm_sender_user_id": current_user.get("id"),
            },
            event_name="purchasing_respond",
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        db.commit()
        return outcome
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{inquiry_id}/submit-for-project-sales",
    response_model=StockInquiryResponse,
    dependencies=[Depends(require_current_revision("stock_inquiry", "inquiry_id"))],
)
async def submit_stock_inquiry_for_project_sales(
    inquiry_id: str,
    current_user: dict = Depends(require_permission("procurement.stock_inquiries.submit_for_project_sales")),
    db: Session = Depends(get_db),
):
    """Move stock inquiry from new to pending_project_sales."""
    assert_can_act_on_form(db, inquiry_id, current_user, source_entity_type="stock_inquiry")
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        service = StockInquiryService(db)
        inquiry = service.submit_inquiry_for_project_sales(inquiry_id)
        return inquiry
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{inquiry_id}/project-sales-approve",
    response_model=StockInquiryResponse,
    dependencies=[Depends(require_current_revision("stock_inquiry", "inquiry_id"))],
)
async def project_sales_approve_stock_inquiry(
    inquiry_id: str,
    request: Request,
    current_user: dict = Depends(require_permission("procurement.stock_inquiries.project_sales_approve")),
    db: Session = Depends(get_db),
):
    """Move stock inquiry from pending_project_sales to pending_purchasing."""
    assert_can_act_on_form(db, inquiry_id, current_user, source_entity_type="stock_inquiry")
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        from app.services.form_action_dispatch import dispatch_or_defer

        service = StockInquiryService(db)
        user_id = (current_user or {}).get("id")
        respond_user_id = (
            (current_user or {}).get("respond_user_id")
            or (current_user or {}).get("respondUserId")
            or user_id
        )
        outcome = dispatch_or_defer(
            db,
            current_user,
            request,
            action_key="si.project_sales_approve",
            entity_type="stock_inquiry",
            entity_id=inquiry_id,
            payload={
                "inquiry_id": inquiry_id,
                "actor_user_id": user_id,
                "crm_sender_user_id": user_id,
                "respond_user_id_fallback": str(respond_user_id or ""),
            },
            event_name="project_sales_approve",
        )
        # Deferred => the 202 JSONResponse; immediate => the service's inquiry, exactly
        # what this route returned before.
        return outcome
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{inquiry_id}/project-sales-reject",
    response_model=StockInquiryResponse,
    dependencies=[Depends(require_current_revision("stock_inquiry", "inquiry_id"))],
)
async def project_sales_reject_stock_inquiry(
    inquiry_id: str,
    request: Request,
    body: Optional[StockInquiryRejectReopenRequest] = Body(None),
    current_user: dict = Depends(require_permission("procurement.stock_inquiries.project_sales_reject")),
    db: Session = Depends(get_db),
):
    """Move stock inquiry from pending_project_sales to rejected."""
    assert_can_act_on_form(db, inquiry_id, current_user, source_entity_type="stock_inquiry")
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        from app.services.form_action_dispatch import dispatch_or_defer

        service = StockInquiryService(db)
        reason = body.reason if body else None
        user_id = (current_user or {}).get("id")
        respond_user_id = (
            (current_user or {}).get("respond_user_id")
            or (current_user or {}).get("respondUserId")
            or user_id
        )
        outcome = dispatch_or_defer(
            db,
            current_user,
            request,
            action_key="si.project_sales_reject",
            entity_type="stock_inquiry",
            entity_id=inquiry_id,
            payload={
                "inquiry_id": inquiry_id,
                "reason": reason,
                "user_id": user_id,
                "crm_sender_user_id": user_id,
                "respond_user_id_fallback": str(respond_user_id or ""),
            },
            event_name="project_sales_reject",
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return service.get_inquiry_for_response(inquiry_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{inquiry_id}/purchasing-reject",
    response_model=StockInquiryResponse,
    dependencies=[Depends(require_current_revision("stock_inquiry", "inquiry_id"))],
)
async def purchasing_reject_stock_inquiry(
    inquiry_id: str,
    request: Request,
    body: Optional[StockInquiryRejectReopenRequest] = Body(None),
    current_user: dict = Depends(require_permission("procurement.stock_inquiries.purchasing_reject")),
    db: Session = Depends(get_db),
):
    """Move stock inquiry from pending_purchasing to rejected."""
    assert_can_act_on_form(db, inquiry_id, current_user, source_entity_type="stock_inquiry")
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        from app.services.form_action_dispatch import dispatch_or_defer

        service = StockInquiryService(db)
        reason = body.reason if body else None
        user_id = (current_user or {}).get("id")
        respond_user_id = (
            (current_user or {}).get("respond_user_id")
            or (current_user or {}).get("respondUserId")
            or user_id
        )
        outcome = dispatch_or_defer(
            db,
            current_user,
            request,
            action_key="si.purchasing_decide",
            entity_type="stock_inquiry",
            entity_id=inquiry_id,
            payload={
                "inquiry_id": inquiry_id,
                "reason": reason,
                "user_id": user_id,
                "crm_sender_user_id": user_id,
                "respond_user_id_fallback": str(respond_user_id or ""),
            },
            event_name="purchasing_decide",
        )
        if isinstance(outcome, JSONResponse):
            return outcome
        return service.get_inquiry_for_response(inquiry_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{inquiry_id}/reopen",
    response_model=StockInquiryResponse,
    dependencies=[Depends(require_current_revision("stock_inquiry", "inquiry_id"))],
)
async def reopen_stock_inquiry(
    inquiry_id: str,
    body: Optional[StockInquiryRejectReopenRequest] = Body(None),
    current_user: dict = Depends(require_permission("procurement.stock_inquiries.reopen")),
    db: Session = Depends(get_db),
):
    """Move stock inquiry from rejected to pending_project_sales."""
    assert_can_act_on_form(db, inquiry_id, current_user, source_entity_type="stock_inquiry")
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        service = StockInquiryService(db)
        reason = body.reason if body else None
        user_id = (current_user or {}).get("id")
        service.reopen_inquiry(inquiry_id, reason=reason, user_id=user_id)
        return service.get_inquiry_for_response(inquiry_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.post(
    "/{inquiry_id}/void",
    response_model=StockInquiryResponse,
    dependencies=[Depends(require_current_revision("stock_inquiry", "inquiry_id"))],
)
async def void_stock_inquiry(
    inquiry_id: str,
    payload: FormVoidRequest,
    current_user: dict = Depends(require_permission("procurement.stock_inquiries.void")),
    db: Session = Depends(get_db),
):
    """Void a stock inquiry (irreversible).

    Requires a free-text reason and the ``procurement.stock_inquiries.void`` permission; allowed
    only from a non-terminal state. Sets status='voided', emits the 'voided'
    form-SLA event, and best-effort notifies assignee + handler (in-app) and the
    salesperson (WhatsApp).
    """
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        respond_user_id = _respond_user_id_from_current_user(current_user)
        service = StockInquiryService(db)
        service.void_inquiry(
            inquiry_id,
            void_reason=payload.void_reason,
            actor_user_id=current_user.get("id"),
            respond_user_id=respond_user_id,
        )
        return service.get_inquiry_for_response(inquiry_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete("/bulk", status_code=status.HTTP_200_OK)
async def bulk_delete_stock_inquiries(
    body: BulkDeleteStockInquiriesRequest = Body(...),
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Bulk delete stock inquiries by ID."""
    try:
        service = StockInquiryService(db)
        return service.bulk_delete_inquiries(body.ids)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


@router.delete(
    "/{inquiry_id}",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_current_revision("stock_inquiry", "inquiry_id"))],
)
async def delete_stock_inquiry(
    inquiry_id: str,
    current_user: dict = Depends(get_current_user_or_api_key),
    db: Session = Depends(get_db)
):
    """Delete a stock inquiry."""
    try:
        validate_uuid_path(inquiry_id, resource="Stock Inquiry")
        service = StockInquiryService(db)
        return service.delete_inquiry(inquiry_id)
    except HTTPException:
        raise
    except Exception as e:
        raise handle_internal_error(str(e))


def _resolve_stock_inquiry_chat_contact(db: Session, inquiry_id: str):
    """(respond_io identifier, internal respond_contact_id) for a stock inquiry."""
    service = StockInquiryService(db)
    inquiry = service.get_inquiry(inquiry_id)
    respond_inbox_url = getattr(inquiry, "respond_inbox_url", None)
    identifier = service._identifier_from_respond_inbox_url(
        str(respond_inbox_url) if respond_inbox_url is not None else None
    )
    return identifier, getattr(inquiry, "contact_id", None)


def _build_stock_inquiry_chat_context(db: Session, inquiry_id: str) -> dict:
    """Template context (portal_url / view_url) for a stock-inquiry chat send."""
    service = StockInquiryService(db)
    inquiry = service.get_inquiry(inquiry_id)
    return {
        "portal_url": service._stock_inquiry_portal_or_view_url(inquiry, str(inquiry_id)),
        "view_url": (service._build_stock_inquiry_view_url(str(inquiry_id)) or "").strip(),
    }


from app.api.v1._respond_chat_template_routes import build_chat_template_router

router.include_router(
    build_chat_template_router(
        business_table="stock_inquiries",
        resolver=_resolve_stock_inquiry_chat_contact,
        chat_use_case="stock_inquiry_chat",
        context_builder=_build_stock_inquiry_chat_context,
    )
)
