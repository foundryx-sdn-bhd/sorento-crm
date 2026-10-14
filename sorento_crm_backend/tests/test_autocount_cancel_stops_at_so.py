"""CANCEL-STOP-AT-SO: an AutoCount Cancel SO's effects stop at the SO (owner ruling 14 Oct).

Plan: documentation/plans/autocount/PLAN-autocount-cancel-crm-13oct.md (R5, as revised) and
documentation/plans/autocount/cancel-stop-at-so-acceptance-criteria.md.

A full cancel still writes `qty_cancelled` and the line's `cancelled` status, but the line's open
order inquiry rows are not retired, not flagged, no handover line is queued and no email is
written: CS handles the line in Fulfilment Planning. Same for the SO push relink path.

Real rows, no monkeypatched retire: the OI row is seeded on the project mirror of the pushed SO
line and read back after the cancel.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func

from app.models.audit import AuditLog
from app.models.email_outbox import EmailOutbox
from app.models.project_so import (
    ACK_ACKNOWLEDGED,
    INQUIRY_ACTIONED,
    INQUIRY_RAISED,
    IV_ORDER,
    OrderInquiry,
    OrderInquiryRow,
    ProjectSalesOrder,
    ProjectSalesOrderLine,
)

from tests.test_order_inquiry_handover_automation import _captured_dispatches
from tests.test_autocount_cancel_effects import _code_of_ref, _line, _push_so, _push_xs
from tests.test_ingest_documents import env  # noqa: F401 - env is a fixture


def _uid() -> str:
    return str(uuid.uuid4())


def _oi_row_on(env, so_number: str, *, state: str = INQUIRY_RAISED) -> str:
    """An acknowledged ORDER row on the project mirror of the SO's one line."""
    db = env.db
    core = _line(env, so_number)
    mirror = (
        db.query(ProjectSalesOrderLine)
        .filter(ProjectSalesOrderLine.core_sales_order_line_id == str(core.id))
        .one_or_none()
    )
    if mirror is None:
        pso = ProjectSalesOrder(id=_uid(), company_id=str(core.company_id),
                                so_id=str(core.sales_order_id),
                                provisional_ref=f"ZZT-{so_number}", status="adopted")
        db.add(pso)
        db.flush()
        mirror = ProjectSalesOrderLine(id=_uid(), company_id=str(core.company_id),
                                       project_sales_order_id=pso.id,
                                       core_sales_order_line_id=str(core.id),
                                       line_no=1, qty=Decimal("10"))
        db.add(mirror)
        db.flush()
    inquiry = OrderInquiry(id=_uid(), company_id=str(core.company_id),
                           project_sales_order_id=mirror.project_sales_order_id,
                           state=INQUIRY_RAISED)
    db.add(inquiry)
    db.flush()
    row = OrderInquiryRow(id=_uid(), company_id=str(core.company_id),
                          order_inquiry_id=inquiry.id, so_line_id=mirror.id,
                          item_code="ZZT-ITEM", qty=Decimal("10"), verb=IV_ORDER,
                          state=state, ack_state=ACK_ACKNOWLEDGED, note="CS note")
    db.add(row)
    db.flush()
    return str(row.id)


def _snapshot(env, row_id: str) -> tuple:
    env.db.expire_all()
    row = env.db.get(OrderInquiryRow, row_id)
    return (row.state, row.ack_state, row.changed_at, row.note, row.qty)


def _cancel_audits(env, row_id: str) -> int:
    return env.db.query(func.count(AuditLog.id)).filter(
        AuditLog.entity_type == "project_order_inquiry_rows",
        AuditLog.entity_id == row_id,
        AuditLog.event == "OI_ROW_CANCELLED",
    ).scalar()


def _capture_dispatch(monkeypatch) -> list:
    """Every post-commit purchasing dispatch (the handover email, changed-with-links)."""
    calls = _captured_dispatches(monkeypatch)
    _register(None)  # the post-commit dispatch the app registers at startup (review B2)

    class _OrderInquiryOnly(list):
        def __eq__(self, other):
            return [c for c in calls if "inquiry" in str(c["trigger_type"])] == other

    return _OrderInquiryOnly()


