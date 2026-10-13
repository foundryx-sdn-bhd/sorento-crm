"""AutoCount Cancel SO (XS) and Cancel PO (XP) on the ingest surface (AC-CANCEL-CRM, contract 2.8).

Plan: documentation/plans/autocount/PLAN-autocount-cancel-crm-13oct.md (rules R1-R7).

Owner rulings 13 Oct: cancelled qty is DERIVED on every sync (sum of the cancel-doc lines
matched to a line, live docs only); a line is cancelled only when fully cancelled; AutoCount is
truth, so a cancel line matches by its FromDocDtlKey, else by doc no + item (+ location + qty),
and anything ambiguous or dangling goes to review, never a guess.

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
            source_ref=f"SRT_DB:4001:{dtl}", company_id=env.company,
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
    assert stored.match_status == "needs_review"
    assert stored.match_reason == "dangling_line_key"
    assert stored.sales_order_line_id is None
    assert _so_line(env, lines[5001]).qty_cancelled == Decimal("0")


def test_no_key_one_line_of_the_item_matches(env):
    lines = _so(env, lines=((5001, "p1", 10, 4, "wh1"), (5002, "p2", 5, 0, "wh1")))
    _push_xs(env, [_xs(details=[_detail(951001, item="ZZAC-P1", from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert stored.match_status == "matched"
    assert stored.sales_order_line_id == lines[5001]


def test_no_key_location_narrows(env):
    _wh2(env)
    lines = _so(env, lines=((5001, "p1", 10, 4, "wh1"), (5002, "p1", 10, 4, "wh2")))
    _push_xs(env, [_xs(details=[_detail(951001, location="ZZAC-WH2", from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert stored.match_status == "matched"
    assert stored.sales_order_line_id == lines[5002]


def test_no_key_same_item_same_location_is_ambiguous(env):
    lines = _so(env, lines=((5001, "p1", 12, 0, "wh1"), (5002, "p1", 12, 0, "wh1")))
    _push_xs(env, [_xs(details=[_detail(951001, qty=12, from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert stored.match_status == "needs_review"
    assert stored.match_reason == "ambiguous"
    assert sorted(stored.candidate_line_ids) == sorted(lines.values())
    assert all(_so_line(env, i).qty_cancelled == Decimal("0") for i in lines.values())


def test_no_key_item_not_on_the_so_goes_to_review(env):
    _so(env)
    _push_xs(env, [_xs(details=[_detail(951001, item="ZZAC-P2", from_dtl=0)])])
    stored = _cancel_lines(env, 950001)[951001]
    assert stored.match_status == "needs_review"
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


# ======================================================================= contract
def test_contract_2_8_lists_the_cancel_entities(env):
    body = env.client.get("/api/v1/external/contract").json()
    assert body["version"] == "2.8"
    assert {"cancel_sales_orders", "cancel_purchase_orders"} <= set(body["entities"])


def test_cancel_slugs_are_the_order_slugs(env):
    from app.api.v1.external.ingest import DELETE_PERMISSIONS, INGEST_PERMISSIONS

    assert INGEST_PERMISSIONS["cancel_sales_orders"] == "scm.sales_orders.edit"
    assert INGEST_PERMISSIONS["cancel_purchase_orders"] == "scm.purchase_orders.edit"
    assert DELETE_PERMISSIONS["cancel_sales_orders"] == "scm.sales_orders.delete"
