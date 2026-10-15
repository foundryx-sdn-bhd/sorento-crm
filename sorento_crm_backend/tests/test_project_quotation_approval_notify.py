"""QUOTE-APPROVAL-UX (15 Oct 2026): who is told when a quotation moves along the approval
ladder. UAC B1-B5 and D5. Written BEFORE the implementation.

The notification backend is replaced at its one door,
``NotificationService.create_with_channel_preferences``, with a recorder. That is what
``project_notify_service._send`` calls for every recipient, so the recorder sees exactly who
would be told, over which channels, and with what data, without touching Redis, SMTP or the
real ``notifications`` table. Postgres only, via ``blank_session``; every user is seeded.
"""
from __future__ import annotations

import pytest

from app.models.user import User

from ._pg_fixture import blank_session
from .test_project_quotation_approval import (
    APPROVE,
    BELOW_FLOOR,
    EVERYTHING,
    _price,
    _setup,
    _status,
)
from .test_project_quotation_approval_routes import (  # noqa: F401  (api is a fixture)
    BASE,
    BELOW_FLOOR as ROUTE_BELOW_FLOOR,
    _grant,
    _quotation,
    _status_id,
    api,
)

MARKER = "zzt-qapprnotify"

REQUESTED = "project_quotation_approval_requested"
APPROVED = "project_quotation_approval_approved"
REJECTED = "project_quotation_approval_rejected"
WITHDRAWN = "project_quotation_approval_withdrawn"


@pytest.fixture()
def sent(monkeypatch):
    """Every notification the code under test tried to send, in order."""
    from app.services.notification_service import NotificationService

    calls: list[dict] = []

    def fake(self, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(NotificationService, "create_with_channel_preferences", fake)
    return calls


def _active(db, name: str, **extra) -> str:
    from app.models.user import UserStatus

    import uuid

    user_id = str(uuid.uuid4())
    db.add(
        User(
            id=user_id,
            email=f"{user_id}@zzt.test",
            name=name,
            status=extra.pop("status", UserStatus.ACTIVE.value),
            **extra,
        )
    )
    db.flush()
    return user_id


def _approver(db, name: str, **extra) -> str:
    user_id = _active(db, name, **extra)
    _grant(db, user_id, APPROVE)
    return user_id


def _of_type(calls: list[dict], notif_type: str) -> list[dict]:
    return [c for c in calls if c.get("type") == notif_type]


def _ask(db, document, actor: str) -> None:
    from app.services import project_quotation_approval_service as approval

    approval.move(
        db,
        document=document,
        to_status_id=_status(db, "pending_approval").id,
        actor_user_id=actor,
    )


def test_b1_sending_for_approval_tells_every_active_non_integration_approver(sent):
    with blank_session() as db:
        _, owner, product, project, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)
        first = _approver(db, f"{MARKER} A")
        second = _approver(db, f"{MARKER} B")
        _approver(db, f"{MARKER} Bot", is_integration=True)
        _approver(db, f"{MARKER} Gone", status="INACTIVE")
        _approver(db, f"{MARKER} Trashed", is_trashed=True)
        _active(db, f"{MARKER} No grant")

        _ask(db, document, owner)

        calls = _of_type(sent, REQUESTED)
        assert sorted(c["user_id"] for c in calls) == sorted([first, second])
        for call in calls:
            assert call["data"]["project_id"] == str(project.id)
            assert call["data"]["document_id"] == str(document.id)
            assert call["data"]["document_no"] == document.document_no
            assert call["send_in_app"] is True
            assert call["send_email"] is True
            assert call["send_whatsapp"] is True


def test_b2_approving_tells_the_salesperson_who_sent_it_and_not_the_approver(sent):
    from app.services import project_quotation_approval_service as approval

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)
        sender = _active(db, f"{MARKER} Sender")  # not the project owner on purpose
        manager = _approver(db, f"{MARKER} Manager")
        _ask(db, document, sender)
        del sent[:]

        approval.approve(
            db, document=document, actor_user_id=manager, permissions=EVERYTHING
        )

        calls = _of_type(sent, APPROVED)
        assert [c["user_id"] for c in calls] == [sender]
        assert calls[0]["data"]["document_id"] == str(document.id)
        assert calls[0]["send_in_app"] is True
        assert calls[0]["send_email"] is True
        assert calls[0]["send_web_push"] is True
        assert manager not in [c["user_id"] for c in sent]


def test_b2_with_no_request_activity_the_project_owner_is_told(sent):
    from app.services import project_quotation_approval_service as approval

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)
        manager = _approver(db, f"{MARKER} Manager")
        # On the rung without the "requested" activity row (imported / pre-feature data).
        approval._set_status(db, document, _status(db, "pending_approval"))

        approval.approve(
            db, document=document, actor_user_id=manager, permissions=EVERYTHING
        )

        assert [c["user_id"] for c in _of_type(sent, APPROVED)] == [owner]


