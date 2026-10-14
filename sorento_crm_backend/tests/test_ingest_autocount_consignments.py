"""AutoCount Consignment (CG) and Consignment Return on the CRM side (AC-CONSIGN-CRM).

Plan: documentation/plans/autocount/PLAN-autocount-consign-crm-14oct.md.

Owner rulings 13 Oct, crew answers 14 Oct:
- A consignment is a sales order with `doc_type = consignment`, written by the same SO ingest
  (customer ladder, demand class, line sync, status). CONSIGN-DELIVERED-FIX (owner, 14 Oct):
  the goods left with the CG, so every line is delivered in full and the order is closed;
  TransferedQty is the RETURNED qty (tests/test_consignment_delivered_at_issue.py).
- A consignment return records `qty_returned` on the consignment line, for reporting only: no
  planned, delivered, cancelled or status change. Exact link by FromDocDtlKey, else the
  (CG, item, location) group's one line, else undetermined (named on no line).
- Contract 2.9 fields (DebtorCode, DebtorName, SalesAgent, per line UnitPrice, SubTotal, UOM,
  TransferedQty) are accepted when sent (Q1 a, additive).

Reuses the DO / GRN suite's `env` (products ZZAC-P1 / ZZAC-P2, warehouse ZZAC-WH1). Every
record here is fake.
"""
from __future__ import annotations

import copy
from decimal import Decimal

from sqlalchemy import select

from app.models.autocount_cancel import AutocountCancelDoc, AutocountCancelLine
from app.models.order import SalesOrder, SalesOrderLine

from tests.test_ingest_autocount_cancel_docs import _detail, _so, _xs
from tests.test_ingest_autocount_do_grn import (  # noqa: F401 - env is a fixture
    _records,
    env,
)

CG_INGEST = "/api/v1/external/ingest/consignments"
CG_DELETE = "/api/v1/external/ingest/consignments/deletions"
CR_INGEST = "/api/v1/external/ingest/consignment_returns"
CR_DELETE = "/api/v1/external/ingest/consignment_returns/deletions"
SO_INGEST = "/api/v1/external/ingest/sales_orders"
XS_INGEST = "/api/v1/external/ingest/cancel_sales_orders"


# ----------------------------------------------------------------------- builders
def _cg_line(dtl_key, item="ZZAC-P1", qty=10.0, *, location="ZZAC-WH1", seq=None,
             transferred=0, **extra):
    """A contract 2.9 line (TransferedQty stated). `transferred=None` leaves the key out, the
    2.8 shape ss#122 ships today."""
    row = {"DtlKey": dtl_key, "Seq": seq if seq is not None else dtl_key % 1000,
           "ItemCode": item, "Location": location, "Qty": qty,
           "FromDocType": None, "FromDocNo": None, "FromDocDtlKey": None}
    if transferred is not None:
        row["TransferedQty"] = transferred
    row.update(extra)
    return row


def _cg(doc_key=980001, doc_no="ZZCG-0001", details=None, *, cancelled="F",
        modified="2026-10-01T10:00:00.000", **extra):
    rec = {"DocKey": doc_key, "DocNo": doc_no, "DocDate": "2026-10-01T00:00:00",
           "Cancelled": cancelled, "LastModified": modified,
           "Details": details if details is not None else [_cg_line(981001)]}
    rec.update(extra)
    return rec


def _cr_line(dtl_key, qty=4.0, *, item="ZZAC-P1", from_no="ZZCG-0001", from_dtl=None,
             location="ZZAC-WH1", from_type="CG"):
    return {"DtlKey": dtl_key, "Seq": dtl_key % 1000, "ItemCode": item, "Location": location,
            "Qty": qty, "FromDocType": from_type, "FromDocNo": from_no,
            "FromDocDtlKey": from_dtl}


def _cr(doc_key=990001, doc_no="ZZCR-0001", details=None, *, cancelled="F",
        modified="2026-10-02T10:00:00.000"):
    return {"DocKey": doc_key, "DocNo": doc_no, "DocDate": "2026-10-02T00:00:00",
            "Cancelled": cancelled, "LastModified": modified, "ReturnType": "CVT TO DO",
            "Details": details if details is not None else [_cr_line(991001)]}


