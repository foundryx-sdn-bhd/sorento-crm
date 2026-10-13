"""PLANNING-WHY R3: the admin-only SO History read.

Contract (documentation/plans/scm/planning-why-12oct-acceptance-criteria.md):
AC-W10 GET /project-sales/fulfilment-planning/history?pso_ids=<id>[,<id>...] (max 50), admin
       only (403 `history_admin_only`), answers {"events": [...]} newest first; each event
       {id, kind, at, actor, pso_id, so_no, lines, ...kind fields}. Kinds: confirm_refused,
       revision_confirmed (lines + reissued_unchanged), autocount_update (counts + lines),
       email_sent (subject, status, sent_at, recipients, body_text, lines, counts),
       oi_cancelled.
AC-W11 a revision cancel with no CANCEL audit row (before the release) takes who/when from the
       revision its note names and the cause `undecided_change` only when an undecided pending
       planning change for that line existed before the revision confirmed, else `not_recorded`.

Seeded SO422492 shape: line 592 ORDER 25 at revision 1, a pending qty_down 25 -> 22 batch row
(decision NULL) created after revision 1, revision 2 cancels the 25 ("Superseded by revision
2"). Counts are asserted as flat keys (kind names and `confirmed` / `not_decided` / `replaced`).
Real-DB fixture chain of tests/test_planning_change_apply_on_board.py.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from app.models.audit import AuditLog
from app.models.email_outbox import EmailOutbox
from app.models.notification import Notification
from app.models.planning_change import PlanningChangeBatch, PlanningChangeRow
from app.models.project_so import (
    IV_ORDER,
    OrderInquiry,
    OrderInquiryRow,
    SOSupplyDecision,
)
from app.services.scm.outstanding_diff import QTY_CHANGED

from .test_planning_change_apply_on_board import (  # noqa: F401 - fixtures
    BASE,
    WAS_1,
    _build,
    _change,
    _core_line,
    _core_so,
    _project_line,
    _project_so,
    _uid,
    _user,
    api,
    world,
)

URL = f"{BASE}/fulfilment-planning/history"
ITEM = "SRTWC8840-SC"

T_REV1 = datetime(2026, 10, 1, 9, 0, 0)
T_BATCH = datetime(2026, 10, 2, 10, 0, 0)
T_REV2 = datetime(2026, 10, 3, 11, 0, 0)
T_EMAIL = datetime(2026, 10, 3, 11, 0, 30)
T_MANUAL = datetime(2026, 10, 3, 15, 0, 0)
T_REFUSED = datetime(2026, 10, 4, 8, 30, 0)

EMAIL_BODY = (
    "Order inquiry OI for SO422492\n"
    "BRW-NTC | SRTWC8840-SC | 25 | 0 | 09/11/2026 | CANCEL ALL\n"
    "Regards"
)


def _as_role(monkeypatch, *slugs):
    from app.services.user_service import UserPermissionService

    monkeypatch.setattr(
        UserPermissionService, "get_user_role_slugs", lambda self, user_id: set(slugs)
    )


def _oi_row(db, inquiry, line, *, qty, created, state, note=None, item=ITEM, decision=None):
    row = OrderInquiryRow(
        order_inquiry_id=inquiry.id, so_line_id=line.id, item_code=item,
        qty=Decimal(str(qty)), verb=IV_ORDER, state=state, note=note,
        company_id=inquiry.company_id, created_at=created, supply_decision_id=decision,
    )
    db.add(row)
    db.flush()
    return row


def _seed(api):
    """One order, the whole SO422492-shaped story. Returns a dict of everything made."""
    client, world = api
    db = world.db
    cs_user = _user(db, f"ZZT History Cs {_uid()[:6]}")
    planner = _user(db, f"ZZT History Planner {_uid()[:6]}")
    cs_name = db.query(__import__("app.models.user", fromlist=["User"]).User).get(cs_user).name

    core_so = _core_so(db, world.company_id)
    cores = [
        _core_line(db, core_so, world.product, world.own_wh, qty_ordered="25",
                   required_date=WAS_1)
        for _ in range(4)
    ]
    order = _project_so(db, world.project, so_id=core_so.id,
                        autocount_doc_no=core_so.so_number)
    l592 = _project_line(db, order, line_no=592, product=world.product, core_line=cores[0])
    l600 = _project_line(db, order, line_no=600, product=world.product, core_line=cores[1])
    l700 = _project_line(db, order, line_no=700, product=world.product, core_line=cores[2])
    l800 = _project_line(db, order, line_no=800, product=world.product, core_line=cores[3])
    inquiry = OrderInquiry(
        id=_uid(), company_id=world.company_id, inquiry_no=f"ZZT{_uid()[:8]}",
        project_sales_order_id=order.id, state="raised", raised_by=world.actor,
    )
    db.add(inquiry)
    db.flush()

    dec1 = SOSupplyDecision(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        revision_no=1, state="superseded", line_snapshots=[], confirmed_by=planner,
        confirmed_at=T_REV1, created_at=T_REV1,
    )
    db.add(dec1)
    db.flush()
    dec2 = SOSupplyDecision(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        revision_no=2, state="active", line_snapshots=[], confirmed_by=cs_user,
        confirmed_at=T_REV2, created_at=T_REV2, supersedes_id=dec1.id,
    )
    db.add(dec2)
    db.flush()

    # Revision 1 raised four rows; revision 2 cancelled three of them (note only, no audit
    # row: the shape every cancel had before this release) and re-issued line 600 unchanged.
    r592 = _oi_row(db, inquiry, l592, qty=25, created=T_REV1, state="cancelled",
                   note="Superseded by revision 2", decision=dec1.id)
    r600_old = _oi_row(db, inquiry, l600, qty=10, created=T_REV1, state="cancelled",
                       note="Superseded by revision 2", decision=dec1.id, item="SRTWT2201")
    r700 = _oi_row(db, inquiry, l700, qty=4, created=T_REV1, state="cancelled",
                   note="Superseded by revision 2", decision=dec1.id, item="SRTWCX8840-S-RL")
    r800 = _oi_row(db, inquiry, l800, qty=3, created=T_REV1, state="cancelled",
                   note="Cancelled by purchasing", decision=dec1.id, item="SRTWCY8840")
    r600_new = _oi_row(db, inquiry, l600, qty=10, created=T_REV2, state="raised",
                       decision=dec2.id, item="SRTWT2201")

    # The pending AutoCount change on line 592, created between the two revisions.
    cores[0].qty_ordered = Decimal("22")
    l592.qty = Decimal("22")
    # A batch is only born for a line that carries a live inquiry row: borrow one for the build.
    r592.state = "raised"
    db.commit()
    batch = _build(
        world,
        [_change(QTY_CHANGED, cores[0], so_number=core_so.so_number, old_date=WAS_1,
                 new_date=WAS_1, old_qty="25", new_qty="22")],
        core_so, [str(cores[0].id)],
    )
    r592.state = "cancelled"
    batch.created_at = T_BATCH
    batch.created_by = planner
    change_row = db.query(PlanningChangeRow).filter(PlanningChangeRow.batch_id == batch.id).one()
    change_row.created_at = T_BATCH
    change_row.item_code = ITEM
    assert change_row.decision is None and change_row.applied_state == "pending"

    # Purchasing's email, tied to the inquiry through its notification.
    notification = Notification(
        id=_uid(), user_id=world.actor, type="order_inquiry_handover",
        title="OI: SO422492", body=EMAIL_BODY,
        data={"source_kind": "order_inquiry_handover", "source_id": inquiry.id},
    )
    db.add(notification)
    db.flush()
    email = EmailOutbox(
        id=_uid(), event_key="notification_delivery",
        recipient_email="buyer1@example.test",
        recipients_json={"to": ["buyer1@example.test"], "cc": ["buyer2@example.test"], "bcc": []},
        subject="OI: SO422492", body_text=EMAIL_BODY, status="sent", sent_at=T_EMAIL,
        metadata_json={"notification_id": notification.id}, created_at=T_EMAIL,
    )
    db.add(email)

    # A person's own cancel (an audit row naming no revision), and a refused press.
    manual = AuditLog(
        id=_uid(), entity_type="order_inquiry_rows", entity_id=str(r800.id), action="EVENT", event="OI_ROW_CANCELLED",
        user_id=planner, changed_at=T_MANUAL, root_entity_type="project_sales_orders",
        root_entity_id=str(order.id),
        new_values={"revision_no": None, "supply_decision_id": None, "cause": "manual",
                    "qty": 3, "item_code": "SRTWCY8840", "so_line_no": 800},
    )
    refused = AuditLog(
        id=_uid(), entity_type="project_sales_orders", entity_id=str(order.id),
        action="EVENT", event="CONFIRM_REFUSED", user_id=cs_user, changed_at=T_REFUSED,
        root_entity_type="project_sales_orders", root_entity_id=str(order.id),
        reason="Nothing on this planning change could be confirmed.",
        new_values={"preview": True, "batch_id": str(batch.id), "lines": [{
            "so_line_no": 592, "item_code": ITEM, "reason_code": "not_decided",
            "reason": "Not decided", "change": "Qty 25 -> 22"}]},
    )
    db.add_all([manual, refused])
    db.commit()
    return {"client": client, "world": world, "order": order, "core_so": core_so,
            "cs_name": cs_name, "cs_user": cs_user, "planner": planner, "batch": batch,
            "inquiry": inquiry, "email": email, "dec2": dec2, "rows": [r592, r600_old, r700, r800, r600_new]}


def _get(fx, *ids):
    ids = ids or (str(fx["order"].id),)
    return fx["client"].get(URL, params={"pso_ids": ",".join(ids)})


def _by_kind(events, kind):
    return [e for e in events if e["kind"] == kind]


# --------------------------------------------------------------------------- gate


def test_a_non_admin_is_refused_with_history_admin_only(api, monkeypatch):
    _as_role(monkeypatch, "user")
    fx = _seed(api)
    response = _get(fx)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "history_admin_only"


def test_more_than_fifty_orders_is_a_422(api, monkeypatch):
    _as_role(monkeypatch, "admin")
    fx = _seed(api)
    response = fx["client"].get(URL, params={"pso_ids": ",".join(_uid() for _ in range(51))})
    assert response.status_code == 422, response.text


# --------------------------------------------------------------------------- the read


def test_admin_gets_every_kind_newest_first(api, monkeypatch):
    _as_role(monkeypatch, "admin")
    fx = _seed(api)

    response = _get(fx)

    assert response.status_code == 200, response.text
    events = response.json()["events"]
    assert [e["kind"] for e in events] == [
        "confirm_refused", "oi_cancelled", "email_sent", "revision_confirmed",
        "autocount_update", "revision_confirmed",
    ]
    assert [e["at"] for e in events] == sorted((e["at"] for e in events), reverse=True)
    for event in events:
        assert event["pso_id"] == str(fx["order"].id)
        assert event["so_no"] == fx["core_so"].so_number
        assert isinstance(event["lines"], list)
        assert event["id"]


def test_a_revision_cancel_with_no_audit_row_derives_who_when_and_cause(api, monkeypatch):
    _as_role(monkeypatch, "admin")
    fx = _seed(api)
    events = _get(fx).json()["events"]

    rev2 = [e for e in _by_kind(events, "revision_confirmed") if e["revision_no"] == 2][0]

    assert rev2["actor"] == fx["cs_name"]
    assert rev2["at"].startswith("2026-10-03T11:00:00")
    cancelled = {l["so_line_no"]: l for l in rev2["lines"] if l["action"] == "cancelled"}
    # Line 592 had an undecided pending change before revision 2 confirmed: AC-W11.
    assert cancelled[592]["cause"] == "undecided_change"
    assert cancelled[592]["item_code"] == ITEM
    assert cancelled[592]["qty"] == 25
    # Line 700 had none: the honest answer is that nothing was recorded.
    assert cancelled[700]["cause"] == "not_recorded"
    # The cancel of line 800 was a person's, not this revision's.
    assert 800 not in cancelled


def test_a_cancel_re_issued_unchanged_is_counted_not_listed(api, monkeypatch):
    _as_role(monkeypatch, "admin")
    fx = _seed(api)
    events = _get(fx).json()["events"]

    rev2 = [e for e in _by_kind(events, "revision_confirmed") if e["revision_no"] == 2][0]

    assert rev2["reissued_unchanged"] == 1
    assert 600 not in {l["so_line_no"] for l in rev2["lines"]}


def test_revision_one_lists_the_rows_it_ordered(api, monkeypatch):
    _as_role(monkeypatch, "admin")
    fx = _seed(api)
    events = _get(fx).json()["events"]

    rev1 = [e for e in _by_kind(events, "revision_confirmed") if e["revision_no"] == 1][0]

    ordered = {l["so_line_no"]: l for l in rev1["lines"] if l["action"] == "ordered"}
    assert ordered[592]["qty"] == 25 and ordered[592]["item_code"] == ITEM
    assert {600, 700, 800} <= set(ordered)
    assert rev1["reissued_unchanged"] == 0


def test_a_recorded_cancel_names_its_own_cause_in_the_revision(api, monkeypatch):
    _as_role(monkeypatch, "admin")
    fx = _seed(api)
    db = fx["world"].db
    db.add(AuditLog(
        id=_uid(), entity_type="order_inquiry_rows", entity_id=str(fx["rows"][2].id),
        action="EVENT", event="OI_ROW_CANCELLED", user_id=fx["cs_user"], changed_at=T_REV2,
        root_entity_type="project_sales_orders", root_entity_id=str(fx["order"].id),
        new_values={"revision_no": 2, "supply_decision_id": str(fx["dec2"].id),
                    "cause": "superseded", "qty": 4, "item_code": "SRTWCX8840-S-RL",
                    "so_line_no": 700},
    ))
    db.commit()

    events = _get(fx).json()["events"]
    rev2 = [e for e in _by_kind(events, "revision_confirmed") if e["revision_no"] == 2][0]

    line_700 = [l for l in rev2["lines"] if l["so_line_no"] == 700]
    assert len(line_700) == 1, "listed once, from the audit row, not twice"
    assert line_700[0]["cause"] == "superseded"
    assert not _by_kind(events, "oi_cancelled")[1:], "a revision's cancel is not also a stray cancel"


def test_the_autocount_update_counts_kinds_and_states_now(api, monkeypatch):
    _as_role(monkeypatch, "admin")
    fx = _seed(api)
    events = _get(fx).json()["events"]

    update = _by_kind(events, "autocount_update")[0]

    assert update["actor"] is not None
    assert update["at"].startswith("2026-10-02T10:00:00")
    assert update["counts"]["qty_down"] == 1
    assert update["counts"]["not_decided"] == 1
    assert update["counts"].get("confirmed", 0) == 0
    assert update["lines"] == [{
        "so_line_no": 592, "item_code": ITEM, "kind": "qty_down",
        "change": "Qty 25 -> 22", "state": "not_decided",
    }]


def test_the_email_event_reads_the_stored_email_and_parses_its_lines(api, monkeypatch):
    _as_role(monkeypatch, "admin")
    fx = _seed(api)
    events = _get(fx).json()["events"]

    mail = _by_kind(events, "email_sent")[0]

    assert mail["subject"] == "OI: SO422492"
    assert mail["status"] == "sent"
    assert mail["sent_at"].startswith("2026-10-03T11:00:30")
    assert set(mail["recipients"]) == {"buyer1@example.test", "buyer2@example.test"}
    assert mail["body_text"] == EMAIL_BODY
    assert mail["lines"] == [{"item_code": ITEM, "remark": "CANCEL ALL"}]
    assert mail["counts"] == {"cancel": 1, "order": 0, "order_back": 0}


def test_an_email_of_another_inquiry_is_not_shown(api, monkeypatch):
    _as_role(monkeypatch, "admin")
    fx = _seed(api)
    db = fx["world"].db
    stranger = Notification(
        id=_uid(), user_id=fx["world"].actor, type="order_inquiry_handover", title="x",
        data={"source_kind": "order_inquiry_handover", "source_id": _uid()},
    )
    db.add(stranger)
    db.flush()
    db.add(EmailOutbox(
        id=_uid(), event_key="notification_delivery", recipient_email="x@example.test",
        subject="OI: SOELSE", body_text="a | b | 1 | 0 | d | ORDER", status="sent",
        sent_at=T_EMAIL, metadata_json={"notification_id": stranger.id},
    ))
    db.commit()

    events = _get(fx).json()["events"]

    assert [e["subject"] for e in _by_kind(events, "email_sent")] == ["OI: SO422492"]


def test_a_refused_press_and_a_manual_cancel_come_from_their_audit_rows(api, monkeypatch):
    _as_role(monkeypatch, "admin")
    fx = _seed(api)
    events = _get(fx).json()["events"]

    refused = _by_kind(events, "confirm_refused")[0]
    assert refused["actor"] == fx["cs_name"]
    assert refused["preview"] is True
    assert refused["lines"] == [{
        "so_line_no": 592, "item_code": ITEM, "reason_code": "not_decided",
        "reason": "Not decided", "change": "Qty 25 -> 22",
    }]

    cancelled = _by_kind(events, "oi_cancelled")
    assert len(cancelled) == 1
    assert len(cancelled[0]["lines"]) == 1
    assert cancelled[0]["lines"][0]["so_line_no"] == 800
    assert cancelled[0]["lines"][0]["cause"] == "manual"


def test_only_the_named_orders_are_read(api, monkeypatch):
    _as_role(monkeypatch, "admin")
    fx = _seed(api)
    other = _seed(api)

    mine = _get(fx).json()["events"]
    both = _get(fx, str(fx["order"].id), str(other["order"].id)).json()["events"]

    assert {e["pso_id"] for e in mine} == {str(fx["order"].id)}
    assert {e["pso_id"] for e in both} == {str(fx["order"].id), str(other["order"].id)}


# --------------------------------------------------------------------------- email layouts
# Real handover emails come in three header shapes; the header row is never a line and the
# item code is read from the ITEM CODE column of whichever header is present.

_TRAILER = "\nRaised by Planner One on 12/10/2026.\nOpen: https://crm.example.test/oi/1\n"
_HEAD = (
    "ORDER, ORDER BACK, CANCEL BALANCE\n\n"
    "SO900001 - 22/09/2026 - (Example Customer) - SITE A\n"
)
OLD_LAYOUT = (
    _HEAD
    + "SO DATE | S/O NO | ITEM CODE | QTY | DELIVERY DATE | REMARK\n"
    "24/09/2026 | SO900001 | SRTWC8840-SC | 25 | 09/11/2026 | ORDER\n"
    "24/09/2026 | SO900001 | SRTWT2200 | 140 | 09/11/2026 | ORDER\n"
    + _TRAILER
)
OLD_LAYOUT_WITH_LOCATION = (
    _HEAD
    + "SO DATE | S/O NO | LOCATION | ITEM CODE | QTY | DELIVERY DATE | REMARK\n"
    "24/09/2026 | SO900001 | BRW-BB | SRTWC8840-SC | 25 | 09/11/2026 | ORDER\n"
    "24/09/2026 | SO900001 | BRW-NTC | SRTWT2200 | 140 | 09/11/2026 | CANCEL BALANCE\n"
    + _TRAILER
)
NEW_LAYOUT = (
    _HEAD
    + "LOCATION | ITEM CODE | QTY | NEW QTY | DELIVERY | REMARK\n"
    "BRW-BB | SRTWT2219 | 6 |  | 09/11/2026 | ORDER BACK\n"
    "BRW-NTC | SRTWC8840-SC | 25 | 0 | 09/11/2026 | CANCEL ALL\n"
    + _TRAILER
)

_LAYOUTS = [
    (
        "old",
        OLD_LAYOUT,
        [{"item_code": "SRTWC8840-SC", "remark": "ORDER"},
         {"item_code": "SRTWT2200", "remark": "ORDER"}],
        {"cancel": 0, "order": 2, "order_back": 0},
    ),
    (
        "old_with_location",
        OLD_LAYOUT_WITH_LOCATION,
        [{"item_code": "SRTWC8840-SC", "remark": "ORDER"},
         {"item_code": "SRTWT2200", "remark": "CANCEL BALANCE"}],
        {"cancel": 1, "order": 1, "order_back": 0},
    ),
    (
        "new",
        NEW_LAYOUT,
        [{"item_code": "SRTWT2219", "remark": "ORDER BACK"},
         {"item_code": "SRTWC8840-SC", "remark": "CANCEL ALL"}],
        {"cancel": 1, "order": 0, "order_back": 1},
    ),
]


@pytest.mark.parametrize("name,body,lines,counts", _LAYOUTS, ids=[x[0] for x in _LAYOUTS])
def test_the_email_parser_reads_each_header_layout(name, body, lines, counts):
    from app.services.planning_history_service import _parse_lines

    got_lines, got_counts = _parse_lines(body)

    assert got_lines == lines
    assert got_counts == counts


@pytest.mark.parametrize("name,body,lines,counts", _LAYOUTS, ids=[x[0] for x in _LAYOUTS])
def test_the_history_route_parses_each_header_layout(api, monkeypatch, name, body, lines, counts):
    _as_role(monkeypatch, "admin")
    fx = _seed(api)
    fx["world"].db.query(EmailOutbox).filter(EmailOutbox.id == fx["email"].id).update(
        {"body_text": body}
    )
    fx["world"].db.commit()

    mail = _by_kind(_get(fx).json()["events"], "email_sent")[0]

    assert mail["lines"] == lines
    assert mail["counts"] == counts
