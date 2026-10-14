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
from tests.test_autocount_cancel_effects import _line, _push_so, _push_xs
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
    assert row["cancel_reason"] == "AutoCount ZZXS-777 · 1 open order inquiry row"
    assert row["cancel_open_oi_rows"] == 1
    assert float(row["qty"]) == 0


def test_board_leaves_out_a_cancel_doc_line_with_no_live_oi_row(api):
    """AC-6: nothing for CS to handle (no row, or only cancelled rows): not on the board."""
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    core_b, _mirror_b, product_b = w["b"]
    inquiry = _inquiry(world.db, world.company_id, w["order"].id)
    _oi_row(world.db, world.company_id, inquiry.id, mirror_a.id, product_a.product_code, "36",
            state="cancelled")
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
