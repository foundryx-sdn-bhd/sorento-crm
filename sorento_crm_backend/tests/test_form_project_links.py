"""FORMS-PROJECT: every form type can carry an explicit, checked project link.

UAC: documentation/plans/forms/forms-project-acceptance-criteria.md.

* staff saves (stock inquiry, sales opportunity, price tag office edit) take a project the
  editor can see and refuse anything else (AC-FP-11/12);
* staff reads carry `project_code` + `project_name`, never only a UUID (AC-FP-30);
* staff list reads filter by `project_id` (AC-FP-31);
* portal saves (complaint, PR, SF, stock inquiry drafts and revisions, price tag, sales
  opportunity) take only a project of one of the contact's companies (AC-FP-20/21).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.access import RespondContact
from app.models.base import company_scope
from app.models.company import RespondContactCompany
from app.models.user import User
from app.services import project_seed_service
from app.services.error_handler import AppException

from ._pg_fixture import blank_session

MARKER = "zzt-fp"


def _uid() -> str:
    return str(uuid.uuid4())


def _code(exc: AppException) -> str:
    detail = exc.detail
    return (detail or {}).get("code", "") if isinstance(detail, dict) else ""


def _sorento(db) -> str:
    return str(db.execute(text("select id from companies where code = 'SRT'")).scalar())


def _other_company(db) -> str:
    company_id = _uid()
    db.execute(
        text("insert into companies (id, code, name, is_active) values (:i, :c, :n, true)"),
        {"i": company_id, "c": f"Z{_uid()[:3]}", "n": f"{MARKER} other co"},
    )
    db.flush()
    return company_id


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
    return project


class World:
    def __init__(self, db, srt, other, own, foreign, contact):
        self.db = db
        self.srt = srt
        self.other = other
        self.own = own
        self.foreign = foreign
        self.contact = contact

    @property
    def own_id(self) -> str:
        return str(self.own.id)

    @property
    def foreign_id(self) -> str:
        return str(self.foreign.id)

    def token(self):
        from app.models.portal import PortalToken

        return PortalToken(
            id=_uid(),
            token=_uid(),
            contact_id=self.contact.id,
            space_id="zzt-space",
            expires_at=datetime.utcnow() + timedelta(days=1),
        )


@pytest.fixture()
def world():
    """Sorento holds `own`, another company holds `foreign`; the staff editor's scope and the
    portal contact's company are both Sorento only."""
    with blank_session() as db:
        srt = _sorento(db)
        other = _other_company(db)
        project_seed_service.run(db, company_id=srt)
        project_seed_service.run(db, company_id=other)
        owner = _uid()
        db.add(User(id=owner, email=f"{owner}@zzt.test", name=f"{MARKER} owner"))
        contact = RespondContact(
            id=_uid(),
            phone_number=f"+6011{_uid()[:8]}",
            name=f"{MARKER} contact",
            session_vars={},
        )
        db.add(contact)
        db.flush()
        db.add(RespondContactCompany(id=_uid(), respond_contact_id=contact.id, company_id=srt))
        db.flush()
        own = _project(db, srt, owner)
        foreign = _project(db, other, owner)
        with company_scope(db, frozenset({srt})):
            yield World(db, srt, other, own, foreign, contact)


# ---------------------------------------------------------------- stock inquiry


def _si_service(db):
    from app.services.procurement_service import StockInquiryService

    return StockInquiryService(db)


def _create_si(world, **kwargs):
    from app.schemas.procurement import StockInquiryCreate

    inquiry, _state = _si_service(world.db).create_inquiry(
        StockInquiryCreate(
            salesperson=f"{MARKER} sales",
            product_code="ZZT-001",
            item_description="basin",
            project_customer="Site A",
            project_name="Residensi Typed",
            quantity="3",
            delivery_date="2026-11-01",
            contact_id="zzt-contact",
            space_id="zzt-space",
            **kwargs,
        )
    )
    return inquiry


def test_stock_inquiry_staff_create_stores_the_link_and_keeps_the_free_text(world):
    inquiry = _create_si(world, project_id=world.own_id)
    assert str(inquiry.project_id) == world.own_id
    assert inquiry.project_name == "Residensi Typed", "free text must stay (AC-FP-14)"


def test_stock_inquiry_staff_create_refuses_a_foreign_project(world):
    with pytest.raises(AppException) as exc:
        _create_si(world, project_id=world.foreign_id)
    assert _code(exc.value) == "PROJECT_LINK_NOT_FOUND"


def test_stock_inquiry_staff_edit_links_and_clears(world):
    from app.schemas.procurement import StockInquiryUpdate

    inquiry = _create_si(world)
    assert inquiry.project_id is None
    svc = _si_service(world.db)
    inquiry = svc.update_inquiry(str(inquiry.id), StockInquiryUpdate(project_id=world.own_id))
    assert str(inquiry.project_id) == world.own_id
    with pytest.raises(AppException):
        svc.update_inquiry(str(inquiry.id), StockInquiryUpdate(project_id=world.foreign_id))
    inquiry = svc.update_inquiry(str(inquiry.id), StockInquiryUpdate(project_id=None))
    assert inquiry.project_id is None


