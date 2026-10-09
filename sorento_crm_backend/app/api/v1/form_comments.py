"""Staff routes for the comment thread on a submitted form.

GET|POST /form-comments/{kind}/{entity_id}. The kind's own view permission gates both verbs
(there is no single slug that covers four kinds), and POST needs a staff session (no API key).
Plan: documentation/plans/forms/PLAN-form-comments-09oct.md
"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.services.error_handler import handle_unprocessable
from app.services.form_comment_service import FORM_COMMENT_KINDS, FormCommentService
from app.services.user_service import UserPermissionService
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()

_VIEW_PERMISSION = {
    "complaint": "complaint_management.complaints.view",
    "stock_inquiry": "procurement.stock_inquiries.view",
    "purchase_request": "procurement.purchase_requests.view",
    "sponsorship_form": "procurement.sponsorship_forms.view",
}


def _authorize(kind: str, entity_id: str, current_user: dict, db: Session) -> str:
    if kind not in FORM_COMMENT_KINDS:
        raise handle_unprocessable(f"Unsupported form type: {kind!r}.")
    slug = _VIEW_PERMISSION[kind]
    if not UserPermissionService(db).check_user_has_permission(current_user["id"], slug):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Permission required: {slug}")
    return validate_uuid_path(entity_id, resource="Form")


@router.get("/{kind}/{entity_id}")
def list_form_comments(
    kind: str,
    entity_id: str,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    eid = _authorize(kind, entity_id, current_user, db)
    return {"items": FormCommentService(db).list_comments(kind, eid)}


@router.post("/{kind}/{entity_id}", status_code=status.HTTP_201_CREATED)
async def add_form_comment(
    kind: str,
    entity_id: str,
    body: Annotated[str, Form()] = "",
    files: Annotated[Optional[list[UploadFile]], File()] = None,
    current_user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    eid = _authorize(kind, entity_id, current_user, db)
    user = db.query(User).filter(User.id == current_user["id"]).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    uploads = [(f.filename, await f.read(), f.content_type) for f in (files or []) if f.filename]
    return FormCommentService(db).add_staff_comment(kind, eid, user, body, uploads)