def _order(env, so_number="ZZCG-0001") -> SalesOrder | None:
    env.db.expire_all()
    return env.db.execute(select(SalesOrder).where(
        SalesOrder.company_id == env.company, SalesOrder.so_number == so_number)
    ).scalars().one_or_none()


def _lines(env, order: SalesOrder) -> dict[str, SalesOrderLine]:
    env.db.expire_all()
    rows = env.db.execute(select(SalesOrderLine).where(
        SalesOrderLine.sales_order_id == order.id)).scalars().all()
    return {r.source_ref: r for r in rows}


def _line(env, source_ref) -> SalesOrderLine:
    env.db.expire_all()
    return env.db.execute(select(SalesOrderLine).where(
        SalesOrderLine.source_ref == source_ref)).scalars().one()


def _return_lines(env, doc_key) -> dict[int, AutocountCancelLine]:
    env.db.expire_all()
    doc = env.db.execute(select(AutocountCancelDoc).where(
        AutocountCancelDoc.company_id == env.company, AutocountCancelDoc.doc_key == doc_key,
        AutocountCancelDoc.kind == "consignment_return")).scalars().one()
    rows = env.db.execute(select(AutocountCancelLine).where(
        AutocountCancelLine.cancel_doc_id == doc.id)).scalars().all()
    return {r.dtl_key: r for r in rows}


def _sweep(env, url, keys):
    return env.client.post(url, json={
        "companyCode": env.company_code, "book": "db1", "doc_date_from": "2026-01-01",
        "doc_date_to": "2026-12-31", "doc_keys": keys})


# ======================================================================= A: CG becomes demand
def test_a_consignment_lands_as_a_sales_order_marked_consignment(env):
    res = env.post(CG_INGEST, [_cg(details=[_cg_line(981001), _cg_line(981002, "ZZAC-P2", 5)])])
    assert _records(res)["db1:CSGN:980001"]["outcome"] == "created"

    order = _order(env)
    assert order is not None
    assert order.doc_type == "consignment"
    assert order.source_ref == "db1:CSGN:980001"
    assert order.status == "closed"
    lines = _lines(env, order)
    assert set(lines) == {"db1:CSGN:980001:981001", "db1:CSGN:980001:981002"}
    a = lines["db1:CSGN:980001:981001"]
    assert (a.product_id, a.warehouse_id) == (env.p1, env.wh1)
    assert (a.qty_ordered, a.qty_delivered, a.line_status) == (
        Decimal("10"), Decimal("10"), "closed")
    assert lines["db1:CSGN:980001:981002"].qty_ordered == Decimal("5")
    # #1623's storage stays: the doc is still kept as sent.
    doc = env.db.execute(select(AutocountCancelDoc).where(
        AutocountCancelDoc.kind == "consignment", AutocountCancelDoc.doc_key == 980001)
    ).scalars().one()
    assert doc.doc_no == "ZZCG-0001"


def test_a_plain_sales_order_reads_sales_order(env):
    _so(env, so_number="ZZSO-PLAIN")
    assert _order(env, "ZZSO-PLAIN").doc_type == "sales_order"


def test_the_2_9_fields_are_taken_when_sent(env):
    rec = _cg(details=[_cg_line(981001, UnitPrice=12.5, SubTotal=125.0, UOM="PCS",
                                transferred=10)],
              DebtorCode="300-ZZAC01", DebtorName="ZZ Dealer Sdn Bhd")
    assert _records(env.post(CG_INGEST, [rec]))["db1:CSGN:980001"]["outcome"] == "created"
    order = _order(env)
    assert (order.debtor_code, order.debtor_name) == ("300-ZZAC01", "ZZ Dealer Sdn Bhd")
    line = _line(env, "db1:CSGN:980001:981001")
    assert line.unit_price == Decimal("12.5")
    assert line.line_total == Decimal("125")
    assert line.uom == "PCS"
    # CONSIGN-DELIVERED-FIX: delivered in full at issue; TransferedQty is the returned qty.
    assert (line.qty_delivered, line.qty_returned) == (Decimal("10"), Decimal("10"))
    assert line.line_status == "closed"


