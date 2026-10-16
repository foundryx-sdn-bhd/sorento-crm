"""Quotation DOCUMENT API (S2 of the quotation rework).

The document is the letterhead the customer receives; the scopes under it are the tabs. URLs say
so: ``/projects/{id}/quotation-documents`` for the documents of one project, and
``/quotation-documents/{id}/scopes`` for its tabs. Lines keep hanging off a VERSION through the
existing ``quotations`` router, unchanged - "which version was this line on" is still the question
the model exists to answer, and an issue is a set of those versions.

Every rule lives in the service, not here: the 422 on editing an issued version, the refusal to
delete an issued document, and the exclusion of rate-only lines from every total. The route does
not second-guess any of them, so there is one place each is stated.
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.api.v1.projects._common import permission_slugs
from app.api.v1.projects.quotations import _notify_breaches
from app.database import get_db
from app.dependencies import require_permission, require_permission_with_api_key
from app.schemas.common import ListResponse
from app.schemas.projects import (
    ProjectQuotationDocumentCreate,
    QuotationApprovalMoveRequest,
    QuotationRejectRequest,
    QuotationReviseRequest,
    QuotationSignatureRequest,
    QuotationSignatureResponse,
    ProjectQuotationDocumentResponse,
    ProjectQuotationDocumentUpdate,
    ProjectQuotationIssueResponse,
    ProjectQuotationScopeCreate,
    ProjectQuotationScopeSummary,
    ProjectQuotationScopeUpdate,
)
from app.schemas.status import StatusGraphResponse
from app.services import project_quotation_approval_service as approvals
from app.services import project_quotation_document_service as svc
from app.services import project_service as projects
from app.services.client_ip import client_ip
from app.services.error_handler import AppException, handle_internal_error
from app.services.uuid_path_param import validate_uuid_path
from app.utils.http import content_disposition

logger = logging.getLogger(__name__)

router = APIRouter()

VIEW = "projects.projects.view"
EDIT = "projects.projects.edit"
DELETE = "projects.projects.delete"
# S16: the sales-manager grant on below-floor pricing. The whole access control on that
# decision, by the client's own choice - no team-tier resolution behind it.
APPROVE = "projects.quotations.approve"


def _envelope(data: List[dict]):
    return {
        "data": data,
        "pagination": {"total": len(data), "page": 1, "limit": max(len(data), 1)},
        "empty": not data,
    }


def _issue_or_404(db: Session, document, issue_id: str):
    """The revision, checked against THIS document rather than merely fetched by id.

    Without the check a known issue id exports through any document the caller may view,
    handing over a price list they were never shown.
    """
    record = next((row for row in svc.list_issues(db, document) if str(row.id) == issue_id), None)
    if record is None:
        from app.services.error_handler import AppException

        raise AppException(
            status_code=404, message="Revision not found.", code="quotation_issue_not_found"
        )
    return record


def _editable_project(db: Session, project_id: str, current_user: dict):
    validate_uuid_path(project_id, resource="Project")
    project = projects.get_project_or_404(db, project_id)
    projects.assert_can_edit_project(
        db, project, current_user["id"], permission_slugs(db, current_user["id"])
    )
    return project


# ------------------------------------------------------------------ documents


@router.get(
    "/projects/{project_id}/quotation-documents",
    response_model=ListResponse[ProjectQuotationDocumentResponse],
)
async def list_quotation_documents(
    project_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(project_id, resource="Project")
        projects.get_project_or_404(db, project_id)
        return _envelope(svc.serialize_documents(db, svc.list_documents(db, project_id)))
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/projects/{project_id}/quotation-documents",
    response_model=ProjectQuotationDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_quotation_document(
    project_id: str,
    payload: ProjectQuotationDocumentCreate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Arrives already filled in (AC-A2): reference, recipient and subject are all derived."""
    try:
        project = _editable_project(db, project_id, current_user)
        # The quotation form page (#1341) sends the scopes and their lines with the letterhead;
        # all of it lands in this one commit, or none of it does.
        document, lines = svc.create_document_with_scopes(
            db,
            project=project,
            actor_user_id=current_user["id"],
            payload=payload.model_dump(exclude_unset=True),
        )
        for line in lines:
            _notify_breaches(db, line, current_user["id"])
        db.commit()
        db.refresh(document)
        return svc.serialize_document(db, document)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get("/projects/{project_id}/quotation-documents/letter-templates")
