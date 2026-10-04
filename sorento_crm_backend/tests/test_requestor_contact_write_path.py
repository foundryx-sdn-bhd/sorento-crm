"""``PortalService._apply_requestor_contact`` (PLAN-requested-by-contact-routing.md
D5/D6/D7): the requestor FK write path validates eligibility, stamps the FK +
a live-derived display label, rejects an ineligible id with 422
REQUESTOR_NOT_ELIGIBLE, and lets the field be cleared to NULL.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.access import MarketSegment, RespondContact, respond_contact_market_segments
from app.models.procurement import StockInquiry
from app.services.error_handler import AppException
from app.services.portal_service import PortalService
from tests._pg_fixture import blank_session


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _segment(db, code="PROJECT") -> None:
    db.add(MarketSegment(code=code, name=code, is_active=True, is_requestor_selectable=True))
    db.commit()


def _contact(db, *, name, segments=()) -> str:
    c = RespondContact(id=str(uuid.uuid4()), phone_number=f"+6011{uuid.uuid4().hex[:8]}", name=name)
    db.add(c)
    db.flush()
    for code in segments:
        db.execute(
            respond_contact_market_segments.insert().values(contact_id=c.id, segment_code=code)
        )
    db.commit()
    return c.id


def _inquiry(db, *, contact_id=None, salesperson_contact_id=None) -> StockInquiry:
    row = StockInquiry(
        id=str(uuid.uuid4()),
        inquiry_number="WRITE-1",
        status="new",
        contact_id=contact_id,
        salesperson_contact_id=salesperson_contact_id,
    )
    db.add(row)
    db.commit()
    return row


def test_eligible_id_accepted_and_label_stamped_from_live_name(db):
    _segment(db)
    eric = _contact(db, name="CONTACT Y", segments=["PROJECT"])
    row = _inquiry(db)

    svc = PortalService(db)
    svc._apply_requestor_contact("stock_inquiry", row, eric)

    assert row.salesperson_contact_id == eric
    assert row.salesperson == "CONTACT Y"


def test_ineligible_id_rejected_422(db):
    stranger = _contact(db, name="No Segment Person")  # not in any flagged segment
    row = _inquiry(db)

    svc = PortalService(db)
    with pytest.raises(AppException) as ei:
        svc._apply_requestor_contact("stock_inquiry", row, stranger)
    assert ei.value.status_code == 422
    detail = ei.value.detail
    code = detail.get("code") if isinstance(detail, dict) else None
    assert code == "REQUESTOR_NOT_ELIGIBLE"
    # Row must be untouched on rejection.
    assert row.salesperson_contact_id is None


def test_submitting_contact_always_eligible_even_without_segment(db):
    """D3: the row's own submitter is always accepted, even unsegmented."""
    contact_x = _contact(db, name="CONTACT X Submitter")  # no segments
    row = _inquiry(db, contact_id=contact_x)

    svc = PortalService(db)
    svc._apply_requestor_contact("stock_inquiry", row, contact_x)

    assert row.salesperson_contact_id == contact_x
    assert row.salesperson == "CONTACT X Submitter"


def test_currently_saved_but_now_ineligible_contact_can_be_kept(db):
    """D7: re-submitting the SAME (already-saved) contact must not be
    rejected even if that contact has since lost segment eligibility."""
    stranger = _contact(db, name="Lost Eligibility")
    row = _inquiry(db, salesperson_contact_id=stranger)
    row.salesperson = "Lost Eligibility"

    svc = PortalService(db)
    # Re-affirming the same id that's already on the row must not 422.
    svc._apply_requestor_contact("stock_inquiry", row, stranger)
    assert row.salesperson_contact_id == stranger


def test_clearing_leaves_fk_null(db):
    _segment(db)
    eric = _contact(db, name="CONTACT Y", segments=["PROJECT"])
    row = _inquiry(db, salesperson_contact_id=eric)
    row.salesperson = "CONTACT Y"

    svc = PortalService(db)
    svc._apply_requestor_contact("stock_inquiry", row, None)

    assert row.salesperson_contact_id is None
    # Clearing the FK does not touch the free-text label (display fallback).
    assert row.salesperson == "CONTACT Y"


def test_clearing_with_empty_string_also_nulls(db):
    _segment(db)
    eric = _contact(db, name="CONTACT Y", segments=["PROJECT"])
    row = _inquiry(db, salesperson_contact_id=eric)

    svc = PortalService(db)
    svc._apply_requestor_contact("stock_inquiry", row, "")
    assert row.salesperson_contact_id is None


