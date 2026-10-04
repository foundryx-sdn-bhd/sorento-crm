"""The customer's counter-sign page, reached by a tokenised link.

Public on purpose, and narrow on purpose. The token identifies ONE issue, expires, and is the only
credential; nothing here takes an id from the caller, so a leaked link exposes exactly the one
quotation it was minted for and nothing else in the project.

An unknown token and an expired token get the SAME answer. Distinguishing them would confirm to
anybody guessing that a token exists.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, status
from fastapi import Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.projects import (
    QuotationSignAcceptRequest,
    QuotationSignChangesRequest,
    QuotationSignPageResponse,
)
from app.services import project_quotation_document_service as svc
from app.services.client_ip import client_ip
from app.services.error_handler import handle_internal_error

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{token}", response_model=QuotationSignPageResponse)
async def read_quotation_for_signing(
    token: str,
    db: Session = Depends(get_db),
):
    """What the customer sees: the quotation as issued, read-only.

    Rendered from the ISSUE, not from live rows, so the page cannot show them something different
    from the PDF they were sent.
    """
    try:
        record = svc.get_issue_by_sign_token(db, token)
        return svc.serialize_sign_page(db, record)
    except Exception as exc:
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/{token}/accept", response_model=QuotationSignPageResponse)
async def accept_quotation(
    token: str,
    payload: QuotationSignAcceptRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """The customer signs, and that WINS the quotation.

    Client decision: a counter-signature is the commitment, so every scope the issue carried is
    marked won and the project's outcome follows. Idempotent, because a double-tap is not two
    agreements.
    """
    try:
        record = svc.get_issue_by_sign_token(db, token)
        svc.accept_issue(
            db,
            record=record,
            signer_name=payload.signer_name,
            mode=payload.mode,
            image_data_uri=payload.image_data_uri,
            ip_address=client_ip(request),
            user_agent=request.headers.get("user-agent"),
            gps_lat=payload.gps_lat,
            gps_lng=payload.gps_lng,
        )
        db.commit()
        return svc.serialize_sign_page(db, record)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))


@router.post("/{token}/request-changes", response_model=QuotationSignPageResponse)
async def request_quotation_changes(
    token: str,
    payload: QuotationSignChangesRequest,
    db: Session = Depends(get_db),
):
    """The customer will not sign it as it stands, and says why.

    The feedback is captured and the salesperson revises by hand - the system deliberately does
    not open a revision, because a customer asking for a lower price has not been given one.

    Answers the page, not an acknowledgement, for the same reason `accept` does: the browser has
    to settle on the new state without a second fetch. Refused on an already-accepted quotation
    (409): the counter-signature won every scope, and a record that says both is unreadable.
    """
    try:
        record = svc.get_issue_by_sign_token(db, token)
        svc.request_changes(
            db,
            record=record,
            note=payload.note,
            requester_name=payload.requester_name,
        )
        db.commit()
        return svc.serialize_sign_page(db, record)
    except Exception as exc:
        db.rollback()
        raise exc if hasattr(exc, "status_code") else handle_internal_error(str(exc))