def test_full_cancel_stops_at_the_so_line(env, monkeypatch):
    """AC-1/2/3: line cancelled exactly as before; the open OI row is untouched; no handover
    queued, nothing dispatched, no email outbox row."""
    dispatched = _capture_dispatch(monkeypatch)
    _push_so(env, "ZZ-STOP-SO1", ordered=10, delivered=10)
    row_id = _oi_row_on(env, "ZZ-STOP-SO1")
    before = _snapshot(env, row_id)
    outbox_before = env.db.query(func.count(EmailOutbox.id)).scalar()

    _push_xs(env, "ZZ-STOP-SO1", 10)

    line = _line(env, "ZZ-STOP-SO1")
    assert line.line_status == "cancelled"
    assert line.qty_cancelled == Decimal("10")
    assert line.cancelled_by_cancel_doc is True
    assert _snapshot(env, row_id) == before
    assert _cancel_audits(env, row_id) == 0
    assert dispatched == []
    assert env.db.query(func.count(EmailOutbox.id)).scalar() == outbox_before


def test_full_cancel_leaves_an_actioned_row_unflagged(env, monkeypatch):
    """AC-2: an acknowledged actioned row is not sent back to To confirm either."""
    dispatched = _capture_dispatch(monkeypatch)
    _push_so(env, "ZZ-STOP-SO2", ordered=10, delivered=10)
    row_id = _oi_row_on(env, "ZZ-STOP-SO2", state=INQUIRY_ACTIONED)
    before = _snapshot(env, row_id)

    _push_xs(env, "ZZ-STOP-SO2", 10)

    assert _line(env, "ZZ-STOP-SO2").line_status == "cancelled"
    assert _snapshot(env, row_id) == before
    assert dispatched == []


def test_relink_path_stops_at_the_so_line(env, monkeypatch):
    """AC-4: the cancel arrives before its SO; the SO push links it and cancels the line, and
    the OI row is still untouched."""
    dispatched = _capture_dispatch(monkeypatch)
    _push_so(env, "ZZ-STOP-SO3", ordered=10, delivered=4)
    row_id = _oi_row_on(env, "ZZ-STOP-SO3")
    _push_xs(env, "ZZ-STOP-SO3", 4)
    assert _line(env, "ZZ-STOP-SO3").line_status == "open"
    before = _snapshot(env, row_id)

    # AutoCount lowers ordered onto the cancel: the relink makes the line fully cancelled.
    _push_so(env, "ZZ-STOP-SO3", ordered=4, delivered=4)

    line = _line(env, "ZZ-STOP-SO3")
    assert line.line_status == "cancelled"
    assert _snapshot(env, row_id) == before
    assert _cancel_audits(env, row_id) == 0
    assert dispatched == []


# ======================================================================= Fulfilment Planning
from datetime import date as _date  # noqa: E402

from app.models.autocount_cancel import (  # noqa: E402
    KIND_CANCEL_SO,
    MATCH_MATCHED,
    AutocountCancelDoc,
    AutocountCancelLine,
)

from tests.test_oi_cancelled_line_used_confirm import _inquiry, _oi_row  # noqa: E402
from tests.test_so_cancel_line import _board, _cb_world  # noqa: E402
from tests.test_so_supply_confirmation import api  # noqa: E402,F401 - fixture


def _cancelled_by_autocount(world, core, *, doc_no=None):
    """What the cancel ingest leaves on a fully cancelled line (`_apply`), plus the matched
    Cancel SO line when `doc_no` is given."""
    db = world.db
    core.qty_cancelled = core.qty_ordered
    core.line_status = "cancelled"
    core.cancelled_by_cancel_doc = True
    if doc_no:
        doc = AutocountCancelDoc(company_id=world.company_id, kind=KIND_CANCEL_SO,
                                 source_book="db1", doc_key=abs(hash(doc_no)) % 10**9,
                                 doc_no=doc_no, doc_date=_date(2026, 10, 14))
        db.add(doc)
        db.flush()
        db.add(AutocountCancelLine(company_id=world.company_id, cancel_doc_id=doc.id,
                                   dtl_key=1, qty=core.qty_ordered, from_doc_type="SO",
                                   match_status=MATCH_MATCHED, match_reason="key",
                                   sales_order_line_id=str(core.id)))
    db.commit()


def _contribution(board, product):
    found = [c for c in board["contributions"] if c["item_code"] == product.product_code]
    return found[0] if found else None


def test_board_shows_a_cancel_doc_line_with_a_live_oi_row_for_cs(api):
    """AC-5: read-only, cancelled, committed (never retired by Confirm), naming AutoCount, the
    Cancel SO number and the live OI rows CS has to handle."""
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    inquiry = _inquiry(world.db, world.company_id, w["order"].id)
    _oi_row(world.db, world.company_id, inquiry.id, mirror_a.id, product_a.product_code, "36")
    _cancelled_by_autocount(world, core_a, doc_no="ZZXS-777")

    row = _contribution(_board(client, w["core_so"]), product_a)

    assert row is not None
    assert row["cancelled"] is True
    assert row["cancelled_at"]
    assert row["cancelled_by"] == "AutoCount"
    assert row["cancel_reason"] == "ZZXS-777 · 1 open order inquiry row"
    assert row["cancel_open_oi_rows"] == 1
    assert float(row["qty"]) == 0


