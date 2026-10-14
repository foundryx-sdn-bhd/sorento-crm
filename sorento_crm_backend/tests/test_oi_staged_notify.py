"""Order inquiry: park reserve requests / reserve notices, send all parked
(`PLAN-oi-staged-notify-14oct.md`; contract `documentation/plans/scm/oi-staged-notify-14oct-
acceptance-criteria.md`, AC-SN-01..11, AC-SN-20..27, AC-SN-30, AC-SN-31; AC-SN-32 lives in
`test_oisn_migration.py`). TEST-FIRST (Phase 2): written before any implementation exists, so
a red here is a missing route (404/405), an unknown payload field, a missing column or a
wrong assertion, never a fixture bug.

Postgres only (`blank_session()` through the reserve suite's own `api` world); every chain is
seeded here, nothing borrowed from a shared table. Dispatch is always captured
(`_captured_dispatches`), never sent.

Automations: the blank schema has none, so `_automations(db)` replays the real seed chain
(`test_email_layout_migrations._seed`, 212 -> soatt_0001) plus `eml_0002_seed_layouts`, then
`oisn_0001_staged_notify` when that file exists (so the bulk template branch is in place).
Tests that assert the bulk body (AC-SN-11/26) use `strict=True`, which fails if the migration
is missing; the rest only need the automation to resolve recipients.

NAMED ASSUMPTIONS (beyond the plan's contract):

1. `OrderInquiryReserveService(db).send_parked_requests(actor_user_id=...)` and
   `.send_parked_notices(actor_user_id=...)` are the service entry points (plan names
   `send_parked_requests(actor)`); only the AC-SN-06 rollback test calls the service directly.
2. `items_sent` is asserted only on data where one request = one OI = one row, so "items" read
   as requests, OIs or lines alike.
3. `my_parked_requests` / `my_parked_notices` / the preview `line_count` count request ROWS
   (lines); asserted with one row per request wherever the number matters.
4. Notices: the "(name)" in "Reserved: N order inquiries (name)" is the CS person who sends
   (the actor), as in the UAC example (Eling); requests name the requester (Joey).
5. AC-SN-07 "concurrent" send is NOT tested with two real sessions (the lane's one-session
   `blank_session` cannot make that deterministic); the sequential double send is. The
   `SELECT ... FOR UPDATE` is left to the reviewer.
6. A multi-OI group's "N" in "and M more order inquiries." is used literally, M=2 here.
"""
from __future__ import annotations

import re
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app.models.project_so import (
    OrderInquiryReserveRequest,
    OrderInquiryReserveRequestRow,
)

from ._pg_fixture import blank_session
from .test_email_layout_migrations import _run, _scripts, _seed
from .test_order_inquiry_reserve import (
    CANCEL_URL,
    COMMIT_URL,
    REQUESTER_PERMISSIONS,
    REQUEST_URL,
    RESERVER_PERMISSIONS,
    _api,
    _as,
    _captured_dispatches,
    _pso,
    _register,
    _requested_calls,
    _reserved_calls,
    _service,
)
from .test_order_inquiry_reserve_round2 import _foreign_reserve_request
from .test_order_inquiry_worklist import LIST, _inquiry_for, _row, _uid, _user

MARKER = "zzt-oi-sn"
HEADERS_URL = "/api/v1/project-sales/order-inquiry-headers"
REQ_PREVIEW = f"{LIST}/reserve-requests/parked/preview"
REQ_SEND = f"{LIST}/reserve-requests/parked/send"
NOTE_PREVIEW = f"{LIST}/reserve-notices/parked/preview"
NOTE_SEND = f"{LIST}/reserve-notices/parked/send"
SUMMARY_URL = f"{LIST}/reserve-status/summary"
RECIPIENTS_URL = lambda inquiry_id: f"{LIST}/{inquiry_id}/reserve-notices/recipients"  # noqa: E731


# --------------------------------------------------------------------------------- #
# harness                                                                            #
# --------------------------------------------------------------------------------- #


@pytest.fixture()
def sn():
    """Joey (requester, both grants) is the default actor; `world.reserver` is CS."""
    yield from _api(RESERVER_PERMISSIONS)


def _automations(world, *, strict: bool = False) -> None:
    """Seed the two reserve automations + templates exactly as a real database carries them."""
    db = world.db
    mods = _seed(db)
    _run(mods["eml_0002_seed_layouts"], db)
    scripts = _scripts()
    if "oisn_0001_staged_notify" in scripts:
        _run(scripts["oisn_0001_staged_notify"], db)
    else:
        assert not strict, "migration oisn_0001_staged_notify does not exist yet"
    db.commit()


def _set_automation(world, trigger: str, *, enabled=None, config: dict | None = None) -> None:
    import json

    if enabled is not None:
        world.db.execute(
            sa.text("UPDATE automations SET enabled = :e WHERE trigger_type = :t"),
            {"e": enabled, "t": trigger},
        )
    if config is not None:
        world.db.execute(
            sa.text(
                "UPDATE automations SET recipient_config = recipient_config || CAST(:c AS jsonb) "
                "WHERE trigger_type = :t"
            ),
            {"c": json.dumps(config), "t": trigger},
        )
    world.db.commit()


