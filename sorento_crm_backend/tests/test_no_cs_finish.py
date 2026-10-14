"""RED tests - FORM-ROUTING-CONDITIONS group NC (No CS outcome + the Completed status).

* NC-3  requestor's winning rule is No CS (cs_pic_user_id NULL): approving creates NO
        customer_service tracker, status becomes `completed`, approval_status stays `approved`,
        the approval-stage tracker resolves as today.
* NC-4  a PIC rule, or no matching rule: unchanged (tracker created, status stays `approved`).
* NC-5  `completed` is terminal: Void -> 409, Processed by CS / Close -> validation error,
        portal revision refuses it with its own "completed" sentence.

The approval is exercised through `FormSLAOrchestrator.emit_event(..., "approved")`, the single
funnel the approve route uses. Seed chain borrowed from tests/test_cs_routing_targets.py.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.main import app  # noqa: E402
from app.models.sla import ConversationSLATracking, FormSLAConfig
from app.models.procurement import PurchaseRequestHeader
from app.services.form_sla_service import FormSLAOrchestrator
from tests._pg_fixture import blank_session, unique_code
from tests.test_cs_routing_targets import _pin, _policy, _seed


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _cs_config(db, policy_id, kind) -> FormSLAConfig:
    cfg = FormSLAConfig(
        id=str(uuid.uuid4()),
        source_entity_type=kind,
        stage_code=unique_code("cs"),
        policy_id=policy_id,
        agent_code="purchase_request",
        team_set_code="customer_service",
        start_event="approved",
        resolve_event="resolved,voided",
        is_active=True,
        notify_assignee=False,
    )
    db.add(cfg)
    db.flush()
    return cfg


def _approval_stage(db, policy_id, kind, entity_id) -> ConversationSLATracking:
    """The approval stage as it stands when the manager approves: one open tracker."""
    cfg = FormSLAConfig(
        id=str(uuid.uuid4()),
        source_entity_type=kind,
        stage_code=unique_code("appr"),
        policy_id=policy_id,
        agent_code="purchase_request",
        team_set_code="approval",
        start_event="send_for_approval",
        resolve_event="approved,rejected",
        is_active=True,
        notify_assignee=False,
    )
    db.add(cfg)
    now = datetime.utcnow()
    tracker = ConversationSLATracking(
        id=str(uuid.uuid4()),
        policy_id=policy_id,
        current_tier=1,
        initiated_at=now - timedelta(hours=2),
        current_tier_started_at=now - timedelta(hours=1),
        due_at=now + timedelta(hours=4),
        due_at_resolution=now + timedelta(hours=20),
        is_responded=False,
        is_resolved=False,
        source_entity_type=kind,
        source_entity_id=entity_id,
        team_set_code="approval",
    )
    db.add(tracker)
    db.flush()
    return tracker


def _approved_form(db, kind, contact_id) -> str:
    pid = str(uuid.uuid4())
    db.add(
        PurchaseRequestHeader(
            id=pid,
            request_type=kind,
            request_number=unique_code("PR"),
            status="approved",
            approval_status="approved",
            source="manual",
            contact_id=contact_id,
            requested_by_contact_id=contact_id,
            sales_type="project",
        )
    )
    db.flush()
    return pid


def _cs_trackers(db, pid):
    return (
        db.query(ConversationSLATracking)
        .filter(
            ConversationSLATracking.source_entity_id == pid,
            ConversationSLATracking.team_set_code == "customer_service",
        )
        .all()
    )


def _world(db, kind, *, rule):
    """rule: 'no_cs' | 'pic' | 'none' (no rule at all)."""
    s = _seed(db)
    policy = _policy(db)
    _cs_config(db, policy, kind)
    if rule == "no_cs":
        _pin(db, s.contact_id, None, use_case=kind, conditions=[])
    elif rule == "pic":
        _pin(db, s.contact_id, s.outside, use_case=kind, conditions=[])
    pid = _approved_form(db, kind, s.contact_id)
    approval = _approval_stage(db, policy, kind, pid)
    return s, pid, approval


def _approve(db, kind, pid, contact_id):
    FormSLAOrchestrator(db).emit_event(kind, pid, "approved", contact_id=contact_id)
    db.expire_all()
    return db.query(PurchaseRequestHeader).filter(PurchaseRequestHeader.id == pid).one()


# ------------------------------------------------------------------ NC-3


@pytest.mark.parametrize("kind", ["purchase_request", "sponsorship_form"])
def test_no_cs_rule_completes_the_form_at_approval_with_no_cs_tracker(kind):
    with blank_session() as db:
        s, pid, approval = _world(db, kind, rule="no_cs")

        header = _approve(db, kind, pid, s.contact_id)

        assert _cs_trackers(db, pid) == []
        assert header.status == "completed"
        assert header.approval_status == "approved"
        db.refresh(approval)
        assert approval.is_resolved is True


def test_the_winning_rule_decides_not_any_no_cs_rule():
    """A No CS rule that does not match the form is ignored: the matching PIC rule wins."""
    with blank_session() as db:
        s = _seed(db)
        policy = _policy(db)
        _cs_config(db, policy, "sponsorship_form")
        _pin(
            db, s.contact_id, None, use_case="sponsorship_form", priority=0,
            conditions=[{"field": "product_lines", "operator": "equals", "value": "none"}],
        )
        _pin(db, s.contact_id, s.outside, use_case="sponsorship_form", priority=1, conditions=[])
        pid = _approved_form(db, "sponsorship_form", s.contact_id)
        from app.models.procurement import PurchaseRequestLine

        db.add(PurchaseRequestLine(id=str(uuid.uuid4()), purchase_request_id=pid, item_code="ITEM-A",
                                   quantity=1, unit_price=1, sort_order=0))
        db.flush()

        header = _approve(db, "sponsorship_form", pid, s.contact_id)

        assert [t.assigned_to_id for t in _cs_trackers(db, pid)] == [s.outside]
        assert header.status == "approved"


def test_no_cs_rule_matching_a_no_lines_form_completes_it():
    with blank_session() as db:
        s = _seed(db)
        policy = _policy(db)
        _cs_config(db, policy, "sponsorship_form")
        _pin(
            db, s.contact_id, None, use_case="sponsorship_form", priority=0,
            conditions=[{"field": "product_lines", "operator": "equals", "value": "none"}],
        )
        _pin(db, s.contact_id, s.outside, use_case="sponsorship_form", priority=1, conditions=[])
        pid = _approved_form(db, "sponsorship_form", s.contact_id)

        header = _approve(db, "sponsorship_form", pid, s.contact_id)

        assert _cs_trackers(db, pid) == []
        assert header.status == "completed"


# ------------------------------------------------------------------ NC-4 (unchanged paths)


@pytest.mark.parametrize("kind", ["purchase_request", "sponsorship_form"])
def test_pic_rule_still_creates_the_cs_tracker_and_status_stays_approved(kind):
    with blank_session() as db:
        s, pid, _ = _world(db, kind, rule="pic")

        header = _approve(db, kind, pid, s.contact_id)

        assert [t.assigned_to_id for t in _cs_trackers(db, pid)] == [s.outside]
        assert header.status == "approved"


@pytest.mark.parametrize("kind", ["purchase_request", "sponsorship_form"])
def test_no_matching_rule_still_round_robins_and_status_stays_approved(kind):
    with blank_session() as db:
        s, pid, _ = _world(db, kind, rule="none")

        header = _approve(db, kind, pid, s.contact_id)

        assert [t.assigned_to_id for t in _cs_trackers(db, pid)] == [s.in_team]
        assert header.status == "approved"


# ------------------------------------------------------------------ NC-5 terminal


def test_void_on_a_completed_form_is_a_conflict():
    import tests._void_harness as H
    from app.services.procurement_service import PurchaseRequestService

    db = H.make_session()
    try:
        uid = H.new_user(db)
        H.ACTOR_GRANTS.clear()
        H.ACTOR_GRANTS[uid] = {"procurement.purchase_requests.void"}
        mp = pytest.MonkeyPatch()
        try:
            H.patch_permissions(mp)
            H.patch_serializers(mp)
            mp.setattr(PurchaseRequestService, "_notify_request_voided", lambda self, *a, **k: None)
            pid = H.new_pr(db, request_type="sponsorship_form", status="completed", approval_status="approved")
            r = H.make_client(db, {"id": uid}).post(
                f"/api/v1/procurement/purchase-requests/{pid}/void", json={"void_reason": "too late"}
            )
            assert r.status_code == 409, r.text
        finally:
            mp.undo()
            H.ACTOR_GRANTS.clear()
    finally:
        db.close()


@pytest.mark.parametrize("action", ["mark_processed_by_cs", "close_request"])
def test_process_and_close_refuse_a_completed_form(action):
    """Guard: `completed` is not in the finalize-from list, so this must keep failing."""
    from app.services.error_handler import AppException
    from app.services.procurement_service import PurchaseRequestService

    with blank_session() as db:
        s = _seed(db)
        pid = _approved_form(db, "sponsorship_form", s.contact_id)
        db.query(PurchaseRequestHeader).filter_by(id=pid).update({"status": "completed"})
        db.flush()

        with pytest.raises(AppException) as exc:
            getattr(PurchaseRequestService(db), action)(pid)

        assert exc.value.status_code in (400, 409, 422)


@pytest.mark.parametrize("kind", ["purchase_request", "sponsorship_form"])
def test_portal_revision_refuses_a_completed_form_with_its_own_sentence(kind):
    from app.services.portal_revision_service import PortalRevisionService
    from tests._revision_harness import seed_config, seed_contact, seed_entity

    with blank_session() as db:
        seed_config(db, kind)
        contact = seed_contact(db)
        row = seed_entity(db, kind, contact, status="completed")

        policy = PortalRevisionService(db).resolve_policy(kind, row)

        assert policy.allowed is False
        assert "completed" in policy.blocked_reason
        assert "can no longer be revised" in policy.blocked_reason
