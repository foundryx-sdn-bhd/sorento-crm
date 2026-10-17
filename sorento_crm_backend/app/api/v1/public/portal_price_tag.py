"""Portal price tag request endpoints.

Mounted at ``/api/v1/public/portal`` alongside the existing portal router.
Auth: portal token (``get_portal_token`` dependency).

These endpoints are separate from ``portal.py`` because price tag requests use
a dedicated service (``PriceTagRequestService``) rather than the generic
``PortalService`` CRUD, and keeping them apart avoids bloating the already-large
portal module.
"""
from __future__ import annotations

import logging
import mimetypes
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.public.portal import get_portal_token
from app.database import get_db
from app.models.base import company_scope
from app.models.portal import PortalToken
from app.models.dealer_kit import Page
from app.schemas.price_tag import (
    DebtorForAgentItem,
    LinePricingRequest,
    LinePricingRow,
    PortalTagSheetDesignResponse,
    PriceTagRequestCreate,
    PriceTagRequestResponse,
    PriceTagRequestUpdate,
    RequestChangesPayload,
    RequestChangesResponse,
    ResolvedLineData,
    ReviewCommentResponse,
    TagItemLookupItem,
)
from app.services.dealer_kit import tag_data_service
from app.services.dealer_kit.tag_sheet_export_service import latest_completed_export
from app.services.error_handler import AppException, handle_not_found
from app.services import price_tag_review_service
from app.services.portal_form_visibility_service import resolve_visible_form_types
from app.services.price_tag_request_service import (
    PriceTagRequestService,
    STATUS_APPROVED,
    STATUS_CHANGES_REQUESTED,
    STATUS_NEW,
    STATUS_PROOF_READY,
    STATUS_READY_FOR_COLLECTION,
    STATUS_COLLECTED,
)
from app.services.storage_router import get_backend
from app.services.uuid_path_param import validate_uuid_path
from app.utils.http import content_disposition

logger = logging.getLogger(__name__)

router = APIRouter(tags=["public-portal-price-tag"])

_FORM_TYPE = "price_tag_request"


