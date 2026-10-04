"""``FormSLAOrchestrator._routing_contact_id`` (PLAN-requested-by-contact-routing.md).

The requestor FK read for CS pin-point routing: purchase_request / sponsorship_form
read ``purchase_requests.requested_by_contact_id``; stock_inquiry reads
``stock_inquiries.salesperson_contact_id``. Anything else (complaint) has no
requestor FK and always returns None. Never raises -- a missing row / bad id /
NULL FK all degrade to None so the caller falls back to the submitter (E3/E9).

Real Postgres rows (raw SQL under the hood), not a mock.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from app.models.access import RespondContact
from app.models.complaints import Complaint
from app.models.procurement import PurchaseRequestHeader, StockInquiry
from app.services.form_sla_service import FormSLAOrchestrator
from tests._pg_fixture import blank_session


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


def _contact(db) -> str:
    c = RespondContact(id=str(uuid.uuid4()), phone_number=f"+6011{uuid.uuid4().hex[:8]}", name="CONTACT Y")
    db.add(c)
    db.commit()
    return c.id


def test_purchase_request_reads_requested_by_contact_id(db):
    requestor_id = _contact(db)
    row = PurchaseRequestHeader(
        id=str(uuid.uuid4()),
        request_type="purchase_request",
        requested_by_contact_id=requestor_id,
    )
    db.add(row)
    db.commit()

    orch = FormSLAOrchestrator(db)
    assert orch._routing_contact_id("purchase_request", row.id) == requestor_id


def test_sponsorship_form_shares_purchase_requests_table(db):
    requestor_id = _contact(db)
    row = PurchaseRequestHeader(
        id=str(uuid.uuid4()),
        request_type="sponsorship_form",
        requested_by_contact_id=requestor_id,
    )
    db.add(row)
    db.commit()

    orch = FormSLAOrchestrator(db)
    assert orch._routing_contact_id("sponsorship_form", row.id) == requestor_id


def test_stock_inquiry_reads_salesperson_contact_id(db):
    requestor_id = _contact(db)
    row = StockInquiry(
        id=str(uuid.uuid4()), inquiry_number="SI-1", status="new",
        salesperson_contact_id=requestor_id,
    )
    db.add(row)
    db.commit()

    orch = FormSLAOrchestrator(db)
    assert orch._routing_contact_id("stock_inquiry", row.id) == requestor_id


def test_null_fk_returns_none_e3_regression(db):
    row = PurchaseRequestHeader(id=str(uuid.uuid4()), request_type="purchase_request")
    db.add(row)
    db.commit()

    orch = FormSLAOrchestrator(db)
    assert orch._routing_contact_id("purchase_request", row.id) is None


def test_complaint_has_no_requestor_fk_always_none(db):
    c = Complaint(id=str(uuid.uuid4()), complaint_number="CMP-1", status="new")
    db.add(c)
    db.commit()

    orch = FormSLAOrchestrator(db)
    assert orch._routing_contact_id("complaint", c.id) is None


def test_unknown_source_entity_type_returns_none(db):
    orch = FormSLAOrchestrator(db)
    assert orch._routing_contact_id("ticket", str(uuid.uuid4())) is None


def test_missing_row_returns_none_never_raises(db):
    orch = FormSLAOrchestrator(db)
    assert orch._routing_contact_id("purchase_request", str(uuid.uuid4())) is None
    assert orch._routing_contact_id("stock_inquiry", str(uuid.uuid4())) is None


def test_none_or_empty_ids_return_none_never_raise(db):
    orch = FormSLAOrchestrator(db)
    assert orch._routing_contact_id(None, "some-id") is None
    assert orch._routing_contact_id("purchase_request", None) is None
    assert orch._routing_contact_id("", "") is None


def test_deleted_requestor_contact_set_null_e7(db):
    """FK is ON DELETE SET NULL: a deleted requestor contact degrades the
    routing lookup to None (E3-equivalent), never an exception."""
    requestor_id = _contact(db)
    row = PurchaseRequestHeader(
        id=str(uuid.uuid4()),
        request_type="purchase_request",
        requested_by_contact_id=requestor_id,
    )
    db.add(row)
    db.commit()

    db.query(RespondContact).filter(RespondContact.id == requestor_id).delete()
    db.commit()

    orch = FormSLAOrchestrator(db)
    assert orch._routing_contact_id("purchase_request", row.id) is None
