"""GET /api/v1/inventory/stock-status - AutoCount's Available Stock Status, read in the CRM.

UAC: documentation/plans/inventory/stock-status-embed-17oct-acceptance-criteria.md (AC-1..AC-12).
Every test seeds its own chain (CI's database is empty) under a fresh company, so nothing the
database already holds can change a figure.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi.testclient import TestClient

from app.models.inventory import Stock, Warehouse
from app.models.order import SalesOrder, SalesOrderLine
from app.models.procurement import (
    InboundShipment,
    InboundShipmentLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
)
from tests.scm.conftest import as_user, grant_permission, requires_pg, seed_user
from tests.scm.test_loading_plan import World
from tests.scm.test_outstanding_import_routes import as_company_user

pytestmark = requires_pg

MARKER = "ZZSS"
URL = "/api/v1/inventory/stock-status"
PLACEHOLDER = date(2030, 1, 1)


def _wh(db, code: str, *, active: bool = True) -> Warehouse:
    wh = Warehouse(
        id=str(uuid.uuid4()),
        warehouse_code=f"{MARKER}-{code}-{uuid.uuid4().hex[:6]}",
        warehouse_name=f"{MARKER} {code}",
        is_active=active,
    )
    db.add(wh)
    db.flush()
    return wh


def _on_hand(db, product, wh, qty) -> None:
    db.add(Stock(id=str(uuid.uuid4()), product_id=product.id, warehouse_id=wh.id, quantity_on_hand=qty))
    db.flush()


def _so(db, product, wh, qty, *, delivered=0, transferable=None, status="open", line_status="open",
        purchasing_status="not_reviewed", qty_required=None, required=None, requested=None,
        debtor=("300-T001", "TEST CUSTOMER"), label=None) -> SalesOrder:
    so = SalesOrder(
        id=str(uuid.uuid4()),
        so_number=f"{MARKER}-SO-{uuid.uuid4().hex[:8]}",
        status=status,
        demand_class="retail",
        order_date=date(2026, 9, 1),
        requested_delivery_date=requested,
        debtor_code=debtor[0],
        debtor_name=debtor[1],
        project_label=label,
        is_transferable=transferable,
    )
    db.add(so)
    db.flush()
    db.add(SalesOrderLine(
        id=str(uuid.uuid4()),
        sales_order_id=so.id,
        product_id=product.id,
        warehouse_id=wh.id,
        qty_ordered=qty,
        qty_delivered=delivered,
        qty_required=qty_required,
        required_date=required,
        line_status=line_status,
        purchasing_status=purchasing_status,
    ))
    db.flush()
    return so


def _po(db, w: World, product, wh, qty, *, received=0, status="active", line_status="open") -> PurchaseOrder:
    po = PurchaseOrder(
        id=str(uuid.uuid4()),
        po_number=f"{MARKER}-PO-{uuid.uuid4().hex[:8]}",
        supplier_id=w.supplier.id,
        issue_date=date(2026, 9, 2),
        expected_date=date(2026, 11, 30),
        status=status,
    )
    db.add(po)
    db.flush()
    db.add(PurchaseOrderLine(
        id=str(uuid.uuid4()),
        purchase_order_id=po.id,
        product_id=product.id,
        warehouse_id=wh.id,
        qty_ordered=qty,
        qty_received=received,
        expected_date=date(2026, 11, 30),
        line_status=line_status,
    ))
    db.flush()
    return po


def _shipment(db, w: World, *, container=None, status="in_transit", eta=date(2026, 10, 20)) -> InboundShipment:
    ship = InboundShipment(
        id=str(uuid.uuid4()),
        shipment_number=f"{MARKER}-PL-{uuid.uuid4().hex[:8]}",
        supplier_id=w.supplier.id,
        shipment_date=date(2026, 9, 20),
        shipment_status=status,
        estimated_arrival_date=eta,
        shipping_container_number=container,
    )
    db.add(ship)
    db.flush()
    return ship


def _pl_line(db, ship, product, shipped, *, received=0) -> None:
    db.add(InboundShipmentLine(
        id=str(uuid.uuid4()),
        shipment_id=ship.id,
        product_id=product.id,
        quantity_shipped=shipped,
        quantity_received=received,
    ))
    db.flush()


def _spo(db, w: World, product, wh, qty, *, shipment=None, container=None, received=0) -> SPOAllocation:
    row = SPOAllocation(
        id=str(uuid.uuid4()),
        spo_number=f"{MARKER}-SPO-{uuid.uuid4().hex[:8]}",
        spo_line_number=1,
        inbound_shipment_id=shipment.id if shipment else None,
        container_number=container,
        product_id=product.id,
        warehouse_id=wh.id,
        allocated_quantity=qty,
        quantity_received=received,
        supplier_id=w.supplier.id,
        issue_date=date(2026, 10, 6),
        expected_date=date(2026, 10, 17),
    )
    db.add(row)
    db.flush()
    return row


def _get(app, product, **params):
    r = TestClient(app).get(URL, params={"product_id": str(product.id), **params})
    assert r.status_code == 200, r.text
    return r.json()


def _row(body, wh):
    return next((r for r in body["locations"] if r["warehouse_id"] == str(wh.id)), None)


def _setup(scm_app):
    app, db, gcu, gcuk = scm_app
    as_company_user(app, db, gcu, gcuk)  # purchasing: holds scm.reorder.run
    w = World(db)
    return app, db, w, w.product("A")


# ---------------------------------------------------------------- AC-1, AC-2, AC-6
def test_rows_cover_inactive_warehouses_skip_empty_ones_and_carry_the_autocount_forecast(scm_app):
    app, db, w, p = _setup(scm_app)
    bb = _wh(db, "BB")
    con = _wh(db, "CON", active=False)
    empty = _wh(db, "EMPTY")
    _on_hand(db, p, bb, 10)
    _on_hand(db, p, con, 1)
    _on_hand(db, p, empty, 0)
    _so(db, p, bb, 30)
    _po(db, w, p, bb, 12)
    _spo(db, w, p, bb, 5)

    body = _get(app, p)

    bb_row, con_row = _row(body, bb), _row(body, con)
    assert _row(body, empty) is None
    assert bb_row["is_active"] is True and con_row["is_active"] is False
    assert (bb_row["on_hand"], bb_row["so_qty"], bb_row["po_qty"], bb_row["spo_qty"]) == (10, 30, 12, 5)
    assert bb_row["forecast"] == 10 - 30 + 12 + 5
    assert con_row["on_hand"] == 1 and con_row["forecast"] == 1
    t = body["totals"]
    assert (t["on_hand"], t["so_qty"], t["po_qty"], t["spo_qty"], t["forecast"]) == (11, 30, 12, 5, -2)
    assert bb_row["warehouse_code"] == bb.warehouse_code


# ---------------------------------------------------------------- AC-3
def test_so_counts_like_autocount_transferable_f_out_covered_in_ordered_not_required(scm_app):
    app, db, w, p = _setup(scm_app)
    bb = _wh(db, "BB")
    _so(db, p, bb, 100, delivered=40)                          # 60
    _so(db, p, bb, 7, transferable=False)                      # out: AutoCount drops F
    _so(db, p, bb, 9, transferable=True)                       # 9
    _so(db, p, bb, 11, purchasing_status="covered")            # 11: AutoCount has no "covered"
    _so(db, p, bb, 50, qty_required=5)                         # 50: ordered, not CS's required
    _so(db, p, bb, 13, line_status="cancelled")                # out
    _so(db, p, bb, 17, status="closed")                        # out
    _so(db, p, bb, 5, delivered=8)                             # over-delivered: 0, never negative

    body = _get(app, p)

    assert _row(body, bb)["so_qty"] == 60 + 9 + 11 + 50
    so_docs = [d for d in body["sales_orders"] if d["warehouse_id"] == str(bb.id)]
    assert sum(d["qty"] for d in so_docs) == 60 + 9 + 11 + 50
    covered = next(d for d in so_docs if d["qty"] == 11)
    assert covered["covered_in_crm"] is True


# ---------------------------------------------------------------- AC-4, AC-5
def test_po_and_spo_count_only_what_is_still_outstanding_on_open_lines(scm_app):
    app, db, w, p = _setup(scm_app)
    bb = _wh(db, "BB")
    _po(db, w, p, bb, 100, received=30)                    # 70
    _po(db, w, p, bb, 20, line_status="cancelled")         # out
    _po(db, w, p, bb, 20, status="cancelled")              # out (header not in the open set)
    _spo(db, w, p, bb, 40, received=15)                    # 25
    _spo(db, w, p, bb, 9, received=9)                      # fully received: out

    body = _get(app, p)

    row = _row(body, bb)
    assert row["po_qty"] == 70
    assert row["spo_qty"] == 25
    assert sum(d["qty"] for d in body["purchase_orders"]) == 70
    assert sum(d["qty"] for d in body["spos"]) == 25


# ---------------------------------------------------------------- AC-7
def test_incoming_pl_counts_only_what_no_spo_already_carries(scm_app):
    app, db, w, p = _setup(scm_app)
    bb = _wh(db, "BB")
    linked = _shipment(db, w)
    _pl_line(db, linked, p, 385)
    _spo(db, w, p, bb, 385, shipment=linked)                 # on SPO by link: counts 0
    by_box = _shipment(db, w, container="SEKU1741573")
    _pl_line(db, by_box, p, 50)
    _spo(db, w, p, bb, 50, container="SEKU1741573")          # on SPO by container: counts 0
    free = _shipment(db, w)
    _pl_line(db, free, p, 118)                               # on no SPO: 118
    partial = _shipment(db, w)
    _pl_line(db, partial, p, 30, received=10)                # 20 to land, 5 on SPO: 15
    _spo(db, w, p, bb, 5, shipment=partial)
    landed = _shipment(db, w, status="fully_received")
    _pl_line(db, landed, p, 99)                              # arrived: out
    draft = _shipment(db, w, status="draft")
    _pl_line(db, draft, p, 77)                               # draft: out

    body = _get(app, p)

    assert body["totals"]["incoming_pl"] == 118 + 15
    by_no = {s["shipment_number"]: s for s in body["incoming_pl"]}
    assert by_no[linked.shipment_number]["counted"] == 0
    assert by_no[by_box.shipment_number]["counted"] == 0
    assert by_no[free.shipment_number]["counted"] == 118
    assert by_no[partial.shipment_number] == {**by_no[partial.shipment_number],
                                              "still_to_land": 20, "on_spo": 5, "counted": 15}
    assert landed.shipment_number not in by_no and draft.shipment_number not in by_no
    assert body["totals"]["forecast_incl_pl"] == body["totals"]["forecast"] + 133


# ---------------------------------------------------------------- AC-8
def test_so_due_by_cuts_dated_so_only_and_never_the_undated_or_placeholder(scm_app):
    app, db, w, p = _setup(scm_app)
    bb = _wh(db, "BB")
    _so(db, p, bb, 10, required=date(2026, 10, 15))                  # in
    _so(db, p, bb, 20, required=date(2026, 12, 15))                  # out by Nov
    _so(db, p, bb, 30, requested=date(2026, 11, 20))                 # header date, in
    _so(db, p, bb, 40)                                               # no date: always
    _so(db, p, bb, 50, required=PLACEHOLDER)                         # 2030-01-01: always
    _po(db, w, p, bb, 7)

    body = _get(app, p, so_due_by="2026-11-30")

    row = _row(body, bb)
    assert row["so_qty"] == 10 + 30 + 40 + 50
    assert row["po_qty"] == 7
    placeholder = next(d for d in body["sales_orders"] if d["qty"] == 50)
    assert placeholder["due_date"] is None
    assert _row(_get(app, p), bb)["so_qty"] == 150


# ---------------------------------------------------------------- AC-9
def test_documents_carry_what_the_panel_prints_and_links_by(scm_app):
    app, db, w, p = _setup(scm_app)
    bb = _wh(db, "BB")
    so = _so(db, p, bb, 12, debtor=("300-K032", "KEE LIN TRDG"), label="MANDARIN PR",
             required=date(2026, 10, 31))
    po = _po(db, w, p, bb, 8)
    spo = _spo(db, w, p, bb, 4, container="TGHU8800001")

    body = _get(app, p)

    s = body["sales_orders"][0]
    assert (s["so_number"], s["sales_order_id"], s["debtor_code"], s["debtor_name"], s["project_label"],
            s["doc_date"], s["due_date"], s["qty"], s["warehouse_id"]) == (
        so.so_number, str(so.id), "300-K032", "KEE LIN TRDG", "MANDARIN PR",
        "2026-09-01", "2026-10-31", 12, str(bb.id))
    o = body["purchase_orders"][0]
    assert (o["po_number"], o["purchase_order_id"], o["supplier_code"], o["qty"]) == (
        po.po_number, str(po.id), w.supplier.supplier_code, 8)
    x = body["spos"][0]
    assert (x["spo_number"], x["spo_line_number"], x["container_number"], x["supplier_code"], x["qty"]) == (
        spo.spo_number, 1, "TGHU8800001", w.supplier.supplier_code, 4)


# ---------------------------------------------------------------- AC-11
def test_access_any_embedding_screen_permission_reads_others_are_refused(scm_app):
    app, db, gcu, gcuk = scm_app
    w = World(db)
    p = w.product("A")
    as_user(app, gcu, gcuk, seed_user(db, "guest"))
    assert TestClient(app).get(URL, params={"product_id": str(p.id)}).status_code == 403

    grant_permission(db, "salesperson", "projects.projects.view")
    as_user(app, gcu, gcuk, seed_user(db, "salesperson"))
    assert TestClient(app).get(URL, params={"product_id": str(p.id)}).status_code == 200


def test_a_product_id_that_is_not_a_uuid_or_not_ours_is_404(scm_app):
    app, db, w, p = _setup(scm_app)
    assert TestClient(app).get(URL, params={"product_id": "SRTWB1515"}).status_code == 404
    assert TestClient(app).get(URL, params={"product_id": str(uuid.uuid4())}).status_code == 404


def test_another_companys_warehouse_never_appears(scm_app):
    from app.models.base import set_company_scope

    app, db, w, p = _setup(scm_app)
    mine = _wh(db, "MINE")
    _on_hand(db, p, mine, 3)
    theirs_company = str(uuid.uuid4())
    from app.models.company import Company
    db.add(Company(id=theirs_company, name=f"{MARKER} other {theirs_company[:6]}",
                   code=f"{MARKER}O{uuid.uuid4().hex[:6]}".upper(), is_active=True))
    db.flush()
    theirs = Warehouse(id=str(uuid.uuid4()), warehouse_code=f"{MARKER}-THEIRS-{uuid.uuid4().hex[:6]}",
                       warehouse_name="other", is_active=True, company_id=theirs_company)
    db.add(theirs)
    db.flush()
    db.add(Stock(id=str(uuid.uuid4()), product_id=p.id, warehouse_id=theirs.id, quantity_on_hand=99,
                 company_id=theirs_company))
    db.flush()

    body = _get(app, p)

    assert _row(body, mine) is not None
    assert _row(body, theirs) is None
    assert body["totals"]["on_hand"] == 3


# ---------------------------------------------------------------- summary (item chips in lists)
SUMMARY_URL = "/api/v1/inventory/stock-status/summary"


def test_summary_gives_each_item_its_on_hand_and_so_on_the_panels_rules_in_one_call(scm_app):
    app, db, w, p = _setup(scm_app)
    q = w.product("B")
    bb = _wh(db, "BB")
    con = _wh(db, "CON", active=False)
    _on_hand(db, p, bb, 10)
    _on_hand(db, p, con, 1)                       # inactive still counts
    _so(db, p, bb, 30)
    _so(db, p, bb, 7, transferable=False)         # F out
    _so(db, p, bb, 4, purchasing_status="covered")
    _on_hand(db, q, bb, 2)

    r = TestClient(app).get(SUMMARY_URL, params={"product_ids": f"{p.id},{q.id}"})

    assert r.status_code == 200, r.text
    by_id = {s["product_id"]: s for s in r.json()}
    assert by_id[str(p.id)] == {"product_id": str(p.id), "on_hand": 11, "so_qty": 34}
    assert by_id[str(q.id)] == {"product_id": str(q.id), "on_hand": 2, "so_qty": 0}
    # The same figures the panel prints for the item.
    panel = _get(app, p)["totals"]
    assert (panel["on_hand"], panel["so_qty"]) == (11, 34)


def test_summary_refuses_more_than_200_ids_and_ignores_ids_that_are_not_uuids(scm_app):
    app, db, w, p = _setup(scm_app)
    too_many = ",".join(str(uuid.uuid4()) for _ in range(201))
    assert TestClient(app).get(SUMMARY_URL, params={"product_ids": too_many}).status_code == 422
    r = TestClient(app).get(SUMMARY_URL, params={"product_ids": f"nope,{p.id}"})
    assert r.status_code == 200
    assert [s["product_id"] for s in r.json()] == [str(p.id)]
