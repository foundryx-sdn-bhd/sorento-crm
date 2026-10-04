"""Tests for the deterministic record-context assembler (complaint tracer).

A blank copy of the real Postgres schema: every table is present with its
production DDL, and rows are seeded directly. No LLM, no network. Writes are
discarded at teardown.

Postgres rather than SQLite because the assembler reads real JSONB audit values
and SLA timestamps, which the old harness only approximated as TEXT.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models.audit import AuditLog
from app.models.complaints import Complaint
from app.models.procurement import PurchaseRequestHeader, StockInquiry
from app.models.sla import ConversationSLATracking, SLAPolicy, SLAPolicyTier
from app.models.user import User
from app.services.error_handler import AppException
from app.services.record_context_service import RecordContextService
from tests._pg_fixture import blank_session


@pytest.fixture
def db_session() -> Session:
    with blank_session() as session:
        yield session


def _seed_user(db: Session, uid: str, name: str) -> None:
    db.add(User(id=uid, email=f"{uid}@test.com", name=name, status="ACTIVE"))


def _seed_complaint(db: Session, **overrides) -> Complaint:
    base = dict(
        id=str(uuid.uuid4()),
        complaint_number="CMP-2026-0142",
        complaint_type="Product defect",
        product_type="Tiles",
        defect_description="Cracked tiles on delivery",
        customer_name="Acme Sdn Bhd",
        status="new",
        created_at=datetime(2026, 6, 20, 0, 0, 0),
    )
    base.update(overrides)
    c = Complaint(**base)
    db.add(c)
    return c


def _seed_status_audit(db: Session, complaint_id: str, old: str, new: str,
                       user_id: str | None, at: datetime) -> None:
    db.add(
        AuditLog(
            id=str(uuid.uuid4()),
            entity_type="complaint",
            entity_id=str(complaint_id),
            action="UPDATE",
            user_id=user_id,
            changed_at=at,
            old_values={"status": old},
            new_values={"status": new},
        )
    )


def _seed_status_audit_for(db: Session, entity_type: str, entity_id: str,
                           old: str, new: str, user_id: str | None,
                           at: datetime) -> None:
    db.add(
        AuditLog(
            id=str(uuid.uuid4()),
            entity_type=entity_type,
            entity_id=str(entity_id),
            action="UPDATE",
            user_id=user_id,
            changed_at=at,
            old_values={"status": old},
            new_values={"status": new},
        )
    )


def test_assemble_rejected_complaint_happy_path(db_session: Session):
    _seed_user(db_session, "5aaa3b4e-f11f-51a0-a954-63632c67ec99", "Jane Lim")
    rejected_at = datetime(2026, 6, 20, 8, 14, 0)
    c = _seed_complaint(
        db_session,
        status="rejected",
        rejection_reason="Out of warranty window",
        rejected_by="5aaa3b4e-f11f-51a0-a954-63632c67ec99",
        rejected_at=rejected_at,
    )
    db_session.flush()
    _seed_status_audit(db_session, c.id, "new", "submitted", "5aaa3b4e-f11f-51a0-a954-63632c67ec99",
                       datetime(2026, 6, 20, 1, 0, 0))
    _seed_status_audit(db_session, c.id, "submitted", "rejected", "5aaa3b4e-f11f-51a0-a954-63632c67ec99",
                       rejected_at)
    db_session.commit()

    out = RecordContextService(db_session).assemble("complaint", c.id)

    assert out["entity_type"] == "complaint"
    assert out["display_ref"] == "CMP-2026-0142"
    # about.description maps to defect_description.
    assert out["about"]["description"] == "Cracked tiles on delivery"
    assert out["about"]["complaint_type"] == "Product defect"

    assert out["current_state"]["status"] == "rejected"
    assert out["current_state"]["reason"] == "Out of warranty window"
    assert out["current_state"]["set_by"] == "Jane Lim"

    approval = out["approval"]
    assert approval["status"] == "rejected"
    assert approval["comments"] == "Out of warranty window"
    assert approval["decided_by"] == "Jane Lim"
    # elapsed = rejected_at - created_at = 8h14m -> 8.2h.
    assert approval["lead_time"]["elapsed_hours"] == pytest.approx(8.2, abs=0.05)
    assert approval["lead_time"]["target_hours"] is None

    # audit_trail newest-first, status-change rows only.
    trail = out["audit_trail"]
    assert len(trail) == 2
    assert trail[0]["action"] == "status: submitted → rejected"
    assert trail[1]["action"] == "status: new → submitted"
    assert trail[0]["by"] == "Jane Lim"

    # No SLA tracker seeded.
    assert out["sla"] is None


def test_assemble_with_form_sla_tracker(db_session: Session):
    _seed_user(db_session, "1a1fa7ab-0926-50b5-bf3c-668eb25d05a9", "Sam Tan")
    c = _seed_complaint(db_session, status="submitted")
    db_session.flush()

    policy = SLAPolicy(id=str(uuid.uuid4()), code="cmp", name="Complaint SLA")
    db_session.add(policy)
    db_session.add(
        SLAPolicyTier(
            id=str(uuid.uuid4()),
            policy_id=policy.id,
            tier_level=2,
            tier_name="Tier 2 Support",
            response_hours=24,
            resolution_hours=48,
        )
    )
    # due_at in the past + not responded => breached.
    past_due = datetime.utcnow() - timedelta(hours=3)
    initiated = datetime.utcnow() - timedelta(hours=30)
    db_session.add(
        ConversationSLATracking(
            id=str(uuid.uuid4()),
            policy_id=policy.id,
            current_tier=2,
            source_entity_type="complaint",
            source_entity_id=str(c.id),
            assigned_to_id="1a1fa7ab-0926-50b5-bf3c-668eb25d05a9",
            initiated_at=initiated,
            current_tier_started_at=initiated,
            due_at=past_due,
            is_responded=False,
            is_resolved=False,
        )
    )
    db_session.commit()

    out = RecordContextService(db_session).assemble("complaint", c.id)

    sla = out["sla"]
    assert sla is not None
    assert sla["current_tier"] == 2
    assert sla["tier_name"] == "Tier 2 Support"
    assert sla["assignee"] == "Sam Tan"
    assert sla["due_at"] is not None
    assert sla["is_breached"] is True
    assert sla["lead_time"]["target_hours"] == pytest.approx(24.0)
    # elapsed ~30h > 24h target -> breached lead time.
    assert sla["lead_time"]["elapsed_hours"] > 24
    assert sla["lead_time"]["breached"] is True


def test_assemble_not_found_raises(db_session: Session):
    with pytest.raises(AppException) as exc:
        RecordContextService(db_session).assemble("complaint", str(uuid.uuid4()))
    assert exc.value.status_code == 404


def test_assemble_unsupported_type_raises(db_session: Session):
    with pytest.raises(AppException) as exc:
        RecordContextService(db_session).assemble("invoice", str(uuid.uuid4()))
    assert exc.value.status_code == 400


def test_assemble_no_approval_block_for_new_complaint(db_session: Session):
    c = _seed_complaint(db_session, status="new")
    db_session.commit()

    out = RecordContextService(db_session).assemble("complaint", c.id)
    assert out["approval"] is None
    assert out["current_state"]["status"] == "new"
    assert out["audit_trail"] == []


# ===========================================================================
# stock_inquiry
# ===========================================================================


def _seed_stock_inquiry(db: Session, **overrides) -> StockInquiry:
    base = dict(
        id=str(uuid.uuid4()),
        inquiry_number="SI-2026-0007",
        salesperson="Lee Wong",
        product_code="TILE-900",
        item_description="40 boxes of porcelain tiles, urgent",
        project_name="Marina Bay Showroom",
        status="new",
        created_at=datetime(2026, 6, 21, 0, 0, 0),
    )
    base.update(overrides)
    row = StockInquiry(**base)
    db.add(row)
    return row


def test_assemble_stock_inquiry_rejected(db_session: Session):
    _seed_user(db_session, "c1b27089-bd7f-5e30-b5d6-1fb1ca38dfcb", "Nora Aziz")
    rejected_at = datetime(2026, 6, 21, 5, 30, 0)
    si = _seed_stock_inquiry(
        db_session,
        status="rejected",
        rejection_reason="Item discontinued - no stock available",
        rejected_by="c1b27089-bd7f-5e30-b5d6-1fb1ca38dfcb",
        rejected_at=rejected_at,
    )
    db_session.flush()
    _seed_status_audit_for(db_session, "stock_inquiry", si.id, "new", "rejected",
                           "c1b27089-bd7f-5e30-b5d6-1fb1ca38dfcb", rejected_at)
    db_session.commit()

    out = RecordContextService(db_session).assemble("stock_inquiry", si.id)

    assert out["entity_type"] == "stock_inquiry"
    assert out["display_ref"] == "SI-2026-0007"
    assert out["about"]["title"] == "Marina Bay Showroom"
    assert out["about"]["description"] == "40 boxes of porcelain tiles, urgent"
    assert out["about"]["salesperson"] == "Lee Wong"

    assert out["current_state"]["status"] == "rejected"
    assert out["current_state"]["reason"] == "Item discontinued - no stock available"
    assert out["current_state"]["set_by"] == "Nora Aziz"

    approval = out["approval"]
    assert approval["status"] == "rejected"
    assert approval["comments"] == "Item discontinued - no stock available"
    assert approval["decided_by"] == "Nora Aziz"
    assert approval["lead_time"]["elapsed_hours"] == pytest.approx(5.5, abs=0.05)

    # No SLA tracker seeded.
    assert out["sla"] is None

    # audit_trail uses stock_inquiry entity_type, status-change rows only.
    assert len(out["audit_trail"]) == 1
    assert out["audit_trail"][0]["action"] == "status: new → rejected"


def test_assemble_stock_inquiry_pending_has_no_decision(db_session: Session):
    si = _seed_stock_inquiry(db_session, status="pending_purchasing")
    db_session.commit()

    out = RecordContextService(db_session).assemble("stock_inquiry", si.id)
    assert out["current_state"]["status"] == "pending_purchasing"
    assert out["current_state"]["reason"] is None
    # Stock inquiry has no approval gate - a non-rejected inquiry is NOT
    # "pending approval"; the decision block is null until/unless it is rejected.
    assert out["approval"] is None
    # No response sent yet at this stage.
    assert out["response"] is None


def test_assemble_stock_inquiry_responded_captures_responder(db_session: Session):
    _seed_user(db_session, "5a100be9-6c93-5a7a-9c7a-1eb22334ba36", "CONTACT AC")
    responded_at = datetime(2026, 5, 22, 6, 47, 0)
    si = _seed_stock_inquiry(
        db_session,
        status="responded",
        last_responded_by="5a100be9-6c93-5a7a-9c7a-1eb22334ba36",
        last_responded_at=responded_at,
        purchasing_response="incoming eta 30.05.2026",
        created_at=datetime(2026, 5, 22, 0, 0, 0),
    )
    db_session.commit()

    out = RecordContextService(db_session).assemble("stock_inquiry", si.id)
    assert out["current_state"]["status"] == "responded"
    # "who set the current state" must resolve to the responder, not None.
    assert out["current_state"]["set_by"] == "CONTACT AC"
    # response block surfaces who/when/what so "who responded" is answerable.
    assert out["response"] is not None
    assert out["response"]["responded_by"] == "CONTACT AC"
    assert out["response"]["summary"] == "incoming eta 30.05.2026"
    assert out["response"]["lead_time_hours"] == 6.8
    # A responded inquiry is answered, NOT awaiting an approval decision.
    assert out["approval"] is None


# ===========================================================================
# purchase_request / sponsorship_form (shared PurchaseRequestHeader model)
# ===========================================================================


import pytest as _pytest


@_pytest.mark.parametrize(
    "status, approval_status, expected",
    [
        ("submitted", "pending", "Pending approval"),  # the reported bug
        ("draft", "pending", "Pending approval"),
        ("approved", "approved", "Approved"),
        ("rejected", "rejected", "Rejected"),
        ("processed_by_cs", "approved", "Processed by CS"),  # terminal wins
        ("closed", "approved", "Closed"),
        ("submitted", None, "Submitted"),
        ("draft", None, "Draft"),
    ],
)
def test_pr_display_status_combines_lifecycle_and_approval(
    db_session: Session, status, approval_status, expected
):
    pr = _seed_request(
        db_session, "purchase_request", status=status, approval_status=approval_status
    )
    db_session.commit()
    out = RecordContextService(db_session).assemble("purchase_request", pr.id)
    # The bubble must present ONE status matching the FE pill - never the raw
    # "submitted status + pending approval_status" split that confused the user.
    assert out["current_state"]["status"] == expected


def _seed_request(db: Session, request_type: str, **overrides) -> PurchaseRequestHeader:
    base = dict(
        id=str(uuid.uuid4()),
        request_type=request_type,
        request_number="PR-2026-0033",
        project_title="HQ Renovation",
        purpose="Procure flooring for level 3",
        customer_name="Internal - Facilities",
        status="draft",
        source="external",
        created_at=datetime(2026, 6, 22, 0, 0, 0),
    )
    base.update(overrides)
    row = PurchaseRequestHeader(**base)
    db.add(row)
    return row


def test_assemble_purchase_request_approved(db_session: Session):
    _seed_user(db_session, "c8b2e855-44d9-5220-8451-e03dc33815bb", "Daniel Ong")
    approved_at = datetime(2026, 6, 22, 10, 0, 0)
    pr = _seed_request(
        db_session,
        "purchase_request",
        status="approved",
        approval_status="approved",
        approved_at=approved_at,
        approved_by="daniel.ong@example.com",
        approver_user_id="c8b2e855-44d9-5220-8451-e03dc33815bb",
        approval_comments="Approved within budget",
    )
    db_session.commit()

    out = RecordContextService(db_session).assemble("purchase_request", pr.id)

    assert out["entity_type"] == "purchase_request"
    assert out["display_ref"] == "PR-2026-0033"
    assert out["about"]["title"] == "HQ Renovation"
    assert out["about"]["description"] == "Procure flooring for level 3"
    # purchase_request has no sponsorship extras.
    assert "sponsor_subject" not in out["about"]

    # PR/SF surface the single user-facing status (combines lifecycle + approval,
    # mirrors the FE pill) - not the raw lifecycle column.
    assert out["current_state"]["status"] == "Approved"
    assert out["current_state"]["set_by"] == "Daniel Ong"

    approval = out["approval"]
    assert approval["status"] == "approved"
    # FK user id preferred over the email text for the name.
    assert approval["decided_by"] == "Daniel Ong"
    assert approval["decided_at"] is not None
    assert approval["comments"] == "Approved within budget"
    # elapsed = approved_at - created_at = 10h.
    assert approval["lead_time"]["elapsed_hours"] == pytest.approx(10.0, abs=0.05)

    assert out["sla"] is None


def test_assemble_purchase_request_rejected_uses_approval_comments(db_session: Session):
    rejected_at = datetime(2026, 6, 22, 6, 0, 0)
    pr = _seed_request(
        db_session,
        "purchase_request",
        status="rejected",
        approval_status="rejected",
        approved_at=rejected_at,
        approved_by="manager@example.com",
        approval_comments="Over budget - resubmit with quotes",
    )
    db_session.commit()

    out = RecordContextService(db_session).assemble("purchase_request", pr.id)

    # User-facing combined status (FE pill), not the raw lifecycle column.
    assert out["current_state"]["status"] == "Rejected"
    assert out["current_state"]["reason"] == "Over budget - resubmit with quotes"
    approval = out["approval"]
    assert approval["status"] == "rejected"
    assert approval["comments"] == "Over budget - resubmit with quotes"
    # No FK user, falls back to the email text.
    assert approval["decided_by"] == "manager@example.com"


def test_assemble_sponsorship_form(db_session: Session):
    _seed_user(db_session, "da194ca8-1d20-51ef-8b9a-f84eadbe4864", "Priya Nair")
    approved_at = datetime(2026, 6, 23, 4, 0, 0)
    sf = _seed_request(
        db_session,
        "sponsorship_form",
        request_number="SF-2026-0009",
        project_title="Mall Roadshow",
        purpose="Showroom sponsorship",
        status="approved",
        approval_status="approved",
        approved_at=approved_at,
        approver_user_id="da194ca8-1d20-51ef-8b9a-f84eadbe4864",
        approval_comments="Good brand exposure",
        sponsor_subject="showroom",
        total_project_value_text="EST RM1.2MIL",
        created_at=datetime(2026, 6, 23, 0, 0, 0),
    )
    db_session.flush()
    # Sponsorship audits under "purchase_request" (shared with PR).
    _seed_status_audit_for(db_session, "purchase_request", sf.id, "pending",
                           "approved", "da194ca8-1d20-51ef-8b9a-f84eadbe4864", approved_at)

    # SLA tracker keyed by "sponsorship_form" (distinct from purchase_request).
    policy = SLAPolicy(id=str(uuid.uuid4()), code="sf", name="Sponsorship SLA")
    db_session.add(policy)
    db_session.add(
        SLAPolicyTier(
            id=str(uuid.uuid4()),
            policy_id=policy.id,
            tier_level=1,
            tier_name="Tier 1",
            response_hours=8,
            resolution_hours=24,
        )
    )
    initiated = datetime.utcnow() - timedelta(hours=2)
    db_session.add(
        ConversationSLATracking(
            id=str(uuid.uuid4()),
            policy_id=policy.id,
            current_tier=1,
            source_entity_type="sponsorship_form",
            source_entity_id=str(sf.id),
            assigned_to_id="da194ca8-1d20-51ef-8b9a-f84eadbe4864",
            initiated_at=initiated,
            current_tier_started_at=initiated,
            due_at=datetime.utcnow() + timedelta(hours=6),
            is_responded=False,
            is_resolved=False,
        )
    )
    db_session.commit()

    out = RecordContextService(db_session).assemble("sponsorship_form", sf.id)

    assert out["entity_type"] == "sponsorship_form"
    assert out["display_ref"] == "SF-2026-0009"
    # sponsorship-only about extras.
    assert out["about"]["sponsor_subject"] == "showroom"
    assert out["about"]["project_value"] == "EST RM1.2MIL"

    assert out["approval"]["status"] == "approved"
    assert out["approval"]["decided_by"] == "Priya Nair"

    # audit_trail resolved via the shared "purchase_request" entity_type.
    assert len(out["audit_trail"]) == 1
    assert out["audit_trail"][0]["action"] == "status: pending → approved"

    # SLA tracker resolved via the distinct "sponsorship_form" source type.
    sla = out["sla"]
    assert sla is not None
    assert sla["current_tier"] == 1
    assert sla["tier_name"] == "Tier 1"
    assert sla["assignee"] == "Priya Nair"


def test_sponsorship_filter_excludes_purchase_request_id(db_session: Session):
    # A purchase_request row must NOT resolve under the sponsorship_form adapter.
    pr = _seed_request(db_session, "purchase_request", status="draft")
    db_session.commit()

    with pytest.raises(AppException) as exc:
        RecordContextService(db_session).assemble("sponsorship_form", pr.id)
    assert exc.value.status_code == 404


def test_assemble_stock_inquiry_not_found(db_session: Session):
    with pytest.raises(AppException) as exc:
        RecordContextService(db_session).assemble("stock_inquiry", str(uuid.uuid4()))
    assert exc.value.status_code == 404


def test_assemble_purchase_request_not_found(db_session: Session):
    with pytest.raises(AppException) as exc:
        RecordContextService(db_session).assemble("purchase_request", str(uuid.uuid4()))
    assert exc.value.status_code == 404
