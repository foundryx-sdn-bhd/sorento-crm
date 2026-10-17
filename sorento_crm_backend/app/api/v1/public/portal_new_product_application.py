"""Portal new product application endpoints (NEW-PRODUCT-APP; owner 18 Oct: "make sure we
have equivalent portal views").

Mounted at ``/api/v1/public/portal`` BEFORE the generic portal router (D49). Opt-in per
contact (`GRANTABLE_PORTAL_FORM_TYPES`), so every route calls ``_assert_visible`` first, and
every record loads by ``(id, contact_id)``: someone else's id is a 404, never a 403.

All rules are in ``new_product_application_service`` (the staff router calls the same
functions); this module resolves the contact and its company. Approval and the purchasing
line work are staff-only and have no route here. Crew PQ1 (b): no supplier lookup and no
Supplier / Cost price on the portal unless ``svc.PORTAL_SUPPLIER_AND_COST`` is switched on.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.public.portal import get_portal_token
from app.api.v1.public.portal_price_tag import _resolve_company
from app.database import get_db
from app.models.base import company_scope
from app.models.portal import PortalToken
from app.models.procurement import StockInquiry
from app.models.user import User
from app.schemas.new_product_application import NewProductApplicationWrite, ReasonIn
from app.services import new_product_application_service as svc
from app.services import sample_request_service
from app.services.error_handler import AppException
from app.services.portal_form_visibility_service import resolve_visible_form_types
from app.services.uuid_path_param import validate_uuid_path

router = APIRouter(tags=["public-portal-new-product-application"])

_FORM_TYPE = "new_product_application"
BASE = "/submissions/new_product_application"
OWN = "/new-product-application"


def _assert_visible(db: Session, contact_id: str) -> None:
    if _FORM_TYPE not in resolve_visible_form_types(db, contact_id):
        raise AppException(
            status_code=403,
            message="New product applications are not available for your account.",
            code="FORM_TYPE_NOT_VISIBLE",
        )


def _actor(token: PortalToken) -> svc.Actor:
    return svc.Actor(contact_id=str(token.contact_id))


def _linked_user_id(db: Session, contact_id: str) -> Optional[str]:
    """The CRM user whose linked contact this is (`users.respond_contact_id`), or None.
    It is the applicant of record, so staff notices (PQ2 (a)) reach the salesman."""
    return db.query(User.id).filter(User.respond_contact_id == str(contact_id)).scalar()


def _own(db: Session, token: PortalToken, application_id: str):
    return svc.get_for(
        db,
        validate_uuid_path(application_id, resource="New product application"),
        contact_id=str(token.contact_id),
    )


def _done(db: Session, app, event: Optional[str] = None):
    db.commit()
    if event:
        svc.notify(db, app, event)
        db.commit()
    db.refresh(app)
    return svc.serialize_detail(db, app, portal=True)


def _check_write(db: Session, token: PortalToken, data: dict, *, current_customer=None) -> None:
    svc.assert_portal_inquiry_allowed(
        db,
        contact_id=str(token.contact_id),
        space_id=token.space_id,
        stock_inquiry_id=data.get("stock_inquiry_id"),
    )
    # Only a CHANGED debtor is re-checked (the Sample Request rule): a draft whose debtor later
    # left the agent's book stays saveable with the customer it already has.
    if data.get("customer_id") != current_customer:
        sample_request_service.assert_portal_customer_allowed(
            db, contact_id=token.contact_id, customer_id=data.get("customer_id")
        )


# ------------------------------------------------------------------ lookups


@router.get(f"{OWN}/inquiries")
def portal_my_inquiries(
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    """The contact's own RESPONDED stock inquiries, newest first: the "start from" picker."""
    _assert_visible(db, token.contact_id)
    q = db.query(StockInquiry).filter(
        StockInquiry.contact_id == str(token.contact_id), StockInquiry.status == "responded"
    )
    if token.space_id is not None:
        q = q.filter(StockInquiry.space_id == str(token.space_id))
    rows = q.order_by(StockInquiry.created_at.desc()).limit(50).all()
    return [
        {
            "id": r.id,
            "inquiry_number": r.inquiry_number,
            "product_code": r.product_code,
            "item_description": r.item_description,
            "project_name": r.project_name,
            "purchasing_response": r.purchasing_response,
            "last_responded_at": r.last_responded_at.isoformat() if r.last_responded_at else None,
        }
        for r in rows
    ]