def _assert_visible(db: Session, contact_id: str) -> None:
    """Raise 403 if price_tag_request is not visible to this contact."""
    visible = resolve_visible_form_types(db, contact_id)
    if _FORM_TYPE not in visible:
        raise AppException(
            status_code=403,
            message="Price tag request is not available for your account.",
            code="FORM_TYPE_NOT_VISIBLE",
        )


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@router.get("/submissions/price_tag_request")
def portal_list_price_tag_requests(
    q: Optional[str] = Query(None),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """List price tag requests for the authenticated contact.

    Drafts included: a draft is the whole point of this screen. Through
    ``list_items`` for the line count the card prints, which was ``undefined``
    on every row because the list schema never carried it.
    """
    _assert_visible(db, token.contact_id)
    results = PriceTagRequestService.list_requests(
        db, contact_id=token.contact_id, search=q,
    )
    return {"items": PriceTagRequestService.list_items(db, results)}


# ---------------------------------------------------------------------------
# Create (draft)
# ---------------------------------------------------------------------------


def _check_portal_project(db: Session, token: PortalToken, project_id):
    """FORMS-PROJECT: a portal contact links only a project of one of their own companies."""
    from app.models.access import RespondContact
    from app.services.project_link import check_portal_project_link

    contact = db.query(RespondContact).filter(RespondContact.id == token.contact_id).first()
    return check_portal_project_link(db, contact=contact, project_id=project_id)


@router.post("/submissions/price_tag_request", status_code=201)
def portal_create_price_tag_request(
    payload: PriceTagRequestCreate,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Create a new price tag request as a draft.

    D1 (S6): the promotion is a LINE fact now - each line's own
    ``promotion_id``, if any, is validated (AC-S6-5) by ``_add_lines``
    itself, against THIS contact's own audience (``tag_data_service.contact_viewer``), not by
    a header-level check here.
    """
    _assert_visible(db, token.contact_id)
    _check_portal_project(db, token, payload.project_id)
    company_id = _resolve_company(db, token)
    with company_scope(db, frozenset({company_id})):
        req = PriceTagRequestService.create_request(
            db,
            contact_id=token.contact_id,
            company_id=company_id,
            data=payload.model_dump(),
            viewer=tag_data_service.contact_viewer(db, token.contact_id),
        )
    db.commit()
    return _detail_body(db, req)


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


@router.get("/submissions/price_tag_request/{request_id}")
def portal_get_price_tag_request(
    request_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """One request, in the shape the portal form reopens a draft from.

    Answers the same body as the CRM detail route (lines resolved to code, name
    and both prices), plus the attachments key the form reads unconditionally.
    """
    request_id = validate_uuid_path(request_id, resource="Price tag request")
    _assert_visible(db, token.contact_id)
    return _detail_body(db, _require_own_request(db, token, request_id))


# ---------------------------------------------------------------------------
# Design preview (D11)
# ---------------------------------------------------------------------------

# The design is only shown once marketing has produced one to show and the
# salesperson's own review of it makes sense - not while it is still being
# designed, and still visible after approval so they can look at what they
# approved (AC-S4-4).
_DESIGN_VISIBLE_STATUSES = frozenset(
    {
        STATUS_PROOF_READY,
        STATUS_CHANGES_REQUESTED,
        STATUS_APPROVED,
        # r9 D8: `ready` is retired and the office print's two closing steps
        # take its place. The salesperson can still look at what they approved
        # right up to the moment they collect it.
        STATUS_READY_FOR_COLLECTION,
        STATUS_COLLECTED,
    }
)


@router.get(
    "/submissions/price_tag_request/{request_id}/design",
    response_model=PortalTagSheetDesignResponse,
)
def portal_get_price_tag_design(
    request_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """The salesperson's real design preview (D11) - the same document AND
    resolved line data the CRM designer reads, so the two screens can never
    disagree about what a tag says.

    404, never 403, everywhere the design is not meant to be seen yet: a
    status the FE's own review section does not offer (``new``, ``designing``),
    or a portal DRAFT regardless of what its status happens to be (a draft's
    status is ``new`` anyway, but this is its own reason, not a consequence of
    the status check) - so a design in progress never leaks its existence to
    the portal before marketing means it to.
    """
    request_id = validate_uuid_path(request_id, resource="Price tag request")
    _assert_visible(db, token.contact_id)
    req = _require_own_request(db, token, request_id)
    if req.portal_draft_at is not None or req.status not in _DESIGN_VISIBLE_STATUSES:
        raise AppException(
            status_code=404,
            message="Price tag request not found.",
            code="NOT_FOUND",
        )
    if not req.page_id:
        raise AppException(
            status_code=404,
            message="No design exists for this request yet.",
            code="NOT_FOUND",
        )
    page = db.query(Page).filter(Page.id == req.page_id).first()
    if not page:
        raise AppException(
            status_code=404,
            message="No design exists for this request yet.",
            code="NOT_FOUND",
        )

    # Function-local: the resolver lives on the CRM router module, imported
    # here rather than duplicated so the two screens can never resolve a
    # different document for the same page (module-cycle-free the same way
    # the export import below is).
    from app.api.v1.dealer_kit.price_tag_requests import resolve_tag_sheet_design
    from app.services.dealer_kit import tag_sheet_export_service

    # prefer="version": the salesperson must see what was deliberately
    # SAVED (and sent to them for review), never marketing's live
    # in-progress autosave - the CRM designer stays draft-first (B1's own
    # reasoning), this screen does not (review D11 follow-up).
    doc_fields = resolve_tag_sheet_design(db, page, prefer="version")
    if doc_fields["doc"] is None:
        # A page whose only document is an autosaved draft has nothing this
        # reader was ever meant to see (r9 S1).
        raise AppException(
            status_code=404,
            message="No design exists for this request yet.",
            code="NOT_FOUND",
        )
    # L3: same company scope as the sibling lookups (portal_lookup_tag_items,
    # portal_lookup_promotions) - unscoped, a two-company contact's line
    # resolution could read the OTHER company's product row for a duplicated
    # code instead of the one this request actually points at. The request's
    # OWN company, like the update route: it was stamped at create, and a
    # contact re-tagged since must still see the design they were sent.
    with company_scope(db, frozenset({req.company_id})):
        # The SAME resolver the PDF reads, media included (r9 S1/D1): without
        # `assets`/`images`/`fonts` every image layer here painted a grey box
        # and every brand face fell back to a system sans.
        rows, media = tag_sheet_export_service.design_media(
            db, req, doc_fields["doc"]
        )
    lines = [ResolvedLineData.model_validate(row) for row in rows]
    return PortalTagSheetDesignResponse(**doc_fields, lines=lines, **media)


# ---------------------------------------------------------------------------
# Download the latest completed tag sheet PDF
# ---------------------------------------------------------------------------


@router.get("/submissions/price_tag_request/{request_id}/download")
def portal_download_price_tag_pdf(
    request_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
) -> Response:
    """The request's latest completed tag sheet PDF, streamed same-origin.

    Ownership is the request's contact (``_require_own_request``), exactly like
    every other portal price tag route - never the download row's ``user_id``
    (whichever marketing staffer ran the export). A foreign token refuses with
    "Price tag request not found." (``_require_own_request``'s message, the
    ownership check runs first); an owned request with no completed export
    refuses with "No completed export exists for this request yet." - two
    DIFFERENT messages, not the same one as an earlier version of this
    docstring claimed. What they share is that neither leaks whether the OTHER
    fact is true: a foreign token never learns whether an export exists, and a
    "no export yet" 404 never confirms the request is genuinely this
    contact's until ownership has already passed (AC-S2-4).
    """
    request_id = validate_uuid_path(request_id, resource="Price tag request")
    _assert_visible(db, token.contact_id)
    req = _require_own_request(db, token, request_id)
    download = latest_completed_export(db, req.id)
    if download is None or not download.storage_key:
        raise AppException(
            status_code=404,
            message="No completed export exists for this request yet.",
            code="NOT_FOUND",
        )
    try:
        content = get_backend(download.storage_provider).download_file(download.storage_key)
    except HTTPException:
        # An AppException from the service is already the right answer -
        # don't relabel it as a storage failure.
        raise
    except Exception as e:  # noqa: BLE001 - mirrors portal_download_attachment
        logger.warning(
            "portal_download_price_tag_pdf: could not read %s", download.storage_key,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="File download failed. Please try again.",
        ) from e

    filename = download.filename or "tag-sheet.pdf"
    media_type = mimetypes.guess_type(filename)[0] or "application/pdf"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": content_disposition(filename),
            "Content-Length": str(len(content)),
        },
    )


@router.post(
    "/submissions/price_tag_request/{request_id}/export", status_code=202
)
def portal_export_price_tag_pdf(
    request_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Queue a NEW tag sheet PDF for the portal's Download PDF (r10 S9).

    Contact-authenticated exactly like the download route above - a request
    that has no READY export yet (or whose last one FAILED) has nothing for
    that route to stream, and until now the portal had no way to ask for
    one; Approve auto-queues an export (D12), but nothing retried a FAILED
    one and the button just sat dead.

    Queued as the request's own ASSIGNEE, the same actor
    ``portal_approve_price_tag_request`` passes - the portal has no CRM user
    of its own, and the marketing person who designed the tag sheet is the
    natural "who asked for this PDF" answer here too.
    `UserDownload.user_id` is a bare string, not FK-checked, so an
    unclaimed request (no assignee yet) falls back to the contact's own id
    rather than 422ing on "no requesting user" - a click here must always
    get a PDF queued, the same way it always streams a READY one.
    `request_tag_sheet_export`'s own guards (status, promotion, page/version)
    pass their 409 straight through as the toast text.
    """
    _assert_visible(db, token.contact_id)
    req = _require_own_request(db, token, request_id)
    from app.services.dealer_kit.tag_sheet_export_service import (
        request_tag_sheet_export,
    )

    download, _sheet_ids = request_tag_sheet_export(
        db, request_id=req.id, user_id=req.assigned_to_id or token.contact_id,
    )
    return {"download_id": str(download.id)}


# ---------------------------------------------------------------------------
# Update (draft)
# ---------------------------------------------------------------------------


@router.put("/submissions/price_tag_request/{request_id}")
def portal_update_price_tag_request(
    request_id: str,
    payload: PriceTagRequestUpdate,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Update a draft. R3-1 REVERSES D-P6/S8: a submitted request is
    read-only exactly like the other portal kinds - changes go through the
    revision engine (``PortalRevisionService.revise``) instead, gated by
    System Settings > Portal Revisions. Back to ``_require_draft``: 409
    ``NOT_DRAFT`` for every non-draft status, ``new`` / ``changes_requested``
    included. The post-submit validators, override carry-over and audit row
    S8 added here now live in ``portal_revision_service._apply_price_tag_lines``,
    part of the revise transaction."""
    request_id = validate_uuid_path(request_id, resource="Price tag request")
    _assert_visible(db, token.contact_id)
    req = _require_own_request(db, token, request_id)
    _require_draft(req, "Only a draft can be edited.")

    update_data = payload.model_dump(exclude_unset=True)
    if "project_id" in update_data:
        update_data["project_id"] = _check_portal_project(db, token, update_data["project_id"])
    # `lines` is a relationship, not a column: given, it REPLACES the draft's
    # lines; omitted, it leaves them alone. Re-saving a draft posts the whole
    # table, which is why the form no longer creates a second request each time.
    lines = update_data.pop("lines", None)
    for key, value in update_data.items():
        setattr(req, key, value)
    if lines is not None:
        # D1/AC-S6-5: each line's own `promotion_id` is validated here,
        # against THIS contact's audience - same as create.
        with company_scope(db, frozenset({req.company_id})):
            PriceTagRequestService.replace_lines(
                db, req, lines, viewer=tag_data_service.contact_viewer(db, req.contact_id)
            )

    db.flush()
    db.commit()
    return _detail_body(db, req)


# ---------------------------------------------------------------------------
# Delete (draft)
# ---------------------------------------------------------------------------


@router.delete("/submissions/price_tag_request/{request_id}", status_code=204)
def portal_delete_price_tag_request(
    request_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Hard-delete a draft, lines and all, the way the legacy kinds delete theirs.

    Draft only: once submitted the request is marketing's work, and taking it back
    is a status change (void), not a delete.
    """
    _assert_visible(db, token.contact_id)
    req = _require_own_request(db, token, request_id)
    _require_draft(req, "Only a draft can be deleted.")
    db.delete(req)
    db.commit()
    return None


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


@router.post("/submissions/price_tag_request/{request_id}/submit")
def portal_submit_price_tag_request(
    request_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Submit a draft price tag request: clears portal_draft_at, runs set guard,
    and fires the form SLA."""
    request_id = validate_uuid_path(request_id, resource="Price tag request")
    _assert_visible(db, token.contact_id)
    req = _require_own_request(db, token, request_id)
    _require_draft(req, "This request has already been submitted.", code="ALREADY_SUBMITTED")

    # A draft may be sloppy; a submitted request may not (D48a). Completeness
    # first, because "you have no dealer" is more use than a guard message about
    # a line on a request that was never going to be accepted anyway.
    PriceTagRequestService.validate_submittable(req)
    # The set guard used to refuse here. Warn and allow instead (D2, AC-S2-7):
    # a guarded line with no package carries a `package_warning` marketing reads
    # and the request goes through.
    PriceTagRequestService.apply_package_warnings(db, req)

    # Clear draft and set status to new (ready for marketing).
    req.portal_draft_at = None
    req.status = STATUS_NEW
    db.flush()
    db.commit()

    # S9/D12: the copy table's first line. Submit is not a status transition -
    # a submitted request keeps `new` until marketing claims it - so it is the
    # one moment the transition notifier cannot cover, and the moment somebody
    # most wants to hear that the form worked. After the commit, and before the
    # auto-assign below can move the request on, so the salesperson reads the
    # two messages in the order the events happened.
    PriceTagRequestService.notify_submitted(db, req)

    # Fire form SLA.
    try:
        from app.services.form_sla_service import emit_form_event

        emit_form_event(
            db,
            _FORM_TYPE,
            str(req.id),
            "submit",
            contact_id=req.contact_id,
        )
    except Exception:
        logger.warning(
            "Form SLA emit 'submit' failed for price_tag_request %s",
            req.id,
            exc_info=True,
        )

    # D8: auto-assign to whatever tracker the emit above just opened. Its own
    # try/except - a bug here must not turn a successful submit into a
    # failed one; the request simply stays `new` and unclaimed, same as if
    # no active config existed at all (AC-S3-3).
    try:
        PriceTagRequestService.auto_assign_from_tracker(db, req)
    except Exception:
        logger.warning(
            "Auto-assign from tracker failed for price_tag_request %s",
            req.id,
            exc_info=True,
        )

    db.commit()
    return PriceTagRequestResponse.model_validate(req)


# ---------------------------------------------------------------------------
# Approve (portal proof review)
# ---------------------------------------------------------------------------


@router.post("/submissions/price_tag_request/{request_id}/approve")
def portal_approve_price_tag_request(
    request_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Approve a proof-ready price tag request."""
    _assert_visible(db, token.contact_id)
    req = PriceTagRequestService.get_request(db, request_id)
    if not req or req.contact_id != token.contact_id:
        raise AppException(
            status_code=404,
            message="Price tag request not found.",
            code="NOT_FOUND",
        )

    # D12's auto-export needs a requesting user; the portal has no CRM user
    # of its own, so this passes the request's own assignee - the marketing
    # person who designed it, and the natural "who asked for this PDF" answer
    # for an export the SALESPERSON'S approve click triggered.
    if not req.assigned_to_id:
        logger.warning(
            "portal_approve_price_tag_request: %s has no assignee, export skipped",
            req.id,
        )
    result = PriceTagRequestService.transition_status(
        db, request_id, STATUS_APPROVED, user_id=req.assigned_to_id,
    )
    db.commit()
    return PriceTagRequestResponse.model_validate(result)


# ---------------------------------------------------------------------------
# Request changes (portal proof review)
# ---------------------------------------------------------------------------


@router.post(
    "/submissions/price_tag_request/{request_id}/request-changes",
    response_model=RequestChangesResponse,
)
def portal_request_changes(
    request_id: str,
    payload: RequestChangesPayload,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """A whole round of pinned change requests, in one call (r9 D5).

    The pins are placed locally and nothing reaches the server until Send, so
    the salesperson can put five pins down, delete two, and the request changes
    state exactly once.

    The salesperson's own ``notes`` are no longer appended to: they are what
    they asked for, not a log of what they later disliked. The comments are
    rows now, each pointing at the part of the tag it is about.

    ``note`` alone (no pins) is the legacy body, accepted for one release and
    stored as one general comment.
    """
    request_id = validate_uuid_path(request_id, resource="Price tag request")
    _assert_visible(db, token.contact_id)
    req = _require_own_request(db, token, request_id)
    if req.status != STATUS_PROOF_READY:
        # Checked BEFORE anything is written: a design still being drawn cannot
        # be commented on, and a 409 that leaves rows behind is worse than no
        # 409 at all.
        raise AppException(
            status_code=409,
            message="This design is not waiting for your review.",
            code="INVALID_TRANSITION",
        )

    created = price_tag_review_service.create_comments(
        db,
        req,
        comments=[pin.model_dump() for pin in payload.comments],
        note=payload.note,
        author_contact_id=token.contact_id,
    )
    round_no = created[0].round if created else price_tag_review_service.current_round(
        db, req
    )
    # S1: the round and the tally travel with the transition. Without them the
    # bell dedups every later round against round 1 (so the assignee is told
    # once, ever) and the salesperson's own confirmation cannot say how many
    # change requests the one call carried.
    PriceTagRequestService.transition_status(
        db,
        request_id,
        STATUS_CHANGES_REQUESTED,
        notify_ctx={"round": round_no, "count": len(created)},
    )
    db.commit()
    return RequestChangesResponse(
        status=STATUS_CHANGES_REQUESTED,
        round=round_no,
        comments=price_tag_review_service.to_responses(db, created),
    )


@router.get(
    "/submissions/price_tag_request/{request_id}/review-comments",
    response_model=list[ReviewCommentResponse],
)
def portal_list_review_comments(
    request_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Every change request on this design, all rounds (D6).

    Read-only for the salesperson: earlier rounds render grey beside the new
    ones, and closing one is marketing's to do.
    """
    request_id = validate_uuid_path(request_id, resource="Price tag request")
    _assert_visible(db, token.contact_id)
    req = _require_own_request(db, token, request_id)
    return price_tag_review_service.to_responses(
        db, price_tag_review_service.list_comments(db, req.id)
    )


# ---------------------------------------------------------------------------
# Collect (the office hand-over, r9 S3/D8)
# ---------------------------------------------------------------------------


@router.post("/submissions/price_tag_request/{request_id}/collect")
def portal_collect_price_tag_request(
    request_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """The salesperson confirming they have the tags (D8).

    The same transition the office's own Mark collected makes, recorded against
    the CONTACT rather than a user: whoever closed it is what the card says
    afterwards, and the two are different people.
    """
    request_id = validate_uuid_path(request_id, resource="Price tag request")
    _assert_visible(db, token.contact_id)
    req = _require_own_request(db, token, request_id)

    result = PriceTagRequestService.transition_status(db, req.id, STATUS_COLLECTED)
    result.collected_by_contact_id = token.contact_id
    result.collected_by_user_id = None
    db.flush()
    db.commit()
    return {"status": result.status}


# ---------------------------------------------------------------------------
# Debtor lookup
# ---------------------------------------------------------------------------


@router.get("/lookups/debtors-for-agent", response_model=list[DebtorForAgentItem])
def portal_lookup_debtors_for_agent(
    q: Optional[str] = Query(None),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """Scoped debtor lookup: customers by sales_agent_id + orders.

    Gated like every other route here, and it was the one that was not: a contact
    whose grant had been taken away could still read out the whole debtor book of
    the agent they are linked to - names, codes and who buys from whom - through
    a form they are no longer allowed to open.
    """
    _assert_visible(db, token.contact_id)
    debtors = PriceTagRequestService.lookup_debtors_for_agent(db, token.contact_id)
    if q:
        q_lower = q.lower()
        debtors = [
            d for d in debtors
            if q_lower in (d.get("customer_name") or "").lower()
            or q_lower in (d.get("customer_code") or "").lower()
        ]
    return [DebtorForAgentItem(**d) for d in debtors]


# ---------------------------------------------------------------------------
# Item lookup: sets and products in one list
# ---------------------------------------------------------------------------


@router.get("/lookups/price-tag-items", response_model=list[TagItemLookupItem])
def portal_lookup_tag_items(
    q: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=50),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """What the lines table's single Item dropdown reads (D47).

    Gated the same way as every other price tag route: a contact who cannot see the
    form cannot search the catalogue through it either.

    Scoped to the SAME company ``_resolve_company`` will stamp the request with -
    the ordinary portal-token company scope covers every company the contact
    belongs to, so a contact shared between two companies (a duplicated product
    catalogue) would otherwise see each code twice, and could pick the wrong
    company's row onto a request already stamped with the other one.
    """
    _assert_visible(db, token.contact_id)
    with company_scope(db, frozenset({_resolve_company(db, token)})):
        return [
            TagItemLookupItem(**item)
            for item in PriceTagRequestService.lookup_tag_items(db, q, limit=limit)
        ]


@router.get("/lookups/product-combos/{product_id}")
def portal_lookup_product_combos(
    product_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """What the picked product is sold as, plus whether its class is guarded (D2).

    Called once per product pick on a line. Same `_assert_visible` gate and the
    same company scope as `price-tag-items` above: a contact who cannot see the
    form cannot read the catalogue's packaging through it either, and a combo on
    another company's copy of the code is not theirs.

    `host_guarded` rides along rather than being a second endpoint. The form has
    to compute the SAME warning the submit guard computes, and that needs to know
    whether this product's `class_label` is in `price_tag_guarded_classes` -
    answering it on the call the form is already making beats both a round trip
    and shipping the tenant's settings list out to the portal. The server
    evaluates the same list the guard evaluates, so the two cannot disagree.
    """
    from app.models.product import Product, ProductCategory
    from app.models.product_combo import ProductCombo, ProductComboPart

    # Same first line as every other portal id route: a non-UUID path id reaches
    # Postgres as a comparison it refuses, which is a 500 where a 404 belongs.
    product_id = validate_uuid_path(product_id, resource="Product")
    _assert_visible(db, token.contact_id)
    with company_scope(db, frozenset({_resolve_company(db, token)})):
        product = db.query(Product).filter(Product.id == product_id).first()
        if product is None:
            raise handle_not_found("Product", product_id)

        category = (
            db.query(ProductCategory)
            .filter(ProductCategory.id == product.category_id)
            .first()
        )
        guarded = PriceTagRequestService.guarded_classes(db)
        host_guarded = bool(category and category.class_label in guarded)

        combos = (
            db.query(ProductCombo)
            .filter(ProductCombo.host_product_id == product_id)
            .order_by(ProductCombo.sort_order, ProductCombo.name)
            .all()
        )
        payload = []
        for combo in combos:
            parts = sorted(combo.parts or [], key=lambda p: (p.sort_order or 0, p.id))
            payload.append(
                {
                    "combo_id": combo.id,
                    "name": combo.name,
                    "parts": [
                        {
                            "product_id": part.part_product_id,
                            "code": (
                                part.part_product.product_code
                                if part.part_product is not None
                                else ""
                            ),
                            "name": (
                                part.part_product.product_name
                                if part.part_product is not None
                                else ""
                            ),
                            "choice_group": part.choice_group,
                        }
                        for part in parts
                    ],
                }
            )
        return {"host_guarded": host_guarded, "combos": payload}


# ---------------------------------------------------------------------------
# Line pricing (D1-D4, S7). Replaces the retired promotion dropdown lookup -
# a line's own Promotion select reads THIS, not a flat promotion list, since
# which promotions are even offered now depends on what is on the line.
# ---------------------------------------------------------------------------


@router.post("/lookups/line-pricing", response_model=list[LinePricingRow])
def portal_line_pricing(
    payload: LinePricingRequest,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """What every line of the form currently in progress costs THIS contact.

    Gated the same way as every other price tag route: a contact who cannot
    see the form cannot price one through it either. Audience-scoped to this
    contact's own access codes (AC-S7-2), same rule the retired promotion
    dropdown enforced. Scoped to the same company ``_resolve_company`` would
    stamp the request with, same reasoning as the other lookups.
    """
    _assert_visible(db, token.contact_id)
    from app.services.dealer_kit.pricing import line_pricing

    with company_scope(db, frozenset({_resolve_company(db, token)})):
        rows = line_pricing(
            db,
            lines=[line.model_dump() for line in payload.lines],
            viewer=tag_data_service.contact_viewer(db, token.contact_id),
        )
    return [LinePricingRow(**row) for row in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _require_own_request(db: Session, token: PortalToken, request_id: str):
    """The contact's own request, or a 404. Another contact's is not theirs to see.

    Gap B (security review of S10): also gates on form visibility, like every
    other price_tag_request route (``_assert_visible``) - this helper is what
    the generic revision routes in portal.py dispatch ownership to
    (``_require_revisable_ownership`` / ``_revision_submission_detail``), and
    those never carried an equivalent check of their own, so a contact whose
    grant was revoked could still list/revise/save-draft their own old
    request. Gap E: validates the id is a UUID first, same as every other
    caller here does before reaching ``get_request``, so a malformed id 404s
    instead of a driver 500.
    """
    request_id = validate_uuid_path(request_id, resource="Price tag request")
    _assert_visible(db, token.contact_id)
    req = PriceTagRequestService.get_request(db, request_id)
    if not req or req.contact_id != token.contact_id:
        raise AppException(
            status_code=404,
            message="Price tag request not found.",
            code="NOT_FOUND",
        )
    return req


def price_tag_neighbours(db: Session, token: PortalToken, request_id: str) -> dict:
    """Prev/next over the contact's OWN price tag requests, newest first -
    same ordering ``PriceTagRequestService.list_requests`` uses (review round
    3). Called from the generic ``/submissions/{kind}/{id}/neighbours`` route
    in portal.py, dispatched the same way ownership/detail already are for
    this kind (``_require_revisable_ownership`` / ``_revision_submission_detail``).
    """
    from app.models.price_tag import PriceTagRequest

    # ownership + visibility + uuid validation; raises on miss
    req = _require_own_request(db, token, request_id)
    ids = [
        str(r[0])
        for r in db.query(PriceTagRequest.id)
        .filter(PriceTagRequest.contact_id == token.contact_id)
        .order_by(PriceTagRequest.created_at.desc())
        .all()
    ]
    try:
        idx = ids.index(str(req.id))
    except ValueError:
        # Unreachable in practice - _require_own_request above already
        # confirmed ownership - but fail closed rather than raise unhandled.
        raise AppException(
            status_code=404,
            message="Price tag request not found.",
            code="NOT_FOUND",
        )
    total = len(ids)
    return {
        "prev_id": ids[idx - 1] if idx > 0 else None,
        "next_id": ids[idx + 1] if idx + 1 < total else None,
        "position": idx + 1,
        "total": total,
    }


def _require_draft(req, message: str, code: str = "NOT_DRAFT") -> None:
    """A draft is ``portal_draft_at``, and nothing else (D48c).

    This used to also pass anything whose status was still `new`, which is the
    status a submitted request keeps until marketing claims it: so a submitted
    request could be edited, and submitted again, firing the form SLA a second
    time. The timestamp is the only thing that tells the two apart.
    """
    if req.portal_draft_at is None:
        raise AppException(status_code=409, message=message, code=code)


def _require_editable(req, db: Session) -> None:
    """R3-1: the attachment gate (upload/delete). A draft is always editable;
    a submitted request is editable while the revision policy currently
    allows a revision for it - the same check ``revise``/``save_draft`` make
    (``PortalRevisionService.policy_for``), never a coincidental status check
    or the existence of a revision DRAFT row. Review round 3: keying this off
    ``get_draft`` 409'd every attachment added mid-revision, since
    ``PriceTagRequestForm`` composes a revision inline (reason + sections)
    and never writes a ``PortalRevisionDraft`` row for it - that row is only
    ever written by Save (as opposed to Send) on a revision draft.

    Gap C (security review of S10) still holds with this shape: the policy is
    re-checked against the request's CURRENT status on every call, so a
    request that has moved on to ``ready``/``void`` since a revision was
    last open refuses attachments outright, same as before.
    """
    if req.portal_draft_at is not None:
        return
    from app.services.portal_revision_service import PortalRevisionService

    if PortalRevisionService(db).policy_for("price_tag_request", req.id).allowed:
        return
    raise AppException(
        status_code=409,
        message="This request can no longer be edited.",
        code="NOT_EDITABLE",
    )


def _detail_body(db: Session, req) -> dict:
    """The request with its lines AND its PO attachments resolved.

    ``response_with_resolved_lines`` (the same call the CRM detail route makes,
    D49) already fills ``attachments`` via
    ``entity_attachment_service.list_attachments_for_entity`` - real rows once
    the PO dropzone has uploaded any, an empty list otherwise. The portal form
    reads the key unconditionally, so it always has to be present.

    R3-1/AC-R7: ``revision`` (the policy block: allowed, remaining, blocked
    reason) and ``revision_draft`` (the in-progress revise composer, if any)
    ride along too, like the legacy kinds' detail bodies do - one call, no
    extra round trip.
    """
    from app.services.portal_revision_service import PortalRevisionService

    body = PriceTagRequestService.response_with_resolved_lines(db, req).model_dump(
        mode="json"
    )
    revision_service = PortalRevisionService(db)
    body["revision"] = revision_service.policy_for("price_tag_request", req.id).as_dict()
    body["revision_draft"] = revision_service.get_draft("price_tag_request", req.id)
    return body


def _resolve_company(db: Session, token: PortalToken) -> str:
    """The one company this contact's price tag work runs under.

    Every price tag route and AI Extract (``ai_extract.py``) scope their reads
    with this and stamp new requests with it, so it is the single place the
    rule lives. It is the token's own contact's ACTIVE companies
    (``respond_contact_companies``, the "Companies" field on Edit Contact):

    - one company: that company;
    - several, the incumbent default among them: the default, the same
      "never an arbitrary pick" rule ``company_routing_service`` applies;
    - several without the default: 409, the contact must be narrowed first;
    - none: 422, an admin has to link the contact to a company.

    It used to be ``Company.first()`` with no ``ORDER BY``, so physical row order
    picked the company and a Sorento salesman searched Mocha's catalogue
    (PRICE-TAG-COMPANY).
    """
    from app.models.company import Company, RespondContactCompany
    from app.services.company_scope import DEFAULT_COMPANY_ID

    company_ids = {
        str(cid)
        for (cid,) in db.query(RespondContactCompany.company_id)
        .join(Company, Company.id == RespondContactCompany.company_id)
        .filter(
            RespondContactCompany.respond_contact_id == str(token.contact_id),
            Company.is_active.is_(True),
        )
        .all()
    }
    if len(company_ids) == 1:
        return next(iter(company_ids))
    if DEFAULT_COMPANY_ID in company_ids:
        return DEFAULT_COMPANY_ID
    if company_ids:
        raise AppException(
            status_code=409,
            message=(
                "Your account belongs to more than one company. Ask your admin "
                "to set the one company your price tag requests are for."
            ),
            code="CONTACT_COMPANY_AMBIGUOUS",
        )
    raise AppException(
        status_code=422,
        message=(
            "Your account is not linked to a company yet. Ask your admin to add "
            "your company on your contact, then try again."
        ),
        code="CONTACT_HAS_NO_COMPANY",
    )
