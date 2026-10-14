"""AutoCount Consignment (CG) and Consignment Return on the CRM side (AC-CONSIGN-CRM).

Plan: documentation/plans/autocount/PLAN-autocount-consign-crm-14oct.md.

Owner rulings 13 Oct, crew answers 14 Oct:
- A consignment is a sales order with `doc_type = consignment`, written by the same SO ingest
  (customer ladder, demand class, line sync, status). AutoCount is truth: outstanding is
  Qty - TransferedQty as sent (Q2 a), the 2.8 shape (no TransferedQty) reads 0 delivered.
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
def _cg_line(dtl_key, item="ZZAC-P1", qty=10.0, *, location="ZZAC-WH1", seq=None, **extra):
    row = {"DtlKey": dtl_key, "Seq": seq if seq is not None else dtl_key % 1000,
           "ItemCode": item, "Location": location, "Qty": qty,
           "FromDocType": None, "FromDocNo": None, "FromDocDtlKey": None}
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
    assert order.status == "open"
    lines = _lines(env, order)
    assert set(lines) == {"db1:CSGN:980001:981001", "db1:CSGN:980001:981002"}
    a = lines["db1:CSGN:980001:981001"]
    assert (a.product_id, a.warehouse_id) == (env.p1, env.wh1)
    # 2.8 sends no TransferedQty: nothing delivered, the whole Qty is outstanding.
    assert (a.qty_ordered, a.qty_delivered, a.line_status) == (Decimal("10"), Decimal("0"), "open")
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
                                TransferedQty=10)],
              DebtorCode="300-ZZAC01", DebtorName="ZZ Dealer Sdn Bhd")
    assert _records(env.post(CG_INGEST, [rec]))["db1:CSGN:980001"]["outcome"] == "created"
    order = _order(env)
    assert (order.debtor_code, order.debtor_name) == ("300-ZZAC01", "ZZ Dealer Sdn Bhd")
    line = _line(env, "db1:CSGN:980001:981001")
    assert line.unit_price == Decimal("12.5")
    assert line.line_total == Decimal("125")
    assert line.uom == "PCS"
    # Q2 a: Qty - TransferedQty as AutoCount sends it; all transferred reads closed.
    assert line.qty_delivered == Decimal("10")
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


def test_an_unknown_item_is_retryable_and_writes_no_order(env):
    rec = _records(env.post(CG_INGEST, [_cg(details=[_cg_line(981001, "ZZAC-NOPE")])]))[
        "db1:CSGN:980001"]
    assert rec["outcome"] == "retryable"
    assert _order(env) is None


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
    assert line.line_status == "open"
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