def test_complaint_kind_has_no_requestor_field_no_op(db):
    """Complaint stays free-text this slice (D9 out of scope) -- calling the
    write path for it is a silent no-op, never an error."""
    row = _inquiry(db)  # any row object; complaint has no requestor field
    svc = PortalService(db)
    svc._apply_requestor_contact("complaint", row, "anything")
    # No requestor field on complaint -> unrelated attributes untouched.
    assert row.salesperson_contact_id is None


# ---------------------------------------------------------------------------
# Internal (JWT) CRM write path - the portal-first tests never touched it, and
# it shipped stamping the FK with NO label: the document / PDF / public approval
# page print the label, so they all read "-" (code-review B1/B2).
# ---------------------------------------------------------------------------


def test_internal_stock_inquiry_create_stamps_fk_and_label(db):
    from app.schemas.procurement import StockInquiryCreate
    from app.services.procurement_service import StockInquiryService

    _segment(db)
    eric = _contact(db, name="CONTACT Y", segments=["PROJECT"])

    payload = StockInquiryCreate(
        product_code="SRT-1",
        item_description="Basin",
        project_customer="Acme Sdn Bhd",
        project_name="Tower A",
        quantity="10",
        delivery_date="2026-09-01",
        # No free-text `salesperson`: the CRM form only offers the picker now.
        salesperson_contact_id=eric,
    )
    inquiry, _outcome = StockInquiryService(db).create_inquiry(payload)

    assert inquiry.salesperson_contact_id == eric
    # The label is what every printed surface reads.
    assert inquiry.salesperson == "CONTACT Y"
    # And the derived read-only display name resolves live off the FK.
    assert inquiry.salesperson_contact_name == "CONTACT Y"


def test_internal_stock_inquiry_create_never_500s_on_derived_field(db):
    """A derived display field must not be accepted on the create payload: it
    would ride into `StockInquiry(**data)` and TypeError the whole route."""
    from app.schemas.procurement import StockInquiryCreate

    payload = StockInquiryCreate(product_code="SRT-2", salesperson_contact_name="Ignored")
    assert not hasattr(payload, "salesperson_contact_name")


def test_internal_stock_inquiry_update_rejects_ineligible_requestor(db):
    from app.schemas.procurement import StockInquiryUpdate
    from app.services.procurement_service import StockInquiryService

    _segment(db)
    outsider = _contact(db, name="Random Person")
    row = _inquiry(db)

    with pytest.raises(AppException) as exc:
        StockInquiryService(db).update_inquiry(
            str(row.id), StockInquiryUpdate(salesperson_contact_id=outsider)
        )
    assert exc.value.detail["code"] == "REQUESTOR_NOT_ELIGIBLE"


def test_internal_purchase_request_update_relabels_on_requestor_change(db):
    from app.models.procurement import PurchaseRequestHeader
    from app.schemas.procurement import PurchaseRequestHeaderUpdate
    from app.services.procurement_service import PurchaseRequestService

    _segment(db)
    ahmad = _contact(db, name="Ahmad", segments=["PROJECT"])
    siti = _contact(db, name="Siti", segments=["PROJECT"])
    header = PurchaseRequestHeader(
        id=str(uuid.uuid4()),
        request_number="WRITE-PR-1",
        request_type="purchase_request",
        requested_by="Ahmad",
        requested_by_contact_id=ahmad,
    )
    db.add(header)
    db.commit()

    updated = PurchaseRequestService(db).update_request(
        str(header.id), PurchaseRequestHeaderUpdate(requested_by_contact_id=siti)
    )

    assert updated.requested_by_contact_id == siti
    # Stale label was the bug: the document kept saying "Ahmad" forever.
    assert updated.requested_by == "Siti"
    assert updated.requested_by_contact_name == "Siti"


def test_internal_requestor_can_be_cleared(db):
    from app.schemas.procurement import StockInquiryUpdate
    from app.services.procurement_service import StockInquiryService

    _segment(db)
    eric = _contact(db, name="CONTACT Y", segments=["PROJECT"])
    row = _inquiry(db, salesperson_contact_id=eric)

    updated = StockInquiryService(db).update_inquiry(
        str(row.id), StockInquiryUpdate(salesperson_contact_id=None)
    )
    assert updated.salesperson_contact_id is None
