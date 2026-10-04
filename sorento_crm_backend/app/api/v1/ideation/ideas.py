"""`/api/v1/ideation/*`: the Ideas gateway (PLAN-ideation-in-crm section 10, AC-A-01..A-12).

Each route checks the CRM permission, then forwards to the ss embed route of the same shape as
the calling user (`ideation_gateway_service`). View = `ideation.board.view`, manage =
`ideation.ideas.manage`. Reads accept a session or an act-as API key; WRITES need a staff session.

Three rules keep the gateway from being wider than its route table (AC-A-10, AC-A-11):
* every id in a path is a UUID (a bad one is a 422 before ss is called) and `call_ss` encodes
  each path segment, so an id can never add a path level, a query or a fragment;
* every body forwarded to ss is rebuilt from an explicit field list, never relayed as received;
* Archive and comment delete have no direct write here: only their pending actions do them.
"""
from typing import Any, Optional
from uuid import UUID

import httpx
from fastapi import APIRouter, Body, Depends, File, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_permission, require_permission_with_api_key
from app.services.error_handler import AppException
from app.services.user_service import UserPermissionService
from app.services.ideation_gateway_service import UNREACHABLE, call_ss, relay, ss_path

VIEW = "ideation.board.view"
MANAGE = "ideation.ideas.manage"
#: ss's own upload ceiling (ss `routers/embed.py`); read at most one byte past it.
ATTACHMENT_CAP_BYTES = 25 * 1024 * 1024
_STAFF_NAME = "Sorento staff"

router = APIRouter()

_read = Depends(require_permission_with_api_key(VIEW))
_write = Depends(require_permission(VIEW))
_manage = Depends(require_permission(MANAGE))


def _forward(db: Session, user: dict, method: str, path: str, **kw: Any) -> Response:
    return relay(call_ss(db, user, method, path, **kw))


def _pick(payload: dict, fields: tuple[str, ...]) -> dict:
    """Only the named fields, so a field ss added or a client invented never rides along."""
    return {k: payload[k] for k in fields if k in payload}


def _comment_body(payload: dict) -> dict:
    body = payload.get("body")
    if not isinstance(body, str) or not body.strip():
        raise AppException(422, "Comment cannot be empty.", code="VALIDATION_ERROR")
    return _pick(payload, ("body", "parentId"))


def _masked_comment(resp: httpx.Response) -> Response:
    """A comment answer with an email-shaped author name masked (AC-E-05 ship gate: ss stores the
    assertion's `name`, and until it stores the display name an address must not be echoed)."""
    try:
        data = resp.json()
    except ValueError:
        raise AppException(502, UNREACHABLE, code="IDEATION_UNREACHABLE")
    if isinstance(data, dict) and isinstance(data.get("authorName"), str) and "@" in data["authorName"]:
        data = {**data, "authorName": _STAFF_NAME}
    return JSONResponse(data, status_code=resp.status_code)


# ---- reads (the literal paths come before `/ideas/{id}`) --------------------------------------


@router.get("/ideas")
def list_ideas(request: Request, user: dict = _read, db: Session = Depends(get_db)):
    params = {k: v for k, v in request.query_params.items() if k in ("filter", "search")}
    if request.query_params.get("mine") == "true":
        params["mine"] = "true"
    return _forward(db, user, "GET", ss_path("embed", "ideas"), params=params)


@router.get("/ideas/board")
def get_board(user: dict = _read, db: Session = Depends(get_db)):
    return _forward(db, user, "GET", ss_path("embed", "board"))


@router.get("/ideas/{idea_id}")
def get_idea(idea_id: UUID, user: dict = _read, db: Session = Depends(get_db)):
    return _forward(db, user, "GET", ss_path("embed", "ideas", str(idea_id)))


@router.get("/ideas/{idea_id}/merged")
def get_merged(idea_id: UUID, user: dict = _read, db: Session = Depends(get_db)):
    return _forward(db, user, "GET", ss_path("embed", "ideas", str(idea_id), "merged"))


@router.get("/ideas/{idea_id}/attachments/{attachment_id}/content")
def attachment_content(
    idea_id: UUID, attachment_id: UUID, user: dict = _read, db: Session = Depends(get_db)
):
    path = ss_path("embed", "ideas", str(idea_id), "attachments", str(attachment_id), "content")
    return _forward(db, user, "GET", path)


@router.get("/ideas/{idea_id}/comments")
def list_comments(idea_id: UUID, user: dict = _read, db: Session = Depends(get_db)):
    return _forward(db, user, "GET", ss_path("embed", "ideas", str(idea_id), "comments"))


# ---- capture, vote, attach, comment (view, staff session) -------------------------------------