def test_stock_inquiry_detail_and_list_carry_code_and_title_and_filter(world):
    linked = _create_si(world, project_id=world.own_id)
    unlinked = _create_si(world)
    svc = _si_service(world.db)
    detail = svc.get_inquiry_for_response(str(linked.id))
    assert detail["project_code"] == world.own.project_code
    assert detail["project_name"] == world.own.title
    rows = svc.list_inquiries(limit=200, project_id=world.own_id)["data"]
    ids = {str(r.id) for r in rows}
    assert str(linked.id) in ids and str(unlinked.id) not in ids
    assert all(r.project_code == world.own.project_code for r in rows)


def test_stock_inquiry_response_schema_declares_the_link():
    from app.schemas.procurement import StockInquiryResponse

    fields = StockInquiryResponse.model_fields
    assert {"project_id", "project_code", "project_name"} <= set(fields)


# ---------------------------------------------------------- sales opportunity


def _create_opp(world, **extra):
    from app.services.sales import opportunity_service as svc
    from app.services.sales import sales_seed_service

    sales_seed_service.run(world.db)
    world.db.flush()
    return svc.create_opportunity(
        world.db,
        company_id=world.srt,
        payload={
            "title": f"{MARKER} opp",
            "expected_amount": Decimal("100.00"),
            "expected_close_date": date(2026, 11, 1),
            "prospect_name": f"{MARKER} prospect",
            **extra,
        },
        sales_agent_id=None,
        source="crm",
        created_by_user_id=None,
    )


def test_opportunity_create_and_edit_link_check_and_clear(world):
    from app.services.sales import opportunity_service as svc

    opp = _create_opp(world, project_id=world.own_id)
    assert str(opp.project_id) == world.own_id
    with pytest.raises(AppException) as exc:
        svc.update_opportunity(world.db, opp, {"project_id": world.foreign_id})
    assert _code(exc.value) == "PROJECT_LINK_NOT_FOUND"
    svc.update_opportunity(world.db, opp, {"project_id": None})
    assert opp.project_id is None


def test_opportunity_create_refuses_a_foreign_project(world):
    with pytest.raises(AppException):
        _create_opp(world, project_id=world.foreign_id)


def test_opportunity_serializers_and_list_filter(world):
    from app.services.sales import opportunity_service as svc

    linked = _create_opp(world, project_id=world.own_id)
    other = _create_opp(world)
    world.db.flush()
    body = svc.serialize(world.db, linked)
    assert body["project_id"] == world.own_id
    assert body["project_code"] == world.own.project_code
    assert body["project_name"] == world.own.title
    result = svc.list_opportunities(world.db, project_id=world.own_id, limit=200)
    rows = result["data"] if isinstance(result, dict) else result[0]
    listed = svc.serialize_list(world.db, rows) if rows and not isinstance(rows[0], dict) else rows
    ids = {str(r["id"]) for r in listed}
    assert str(linked.id) in ids and str(other.id) not in ids
    assert all(r["project_code"] == world.own.project_code for r in listed)


def test_opportunity_create_schemas_accept_the_link():
    from app.schemas.sales import (
        PortalSalesOpportunityCreate,
        PortalSalesOpportunityUpdate,
        SalesOpportunityCreate,
        SalesOpportunityResponse,
        SalesOpportunityUpdate,
    )

    for schema in (
        SalesOpportunityCreate,
        SalesOpportunityUpdate,
        PortalSalesOpportunityCreate,
        PortalSalesOpportunityUpdate,
    ):
        assert "project_id" in schema.model_fields, schema.__name__
    assert {"project_id", "project_code", "project_name"} <= set(
        SalesOpportunityResponse.model_fields
    )


# ------------------------------------------------------------- price tag request


def _create_ptr(world, **data):
    from app.services.price_tag_request_service import PriceTagRequestService

    return PriceTagRequestService.create_request(
        world.db, contact_id=world.contact.id, company_id=world.srt, data=data
    )


def test_price_tag_create_stores_the_link(world):
    req = _create_ptr(world, project_id=world.own_id)
    assert str(req.project_id) == world.own_id


def test_price_tag_office_link_is_checked_and_clearable(world):
    from app.services.price_tag_request_service import PriceTagRequestService

    req = _create_ptr(world)
    PriceTagRequestService.set_project(world.db, req, world.own_id)
    assert str(req.project_id) == world.own_id
    with pytest.raises(AppException) as exc:
        PriceTagRequestService.set_project(world.db, req, world.foreign_id)
    assert _code(exc.value) == "PROJECT_LINK_NOT_FOUND"
    assert str(req.project_id) == world.own_id
    PriceTagRequestService.set_project(world.db, req, None)
    assert req.project_id is None


