"""AutoCount Cancel SO (XS) and Cancel PO (XP) on the ingest surface (AC-CANCEL-CRM, contract 2.8).

Plan: documentation/plans/autocount/PLAN-autocount-cancel-crm-13oct.md (rules R1-R7).

Owner rulings 13 Oct: cancelled qty is DERIVED on every sync (sum of the cancel-doc lines
matched to a line, live docs only); a line is cancelled only when fully cancelled; AutoCount is
truth, so a cancel line matches by its FromDocDtlKey. Without one it is decided with its group
by exact arithmetic only (owner rule 13 Oct): a Cancel SO's group is SO + item, whatever the
location (owner rule 14 Oct); a Cancel PO's is order + item + location. The group's one line,
or, when nothing was delivered and the cancels are every transfer, each line's own transfer.
Anything else is undetermined and names no line: no tie-break, no lowest-Seq fallback, no
guess. A dangling key stays unmatched. The result never depends on the order documents arrive in.

Reuses the DO / GRN suite's `env` (superadmin principal, products ZZAC-P1 / ZZAC-P2, warehouse
ZZAC-WH1, the default company). Every record here is fake.
"""
from __future__ import annotations

import copy
from decimal import Decimal

from sqlalchemy import select

from app.models.autocount_cancel import AutocountCancelDoc, AutocountCancelLine
from app.models.inventory import Warehouse
from app.models.order import SalesOrder, SalesOrderLine
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, SPOAllocation

from tests.test_ingest_autocount_do_grn import (  # noqa: F401 - env is a fixture
    _records,
    env,
)

XS_INGEST = "/api/v1/external/ingest/cancel_sales_orders"
XP_INGEST = "/api/v1/external/ingest/cancel_purchase_orders"
XS_DELETE = "/api/v1/external/ingest/cancel_sales_orders/deletions"


# ----------------------------------------------------------------------- builders
def _detail(dtl_key, item="ZZAC-P1", qty=4.0, *, from_no="ZZSO-C001", from_dtl=0,
            location="ZZAC-WH1", seq=None, from_type="SO"):
    return {"DtlKey": dtl_key, "Seq": seq if seq is not None else dtl_key % 1000,
            "ItemCode": item, "Location": location, "Qty": qty, "UOM": "PCS",
            "FromDocType": from_type, "FromDocNo": from_no, "FromDocDtlKey": from_dtl}


def _xs(doc_key=950001, doc_no="ZZXS-0001", details=None, *, cancelled="F",
        modified="2026-10-01T10:00:00.000"):
    return {"DocKey": doc_key, "DocNo": doc_no, "DocDate": "2026-10-01T00:00:00",
            "DocStatus": "V" if cancelled == "T" else "A", "Cancelled": cancelled,
            "DebtorCode": "300-ZZAC01", "LastModified": modified,
            "Details": details if details is not None else [_detail(951001)]}


def _xp(doc_key=960001, doc_no="ZZXP-0001", details=None, modified="2026-10-01T10:00:00.000"):
    return {"DocKey": doc_key, "DocNo": doc_no, "DocDate": "2026-10-01T00:00:00",
            "DocStatus": "A", "Cancelled": "F", "CreditorCode": "400-ZZAC01",
            "LastModified": modified, "Details": details or []}


def _so(env, so_number="ZZSO-C001", lines=((5001, "p1", 10, 0, "wh1"),)) -> dict[int, str]:
    """An AutoCount SO with lines (dtl_key, product, ordered, transferred, warehouse)."""
    so = SalesOrder(so_number=so_number, company_id=env.company, source_system="autocount")
    env.db.add(so)
    env.db.flush()
    out = {}
    for dtl, product, ordered, transferred, wh in lines:
        line = SalesOrderLine(
            sales_order_id=so.id, product_id=getattr(env, product),
            warehouse_id=getattr(env, wh) if wh else None,
            qty_ordered=ordered, qty_delivered=transferred,
            line_status="closed" if ordered and transferred >= ordered else "open",
            source_ref=f"SRT_DB:4001:{dtl}", company_id=env.company, line_no=dtl % 1000,
        )
        env.db.add(line)
        env.db.flush()
        out[dtl] = str(line.id)
    env.db.commit()
    return out


def _wh2(env) -> str:
    wh = Warehouse(warehouse_code="ZZAC-WH2", warehouse_name="wh2", company_id=env.company)
    env.db.add(wh)
    env.db.commit()
    env.wh2 = str(wh.id)
    return env.wh2


def _push_xs(env, records, **kw):
    return env.post(XS_INGEST, records, **kw)


def _so_line(env, line_id) -> SalesOrderLine:
    env.db.expire_all()
    return env.db.get(SalesOrderLine, line_id)


def _cancel_lines(env, doc_key) -> dict[int, AutocountCancelLine]:
    env.db.expire_all()
    doc = env.db.execute(
        select(AutocountCancelDoc).where(AutocountCancelDoc.company_id == env.company,
                                         AutocountCancelDoc.doc_key == doc_key)
    ).scalars().one()
    rows = env.db.execute(
        select(AutocountCancelLine).where(AutocountCancelLine.cancel_doc_id == doc.id)
    ).scalars().all()
    return {r.dtl_key: r for r in rows}


# ======================================================================= R1 ingest + storage
def test_xs_push_stores_the_doc_and_matches_by_line_key(env):
    lines = _so(env)
    res = _push_xs(env, [_xs(details=[_detail(951001, from_dtl=5001)])])
    rec = _records(res)["db1:XS:950001"]
    assert rec["outcome"] == "created"
    stored = _cancel_lines(env, 950001)
    assert stored[951001].match_status == "matched"
    assert stored[951001].sales_order_line_id == lines[5001]
    assert stored[951001].qty == Decimal("4")
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("4")


def test_repush_unchanged(env):
    _so(env)
    rec = _xs(details=[_detail(951001, from_dtl=5001)])
    _push_xs(env, [rec])
    assert _records(_push_xs(env, [copy.deepcopy(rec)]))["db1:XS:950001"]["outcome"] == "unchanged"


