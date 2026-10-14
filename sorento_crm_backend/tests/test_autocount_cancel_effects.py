"""AC-CANCEL-CRM: what a derived cancelled qty does to the rest of the CRM.

Plan: documentation/plans/autocount/PLAN-autocount-cancel-crm-13oct.md (R4-R6, owner Q1 a, Q4 a).

- The SO push (canonical `sales_orders`) keeps a fully Cancel-SO'd line `cancelled` and links a
  cancel line that was waiting for that SO.
- A full cancel retires the line's OI rows through the #1530 path; a partial one raises the
  AutoCount planning change for CS (#1599 flow), never a silent trim.
- Readers: planned qty and the delivered figure leave the cancelled part out; outstanding
  (ordered - TransferedQty) does not change.

Reuses `tests/test_ingest_documents.py`'s `env` (canonical SO push, company A, a product).
"""
from __future__ import annotations

from decimal import Decimal
from sqlalchemy import select

from app.models.order import SalesOrderLine

from tests.test_ingest_documents import (  # noqa: F401 - env is a fixture
    INGEST_SO,
    _code_of_ref,
    _so_line,
    _so_record,
    env,
)

XS_INGEST = "/api/v1/external/ingest/cancel_sales_orders"


def _push_so(env, number, *, ordered=10, delivered=0, dtl=5001):
    record = _so_record(env, number=number, ref=f"ZZTDOC:SO:{number}", lines=[
        _so_line(env, ref=f"SRT_DB:4001:{dtl}", qty_ordered=ordered, qty_delivered=delivered)])
    res = env.post(INGEST_SO, [record])
    assert res.status_code == 200, res.text
    assert res.json()["records"][0]["outcome"] in ("created", "updated", "unchanged"), res.text
    return record


def _push_xs(env, so_number, qty, *, dtl=5001, doc_key=950001, modified="2026-10-01T10:00:00"):
    record = {"DocKey": doc_key, "DocNo": f"ZZXS-{doc_key}", "DocDate": "2026-10-01",
              "Cancelled": "F", "LastModified": modified,
              "Details": [{"DtlKey": doc_key + 1000, "Seq": 1,
                           "ItemCode": _code_of_ref(env, env.product_ref), "Qty": qty,
                           "FromDocType": "SO", "FromDocNo": so_number, "FromDocDtlKey": dtl}]}
    res = env.client.post(XS_INGEST, json={"companyCode": env.company_a_code, "book": "db1",
                                           "records": [record]})
    assert res.status_code == 200, res.text
    return res


def _line(env, so_number) -> SalesOrderLine:
    env.db.expire_all()
    from app.models.order import SalesOrder

    return env.db.execute(
        select(SalesOrderLine).join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
        .where(SalesOrder.so_number == so_number)
    ).scalars().one()


# ======================================================================= the SO push
def test_so_push_keeps_a_fully_cancelled_line_cancelled(env, monkeypatch):
    """Review B2: and a re-push neither retires / flags the OI rows again nor raises a
    planning change (without the push guard the line would go closed and back)."""
    import app.services.project_order_inquiry_service as oi
    from app.services import planning_change_service

    retired, flagged, diffs = [], [], []
    monkeypatch.setattr(oi.ProjectOrderInquiryService, "retire_rows_for_cancelled_lines",
                        lambda self, cancelled, actor_user_id=None: retired.append(1) or 0)
    real_flag = oi.flag_rows_for_cancelled_lines
    monkeypatch.setattr(oi, "flag_rows_for_cancelled_lines",
                        lambda db, ids: flagged.append(list(ids)) or real_flag(db, ids))
    monkeypatch.setattr(planning_change_service, "build_batch",
                        lambda db, diff, **kw: diffs.append(
                            [c for c in diff.changes if c.kind != "unchanged"]) or None)
    _push_so(env, "ZZ-CX-SO1", ordered=10, delivered=10)
    _push_xs(env, "ZZ-CX-SO1", 10)
    assert _line(env, "ZZ-CX-SO1").line_status == "cancelled"
    assert len(retired) == 1
    retired.clear(), flagged.clear(), diffs.clear()
    # AutoCount pushes the SO again (TransferedQty 10 of 10): the line must not read closed.
    _push_so(env, "ZZ-CX-SO1", ordered=10, delivered=10)
    line = _line(env, "ZZ-CX-SO1")
    assert line.line_status == "cancelled"
    assert line.qty_cancelled == Decimal("10")
    assert retired == [] and flagged == []
    assert all(changes == [] for changes in diffs)