def test_price_tag_list_filter_and_display(world):
    from app.services.price_tag_request_service import PriceTagRequestService

    linked = _create_ptr(world, project_id=world.own_id)
    other = _create_ptr(world)
    world.db.flush()
    rows, _total = PriceTagRequestService.list_page(
        world.db, project_id=world.own_id, include_drafts=True, limit=200
    )
    ids = {str(r.id) for r in rows}
    assert str(linked.id) in ids and str(other.id) not in ids
    body = PriceTagRequestService.response_with_resolved_lines(world.db, linked)
    code = body["project_code"] if isinstance(body, dict) else body.project_code
    assert code == world.own.project_code


def test_price_tag_schemas_declare_the_link():
    from app.schemas.price_tag import (
        PriceTagRequestCreate,
        PriceTagRequestListItem,
        PriceTagRequestOfficeUpdate,
        PriceTagRequestResponse,
        PriceTagRequestUpdate,
    )

    for schema in (PriceTagRequestCreate, PriceTagRequestUpdate, PriceTagRequestOfficeUpdate):
        assert "project_id" in schema.model_fields, schema.__name__
    for schema in (PriceTagRequestResponse, PriceTagRequestListItem):
        assert {"project_id", "project_code", "project_name"} <= set(schema.model_fields)


# ------------------------------------------------- complaint / PR list filters


def test_complaint_list_filters_by_project(world):
    from app.schemas.complaints import ComplaintCreate
    from app.services.complaints_service import ComplaintService

    svc = ComplaintService(world.db)
    linked = svc.create_complaint(
        ComplaintCreate(complaint_number=f"{MARKER}-{_uid()[:6]}", project_id=world.own_id)
    )
    other = svc.create_complaint(ComplaintCreate(complaint_number=f"{MARKER}-{_uid()[:6]}"))
    result = svc.list_complaints(limit=200, project_id=world.own_id)
    ids = {str(r["id"] if isinstance(r, dict) else r.id) for r in result["data"]}
    assert str(linked.id) in ids and str(other.id) not in ids


def test_purchase_request_list_filters_by_project_and_detail_has_the_title(world):
    from app.schemas.procurement import PurchaseRequestHeaderCreate
    from app.services.procurement_service import PurchaseRequestService

    svc = PurchaseRequestService(world.db)

    def make(**kwargs):
        return svc.create_request(
            PurchaseRequestHeaderCreate(
                request_type="purchase_request",
                request_number=f"{MARKER}-{_uid()[:6]}",
                **kwargs,
            )
        )

    linked = make(project_id=world.own_id)
    other = make()
    result = svc.list_requests(
        limit=200, request_type="purchase_request", project_id=world.own_id
    )
    ids = {str(r.id) for r in result["data"]}
    assert str(linked.id) in ids and str(other.id) not in ids
    detail = svc.get_request(str(linked.id))
    assert detail.project_code == world.own.project_code
    assert detail.project_name == world.own.title


# ------------------------------------------------------------------- portal


def _portal(db):
    from app.services.portal_service import PortalService

    return PortalService(db)


@pytest.mark.parametrize("kind", ["complaint", "stock_inquiry", "purchase_request"])
def test_portal_draft_takes_one_of_the_contacts_projects(world, kind):
    body = _portal(world.db).create_or_update_draft(
        world.token(), kind, {"project_id": world.own_id}
    )
    assert body.get("project_id") == world.own_id
    assert body.get("project_code") == world.own.project_code


@pytest.mark.parametrize(
    "kind", ["complaint", "stock_inquiry", "purchase_request", "sponsorship_form"]
)
def test_portal_draft_refuses_a_project_of_another_company(world, kind):
    with pytest.raises(AppException) as exc:
        _portal(world.db).create_or_update_draft(
            world.token(), kind, {"project_id": world.foreign_id}
        )
    assert _code(exc.value) == "PROJECT_LINK_NOT_ALLOWED"


def test_portal_draft_update_refuses_a_project_of_another_company(world):
    portal = _portal(world.db)
    token = world.token()
    body = portal.create_or_update_draft(token, "stock_inquiry", {"project_name": "x"})
    with pytest.raises(AppException) as exc:
        portal.create_or_update_draft(
            token, "stock_inquiry", {"project_id": world.foreign_id}, submission_id=body["id"]
        )
    assert _code(exc.value) == "PROJECT_LINK_NOT_ALLOWED"


def test_portal_editable_fields_include_the_link_for_every_kind():
    from app.services.portal_service import PortalService

    svc = PortalService.__new__(PortalService)
    for kind in (
        "complaint",
        "stock_inquiry",
        "purchase_request",
        "sponsorship_form",
        "price_tag_request",
    ):
        assert "project_id" in svc._editable_fields(kind), kind  # noqa: SLF001


def test_portal_apply_payload_refuses_a_foreign_project_on_the_revision_path(world):
    """Revisions write through `_apply_payload`; the check has to live there too."""
    from app.models.complaints import Complaint

    row = Complaint(contact_id=world.contact.id, space_id="zzt-space", status="submitted")
    with pytest.raises(AppException) as exc:
        _portal(world.db)._apply_payload(  # noqa: SLF001
            "complaint", row, {"project_id": world.foreign_id}, token=world.token()
        )
    assert _code(exc.value) == "PROJECT_LINK_NOT_ALLOWED"