def test_repush_is_unchanged_and_an_edit_moves_the_line(env):
    rec = _cg()
    env.post(CG_INGEST, [rec])
    assert _records(env.post(CG_INGEST, [copy.deepcopy(rec)]))["db1:CSGN:980001"][
        "outcome"] == "unchanged"
    edited = _cg(details=[_cg_line(981001, qty=7)], modified="2026-10-03T10:00:00.000")
    assert _records(env.post(CG_INGEST, [edited]))["db1:CSGN:980001"]["outcome"] == "updated"
    assert _line(env, "db1:CSGN:980001:981001").qty_ordered == Decimal("7")


def test_a_voided_consignment_cancels_its_order(env):
    env.post(CG_INGEST, [_cg()])
    env.post(CG_INGEST, [_cg(cancelled="T", modified="2026-10-03T10:00:00.000")])
    order = _order(env)
    assert order.status == "cancelled"
    assert _line(env, "db1:CSGN:980001:981001").line_status == "cancelled"


def test_a_swept_consignment_cancels_its_order_and_is_never_deleted(env):
    env.post(CG_INGEST, [_cg()])
    res = _sweep(env, CG_DELETE, [980001])
    assert res.status_code == 200, res.text
    assert res.json()["records"][0]["outcome"] == "deactivated"
    order = _order(env)
    assert order is not None and order.status == "cancelled"
    assert _line(env, "db1:CSGN:980001:981001").line_status == "cancelled"


def test_a_consignment_never_takes_over_a_real_sales_order(env):
    lines = _so(env, so_number="ZZCG-0001")
    rec = _records(env.post(CG_INGEST, [_cg()]))["db1:CSGN:980001"]
    assert rec["outcome"] == "failed"
    assert "so_number" in rec["errors"]
    order = _order(env)
    assert order.doc_type == "sales_order"
    assert set(_lines(env, order)) == {"SRT_DB:4001:5001"}
    assert _line(env, "SRT_DB:4001:5001").id == lines[5001]


def test_a_sales_order_push_never_takes_over_a_consignment(env):
    env.post(CG_INGEST, [_cg()])
    so = {"source_ref": "db1:4999", "so_number": "ZZCG-0001", "doc_date": "2026-10-01",
          "lines": [{"source_ref": "db1:4999:1", "product_code": "ZZAC-P1", "qty_ordered": 3}]}
    rec = _records(env.post(SO_INGEST, [so], book=None))["db1:4999"]
    assert rec["outcome"] == "failed"
    assert _order(env).doc_type == "consignment"


def test_an_unknown_item_is_dropped_and_counted_like_an_so_line(env):
    """The SO push's own rule: a line whose item the CRM does not hold is dropped and counted
    (`lines.dropped`), the rest of the order lands."""
    rec = _records(env.post(CG_INGEST, [_cg(details=[_cg_line(981001),
                                                     _cg_line(981002, "ZZAC-NOPE")])]))[
        "db1:CSGN:980001"]
    assert rec["outcome"] == "created"
    assert rec["lines"]["dropped"] == 1
    assert set(_lines(env, _order(env))) == {"db1:CSGN:980001:981001"}


def test_dry_run_writes_no_order(env):
    res = env.post(CG_INGEST, [_cg()], dry_run=True)
    assert _records(res)["db1:CSGN:980001"]["outcome"] == "created"
    assert _order(env) is None


def test_a_line_without_an_item_is_not_demand(env):
    rec = _cg(details=[_cg_line(981001), _cg_line(981002, item=None)])
    assert _records(env.post(CG_INGEST, [rec]))["db1:CSGN:980001"]["outcome"] == "created"
    assert set(_lines(env, _order(env))) == {"db1:CSGN:980001:981001"}


# ======================================================================= B: returns
def test_a_keyed_return_records_qty_returned_and_nothing_else(env):
    env.post(CG_INGEST, [_cg()])
    before = _line(env, "db1:CSGN:980001:981001")
    planned = (before.qty_ordered, before.qty_delivered, before.qty_required, before.line_status)
    res = env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 4, from_dtl=981001)])])
    assert _records(res)["db1:CSGR:990001"]["outcome"] == "created"

    stored = _return_lines(env, 990001)[991001]
    assert (stored.match_status, stored.match_reason) == ("matched", "line_key")
    line = _line(env, "db1:CSGN:980001:981001")
    assert stored.sales_order_line_id == str(line.id)
    assert line.qty_returned == Decimal("4")
    # Reporting only (ruling 2): no planned, delivered, cancelled or status change.
    assert line.qty_cancelled == Decimal("0")
    assert (line.qty_ordered, line.qty_delivered, line.qty_required, line.line_status) == planned


