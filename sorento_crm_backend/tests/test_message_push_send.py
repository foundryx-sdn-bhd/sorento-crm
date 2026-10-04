"""The send path: one bell row and one push per inbound message, however many lanes.

UAC: documentation/plans/notifications/message-push-acceptance-criteria.md
     AC-M14 / AC-M14a / AC-M15 (what the notification carries)
     AC-M16  (in_app + web_push, and NO email delivery row)
     AC-M16a (the dual-lane ingest produces ONE bell row and ONE push)
     AC-M16b (a message with no message_id has nothing to dedupe on)
     AC-M17  (a recipient with no subscription: nothing sent, nothing raised)
     AC-M18  (404/410 prunes the dead subscription - existing behaviour, pinned)
     AC-M19  (VAPID absent: logged, not raised)
     AC-M20  (enqueued on the `notifications` queue AFTER commit; Redis down is a 201)

AC-M16a is the one that matters. The ingest endpoint is reached TWICE for the same
WhatsApp message - its own AC-J5 dual-lane race, which is why the insert is an upsert
on `message_id` - so the notification has to dedupe on the same key or every message
double-rings.

Run:
    venv/bin/pytest tests/test_message_push_send.py -q
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: F401  (import first: app.dependencies alone is circular)
from app.dependencies import get_current_user_or_api_key, get_db, get_external_api_user
from app.models.access import RespondContact
from app.models.chat_history import ChatHistory
from app.models.notification import (
    Notification,
    NotificationDelivery,
    PushSubscription,
)
from app.models.sla import ConversationSLATracking, SLAPolicy, SLAPolicyTier
from app.models.user import User
from tests._external_auth import external_permissions_granted
from tests._pg_fixture import TEST_PREFIX, blank_session

CONVERSATIONS_LINK = "/sla-management/conversations"
RESPOND_IO_ID = f"{TEST_PREFIX}-rio-{uuid.uuid4().hex[:8]}"
PHONE = "+60160000509"


@pytest.fixture
def db():
    with blank_session() as session:
        yield session


@pytest.fixture
def enqueued(monkeypatch):
    """Capture what the route hands to RQ instead of reaching a real Redis."""
    calls: list[tuple] = []
    import app.services.queue_service as queue_service

    monkeypatch.setattr(
        queue_service,
        "enqueue_job",
        lambda func, *args, **kwargs: calls.append((func, args, kwargs)),
    )
    return calls


@pytest.fixture
def client(db, enqueued):
    def _principal():
        return {"id": "system"}

    def _db():
        yield db

    app.dependency_overrides[get_external_api_user] = _principal
    app.dependency_overrides[get_current_user_or_api_key] = _principal
    app.dependency_overrides[get_db] = _db
    with external_permissions_granted():
        yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def task_session(db, monkeypatch):
    """Run the RQ task against the scratch schema instead of a real SessionLocal."""
    import app.tasks.message_push_tasks as tasks

    class _Keeper:
        def __call__(self):
            return db

    monkeypatch.setattr(tasks, "SessionLocal", _Keeper())
    monkeypatch.setattr(db, "close", lambda: None)
    return tasks


# --------------------------------------------------------------------------- #
# Seeding                                                                      #
# --------------------------------------------------------------------------- #


def _marker(stem: str) -> str:
    return f"{TEST_PREFIX}-{stem}-{uuid.uuid4().hex[:8]}"


def _contact(db) -> RespondContact:
    contact = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=PHONE,
        name=f"{TEST_PREFIX} Ah Meng",
        respond_io_id=RESPOND_IO_ID,
        session_vars={},
    )
    db.add(contact)
    db.commit()
    return contact


def _user(db, scope: str = "assigned_only") -> str:
    uid = str(uuid.uuid4())
    db.add(
        User(
            id=uid,
            email=f"{TEST_PREFIX.lower()}-{uid[:8]}@test.invalid",
            name=f"{TEST_PREFIX} Owner",
            status="ACTIVE",
            notify_push_message_scope=scope,
        )
    )
    db.commit()
    return uid


def _tracking(db, contact: RespondContact, assignee: str) -> str:
    policy_id = str(uuid.uuid4())
    db.add(SLAPolicy(id=policy_id, code=_marker("POL"), name=f"{TEST_PREFIX} policy"))
    db.add(
        SLAPolicyTier(
            id=str(uuid.uuid4()),
            policy_id=policy_id,
            tier_level=1,
            tier_name="Tier 1",
            response_hours=4,
            resolution_hours=24,
        )
    )
    now = datetime.utcnow()
    tracking_id = str(uuid.uuid4())
    db.add(
        ConversationSLATracking(
            id=tracking_id,
            policy_id=policy_id,
            current_tier=1,
            initiated_at=now - timedelta(hours=1),
            current_tier_started_at=now - timedelta(hours=1),
            due_at=now + timedelta(hours=4),
            is_responded=False,
            is_resolved=False,
            respond_contact_id=contact.id,
            assigned_to_id=assignee,
        )
    )
    db.commit()
    return tracking_id


def _message_row(db, *, message_id: str | None, type_: str = "incoming") -> ChatHistory:
    row = ChatHistory(
        channel="whatsapp",
        contact_id=RESPOND_IO_ID,
        phone_number=PHONE,
        message="Can I get the price for the 900mm hood?",
        sent_at=datetime.utcnow(),
        type=type_,
        message_id=message_id,
    )
    db.add(row)
    db.commit()
    return row


def _notifications(db, user_id: str) -> list[Notification]:
    return (
        db.query(Notification)
        .filter(Notification.user_id == user_id)
        .order_by(Notification.created_at.asc())
        .all()
    )


def _channels(db, notification_id: str) -> set[str]:
    return {
        d.channel
        for d in db.query(NotificationDelivery)
        .filter(NotificationDelivery.notification_id == notification_id)
        .all()
    }


def _ingest_payload(**overrides) -> dict:
    payload = {
        "channel": "whatsapp",
        "contact_id": RESPOND_IO_ID,
        "phone_number": PHONE,
        "message": "Can I get the price for the 900mm hood?",
        "sent_at": int(datetime.utcnow().timestamp() * 1000),
        "first_name": "Ah",
        "last_name": "Meng",
        "type": "incoming",
        "message_id": _marker("msg"),
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------------- #
# AC-M14 / AC-M14a / AC-M15 / AC-M16 - one row, two surfaces, no email         #
# --------------------------------------------------------------------------- #


def test_the_bell_row_says_what_the_phone_says(db, task_session, enqueued):
    contact = _contact(db)
    owner = _user(db)
    tracking_id = _tracking(db, contact, owner)
    row = _message_row(db, message_id=_marker("msg"))

    task_session.send_message_push(row.id)

    (notification,) = _notifications(db, owner)
    assert notification.title == contact.name
    assert notification.body == "Can I get the price for the 900mm hood?"
    assert notification.data["link"] == f"{CONVERSATIONS_LINK}?contact={contact.respond_io_id}"
    assert notification.data["tag"] == f"contact-{RESPOND_IO_ID}"
    assert notification.data["contact_id"] == RESPOND_IO_ID


def test_channels_are_in_app_and_web_push_and_never_email(db, task_session, enqueued):
    contact = _contact(db)
    owner = _user(db)
    _tracking(db, contact, owner)
    row = _message_row(db, message_id=_marker("msg"))

    task_session.send_message_push(row.id)

    (notification,) = _notifications(db, owner)
    assert _channels(db, notification.id) == {"in_app", "web_push"}


def test_each_recipient_gets_their_own_link(db, task_session, enqueued):
    contact = _contact(db)
    first = _user(db)
    second = _user(db)
    first_ticket = _tracking(db, contact, first)
    second_ticket = _tracking(db, contact, second)
    row = _message_row(db, message_id=_marker("msg"))

    task_session.send_message_push(row.id)

    assert _notifications(db, first)[0].data["link"] == f"{CONVERSATIONS_LINK}?contact={contact.respond_io_id}"
    assert (
        _notifications(db, second)[0].data["link"] == f"{CONVERSATIONS_LINK}?contact={contact.respond_io_id}"
    )


def test_an_outgoing_message_writes_no_notification(db, task_session, enqueued):
    contact = _contact(db)
    owner = _user(db, "all_contacts")
    _tracking(db, contact, owner)
    row = _message_row(db, message_id=_marker("msg"), type_="outgoing")

    task_session.send_message_push(row.id)

    assert _notifications(db, owner) == []


def test_a_missing_row_is_a_no_op(db, task_session, enqueued):
    owner = _user(db, "all_contacts")

    task_session.send_message_push(987654321)

    assert _notifications(db, owner) == []


# --------------------------------------------------------------------------- #
# AC-M16a / AC-M16b - idempotency on the Respond message_id                    #
# --------------------------------------------------------------------------- #


def test_the_same_message_twice_rings_once(db, task_session, enqueued):
    contact = _contact(db)
    owner = _user(db)
    _tracking(db, contact, owner)
    row = _message_row(db, message_id=_marker("msg"))

    task_session.send_message_push(row.id)
    task_session.send_message_push(row.id)

    (notification,) = _notifications(db, owner)
    assert (
        db.query(NotificationDelivery)
        .filter(
            NotificationDelivery.notification_id == notification.id,
            NotificationDelivery.channel == "web_push",
        )
        .count()
        == 1
    )


def test_the_dedup_key_is_the_respond_message_id(db, task_session, enqueued):
    contact = _contact(db)
    owner = _user(db)
    _tracking(db, contact, owner)
    message_id = _marker("msg")
    row = _message_row(db, message_id=message_id)

    task_session.send_message_push(row.id)

    (notification,) = _notifications(db, owner)
    assert notification.dedup_key == message_id
    assert notification.source_entity_type == "chat_message"
    assert notification.event_type == "message_received"


def test_a_message_with_no_message_id_notifies_once_per_ingest(
    db, task_session, enqueued
):
    contact = _contact(db)
    owner = _user(db)
    _tracking(db, contact, owner)
    first = _message_row(db, message_id=None)
    second = _message_row(db, message_id=None)

    task_session.send_message_push(first.id)
    task_session.send_message_push(second.id)

    assert len(_notifications(db, owner)) == 2


# --------------------------------------------------------------------------- #
# AC-M17 / AC-M18 / AC-M19 - the existing web-push sender, pinned              #
# --------------------------------------------------------------------------- #


def _delivery_for(db, notification_id: str) -> NotificationDelivery:
    return (
        db.query(NotificationDelivery)
        .filter(
            NotificationDelivery.notification_id == notification_id,
            NotificationDelivery.channel == "web_push",
        )
        .one()
    )


def _one_notification(db, task_session, enqueued) -> tuple[str, Notification]:
    contact = _contact(db)
    owner = _user(db)
    _tracking(db, contact, owner)
    row = _message_row(db, message_id=_marker("msg"))
    task_session.send_message_push(row.id)
    return owner, _notifications(db, owner)[0]


def test_a_recipient_with_no_subscription_sends_nothing_and_raises_nothing(
    db, task_session, enqueued, monkeypatch
):
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-key")
    from app.tasks.notification_tasks import _send_web_push_for_notification

    owner, notification = _one_notification(db, task_session, enqueued)

    _send_web_push_for_notification(db, notification, owner, _delivery_for(db, notification.id))

    assert _delivery_for(db, notification.id).status == "sent"


def test_a_gone_endpoint_is_pruned(db, task_session, enqueued, monkeypatch):
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-key")
    from app.tasks.notification_tasks import _send_web_push_for_notification

    owner, notification = _one_notification(db, task_session, enqueued)
    db.add(
        PushSubscription(
            user_id=owner,
            endpoint=f"https://push.test/{uuid.uuid4().hex}",
            p256dh="p",
            auth="a",
        )
    )
    db.commit()

    class _Gone(Exception):
        response = type("R", (), {"status_code": 410})()

    def _boom(**kwargs):
        raise _Gone()

    fake = type("M", (), {"webpush": staticmethod(_boom), "WebPushException": _Gone})
    monkeypatch.setattr(
        "importlib.import_module", lambda name: fake if name == "pywebpush" else None
    )

    _send_web_push_for_notification(db, notification, owner, _delivery_for(db, notification.id))

    assert (
        db.query(PushSubscription).filter(PushSubscription.user_id == owner).count() == 0
    )


def test_no_vapid_is_logged_not_raised(db, task_session, enqueued, monkeypatch):
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    from app.tasks.notification_tasks import _send_web_push_for_notification

    owner, notification = _one_notification(db, task_session, enqueued)

    _send_web_push_for_notification(db, notification, owner, _delivery_for(db, notification.id))

    delivery = _delivery_for(db, notification.id)
    assert delivery.status == "failed"
    assert delivery.error_message == "VAPID not configured"


# --------------------------------------------------------------------------- #
# AC-M20 - enqueued after commit, on the notifications queue, best-effort      #
# --------------------------------------------------------------------------- #


def _push_jobs(enqueued) -> list[tuple]:
    from app.tasks.message_push_tasks import send_message_push

    return [call for call in enqueued if call[0] is send_message_push]


def test_the_ingest_enqueues_the_push_on_the_notifications_queue(client, db, enqueued):
    _contact(db)

    response = client.post("/api/v1/external/chat-history/messages", json=_ingest_payload())

    assert response.status_code == 201
    (job,) = _push_jobs(enqueued)
    assert job[1] == (response.json()["id"],)
    assert job[2]["queue_name"] == "notifications"


def test_the_second_lane_of_the_same_message_enqueues_nothing_extra(
    client, db, enqueued
):
    _contact(db)
    payload = _ingest_payload()

    first = client.post("/api/v1/external/chat-history/messages", json=payload)
    second = client.post("/api/v1/external/chat-history/messages", json=payload)

    assert first.json()["status"] == "created"
    assert second.json()["status"] == "duplicate"
    assert second.json()["id"] == first.json()["id"]
    assert len(_push_jobs(enqueued)) == 1


def test_an_outgoing_ingest_enqueues_nothing(client, db, enqueued):
    _contact(db)

    client.post(
        "/api/v1/external/chat-history/messages",
        json=_ingest_payload(type="outgoing"),
    )

    assert _push_jobs(enqueued) == []


def test_a_dead_redis_still_returns_201(client, db, monkeypatch):
    _contact(db)
    import app.services.queue_service as queue_service

    def _down(*args, **kwargs):
        raise ConnectionError("Redis is unreachable")

    monkeypatch.setattr(queue_service, "enqueue_job", _down)

    response = client.post(
        "/api/v1/external/chat-history/messages", json=_ingest_payload()
    )

    assert response.status_code == 201
    assert db.query(ChatHistory).filter(ChatHistory.contact_id == RESPOND_IO_ID).count() == 1