def test_dry_run_writes_nothing(env):
    lines = _so(env)
    res = _push_xs(env, [_xs(details=[_detail(951001, from_dtl=5001)])], dry_run=True)
    assert _records(res)["db1:XS:950001"]["outcome"] == "created"
    env.db.expire_all()
    assert env.db.execute(select(AutocountCancelDoc).where(
        AutocountCancelDoc.doc_key == 950001)).first() is None
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("0")


def test_book_is_required(env):
    res = _push_xs(env, [_xs()], book=None)
    assert res.status_code == 422


def test_bad_record_fails_with_the_field_named(env):
    bad = _xs()
    del bad["DocKey"]
    res = _push_xs(env, [bad])
    assert res.status_code == 200
    assert res.json()["records"][0]["outcome"] == "failed"
    assert "DocKey" in res.json()["records"][0]["errors"]


# ======================================================================= R3/R5 derived qty
def test_partial_cancel_keeps_the_line_open(env):
    lines = _so(env, lines=((5001, "p1", 10, 4, "wh1"),))
    _push_xs(env, [_xs(details=[_detail(951001, qty=4, from_dtl=5001)])])
    line = _so_line(env, lines[5001])
    assert line.qty_cancelled == Decimal("4")
    assert line.line_status == "open"


def test_full_cancel_marks_the_line_cancelled(env):
    """SO373923 shape: ordered 36, TransferedQty 36, all of it Cancel SO."""
    lines = _so(env, lines=((5001, "p1", 36, 36, "wh1"),))
    assert _so_line(env, lines[5001]).line_status == "closed"
    _push_xs(env, [_xs(details=[_detail(951001, qty=36, from_dtl=5001)])])
    line = _so_line(env, lines[5001])
    assert line.qty_cancelled == Decimal("36")
    assert line.line_status == "cancelled"
    # derived from AutoCount, not a CRM board cancel
    assert line.cancelled_at is None


def test_two_cancel_docs_sum_and_cap_at_ordered(env):
    lines = _so(env, lines=((5001, "p1", 10, 10, "wh1"),))
    _push_xs(env, [_xs(details=[_detail(951001, qty=6, from_dtl=5001)]),
                   _xs(doc_key=950002, doc_no="ZZXS-0002",
                       details=[_detail(952001, qty=7, from_dtl=5001)])])
    line = _so_line(env, lines[5001])
    assert line.qty_cancelled == Decimal("10")
    assert line.line_status == "cancelled"


def test_cancel_line_removed_on_repush_lowers_cancelled(env):
    lines = _so(env, lines=((5001, "p1", 10, 10, "wh1"),))
    _push_xs(env, [_xs(details=[_detail(951001, qty=10, from_dtl=5001)])])
    assert _so_line(env, lines[5001]).line_status == "cancelled"
    later = _xs(details=[_detail(951001, qty=3, from_dtl=5001)],
                modified="2026-10-02T10:00:00.000")
    assert _records(_push_xs(env, [later]))["db1:XS:950001"]["outcome"] == "updated"
    line = _so_line(env, lines[5001])
    assert line.qty_cancelled == Decimal("3")
    # reopened from its own quantities: TransferedQty 10 of 10 reads closed
    assert line.line_status == "closed"


def test_voided_cancel_doc_counts_zero_and_unvoid_counts_again(env):
    lines = _so(env, lines=((5001, "p1", 10, 4, "wh1"),))
    _push_xs(env, [_xs(details=[_detail(951001, qty=4, from_dtl=5001)])])
    _push_xs(env, [_xs(details=[_detail(951001, qty=4, from_dtl=5001)], cancelled="T",
                       modified="2026-10-02T10:00:00.000")])
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("0")
    _push_xs(env, [_xs(details=[_detail(951001, qty=4, from_dtl=5001)],
                       modified="2026-10-03T10:00:00.000")])
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("4")


def test_deletion_sweep_lowers_cancelled_and_reopens(env):
    lines = _so(env, lines=((5001, "p1", 10, 10, "wh1"),))
    _push_xs(env, [_xs(details=[_detail(951001, qty=10, from_dtl=5001)])])
    res = env.delete(XS_DELETE, [950001])
    assert res.status_code == 200, res.text
    line = _so_line(env, lines[5001])
    assert line.qty_cancelled == Decimal("0")
    assert line.line_status == "closed"


def test_crm_board_cancel_stays_cancelled_when_the_xs_vanishes(env):
    lines = _so(env, lines=((5001, "p1", 10, 10, "wh1"),))
    from datetime import datetime
    row = _so_line(env, lines[5001])
    row.line_status, row.cancelled_at = "cancelled", datetime(2026, 10, 1)
    env.db.commit()
    _push_xs(env, [_xs(details=[_detail(951001, qty=10, from_dtl=5001)])])
    env.delete(XS_DELETE, [950001])
    assert _so_line(env, lines[5001]).line_status == "cancelled"