def test_a_keyless_return_takes_the_groups_one_line(env):
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001), _cg_line(981002, "ZZAC-P2", 5)])])
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 3, item="ZZAC-P2")])])
    stored = _return_lines(env, 990001)[991001]
    assert (stored.match_status, stored.match_reason) == ("matched", "item")
    assert _line(env, "db1:CSGN:980001:981002").qty_returned == Decimal("3")
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("0")


def test_a_keyless_return_over_two_lines_is_undetermined_and_names_none(env):
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001), _cg_line(981002)])])
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 3)])])
    stored = _return_lines(env, 990001)[991001]
    assert (stored.match_status, stored.match_reason) == ("unmatched", "undetermined")
    assert stored.sales_order_line_id is None
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("0")
    assert _line(env, "db1:CSGN:980001:981002").qty_returned == Decimal("0")


def test_a_return_before_its_consignment_waits_then_links(env):
    res = env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 4, from_dtl=981001)])])
    assert "cancel_line_waiting" in _records(res)["db1:CSGR:990001"]["warnings"]
    assert _return_lines(env, 990001)[991001].match_status == "waiting"
    env.post(CG_INGEST, [_cg()])
    assert _return_lines(env, 990001)[991001].match_status == "matched"
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("4")


def test_returns_sum_and_cap_at_ordered(env):
    env.post(CG_INGEST, [_cg()])
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 6, from_dtl=981001)]),
                         _cr(990002, "ZZCR-0002", [_cr_line(992001, 7, from_dtl=981001)])])
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("10")


def test_a_voided_or_swept_return_drops_out(env):
    env.post(CG_INGEST, [_cg()])
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 4, from_dtl=981001)]),
                         _cr(990002, "ZZCR-0002", [_cr_line(992001, 2, from_dtl=981001)])])
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("6")
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 4, from_dtl=981001)], cancelled="T",
                             modified="2026-10-05T10:00:00.000")])
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("2")
    assert _sweep(env, CR_DELETE, [990002]).json()["records"][0]["outcome"] == "deactivated"
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("0")


def test_a_full_return_never_reads_as_cancelled(env):
    env.post(CG_INGEST, [_cg()])
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 10, from_dtl=981001)])])
    line = _line(env, "db1:CSGN:980001:981001")
    assert line.qty_returned == Decimal("10")
    assert line.qty_cancelled == Decimal("0")
    assert line.line_status == "closed"
    assert not line.cancelled_by_cancel_doc


def test_a_dangling_return_key_stays_unmatched(env):
    env.post(CG_INGEST, [_cg()])
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 4, from_dtl=989999)])])
    stored = _return_lines(env, 990001)[991001]
    assert (stored.match_status, stored.match_reason) == ("unmatched", "dangling_line_key")


def test_a_return_never_links_to_a_real_sales_order(env):
    """A return names a consignment; a real SO with the same number or DtlKey is not one."""
    _so(env, so_number="ZZSO-C001", lines=((981001, "p1", 10, 0, "wh1"),))
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 4, from_no="ZZSO-C001",
                                                from_dtl=981001)])])
    stored = _return_lines(env, 990001)[991001]
    assert stored.match_status == "waiting"
    assert stored.sales_order_line_id is None


def test_a_cancel_so_key_never_lands_on_a_consignment_line(env):
    """XS keys match SO lines by `...:{DtlKey}`; a CG line with the same DtlKey is not an SO
    line, so the keyed cancel stays exact instead of turning ambiguous."""
    lines = _so(env, lines=((981001, "p1", 10, 4, "wh1"),))
    env.post(CG_INGEST, [_cg()])
    detail = _detail(951001, from_dtl=981001)
    detail["FromDocNo"] = None
    env.post(XS_INGEST, [_xs(details=[detail])])
    env.db.expire_all()
    stored = env.db.execute(select(AutocountCancelLine).where(
        AutocountCancelLine.dtl_key == 951001)).scalars().one()
    assert (stored.match_status, stored.sales_order_line_id) == ("matched", lines[981001])
    assert _line(env, "db1:CSGN:980001:981001").qty_cancelled == Decimal("0")