def test_board_leaves_out_a_cancel_doc_line_with_no_live_oi_row(api):
    """AC-6: nothing for CS to handle (no row, or only cancelled rows): not on the board. A live
    row on ANOTHER line of the order does not count (the EXISTS is per line)."""
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    core_b, _mirror_b, product_b = w["b"]
    _open_core, open_line = w["open"]
    inquiry = _inquiry(world.db, world.company_id, w["order"].id)
    _oi_row(world.db, world.company_id, inquiry.id, mirror_a.id, product_a.product_code, "36",
            state="cancelled")
    _oi_row(world.db, world.company_id, inquiry.id, open_line.id, "ZZT-OTHER", "5")
    _cancelled_by_autocount(world, core_a)
    _cancelled_by_autocount(world, core_b)

    board = _board(client, w["core_so"])

    assert _contribution(board, product_a) is None
    assert _contribution(board, product_b) is None


def test_board_cs_cancelled_line_reads_as_before(api):
    """Guard: a line CS transferred on the board keeps its own who / why, 0 open rows."""
    from tests.test_so_cancel_line import REASON, _cancel_body, _confirm_all

    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    _confirm_all(client, w["order"], **_cancel_body(mirror_a))

    row = _contribution(_board(client, w["core_so"]), product_a)

    assert row["cancel_reason"] == REASON
    assert row["cancelled_by"] != "AutoCount"
    assert row["cancel_open_oi_rows"] == 0


# ======================================================================= the pending decision
# Owner ruling 14 Oct 23:20: "when SO is cancelled, we trigger the transfer to cancel SO action
# in fulfilment planning and let the CS confirm themselves". The sync saves the board's own
# Transfer to Cancel SO as a pending decision (a draft); nothing else moves until CS confirms.
from types import SimpleNamespace  # noqa: E402

from app.models.project_so import SOSupplyDecisionDraft  # noqa: E402
from app.services import project_line_draft_service  # noqa: E402
from app.services.autocount_cancel_ingest_service import Derived, run_cancel_hooks  # noqa: E402

from tests.test_order_inquiry_handover_automation import (  # noqa: E402
    TRIGGER,
    _handover_calls,
    _register,
)
from tests.test_so_cancel_line import _cancel_body, _confirm_all  # noqa: E402
from tests.test_so_supply_confirmation import BASE  # noqa: E402


def _draft(db, core_line_id):
    db.expire_all()
    return (
        db.query(SOSupplyDecisionDraft)
        .filter(SOSupplyDecisionDraft.core_line_id == str(core_line_id))
        .one_or_none()
    )


def test_full_cancel_with_a_live_oi_row_proposes_transfer_to_cancel_so(env, monkeypatch):
    """AC-12: the sync saves Transfer to Cancel SO for CS, naming the XS; the OI row is
    untouched and nothing is dispatched."""
    dispatched = _capture_dispatch(monkeypatch)
    _push_so(env, "ZZ-STOP-SO4", ordered=10, delivered=10)
    row_id = _oi_row_on(env, "ZZ-STOP-SO4")
    before = _snapshot(env, row_id)

    _push_xs(env, "ZZ-STOP-SO4", 10)

    line = _line(env, "ZZ-STOP-SO4")
    draft = _draft(env.db, line.id)
    assert draft is not None
    assert draft.decision["verdict"] == "cancelled"
    assert draft.decision["reason"] == "AutoCount Cancel SO ZZXS-950001"
    assert draft.decision["source"] == project_line_draft_service.AUTOCOUNT_CANCEL_SOURCE
    assert _snapshot(env, row_id) == before
    assert dispatched == []


def test_full_cancel_with_no_live_oi_row_proposes_nothing(env):
    """AC-13: nothing for CS to handle, so no pending decision."""
    _push_so(env, "ZZ-STOP-SO5", ordered=10, delivered=10)
    _push_xs(env, "ZZ-STOP-SO5", 10)
    assert _draft(env.db, _line(env, "ZZ-STOP-SO5").id) is None


