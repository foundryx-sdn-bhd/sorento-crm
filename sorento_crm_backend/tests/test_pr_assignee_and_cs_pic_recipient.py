"""PR/sponsorship list assignee resolution + "Assigned CS PIC" automation recipient.

- _attach_sla_assignees sets assigned_to_id/name from the latest unresolved
  form-SLA tracker (project-sales pre-approval, CS post-approval).
- resolve_recipients with include_assigned_cs_pic emails the active CS-stage
  tracker's assigned user for the triggering entity.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models.sla import ConversationSLATracking
from app.models.user import User
from app.services import automation_recipients
from app.services.procurement_service import PurchaseRequestService


def _query_returning(rows_by_model):
    db = MagicMock()

    def _query(model):
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = rows_by_model.get(model, [])
        q.first.return_value = (rows_by_model.get(model) or [None])[0]
        return q

    db.query.side_effect = _query
    return db


def test_attach_assignees_uses_latest_unresolved_tracker():
    tracker = SimpleNamespace(
        source_entity_id="ed46d5e7-d871-5921-b065-69e87db348fd", assigned_to_id="u1", initiated_at=2
    )
    user = SimpleNamespace(id="u1", name="Project Sales Exec", email="ps@x.com")
    db = _query_returning({ConversationSLATracking: [tracker], User: [user]})
    svc = PurchaseRequestService(db)
    item = SimpleNamespace(id="ed46d5e7-d871-5921-b065-69e87db348fd")
    svc._attach_sla_assignees([item])
    assert item.assigned_to_id == "u1"
    assert item.assigned_to_name == "Project Sales Exec"


def test_attach_assignees_none_when_no_tracker():
    db = _query_returning({ConversationSLATracking: [], User: []})
    svc = PurchaseRequestService(db)
    item = SimpleNamespace(id="ed46d5e7-d871-5921-b065-69e87db348fd")
    svc._attach_sla_assignees([item])
    assert item.assigned_to_id is None
    assert item.assigned_to_name is None


def test_cs_pic_recipient_resolves_active_cs_tracker_assignee():
    tracker = SimpleNamespace(
        source_entity_id="ed46d5e7-d871-5921-b065-69e87db348fd",
        team_set_code="customer_service",
        is_resolved=False,
        assigned_to_id="agnes",
        initiated_at=1,
    )
    pic = SimpleNamespace(id="agnes", name="Agnes", email="person9@example.com", is_trashed=False)
    db = _query_returning({ConversationSLATracking: [tracker], User: [pic]})
    recips = automation_recipients.resolve_recipients(
        db, {"include_assigned_cs_pic": True}, source_id="ed46d5e7-d871-5921-b065-69e87db348fd"
    )
    assert recips == [
        {"email": "person9@example.com", "name": "Agnes", "user_id": "agnes"}
    ]


def test_cs_pic_recipient_noop_without_source_id():
    db = MagicMock()
    assert automation_recipients.resolve_recipients(db, {"include_assigned_cs_pic": True}) == []


def test_cs_pic_recipient_noop_when_flag_off():
    db = MagicMock()
    assert automation_recipients.resolve_recipients(db, {}, source_id="ed46d5e7-d871-5921-b065-69e87db348fd") == []
