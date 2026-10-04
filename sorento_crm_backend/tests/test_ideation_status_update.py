"""#1355 - pull the shared service's idea status-event feed, send the approved
``ideation_status_update`` template to the requester.

Keys back to
``documentation/plans/ideation/ideation-status-update-29sep-acceptance-criteria.md``
(AC-IS001 onward). Postgres only (``tests/_pg_fixture.py``). Two seams are stubbed,
nothing else: the feed (a fake feed over a list of events that honours ``after``, or
``httpx`` behind a ``MockTransport`` for the request shape) and the Respond.io HTTP
client (``RespondClient``), so the template mapping path - ``respond_template_defaults``
to ``send_template_for_use_case`` - is the production one over real rows.

The 24h window is held CLOSED by default (the ``db`` fixture patches
``messaging.get_window_state``): the send is window-aware (``send_text_or_template``),
so a closed window is what keeps every template-asserting test on the template branch.
Window-open and window-unknown cases opt in through ``_window`` (AC-UW001 onward).
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import app.services.ideation_status_update_service as svc
import app.services.integration_service as integration_service
import app.services.respond_messaging_service as messaging
from app.models.access import RespondContact
from app.models.integration import IntegrationLog
from app.models.respond_template import (
    TEMPLATE_DEFAULT_USE_CASES,
    RespondChannel,
    RespondMessageTemplate,
    RespondTemplateDefault,
)
from app.models.respond_workspace import RespondWorkspace
from app.services.ideation_turn_service import _IdeationConfig
from tests._pg_fixture import blank_session, unique_code

# Captured before any fixture patches it: the real shared window function (AC-UW003).
_REAL_GET_WINDOW_STATE = messaging.get_window_state

BASE_URL = "https://shared.test/be"
API_KEY = "ws-key"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


class FakeRespondClient:
    """Stands in for RespondClient. Records every send: template sends as
    ``kind == "template"``, free-text session sends as ``kind == "text"``."""

    sent: list[dict[str, Any]] = []
    fail_with: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    def send_template_message(self, identifier, **kwargs):
        if FakeRespondClient.fail_with is not None:
            raise FakeRespondClient.fail_with
        FakeRespondClient.sent.append({"identifier": identifier, "kind": "template", **kwargs})
        return {"messageId": 991}

    def send_message(self, identifier, text, *args, **kwargs):
        if FakeRespondClient.fail_with is not None:
            raise FakeRespondClient.fail_with
        FakeRespondClient.sent.append({"identifier": identifier, "text": text, "kind": "text"})
        return {"messageId": 992}


def _window(monkeypatch, open: bool) -> None:
    """Re-patch the window the send reads (open or closed)."""
    monkeypatch.setattr(
        messaging,
        "get_window_state",
        lambda *a, **k: {
            "open": open,
            "last_incoming_at": None,
            "checked_at": "",
            "source": "respond_api" if open else "none",
        },
    )


@pytest.fixture
def db(monkeypatch) -> Session:
    """The 24h window is held CLOSED by default so the template path runs and the
    template-asserting tests stay valid; a test opts into an open window with
    ``_window(monkeypatch, True)``. RespondClient is always the fake."""
    FakeRespondClient.sent = []
    FakeRespondClient.fail_with = None
    monkeypatch.setattr(integration_service, "RespondClient", FakeRespondClient)
    monkeypatch.setattr(
        messaging,
        "get_window_state",
        lambda *a, **k: {"open": False, "last_incoming_at": None, "checked_at": "", "source": "none"},
    )
    monkeypatch.setattr(
        svc,
        "_resolve_ideation_config",
        lambda _db: _IdeationConfig(base_url=BASE_URL, api_key=API_KEY, product_id=None),
    )
    with blank_session() as session:
        yield session


def _map_template(db: Session, *, params: int = 3, status: str = "approved") -> RespondMessageTemplate:
    ws = RespondWorkspace(
        id=str(uuid.uuid4()),
        space_id=unique_code("space"),
        name="ZZT workspace",
        api_key_ciphertext="not-encrypted-test",
        is_active=True,
        is_default=True,
    )
    db.add(ws)
    db.flush()
    ch = RespondChannel(id=str(uuid.uuid4()), workspace_id=ws.id, respond_channel_id=453209)
    db.add(ch)
    db.flush()
    body = "Update on your idea {{1}}: it is now {{2}}. Track it here: {{3}}"
    tpl = RespondMessageTemplate(
        id=str(uuid.uuid4()),
        channel_id=ch.id,
        respond_template_id=77,
        name=unique_code("idea-status"),
        language_code="en",
        category="UTILITY",
        status=status,
        components=[{"type": "body", "text": body}],
        body_text=body,
        param_count=params,
    )
    db.add(tpl)
    db.flush()
    db.add(
        RespondTemplateDefault(
            use_case="ideation_status_update",
            template_id=tpl.id,
            template_name_snapshot=tpl.name,
            param_mapping={"1": "idea_number", "2": "status_label", "3": "track_url"},
        )
    )
    db.commit()
    return tpl


def _contact(db: Session, *, phone: str | None = None, outbound: bool = True,
             respond_io_id: str | None = "auto", name: str | None = None,
             first_name: str | None = None, last_name: str | None = None) -> RespondContact:
    phone = phone or f"+601{uuid.uuid4().int % 10**8:08d}"
    c = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=phone,
        respond_io_id=(str(uuid.uuid4().int % 10**9) if respond_io_id == "auto" else respond_io_id),
        outbound_enabled=outbound,
        session_vars={},
        name=name,
        first_name=first_name,
        last_name=last_name,
    )
    db.add(c)
    db.commit()
    return c


def _event(seq: int, *, phone: str | None, kind: str = "status_changed", **over) -> dict:
    ev = {
        "event_id": str(uuid.uuid4()),
        "seq": seq,
        "kind": kind,
        "occurred_at": "2026-09-28T09:10:00Z",
        "idea_id": str(uuid.uuid4()),
        "idea_number": f"IDEA-{seq:04d}",
        "idea_title": "Faster quotation",
        "product_id": "prod-1",
        "status_label": "Discussed",
        "from_status_label": "New",
        "track_url": f"https://ss.test/public/ideas/tok{seq}",
        "requester_phone": phone,
        "merged_into": None,
        "separated_from": None,
        "is_test": False,
    }
    ev.update(over)
    return ev


class FakeFeed:
    """At-least-once feed over a fixed list, honouring ``after`` and ``limit``."""

    def __init__(self, events: list[dict], *, shuffle: bool = False, fail: Exception | None = None):
        self.events = events
        self.shuffle = shuffle
        self.fail = fail
        self.calls: list[dict] = []

    def __call__(self, base_url, api_key, *, after, limit):
        self.calls.append({"base_url": base_url, "api_key": api_key, "after": after, "limit": limit})
        if self.fail is not None:
            raise self.fail
        page = [e for e in self.events if e["seq"] > after][:limit]
        if self.shuffle:
            page = list(reversed(page))
        return {"events": page, "next_after": page[-1]["seq"] if page else after}


def _rows(db: Session, event_id: str | None = None) -> list[IntegrationLog]:
    q = db.query(IntegrationLog).filter(IntegrationLog.business_table == "ideation_status_events")
    if event_id is not None:
        q = q.filter(IntegrationLog.business_id == event_id)
    return q.all()


def _payload(row: IntegrationLog) -> dict:
    return json.loads(row.request_payload or "{}")


# ---------------------------------------------------------------------------
# Template use case
# ---------------------------------------------------------------------------


def test_ac_is001_use_case_registered_and_listed(db):
    from app.services import respond_template_service as rts

    assert "ideation_status_update" in TEMPLATE_DEFAULT_USE_CASES
    rows = rts.get_defaults(db)
    assert any(r["use_case"] == "ideation_status_update" for r in rows)


def test_ac_is001_set_default_accepts_the_ideation_variables(db):
    """The mapping screen saves through ``set_default``, which refuses a variable missing
    from ``PARAM_VARIABLES``; the other tests insert the row directly and never hit it."""
    from app.services import respond_template_service as rts

    tpl = _map_template(db)
    out = rts.set_default(
        db,
        "ideation_status_update",
        template_id=tpl.id,
        param_mapping={"1": "idea_number", "2": "status_label", "3": "track_url"},
    )
    assert out["is_valid"] is True
    assert out["param_mapping"] == {"1": "idea_number", "2": "status_label", "3": "track_url"}


# ---------------------------------------------------------------------------
# Kinds (AC-IS020 to AC-IS024), send path (AC-IS050), log row (AC-IS060)
# ---------------------------------------------------------------------------


def test_ac_is020_status_changed_sends_the_template_and_logs_success(db):
    tpl = _map_template(db)
    c = _contact(db)
    ev = _event(5, phone=c.phone_number)

    out = svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert out["sent"] == 1
    assert len(FakeRespondClient.sent) == 1
    send = FakeRespondClient.sent[0]
    assert send["identifier"] == c.respond_io_id
    assert send["template_name"] == tpl.name
    assert send["parameters"] == ["IDEA-0005", "Discussed", "https://ss.test/public/ideas/tok5"]

    (row,) = _rows(db, ev["event_id"])
    assert row.integration_channel == "respond_io"
    assert row.external_reference == "IDEA-0005"
    assert row.endpoint == "ideation_status_update"
    assert row.direction == "outbound"
    assert row.status == "success"
    p = _payload(row)
    assert p["event"]["event_id"] == ev["event_id"]
    assert p["event"]["kind"] == "status_changed"
    assert p["event"]["idea_number"] == "IDEA-0005"
    assert p["event"]["template"] == tpl.name
    assert p["message"]["type"] == "whatsapp_template"
    assert p["message"]["template_name"] == tpl.name
    assert p["message"]["parameters"] == ["IDEA-0005", "Discussed", "https://ss.test/public/ideas/tok5"]


def test_ac_is021_merged_names_the_survivor(db):
    _map_template(db)
    c = _contact(db)
    ev = _event(
        6, phone=c.phone_number, kind="merged",
        merged_into={"idea_number": "IDEA-0012", "title": "Quote speed"},
    )

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert FakeRespondClient.sent[0]["parameters"][1] == "combined with IDEA-0012"
    assert _rows(db, ev["event_id"])[0].status == "success"


def test_ac_is022_unmerged_says_separated_again_and_the_new_status(db):
    _map_template(db)
    c = _contact(db)
    ev = _event(
        7, phone=c.phone_number, kind="unmerged", status_label="New",
        separated_from={"idea_number": "IDEA-0012", "title": "Quote speed"},
    )

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert (
        FakeRespondClient.sent[0]["parameters"][1]
        == "handled separately again from IDEA-0012, now New"
    )


def test_ac_is023_idea_number_falls_back_to_title(db):
    _map_template(db)
    c = _contact(db)
    ev = _event(8, phone=c.phone_number, idea_number=None, idea_title="Faster quotation")

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert FakeRespondClient.sent[0]["parameters"][0] == "Faster quotation"


def test_ac_is024_unknown_kind_skips_logs_and_advances(db):
    _map_template(db)
    c = _contact(db)
    ev = _event(9, phone=c.phone_number, kind="archived")

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert FakeRespondClient.sent == []
    (row,) = _rows(db, ev["event_id"])
    assert (row.status, row.error_code) == ("skipped", "UNKNOWN_KIND")
    assert svc.get_cursor(db, BASE_URL) == 9


URL10 = "https://ss.test/public/ideas/tok10"


def test_ac_uw001_open_window_sends_the_session_text(db, monkeypatch):
    _map_template(db)
    _window(monkeypatch, True)
    c = _contact(db, name="Ali")
    ev = _event(10, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert len(FakeRespondClient.sent) == 1
    send = FakeRespondClient.sent[0]
    assert send["kind"] == "text"
    assert send["identifier"] == c.respond_io_id
    assert send["text"] == f"Hi Ali, update on your idea IDEA-0010: it is now Discussed. Track it here: {URL10}"
    (row,) = _rows(db, ev["event_id"])
    assert row.status == "success"
    p = _payload(row)
    assert p["message"]["type"] == "text"
    assert p["event"]["sent_as"] == "text"
    assert p["event"]["window"]["open"] is True


def test_ac_uw002_closed_window_sends_the_template(db):
    tpl = _map_template(db)
    c = _contact(db, name="Ali")
    ev = _event(10, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert len(FakeRespondClient.sent) == 1
    send = FakeRespondClient.sent[0]
    assert send["kind"] == "template"
    assert send["template_name"] == tpl.name
    assert send["parameters"][0] == "IDEA-0010"
    (row,) = _rows(db, ev["event_id"])
    p = _payload(row)
    assert p["message"]["type"] == "whatsapp_template"
    assert p["event"]["sent_as"] == "template"
    assert p["event"]["window"]["open"] is False


def test_ac_uw003_unknown_window_sends_the_template(db, monkeypatch):
    _map_template(db)
    monkeypatch.setattr(messaging, "get_window_state", _REAL_GET_WINDOW_STATE)
    monkeypatch.setattr(messaging, "_resolve_last_incoming", lambda *a, **k: (None, "none"))
    messaging.reset_window_cache()
    c = _contact(db, name="Ali")
    ev = _event(10, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert [s["kind"] for s in FakeRespondClient.sent] == ["template"]
    (row,) = _rows(db, ev["event_id"])
    p = _payload(row)
    assert p["event"]["window"]["source"] == "none"
    assert p["event"]["window"]["open"] is False
    messaging.reset_window_cache()


def test_ac_uw004_no_contact_name_means_no_greeting(db, monkeypatch):
    _map_template(db)
    _window(monkeypatch, True)
    c = _contact(db)
    ev = _event(10, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    text = FakeRespondClient.sent[0]["text"]
    assert text == f"Update on your idea IDEA-0010: it is now Discussed. Track it here: {URL10}"
    assert "Hi -" not in text
    assert not text.startswith("Hi")


def test_ac_uw004_first_and_last_name_greet(db, monkeypatch):
    _map_template(db)
    _window(monkeypatch, True)
    c = _contact(db, first_name="Siti", last_name="Rahman")
    ev = _event(10, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert FakeRespondClient.sent[0]["text"].startswith("Hi Siti Rahman, ")


def test_ac_uw010_phone_stored_as_name_gets_no_greeting(db, monkeypatch):
    """A Respond.io contact created from a bare number can carry the number as its name."""
    _map_template(db)
    _window(monkeypatch, True)
    phone = f"+601{uuid.uuid4().int % 10**8:08d}"
    c = _contact(db, phone=phone, name=phone)
    ev = _event(10, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    text = FakeRespondClient.sent[0]["text"]
    assert not text.startswith("Hi")
    assert phone not in text


def test_ac_uw010_session_text_row_names_no_template(db, monkeypatch):
    tpl = _map_template(db)
    _window(monkeypatch, True)
    c = _contact(db, name="Ali")
    ev = _event(10, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    (row,) = _rows(db, ev["event_id"])
    p = _payload(row)
    assert p["event"]["sent_as"] == "text"
    assert p["event"]["template"] is None
    assert tpl.name not in row.request_payload


def test_ac_uw004_contact_name_reaches_the_template(db):
    tpl = _map_template(db, params=1)
    db.query(RespondTemplateDefault).filter_by(use_case="ideation_status_update").update(
        {"param_mapping": {"1": "contact_name"}}
    )
    db.commit()
    c = _contact(db, name="Ali")
    ev = _event(10, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert FakeRespondClient.sent[0]["template_name"] == tpl.name
    assert FakeRespondClient.sent[0]["parameters"] == ["Ali"]


def test_ac_uw006_open_window_without_a_template_still_sends_text(db, monkeypatch):
    _window(monkeypatch, True)
    c = _contact(db, name="Ali")
    ev = _event(10, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert [s["kind"] for s in FakeRespondClient.sent] == ["text"]
    (row,) = _rows(db, ev["event_id"])
    assert row.status == "success"
    assert row.error_code is None


def test_ac_uw006_closed_window_without_a_template_skips(db):
    c = _contact(db, name="Ali")
    ev = _event(10, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert FakeRespondClient.sent == []
    (row,) = _rows(db, ev["event_id"])
    assert (row.status, row.error_code) == ("skipped", "NO_TEMPLATE")


def test_ac_uw005_failed_open_window_send_logs_a_text_attempt(db, monkeypatch):
    _map_template(db)
    _window(monkeypatch, True)
    c = _contact(db, name="Ali")
    ev = _event(10, phone=c.phone_number)
    FakeRespondClient.fail_with = RuntimeError("respond 500")

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    (row,) = _rows(db, ev["event_id"])
    assert (row.status, row.error_code) == ("failed", "SEND_FAILED")
    p = _payload(row)
    assert p["message"]["type"] == "text"
    assert p["event"]["sent_as"] == "text"


def test_ac_uw007_redelivered_event_not_sent_twice_in_window(db, monkeypatch):
    _map_template(db)
    _window(monkeypatch, True)
    c = _contact(db, name="Ali")
    ev = _event(10, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))
    svc.set_cursor(db, BASE_URL, 0)
    db.commit()
    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert len(FakeRespondClient.sent) == 1


# ---------------------------------------------------------------------------
# Idempotency (AC-IS030, AC-IS031)
# ---------------------------------------------------------------------------


def test_ac_is030_redelivered_event_is_not_sent_twice(db):
    _map_template(db)
    c = _contact(db)
    ev = _event(11, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))
    # Cursor rewound (at-least-once redelivery of the same event).
    svc.set_cursor(db, BASE_URL, 0)
    db.commit()
    out = svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert len(FakeRespondClient.sent) == 1
    assert out["seen"] == 1
    assert len(_rows(db, ev["event_id"])) == 1
    assert svc.get_cursor(db, BASE_URL) == 11


def test_ac_is030_same_event_twice_in_one_page(db):
    _map_template(db)
    c = _contact(db)
    ev = _event(12, phone=c.phone_number)
    dup = dict(ev, seq=13)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev, dup]))

    assert len(FakeRespondClient.sent) == 1
    assert len(_rows(db, ev["event_id"])) == 1
    assert svc.get_cursor(db, BASE_URL) == 13


def test_ac_is031_database_refuses_a_second_row_for_an_event(db):
    eid = str(uuid.uuid4())
    for _ in range(2):
        db.add(
            IntegrationLog(
                integration_channel="respond_io",
                business_table="ideation_status_events",
                business_id=eid,
                direction="outbound",
                endpoint="ideation_status_update",
                http_method="POST",
                status="skipped",
            )
        )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ---------------------------------------------------------------------------
# Guards (AC-IS040 to AC-IS044)
# ---------------------------------------------------------------------------


def test_ac_is040_is_test_never_sends_and_logs(db):
    _map_template(db)
    c = _contact(db)
    ev = _event(20, phone=c.phone_number, is_test=True)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert FakeRespondClient.sent == []
    (row,) = _rows(db, ev["event_id"])
    assert (row.status, row.error_code) == ("skipped", "IS_TEST")
    assert row.external_reference == "IDEA-0020"


def test_ac_is041_opted_out_skips_and_logs(db):
    _map_template(db)
    c = _contact(db, outbound=False)
    ev = _event(21, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert FakeRespondClient.sent == []
    (row,) = _rows(db, ev["event_id"])
    assert (row.status, row.error_code) == ("skipped", "OPTED_OUT")


def test_ac_is042_unknown_phone_skips_and_logs(db):
    tpl = _map_template(db)
    ev = _event(22, phone="+60900000074")

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert FakeRespondClient.sent == []
    (row,) = _rows(db, ev["event_id"])
    assert (row.status, row.error_code) == ("skipped", "UNKNOWN_CONTACT")
    # AC-IS061: a skip still names the mapped template.
    assert _payload(row)["event"]["template"] == tpl.name


def test_ac_is042_contact_without_respond_io_id_is_unknown(db):
    _map_template(db)
    c = _contact(db, respond_io_id=None)
    ev = _event(23, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert FakeRespondClient.sent == []
    assert _rows(db, ev["event_id"])[0].error_code == "UNKNOWN_CONTACT"


def test_ac_is043_phone_matches_by_digits(db):
    _map_template(db)
    digits = f"601{uuid.uuid4().int % 10**8:08d}"
    c = _contact(db, phone=digits)
    ev = _event(24, phone=f"+{digits[:2]} {digits[2:4]}-{digits[4:]}")

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert FakeRespondClient.sent[0]["identifier"] == c.respond_io_id


def test_ac_is044_no_requester_phone_skips_and_logs(db):
    _map_template(db)
    ev = _event(25, phone=None)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert _rows(db, ev["event_id"])[0].error_code == "NO_REQUESTER_PHONE"


# ---------------------------------------------------------------------------
# Template mapping and send errors (AC-IS051, AC-IS052), AC-IS061, AC-IS062
# ---------------------------------------------------------------------------


def test_ac_is051_missing_mapping_skips_logs_and_goes_on(db):
    c = _contact(db)
    ev1 = _event(30, phone=c.phone_number)
    ev2 = _event(31, phone=c.phone_number)

    out = svc.poll_ideation_status_events(db, fetch=FakeFeed([ev1, ev2]))

    assert FakeRespondClient.sent == []
    assert out["skipped"] == 2
    for ev in (ev1, ev2):
        (row,) = _rows(db, ev["event_id"])
        assert (row.status, row.error_code) == ("skipped", "NO_TEMPLATE")
        assert "no default template configured" in (row.error_message or "")
        assert _payload(row)["event"]["template"] is None
    assert svc.get_cursor(db, BASE_URL) == 31


# ---------------------------------------------------------------------------
# Outbox shape (owner hand test, 29 Sep): the row's request_payload carries the same
# ``message`` block every other template send writes, so the Respond Outbox renders
# the template and its filled parameters; the event metadata rides under ``event``.
# ---------------------------------------------------------------------------

_FILLED = "Update on your idea IDEA-{n:04d}: it is now Discussed. Track it here: https://ss.test/public/ideas/tok{n}"


def _assert_outbox_renders(row: IntegrationLog, tpl: RespondMessageTemplate, n: int) -> None:
    from app.api.v1.system.respond_outbox import _parse_payload

    p = _payload(row)
    msg = p["message"]
    assert msg["type"] == "whatsapp_template"
    assert msg["template_name"] == tpl.name
    assert msg["template_id"] == str(tpl.id)
    assert msg["use_case"] == "ideation_status_update"
    assert msg["parameters"] == [f"IDEA-{n:04d}", "Discussed", f"https://ss.test/public/ideas/tok{n}"]
    assert p["event"]["seq"] == n
    assert p["event"]["idea_number"] == f"IDEA-{n:04d}"
    sent_as, text, template_name, _button = _parse_payload(row.request_payload)
    assert (sent_as, text, template_name) == ("template", _FILLED.format(n=n), tpl.name)


def test_outbox_renders_a_sent_row(db):
    tpl = _map_template(db)
    c = _contact(db)
    ev = _event(90, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    (row,) = _rows(db, ev["event_id"])
    assert row.status == "success"
    _assert_outbox_renders(row, tpl, 90)


def test_outbox_renders_a_failed_row(db):
    tpl = _map_template(db)
    c = _contact(db)
    ev = _event(91, phone=c.phone_number)
    FakeRespondClient.fail_with = RuntimeError("respond 500")

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    (row,) = _rows(db, ev["event_id"])
    assert (row.status, row.error_code) == ("failed", "SEND_FAILED")
    _assert_outbox_renders(row, tpl, 91)


@pytest.mark.parametrize(
    "case, code",
    [("opted_out", "OPTED_OUT"), ("unknown_contact", "UNKNOWN_CONTACT"), ("is_test", "IS_TEST")],
)
def test_outbox_renders_a_skipped_row_when_the_template_resolves(db, case, code):
    tpl = _map_template(db)
    n = {"opted_out": 92, "unknown_contact": 93, "is_test": 94}[case]
    if case == "opted_out":
        ev = _event(n, phone=_contact(db, outbound=False).phone_number)
    elif case == "unknown_contact":
        ev = _event(n, phone="+60900000075")
    else:
        ev = _event(n, phone=_contact(db).phone_number, is_test=True)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert FakeRespondClient.sent == []
    (row,) = _rows(db, ev["event_id"])
    assert (row.status, row.error_code) == ("skipped", code)
    _assert_outbox_renders(row, tpl, n)


def test_no_template_skip_keeps_the_event_and_has_no_message(db):
    c = _contact(db)
    ev = _event(95, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    (row,) = _rows(db, ev["event_id"])
    assert (row.status, row.error_code) == ("skipped", "NO_TEMPLATE")
    p = _payload(row)
    assert "message" not in p
    assert p["event"]["event_id"] == ev["event_id"]
    assert p["event"]["template"] is None


def test_ac_is051_unapproved_template_skips(db):
    _map_template(db, status="pending")
    c = _contact(db)
    ev = _event(32, phone=c.phone_number)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert _rows(db, ev["event_id"])[0].error_code == "NO_TEMPLATE"


def test_ac_is052_send_error_logs_failed_and_advances(db):
    _map_template(db)
    c = _contact(db)
    ev1 = _event(33, phone=c.phone_number)
    ev2 = _event(34, phone=c.phone_number)
    FakeRespondClient.fail_with = RuntimeError("respond 500")

    out = svc.poll_ideation_status_events(db, fetch=FakeFeed([ev1, ev2]))

    assert out["failed"] == 2
    row = _rows(db, ev1["event_id"])[0]
    assert (row.status, row.error_code) == ("failed", "SEND_FAILED")
    assert "respond 500" in row.error_message
    assert svc.get_cursor(db, BASE_URL) == 34


def test_ac_is062_no_row_is_ever_pending_or_processing(db):
    _map_template(db)
    c = _contact(db)
    off = _contact(db, outbound=False)
    events = [
        _event(40, phone=c.phone_number),
        _event(41, phone=c.phone_number, is_test=True),
        _event(42, phone=off.phone_number),
        _event(43, phone="+60900000075"),
    ]

    svc.poll_ideation_status_events(db, fetch=FakeFeed(events))

    statuses = {r.status for r in _rows(db)}
    assert statuses <= {"success", "skipped", "failed"}
    assert len(_rows(db)) == 4


# ---------------------------------------------------------------------------
# Poller and cursor (AC-IS010 to AC-IS017)
# ---------------------------------------------------------------------------


def test_ac_is010_feed_request_shape(monkeypatch):
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"events": [], "next_after": 7})

    real_client = httpx.Client
    monkeypatch.setattr(
        svc.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw)
    )

    out = svc.fetch_status_events(BASE_URL + "/", API_KEY, after=7, limit=100)

    assert out == {"events": [], "next_after": 7}
    assert seen["path"] == "/be/ideation/intake/status-events"
    assert seen["params"] == {"after": "7", "limit": "100", "includeTest": "true"}
    assert seen["auth"] == f"Bearer {API_KEY}"


def test_ac_is010_poll_uses_the_stored_cursor_and_limit_100(db):
    svc.set_cursor(db, BASE_URL, 55)
    db.commit()
    feed = FakeFeed([])

    svc.poll_ideation_status_events(db, fetch=feed)

    assert feed.calls == [{"base_url": BASE_URL, "api_key": API_KEY, "after": 55, "limit": 100}]


def test_ac_is011_events_handled_ascending(db):
    _map_template(db)
    c = _contact(db)
    events = [_event(s, phone=c.phone_number) for s in (50, 51, 52)]

    svc.poll_ideation_status_events(db, fetch=FakeFeed(events, shuffle=True))

    assert [s["parameters"][0] for s in FakeRespondClient.sent] == [
        "IDEA-0050", "IDEA-0051", "IDEA-0052",
    ]
    assert svc.get_cursor(db, BASE_URL) == 52


def test_ac_is012_cursor_moves_with_each_handled_event(db, monkeypatch):
    _map_template(db)
    c = _contact(db)
    events = [_event(s, phone=c.phone_number) for s in (60, 61)]
    cursors_at_send: list[int] = []

    original = FakeRespondClient.send_template_message

    def spy(self, identifier, **kwargs):
        cursors_at_send.append(svc.get_cursor(db, BASE_URL))
        return original(self, identifier, **kwargs)

    monkeypatch.setattr(FakeRespondClient, "send_template_message", spy)

    svc.poll_ideation_status_events(db, fetch=FakeFeed(events))

    # The first send saw the untouched cursor, the second saw event 60 committed.
    assert cursors_at_send == [0, 60]
    assert svc.get_cursor(db, BASE_URL) == 61


def test_ac_is013_failed_log_write_keeps_the_cursor_and_does_not_raise(db, monkeypatch):
    _map_template(db)
    c = _contact(db)
    events = [_event(s, phone=c.phone_number, is_test=True) for s in (70, 71, 72)]
    real_commit = svc._commit_handled
    calls = {"n": 0}

    def flaky(db_, base_url, event, row):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("db down")
        return real_commit(db_, base_url, event, row)

    monkeypatch.setattr(svc, "_commit_handled", flaky)

    out = svc.poll_ideation_status_events(db, fetch=FakeFeed(events))

    assert svc.get_cursor(db, BASE_URL) == 70
    assert calls["n"] == 2  # stopped, event 72 not touched this tick
    assert out["stopped"] is True
    assert [r.external_reference for r in _rows(db)] == ["IDEA-0070"]


def test_ac_is013_a_sent_event_that_cannot_be_fully_logged_is_never_resent(db, monkeypatch):
    """Reviewer round 1 blocker: a success whose full row fails to commit must still
    be recorded (minimal row + cursor), or every tick re-sends it."""
    _map_template(db)
    c = _contact(db)
    ev = _event(74, phone=c.phone_number)
    real_commit = svc._commit_handled

    def always_fails(db_, base_url, event, row):
        raise RuntimeError("row rejected")

    monkeypatch.setattr(svc, "_commit_handled", always_fails)
    first = svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))
    monkeypatch.setattr(svc, "_commit_handled", real_commit)
    svc.set_cursor(db, BASE_URL, 0)  # even a rewound cursor must not re-send it
    db.commit()
    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert len(FakeRespondClient.sent) == 1
    assert first["stopped"] is False
    (row,) = _rows(db, ev["event_id"])
    assert (row.status, row.error_code) == ("success", "LOG_DEGRADED")


def test_ac_is023_an_overlong_title_is_logged_and_sent_once(db):
    """The title fallback can exceed external_reference's 255 chars."""
    _map_template(db)
    c = _contact(db)
    ev = _event(75, phone=c.phone_number, idea_number=None, idea_title="T" * 300)

    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))
    svc.set_cursor(db, BASE_URL, 0)
    db.commit()
    svc.poll_ideation_status_events(db, fetch=FakeFeed([ev]))

    assert len(FakeRespondClient.sent) == 1
    (row,) = _rows(db, ev["event_id"])
    assert row.status == "success"
    assert len(row.external_reference) == 255


