"""`/api/v1/ideation/portal/*`: the Roadmap portal gateway (PLAN-idea-crm-side-13oct, AC-A-05..A-10).

Same rules as `ideas.py`, over the ss portal API (`/portal/ideas/*`, a separate portal token the
gateway exchanges from the user's embed token, `ideation_gateway_service.call_portal`): view =
`ideation.board.view`, manage = `ideation.ideas.manage`; reads accept a session or an act-as API
key, writes need a staff session; every path id is a UUID; query params come from an explicit list
per route (`productId` is never forwarded: the connection is scoped to the workspace's product);
bodies are rebuilt from field lists. Comment delete is the `idea_comment.delete` pending action.
"""
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.v1.ideation.ideas import (
    MANAGE,
    VIEW,
    _comment_body,
    _masked_comment,
    _pick,
)
from app.database import get_db
from app.dependencies import require_permission, require_permission_with_api_key
from app.services.error_handler import AppException
from app.services.ideation_gateway_service import call_portal, relay, relay_masked, ss_path

NOTE_MAX_CHARS = 500
_LIST_PARAMS = ("q", "column", "sort", "mine", "includeClosed", "page", "pageSize")

router = APIRouter()

_read = Depends(require_permission_with_api_key(VIEW))
_write = Depends(require_permission(VIEW))
_manage = Depends(require_permission(MANAGE))


def _forward(db: Session, user: dict, method: str, path: str, **kw: Any) -> Response:
    return relay(call_portal(db, user, method, path, **kw))


def _query(request: Request, allowed: tuple[str, ...]) -> list[tuple[str, str]]:
    """Only the listed query params, repeats kept (`column` is multi-valued)."""
    return [(k, v) for k, v in request.query_params.multi_items() if k in allowed]


def _idea_path(idea_id: UUID, *rest: str) -> str:
    return ss_path("portal", "ideas", str(idea_id), *rest)


# ---- reads ------------------------------------------------------------------------------------


@router.get("/roadmap")
def roadmap(request: Request, user: dict = _read, db: Session = Depends(get_db)):
    return _forward(db, user, "GET", ss_path("portal", "ideas", "roadmap"), params=_query(request, ("mine",)))


@router.get("/changelog")
def changelog(user: dict = _read, db: Session = Depends(get_db)):
    return _forward(db, user, "GET", ss_path("portal", "ideas", "changelog"))


@router.get("/ideas")
def list_ideas(request: Request, user: dict = _read, db: Session = Depends(get_db)):
    return _forward(db, user, "GET", ss_path("portal", "ideas"), params=_query(request, _LIST_PARAMS))


@router.get("/ideas/{idea_id}")
def get_idea(idea_id: UUID, user: dict = _read, db: Session = Depends(get_db)):
    return _forward(db, user, "GET", _idea_path(idea_id))


@router.get("/ideas/{idea_id}/voters")
def voters(idea_id: UUID, user: dict = _read, db: Session = Depends(get_db)):
    return relay_masked(call_portal(db, user, "GET", _idea_path(idea_id, "voters")), ("name",))


@router.get("/ideas/{idea_id}/activity")
def activity(idea_id: UUID, request: Request, user: dict = _read, db: Session = Depends(get_db)):
    resp = call_portal(db, user, "GET", _idea_path(idea_id, "activity"), params=_query(request, ("order",)))
    return relay_masked(resp, ("actorName",))


@router.get("/ideas/{idea_id}/comments")
def list_comments(idea_id: UUID, user: dict = _read, db: Session = Depends(get_db)):
    return relay_masked(call_portal(db, user, "GET", _idea_path(idea_id, "comments")), ("authorName",))


# ---- vote, comment, like (view, staff session) ------------------------------------------------


@router.post("/ideas/{idea_id}/vote")
def vote(idea_id: UUID, user: dict = _write, db: Session = Depends(get_db)):
    return _forward(db, user, "POST", _idea_path(idea_id, "vote"))


@router.post("/ideas/{idea_id}/comments", status_code=201)
def post_comment(idea_id: UUID, payload: dict = Body(...), user: dict = _write, db: Session = Depends(get_db)):
    resp = call_portal(db, user, "POST", _idea_path(idea_id, "comments"), json=_comment_body(payload))
    return _masked_comment(resp)


@router.post("/ideas/{idea_id}/comments/{comment_id}/like")
def like_comment(idea_id: UUID, comment_id: UUID, user: dict = _write, db: Session = Depends(get_db)):
    return _forward(db, user, "POST", _idea_path(idea_id, "comments", str(comment_id), "like"))


# ---- manager controls (manage, staff session) -------------------------------------------------


@router.put("/ideas/{idea_id}/hidden")
def set_hidden(idea_id: UUID, payload: dict = Body(...), user: dict = _manage, db: Session = Depends(get_db)):
    if not isinstance(payload.get("hidden"), bool):
        raise AppException(422, "hidden must be true or false.", code="VALIDATION_ERROR")
    return _forward(db, user, "PUT", _idea_path(idea_id, "hidden"), json=_pick(payload, ("hidden",)))


@router.put("/ideas/{idea_id}/note")
def set_note(idea_id: UUID, payload: dict = Body(...), user: dict = _manage, db: Session = Depends(get_db)):
    note = payload.get("note")
    if not isinstance(note, str) or len(note) > NOTE_MAX_CHARS:
        raise AppException(422, f"note must be text of at most {NOTE_MAX_CHARS} characters.", code="VALIDATION_ERROR")
    return _forward(db, user, "PUT", _idea_path(idea_id, "note"), json=_pick(payload, ("note",)))