async def get_quotation_letter_templates(
    project_id: str,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """The company's cover letter and terms for the create form's own tabs (#1341). Declared
    before ``/{document_id}`` so the literal segment is not read as a document id."""
    try:
        project = _editable_project(db, project_id, current_user)
        return svc.letter_templates(db, project=project)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.get(
    "/projects/{project_id}/quotation-documents/{document_id}",
    response_model=ProjectQuotationDocumentResponse,
)
async def get_quotation_document(
    project_id: str,
    document_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(project_id, resource="Project")
        validate_uuid_path(document_id, resource="Quotation")
        projects.get_project_or_404(db, project_id)
        # Scoped to the project in the lookup, so a document belonging to another project 404s
        # rather than leaking its recipient and totals to whoever guessed the id.
        document = svc.get_document_or_404(db, project_id, document_id)
        return svc.serialize_document(db, document)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.patch(
    "/projects/{project_id}/quotation-documents/{document_id}",
    response_model=ProjectQuotationDocumentResponse,
)
async def update_quotation_document(
    project_id: str,
    document_id: str,
    payload: ProjectQuotationDocumentUpdate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(document_id, resource="Quotation")
        _editable_project(db, project_id, current_user)
        document = svc.get_document_or_404(db, project_id, document_id)
        body = payload.model_dump(exclude_unset=True)
        scopes = body.pop("scopes", None)
        # Normalised and de-duplicated before any write: a repeated id is one removal.
        removed = list(
            dict.fromkeys(
                validate_uuid_path(str(scope_id), resource="Scope")
                for scope_id in body.pop("remove_scope_ids", None) or []
            )
        )
        if removed:
            # A hard delete of the scope, its versions and lines: the same grant the scope
            # DELETE route asks for, not merely edit.
            if DELETE not in permission_slugs(db, current_user["id"]):
                raise AppException(
                    status_code=403,
                    message="You can edit this quotation but not remove its scopes.",
                    code="quotation_scope_remove_forbidden",
                )
            edited = {str(item.get("id")).lower() for item in (scopes or []) if item.get("id")}
            if edited & set(removed):
                raise AppException(
                    status_code=422,
                    message="A scope cannot be removed and edited in the same save.",
                    code="quotation_scope_removed_and_edited",
                )
        svc.update_document(db, document=document, payload=body)
        # Edit quotation may delete a saved scope nothing in which was issued (#1341, Q2).
        if removed:
            svc.remove_form_scopes(db, document=document, scope_ids=removed)
        # The form's edit Save (#1341): header and scopes in ONE commit. A scope the customer
        # holds refuses new lines with the existing 422, and the header change goes back too.
        if scopes is not None:
            for item in scopes:
                if item.get("id"):
                    validate_uuid_path(str(item["id"]), resource="Scope")
            lines = svc.apply_form_scopes(
                db, document=document, actor_user_id=current_user["id"], scopes=scopes
            )
            for line in lines:
                _notify_breaches(db, line, current_user["id"])
        db.commit()
        db.refresh(document)
        return svc.serialize_document(db, document)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.delete(
    "/projects/{project_id}/quotation-documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_quotation_document(
    project_id: str,
    document_id: str,
    current_user: dict = Depends(require_permission(DELETE)),
    db: Session = Depends(get_db),
):
    """Hard delete, and refused by the service once anything has been issued (AC-A6)."""
    try:
        validate_uuid_path(document_id, resource="Quotation")
        _editable_project(db, project_id, current_user)
        document = svc.get_document_or_404(db, project_id, document_id)
        svc.delete_document(db, document)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


# --------------------------------------------------------------------- scopes


@router.post(
    "/projects/{project_id}/quotation-documents/{document_id}/scopes",
    response_model=ProjectQuotationScopeSummary,
    status_code=status.HTTP_201_CREATED,
)
async def add_quotation_scope(
    project_id: str,
    document_id: str,
    payload: ProjectQuotationScopeCreate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """A tab, with its version 1 opened: a scope with no version has nowhere to put a line."""
    try:
        validate_uuid_path(document_id, resource="Quotation")
        _editable_project(db, project_id, current_user)
        document = svc.get_document_or_404(db, project_id, document_id)
        body = payload.model_dump(exclude_unset=True)
        scope = svc.add_scope(
            db,
            document=document,
            scope_label=body.pop("scope_label"),
            actor_user_id=current_user["id"],
            payload=body,
        )
        db.commit()
        db.refresh(scope)
        return svc._scope_summary(db, scope)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.patch(
    "/projects/{project_id}/quotation-documents/{document_id}/scopes/{quotation_id}",
    response_model=ProjectQuotationScopeSummary,
)
async def update_quotation_scope(
    project_id: str,
    document_id: str,
    quotation_id: str,
    payload: ProjectQuotationScopeUpdate,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(document_id, resource="Quotation")
        validate_uuid_path(quotation_id, resource="Scope")
        _editable_project(db, project_id, current_user)
        document = svc.get_document_or_404(db, project_id, document_id)
        scope = svc.get_scope_or_404(db, document, quotation_id)
        svc.update_scope(db, scope=scope, payload=payload.model_dump(exclude_unset=True))
        db.commit()
        db.refresh(scope)
        return svc._scope_summary(db, scope)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


# --------------------------------------------------------------------- issues


@router.get(
    "/projects/{project_id}/quotation-documents/{document_id}/issues",
    response_model=ListResponse[ProjectQuotationIssueResponse],
)
async def list_quotation_issues(
    project_id: str,
    document_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    try:
        validate_uuid_path(document_id, resource="Quotation")
        validate_uuid_path(project_id, resource="Project")
        projects.get_project_or_404(db, project_id)
        document = svc.get_document_or_404(db, project_id, document_id)
        rows = svc.list_issues(db, document)
        return _envelope([svc.serialize_issue(db, row) for row in rows])
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/projects/{project_id}/quotation-documents/{document_id}/issue",
    response_model=ProjectQuotationIssueResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_quotation_document(
    project_id: str,
    document_id: str,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Stamp R{n} and freeze what went out.

    After this, every version the issue names refuses line edits with 422
    ``quotation_version_issued`` - the customer is holding those rows, so a change opens the next
    revision instead of rewriting history under them.
    """
    try:
        validate_uuid_path(document_id, resource="Quotation")
        _editable_project(db, project_id, current_user)
        document = svc.get_document_or_404(db, project_id, document_id)
        record = svc.issue(db, document=document, actor_user_id=current_user["id"])
        db.commit()
        db.refresh(record)
        return svc.serialize_issue(db, record)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


# ----------------------------------------------------- price-floor approval (S14-S16)


@router.get("/quotation-approval-graph", response_model=StatusGraphResponse)
async def get_quotation_approval_graph(
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The `quotation` approval graph, readable by anyone who can see a project.

    Its own route rather than the admin ``/statuses/graph/{entity_type}`` one, which is gated on
    `system.statuses.view` and held by administrators alone. A salesperson has to be able to read
    the rung their own quotation stands on and the LABEL of the move out of it, or the block on
    the quotation screen can only ever offer a hardcoded button that an admin renaming the edge
    cannot change. Same response shape, so the frontend's shared status-move helpers read it
    unchanged.

    Read-only, and the graph is edited where every other graph is edited: Setup > Status Graphs.
    """
    try:
        graph = approvals.graph(db)
        return {
            "entity_type": graph.entity_type,
            "requested_scope_id": None,
            "resolved_scope_id": graph.resolved_scope_id,
            "is_fork": graph.is_fork,
            "statuses": graph.statuses,
            "transitions": graph.transitions,
        }
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/projects/{project_id}/quotation-documents/{document_id}/approval-status",
    response_model=ProjectQuotationDocumentResponse,
)
async def move_quotation_approval(
    project_id: str,
    document_id: str,
    payload: QuotationApprovalMoveRequest,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """The salesperson's own two moves: ask for approval, or take a rejected one back to draft.

    Both are edits to their own quotation, so the edit grant is the whole check. Approving and
    rejecting have their own routes below even though their edges are on the same graph: the
    service refuses them here (422 ``quotation_status_not_self_serve``), because reaching them
    through a route that asks for neither the permission nor the reason would make both rules
    decorative.
    """
    try:
        validate_uuid_path(document_id, resource="Quotation")
        _editable_project(db, project_id, current_user)
        document = svc.get_document_or_404(db, project_id, document_id)
        approvals.move(
            db,
            document=document,
            to_status_id=payload.to_status_id,
            actor_user_id=current_user["id"],
        )
        db.commit()
        # After the commit, best effort: a notification fault never undoes the status.
        approvals.notify_requested(db, document, current_user["id"])
        db.refresh(document)
        return svc.serialize_document(db, document)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/projects/{project_id}/quotation-documents/{document_id}/approve",
    response_model=ProjectQuotationDocumentResponse,
)
async def approve_quotation_document(
    project_id: str,
    document_id: str,
    current_user: dict = Depends(require_permission(APPROVE)),
    db: Session = Depends(get_db),
):
    """A manager accepts the below-floor pricing; the next Issue press then proceeds.

    Gated on the approve slug and NOT on project edit rights: a sales manager decides on
    quotations they do not own, which is the entire point of sending it to them. The project is
    still resolved so a document id from another project cannot be decided through a URL that
    names this one.
    """
    try:
        validate_uuid_path(project_id, resource="Project")
        validate_uuid_path(document_id, resource="Quotation")
        projects.get_project_or_404(db, project_id)
        document = svc.get_document_or_404(db, project_id, document_id)
        approvals.approve(
            db,
            document=document,
            actor_user_id=current_user["id"],
            permissions=permission_slugs(db, current_user["id"]),
        )
        db.commit()
        approvals.notify_decided(db, document, current_user["id"], approved=True)
        db.refresh(document)
        return svc.serialize_document(db, document)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/projects/{project_id}/quotation-documents/{document_id}/reject",
    response_model=ProjectQuotationDocumentResponse,
)
async def reject_quotation_document(
    project_id: str,
    document_id: str,
    payload: QuotationRejectRequest,
    current_user: dict = Depends(require_permission(APPROVE)),
    db: Session = Depends(get_db),
):
    """A manager sends it back, and the reason is required.

    Same grant as approve: deciding is one act with two answers, and a manager who may say yes
    may say no. The reason is stored on the document because the block on the salesperson's
    screen is where it has to be read.
    """
    try:
        validate_uuid_path(project_id, resource="Project")
        validate_uuid_path(document_id, resource="Quotation")
        projects.get_project_or_404(db, project_id)
        document = svc.get_document_or_404(db, project_id, document_id)
        approvals.reject(
            db,
            document=document,
            actor_user_id=current_user["id"],
            reason=payload.reason,
            permissions=permission_slugs(db, current_user["id"]),
        )
        db.commit()
        approvals.notify_decided(
            db, document, current_user["id"], approved=False, reason=document.approval_rejected_reason
        )
        db.refresh(document)
        return svc.serialize_document(db, document)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/projects/{project_id}/quotation-documents/{document_id}/approval-revise",
    response_model=ProjectQuotationDocumentResponse,
)
async def revise_quotation_approval(
    project_id: str,
    document_id: str,
    payload: QuotationReviseRequest,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """The creator reopens a pending or approved quotation, with a reason, to change it."""
    try:
        validate_uuid_path(document_id, resource="Quotation")
        _editable_project(db, project_id, current_user)
        document = svc.get_document_or_404(db, project_id, document_id)
        was_pending = approvals.current_key(db, document) == approvals.STATUS_PENDING
        approvals.revise_approval(db, document, current_user["id"], payload.reason)
        reason = " ".join((payload.reason or "").split())
        db.commit()
        if was_pending:
            approvals.notify_withdrawn(db, document, current_user["id"], reason)
        db.refresh(document)
        return svc.serialize_document(db, document)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


# --------------------------------------------------------------------- signing


@router.post(
    "/projects/{project_id}/quotation-documents/{document_id}/sign",
    response_model=QuotationSignatureResponse,
    status_code=status.HTTP_201_CREATED,
)
async def sign_quotation_document(
    project_id: str,
    document_id: str,
    payload: QuotationSignatureRequest,
    request: Request,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """Sign the draft, which is what makes it issuable (AC-H1).

    Separate from issuing on purpose: a person signs, checks the document, then issues. Signing
    again before issuing simply replaces the signature on the draft; a signature already carried by
    an issue is a copy and is never touched.
    """
    try:
        validate_uuid_path(document_id, resource="Quotation")
        _editable_project(db, project_id, current_user)
        document = svc.get_document_or_404(db, project_id, document_id)
        body = payload.model_dump(exclude_unset=True)
        body["ip_address"] = client_ip(request)
        body["user_agent"] = request.headers.get("user-agent")
        signature = svc.sign_as_sorento(
            db, document=document, actor_user_id=current_user["id"], payload=body
        )
        db.commit()
        db.refresh(signature)
        return svc._serialize_signature(signature)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/projects/{project_id}/quotation-documents/{document_id}/issues/{issue_id}/sign-link",
)
async def create_quotation_sign_link(
    project_id: str,
    document_id: str,
    issue_id: str,
    current_user: dict = Depends(require_permission(EDIT)),
    db: Session = Depends(get_db),
):
    """The tokenised link to send the customer.

    Reused while still valid, so re-sending a quotation does not invalidate the link already
    sitting in somebody's inbox.
    """
    try:
        validate_uuid_path(document_id, resource="Quotation")
        validate_uuid_path(issue_id, resource="Revision")
        _editable_project(db, project_id, current_user)
        document = svc.get_document_or_404(db, project_id, document_id)
        record = _issue_or_404(db, document, issue_id)
        token = svc.issue_sign_link(db, record=record)
        db.commit()
        return {
            "token": token,
            "path": f"/quotation-sign/{token}",
            "expires_at": record.sign_token_expires_at,
        }
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


# ------------------------------------------------------------------------ pdf


@router.get(
    "/projects/{project_id}/quotation-documents/{document_id}/issues/{issue_id}/pdf",
)
async def download_quotation_issue_pdf(
    project_id: str,
    document_id: str,
    issue_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The issued quotation, as the customer received it, rendered inline in this request.

    Rendered from the ISSUE snapshot every time rather than from live rows, so a download next year
    is what was sent. Generated on demand and never stored: the snapshot IS the source of truth, so
    a stored file would be a second copy of the same facts that could fall out of step with it.

    The CRM screen no longer calls this - a 50-page render held the browser long enough to read as
    a broken button, so the gear queues ``/export/pdf`` instead and the file arrives in My
    Downloads. This stays as the on-demand render for API/automation callers who want the bytes in
    the response, and it remains the only path that stores nothing.
    """
    from app.services.complaint_pdf_service import PDFRenderingUnavailable
    from app.services import project_quotation_pdf_service as pdf
    from app.services.error_handler import AppException

    try:
        validate_uuid_path(document_id, resource="Quotation")
        validate_uuid_path(issue_id, resource="Revision")
        validate_uuid_path(project_id, resource="Project")
        projects.get_project_or_404(db, project_id)
        document = svc.get_document_or_404(db, project_id, document_id)
        record = _issue_or_404(db, document, issue_id)
        try:
            pdf_bytes, filename = pdf.render_issue_pdf(db, record)
        except PDFRenderingUnavailable as unavailable:
            # A missing native library is an operational fact, not a bug in the request. Say so,
            # rather than letting it surface as an unexplained 500.
            raise AppException(
                status_code=503,
                message=(
                    "PDF rendering is not available on this server. "
                    f"Ask an administrator to install the rendering libraries. ({unavailable})"
                ),
                code="pdf_rendering_unavailable",
            )
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": content_disposition(filename, inline=True)},
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


# ---------------------------------------------------------------------- excel


@router.get(
    "/projects/{project_id}/quotation-documents/{document_id}/issues/{issue_id}/xlsx",
)
async def download_quotation_issue_xlsx(
    project_id: str,
    document_id: str,
    issue_id: str,
    _user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """The same issued quotation as a workbook, one sheet per scope (AC-F2).

    Sent as an ATTACHMENT rather than inline: nobody previews a spreadsheet in a browser tab, and
    an inline disposition on a binary the browser cannot render turns a download into a blank page.
    Built from the ISSUE snapshot on demand, exactly as the PDF is, so the two artifacts of one
    revision can never quote different money.

    As with the PDF route, the CRM screen now queues ``/export/xlsx`` instead; this remains the
    on-demand, stores-nothing render for API/automation callers.
    """
    from app.services import project_quotation_excel_service as excel

    try:
        validate_uuid_path(document_id, resource="Quotation")
        validate_uuid_path(issue_id, resource="Revision")
        validate_uuid_path(project_id, resource="Project")
        projects.get_project_or_404(db, project_id)
        document = svc.get_document_or_404(db, project_id, document_id)
        record = _issue_or_404(db, document, issue_id)
        payload, filename = excel.render_issue_xlsx(db, record)
        return Response(
            content=payload,
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            headers={"Content-Disposition": content_disposition(filename)},
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


# ------------------------------------------------------------- queued exports


def _queue_issue_export(
    db: Session,
    *,
    project_id: str,
    document_id: str,
    issue_id: str,
    current_user: dict,
    kind: str,
    suffix: str,
    task,
):
    """Create the ``pending`` download row and hand the render to the worker.

    One body for both formats: the only things that differ are the kind, the extension and the
    task, and duplicating the row-creation plus the enqueue-failure handling twice is how the
    two drift. The row is committed BEFORE the enqueue so the printer chip has something to
    show the instant the click returns, and a queue that cannot be reached marks that row
    failed rather than leaving it pending forever behind a spinner.
    """
    from app.schemas.download import DownloadResponse
    from app.services.company_scope import get_company_scope
    from app.services.download_service import DownloadService
    from app.services.queue_service import enqueue_job

    validate_uuid_path(document_id, resource="Quotation")
    validate_uuid_path(issue_id, resource="Revision")
    validate_uuid_path(project_id, resource="Project")
    projects.get_project_or_404(db, project_id)
    document = svc.get_document_or_404(db, project_id, document_id)
    record = _issue_or_404(db, document, issue_id)

    downloads = DownloadService(db)
    download = downloads.create(
        user_id=str(current_user["id"]),
        kind=kind,
        source_entity_type="quotation_issue",
        source_entity_id=str(record.id),
        # Named up front so the drawer reads as the quotation it is while still pending; the
        # task overwrites it with the renderer's own name once it knows it.
        filename=(
            f"quotation-{(record.our_ref_text or document.document_no or 'quotation')}"
            f".{suffix}"
        ).replace("/", "-"),
    )
    # Snapshot the enqueuer's active company: the worker runs at the fail-closed UNSET scope
    # and every quotation row is company-owned, so without this the render sees nothing.
    scope = get_company_scope(db)
    company_id = next(iter(scope)) if isinstance(scope, frozenset) and len(scope) == 1 else None
    try:
        enqueue_job(
            task,
            str(download.id),
            str(record.id),
            str(current_user["id"]),
            company_id=company_id,
            queue_name="imports",
            job_timeout=600,
        )
    except Exception as e:  # noqa: BLE001 - a queue outage is a failed row, not a stack trace
        downloads.mark_failed(str(download.id), f"Could not queue the export: {e}")
        raise handle_internal_error("Could not queue the export. Please try again.")

    return DownloadResponse.model_validate(downloads.get(str(download.id)))


@router.post(
    "/projects/{project_id}/quotation-documents/{document_id}/issues/{issue_id}/export/pdf",
)
async def queue_quotation_issue_pdf(
    project_id: str,
    document_id: str,
    issue_id: str,
    current_user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """Queue the issued quotation's PDF; it appears in My Downloads when it is ready.

    Same permission as the inline route: a quotation export is the full price list whether it
    arrives in the response or in a drawer ten seconds later.
    """
    from app.tasks.export_tasks import generate_quotation_issue_pdf

    try:
        return _queue_issue_export(
            db,
            project_id=project_id,
            document_id=document_id,
            issue_id=issue_id,
            current_user=current_user,
            kind="quotation_pdf",
            suffix="pdf",
            task=generate_quotation_issue_pdf,
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post(
    "/projects/{project_id}/quotation-documents/{document_id}/issues/{issue_id}/export/xlsx",
)
async def queue_quotation_issue_xlsx(
    project_id: str,
    document_id: str,
    issue_id: str,
    current_user: dict = Depends(require_permission_with_api_key(VIEW)),
    db: Session = Depends(get_db),
):
    """Queue the issued quotation's workbook; it appears in My Downloads when it is ready."""
    from app.tasks.export_tasks import generate_quotation_issue_xlsx

    try:
        return _queue_issue_export(
            db,
            project_id=project_id,
            document_id=document_id,
            issue_id=issue_id,
            current_user=current_user,
            kind="quotation_xlsx",
            suffix="xlsx",
            task=generate_quotation_issue_xlsx,
        )
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))