def test_a_keyless_return_of_every_unit_out_on_the_group_is_exact_per_line(env):
    """Two SAMPLE lines at CON, one key-less return line each naming the CG: the returns are
    every unit still out on the group, so each line's returned qty is all of it (the #1623
    whole-group arithmetic); no single line is named."""
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, qty=1), _cg_line(981002, qty=1),
                                      _cg_line(981003, "ZZAC-P2", qty=1)])])
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 1), _cr_line(991002, 1)])])
    stored = _return_lines(env, 990001)
    for dtl in (991001, 991002):
        assert (stored[dtl].match_status, stored[dtl].match_reason) == ("matched", "group_whole")
        assert stored[dtl].sales_order_line_id is None
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("1")
    assert _line(env, "db1:CSGN:980001:981002").qty_returned == Decimal("1")
    assert _line(env, "db1:CSGN:980001:981003").qty_returned == Decimal("0")


def test_voiding_part_of_a_whole_group_return_makes_the_rest_undetermined(env):
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, qty=1), _cg_line(981002, qty=1)])])
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 1)]),
                         _cr(990002, "ZZCR-0002", [_cr_line(992001, 1)])])
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("1")
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 1)], cancelled="T",
                             modified="2026-10-05T10:00:00.000")])
    assert _return_lines(env, 990002)[992001].match_reason == "undetermined"
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("0")
    assert _line(env, "db1:CSGN:980001:981002").qty_returned == Decimal("0")


def test_the_return_decision_does_not_depend_on_arrival_order(env):
    cg = _cg(details=[_cg_line(981001, qty=1), _cg_line(981002, qty=1)])
    returns = [_cr(details=[_cr_line(991001, 1)]), _cr(990002, "ZZCR-0002", [_cr_line(992001, 1)])]
    env.post(CR_INGEST, [returns[1]])
    env.post(CG_INGEST, [cg])
    env.post(CR_INGEST, [returns[0]])
    assert _return_lines(env, 990001)[991001].match_reason == "group_whole"
    assert _return_lines(env, 990002)[992001].match_reason == "group_whole"
    assert _line(env, "db1:CSGN:980001:981002").qty_returned == Decimal("1")


def test_the_ss122_wire_shape_end_to_end(env):
    """The consignments / consignment_returns preset shape ss#122 sends (crew, 14 Oct; every
    value here is fake): header Debtor*, SalesAgent, Ref, Description, RefDocNo, CurrencyCode,
    Note (RTF), and lines with DocKey, Description, UOM, UnitPrice, SubTotal, plus the 2.9
    TransferedQty the follow-up SS lane adds. The return's DocNo carries the CG number plus a suffix and is stored as it is (Q4 a)."""
    cg = {"DocKey": 980050, "DocNo": "ZZCG-202606-001", "DocDate": "2026-06-19T00:00:00.000",
          "DebtorCode": "300-ZZAC01", "DebtorName": "ZZ DEALER SDN BHD (PROJECT)",
          "SalesAgent": "ZZ AGENT", "Ref": None, "Description": "STOCK TRANSFER - TO CON",
          "RefDocNo": None, "CurrencyCode": "MYR", "Note": "{\\rtf1\\ansi zz note\\par}",
          "Cancelled": "F", "LastModified": "2026-06-19T11:28:32.560",
          "Details": [{"DocKey": 980050, "DtlKey": 980051, "Seq": 16, "ItemCode": "ZZAC-P1",
                       "Description": "ZZ TAP", "Location": "ZZAC-WH1", "Qty": 1, "UOM": "UNIT",
                       "UnitPrice": 108, "SubTotal": 108, "FromDocType": None,
                       "FromDocNo": None, "FromDocDtlKey": None, "TransferedQty": 1}]}
    cr = {"DocKey": 990050, "DocNo": "ZZCG-202606-001(CVT TO DO)",
          "DocDate": "2026-09-29T00:00:00.000", "DebtorCode": "300-ZZAC01",
          "DebtorName": "ZZ DEALER SDN BHD (PROJECT)", "SalesAgent": "ZZ AGENT", "Ref": None,
          "Description": "STOCK TRANSFER - ", "RefDocNo": None, "CurrencyCode": "MYR",
          "Note": None, "Cancelled": "F", "LastModified": "2026-09-29T11:00:27.963",
          "Details": [{"DocKey": 990050, "DtlKey": 990051, "Seq": 16, "ItemCode": "ZZAC-P1",
                       "Description": "ZZ TAP", "Location": "ZZAC-WH1", "Qty": 1, "UOM": "UNIT",
                       "UnitPrice": 108, "SubTotal": 108, "FromDocType": "CG",
                       "FromDocNo": "ZZCG-202606-001", "FromDocDtlKey": None}]}
    assert _records(env.post(CG_INGEST, [cg]))["db1:CSGN:980050"]["outcome"] == "created"
    assert _records(env.post(CR_INGEST, [cr]))["db1:CSGR:990050"]["outcome"] == "created"

    order = _order(env, "ZZCG-202606-001")
    assert (order.doc_type, order.debtor_code, order.customer_id) == (
        "consignment", "300-ZZAC01", env.customer)
    line = _line(env, "db1:CSGN:980050:980051")
    # Delivered at issue; AutoCount counts the CVT TO DO return in TransferedQty (returned).
    assert (line.qty_ordered, line.qty_delivered, line.unit_price, line.uom) == (
        Decimal("1"), Decimal("1"), Decimal("108"), "UNIT")
    assert line.line_status == "closed"
    assert line.qty_returned == Decimal("1")
    stored = env.db.execute(select(AutocountCancelDoc).where(
        AutocountCancelDoc.doc_key == 990050)).scalars().one()
    assert stored.doc_no == "ZZCG-202606-001(CVT TO DO)"


