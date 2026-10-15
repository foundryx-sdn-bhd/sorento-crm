"""OI-RESEND-PURCHASING: "Send to purchasing" rebuilds an order inquiry's open rows into the
`order_inquiry_handover` email and sends it again, single or bulk, with one preview first
(`documentation/plans/scm/PLAN-oi-resend-purchasing-15oct.md`).

TEST-FIRST: written before the routes exist, so a red here is a missing route, a missing
field or a wrong assertion, never a fixture bug.

Postgres only (`blank_session()` through the reserve suite's own `api` world). The handover
automation is the real seed chain (`test_email_layout_migrations._seed`). SMTP is stubbed:
the send writes `notifications` + `notification_deliveries` rows and nothing leaves.

Contract asserted here:

* `POST {LIST}/handover-resend/preview` `{"inquiry_ids": [...]}` ->
  `{emails: [{inquiry_id, inquiry_no, to, cc, subject, body_html, inquiry_count, line_count,
  last_sent}], inquiry_total, unresolved, skipped: [{inquiry_id, inquiry_no, reason}],
  automation_enabled}`; `last_sent` is `null` or `{at, kind: automatic|resent, by}`.
* `POST {LIST}/handover-resend/send` same body -> `{emails_sent, items_sent, skipped}`.
* Both need `projects.order_inquiry.action`.
"""
from __future__ import annotations

import json
from datetime import date

import pytest
import sqlalchemy as sa

from app.models.project_so import (
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    INQUIRY_PARTLY_LINKED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
)

from ._pg_fixture import blank_session  # noqa: F401 - the `api` world opens it
from .test_email_layout_migrations import _run, _seed
from .test_order_inquiry_reserve import _api, _as, _captured_dispatches, _pso
from .test_order_inquiry_worklist import LIST, _inquiry_for, _row, _uid, _user

MARKER = "zzt-oi-resend"
PREVIEW = f"{LIST}/handover-resend/preview"
SEND = f"{LIST}/handover-resend/send"
ACTION_PERMISSIONS = ["projects.projects.view", "projects.order_inquiry.action"]


@pytest.fixture()
def rs(monkeypatch):
    from app.services import notification_email

    monkeypatch.setattr(notification_email, "send_notification_email", lambda *a, **kw: None)
    monkeypatch.setattr(
        notification_email, "send_notification_email_multi", lambda *a, **kw: None
    )
    monkeypatch.setattr("app.services.queue_service.enqueue_job", lambda *a, **kw: None)
    yield from _api(ACTION_PERMISSIONS)


def _automation(world, *, enabled: bool = True):
    """The real handover automation, plus one purchasing buyer on its user list (the blank
    schema has no `purchasing%` role for the seed to pick up)."""
    db = world.db
    mods = _seed(db)
    _run(mods["eml_0002_seed_layouts"], db)
    buyer = _user(db, f"{MARKER} Purchasing Buyer")
    db.execute(
        sa.text(
            "UPDATE automations SET enabled = :e, recipient_config = recipient_config "
            "|| CAST(:c AS jsonb) WHERE trigger_type = 'order_inquiry_handover'"
        ),
        {"e": enabled, "c": json.dumps({"user_ids": [buyer]})},
    )
    db.commit()
    return buyer


def _oi(world, *, location=None):
    inquiry = _inquiry_for(world.db, world.company_id, _pso(world.db, world.company_id))
    inquiry.raised_by = world.requester
    world.db.commit()
    return inquiry


def _line(world, inquiry, *, state=INQUIRY_RAISED, qty="10", item=None, location="BRW-BB"):
    row = _row(
        world.db,
        world.company_id,
        inquiry,
        state=state,
        qty=qty,
        item_code=item or f"{MARKER}-{_uid()[:6]}".upper(),
        stock_location=location,
        delivery_date=date(2026, 10, 20),
    )
    world.db.commit()
    return row


def _joey(world) -> str:
    """The `api` world's requester (the presser here), by the name it was seeded with."""
    from app.models.user import User

    return world.db.get(User, world.requester).name


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def _notifications(world, inquiry_id):
    from app.models.notification import Notification

    world.db.expire_all()
    return (
        world.db.query(Notification)
        .filter(
            Notification.type == "automation_email",
            Notification.data["source_id"].astext == str(inquiry_id),
        )
        .order_by(Notification.created_at.asc())
        .all()
    )