# ======================================================================= R2 matching
def test_dangling_line_key_goes_to_review(env):
    lines = _so(env)
    _push_xs(env, [_xs(details=[_detail(951001, from_dtl=5999)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert stored.match_status == "unmatched"
    assert stored.match_reason == "dangling_line_key"
    assert stored.sales_order_line_id is None
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("0")


def test_no_key_one_line_of_the_item_matches(env):
    lines = _so(env, lines=((5001, "p1", 10, 4, "wh1"), (5002, "p2", 5, 0, "wh1")))
    _push_xs(env, [_xs(details=[_detail(951001, item="ZZAC-P1", from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert stored.match_status == "matched"
    assert stored.sales_order_line_id == lines[5001]


def test_no_key_the_so_group_is_so_and_item_whatever_the_location(env):
    """Owner rule 14 Oct: the cancel sits at the second line's location, but the group is both
    lines of the item. C (4) is not every transfer (8), so it names no line."""
    _wh2(env)
    lines = _so(env, lines=((5001, "p1", 10, 4, "wh1"), (5002, "p1", 10, 4, "wh2")))
    _push_xs(env, [_xs(details=[_detail(951001, location="ZZAC-WH2", from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert (stored.match_status, stored.match_reason) == ("unmatched", "undetermined")
    assert stored.sales_order_line_id is None
    assert _totals(env, lines) == {5001: Decimal("0"), 5002: Decimal("0")}


def test_no_key_one_line_of_the_item_at_another_location_matches(env):
    """Owner rule 14 Oct: the SO's one line of the item takes the cancel, whatever the location."""
    _wh2(env)
    lines = _so(env, lines=((5001, "p1", 10, 4, "wh1"),))
    _push_xs(env, [_xs(details=[_detail(951001, location="ZZAC-WH2", from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert (stored.match_status, stored.match_reason) == ("matched", "item")
    assert stored.sales_order_line_id == lines[5001]
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("4")


def test_a_do_at_another_location_still_counts_as_delivered_for_the_so_group(env):
    """Owner rule 14 Oct: D is every live DO line naming the SO and item, whatever its location.
    Whole at first (C = sum(T), D = 0); a DO at the other warehouse makes it undetermined."""
    from tests.test_ingest_autocount_do_grn import do_records

    _wh2(env)
    lines = _so(env, lines=((5001, "p1", 12, 12, "wh1"), (5002, "p1", 12, 0, "wh2")))
    _push_xs(env, [_xs(details=[_detail(951001, qty=12, from_dtl=0)])])
    assert _cancel_lines(env, 950001)[951001].match_reason == "group_whole"
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("12")
    rec = copy.deepcopy(do_records()[0])
    rec["Details"] = [dict(rec["Details"][0], ItemCode="ZZAC-P1", Qty=3.0, Location="ZZAC-WH2",
                           FromDocType="SO", FromDocNo="ZZSO-C001", FromDocDtlKey=0)]
    assert _records(env.push_do([rec]))["db1:DO:900001"]["outcome"] == "created"
    stored = _cancel_lines(env, 950001)[951001]
    assert (stored.match_reason, stored.sales_order_line_id) == ("undetermined", None)
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("0")


def test_no_key_nothing_explains_the_cancel_is_undetermined(env):
    """Two same-item lines and neither has transferred anything: C is not every transfer, so the
    cancel is kept at order + item level and names no line."""
    lines = _so(env, lines=((5001, "p1", 12, 0, "wh1"), (5002, "p1", 12, 0, "wh1")))
    _push_xs(env, [_xs(details=[_detail(951001, qty=12, from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert (stored.match_status, stored.match_reason) == ("unmatched", "undetermined")
    assert stored.sales_order_line_id is None
    assert sorted(stored.candidate_line_ids) == sorted(lines.values())
    assert all(_so_line(env, i).qty_cancelled == Decimal("0") for i in lines.values())


def _do_line(env, so_line_id, qty, number="ZZDO-C001"):
    from app.models.order import Order, OrderLine

    order = Order(order_number=number, company_id=env.company, order_status_id=env.new_status)
    env.db.add(order)
    env.db.flush()
    env.db.add(OrderLine(order_id=order.id, product_id=env.p1, quantity=qty, line_sequence=1,
                         warehouse_id=env.wh1, sales_order_line_id=so_line_id,
                         company_id=env.company))
    env.db.commit()


def test_a_do_on_the_group_leaves_the_old_tie_break_case_undetermined(env):
    """Owner rule 13 Oct: no tie-break. D > 0 and C > 0 over two lines: undetermined."""
    lines = _so(env, lines=((5001, "p1", 12, 12, "wh1"), (5002, "p1", 12, 12, "wh1")))
    _do_line(env, lines[5001], 12)
    _push_xs(env, [_xs(details=[_detail(951001, qty=12, from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert (stored.match_status, stored.match_reason) == ("unmatched", "undetermined")
    assert stored.sales_order_line_id is None
    assert all(_so_line(env, i).qty_cancelled == Decimal("0") for i in lines.values())
    assert all(_so_line(env, i).line_status == "closed" for i in lines.values())


def test_two_lines_that_could_each_hold_the_cancel_get_neither_and_no_history(env):
    """Owner rule 13 Oct: no lowest-Seq fallback and no auto-match history."""
    from app.models.audit import AuditLog

    lines = _so(env, lines=((5002, "p1", 12, 12, "wh1"), (5001, "p1", 12, 12, "wh1")))
    _push_xs(env, [_xs(doc_no="ZZXS-0042", details=[_detail(951001, qty=12, from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert (stored.match_status, stored.match_reason) == ("unmatched", "undetermined")
    assert stored.sales_order_line_id is None
    assert env.db.execute(select(AuditLog).where(
        AuditLog.entity_id.in_(list(lines.values())),
        AuditLog.event == "CANCEL_AUTO_MATCHED")).first() is None


def test_nothing_delivered_and_the_cancel_is_every_transfer_is_exact_per_line(env):
    """D = 0 and C = sum(T): every line's transfer is its cancel. The cancel lines name the
    group (no single line) and each line's qty_cancelled is its own transfer."""
    lines = _so(env, lines=((5001, "p1", 10, 10, "wh1"), (5002, "p1", 10, 4, "wh1")))
    _push_xs(env, [_xs(details=[_detail(951001, qty=8, from_dtl=0),
                                _detail(951002, qty=6, from_dtl=0)])])
    stored = _cancel_lines(env, 950001)
    for dtl in (951001, 951002):
        assert (stored[dtl].match_status, stored[dtl].match_reason) == ("matched", "group_whole")
        assert stored[dtl].sales_order_line_id is None
        assert sorted(stored[dtl].candidate_line_ids) == sorted(lines.values())
    first, second = _so_line(env, lines[5001]), _so_line(env, lines[5002])
    assert (first.qty_cancelled, first.line_status) == (Decimal("10"), "cancelled")
    assert (second.qty_cancelled, second.line_status) == (Decimal("4"), "open")
    # each line's tooltip names the group's cancel documents
    from app.services.autocount_cancel_ingest_service import cancel_sources

    sources = cancel_sources(env.db, AutocountCancelLine.sales_order_line_id,
                             list(lines.values()))
    # Review S1: only per-line totals are known, so the document is named once, with no qty.
    assert sources[lines[5001]] == sources[lines[5002]] == [
        {"doc_no": "ZZXS-0001", "qty": None, "match_reason": "group_whole"}]


def test_a_whole_group_cancel_that_lowers_reopens_and_clears(env):
    lines = _so(env, lines=((5001, "p1", 10, 10, "wh1"), (5002, "p1", 10, 4, "wh1")))
    _push_xs(env, [_xs(details=[_detail(951001, qty=14, from_dtl=0)])])
    assert _so_line(env, lines[5001]).line_status == "cancelled"
    _push_xs(env, [_xs(details=[_detail(951001, qty=5, from_dtl=0)],
                       modified="2026-10-02T10:00:00.000")])
    stored = _cancel_lines(env, 950001)[951001]
    assert stored.match_reason == "undetermined"
    assert {k: _so_line(env, v).qty_cancelled for k, v in lines.items()} == {
        5001: Decimal("0"), 5002: Decimal("0")}
    assert _so_line(env, lines[5001]).line_status == "closed"


def _match_picture(env, keys):
    out = {}
    for doc_key in keys:
        for dtl, line in _cancel_lines(env, doc_key).items():
            out[dtl] = (line.match_status, line.match_reason, line.sales_order_line_id)
    return out


def test_match_does_not_depend_on_arrival_order(env):
    """The same two cancel docs, pushed in either order (and one per batch), end in the same
    answer: the first alone is undetermined, both together are every transfer (exact)."""
    lines = _so(env, lines=((5001, "p1", 10, 10, "wh1"), (5002, "p1", 10, 4, "wh1")))
    first = _xs(details=[_detail(951001, qty=4, from_dtl=0)])
    second = _xs(doc_key=950002, doc_no="ZZXS-0002",
                 details=[_detail(952001, qty=10, from_dtl=0)])
    _push_xs(env, [first])
    _push_xs(env, [second])
    one_way = _match_picture(env, (950001, 950002))
    totals = {k: _so_line(env, v).qty_cancelled for k, v in lines.items()}
    # undo and replay the other way round
    _reset_cancels(env, lines)
    _push_xs(env, [second])
    _push_xs(env, [first])
    assert _match_picture(env, (950001, 950002)) == one_way
    assert {k: _so_line(env, v).qty_cancelled for k, v in lines.items()} == totals
    assert totals == {5001: Decimal("10"), 5002: Decimal("4")}


def test_sync_log_counts_every_decision(env, caplog):
    import logging

    _so(env, lines=((5001, "p1", 12, 12, "wh1"), (5002, "p1", 12, 12, "wh1")))
    with caplog.at_level(logging.INFO):
        _push_xs(env, [_xs(details=[_detail(951001, qty=12, from_dtl=0),
                                    _detail(951002, qty=12, from_dtl=5999)])])
    line = next(r.getMessage() for r in caplog.records
                if r.getMessage().startswith("ingest.cancel_match"))
    assert "unmatched:undetermined=1" in line
    assert "unmatched:dangling_line_key=1" in line


def test_no_key_item_not_on_the_so_goes_to_review(env):
    _so(env)
    _push_xs(env, [_xs(details=[_detail(951001, item="ZZAC-P2", from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert stored.match_status == "unmatched"
    assert stored.match_reason == "item_not_on_order"


def test_unknown_so_waits_and_links_at_a_later_batch(env):
    _push_xs(env, [_xs(details=[_detail(951001, from_no="ZZSO-LATE", from_dtl=7001)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert stored.match_status == "waiting"
    assert stored.match_reason == "order_not_found"
    lines = _so(env, so_number="ZZSO-LATE", lines=((7001, "p1", 10, 4, "wh1"),))
    _push_xs(env, [_xs(doc_key=950002, doc_no="ZZXS-0002",
                       details=[_detail(952001, from_no="ZZSO-NONE", from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert stored.match_status == "matched"
    assert _so_line(env, lines[7001]).qty_cancelled == Decimal("4")


def test_other_company_lines_never_match(env):
    so = SalesOrder(so_number="ZZSO-C001", company_id=env.company_b, source_system="autocount")
    env.db.add(so)
    env.db.flush()
    env.db.add(SalesOrderLine(sales_order_id=so.id, product_id=env.p1, qty_ordered=10,
                              source_ref="SRT_DB:4001:5001", company_id=env.company_b))
    env.db.commit()
    _push_xs(env, [_xs(details=[_detail(951001, from_dtl=5001)])])
    assert _cancel_lines(env, 950001)[951001].match_status == "waiting"


# ======================================================================= Cancel PO
def test_xp_matches_a_po_line_by_key(env):
    po = PurchaseOrder(po_number="ZZPO-C001", company_id=env.company)
    env.db.add(po)
    env.db.flush()
    pol = PurchaseOrderLine(purchase_order_id=po.id, product_id=env.p1, qty_ordered=100,
                            qty_received=100, source_ref="SRT_DB:7001:7101",
                            company_id=env.company)
    env.db.add(pol)
    env.db.commit()
    rec = _xp(details=[_detail(961001, qty=100, from_no="ZZPO-C001", from_dtl=7101,
                               from_type="PO")])
    assert _records(env.post(XP_INGEST, [rec]))["db1:XP:960001"]["outcome"] == "created"
    stored = _cancel_lines(env, 960001)[961001]
    assert stored.purchase_order_line_id == str(pol.id)
    env.db.expire_all()
    line = env.db.get(PurchaseOrderLine, str(pol.id))
    assert line.qty_cancelled == Decimal("100")
    assert line.line_status == "cancelled"


def test_xp_can_name_an_spo_line(env):
    """PO and SPO share one AutoCount table, so a DtlKey resolves against either."""
    spo = SPOAllocation(spo_number="SPO-ZZ-C001", product_id=env.p2, allocated_quantity=20,
                        source_ref="SRT_DB:7002:7201", company_id=env.company)
    env.db.add(spo)
    env.db.commit()
    rec = _xp(details=[_detail(961001, item="ZZAC-P2", qty=5, from_no="SPO-ZZ-C001",
                               from_dtl=7201, from_type="PO")])
    env.post(XP_INGEST, [rec])
    stored = _cancel_lines(env, 960001)[961001]
    assert stored.match_status == "matched"
    assert stored.spo_allocation_id == str(spo.id)
    env.db.expire_all()
    assert env.db.get(SPOAllocation, str(spo.id)).qty_cancelled == Decimal("5")


# ======================================================================= contract
def test_contract_2_8_lists_the_cancel_entities(env):
    body = env.client.get("/api/v1/external/contract").json()
    assert body["version"] == "2.10"
    assert {"cancel_sales_orders", "cancel_purchase_orders", "consignments",
            "consignment_returns"} <= set(body["entities"])


def test_each_entity_has_its_own_slugs(env):
    from app.api.v1.external.ingest import (
        DELETE_PERMISSIONS,
        INGEST_PERMISSIONS,
        READ_PERMISSIONS,
    )

    for entity in ("cancel_sales_orders", "cancel_purchase_orders", "consignments",
                   "consignment_returns"):
        assert INGEST_PERMISSIONS[entity] == f"scm.{entity}.edit"
        assert READ_PERMISSIONS[entity] == f"scm.{entity}.view"
        assert DELETE_PERMISSIONS[entity] == f"scm.{entity}.delete"


def test_consignments_are_stored_only(env):
    """Crew, 13 Oct: accepted and stored in the same shape; nothing is matched or moved."""
    lines = _so(env, lines=((5001, "p1", 10, 10, "wh1"),))
    rec = _xs(doc_key=970001, doc_no="ZZCSGN-0001",
              details=[_detail(971001, qty=10, from_dtl=5001)])
    res = env.post("/api/v1/external/ingest/consignments", [rec])
    assert _records(res)["db1:CSGN:970001"]["outcome"] == "created"
    env.db.expire_all()
    doc = env.db.execute(select(AutocountCancelDoc).where(
        AutocountCancelDoc.doc_key == 970001)).scalars().one()
    line = env.db.execute(select(AutocountCancelLine).where(
        AutocountCancelLine.cancel_doc_id == doc.id)).scalars().one()
    assert doc.kind == "consignment"
    assert (line.match_status, line.match_reason) == ("unmatched", "stored_only")
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("0")
    # a consignment's DocKey is not a consignment return's
    res = env.client.post("/api/v1/external/ingest/consignment_returns/deletions", json={
        "companyCode": env.company_code, "book": "db1", "doc_date_from": "2026-01-01",
        "doc_date_to": "2026-12-31", "doc_keys": [970001]})
    assert res.json()["records"][0]["outcome"] == "not_found"


# ======================================================================= review round 1
def _reset_cancels(env, lines):
    env.db.execute(AutocountCancelLine.__table__.delete())
    env.db.execute(AutocountCancelDoc.__table__.delete())
    for line_id in lines.values():
        row = _so_line(env, line_id)
        row.qty_cancelled = 0
        row.cancelled_by_cancel_doc = False
        row.line_status = "closed" if row.qty_delivered >= row.qty_ordered else "open"
    env.db.commit()


def test_keyed_and_keyless_cancels_match_the_same_in_any_order(env):
    """Review B1: a key-less cancel is decided after the keyed ones it shares a group with
    have landed, whichever document came first and whether they share a batch."""
    lines = _so(env, lines=((5001, "p1", 5, 5, "wh1"), (5002, "p1", 5, 5, "wh1")))
    keyed = _xs(details=[_detail(951001, qty=5, from_dtl=5001)])
    keyless = _xs(doc_key=950002, doc_no="ZZXS-0002", details=[_detail(952001, qty=5, from_dtl=0)])
    pictures = []
    for order in ([keyed], [keyless]), ([keyless], [keyed]), ([keyed, keyless],):
        _reset_cancels(env, lines)
        for batch in order:
            _push_xs(env, batch)
        pictures.append((_match_picture(env, (950001, 950002)),
                         {k: _so_line(env, v).qty_cancelled for k, v in lines.items()}))
    assert pictures[0] == pictures[1] == pictures[2]
    assert pictures[0][1] == {5001: Decimal("5"), 5002: Decimal("5")}


def test_a_cancelled_order_never_reopens_when_the_xs_vanishes(env):
    """Review B3 / R6."""
    lines = _so(env, lines=((5001, "p1", 10, 10, "wh1"),))
    so = env.db.get(SalesOrderLine, lines[5001]).sales_order
    so.status = "cancelled"
    env.db.commit()
    _push_xs(env, [_xs(details=[_detail(951001, qty=10, from_dtl=5001)])])
    env.delete(XS_DELETE, [950001])
    assert _so_line(env, lines[5001]).line_status == "cancelled"


def test_a_line_cancelled_for_another_reason_is_never_reopened(env):
    """Review should-fix 2: a line AutoCount removed (kept `cancelled`, no `cancelled_at`) is
    not this rule's to reopen when a cancel doc on it vanishes."""
    lines = _so(env, lines=((5001, "p1", 10, 10, "wh1"),))
    row = _so_line(env, lines[5001])
    row.line_status = "cancelled"
    env.db.commit()
    _push_xs(env, [_xs(details=[_detail(951001, qty=10, from_dtl=5001)])])
    assert _so_line(env, lines[5001]).cancelled_by_cancel_doc is False
    env.delete(XS_DELETE, [950001])
    assert _so_line(env, lines[5001]).line_status == "cancelled"


def test_a_voided_keyed_cancel_does_not_explain_a_transfer(env):
    """Review M13: a voided line-key cancel takes nothing from a line's transfer, so the
    key-less 5 is not every transfer (10) and stays undetermined; a live one would make it so."""
    _so(env, lines=((5001, "p1", 5, 5, "wh1"), (5002, "p1", 5, 5, "wh1")))
    _push_xs(env, [_xs(details=[_detail(951001, qty=5, from_dtl=5001)], cancelled="T")])
    _push_xs(env, [_xs(doc_key=950002, doc_no="ZZXS-0002",
                       details=[_detail(952001, qty=5, from_dtl=0)])])
    stored = _cancel_lines(env, 950002)[952001]
    assert (stored.match_reason, stored.sales_order_line_id) == ("undetermined", None)


def test_a_huge_qty_fails_its_record_not_the_batch(env):
    """Security review should-fix 1."""
    lines = _so(env)
    res = _push_xs(env, [_xs(details=[_detail(951001, qty="1e30", from_dtl=5001)]),
                         _xs(doc_key=950002, doc_no="ZZXS-0002",
                             details=[_detail(952001, qty=4, from_dtl=5001)])])
    recs = _records(res)
    assert recs["db1:XS:950001"]["outcome"] == "failed"
    assert "Details.0.Qty" in recs["db1:XS:950001"]["errors"]
    assert recs["db1:XS:950002"]["outcome"] == "created"
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("4")


def test_other_company_lines_never_match_without_a_key(env):
    """Security review should-fix 3: same SO number and item in company B, key-less line."""
    so = SalesOrder(so_number="ZZSO-C001", company_id=env.company_b, source_system="autocount")
    env.db.add(so)
    env.db.flush()
    other = SalesOrderLine(sales_order_id=so.id, product_id=env.p1, qty_ordered=10,
                           qty_delivered=10, source_ref="SRT_DB:4001:5001",
                           company_id=env.company_b)
    env.db.add(other)
    env.db.commit()
    _push_xs(env, [_xs(details=[_detail(951001, from_dtl=0)])])
    assert _cancel_lines(env, 950001)[951001].match_status == "waiting"
    env.db.expire_all()
    assert env.db.get(SalesOrderLine, str(other.id)).qty_cancelled == Decimal("0")


def test_other_company_spo_never_matches(env):
    """Security review should-fix 3: the SPO match-key path stays inside the company."""
    spo = SPOAllocation(spo_number="SPO-ZZ-C009", product_id=env.p2, allocated_quantity=20,
                        source_ref="SRT_DB:7002:7209", company_id=env.company_b)
    env.db.add(spo)
    env.db.commit()
    env.post(XP_INGEST, [_xp(details=[_detail(961001, item="ZZAC-P2", qty=5,
                                              from_no="SPO-ZZ-C009", from_dtl=0,
                                              from_type="PO")])])
    assert _cancel_lines(env, 960001)[961001].match_status == "waiting"
    env.db.expire_all()
    assert env.db.get(SPOAllocation, str(spo.id)).qty_cancelled == Decimal("0")


def test_a_do_push_re_decides_the_cancels_naming_its_orders(env, monkeypatch):
    """Review should-fix 3: a DO moves an SO line's unexplained transfer, so the route hands
    the order numbers a landed DO names to the cancel relink, inside the DO's company."""
    import app.api.v1.external.ingest as ingest_route
    from app.services.autocount_cancel_ingest_service import Derived
    from tests.test_ingest_autocount_do_grn import do_records

    seen = []
    monkeypatch.setattr(ingest_route, "relink_cancels_for_orders",
                        lambda db, company_id, numbers: seen.append((company_id, set(numbers)))
                        or Derived())
    rec = copy.deepcopy(do_records()[0])
    rec["RefDocNo"] = "ZZSO-C001"
    rec["Details"][0].update({"FromDocType": "SO", "FromDocNo": "ZZSO-C002", "FromDocDtlKey": 0})
    env.push_do([rec])
    assert seen == [(env.company, {"ZZSO-C001", "ZZSO-C002"})]
    seen.clear()
    env.push_do([rec])  # unchanged: nothing to re-decide
    assert seen == []


def test_a_do_that_lands_on_the_group_turns_an_exact_cancel_undetermined(env):
    """The cancel is first every transfer (exact); a DO naming the SO and item then lands, so
    D > 0 and the re-decision takes the cancel off both lines."""
    from tests.test_ingest_autocount_do_grn import do_records

    lines = _so(env, lines=((5001, "p1", 12, 12, "wh1"), (5002, "p1", 12, 0, "wh1")))
    _push_xs(env, [_xs(details=[_detail(951001, qty=12, from_dtl=0)])])
    assert _cancel_lines(env, 950001)[951001].match_reason == "group_whole"
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("12")
    rec = copy.deepcopy(do_records()[0])
    rec["Details"] = [dict(rec["Details"][0], ItemCode="ZZAC-P1", Qty=3.0, Location="ZZAC-WH1",
                           FromDocType="SO", FromDocNo="ZZSO-C001", FromDocDtlKey=0)]
    assert _records(env.push_do([rec]))["db1:DO:900001"]["outcome"] == "created"
    stored = _cancel_lines(env, 950001)[951001]
    assert (stored.match_reason, stored.sales_order_line_id) == ("undetermined", None)
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("0")
    assert _so_line(env, lines[5001]).line_status == "closed"


# ======================================================================= Cancel PO groups
def test_xp_without_a_key_over_two_po_lines_is_exact_when_it_is_every_receipt(env):
    po = PurchaseOrder(po_number="ZZPO-C002", company_id=env.company)
    env.db.add(po)
    env.db.flush()
    ids = []
    for dtl, received in ((7101, 5), (7102, 3)):
        pol = PurchaseOrderLine(purchase_order_id=po.id, product_id=env.p1, qty_ordered=10,
                                qty_received=received, warehouse_id=env.wh1,
                                source_ref=f"SRT_DB:7001:{dtl}", company_id=env.company)
        env.db.add(pol)
        env.db.flush()
        ids.append(str(pol.id))
    env.db.commit()
    env.post(XP_INGEST, [_xp(details=[_detail(961001, qty=8, from_no="ZZPO-C002", from_dtl=0,
                                              from_type="PO")])])
    stored = _cancel_lines(env, 960001)[961001]
    assert (stored.match_status, stored.match_reason) == ("matched", "group_whole")
    assert stored.purchase_order_line_id is None
    env.db.expire_all()
    assert [env.db.get(PurchaseOrderLine, i).qty_cancelled for i in ids] == [Decimal("5"),
                                                                            Decimal("3")]


# ======================================================================= review round (exact rework)
def _whole_pair(env, a=8, b=6):
    """5001 (ordered 10, T 10) and 5002 (ordered 10, T 4); key-less XS-A and XS-B."""
    lines = _so(env, lines=((5001, "p1", 10, 10, "wh1"), (5002, "p1", 10, 4, "wh1")))
    _push_xs(env, [_xs(details=[_detail(951001, qty=a, from_dtl=0)]),
                   _xs(doc_key=950002, doc_no="ZZXS-0002",
                       details=[_detail(952001, qty=b, from_dtl=0)])])
    return lines


def _totals(env, lines):
    return {k: _so_line(env, v).qty_cancelled for k, v in lines.items()}


def _undone(env, lines, survivor):
    stored = _cancel_lines(env, survivor[0])[survivor[1]]
    assert (stored.match_status, stored.match_reason) == ("unmatched", "undetermined")
    assert _totals(env, lines) == {5001: Decimal("0"), 5002: Decimal("0")}
    assert _so_line(env, lines[5001]).line_status == "closed"


def test_voiding_one_cancel_of_a_whole_group_re_decides_the_rest(env):
    """Review B1 (1): C drops to 6, sum(T) is 14: undetermined, both lines back to 0."""
    lines = _whole_pair(env)
    assert _cancel_lines(env, 950002)[952001].match_reason == "group_whole"
    _push_xs(env, [_xs(details=[_detail(951001, qty=8, from_dtl=0)], cancelled="T",
                       modified="2026-10-02T10:00:00.000")])
    _undone(env, lines, (950002, 952001))


def test_sweeping_one_cancel_of_a_whole_group_re_decides_the_rest(env):
    """Review B1 (2)."""
    lines = _whole_pair(env)
    assert env.delete(XS_DELETE, [950001]).status_code == 200
    _undone(env, lines, (950002, 952001))


def test_removing_a_cancel_line_from_a_whole_group_re_decides_the_rest(env):
    """Review B1 (3): XS-A is re-pushed without its P1 line (a P2 line stays)."""
    lines = _whole_pair(env)
    _push_xs(env, [_xs(details=[_detail(951009, item="ZZAC-P2", qty=1, from_dtl=0)],
                       modified="2026-10-02T10:00:00.000")])
    _undone(env, lines, (950002, 952001))


def test_moving_a_cancel_line_to_another_item_re_decides_its_old_group(env):
    """Review B1 (4): XS-B's line turns from P1 to P2; XS-A alone (8) is not every transfer."""
    lines = _whole_pair(env)
    _push_xs(env, [_xs(doc_key=950002, doc_no="ZZXS-0002",
                       details=[_detail(952001, item="ZZAC-P2", qty=6, from_dtl=0)],
                       modified="2026-10-02T10:00:00.000")])
    _undone(env, lines, (950001, 951001))


def test_voiding_the_extra_cancel_makes_the_group_whole(env):
    """Review B1 (5): 14 + 5 is not every transfer; voiding the 5 leaves exactly 14."""
    lines = _whole_pair(env, a=14, b=5)
    assert _cancel_lines(env, 950001)[951001].match_reason == "undetermined"
    _push_xs(env, [_xs(doc_key=950002, doc_no="ZZXS-0002",
                       details=[_detail(952001, qty=5, from_dtl=0)], cancelled="T",
                       modified="2026-10-02T10:00:00.000")])
    assert _cancel_lines(env, 950001)[951001].match_reason == "group_whole"
    assert _totals(env, lines) == {5001: Decimal("10"), 5002: Decimal("4")}


def test_two_locations_in_one_batch_are_one_so_group(env):
    """Review M7, owner rule 14 Oct: the two cancels sit at the two lines' locations, but the
    SO group is SO + item. Together they are every transfer (7) with nothing delivered, so each
    line's cancelled qty is its own transfer (`group_whole`), never a per-location pick."""
    _wh2(env)
    lines = _so(env, lines=((5001, "p1", 10, 4, "wh1"), (5002, "p1", 10, 3, "wh2")))
    _push_xs(env, [_xs(details=[_detail(951001, qty=4, from_dtl=0),
                                _detail(951002, qty=3, location="ZZAC-WH2", from_dtl=0)])])
    stored = _cancel_lines(env, 950001)
    assert {k: (v.match_reason, v.sales_order_line_id) for k, v in stored.items()} == {
        951001: ("group_whole", None), 951002: ("group_whole", None)}
    assert _totals(env, lines) == {5001: Decimal("4"), 5002: Decimal("3")}


def test_a_whole_group_cancel_names_its_document_to_the_oi_retire(env):
    """Review M9: the R5 retire reads "Transferred to Cancel SO <XS no>" for a whole group."""
    from app.services.autocount_cancel_ingest_service import _latest_doc_no

    lines = _whole_pair(env)
    assert _so_line(env, lines[5001]).line_status == "cancelled"
    # the latest of the group's documents (same date: the higher number), never blank
    assert _latest_doc_no(env.db, lines[5001]) == "ZZXS-0002"


def test_another_companys_do_and_a_cancelled_do_do_not_count_as_delivered(env):
    """Review M12 / M14: D reads this company's live DOs only."""
    from app.models.order import Order, OrderLine

    lines = _so(env, lines=((5001, "p1", 10, 10, "wh1"), (5002, "p1", 10, 4, "wh1")))
    for company, cancelled, number in ((env.company_b, False, "ZZDO-CB01"),
                                       (env.company, True, "ZZDO-CX01")):
        order = Order(order_number=number, company_id=company, order_status_id=env.new_status,
                      is_cancelled=cancelled)
        env.db.add(order)
        env.db.flush()
        env.db.add(OrderLine(order_id=order.id, product_id=env.p1, warehouse_id=env.wh1,
                             quantity=5, line_sequence=1,
                             company_id=company,
                             item_code="ZZAC-P1", location_code="ZZAC-WH1",
                             from_doc_type="SO", from_doc_no="ZZSO-C001"))
    env.db.commit()
    _push_xs(env, [_xs(details=[_detail(951001, qty=14, from_dtl=0)])])
    assert _cancel_lines(env, 950001)[951001].match_reason == "group_whole"
    assert _totals(env, lines) == {5001: Decimal("10"), 5002: Decimal("4")}


def test_a_keyed_cancel_and_a_whole_group_on_one_line_read_its_transfer(env):
    """Review M17: ordered above the transfer, so the cap hides nothing. 5001 T 6 with a keyed
    2; key-less 8 = (6 - 2) + 4: 5001 reads 6, not 6 + 2."""
    lines = _so(env, lines=((5001, "p1", 10, 6, "wh1"), (5002, "p1", 10, 4, "wh1")))
    _push_xs(env, [_xs(details=[_detail(951001, qty=2, from_dtl=5001)]),
                   _xs(doc_key=950002, doc_no="ZZXS-0002",
                       details=[_detail(952001, qty=8, from_dtl=0)])])
    assert _cancel_lines(env, 950002)[952001].match_reason == "group_whole"
    assert _totals(env, lines) == {5001: Decimal("6"), 5002: Decimal("4")}


def _po_pair(env, number="ZZPO-C003"):
    po = PurchaseOrder(po_number=number, company_id=env.company)
    env.db.add(po)
    env.db.flush()
    ids = []
    for dtl, received in ((7101, 5), (7102, 3)):
        pol = PurchaseOrderLine(purchase_order_id=po.id, product_id=env.p1, qty_ordered=10,
                                qty_received=received, warehouse_id=env.wh1,
                                source_ref=f"SRT_DB:7001:{dtl}", company_id=env.company)
        env.db.add(pol)
        env.db.flush()
        ids.append(str(pol.id))
    env.db.commit()
    return ids


def _po_totals(env, ids):
    env.db.expire_all()
    return [env.db.get(PurchaseOrderLine, i).qty_cancelled for i in ids]


def test_a_grn_on_the_po_group_leaves_the_cancel_undetermined(env):
    """Review M16."""
    import uuid

    from app.models.procurement import PickingHeader, PickingLine

    ids = _po_pair(env)
    header = PickingHeader(id=str(uuid.uuid4()), company_id=env.company,
                           picking_number="ZZGRN-C001", picking_type="goods_received",
                           picking_status="approved", inspection_status="pending")
    env.db.add(header)
    env.db.flush()
    env.db.add(PickingLine(id=str(uuid.uuid4()), company_id=env.company,
                           picking_header_id=header.id, po_line_id=ids[1], product_id=env.p1,
                           quantity_expected=3, quantity_picked=3))
    env.db.commit()
    env.post(XP_INGEST, [_xp(details=[_detail(961001, qty=8, from_no="ZZPO-C003", from_dtl=0,
                                              from_type="PO")])])
    assert _cancel_lines(env, 960001)[961001].match_reason == "undetermined"
    assert _po_totals(env, ids) == [Decimal("0"), Decimal("0")]


def test_a_keyed_xp_counts_against_its_po_line_in_the_group(env):
    """Review M15: keyed 3 on 7102 leaves T' 5 and 0, so a key-less 5 is every transfer."""
    ids = _po_pair(env)
    env.post(XP_INGEST, [_xp(details=[
        _detail(961001, qty=3, from_no="ZZPO-C003", from_dtl=7102, from_type="PO"),
        _detail(961002, qty=5, from_no="ZZPO-C003", from_dtl=0, from_type="PO")])])
    assert _cancel_lines(env, 960001)[961002].match_reason == "group_whole"
    assert _po_totals(env, ids) == [Decimal("5"), Decimal("3")]


def test_xp_without_a_key_over_two_spo_lines_is_exact_when_it_is_every_receipt(env):
    """Review M18."""
    ids = []
    for dtl, received in ((7201, 5), (7202, 3)):
        spo = SPOAllocation(spo_number="SPO-ZZ-C002", product_id=env.p2, allocated_quantity=20,
                            stated_received=received, warehouse_id=env.wh1,
                            spo_line_number=dtl % 100, source_ref=f"SRT_DB:7002:{dtl}",
                            company_id=env.company)
        env.db.add(spo)
        env.db.flush()
        ids.append(str(spo.id))
    env.db.commit()
    env.post(XP_INGEST, [_xp(details=[_detail(961001, item="ZZAC-P2", qty=8,
                                              from_no="SPO-ZZ-C002", from_dtl=0,
                                              from_type="PO")])])
    assert _cancel_lines(env, 960001)[961001].match_reason == "group_whole"
    env.db.expire_all()
    assert [env.db.get(SPOAllocation, i).qty_cancelled for i in ids] == [Decimal("5"),
                                                                        Decimal("3")]


def test_the_old_group_is_found_when_its_first_line_is_gone(env):
    """Re-review should-fix 1: the group's first stored line was removed from the SO, so the old
    group is found through the next one. XS-B moves to P2; XS-A alone now faces one line."""
    lines = _whole_pair(env)
    first = _cancel_lines(env, 950002)[952001].candidate_line_ids[0]
    env.db.execute(AutocountCancelLine.__table__.update()
                   .where(AutocountCancelLine.sales_order_line_id == first)
                   .values(sales_order_line_id=None))
    env.db.delete(env.db.get(SalesOrderLine, first))
    env.db.commit()
    _push_xs(env, [_xs(doc_key=950002, doc_no="ZZXS-0002",
                       details=[_detail(952001, item="ZZAC-P2", qty=6, from_dtl=0)],
                       modified="2026-10-02T10:00:00.000")])
    remaining = next(v for v in lines.values() if v != first)
    stored = _cancel_lines(env, 950001)[951001]
    assert (stored.match_reason, stored.sales_order_line_id) == ("item", remaining)