def test_ac_is022_unmerged_with_no_status_label_reads_cleanly(db):
    ev = _event(76, phone=None, kind="unmerged", status_label="",
                separated_from={"idea_number": "IDEA-0012", "title": "x"})
    assert svc.build_context_vars(ev)["status_label"] == "handled separately again from IDEA-0012"


def test_ac_is011_seq_must_be_an_int(db):
    assert svc._seq({"seq": 5}) == 5
    assert svc._seq({"seq": "6"}) == 6
    assert svc._seq({"seq": 5.7}) is None
    assert svc._seq({"seq": True}) is None


def test_ac_is014_feed_outage_keeps_the_cursor(db):
    svc.set_cursor(db, BASE_URL, 80)
    db.commit()

    out = svc.poll_ideation_status_events(
        db, fetch=FakeFeed([], fail=svc.IdeationFeedError("boom"))
    )

    assert out["sent"] == 0
    assert svc.get_cursor(db, BASE_URL) == 80
    assert _rows(db) == []


def test_ac_is014_http_error_becomes_feed_error(monkeypatch):
    real_client = httpx.Client
    monkeypatch.setattr(
        svc.httpx,
        "Client",
        lambda **kw: real_client(
            transport=httpx.MockTransport(lambda r: httpx.Response(503, text="down")), **kw
        ),
    )
    with pytest.raises(svc.IdeationFeedError):
        svc.fetch_status_events(BASE_URL, API_KEY, after=0, limit=100)