def test_an_so_push_that_lowers_ordered_onto_the_cancel_retires_the_rows(env, monkeypatch):
    """Review should-fix 1: 4 of 10 cancelled, then AutoCount lowers ordered to 4: the line
    is now fully cancelled and its OI rows are retired like any full cancel."""
    import app.services.project_order_inquiry_service as oi

    retired = []
    monkeypatch.setattr(oi.ProjectOrderInquiryService, "retire_rows_for_cancelled_lines",
                        lambda self, cancelled, actor_user_id=None: retired.append(
                            list(cancelled)) or 0)
    _push_so(env, "ZZ-CX-SO8", ordered=10, delivered=4)
    _push_xs(env, "ZZ-CX-SO8", 4)
    assert retired == []
    _push_so(env, "ZZ-CX-SO8", ordered=4, delivered=4)
    line = _line(env, "ZZ-CX-SO8")
    assert line.line_status == "cancelled"
    assert retired == [[(str(line.id), "ZZXS-950001")]]


def test_the_so_push_planning_diff_reads_the_planned_basis(env, monkeypatch):
    """Review M14: an AutoCount qty edit on a partly cancelled line is diffed on what is
    planned (ordered - cancelled), 6 -> 8, not 10 -> 12."""
    from app.services import planning_change_service

    seen = []
    monkeypatch.setattr(planning_change_service, "build_batch",
                        lambda db, diff, **kw: seen.append(diff) or None)
    _push_so(env, "ZZ-CX-SO9", ordered=10, delivered=4)
    _push_xs(env, "ZZ-CX-SO9", 4)
    seen.clear()
    _push_so(env, "ZZ-CX-SO9", ordered=12, delivered=4)
    changes = [c for d in seen for c in d.changes if c.kind != "unchanged"]
    assert [(c.before.qty, c.after.qty) for c in changes] == [(6.0, 8.0)]


def test_so_push_links_a_waiting_cancel_line(env):
    _push_xs(env, "ZZ-CX-SO2", 4)
    _push_so(env, "ZZ-CX-SO2", ordered=10, delivered=4)
    line = _line(env, "ZZ-CX-SO2")
    assert line.qty_cancelled == Decimal("4")
    assert line.line_status == "open"


def test_so_push_raising_ordered_recaps_and_reopens(env):
    _push_so(env, "ZZ-CX-SO3", ordered=10, delivered=10)
    _push_xs(env, "ZZ-CX-SO3", 10)
    assert _line(env, "ZZ-CX-SO3").line_status == "cancelled"
    _push_so(env, "ZZ-CX-SO3", ordered=12, delivered=10)
    line = _line(env, "ZZ-CX-SO3")
    assert line.qty_cancelled == Decimal("10")
    assert line.line_status == "open"


# ======================================================================= OI reactions
def test_full_cancel_retires_the_oi_rows_by_the_board_cancel_path(env, monkeypatch):
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    calls = []
    monkeypatch.setattr(
        ProjectOrderInquiryService, "retire_rows_for_cancelled_lines",
        lambda self, cancelled, actor_user_id=None: calls.append(list(cancelled)) or 0,
    )
    _push_so(env, "ZZ-CX-SO4", ordered=36, delivered=36)
    _push_xs(env, "ZZ-CX-SO4", 36)
    line = _line(env, "ZZ-CX-SO4")
    assert calls == [[(str(line.id), "ZZXS-950001")]]
    # a second sync of the same cancel retires nothing new
    _push_xs(env, "ZZ-CX-SO4", 36, modified="2026-10-02T10:00:00")
    assert len(calls) == 1