# ------------------------------------------------------------------------- preview


def test_preview_single_rebuilds_open_rows_marked_resent(rs):
    client, world = rs
    buyer = _automation(world)
    inquiry = _oi(world)
    open_a = _line(world, inquiry, item="ZZT-OPEN-A")
    open_b = _line(world, inquiry, state=INQUIRY_PARTLY_LINKED, item="ZZT-OPEN-B")
    _line(world, inquiry, state=INQUIRY_CANCELLED, item="ZZT-GONE-C")
    _line(world, inquiry, state=INQUIRY_PLACED, item="ZZT-PLACED-D")
    _line(world, inquiry, state=INQUIRY_ACTIONED, item="ZZT-ACTED-E")

    body = _ok(client.post(PREVIEW, json={"inquiry_ids": [inquiry.id]}))

    assert body["automation_enabled"] is True
    assert body["skipped"] == []
    assert len(body["emails"]) == 1
    email = body["emails"][0]
    assert email["inquiry_id"] == inquiry.id
    assert email["inquiry_no"] == inquiry.inquiry_no
    assert email["line_count"] == 2
    assert email["inquiry_count"] == 1
    assert email["subject"].startswith("OI: BRW-BB @ ")
    assert email["subject"].endswith("(Resent)")
    assert open_a.item_code in email["body_html"] and open_b.item_code in email["body_html"]
    for gone in ("ZZT-GONE-C", "ZZT-PLACED-D", "ZZT-ACTED-E"):
        assert gone not in email["body_html"]
    assert "RESENT" in email["body_html"]
    assert email["to"]["name"] == f"{MARKER} Purchasing Buyer"
    # The presser (include_actor) rides on Cc, never displaces purchasing from To.
    assert [p["name"] for p in email["cc"]] == [_joey(world)]
    assert email["last_sent"] is None
    assert buyer


def test_preview_writes_nothing(rs):
    client, world = rs
    _automation(world)
    inquiry = _oi(world)
    _line(world, inquiry)
    _ok(client.post(PREVIEW, json={"inquiry_ids": [inquiry.id]}))
    assert _notifications(world, inquiry.id) == []


# ------------------------------------------------------------------------- send


def test_send_single_records_notification_and_reads_back_as_last_sent(rs, monkeypatch):
    client, world = rs
    calls = _captured_dispatches(monkeypatch)
    buyer = _automation(world)
    inquiry = _oi(world)
    _line(world, inquiry, item="ZZT-SEND-A")

    result = _ok(client.post(SEND, json={"inquiry_ids": [inquiry.id]}))
    assert result["emails_sent"] == 1
    assert result["items_sent"] == 1
    assert result["skipped"] == []
    # The resend never goes through the enabled-only event fan-out.
    assert calls == []

    notes = _notifications(world, inquiry.id)
    assert len(notes) == 1
    data = notes[0].data
    assert data["trigger_type"] == "order_inquiry_handover"
    assert data["source_kind"] == "order_inquiry_handover_resend"
    assert data["sent_by"] == _joey(world)
    assert data["recipient_emails"] == [f"{buyer}@zzt.test", f"{world.requester}@zzt.test"]
    assert notes[0].title.endswith("(Resent)")
    assert "ZZT-SEND-A" in data["body_html"]

    from app.models.notification import NotificationDelivery

    deliveries = (
        world.db.query(NotificationDelivery)
        .filter(NotificationDelivery.notification_id == notes[0].id)
        .all()
    )
    assert len(deliveries) == 1 and deliveries[0].channel == "email"

    again = _ok(client.post(PREVIEW, json={"inquiry_ids": [inquiry.id]}))
    last = again["emails"][0]["last_sent"]
    assert last["kind"] == "resent"
    assert last["by"] == _joey(world)
    assert last["at"]


def test_last_sent_reads_the_automatic_handover(rs):
    client, world = rs
    _automation(world)
    inquiry = _oi(world)
    _line(world, inquiry)

    from app.services.automation_service import AutomationService

    AutomationService(world.db).dispatch_event(
        "order_inquiry_handover",
        context={
            "handover": {"subject_scope": "X", "headline": "ORDER", "orders": [], "lines": []},
            "actor": None,
            "today": "15/10/2026",
        },
        source_kind="order_inquiry_handover",
        source_id=inquiry.id,
    )
    world.db.commit()

    last = _ok(client.post(PREVIEW, json={"inquiry_ids": [inquiry.id]}))["emails"][0][
        "last_sent"
    ]
    assert last["kind"] == "automatic"
    assert last["by"] is None
    assert last["at"]