def _new_oi(world, raiser_id: str):
    inquiry = _inquiry_for(world.db, world.company_id, _pso(world.db, world.company_id))
    inquiry.raised_by = raiser_id
    world.db.commit()
    return inquiry


def _oi_row(world, inquiry, qty="50", **fields):
    fields.setdefault("item_code", f"{MARKER}-{_uid()[:6]}")
    fields.setdefault("stock_location", world.site.warehouse_code)
    from datetime import date

    fields.setdefault("delivery_date", date(2026, 10, 1))
    row = _row(world.db, world.company_id, inquiry, qty=qty, **fields)
    world.db.commit()
    return row


def _request(client, world, inquiry, rows_qty, *, park=None) -> str:
    """`rows_qty` = [(row, qty_requested)]; returns the request id."""
    body = {
        "rows": [
            {"row_id": row.id, "qty_requested": qty, "warehouse_id": world.site.id}
            for row, qty in rows_qty
        ]
    }
    if park is not None:
        body["park"] = park
    response = client.post(REQUEST_URL(inquiry.id), json=body)
    assert response.status_code == 201, response.text
    world.db.commit()
    return response.json()["id"]


def _commit(world, inquiry, *, reserves=(), amendments=(), park_notice=None, user=None, perms=None):
    body = {"reserves": list(reserves), "amendments": list(amendments)}
    if park_notice is not None:
        body["park_notice"] = park_notice
    with _as(world.db, user or world.reserver, perms or RESERVER_PERMISSIONS) as cs:
        response = cs.post(COMMIT_URL(inquiry.id), json=body)
    assert response.status_code == 200, response.text
    world.db.commit()
    return response


def _reserve(world, row, qty):
    return {"row_id": row.id, "warehouse_id": world.site.id, "qty_reserved": qty}


def _rr(world, row) -> OrderInquiryReserveRequestRow:
    world.db.expire_all()
    return (
        world.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.row_id == row.id)
        .order_by(OrderInquiryReserveRequestRow.id)
        .one()
    )


def _req(world, request_id) -> OrderInquiryReserveRequest:
    world.db.expire_all()
    return (
        world.db.query(OrderInquiryReserveRequest)
        .filter(OrderInquiryReserveRequest.id == request_id)
        .one()
    )


def _parked_request(client, world, raiser_id, *, qty="50", park=True):
    """One OI raised by `raiser_id`, one row, one request. Returns (inquiry, row, request_id)."""
    inquiry = _new_oi(world, raiser_id)
    row = _oi_row(world, inquiry, qty=qty)
    request_id = _request(client, world, inquiry, [(row, qty)], park=park)
    return inquiry, row, request_id


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def _name(world, user_id: str) -> str:
    return world.db.execute(sa.text("SELECT name FROM users WHERE id = :i"), {"i": user_id}).scalar()


def _names(people) -> list:
    return [p["name"] for p in people]


# --------------------------------------------------------------------------------- #
# Request side                                                                       #
# --------------------------------------------------------------------------------- #


def test_AC_SN_01_park_true_creates_request_without_dispatch(sn, monkeypatch):
    client, world = sn
    _register()
    calls = _captured_dispatches(monkeypatch)
    _inq, _row_, request_id = _parked_request(client, world, world.reserver, park=True)

    request = _req(world, request_id)
    assert request.state == "requested"
    assert request.parked_at is not None
    assert _requested_calls(calls) == [], "a parked request must queue no dispatch"


@pytest.mark.parametrize("park", [None, False])
def test_AC_SN_02_park_omitted_or_false_is_unchanged(sn, monkeypatch, park):
    client, world = sn
    _register()
    calls = _captured_dispatches(monkeypatch)
    _inq, _row_, request_id = _parked_request(client, world, world.reserver, park=park)

    assert _req(world, request_id).parked_at is None
    assert len(_requested_calls(calls)) == 1


def test_AC_SN_03_parked_request_is_answerable_by_cs(sn, monkeypatch):
    client, world = sn
    _register()
    _captured_dispatches(monkeypatch)
    inquiry, row, request_id = _parked_request(client, world, world.reserver, park=True)

    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        listed = cs.get(f"{LIST}/{inquiry.id}/reserve-requests")
        assert listed.status_code == 200, listed.text
        assert [r["id"] for r in listed.json()] == [request_id]
    _commit(world, inquiry, reserves=[_reserve(world, row, "50")])
    assert _rr(world, row).qty_reserved == Decimal("50")
    assert _req(world, request_id).state == "reserved"


