"""RED tests - FORM-ROUTING-CONDITIONS group NC (No CS outcome; owner ruling 14 Oct 22:02:
a No CS form STAYS Approved, there is no Completed status).

* NC-3  requestor's winning rule is No CS (cs_pic_user_id NULL): approving creates NO
        customer_service tracker, status AND approval_status stay `approved`, the approval-stage
        tracker resolves as today.
* NC-4  a PIC rule, or no matching rule: unchanged (tracker created, status stays `approved`).
* NC-5  a No CS form behaves like any Approved form: Void allowed, portal revision allowed when
        the config allows `approved`, Processed by CS not refused by the backend. No `completed`
        value exists in the void-blocked / revision-terminal / report constants.

The approval is exercised through `FormSLAOrchestrator.emit_event(..., "approved")`, the single
funnel the approve route uses. Seed chain borrowed from tests/test_cs_routing_targets.py.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch
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
def test_no_cs_rule_leaves_the_form_approved_with_no_cs_tracker(kind):
    with blank_session() as db:
        s, pid, approval = _world(db, kind, rule="no_cs")

        header = _approve(db, kind, pid, s.contact_id)

        assert _cs_trackers(db, pid) == []
        assert header.status == "approved"
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


def test_no_cs_rule_matching_a_no_lines_form_leaves_it_approved():
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
        assert header.status == "approved"


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


# ------------------------------------------------------------------ NC-5 behaves like any Approved form


def _no_cs_approved(db, kind="sponsorship_form"):
    s, pid, _ = _world(db, kind, rule="no_cs")
    header = _approve(db, kind, pid, s.contact_id)
    assert _cs_trackers(db, pid) == []
    return s, pid, header


def test_void_is_allowed_on_a_no_cs_approved_form():
    from app.services.procurement_service import PurchaseRequestService

    with blank_session() as db:
        s, pid, _ = _no_cs_approved(db)
        svc = PurchaseRequestService(db)
        with patch.object(PurchaseRequestService, "_notify_request_voided", lambda self, *a, **k: None):
            svc.void_request(pid, void_reason="zzt void after no cs")
        db.expire_all()
        assert db.query(PurchaseRequestHeader).filter_by(id=pid).one().status == "voided"


@pytest.mark.parametrize("kind", ["purchase_request", "sponsorship_form"])
def test_portal_revision_is_allowed_on_a_no_cs_approved_form(kind):
    from app.services.portal_revision_service import PortalRevisionService
    from tests._revision_harness import seed_config

    with blank_session() as db:
        _, pid, header = _no_cs_approved(db, kind)
        seed_config(db, kind)  # allowed statuses: submitted, approved

        policy = PortalRevisionService(db).resolve_policy(kind, header)

        assert policy.allowed is True, policy.blocked_reason


@pytest.mark.parametrize("action", ["mark_processed_by_cs", "close_request"])
def test_processed_by_cs_and_close_are_not_refused_on_a_no_cs_approved_form(action):
    """Guard: the backend treats it as any approved form (the FE hides the buttons)."""
    from tests.test_purchase_request_status_not_editable import _FakeSend
    from app.services.procurement_service import PurchaseRequestService

    with blank_session() as db:
        s, pid, _ = _no_cs_approved(db)
        send = _FakeSend()
        with patch("app.services.respond_messaging_service.send_text_or_template", send), patch(
            "app.services.respond_messaging_service.build_context_vars", return_value={}
        ), patch("app.services.crm_chat_outbound_webhook.enqueue_crm_chat_outbound_webhook"):
            getattr(PurchaseRequestService(db), action)(pid)
        db.expire_all()
        expected = "processed_by_cs" if action == "mark_processed_by_cs" else "closed"
        assert db.query(PurchaseRequestHeader).filter_by(id=pid).one().status == expected


def test_there_is_no_completed_status_anywhere_in_the_lane_code():
    from app.services.portal_revision_service import ADAPTERS
    from app.services.procurement_service import PurchaseRequestService
    from app.services.reports.datasets import sponsorship_forms as ds

    for cls in PurchaseRequestService.__mro__:
        assert "completed" not in getattr(cls, "_VOID_BLOCKED_STATUSES", frozenset())
    for kind in ("purchase_request", "sponsorship_form"):
        assert "completed" not in ADAPTERS[kind].terminal_statuses
    assert "completed" not in ds.DEFAULT_STATUSES
    assert "completed" not in [v for v, _ in ds.STATUS_LABELS]
    assert "Completed" not in [label for _, label in ds.STATUS_LABELS]