def test_a_voided_cancel_withdraws_the_pending_decision(env):
    """AC-14: AutoCount voids the XS, the line reopens (R6) and the proposal it made goes."""
    _push_so(env, "ZZ-STOP-SO6", ordered=10, delivered=4)
    _oi_row_on(env, "ZZ-STOP-SO6")
    _push_xs(env, "ZZ-STOP-SO6", 10)
    line = _line(env, "ZZ-STOP-SO6")
    assert line.line_status == "cancelled" and _draft(env.db, line.id) is not None

    record = {"DocKey": 950001, "DocNo": "ZZXS-950001", "DocDate": "2026-10-01",
              "Cancelled": "T", "LastModified": "2026-10-02T10:00:00",
              "Details": [{"DtlKey": 951001, "Seq": 1,
                           "ItemCode": _code_of_ref(env, env.product_ref), "Qty": 10,
                           "FromDocType": "SO", "FromDocNo": "ZZ-STOP-SO6",
                           "FromDocDtlKey": 5001}]}
    res = env.client.post("/api/v1/external/ingest/cancel_sales_orders",
                          json={"companyCode": env.company_a_code, "book": "db1",
                                "records": [record]})
    assert res.status_code == 200, res.text

    line = _line(env, "ZZ-STOP-SO6")
    assert line.line_status == "open"
    assert _draft(env.db, line.id) is None


def _awaiting_world(api, monkeypatch=None):
    """An order whose line A an AutoCount Cancel SO cancelled, with a live OI row, and the
    hook run as the ingest runs it."""
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    inquiry = _inquiry(world.db, world.company_id, w["order"].id)
    row = _oi_row(world.db, world.company_id, inquiry.id, mirror_a.id,
                  product_a.product_code, "36")
    _cancelled_by_autocount(world, core_a, doc_no="ZZXS-777")
    run_cancel_hooks(world.db, SimpleNamespace(
        derived=Derived(newly_cancelled=[(str(core_a.id), "ZZXS-777")])), actor=None)
    return client, world, w, row


def test_board_shows_the_pending_transfer_on_the_awaiting_line(api):
    """AC-15: the board line carries the saved Transfer to Cancel SO, so it reads Saved."""
    client, world, w, _row = _awaiting_world(api)
    _core_a, _mirror_a, product_a = w["a"]

    row = _contribution(_board(client, w["core_so"]), product_a)

    assert row["cancel_open_oi_rows"] == 1
    assert row["draft"]["decision"]["verdict"] == "cancelled"
    assert row["draft"]["decision"]["reason"] == "AutoCount Cancel SO ZZXS-777"


def test_cs_confirm_runs_the_existing_transfer_to_cancel_so(api, monkeypatch):
    """AC-16: Confirm carries the pending decision through the SAME cancel the board's own
    Transfer to Cancel SO runs: the OI row is cancelled, purchasing gets the handover, the
    line records who / when / why, the draft is gone and the board reads it as CS's."""
    calls = _captured_dispatches(monkeypatch)
    _register(None)  # the post-commit dispatch the app registers at startup (review B2)
    client, world, w, oi_row = _awaiting_world(api)
    core_a, mirror_a, product_a = w["a"]

    result = _confirm_all(client, w["order"],
                          **_cancel_body(mirror_a, reason="AutoCount Cancel SO ZZXS-777"))

    assert result["ok"] is True, result
    world.db.expire_all()
    assert world.db.get(OrderInquiryRow, oi_row.id).state == "cancelled"
    assert _handover_calls(calls), calls
    core = world.db.get(type(core_a), core_a.id)
    assert core.cancelled_at is not None
    assert str(core.cancelled_by_id) == str(world.eling)
    assert core.cancel_reason == "AutoCount Cancel SO ZZXS-777"
    assert _draft(world.db, core_a.id) is None
    row = _contribution(_board(client, w["core_so"]), product_a)
    assert row["cancelled_by"] != "AutoCount"
    assert row["cancel_open_oi_rows"] == 0
    assert TRIGGER == "order_inquiry_handover"


def test_cs_can_dismiss_the_pending_transfer(api, monkeypatch):
    """AC-17: Undo on the board deletes the pending decision; nothing else moves."""
    calls = _captured_dispatches(monkeypatch)
    _register(None)  # the post-commit dispatch the app registers at startup (review B2)
    client, world, w, oi_row = _awaiting_world(api)
    core_a, _mirror_a, product_a = w["a"]
    key = _contribution(_board(client, w["core_so"]), product_a)["key"]

    removal = client.delete(f"{BASE}/fulfilment-planning/lines/{key}/draft")

    assert removal.status_code == 204, removal.text
    assert _draft(world.db, core_a.id) is None
    world.db.expire_all()
    assert world.db.get(OrderInquiryRow, oi_row.id).state == "raised"
    row = _contribution(_board(client, w["core_so"]), product_a)
    assert row is not None and row["draft"] is None
    assert _handover_calls(calls) == []