def test_partial_cancel_raises_a_planning_change_not_a_trim(env, monkeypatch):
    from app.services import planning_change_service

    seen = []
    monkeypatch.setattr(planning_change_service, "build_batch",
                        lambda db, diff, **kw: seen.append(diff) or None)
    _push_so(env, "ZZ-CX-SO5", ordered=10, delivered=4)
    seen.clear()
    _push_xs(env, "ZZ-CX-SO5", 4)
    changes = [c for d in seen for c in d.changes if c.kind != "unchanged"]
    assert len(changes) == 1
    assert (changes[0].before.qty, changes[0].after.qty) == (10.0, 6.0)
    assert changes[0].after.row_ref == str(_line(env, "ZZ-CX-SO5").id)


# ======================================================================= readers
def test_planned_and_delivered_leave_the_cancel_out_outstanding_does_not_move():
    from app.services.project_supply_service import _open_of, delivered_qty_of, plan_qty_of

    line = SalesOrderLine(qty_ordered=Decimal("10"), qty_delivered=Decimal("10"),
                          qty_cancelled=Decimal("6"), line_status="closed")
    assert plan_qty_of(line) == Decimal("4")
    assert delivered_qty_of(line) == Decimal("4")
    assert _open_of(line) == Decimal("0")
    line.qty_required = Decimal("8")
    assert plan_qty_of(line) == Decimal("2")


def test_demand_plan_qty_sql_leaves_the_cancel_out(env):
    from app.services.scm.demand import plan_qty

    _push_so(env, "ZZ-CX-SO6", ordered=10, delivered=4)
    _push_xs(env, "ZZ-CX-SO6", 4)
    line = _line(env, "ZZ-CX-SO6")
    value = env.db.execute(
        select(plan_qty()).where(SalesOrderLine.id == line.id)
    ).scalar()
    assert Decimal(value) == Decimal("6")


def test_chatbot_reads_ordered_and_delivered_net_of_the_cancel():
    from app.services.chatbot.so_status import _net_of_cancel

    assert _net_of_cancel(Decimal("10"), Decimal("10"), Decimal("6")) == (Decimal("4"),
                                                                          Decimal("4"))
    assert _net_of_cancel(Decimal("10"), Decimal("3"), None) == (Decimal("10"), Decimal("3"))


def test_so_detail_shows_delivered_without_the_cancel(env):
    from app.services.scm.sales_order_service import SalesOrderService

    _push_so(env, "ZZ-CX-SO7", ordered=10, delivered=10)
    _push_xs(env, "ZZ-CX-SO7", 6)
    so_id = str(_line(env, "ZZ-CX-SO7").sales_order_id)
    lines = SalesOrderService(env.db).get(so_id)["lines"]
    assert lines[0]["qty_delivered"] == 4.0
    assert lines[0]["qty_cancelled"] == 6.0
    assert lines[0]["outstanding_qty"] == 0.0


# ======================================================================= review round 2
def test_a_full_cancel_after_the_push_raised_ordered_still_cancels(env, monkeypatch):
    """Review N1: XS 10 + XS 2 on 10 ordered (capped, cancelled), then AutoCount raises the line
    to 12 ordered / 12 transferred: the push reopens it, the relink derives 12 of 12 and the
    line must read cancelled again, not stay closed. N1b: that re-cancel is not a NEW one, so
    the OI rows are retired and flagged once over the whole story, never again."""
    import app.services.project_order_inquiry_service as oi

    retired, flagged = [], []
    monkeypatch.setattr(oi.ProjectOrderInquiryService, "retire_rows_for_cancelled_lines",
                        lambda self, cancelled, actor_user_id=None: retired.append(1) or 0)
    monkeypatch.setattr(oi, "flag_rows_for_cancelled_lines",
                        lambda db, ids: flagged.append(list(ids)) or 0)
    _push_so(env, "ZZ-CX-SN1", ordered=10, delivered=10)
    _push_xs(env, "ZZ-CX-SN1", 10)
    _push_xs(env, "ZZ-CX-SN1", 2, doc_key=950002)
    assert _line(env, "ZZ-CX-SN1").line_status == "cancelled"
    _push_so(env, "ZZ-CX-SN1", ordered=12, delivered=12)
    line = _line(env, "ZZ-CX-SN1")
    assert (line.qty_cancelled, line.line_status) == (Decimal("12"), "cancelled")
    assert len(retired) == 1
    assert flagged == [[str(line.id)]]