def test_AC_SN_04_preview_dev_config_two_emails(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    niki = _user(world.db, f"{MARKER} Niki")
    for _ in range(2):
        _parked_request(client, world, eling)
    _parked_request(client, world, niki)

    body = _ok(client.post(REQ_PREVIEW))
    by_to = {e["to"]["name"]: e for e in body["emails"]}
    assert set(by_to) == {f"{MARKER} Eling", f"{MARKER} Niki"}, body
    assert body["unresolved"] == []
    eling_mail, niki_mail = by_to[f"{MARKER} Eling"], by_to[f"{MARKER} Niki"]
    assert _names(eling_mail["cc"]) == [_name(world, world.requester)]
    assert eling_mail["inquiry_count"] == 2 and eling_mail["line_count"] == 2
    assert niki_mail["inquiry_count"] == 1 and niki_mail["line_count"] == 1
    for mail in body["emails"]:
        assert mail["subject"] and mail["body_html"]
        assert _names(mail["cc"]) == [_name(world, world.requester)], "Cc is the requester only"


def test_AC_SN_04_preview_fixed_cs_user_one_email(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    _captured_dispatches(monkeypatch)
    cs_fixed = _user(world.db, f"{MARKER} CSFixed")
    eling = _user(world.db, f"{MARKER} Eling")
    niki = _user(world.db, f"{MARKER} Niki")
    _set_automation(world, "order_inquiry_reserve_requested", config={"user_ids": [cs_fixed]})
    _parked_request(client, world, eling)
    _parked_request(client, world, eling)
    _parked_request(client, world, niki)

    body = _ok(client.post(REQ_PREVIEW))
    assert len(body["emails"]) == 1, body
    mail = body["emails"][0]
    assert mail["to"]["name"] == f"{MARKER} CSFixed"
    cc = _names(mail["cc"])
    assert len(cc) == len(set(cc)) == 3, f"Eling once, deduped: {cc}"
    assert {f"{MARKER} Eling", f"{MARKER} Niki", _name(world, world.requester)} == set(cc)
    assert mail["inquiry_count"] == 3


def test_AC_SN_04_preview_requires_acknowledge(sn):
    client, world = sn
    with _as(world.db, world.reserver, ["projects.projects.view"]) as nobody:
        assert nobody.post(REQ_PREVIEW).status_code == 403
        assert nobody.post(REQ_SEND).status_code == 403


def test_AC_SN_05_only_unanswered_rows_with_current_remaining(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")

    # One request, two rows; CS answers one -> only the other is carried.
    inquiry = _new_oi(world, eling)
    row_a, row_b = _oi_row(world, inquiry, qty="50"), _oi_row(world, inquiry, qty="30")
    _request(client, world, inquiry, [(row_a, "50"), (row_b, "30")], park=True)
    _commit(world, inquiry, reserves=[_reserve(world, row_a, "50")])
    # Answered in full (completes -> out) and cancelled (out) requests are not carried.
    _parked_request(client, world, eling)  # control: still parked, unanswered
    done_inq, done_row, _ = _parked_request(client, world, eling)
    _commit(world, done_inq, reserves=[_reserve(world, done_row, "50")])
    _inq, _r, cancelled_id = _parked_request(client, world, eling)
    assert client.post(CANCEL_URL(cancelled_id)).status_code == 200
    world.db.commit()

    body = _ok(client.post(REQ_PREVIEW))
    assert len(body["emails"]) == 1
    mail = body["emails"][0]
    assert mail["inquiry_count"] == 2, "the half-answered OI and the control OI"
    assert mail["line_count"] == 2, "row_b + the control row, never row_a / done / cancelled"
    assert row_a.item_code not in mail["body_html"]


def test_AC_SN_06_send_stamps_and_queues_one_dispatch_per_group(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    calls = _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    niki = _user(world.db, f"{MARKER} Niki")
    _a, _ra, req_a = _parked_request(client, world, eling)
    _b, _rb, req_b = _parked_request(client, world, niki)
    assert _requested_calls(calls) == []

    body = _ok(client.post(REQ_SEND))
    assert body["emails_sent"] == 2 and body["items_sent"] == 2 and body["unresolved"] == []
    assert len(_requested_calls(calls)) == 2, "one dispatch per email group"
    assert _req(world, req_a).parked_at is None and _req(world, req_b).parked_at is None


def test_AC_SN_06_rollback_dispatches_nothing_and_keeps_park(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    calls = _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    _inq, _row_, request_id = _parked_request(client, world, eling)

    savepoint = world.db.begin_nested()
    sent = _service(world.db).send_parked_requests(actor_user_id=world.requester)
    assert sent is not None
    savepoint.rollback()
    world.db.commit()  # an unrelated commit: nothing above may still fire

    assert _requested_calls(calls) == []
    assert _req(world, request_id).parked_at is not None, "the rolled-back stamp must not stick"


def test_AC_SN_07_second_send_sends_nothing(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    calls = _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    _parked_request(client, world, eling)

    assert _ok(client.post(REQ_SEND))["emails_sent"] == 1
    after_first = len(_requested_calls(calls))
    again = _ok(client.post(REQ_SEND))
    assert again["emails_sent"] == 0 and again["items_sent"] == 0
    assert len(_requested_calls(calls)) == after_first, "no extra dispatch"


def test_AC_SN_08_unresolved_group_stays_parked(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    calls = _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    _inq, _row_, request_id = _parked_request(client, world, eling)
    _set_automation(world, "order_inquiry_reserve_requested", enabled=False)

    preview = _ok(client.post(REQ_PREVIEW))
    assert preview["emails"] == []
    assert preview["unresolved"] == [{"inquiry_count": 1, "line_count": 1}]
    sent = _ok(client.post(REQ_SEND))
    assert sent["emails_sent"] == 0 and len(sent["unresolved"]) == 1
    assert _requested_calls(calls) == []
    assert _req(world, request_id).parked_at is not None, "unresolved stays parked"


def test_AC_SN_09_colleague_parked_is_counted_not_sent(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    calls = _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    mia = _user(world.db, f"{MARKER} Mia")
    _mine, _r, my_req = _parked_request(client, world, eling)
    other = _new_oi(world, eling)
    other_row = _oi_row(world, other)
    with _as(world.db, mia, REQUESTER_PERMISSIONS) as mia_client:
        created = mia_client.post(
            REQUEST_URL(other.id),
            json={"rows": [{"row_id": other_row.id, "qty_requested": "50"}], "park": True},
        )
        assert created.status_code == 201, created.text
    world.db.commit()
    their_req = created.json()["id"]

    summary = _ok(client.get(SUMMARY_URL))
    assert summary["parked"] == 2, "everyone's parked count"
    assert summary["my_parked_requests"] == 1
    preview = _ok(client.post(REQ_PREVIEW))
    assert sum(e["inquiry_count"] for e in preview["emails"]) == 1
    assert _ok(client.post(REQ_SEND))["emails_sent"] == 1
    assert len(_requested_calls(calls)) == 1
    assert _req(world, my_req).parked_at is None
    assert _req(world, their_req).parked_at is not None, "a colleague's park is untouched"


def test_AC_SN_10_cancel_and_completing_commit_clear_parked_at(sn, monkeypatch):
    client, world = sn
    _register()
    calls = _captured_dispatches(monkeypatch)
    _i1, _r1, cancelled = _parked_request(client, world, world.reserver)
    assert client.post(CANCEL_URL(cancelled)).status_code == 200
    world.db.commit()
    assert _req(world, cancelled).parked_at is None
    assert _requested_calls(calls) == []

    inquiry, row, completed = _parked_request(client, world, world.reserver)
    _commit(world, inquiry, reserves=[_reserve(world, row, "50")])
    assert _req(world, completed).parked_at is None


def test_AC_SN_11_one_oi_email_keeps_todays_subject(sn, monkeypatch):
    client, world = sn
    _automations(world, strict=True)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    inquiry, _row_, _rid = _parked_request(client, world, eling)

    mail = _ok(client.post(REQ_PREVIEW))["emails"][0]
    assert mail["subject"].startswith(f"Reserve request: {inquiry.inquiry_no} #1"), mail["subject"]
    assert "Reserve requests:" not in mail["subject"]
    assert "order inquiries need your reserve" not in mail["body_html"]


def _bulk_inquiries(client, world, raiser_id, n):
    made = []
    for _ in range(n):
        inquiry, _row_, _rid = _parked_request(client, world, raiser_id)
        made.append(inquiry)
    return sorted(made, key=lambda i: i.inquiry_no)


def test_AC_SN_11_multi_oi_email_is_short_with_five_listed(sn, monkeypatch):
    client, world = sn
    _automations(world, strict=True)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    ordered = _bulk_inquiries(client, world, eling, 7)

    body = _ok(client.post(REQ_PREVIEW))
    assert len(body["emails"]) == 1
    mail = body["emails"][0]
    joey_name = world.db.execute(
        sa.text("SELECT name FROM users WHERE id = :i"), {"i": world.requester}
    ).scalar()
    assert mail["subject"] == f"Reserve requests: 7 order inquiries ({joey_name})", mail["subject"]
    html = mail["body_html"]
    assert "7 order inquiries need your reserve" in html
    for inquiry in ordered[:5]:
        assert inquiry.inquiry_no in html, f"first five by OI no: {inquiry.inquiry_no}"
    for inquiry in ordered[5:]:
        assert inquiry.inquiry_no not in html, f"never more than 5 listed: {inquiry.inquiry_no}"
    assert "and 2 more order inquiries." in html
    assert "View order inquiries that need your reserve" in html
    assert "/project-sales/order-inquiries?reserve=needs_reserve" in html
    assert len(re.findall(r'class="em-btn-link"', html)) == 1, "ONE button"


# --------------------------------------------------------------------------------- #
# Notice side                                                                        #
# --------------------------------------------------------------------------------- #


def _sent_request(client, world, raiser_id, *, qty="50"):
    inquiry, row, request_id = _parked_request(client, world, raiser_id, qty=qty, park=False)
    return inquiry, row, request_id


def test_AC_SN_20_park_notice_stamps_rows_without_dispatch(sn, monkeypatch):
    client, world = sn
    _register()
    calls = _captured_dispatches(monkeypatch)
    inquiry, row, _rid = _sent_request(client, world, world.reserver)
    calls.clear()

    _commit(world, inquiry, reserves=[_reserve(world, row, "50")], park_notice=True)
    rr = _rr(world, row)
    assert rr.qty_reserved == Decimal("50")
    assert rr.notice_parked_at is not None
    assert rr.notice_parked_by == world.reserver
    assert _reserved_calls(calls) == [], "a parked notice queues no dispatch"


def test_AC_SN_21_default_commit_dispatches_and_stamps_notified_qty(sn, monkeypatch):
    client, world = sn
    _register()
    calls = _captured_dispatches(monkeypatch)
    inquiry, row, _rid = _sent_request(client, world, world.reserver)
    # Park first, then a normal (unparked) amend: today's dispatch, park cleared.
    _commit(world, inquiry, reserves=[_reserve(world, row, "50")], park_notice=True)
    calls.clear()
    _commit(world, inquiry, amendments=[{"row_id": row.id, "qty_reserved": "40", "reason": "short"}])

    assert len(_reserved_calls(calls)) == 1
    rr = _rr(world, row)
    assert rr.notified_qty == Decimal("40") == rr.qty_reserved
    assert rr.notice_parked_at is None and rr.notice_parked_by is None


def test_AC_SN_21_park_notice_false_matches_omitted(sn, monkeypatch):
    client, world = sn
    _register()
    calls = _captured_dispatches(monkeypatch)
    inquiry, row, _rid = _sent_request(client, world, world.reserver)
    calls.clear()
    _commit(world, inquiry, reserves=[_reserve(world, row, "50")], park_notice=False)
    assert len(_reserved_calls(calls)) == 1
    assert _rr(world, row).notified_qty == Decimal("50")


def test_AC_SN_22_notice_preview_and_send_actionable_rows_only(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    calls = _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    i1, r1, _a = _sent_request(client, world, eling)
    i2, r2, _b = _sent_request(client, world, eling)
    i3, r3, _c = _sent_request(client, world, eling)
    _commit(world, i1, reserves=[_reserve(world, r1, "50")], park_notice=True)
    _commit(
        world, i2, reserves=[{**_reserve(world, r2, "0"), "reason": "none in stock"}], park_notice=True
    )  # a parked Reserve 0 counts (notified_qty null)
    _commit(world, i3, reserves=[_reserve(world, r3, "50")])  # sent already, never parked
    calls.clear()

    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        preview = _ok(cs.post(NOTE_PREVIEW))
        assert len(preview["emails"]) == 1, preview
        mail = preview["emails"][0]
        assert mail["to"]["name"] == _name(world, world.requester)
        assert mail["inquiry_count"] == 2 and mail["line_count"] == 2
        sent = _ok(cs.post(NOTE_SEND))
    world.db.commit()
    assert sent["emails_sent"] == 1 and sent["unresolved"] == []
    assert len(_reserved_calls(calls)) == 1
    for row, qty in ((r1, "50"), (r2, "0")):
        rr = _rr(world, row)
        assert rr.notified_qty == Decimal(qty)
        assert rr.notice_parked_at is None and rr.notice_parked_by is None


def test_AC_SN_22_notice_routes_require_reserve(sn):
    client, world = sn
    with _as(world.db, world.requester, REQUESTER_PERMISSIONS) as joey:
        assert joey.post(NOTE_PREVIEW).status_code == 403
        assert joey.post(NOTE_SEND).status_code == 403


def test_AC_SN_23_amend_after_sent_and_back(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    inquiry, row, _rid = _sent_request(client, world, eling, qty="200")
    _commit(world, inquiry, reserves=[_reserve(world, row, "200")])  # sent: notified 200
    _commit(
        world,
        inquiry,
        amendments=[{"row_id": row.id, "qty_reserved": "150", "reason": "short"}],
        park_notice=True,
    )
    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        preview = _ok(cs.post(NOTE_PREVIEW))
    assert len(preview["emails"]) == 1 and preview["emails"][0]["line_count"] == 1
    assert "150" in preview["emails"][0]["body_html"]

    _commit(
        world,
        inquiry,
        amendments=[{"row_id": row.id, "qty_reserved": "200"}],
        park_notice=True,
    )
    rr = _rr(world, row)
    assert rr.notice_parked_at is None, "amend back to the sent figure drops the park"
    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        again = _ok(cs.post(NOTE_PREVIEW))
    assert again["emails"] == [] and again["unresolved"] == []


def test_AC_SN_24_two_parked_commits_one_row_latest_figure(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    inquiry, row, _rid = _sent_request(client, world, eling, qty="300")
    _commit(world, inquiry, reserves=[{**_reserve(world, row, "177"), "reason": "short"}], park_notice=True)
    _commit(
        world,
        inquiry,
        amendments=[{"row_id": row.id, "qty_reserved": "133", "reason": "shorter"}],
        park_notice=True,
    )
    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        preview = _ok(cs.post(NOTE_PREVIEW))
    assert len(preview["emails"]) == 1 and preview["emails"][0]["line_count"] == 1
    html = preview["emails"][0]["body_html"]
    assert "133" in html and "177" not in html


def test_AC_SN_25_double_send_unresolved_and_colleague(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    calls = _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    other_cs = _user(world.db, f"{MARKER} OtherCS")
    mine, mine_row, _a = _sent_request(client, world, eling)
    theirs, theirs_row, _b = _sent_request(client, world, eling)
    _commit(world, mine, reserves=[_reserve(world, mine_row, "50")], park_notice=True)
    _commit(
        world, theirs, reserves=[_reserve(world, theirs_row, "50")], park_notice=True,
        user=other_cs, perms=RESERVER_PERMISSIONS,
    )
    calls.clear()

    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        preview = _ok(cs.post(NOTE_PREVIEW))
        assert sum(e["inquiry_count"] for e in preview["emails"]) == 1, "colleague's park excluded"
        first = _ok(cs.post(NOTE_SEND))
        world.db.commit()
        second = _ok(cs.post(NOTE_SEND))
        summary = _ok(cs.get(SUMMARY_URL))
    assert first["emails_sent"] == 1 and second["emails_sent"] == 0
    assert len(_reserved_calls(calls)) == 1
    assert summary["my_parked_notices"] == 0
    assert _rr(world, theirs_row).notice_parked_at is not None

    # Unresolved: nobody to send to -> stays parked, nothing dispatched.
    _set_automation(world, "order_inquiry_reserved", enabled=False)
    with _as(world.db, other_cs, RESERVER_PERMISSIONS) as cs2:
        sent = _ok(cs2.post(NOTE_SEND))
    assert sent["emails_sent"] == 0 and len(sent["unresolved"]) == 1
    assert _rr(world, theirs_row).notice_parked_at is not None
    assert len(_reserved_calls(calls)) == 1


def test_AC_SN_26_multi_oi_notice_email_is_short(sn, monkeypatch):
    client, world = sn
    _automations(world, strict=True)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    made = []
    for _ in range(7):
        inquiry, row, _rid = _sent_request(client, world, eling)
        _commit(world, inquiry, reserves=[_reserve(world, row, "50")], park_notice=True)
        made.append(inquiry)
    ordered = sorted(made, key=lambda i: i.inquiry_no)

    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        body = _ok(cs.post(NOTE_PREVIEW))
    assert len(body["emails"]) == 1
    mail = body["emails"][0]
    cs_name = world.db.execute(
        sa.text("SELECT name FROM users WHERE id = :i"), {"i": world.reserver}
    ).scalar()
    assert mail["subject"] == f"Reserved: 7 order inquiries ({cs_name})", mail["subject"]
    html = mail["body_html"]
    assert "CS answered your reserve requests on 7 order inquiries" in html
    for inquiry in ordered[:5]:
        assert inquiry.inquiry_no in html
    for inquiry in ordered[5:]:
        assert inquiry.inquiry_no not in html
    assert "and 2 more order inquiries." in html
    assert "View reserved order inquiries" in html
    assert "reserve=reserved" in html


def test_AC_SN_26_one_oi_notice_keeps_todays_subject(sn, monkeypatch):
    client, world = sn
    _automations(world, strict=True)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    inquiry, row, _rid = _sent_request(client, world, eling)
    _commit(world, inquiry, reserves=[_reserve(world, row, "50")], park_notice=True)
    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        mail = _ok(cs.post(NOTE_PREVIEW))["emails"][0]
    assert mail["subject"].startswith(f"Reserved: {inquiry.inquiry_no} #1"), mail["subject"]


def test_AC_SN_27_notice_recipients(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    inquiry, _row_, _rid = _sent_request(client, world, eling)

    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        body = _ok(cs.get(RECIPIENTS_URL(inquiry.id)))
        assert body["to"]["name"] == _name(world, world.requester)
        assert _names(body["cc"]) == [f"{MARKER} Eling"]
        assert cs.get(RECIPIENTS_URL(_uid())).status_code == 404
        _fid, _frow, foreign_inquiry_id = _foreign_reserve_request(world.db)
        assert cs.get(RECIPIENTS_URL(foreign_inquiry_id)).status_code == 404
    with _as(world.db, world.requester, REQUESTER_PERMISSIONS) as joey:
        assert joey.get(RECIPIENTS_URL(inquiry.id)).status_code == 403

    _set_automation(world, "order_inquiry_reserved", enabled=False)
    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        none = _ok(cs.get(RECIPIENTS_URL(inquiry.id)))
    assert none["to"] is None


# --------------------------------------------------------------------------------- #
# Status                                                                             #
# --------------------------------------------------------------------------------- #


def _status_world(client, world):
    """One OI per status plus a mixed one and one with no request at all."""
    who = _user(world.db, f"{MARKER} Raiser")
    out = {}
    out["parked"], _r, _x = _parked_request(client, world, who, park=True)
    out["needs"], _r, _x = _parked_request(client, world, who, park=False)
    inq, row, _x = _parked_request(client, world, who, park=False)
    _commit(world, inq, reserves=[_reserve(world, row, "50")], park_notice=True)
    out["notice_parked"] = inq
    inq, row, _x = _parked_request(client, world, who, park=False)
    _commit(world, inq, reserves=[_reserve(world, row, "50")])
    out["reserved"] = inq
    out["none"] = _new_oi(world, who)
    _oi_row(world, out["none"])
    mix = _new_oi(world, who)
    row1, row2 = _oi_row(world, mix), _oi_row(world, mix)
    _request(client, world, mix, [(row1, "50"), (row2, "50")], park=False)
    _commit(world, mix, reserves=[_reserve(world, row1, "50")], park_notice=True)
    out["mixed"] = mix
    return out


def _headers(client, **params):
    body = _ok(client.get(HEADERS_URL, params={"state": "all", "limit": 100, **params}))
    return {row["id"]: row for row in body["data"]}


def test_AC_SN_30_reserve_filter_and_row_fields(sn, monkeypatch):
    client, world = sn
    _register()
    _captured_dispatches(monkeypatch)
    w = _status_world(client, world)
    ids = {k: v.id for k, v in w.items()}

    expect = {
        "parked": {ids["parked"]},
        "needs_reserve": {ids["needs"], ids["mixed"]},
        "notice_parked": {ids["notice_parked"], ids["mixed"]},
        "reserved": {ids["reserved"]},
    }
    for status, want in expect.items():
        assert set(_headers(client, reserve=status)) == want, status

    rows = _headers(client)
    assert rows[ids["parked"]]["reserve_status"] == "parked"
    assert rows[ids["needs"]]["reserve_status"] == "needs_reserve"
    assert rows[ids["notice_parked"]]["reserve_status"] == "notice_parked"
    assert rows[ids["reserved"]]["reserve_status"] == "reserved"
    assert rows[ids["mixed"]]["reserve_status"] == "needs_reserve", "earliest status wins"
    assert rows[ids["none"]]["reserve_status"] is None
    joey = world.db.execute(
        sa.text("SELECT name FROM users WHERE id = :i"), {"i": world.requester}
    ).scalar()
    cs_name = world.db.execute(
        sa.text("SELECT name FROM users WHERE id = :i"), {"i": world.reserver}
    ).scalar()
    assert rows[ids["parked"]]["parked_by_name"] == joey
    assert rows[ids["notice_parked"]]["parked_by_name"] == cs_name


def test_AC_SN_30_unknown_reserve_status_is_422(sn):
    client, _world = sn
    assert client.get(HEADERS_URL, params={"reserve": "bogus"}).status_code == 422


def test_AC_SN_31_summary_counts(sn, monkeypatch):
    client, world = sn
    _register()
    _captured_dispatches(monkeypatch)
    _status_world(client, world)

    summary = _ok(client.get(SUMMARY_URL))
    assert summary == {
        "parked": 1,
        "needs_reserve": 2,
        "notice_parked": 2,
        "reserved": 1,
        "my_parked_requests": 1,
        "my_parked_notices": 0,
    }
    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        theirs = _ok(cs.get(SUMMARY_URL))
    assert theirs["my_parked_notices"] == 2
    assert theirs["my_parked_requests"] == 0


# --------------------------------------------------------------------------------- #
# Fix round (reviewer): shape follows DISTINCT order inquiries; staged recipients    #
# --------------------------------------------------------------------------------- #
#
# NAMED ASSUMPTION (fix round): for B1(a)/(b) the preview carries one email per REQUEST when
# the To person's group holds a single distinct OI, so two requests on one OI are two emails;
# `items_sent` counts distinct OIs.


def _two_requests_one_oi(client, world, raiser_id):
    inquiry = _new_oi(world, raiser_id)
    row_a, row_b = _oi_row(world, inquiry), _oi_row(world, inquiry)
    _request(client, world, inquiry, [(row_a, "50")], park=True)
    _request(client, world, inquiry, [(row_b, "50")], park=True)
    return inquiry, row_a, row_b


def test_fix_B1a_two_parked_requests_one_oi_keep_one_oi_shape(sn, monkeypatch):
    client, world = sn
    _automations(world, strict=True)
    _register()
    calls = _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    inquiry, _a, _b = _two_requests_one_oi(client, world, eling)

    emails = _ok(client.post(REQ_PREVIEW))["emails"]
    assert len(emails) == 2, [e["subject"] for e in emails]
    subjects = sorted(e["subject"] for e in emails)
    assert subjects[0].startswith(f"Reserve request: {inquiry.inquiry_no} #1"), subjects
    assert subjects[1].startswith(f"Reserve request: {inquiry.inquiry_no} #2"), subjects
    assert not any("Reserve requests:" in s for s in subjects), subjects
    sent = _ok(client.post(REQ_SEND))
    assert sent["emails_sent"] == 2 and sent["items_sent"] == 1, sent
    assert len(_requested_calls(calls)) == 2


def test_fix_B1b_one_commit_two_requests_one_oi_keeps_one_oi_notice(sn, monkeypatch):
    client, world = sn
    _automations(world, strict=True)
    _register()
    calls = _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    inquiry = _new_oi(world, eling)
    row_a, row_b = _oi_row(world, inquiry), _oi_row(world, inquiry)
    _request(client, world, inquiry, [(row_a, "50")], park=False)
    _request(client, world, inquiry, [(row_b, "50")], park=False)
    _commit(
        world, inquiry,
        reserves=[_reserve(world, row_a, "50"), _reserve(world, row_b, "50")],
        park_notice=True,
    )
    calls.clear()
    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        emails = _ok(cs.post(NOTE_PREVIEW))["emails"]
        subjects = sorted(e["subject"] for e in emails)
        assert len(emails) == 2, subjects
        assert subjects[0].startswith(f"Reserved: {inquiry.inquiry_no} #1"), subjects
        assert subjects[1].startswith(f"Reserved: {inquiry.inquiry_no} #2"), subjects
        assert not any("order inquiries" in s for s in subjects), subjects
        sent = _ok(cs.post(NOTE_SEND))
    world.db.commit()
    assert sent["emails_sent"] == 2
    assert len(_reserved_calls(calls)) == 2


def test_fix_B1c_two_oi_group_with_three_requests_is_bulk_by_oi_count(sn, monkeypatch):
    client, world = sn
    _automations(world, strict=True)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    _two_requests_one_oi(client, world, eling)
    _parked_request(client, world, eling)

    emails = _ok(client.post(REQ_PREVIEW))["emails"]
    assert len(emails) == 1, [e["subject"] for e in emails]
    assert emails[0]["subject"] == (
        f"Reserve requests: 2 order inquiries ({_name(world, world.requester)})"
    )
    assert emails[0]["inquiry_count"] == 2
    assert "2 order inquiries need your reserve" in emails[0]["body_html"]


def test_fix_S2_recipients_resolve_staged_rows_on_a_reserved_request(sn, monkeypatch):
    client, world = sn
    _automations(world)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    inquiry, row, request_id = _sent_request(client, world, eling)
    _commit(world, inquiry, reserves=[_reserve(world, row, "50")])
    assert _req(world, request_id).state == "reserved"
    other, other_row, _o = _sent_request(client, world, eling)

    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        body = _ok(cs.get(RECIPIENTS_URL(inquiry.id), params={"row_ids": row.id}))
        assert body["to"] is not None, "the staged (answered) row must resolve its request"
        assert body["to"]["name"] == _name(world, world.requester)
        assert _names(body["cc"]) == [f"{MARKER} Eling"]
        assert cs.get(RECIPIENTS_URL(inquiry.id), params={"row_ids": _uid()}).status_code == 404
        assert (
            cs.get(RECIPIENTS_URL(inquiry.id), params={"row_ids": other_row.id}).status_code == 404
        ), "a row of another OI is 404"
        unknown_oi = cs.get(RECIPIENTS_URL(_uid()))
        foreign = cs.get(RECIPIENTS_URL(inquiry.id), params={"row_ids": other_row.id})
        assert foreign.json() == unknown_oi.json(), "same text as an unknown OI"


# --------------------------------------------------------------------------------- #
# Follow-up: inquiry_total + split items resolve their own recipients                #
# --------------------------------------------------------------------------------- #


def test_fix_S1_preview_inquiry_total_is_distinct_ois(sn, monkeypatch):
    """Two parked requests on ONE OI split into two emails (each inquiry_count 1); the
    preview's `inquiry_total` counts DISTINCT OIs across all emails, not the sum."""
    client, world = sn
    _automations(world, strict=True)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    niki = _user(world.db, f"{MARKER} Niki")
    # Dev config: To = the OI's raiser. Eling's group is one OI (split), Niki's is one OI.
    _two_requests_one_oi(client, world, eling)
    _parked_request(client, world, niki)

    body = _ok(client.post(REQ_PREVIEW))
    assert len(body["emails"]) == 3, [e["subject"] for e in body["emails"]]
    assert body.get("inquiry_total") == 2, body.get("inquiry_total")

    # Notices: one commit answers two requests on one OI, plus a second OI.
    inquiry = _new_oi(world, eling)
    row_a, row_b = _oi_row(world, inquiry), _oi_row(world, inquiry)
    _request(client, world, inquiry, [(row_a, "50")], park=False)
    _request(client, world, inquiry, [(row_b, "50")], park=False)
    _commit(
        world, inquiry,
        reserves=[_reserve(world, row_a, "50"), _reserve(world, row_b, "50")],
        park_notice=True,
    )
    other, other_row, _rid = _sent_request(client, world, niki)
    _commit(world, other, reserves=[_reserve(world, other_row, "50")], park_notice=True)
    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        notes = _ok(cs.post(NOTE_PREVIEW))
    assert len(notes["emails"]) == 1, [e["subject"] for e in notes["emails"]]
    assert notes.get("inquiry_total") == 2, notes.get("inquiry_total")


def test_fix_N1_split_items_resolve_their_own_recipients(sn, monkeypatch):
    """Request side cannot produce two requesters in one group (own items only, and the
    OI's raiser is the same for both). Notice side can, via the actor-last ordering:
    the reserved automation carries a FIXED user (Joey) and CS (actor) is himself a
    requester on the same OI. CS's request: recipients [Joey fixed, CS last] -> To Joey.
    Joey's request: To Joey. One group, then split. Joey's email must not Cc CS."""
    client, world = sn
    _automations(world, strict=True)
    _register()
    _captured_dispatches(monkeypatch)
    eling = _user(world.db, f"{MARKER} Eling")
    _set_automation(world, "order_inquiry_reserved", config={"user_ids": [world.requester]})
    inquiry = _new_oi(world, eling)
    row_joey, row_cs = _oi_row(world, inquiry), _oi_row(world, inquiry)
    _request(client, world, inquiry, [(row_joey, "50")], park=False)
    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        response = cs.post(
            REQUEST_URL(inquiry.id),
            json={"rows": [{"row_id": row_cs.id, "qty_requested": "50", "warehouse_id": world.site.id}]},
        )
        assert response.status_code == 201, response.text
    world.db.commit()
    _commit(
        world, inquiry,
        reserves=[_reserve(world, row_joey, "50"), _reserve(world, row_cs, "50")],
        park_notice=True,
    )
    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as cs:
        emails = _ok(cs.post(NOTE_PREVIEW))["emails"]
    cs_name = _name(world, world.reserver)
    assert len(emails) == 2, [(e["to"], e["cc"]) for e in emails]
    with_cs = [e for e in emails if cs_name in _names(e["cc"])]
    assert len(with_cs) == 1, f"only CS's own request may Cc CS: {[_names(e['cc']) for e in emails]}"