def test_cs_can_save_the_transfer_again_but_nothing_else(api):
    """AC-18: on the awaiting line the board accepts Transfer to Cancel SO (re-saved after a
    dismiss, or with CS's own reason) and refuses any supply decision."""
    client, world, w, _row = _awaiting_world(api)
    core_a, _mirror_a, product_a = w["a"]
    key = _contribution(_board(client, w["core_so"]), product_a)["key"]

    again = client.put(f"{BASE}/fulfilment-planning/lines/{key}/draft",
                       json={"decision": {"verdict": "cancelled", "reason": "CS: customer"}})
    assert again.status_code == 200, again.text
    assert _draft(world.db, core_a.id).decision["reason"] == "CS: customer"

    buy = client.put(f"{BASE}/fulfilment-planning/lines/{key}/draft",
                     json={"decision": {"verdict": "amended", "reserve_qty": "0",
                                        "reserve": [], "borrow": [], "buy_qty": "36",
                                        "reason": "x"}})
    assert buy.status_code == 409, buy.text


def test_an_order_autocount_cancelled_in_full_can_still_be_dismissed_and_saved(api):
    """Review B1: every line of the order cancelled in AutoCount leaves no demand line to
    number, and the pending transfer must still resolve for Undo and a re-save."""
    client, world, w, _row = _awaiting_world(api)
    core_a, _mirror_a, product_a = w["a"]
    _cancelled_by_autocount(world, w["b"][0])
    _cancelled_by_autocount(world, w["open"][0])
    row = _contribution(_board(client, w["core_so"]), product_a)
    assert row["draft"]["decision"]["verdict"] == "cancelled"

    removal = client.delete(f"{BASE}/fulfilment-planning/lines/{row['key']}/draft")
    assert removal.status_code == 204, removal.text
    again = client.put(
        f"{BASE}/fulfilment-planning/lines/{row['key']}/draft",
        json={"decision": {"verdict": "cancelled", "reason": "CS: whole order"}})
    assert again.status_code == 200, again.text
    assert _draft(world.db, core_a.id).decision["reason"] == "CS: whole order"


def test_a_saved_decision_never_carries_the_sync_marker(api):
    """Security nit: only the sync marks its proposal, so a reopen never withdraws CS's own
    saved transfer."""
    client, world, w, _row = _awaiting_world(api)
    core_a, _mirror_a, product_a = w["a"]
    key = _contribution(_board(client, w["core_so"]), product_a)["key"]
    saved = client.put(f"{BASE}/fulfilment-planning/lines/{key}/draft",
                       json={"decision": {"verdict": "cancelled", "reason": "CS",
                                          "source": "autocount_cancel"}})
    assert saved.status_code == 200, saved.text
    assert "source" not in _draft(world.db, core_a.id).decision
    project_line_draft_service.withdraw_autocount_cancel(world.db, [str(core_a.id)])
    assert _draft(world.db, core_a.id) is not None


def test_a_line_an_active_decision_covers_can_still_be_transferred(api, monkeypatch):
    """Review should-fix 3: AutoCount cancelled a confirmed line. It holds nothing any more, so
    the active decision naming it does not stop CS confirming the pending transfer."""
    from tests.test_fulfilment_line_draft_route import _covered_two_line_world

    calls = _captured_dispatches(monkeypatch)
    _register(None)  # the post-commit dispatch the app registers at startup (review B2)
    client, world, _core_so, order, core_1, line_1, _c2, _l2 = _covered_two_line_world(api)
    inquiry = (world.db.query(OrderInquiry)
               .filter(OrderInquiry.project_sales_order_id == order.id).one_or_none()
               or _inquiry(world.db, world.company_id, order.id))
    oi_row = _oi_row(world.db, world.company_id, inquiry.id, line_1.id,
                     world.product.product_code, "5")
    _cancelled_by_autocount(world, core_1, doc_no="ZZXS-778")
    run_cancel_hooks(world.db, SimpleNamespace(
        derived=Derived(newly_cancelled=[(str(core_1.id), "ZZXS-778")])), actor=None)
    assert _draft(world.db, core_1.id) is not None

    response = client.post(f"{BASE}/sales-orders/{order.id}/confirm",
                           json=_cancel_body(line_1, reason="AutoCount Cancel SO ZZXS-778"))

    assert response.status_code == 200, response.text
    world.db.expire_all()
    assert world.db.get(OrderInquiryRow, oi_row.id).state == "cancelled"
    assert world.db.get(type(core_1), core_1.id).cancelled_at is not None
    assert _handover_calls(calls)