def test_the_partial_cancel_change_reads_the_cs_required_basis(env, monkeypatch):
    """Review SF5: CS stated 8 of 10; a 4 cancel moves the plan 8 -> 4, as the board plans."""
    from app.services import planning_change_service

    seen = []
    monkeypatch.setattr(planning_change_service, "build_batch",
                        lambda db, diff, **kw: seen.append(diff) or None)
    _push_so(env, "ZZ-CX-SN2", ordered=10, delivered=4)
    line = _line(env, "ZZ-CX-SN2")
    line.qty_required = Decimal("8")
    env.db.commit()
    seen.clear()
    _push_xs(env, "ZZ-CX-SN2", 4)
    changes = [c for d in seen for c in d.changes if c.kind != "unchanged"]
    assert [(c.before.qty, c.after.qty) for c in changes] == [(8.0, 4.0)]


def test_po_detail_shows_received_without_the_cancel(env):
    """Review SF4: Cancel PO 6 of a 10 / 10 PO line reads received 4, cancelled 6."""
    from app.services.scm.purchase_order_service import PurchaseOrderService
    from tests.test_ingest_documents import _po_line, _po_record

    record = _po_record(env, number="ZZ-CX-PO1", ref="ZZTDOC:PO:ZZ-CX-PO1", lines=[
        _po_line(env, ref="SRT_DB:7001:7101", qty_ordered=10, qty_received=10)])
    assert env.post("/api/v1/external/ingest/purchase_orders", [record]).status_code == 200
    xp = {"DocKey": 960001, "DocNo": "ZZXP-960001", "DocDate": "2026-10-01", "Cancelled": "F",
          "LastModified": "2026-10-01T10:00:00",
          "Details": [{"DtlKey": 961001, "Seq": 1, "ItemCode": _code_of_ref(env, env.product_ref),
                       "Qty": 6, "FromDocType": "PO", "FromDocNo": "ZZ-CX-PO1",
                       "FromDocDtlKey": 7101}]}
    res = env.client.post("/api/v1/external/ingest/cancel_purchase_orders", json={
        "companyCode": env.company_a_code, "book": "db1", "records": [xp]})
    assert res.status_code == 200, res.text
    from app.models.procurement import PurchaseOrder

    env.db.expire_all()
    po_id = env.db.execute(select(PurchaseOrder.id).where(
        PurchaseOrder.po_number == "ZZ-CX-PO1")).scalar()
    line = PurchaseOrderService(env.db).get_one(str(po_id))["lines"][0]
    assert (line["qty_received"], line["qty_cancelled"]) == (4.0, 6.0)


# ======================================================================= owner hand test (13 Oct)
def test_so_detail_names_the_cancel_documents_and_how_they_matched(env):
    """Owner: "where do I see the cancelled quantity?" Each SO line carries the cancel docs
    matched to it, with the qty and the match reason, through the response schema."""
    from app.schemas.scm_orders import SalesOrderLine as LineSchema
    from app.services.scm.sales_order_service import SalesOrderService

    _push_so(env, "ZZ-CX-SQ1", ordered=10, delivered=10)
    _push_xs(env, "ZZ-CX-SQ1", 6)
    so_id = str(_line(env, "ZZ-CX-SQ1").sales_order_id)
    line = SalesOrderService(env.db).get(so_id)["lines"][0]
    dumped = LineSchema.model_validate(line).model_dump()
    assert dumped["qty_cancelled"] == 6.0
    assert dumped["cancels"] == [{"doc_no": "ZZXS-950001", "qty": 6.0, "match_reason": "line_key"}]