@router.get(f"{OWN}/prefill")
def portal_prefill(
    stock_inquiry_id: str = Query(...),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    _assert_visible(db, token.contact_id)
    return svc.prefill(
        db, stock_inquiry_id, contact_id=str(token.contact_id), space_id=token.space_id
    )


@router.get(f"{OWN}/lookups/{{kind}}")
def portal_lookup(
    kind: str,
    q: str = Query(""),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    _assert_visible(db, token.contact_id)
    with company_scope(db, frozenset({_resolve_company(db, token)})):
        return svc.portal_lookup(db, kind, q)


@router.get(f"{OWN}/customers")
def portal_customers(
    q: str = Query(""),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    _assert_visible(db, token.contact_id)
    # The same one company submit checks the pick in, so listed and accepted cannot drift.
    with company_scope(db, frozenset({_resolve_company(db, token)})):
        return sample_request_service.portal_customers(db, str(token.contact_id), q)


# ------------------------------------------------------------------ records


@router.get(BASE)
def portal_list(
    q: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    _assert_visible(db, token.contact_id)
    with company_scope(db, frozenset({_resolve_company(db, token)})):
        out = svc.list_for(db, actor=_actor(token), q=q, page=page, limit=limit)
        return {"data": out["items"], "total": out["total"], "page": page, "limit": limit}


@router.post(BASE, status_code=201)
def portal_create(
    payload: NewProductApplicationWrite,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    _assert_visible(db, token.contact_id)
    company_id = _resolve_company(db, token)
    try:
        with company_scope(db, frozenset({company_id})):
            data = payload.model_dump(exclude_unset=True)
            _check_write(db, token, data)
            app = svc.create_draft(
                db,
                applicant_id=_linked_user_id(db, token.contact_id),
                data=data,
                source="portal",
                contact_id=str(token.contact_id),
                space_id=token.space_id,
            )
            return _done(db, app)
    except Exception:
        db.rollback()
        raise


@router.get(f"{BASE}/{{application_id}}")
def portal_get(
    application_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    _assert_visible(db, token.contact_id)
    with company_scope(db, frozenset({_resolve_company(db, token)})):
        return svc.serialize_detail(db, _own(db, token, application_id), portal=True)


@router.put(f"{BASE}/{{application_id}}")
def portal_update(
    application_id: str,
    payload: NewProductApplicationWrite,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    _assert_visible(db, token.contact_id)
    company_id = _resolve_company(db, token)
    try:
        with company_scope(db, frozenset({company_id})):
            app = _own(db, token, application_id)
            data = payload.model_dump(exclude_unset=True)
            svc.assert_unlocked(app)
            _check_write(db, token, data, current_customer=app.customer_id)
            svc.update(db, app, actor=_actor(token), data=data)
            return _done(db, app)
    except Exception:
        db.rollback()
        raise


def _move(db: Session, token: PortalToken, application_id: str, fn, **kw):
    _assert_visible(db, token.contact_id)
    company_id = _resolve_company(db, token)
    try:
        with company_scope(db, frozenset({company_id})):
            app = _own(db, token, application_id)
            event = fn(db, app, actor=_actor(token), **kw)
            return _done(db, app, event)
    except Exception:
        db.rollback()
        raise


@router.post(f"{BASE}/{{application_id}}/submit")
def portal_submit(
    application_id: str,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    return _move(db, token, application_id, svc.submit)


@router.post(f"{BASE}/{{application_id}}/revise")
def portal_revise(
    application_id: str,
    payload: ReasonIn,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    return _move(db, token, application_id, svc.revise, reason=payload.reason)


@router.post(f"{BASE}/{{application_id}}/cancel")
def portal_cancel(
    application_id: str,
    payload: Optional[ReasonIn] = None,
    token: PortalToken = Depends(get_portal_token),
    db: Session = Depends(get_db),
):
    return _move(
        db, token, application_id, svc.cancel, reason=payload.reason if payload else None
    )
