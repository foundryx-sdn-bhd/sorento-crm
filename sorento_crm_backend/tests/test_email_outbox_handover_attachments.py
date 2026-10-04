"""The OI handover email's attachments reaching the outbox and the drainer's MIME table
(#1312, PLAN-oi-line-attachments-27sep.md). UAC AC-E2/AC-E6.

Two hops pinned here, matching the plan's own wording:

* `app/tasks/notification_tasks.py::_enqueue_email_for_delivery` forwards
  `Notification.data["extra_attachments"]` into the outbox row's own
  `metadata_json["extra_attachments"]` - today the `single_email_to_all` branch (the OI
  handover email's own shape) builds its `metadata` dict with no such key at all, so this
  is a red until that forwarding is added.
* `app/tasks/email_outbox_tasks.py`'s `_ATTACHMENT_MIME` only knows `.pdf`/`.xlsx` today;
  this lane adds `.png/.jpg/.jpeg/.webp/.gif/.xls`, and `_attachments_for` gains an
  "optional" skip: an extra marked `optional` whose download raises must be skipped
  (logged) rather than aborting the whole send, unlike a non-optional one (AC-E6).

TEST-FIRST: `test_handover_outbox_row_carries_extra_attachments` needs a real DB row
(`blank_session`, own seeded chain) since `email_outbox_service.enqueue` writes one;
the drainer tests are pure/monkeypatched, mirroring `tests/test_email_outbox_attachment.py`.
"""
from __future__ import annotations

import uuid

from app.models.base import set_company_scope
from app.models.email_outbox import EmailOutbox
from app.models.notification import Notification, NotificationDelivery
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.tasks import email_outbox_tasks as tasks
from app.tasks import notification_tasks

from ._pg_fixture import blank_session


# --------------------------------------------------------------------------- #
# AC-E2 (hop 1) - notification -> outbox forwarding                           #
# --------------------------------------------------------------------------- #


def test_handover_outbox_row_carries_extra_attachments():
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))

        notification = Notification(
            id=str(uuid.uuid4()),
            user_id="system",
            # "automation_email" (`AutomationService._enqueue_email`'s own stamp) maps
            # to no entry in `notification_tasks._NOTIFICATION_TYPE_TO_EVENT_KEY`, so
            # `_resolve_event_key` falls back to "notification_delivery" - the real
            # outbox event key an OI handover email is enqueued under.
            type="automation_email",
            title="Order inquiry handover - SO423136",
            body="Handover body",
            data={
                "single_email_to_all": True,
                "recipient_emails": ["person7@example.com"],
                "extra_attachments": [
                    {
                        "filename": "SO423136-L3-a.png",
                        "storage_provider": "r2",
                        "storage_key": "k1",
                        "optional": True,
                    },
                ],
            },
        )
        db.add(notification)
        db.flush()
        delivery = NotificationDelivery(
            id=str(uuid.uuid4()),
            notification_id=notification.id,
            channel="email",
            status="pending",
        )
        db.add(delivery)
        db.flush()
        db.commit()

        notification_tasks._enqueue_email_for_delivery(
            db, notification, None, delivery, "notification_delivery"
        )

        row = (
            db.query(EmailOutbox)
            .filter(EmailOutbox.recipient_email == "person7@example.com")
            .one()
        )
        assert row.metadata_json.get("extra_attachments") == [
            {
                "filename": "SO423136-L3-a.png",
                "storage_provider": "r2",
                "storage_key": "k1",
                "optional": True,
            },
        ]


# --------------------------------------------------------------------------- #
# AC-E2 (hop 2) - the drainer's MIME table                                    #
# --------------------------------------------------------------------------- #


def test_drainer_image_mime(monkeypatch):
    class FakeBackend:
        def download_file(self, key):
            return b"bytes"

    import app.services.storage_router as router

    monkeypatch.setattr(router, "get_backend", lambda provider: FakeBackend())

    _name, png_mime, _data = tasks._fetch_attachment("r2", "k/a.png", "a.png")
    assert png_mime == "image/png"
    _name, jpg_mime, _data = tasks._fetch_attachment("r2", "k/b.jpg", "b.jpg")
    assert jpg_mime == "image/jpeg"
    _name, jpeg_mime, _data = tasks._fetch_attachment("r2", "k/c.jpeg", "c.jpeg")
    assert jpeg_mime == "image/jpeg"
    _name, webp_mime, _data = tasks._fetch_attachment("r2", "k/d.webp", "d.webp")
    assert webp_mime == "image/webp"
    _name, gif_mime, _data = tasks._fetch_attachment("r2", "k/e.gif", "e.gif")
    assert gif_mime == "image/gif"
    _name, xls_mime, _data = tasks._fetch_attachment("r2", "k/f.xls", "f.xls")
    assert xls_mime == "application/vnd.ms-excel"


# --------------------------------------------------------------------------- #
# AC-E6 - a missing OPTIONAL attachment is skipped, not a send failure         #
# --------------------------------------------------------------------------- #


def test_drainer_skips_missing_optional_attachment(monkeypatch):
    class BrokenBackend:
        def download_file(self, key):
            raise RuntimeError("object missing")

    class WorkingBackend:
        def download_file(self, key):
            return b"data"

    import app.services.storage_router as router

    def _get_backend(provider):
        return BrokenBackend() if provider == "broken" else WorkingBackend()

    monkeypatch.setattr(router, "get_backend", _get_backend)

    row = EmailOutbox(
        event_key="order_inquiry_handover",
        recipient_email="person7@example.com",
        subject="Order inquiry handover",
        body_text="Body",
        metadata_json={
            "extra_attachments": [
                {
                    "filename": "missing.png",
                    "storage_provider": "broken",
                    "storage_key": "gone.png",
                    "optional": True,
                },
                {
                    "filename": "present.pdf",
                    "storage_provider": "r2",
                    "storage_key": "here.pdf",
                },
            ],
        },
    )

    out = tasks._attachments_for(row)

    assert out is not None
    assert [item[0] for item in out] == ["present.pdf"], (
        "a missing OPTIONAL attachment must be skipped, and the rest of the email must "
        f"still send, got {out}"
    )