@router.post("/ideas", status_code=201)
def create_idea(payload: dict = Body(...), user: dict = _write, db: Session = Depends(get_db)):
    body = _pick(payload, ("problem", "proposedSolution", "impact", "department", "rawText"))
    return _forward(db, user, "POST", ss_path("embed", "ideas"), json=body)


@router.post("/ideas/{idea_id}/vote")
def vote(idea_id: UUID, user: dict = _write, db: Session = Depends(get_db)):
    # Upvote only: whatever the client sent, the direction is always up.
    return _forward(db, user, "POST", ss_path("embed", "ideas", str(idea_id), "vote"), json={"dir": "up"})


@router.post("/ideas/{idea_id}/attachments", status_code=201)
def upload_attachment(
    idea_id: UUID,
    file: UploadFile = File(...),
    user: dict = _write,
    db: Session = Depends(get_db),
):
    content = file.file.read(ATTACHMENT_CAP_BYTES + 1)
    if len(content) > ATTACHMENT_CAP_BYTES:
        raise AppException(413, "File is too large.", code="PAYLOAD_TOO_LARGE")
    files = {"file": (file.filename or "file", content, file.content_type or "application/octet-stream")}
    return _forward(db, user, "POST", ss_path("embed", "ideas", str(idea_id), "attachments"), files=files)


@router.post("/ideas/{idea_id}/comments", status_code=201)
def post_comment(idea_id: UUID, payload: dict = Body(...), user: dict = _write, db: Session = Depends(get_db)):
    resp = call_ss(
        db, user, "POST", ss_path("embed", "ideas", str(idea_id), "comments"), json=_comment_body(payload)
    )
    return _masked_comment(resp)


@router.patch("/ideas/{idea_id}/comments/{comment_id}")
def edit_comment(
    idea_id: UUID,
    comment_id: UUID,
    payload: dict = Body(...),
    user: dict = _write,
    db: Session = Depends(get_db),
):
    resp = call_ss(
        db,
        user,
        "PATCH",
        ss_path("embed", "ideas", str(idea_id), "comments", str(comment_id)),
        json=_comment_body(payload),
    )
    return _masked_comment(resp)


# ---- triage (manage, staff session) -----------------------------------------------------------


@router.patch("/ideas/{idea_id}")
def update_idea(idea_id: UUID, payload: dict = Body(...), user: dict = _write, db: Session = Depends(get_db)):
    # The product is the workspace's, never edited from here, so `productId` is not forwarded.
    body = _pick(payload, ("problem", "proposedSolution", "impact", "department", "rawText"))
    path = ss_path("embed", "ideas", str(idea_id))
    # A manager edits any idea. Anyone else (view only) edits only their own, and ss alone says
    # whose it is (`isMine`); a non-2xx on the lookup is relayed by `call_ss` (404 stays 404).
    if not UserPermissionService(db).check_user_has_permission(user["id"], MANAGE):
        try:
            idea = call_ss(db, user, "GET", path).json()
        except ValueError:
            raise AppException(502, UNREACHABLE, code="IDEATION_UNREACHABLE")
        if not (isinstance(idea, dict) and idea.get("isMine") is True):
            raise AppException(403, "You can only edit your own ideas.", code="FORBIDDEN")
    return _forward(db, user, "PATCH", path, json=body)


@router.post("/ideas/{idea_id}/status")
def move_status(idea_id: UUID, payload: dict = Body(...), user: dict = _manage, db: Session = Depends(get_db)):
    body = _pick(payload, ("status", "toStatusId"))
    # Archiving is the `idea.archive` pending action's job (5 s window), never a direct write.
    if str(body.get("status", "")).lower() == "archived":
        raise AppException(422, "Archive an idea from its menu.", code="VALIDATION_ERROR")
    return _forward(db, user, "POST", ss_path("embed", "ideas", str(idea_id), "status"), json=body)


@router.put("/ideas/reorder")
def reorder(payload: dict = Body(...), user: dict = _manage, db: Session = Depends(get_db)):
    return _forward(db, user, "PUT", ss_path("embed", "ideas", "reorder"), json=_pick(payload, ("orderedIds",)))


@router.post("/ideas/merge")
def merge(payload: dict = Body(...), user: dict = _manage, db: Session = Depends(get_db)):
    body = _pick(payload, ("survivorId", "ideaIds"))
    return _forward(db, user, "POST", ss_path("embed", "ideas", "merge"), json=body)


@router.post("/ideas/promote", status_code=201)
def promote(payload: dict = Body(...), user: dict = _manage, db: Session = Depends(get_db)):
    body = _pick(payload, ("ideaIds", "title"))
    return _forward(db, user, "POST", ss_path("embed", "ideas", "promote"), json=body)


@router.post("/ideas/{idea_id}/unmerge")
def unmerge(idea_id: UUID, user: dict = _manage, db: Session = Depends(get_db)):
    return _forward(db, user, "POST", ss_path("embed", "ideas", str(idea_id), "unmerge"))