def test_so_detail_leaves_out_a_voided_cancel_document(env):
    from app.services.scm.sales_order_service import SalesOrderService

    _push_so(env, "ZZ-CX-SQ2", ordered=10, delivered=10)
    _push_xs(env, "ZZ-CX-SQ2", 6)
    record = {"DocKey": 950001, "DocNo": "ZZXS-950001", "DocDate": "2026-10-01",
              "Cancelled": "T", "LastModified": "2026-10-02T10:00:00", "Details": [
                  {"DtlKey": 951001, "Seq": 1, "ItemCode": _code_of_ref(env, env.product_ref),
                   "Qty": 6, "FromDocType": "SO", "FromDocNo": "ZZ-CX-SQ2", "FromDocDtlKey": 5001}]}
    env.client.post(XS_INGEST, json={"companyCode": env.company_a_code, "book": "db1",
                                     "records": [record]})
    so_id = str(_line(env, "ZZ-CX-SQ2").sales_order_id)
    line = SalesOrderService(env.db).get(so_id)["lines"][0]
    assert line["cancels"] == [] and line["qty_cancelled"] == 0.0


def test_po_detail_names_the_cancel_documents(env):
    from app.models.procurement import PurchaseOrder
    from app.schemas.scm_orders import PurchaseOrderLine as LineSchema
    from app.services.scm.purchase_order_service import PurchaseOrderService
    from tests.test_ingest_documents import _po_line, _po_record

    record = _po_record(env, number="ZZ-CX-PQ1", ref="ZZTDOC:PO:ZZ-CX-PQ1", lines=[
        _po_line(env, ref="SRT_DB:7001:7101", qty_ordered=10, qty_received=10)])
    env.post("/api/v1/external/ingest/purchase_orders", [record])
    xp = {"DocKey": 960001, "DocNo": "ZZXP-960001", "DocDate": "2026-10-01", "Cancelled": "F",
          "LastModified": "2026-10-01T10:00:00",
          "Details": [{"DtlKey": 961001, "Seq": 1, "ItemCode": _code_of_ref(env, env.product_ref),
                       "Qty": 3, "FromDocType": "PO", "FromDocNo": "ZZ-CX-PQ1",
                       "FromDocDtlKey": 0}]}
    env.client.post("/api/v1/external/ingest/cancel_purchase_orders", json={
        "companyCode": env.company_a_code, "book": "db1", "records": [xp]})
    env.db.expire_all()
    po_id = env.db.execute(select(PurchaseOrder.id).where(
        PurchaseOrder.po_number == "ZZ-CX-PQ1")).scalar()
    line = PurchaseOrderService(env.db).get_one(str(po_id))["lines"][0]
    assert LineSchema.model_validate(line).model_dump()["cancels"] == [
        {"doc_no": "ZZXP-960001", "qty": 3.0, "match_reason": "item"}]


def test_so_detail_lists_the_cancel_documents_oldest_first(env):
    """Review nit: the tooltip reads the documents in the order they were raised."""
    from app.services.scm.sales_order_service import SalesOrderService

    _push_so(env, "ZZ-CX-SQ3", ordered=10, delivered=10)
    _push_xs(env, "ZZ-CX-SQ3", 2, doc_key=950009)
    _push_xs(env, "ZZ-CX-SQ3", 3, doc_key=950002)
    so_id = str(_line(env, "ZZ-CX-SQ3").sales_order_id)
    line = SalesOrderService(env.db).get(so_id)["lines"][0]
    assert [c["doc_no"] for c in line["cancels"]] == ["ZZXS-950002", "ZZXS-950009"]