def test_ac_is015_config_error_does_not_raise(db, monkeypatch):
    def boom(_db):
        raise RuntimeError("cannot decrypt")

    monkeypatch.setattr(svc, "_resolve_ideation_config", boom)
    assert svc.poll_ideation_status_events(db, fetch=FakeFeed([]))["sent"] == 0


def test_ac_is015_not_configured_does_nothing(db, monkeypatch):
    monkeypatch.setattr(
        svc,
        "_resolve_ideation_config",
        lambda _db: _IdeationConfig(base_url=None, api_key=None, product_id=None),
    )
    feed = FakeFeed([])

    out = svc.poll_ideation_status_events(db, fetch=feed)

    assert feed.calls == []
    assert out["sent"] == 0


def test_ac_is016_cursor_is_per_feed_base_url(db):
    svc.set_cursor(db, BASE_URL, 90)
    svc.set_cursor(db, "https://other.test", 3)
    db.commit()

    assert svc.get_cursor(db, BASE_URL + "/") == 90
    assert svc.get_cursor(db, "https://other.test") == 3
    assert svc.get_cursor(db, "https://third.test") == 0


def test_ac_is017_scheduler_registers_the_poll_every_60s(monkeypatch):
    from apscheduler.schedulers.background import BackgroundScheduler

    import app.scheduler.task_scheduler as ts

    jobs: dict[str, Any] = {}

    def fake_add_job(self, func, trigger=None, id=None, **kwargs):
        jobs[id] = (func, trigger)

    monkeypatch.setattr(BackgroundScheduler, "add_job", fake_add_job)
    monkeypatch.setattr(BackgroundScheduler, "start", lambda self: None)
    monkeypatch.setattr(ts, "register_task_handlers", lambda: None)

    ts.start_scheduler()

    func, trigger = jobs["ideation_status_events_poll"]
    assert func is ts._ideation_status_events_tick
    assert trigger.interval.total_seconds() == 60


def test_tick_never_raises(monkeypatch):
    import app.scheduler.task_scheduler as ts

    def boom(_db):
        raise RuntimeError("x")

    monkeypatch.setattr(svc, "poll_ideation_status_events", boom)
    ts._ideation_status_events_tick()  # must not raise
