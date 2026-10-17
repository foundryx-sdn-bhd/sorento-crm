"""New product application staff API (NEW-PRODUCT-APP).

Rules live in `new_product_application_service`; this module resolves who is asking and maps
the HTTP shape. Every move commits first and notifies after (`svc.notify`), so a notification
failure never undoes the move. Literal paths (`/prefill`, `/products`, `/item-types`) are
declared before `/{application_id}`.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.projects._common import permission_slugs
from app.database import get_db
from app.dependencies import get_current_user, require_permission
from app.schemas.new_product_application import LinkIn, NewProductApplicationWrite, ReasonIn
from app.services import new_product_application_service as svc
from app.services.error_handler import AppException
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter()

_ANY = (svc.VIEW, svc.CREATE, svc.APPROVE, svc.OPEN_CODE)


def _actor(db: Session, user: dict) -> svc.Actor:
    return svc.actor_from_slugs(user["id"], permission_slugs(db, user["id"]))


def _any_actor(db: Session, user: dict) -> svc.Actor:
    slugs = permission_slugs(db, user["id"])
    if not set(slugs) & set(_ANY):
        raise AppException(
            status_code=403, message=f"Permission required: {svc.VIEW}", code="FORBIDDEN"
        )
    return svc.actor_from_slugs(user["id"], slugs)


def _load(db: Session, application_id: str, *, for_update: bool = False):
    return svc.get_for(
        db,
        validate_uuid_path(application_id, resource="New product application"),
        for_update=for_update,
    )


def _done(db: Session, app, event: Optional[str] = None, actor_id: Optional[str] = None):
    """Commit, then tell whoever now has to act, then answer with the fresh record."""
    db.commit()
    if event:
        svc.notify(db, app, event, actor_id=actor_id)
        db.commit()
    db.refresh(app)
    return svc.serialize_detail(db, app)


@router.get("/prefill")
def prefill(
    stock_inquiry_id: str = Query(...),
    _user: dict = Depends(require_permission(svc.CREATE)),
    db: Session = Depends(get_db),
):
    return svc.prefill(db, stock_inquiry_id)


@router.get("/products")
def search_products(
    q: str = Query(""),
    _user: dict = Depends(require_permission(svc.OPEN_CODE)),
    db: Session = Depends(get_db),
):
    return svc.search_products(db, q)


@router.get("/item-types")
def item_types(
    q: str = Query(""),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _any_actor(db, user)
    return svc.item_types(db, q)


@router.get("")
def list_applications(
    status_filter: Optional[str] = Query(None, alias="status"),
    q: Optional[str] = Query(None),
    stock_inquiry_id: Optional[str] = Query(None),
    applicant_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=200),
    sort: Optional[str] = Query(None),
    dir: str = Query("desc"),
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    statuses = [s.strip() for s in status_filter.split(",") if s.strip()] if status_filter else None
    return svc.list_for(
        db,
        actor=_any_actor(db, user),
        statuses=statuses,
        q=q,
        stock_inquiry_id=stock_inquiry_id,
        applicant_id=applicant_id or None,
        page=page,
        limit=limit,
        sort=sort,
        direction=dir,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_application(
    payload: NewProductApplicationWrite,
    user: dict = Depends(require_permission(svc.CREATE)),
    db: Session = Depends(get_db),
):
    try:
        app = svc.create_draft(db, applicant_id=user["id"], data=payload.model_dump(exclude_unset=True))
        return _done(db, app)
    except Exception:
        db.rollback()
        raise


@router.get("/{application_id}")
def get_application(
    application_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app = _load(db, application_id)
    svc.assert_can_view(app, _actor(db, user))
    return svc.serialize_detail(db, app)


@router.put("/{application_id}")
def update_application(
    application_id: str,
    payload: NewProductApplicationWrite,
    user: dict = Depends(require_permission(svc.CREATE)),
    db: Session = Depends(get_db),
):
    try:
        app = _load(db, application_id, for_update=True)
        svc.update(db, app, actor=_actor(db, user), data=payload.model_dump(exclude_unset=True))
        return _done(db, app)
    except Exception:
        db.rollback()
        raise


def _move(db: Session, user: dict, application_id: str, fn, **kw):
    try:
        app = _load(db, application_id, for_update=True)
        actor = _actor(db, user)
        svc.assert_can_view(app, actor)
        event = fn(db, app, actor=actor, **kw)
        return _done(db, app, event, actor.user_id)
    except Exception:
        db.rollback()
        raise


@router.post("/{application_id}/submit")
def submit(application_id: str, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    return _move(db, user, application_id, svc.submit)


@router.post("/{application_id}/approve")
def approve(application_id: str, user: dict = Depends(get_current_user), db: Session = Depends(get_db)):
    return _move(db, user, application_id, svc.approve)


@router.post("/{application_id}/reject")
def reject(
    application_id: str,
    payload: ReasonIn,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _move(db, user, application_id, svc.reject, reason=payload.reason)


@router.post("/{application_id}/revise")
def revise(
    application_id: str,
    payload: ReasonIn,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _move(db, user, application_id, svc.revise, reason=payload.reason)


@router.post("/{application_id}/cancel")
def cancel(
    application_id: str,
    payload: Optional[ReasonIn] = None,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _move(db, user, application_id, svc.cancel, reason=payload.reason if payload else None)


def _line_move(db: Session, user: dict, application_id: str, line_id: str, fn, **kw):
    try:
        app = _load(db, application_id, for_update=True)
        actor = _actor(db, user)
        svc.assert_can_view(app, actor)
        event = fn(db, app, validate_uuid_path(line_id, resource="Line"), actor=actor, **kw)
        return _done(db, app, event, actor.user_id)
    except Exception:
        db.rollback()
        raise


@router.post("/{application_id}/lines/{line_id}/link")
def link_line(
    application_id: str,
    line_id: str,
    payload: LinkIn,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _line_move(db, user, application_id, line_id, svc.link_line, product_id=payload.product_id)


@router.post("/{application_id}/lines/{line_id}/close")
def close_line(
    application_id: str,
    line_id: str,
    payload: ReasonIn,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _line_move(db, user, application_id, line_id, svc.close_line, reason=payload.reason)


@router.post("/{application_id}/lines/{line_id}/undo")
def undo_line(
    application_id: str,
    line_id: str,
    user: dict = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _line_move(db, user, application_id, line_id, svc.undo_line)