def test_automation_disabled_still_previews_and_sends(rs):
    client, world = rs
    _automation(world, enabled=False)
    inquiry = _oi(world)
    _line(world, inquiry)

    body = _ok(client.post(PREVIEW, json={"inquiry_ids": [inquiry.id]}))
    assert body["automation_enabled"] is False
    assert len(body["emails"]) == 1

    result = _ok(client.post(SEND, json={"inquiry_ids": [inquiry.id]}))
    assert result["emails_sent"] == 1
    assert len(_notifications(world, inquiry.id)) == 1


# ------------------------------------------------------------------------- bulk


def test_bulk_one_email_per_oi_and_skips_nothing_open(rs):
    client, world = rs
    _automation(world)
    first, second, closed = _oi(world), _oi(world), _oi(world)
    _line(world, first, location="BRW-BB")
    _line(world, second, location="KL-HQ")
    _line(world, second, location="KL-HQ")
    _line(world, closed, state=INQUIRY_CANCELLED)

    ids = [first.id, second.id, closed.id]
    body = _ok(client.post(PREVIEW, json={"inquiry_ids": ids}))
    assert [e["inquiry_id"] for e in body["emails"]] == [first.id, second.id]
    assert [e["line_count"] for e in body["emails"]] == [1, 2]
    assert body["emails"][1]["subject"].startswith("OI: KL-HQ @ ")
    assert body["inquiry_total"] == 2
    assert body["skipped"] == [
        {"inquiry_id": closed.id, "inquiry_no": closed.inquiry_no, "reason": "nothing_open"}
    ]

    result = _ok(client.post(SEND, json={"inquiry_ids": ids}))
    assert result["emails_sent"] == 2
    assert result["items_sent"] == 2
    assert [s["inquiry_id"] for s in result["skipped"]] == [closed.id]
    assert len(_notifications(world, first.id)) == 1
    assert len(_notifications(world, second.id)) == 1
    assert _notifications(world, closed.id) == []


def test_no_automation_means_no_recipient_and_no_send(rs):
    client, world = rs
    # No seed at all: nothing names purchasing.
    world.db.execute(sa.text("DELETE FROM automations WHERE trigger_type = 'order_inquiry_handover'"))
    world.db.commit()
    inquiry = _oi(world)
    _line(world, inquiry)

    body = _ok(client.post(PREVIEW, json={"inquiry_ids": [inquiry.id]}))
    assert body["emails"] == []
    assert body["skipped"] == [
        {"inquiry_id": inquiry.id, "inquiry_no": inquiry.inquiry_no, "reason": "no_recipient"}
    ]
    result = _ok(client.post(SEND, json={"inquiry_ids": [inquiry.id]}))
    assert result["emails_sent"] == 0
    assert _notifications(world, inquiry.id) == []


# ------------------------------------------------------------------------- guards


def test_view_only_user_is_refused(rs):
    _client, world = rs
    _automation(world)
    inquiry = _oi(world)
    _line(world, inquiry)
    viewer = _user(world.db, f"{MARKER} Viewer")
    world.db.commit()
    with _as(world.db, viewer, ["projects.projects.view"]) as client:
        assert client.post(PREVIEW, json={"inquiry_ids": [inquiry.id]}).status_code == 403
        assert client.post(SEND, json={"inquiry_ids": [inquiry.id]}).status_code == 403
    assert _notifications(world, inquiry.id) == []


def test_unknown_inquiry_is_404_and_bad_bodies_422(rs):
    client, world = rs
    _automation(world)
    assert client.post(PREVIEW, json={"inquiry_ids": [_uid()]}).status_code == 404
    assert client.post(SEND, json={"inquiry_ids": [_uid()]}).status_code == 404
    assert client.post(PREVIEW, json={"inquiry_ids": []}).status_code == 422
    assert client.post(PREVIEW, json={"inquiry_ids": ["not-a-uuid"]}).status_code == 422
    too_many = [_uid() for _ in range(51)]
    assert client.post(SEND, json={"inquiry_ids": too_many}).status_code == 422
