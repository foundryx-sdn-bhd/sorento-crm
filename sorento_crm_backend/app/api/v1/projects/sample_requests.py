"""Sample request staff API (SR-CORE): the consignment requisition.

Rules live in `sample_request_service`; this module only resolves who is asking
(owner / `.view` / `.process`) and maps the HTTP shape. Mounted before the projects
router so `/projects/{id}/sample-requests` and the literal `/sample-requests/products`
are not captured by a `{...}` path.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.v1.projects._common import permission_slugs
from app.database import get_db
from app.dependencies import get_current_user, require_permission
from app.schemas.sample_request import (
    SampleRequestInternal,
    SampleRequestMove,
    SampleRequestWrite,
)
from app.services import sample_request_service as svc
from app.services.error_handler import AppException
from app.services.uuid_path_param import validate_uuid_path
from app.utils.http import content_disposition

router = APIRouter()

VIEW = "projects.sample_requests.view"
CREATE = "projects.sample_requests.create"
PROCESS = "projects.sample_requests.process"


def _actor(db: Session, user: dict, slugs=None) -> svc.Actor:
    slugs = permission_slugs(db, user["id"]) if slugs is None else slugs
    return svc.Actor(
        user_id=user["id"], can_process=PROCESS in slugs, can_view_all=VIEW in slugs
    )


def _list_actor(db: Session, user: dict) -> svc.Actor:
    """The queue and the project tab: `.view` sees all, `.create` sees own rows."""
    slugs = permission_slugs(db, user["id"])
    actor = _actor(db, user, slugs)
    if not (actor.can_view_all or CREATE in slugs):
        raise AppException(
            status_code=403, message=f"Permission required: {VIEW}", code="FORBIDDEN"
        )
    return actor


def _load(db: Session, request_id: str):
    return svc.get_for(db, validate_uuid_path(request_id, resource="Sample request"))


def _statuses(raw: Optional[str]):
    if raw is None:
        return list(svc.DEFAULT_OPEN)
    return [s.strip() for s in raw.split(",") if s.strip()]


@router.get("/sample-requests/products")
def search_products(
    q: str = Query(""),
    _user: dict = Depends(require_permission(CREATE)),
    db: Session = Depends(get_db),
):
    return svc.search_products(db, q)


@router.get("/sample-requests")
def list_requests(
    status_filter: Optional[str] = Query(None, alias="status"),
    project_id: Optional[str] = Query(None),
    salesperson_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    sort: Optional[str] = Query(None),
    dir: str = Query("asc"),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    actor = _list_actor(db, user)
    return svc.list_for(
        db,
        actor=actor,
        statuses=_statuses(status_filter),
        project_id=validate_uuid_path(project_id, resource="Project") if project_id else None,
        salesperson_id=salesperson_id or None,
        q=q,
        page=page,
        limit=limit,
        sort=sort,
        direction=dir,
    )


@router.get("/projects/{project_id}/sample-requests")
def list_for_project(
    project_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    q: Optional[str] = Query(None),
    sort: Optional[str] = Query(None),
    dir: str = Query("asc"),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """The project tab: every status of that project's requests."""
    actor = _list_actor(db, user)
    return svc.list_for(
        db,
        actor=actor,
        statuses=None,
        project_id=validate_uuid_path(project_id, resource="Project"),
        q=q,
        page=page,
        limit=limit,
        sort=sort,
        direction=dir,
    )


@router.post("/sample-requests", status_code=status.HTTP_201_CREATED)
def create_request(
    payload: SampleRequestWrite,
    user: dict = Depends(require_permission(CREATE)),
    db: Session = Depends(get_db),
):
    try:
        req = svc.create_draft(
            db,
            salesperson_id=user["id"],
            data=payload.model_dump(exclude_unset=True),
            source="staff",
        )
        db.commit()
        db.refresh(req)
        return svc.serialize_detail(db, req)
    except Exception:
        db.rollback()
        raise


@router.get("/sample-requests/{request_id}")
def get_request(
    request_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = _load(db, request_id)
    svc.assert_can_view(req, _actor(db, user))
    return svc.serialize_detail(db, req)


@router.put("/sample-requests/{request_id}")
def update_request(
    request_id: str,
    payload: SampleRequestWrite,
    user: dict = Depends(require_permission(CREATE)),
    db: Session = Depends(get_db),
):
    try:
        req = _load(db, request_id)
        svc.update_draft(
            db, req, actor=_actor(db, user), data=payload.model_dump(exclude_unset=True)
        )
        db.commit()
        db.refresh(req)
        return svc.serialize_detail(db, req)
    except Exception:
        db.rollback()
        raise


@router.post("/sample-requests/{request_id}/submit")
def submit_request(
    request_id: str,
    user: dict = Depends(require_permission(CREATE)),
    db: Session = Depends(get_db),
):
    try:
        req = _load(db, request_id)
        svc.submit(db, req, actor=_actor(db, user))
        db.commit()
        db.refresh(req)
        return svc.serialize_detail(db, req)
    except Exception:
        db.rollback()
        raise


@router.post("/sample-requests/{request_id}/move")
def move_request(
    request_id: str,
    payload: SampleRequestMove,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        slugs = permission_slugs(db, user["id"])
        if not (CREATE in slugs or PROCESS in slugs):
            raise AppException(
                status_code=403,
                message=f"Permission required: {CREATE} or {PROCESS}",
                code="FORBIDDEN",
            )
        actor = _actor(db, user, slugs)
        req = _load(db, request_id)
        svc.assert_can_view(req, actor)
        svc.move(
            db,
            req,
            actor=actor,
            to=payload.to,
            on=payload.date,
            reason=payload.reason,
        )
        db.commit()
        db.refresh(req)
        return svc.serialize_detail(db, req)
    except Exception:
        db.rollback()
        raise


@router.put("/sample-requests/{request_id}/internal")
def update_internal(
    request_id: str,
    payload: SampleRequestInternal,
    user: dict = Depends(require_permission(PROCESS)),
    db: Session = Depends(get_db),
):
    try:
        req = _load(db, request_id)
        actor = _actor(db, user)
        svc.assert_can_view(req, actor)
        svc.update_internal(
            db,
            req,
            actor=actor,
            consignment_no=payload.consignment_no,
            po_no=payload.po_no,
        )
        db.commit()
        db.refresh(req)
        return svc.serialize_detail(db, req)
    except Exception:
        db.rollback()
        raise


@router.get("/sample-requests/{request_id}/pdf")
def print_request(
    request_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    req = _load(db, request_id)
    svc.assert_can_view(req, _actor(db, user))
    return Response(
        content=svc.render_pdf(db, req),
        media_type="application/pdf",
        headers={"Content-Disposition": content_disposition(f"{req.request_no}.pdf", inline=True)},
    )
