"""FORMS-PROJECT review round 1: every write path refuses a foreign project, and a save that
leaves the link unchanged is never refused for it.

* reviewer B3: complaint update-and-reply, stock inquiry update-and-reply, stock inquiry
  resubmit-of-a-rejected-row, the portal price tag routes and the portal opportunity routes
  each refuse a project outside the caller's reach (killing each check turns a test red);
* reviewer S1: re-saving a form whose stored link the editor could not pick today (another
  company's project, linked by somebody else) is not refused, as long as the link is unchanged;
* reviewer S2: a portal contact of several companies may link any of their companies'
  projects on a price tag request, whichever company the request runs under.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.models.access import RespondContact
from app.models.base import company_scope
from app.models.company import RespondContactCompany
from app.models.user import User
from app.services import project_seed_service
from app.services.error_handler import AppException

from ._pg_fixture import blank_session

MARKER = "zzt-fpp"


def _uid() -> str:
    return str(uuid.uuid4())


def _code(exc: AppException) -> str:
    detail = exc.detail
    return (detail or {}).get("code", "") if isinstance(detail, dict) else ""


def _project(db, company_id: str, owner: str):
    from app.services.project_service import register_project

    project = register_project(
        db,
        company_id=company_id,
        actor_user_id=owner,
        developer_party_id=None,
        title=f"{MARKER} Tower {_uid()[:6]}",
    )
    db.flush()
    return str(project.id)


@pytest.fixture()
def w():
    with blank_session() as db:
        srt = str(db.execute(text("select id from companies where code = 'SRT'")).scalar())
        other = _uid()
        db.execute(
            text("insert into companies (id, code, name, is_active) values (:i, :c, :n, true)"),
            {"i": other, "c": f"Z{_uid()[:3]}", "n": f"{MARKER} other co"},
        )
        project_seed_service.run(db, company_id=srt)
        project_seed_service.run(db, company_id=other)
        owner = _uid()
        db.add(User(id=owner, email=f"{owner}@zzt.test", name=f"{MARKER} owner"))
        contact = RespondContact(
            id=_uid(), phone_number=f"+6011{_uid()[:8]}", name=f"{MARKER} c", session_vars={}
        )
        db.add(contact)
        db.flush()
        db.add(RespondContactCompany(id=_uid(), respond_contact_id=contact.id, company_id=srt))
        db.flush()
        ns = SimpleNamespace(
            db=db,
            srt=srt,
            other=other,
            own=_project(db, srt, owner),
            foreign=_project(db, other, owner),
            contact=contact,
        )
        with company_scope(db, frozenset({srt})):
            yield ns


def _token(contact):
    from app.models.portal import PortalToken

    return PortalToken(
        id=_uid(),
        token=_uid(),
        contact_id=contact.id,
        space_id="zzt-space",
        expires_at=datetime.utcnow() + timedelta(days=1),
    )


def _store_link(db, model, row_id, project_id):
    """Write a link straight to the row, as an earlier editor with wider reach would have."""
    db.query(model).filter(model.id == row_id).update(
        {"project_id": project_id}, synchronize_session=False
    )
    db.flush()
    db.expire_all()


# ------------------------------------------------------------- staff paths (B3)


def _si(w, **kwargs):
    from app.schemas.procurement import StockInquiryCreate
    from app.services.procurement_service import StockInquiryService

    inquiry, _ = StockInquiryService(w.db).create_inquiry(
        StockInquiryCreate(
            salesperson="s",
            product_code="ZZT-1",
            item_description="basin",
            project_customer="c",
            project_name="n",
            quantity="1",
            delivery_date="2026-11-01",
            contact_id="zzt-contact",
            space_id="zzt-space",
            **kwargs,
        )
    )
    return inquiry


def test_stock_inquiry_update_and_reply_refuses_a_foreign_project(w):
    from app.schemas.procurement import StockInquiryUpdate
    from app.services.procurement_service import StockInquiryService

    from app.models.procurement import StockInquiry
    from app.services.response_gate import ALLOWED_RESPONSE_STATUSES

    inquiry = _si(w)
    # Into the response stage, or the reply gate refuses before the project is looked at.
    w.db.query(StockInquiry).filter(StockInquiry.id == inquiry.id).update(
        {"status": ALLOWED_RESPONSE_STATUSES["stock_inquiry"][0]}, synchronize_session=False
    )
    w.db.flush()
    w.db.expire_all()
    with pytest.raises(AppException) as exc:
        StockInquiryService(w.db).update_inquiry_and_reply(
            str(inquiry.id),
            StockInquiryUpdate(project_id=w.foreign, purchasing_response="in stock"),
            respond_user_id="1",
        )
    assert _code(exc.value) == "PROJECT_LINK_NOT_FOUND"


def test_stock_inquiry_resubmit_of_a_rejected_row_refuses_a_foreign_project(w):
    from app.models.procurement import StockInquiry

    inquiry = _si(w)
    w.db.query(StockInquiry).filter(StockInquiry.id == inquiry.id).update(
        {"status": "rejected"}, synchronize_session=False
    )
    w.db.flush()
    with pytest.raises(AppException) as exc:
        _si(w, inquiry_number=inquiry.inquiry_number, project_id=w.foreign)
    assert _code(exc.value) == "PROJECT_LINK_NOT_FOUND"


def test_complaint_update_and_reply_refuses_a_foreign_project(w):
    from app.schemas.complaints import ComplaintCreate, ComplaintUpdate
    from app.services.complaints_service import ComplaintService

    svc = ComplaintService(w.db)
    complaint = svc.create_complaint(ComplaintCreate(complaint_number=f"{MARKER}-{_uid()[:6]}"))
    with pytest.raises(AppException) as exc:
        svc.update_complaint_and_reply(
            str(complaint.id),
            ComplaintUpdate(project_id=w.foreign, technical_team_response="replaced"),
            respond_user_id="1",
        )
    assert _code(exc.value) == "PROJECT_LINK_NOT_FOUND"


# ---------------------------------------------------- unchanged links (S1)


def test_staff_resave_keeps_an_unchanged_link_the_editor_cannot_pick(w):
    from app.models.procurement import PurchaseRequestHeader, StockInquiry
    from app.schemas.procurement import (
        PurchaseRequestHeaderCreate,
        PurchaseRequestHeaderUpdate,
        StockInquiryUpdate,
    )
    from app.services.procurement_service import PurchaseRequestService, StockInquiryService

    prs = PurchaseRequestService(w.db)
    header = prs.create_request(
        PurchaseRequestHeaderCreate(
            request_type="purchase_request", request_number=f"{MARKER}-{_uid()[:6]}"
        )
    )
    _store_link(w.db, PurchaseRequestHeader, header.id, w.foreign)
    header = prs.update_request(
        str(header.id), PurchaseRequestHeaderUpdate(project_id=w.foreign, pic="x")
    )
    assert str(header.project_id) == w.foreign

    inquiry = _si(w)
    _store_link(w.db, StockInquiry, inquiry.id, w.foreign)
    inquiry = StockInquiryService(w.db).update_inquiry(
        str(inquiry.id), StockInquiryUpdate(project_id=w.foreign, remark="x")
    )
    assert str(inquiry.project_id) == w.foreign


def test_portal_draft_resave_keeps_an_unchanged_link(w):
    from app.models.procurement import StockInquiry
    from app.services.portal_service import PortalService

    portal = PortalService(w.db)
    token = _token(w.contact)
    body = portal.create_or_update_draft(token, "stock_inquiry", {"project_name": "x"})
    _store_link(w.db, StockInquiry, body["id"], w.foreign)
    portal.create_or_update_draft(
        token,
        "stock_inquiry",
        {"project_id": w.foreign, "remark": "y"},
        submission_id=body["id"],
    )
    row = w.db.query(StockInquiry).filter(StockInquiry.id == body["id"]).one()
    assert str(row.project_id) == w.foreign


# ---------------------------------------------- portal price tag routes (B3/S2)


def _ptr_routes(monkeypatch):
    from app.api.v1.public import portal_price_tag as routes

    monkeypatch.setattr(routes, "_assert_visible", lambda db, contact_id: None)
    return routes


def test_portal_price_tag_create_refuses_a_foreign_project(w, monkeypatch):
    from app.schemas.price_tag import PriceTagRequestCreate

    routes = _ptr_routes(monkeypatch)
    monkeypatch.setattr(routes, "_resolve_company", lambda db, token: w.srt)
    with pytest.raises(AppException) as exc:
        routes.portal_create_price_tag_request(
            PriceTagRequestCreate(project_id=w.foreign), token=_token(w.contact), db=w.db
        )
    assert _code(exc.value) == "PROJECT_LINK_NOT_ALLOWED"


def test_portal_price_tag_create_takes_any_of_the_contacts_companies(w, monkeypatch):
    """S2: the contact also belongs to the other company; the request runs under Sorento."""
    from app.models.price_tag import PriceTagRequest
    from app.schemas.price_tag import PriceTagRequestCreate

    w.db.add(RespondContactCompany(id=_uid(), respond_contact_id=w.contact.id, company_id=w.other))
    w.db.flush()
    routes = _ptr_routes(monkeypatch)
    monkeypatch.setattr(routes, "_resolve_company", lambda db, token: w.srt)
    monkeypatch.setattr(routes, "_detail_body", lambda db, req: {"id": str(req.id)})
    body = routes.portal_create_price_tag_request(
        PriceTagRequestCreate(project_id=w.foreign), token=_token(w.contact), db=w.db
    )
    row = w.db.query(PriceTagRequest).filter(PriceTagRequest.id == body["id"]).one()
    assert str(row.project_id) == w.foreign


def test_portal_price_tag_draft_update_refuses_a_foreign_project(w, monkeypatch):
    from app.schemas.price_tag import PriceTagRequestUpdate

    routes = _ptr_routes(monkeypatch)
    draft = SimpleNamespace(project_id=None, portal_draft_at=datetime.utcnow(), company_id=w.srt)
    monkeypatch.setattr(routes, "_require_own_request", lambda db, token, rid: draft)
    with pytest.raises(AppException) as exc:
        routes.portal_update_price_tag_request(
            _uid(), PriceTagRequestUpdate(project_id=w.foreign), token=_token(w.contact), db=w.db
        )
    assert _code(exc.value) == "PROJECT_LINK_NOT_ALLOWED"
    assert draft.project_id is None


# -------------------------------------------------- portal opportunity routes (B3)


def _opp_routes(monkeypatch):
    from app.api.v1.public import portal_sales_opportunity as routes

    monkeypatch.setattr(routes, "_require_agents", lambda db, token: [SimpleNamespace(id=_uid())])
    return routes


def test_portal_opportunity_create_refuses_a_foreign_project(w, monkeypatch):
    from app.schemas.sales import PortalSalesOpportunityCreate

    routes = _opp_routes(monkeypatch)
    with pytest.raises(AppException) as exc:
        routes.portal_create_sales_opportunity(
            PortalSalesOpportunityCreate(
                title="t",
                expected_close_date=date(2026, 11, 1),
                prospect_name="p",
                project_id=w.foreign,
            ),
            token=_token(w.contact),
            db=w.db,
        )
    assert _code(exc.value) == "PROJECT_LINK_NOT_ALLOWED"


def test_portal_opportunity_update_refuses_a_foreign_project(w, monkeypatch):
    from app.schemas.sales import PortalSalesOpportunityUpdate

    routes = _opp_routes(monkeypatch)
    opp = SimpleNamespace(project_id=None, sales_agent_id=None, company_id=w.srt)
    monkeypatch.setattr(routes.svc, "get_opportunity_for_agent_or_404", lambda db, oid, ids: opp)
    with pytest.raises(AppException) as exc:
        routes.portal_update_sales_opportunity(
            _uid(),
            PortalSalesOpportunityUpdate(project_id=w.foreign),
            token=_token(w.contact),
            db=w.db,
        )
    assert _code(exc.value) == "PROJECT_LINK_NOT_ALLOWED"