def test_b3_rejecting_tells_the_same_recipient_and_the_body_carries_the_reason(sent):
    from app.services import project_quotation_approval_service as approval

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)
        sender = _active(db, f"{MARKER} Sender")
        manager = _approver(db, f"{MARKER} Manager")
        _ask(db, document, sender)
        del sent[:]

        approval.reject(
            db,
            document=document,
            actor_user_id=manager,
            reason="Bring the WC suite back to RM 240.",
            permissions=EVERYTHING,
        )

        calls = _of_type(sent, REJECTED)
        assert [c["user_id"] for c in calls] == [sender]
        assert "Bring the WC suite back to RM 240." in calls[0]["body"]
        assert calls[0]["send_web_push"] is True
        assert calls[0]["send_email"] is True


def test_b4_a_resubmit_from_rejected_tells_the_approvers_again_once_per_press(sent):
    from app.services import project_quotation_approval_service as approval

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)
        manager = _approver(db, f"{MARKER} Manager")

        _ask(db, document, owner)
        approval.reject(
            db,
            document=document,
            actor_user_id=manager,
            reason="Too low.",
            permissions=EVERYTHING,
        )
        _ask(db, document, owner)  # C1: straight from Rejected

        calls = [c for c in _of_type(sent, REQUESTED) if c["user_id"] == manager]
        assert len(calls) == 2, "one notification per press, not one per document"
        # The real service dedups on (user, entity, dedup_key, event_type) for ever, so the
        # second press only gets through if its key differs from the first press's.
        assert calls[0]["dedup_key"] != calls[1]["dedup_key"]


def test_d5_revise_from_pending_tells_the_approvers_it_was_withdrawn(sent):
    from app.services import project_quotation_approval_service as approval

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)
        first = _approver(db, f"{MARKER} A")
        _approver(db, f"{MARKER} Bot", is_integration=True)
        _ask(db, document, owner)
        del sent[:]

        approval.revise_approval(
            db, document, owner, "Customer changed the spec"
        )

        calls = _of_type(sent, WITHDRAWN)
        assert [c["user_id"] for c in calls] == [first]
        assert calls[0]["data"]["document_id"] == str(document.id)


def test_d5_revise_from_approved_tells_nobody(sent):
    from app.services import project_quotation_approval_service as approval

    with blank_session() as db:
        _, owner, product, _, document, scope = _setup(db)
        _price(db, scope, product, owner, BELOW_FLOOR)
        manager = _approver(db, f"{MARKER} Manager")
        _ask(db, document, owner)
        approval.approve(
            db, document=document, actor_user_id=manager, permissions=EVERYTHING
        )
        del sent[:]

        approval.revise_approval(db, document, owner, "Customer changed the spec")

        assert sent == []
        assert approval.current_key(db, document) == "draft"


def test_b5_a_notification_backend_that_raises_never_fails_the_action(api, monkeypatch):
    """Route level: send, approve and reject all answer 200 and the rung still moves."""
    from app.services.notification_service import NotificationService

    client, db, _, _, project, product = api
    # The recipient set must be non-empty or nothing is ever attempted and the test is vacuous.
    _approver(db, f"{MARKER} Manager")
    db.commit()
    attempts: list[str] = []

    def boom(self, **kwargs):
        attempts.append(kwargs.get("type"))
        raise RuntimeError("notification backend is down")

    monkeypatch.setattr(NotificationService, "create_with_channel_preferences", boom)

    document = _quotation(client, project.id, product.id, ROUTE_BELOW_FLOOR)
    root = f"{BASE}/projects/{project.id}/quotation-documents/{document['id']}"

    sent_for_approval = client.post(
        f"{root}/approval-status",
        json={"to_status_id": _status_id(db, "pending_approval")},
    )
    assert sent_for_approval.status_code == 200, sent_for_approval.text
    assert sent_for_approval.json()["approval_status_key"] == "pending_approval"
    assert attempts, "the requested notification was never even attempted"

    approved = client.post(f"{root}/approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status_key"] == "approved"
    assert APPROVED in attempts

    # And a rejection on a second document.
    second = _quotation(client, project.id, product.id, ROUTE_BELOW_FLOOR)
    root2 = f"{BASE}/projects/{project.id}/quotation-documents/{second['id']}"
    client.post(
        f"{root2}/approval-status",
        json={"to_status_id": _status_id(db, "pending_approval")},
    )
    rejected = client.post(f"{root2}/reject", json={"reason": "Too low."})
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["approval_status_key"] == "rejected"
    assert REJECTED in attempts
