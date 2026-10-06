"""``response_write_allowed`` on the two detail responses (UAC O1).

The frontend used to mirror the backend's allowed-status lists in
``lib/response-gate.ts``. Two sources for one rule drift, and when they do the UI
either hides a working button or offers one that 422s. The server now states the
answer on the record, and the client reads it.

So the tests that matter here are not "is the flag true for status X" (that is a
restatement of the table). They are:

* the flag is DECLARED on the response model - a ``response_model`` silently drops
  what it does not name, and a dropped bool reads as "not allowed" on the client,
  hiding the response affordance on a perfectly writable record;
* the flag AGREES with what the write path enforces, status by status. If the flag
  can say yes where the gate says no, the mirror is back - just on one side.

Postgres only, blank scratch schema, every row seeded here under a marker.

Run: venv/bin/pytest tests/test_response_write_allowed_contract.py -q
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.models.complaints import Complaint
from app.models.procurement import StockInquiry
from app.schemas.complaints import ComplaintUpdate
from app.schemas.procurement import StockInquiryUpdate
from app.services.complaints_service import ComplaintService
from app.services.error_handler import AppException
from app.services.procurement_service import StockInquiryService
from tests._pg_fixture import blank_session

MARKER = "ZZT-RWALLOWED"

# A ':' in the last segment passes straight through resolve_send_identifier, so the
# chat resolver finds a sendable identifier without a RespondContact row.
INBOX_URL = "https://app.respond.io/space/364817/inbox/id:60123"

# Every status each type can actually sit in, on both sides of its gate. The test
# derives the expectation from the gate module rather than restating it, so a
# deliberate change to the allowed set moves both at once.
STOCK_INQUIRY_STATUSES = (
    "new",
    "pending_project_sales",
    "pending_purchasing",
    "responded",
    "rejected",
    "voided",
)
COMPLAINT_STATUSES = (
    "new",
    "submitted",
    "updated",
    "responded",
    "approved",
    "rejected",
    "processed_by_cs",
    "closed",
)


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


@pytest.fixture
def client(db, monkeypatch):
    from app.database import get_db
    from app.services.user_service import UserPermissionService

    def _override_get_db():
        yield db

    app.dependency_overrides[get_db] = _override_get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key

    actor = {"id": str(uuid.uuid4()), "email": "office@example.test", "role": "admin"}
    # LIST-API-PERMS: GET /complaints/{id} carries a permission gate now; the actor's "role"
    # key is not what the gate reads, so answer the role lookup as admin.
    monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda self, uid: {"admin"})
    app.dependency_overrides[get_current_user] = lambda: actor
    app.dependency_overrides[get_current_user_or_api_key] = lambda: actor
    try:
        with patch("app.services.queue_service.enqueue_job", return_value=None):
            with TestClient(app) as c:
                yield c
    finally:
        app.dependency_overrides.clear()


def _inquiry(db, *, status: str, purchasing_response=None) -> StockInquiry:
    row = StockInquiry(
        id=str(uuid.uuid4()),
        inquiry_number=f"{MARKER}-{uuid.uuid4().hex[:6]}",
        salesperson=f"{MARKER} salesperson",
        product_code=f"{MARKER}-P",
        status=status,
        purchasing_response=purchasing_response,
        contact_id=f"{MARKER}-contact",
        respond_inbox_url=INBOX_URL,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _complaint(db, *, status: str, technical_team_response=None) -> Complaint:
    row = Complaint(
        id=str(uuid.uuid4()),
        complaint_number=f"{MARKER}-{uuid.uuid4().hex[:6]}",
        customer_name=f"{MARKER} customer",
        status=status,
        technical_team_response=technical_team_response,
        contact_id=f"{MARKER}-contact",
        respond_inbox_url=INBOX_URL,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --------------------------------------------------------------------------- #
# Declared on the wire
# --------------------------------------------------------------------------- #


def test_stock_inquiry_detail_declares_the_flag(client, db):
    inquiry = _inquiry(db, status="pending_purchasing")

    response = client.get(f"/api/v1/procurement/stock-inquiries/{inquiry.id}")
    assert response.status_code == 200, response.text
    assert response.json()["response_write_allowed"] is True


def test_stock_inquiry_detail_says_no_on_a_closed_record(client, db):
    inquiry = _inquiry(db, status="rejected")

    body = client.get(f"/api/v1/procurement/stock-inquiries/{inquiry.id}").json()
    assert body["response_write_allowed"] is False


def test_complaint_detail_declares_the_flag(client, db):
    complaint = _complaint(db, status="submitted")

    response = client.get(f"/api/v1/complaints-management/complaints/{complaint.id}")
    assert response.status_code == 200, response.text
    assert response.json()["response_write_allowed"] is True


def test_complaint_detail_says_no_on_a_decided_record(client, db):
    complaint = _complaint(db, status="approved")

    body = client.get(f"/api/v1/complaints-management/complaints/{complaint.id}").json()
    assert body["response_write_allowed"] is False


# --------------------------------------------------------------------------- #
# The contract: the flag agrees with the gate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", STOCK_INQUIRY_STATUSES)
def test_stock_inquiry_flag_matches_what_the_write_path_enforces(db, status):
    """If the flag ever says yes where the write raises (or the reverse), the
    frontend is back to guessing - which is the duplication this replaced."""
    from app.services.response_gate import STOCK_INQUIRY, is_response_status_allowed

    inquiry = _inquiry(db, status=status, purchasing_response="original answer")
    flag = inquiry.response_write_allowed
    assert flag is is_response_status_allowed(STOCK_INQUIRY, status)

    service = StockInquiryService(db)
    try:
        service.update_inquiry(
            str(inquiry.id),
            StockInquiryUpdate(purchasing_response="a brand new answer"),
        )
        refused = False
    except AppException as exc:
        assert exc.status_code == 422
        refused = True

    assert refused is not flag, (
        f"status {status!r}: response_write_allowed={flag} but the write "
        f"{'was refused' if refused else 'went through'}"
    )


@pytest.mark.parametrize("status", COMPLAINT_STATUSES)
def test_complaint_flag_matches_what_the_write_path_enforces(db, status):
    from app.services.response_gate import COMPLAINT, is_response_status_allowed

    complaint = _complaint(db, status=status, technical_team_response="original answer")
    flag = complaint.response_write_allowed
    assert flag is is_response_status_allowed(COMPLAINT, status)

    service = ComplaintService(db)
    try:
        service.update_complaint(
            str(complaint.id),
            ComplaintUpdate(technical_team_response="a brand new answer"),
        )
        refused = False
    except AppException as exc:
        assert exc.status_code == 422
        refused = True

    assert refused is not flag, (
        f"status {status!r}: response_write_allowed={flag} but the write "
        f"{'was refused' if refused else 'went through'}"
    )


def test_the_flag_follows_a_status_change_with_no_backfill(db):
    """Derived live, never a column. A record that moves out of its response stage
    has to stop advertising the affordance on the very next read."""
    inquiry = _inquiry(db, status="pending_purchasing")
    assert inquiry.response_write_allowed is True

    inquiry.status = "voided"
    db.commit()
    db.refresh(inquiry)
    assert inquiry.response_write_allowed is False


def test_the_flag_is_true_for_every_allowed_status_and_only_those(db):
    """The two sets in ``response_gate`` are the ONE definition. Read them, do not
    restate them: a copy here would be the same drift on a third surface."""
    from app.services.response_gate import ALLOWED_RESPONSE_STATUSES, COMPLAINT, STOCK_INQUIRY

    for status in STOCK_INQUIRY_STATUSES:
        expected = status in ALLOWED_RESPONSE_STATUSES[STOCK_INQUIRY]
        assert _inquiry(db, status=status).response_write_allowed is expected

    for status in COMPLAINT_STATUSES:
        expected = status in ALLOWED_RESPONSE_STATUSES[COMPLAINT]
        assert _complaint(db, status=status).response_write_allowed is expected