# ======================================================================= the 2.9 gate (crew, 14 Oct)
def test_a_2_8_consignment_is_stored_only_until_a_push_states_transfers(env):
    """ss#122 ships the 2.8 shape (no TransferedQty). Such a CG is kept as sent and is not
    demand; the first push that carries TransferedQty makes it demand, even with the same
    LastModified (the follow-up SS lane re-sends on its new fingerprint)."""
    first = _cg(details=[_cg_line(981001, transferred=None)])
    assert _records(env.post(CG_INGEST, [first]))["db1:CSGN:980001"]["outcome"] == "created"
    assert _order(env) is None
    assert _records(env.post(CG_INGEST, [copy.deepcopy(first)]))["db1:CSGN:980001"][
        "outcome"] == "unchanged"
    assert _order(env) is None

    stated = _cg(details=[_cg_line(981001, transferred=4)])
    assert _records(env.post(CG_INGEST, [stated]))["db1:CSGN:980001"]["outcome"] == "updated"
    line = _line(env, "db1:CSGN:980001:981001")
    assert (line.qty_ordered, line.qty_delivered, line.qty_returned) == (
        Decimal("10"), Decimal("10"), Decimal("4"))


def test_a_line_without_transfers_keeps_the_whole_record_stored_only(env):
    rec = _cg(details=[_cg_line(981001), _cg_line(981002, "ZZAC-P2", transferred=None)])
    env.post(CG_INGEST, [rec])
    assert _order(env) is None


def test_a_return_naming_a_stored_only_consignment_waits(env):
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=None)])])
    env.post(CR_INGEST, [_cr(details=[_cr_line(991001, 4)])])
    assert _return_lines(env, 990001)[991001].match_status == "waiting"
    env.post(CG_INGEST, [_cg(details=[_cg_line(981001, transferred=4)])])
    assert _return_lines(env, 990001)[991001].match_status == "matched"
    assert _line(env, "db1:CSGN:980001:981001").qty_returned == Decimal("4")


def test_a_consignment_batch_runs_the_sales_order_hooks(env, monkeypatch):
    """The order is written by the SO ingest, so its reactions follow (plan exception batch,
    planning change for CS), read off the consignments batch's own SO ingest."""
    import app.api.v1.external.ingest as ingest

    seen = []
    monkeypatch.setattr(ingest, "_run_plan_exception_hook",
                        lambda db, service, actor: seen.append(("plan", set(service.so_numbers))))
    monkeypatch.setattr(ingest, "_run_planning_change_hook",
                        lambda db, service, actor: seen.append(("change", set(service.so_numbers))))
    env.post(CG_INGEST, [_cg()])
    assert ("plan", {"ZZCG-0001"}) in seen and ("change", {"ZZCG-0001"}) in seen
