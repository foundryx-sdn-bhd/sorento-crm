"""AutoCount's "Available Stock Status" for one item, read in the CRM (lane STOCK-STATUS).

Plan: documentation/plans/inventory/PLAN-stock-status-embed-17oct.md (rules R1-R12).
UAC:  documentation/plans/inventory/stock-status-embed-17oct-acceptance-criteria.md.

AutoCount is the truth and this module only reads. The figures follow AutoCount's report,
NOT the fulfilment board, wherever the two differ, and each difference is a ruling:

* **Every location AutoCount shows** (Q6 b): warehouses inactive in the CRM are listed and
  counted, tagged by `is_active`. `location_stock_for_product` reads active ones only.
* **SO the AutoCount way** (Q3 b, R4): ordered less delivered over open lines of open orders
  (AutoCount's delivered already includes cancelled qty), a line CS marked `covered` counts,
  `qty_required` is ignored, and an order AutoCount marks Transferable = F (not approved for the
  queue) is left out. The board's `demand.is_open_demand()` does the opposite on all three, so
  the board's SO can read lower than this panel's.
* **Forecast = On hand - SO + PO + SPO** (Q1), AutoCount's own column, confirmed row by row
  against SRTWB1515.
* **Incoming PL is only what no SPO already carries** (R3). `PL_UNALLOCATED_SQL` subtracts a
  STORED allocated quantity that is 0 on packing lists SPO rows do link to, so it counts those
  units twice (686 against a true 227 for SRTWB1515). Here an SPO row is "on" a packing list
  when it names the shipment or carries its container number.

PO and SPO use the predicates every other screen uses (`OPEN_PO_STATUSES`,
`spo_supply.open_incoming_clauses`), so supply means one thing everywhere.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.inventory import Stock, Warehouse
from app.models.order import SalesOrder, SalesOrderLine
from app.models.procurement import (
    InboundShipment,
    InboundShipmentLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SPOAllocation,
    Supplier,
)
from app.models.product import Product
from app.models.sales_agent import SalesAgent
from app.services.error_handler import AppException
from app.services.scm import spo_supply
from app.services.scm.container_request_service import OPEN_PO_STATUSES
from app.services.scm.location_stock_service import _stock_as_of
from app.services.scm.proforma_invoice_service import _DRAFT_SHIPMENT_STATUS
from app.services.scm.supplier_scope import is_uuid

#: AutoCount's "no date yet" placeholder (printed 3001). A line carrying it is undated: it
#: counts under every horizon and its due date reads null.
PLACEHOLDER_DATE = date(2030, 1, 1)

#: Shipment states that mean the goods have landed, as `PL_NOT_ARRIVED_SQL` spells them.
_LANDED_SHIPMENT_STATES = ("fully_received", "closed")

_ZERO = Decimal("0")


def _num(value: Any) -> float:
    return float(value or 0)


def _due(line_date: Optional[date], header_date: Optional[date]) -> Optional[date]:
    """The date a line is wanted, or None when it has none (placeholder included)."""
    value = line_date or header_date
    if value is None or value == PLACEHOLDER_DATE:
        return None
    return value


def _iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


def stock_status(db: Session, product_id: str, so_due_by: Optional[date] = None) -> dict[str, Any]:
    """Every location of the caller's company for one product, with the documents behind it."""
    if not is_uuid(product_id):
        raise AppException(404, "Product not found")
    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        raise AppException(404, "Product not found")

    warehouses = {
        str(w.id): w
        for w in db.query(Warehouse.id, Warehouse.warehouse_code, Warehouse.warehouse_name,
                          Warehouse.is_active).all()
    }

    on_hand: dict[str, float] = defaultdict(float)
    for wid, qty in (
        db.query(Stock.warehouse_id, Stock.quantity_on_hand)
        .filter(Stock.product_id == product_id)
        .all()
    ):
        on_hand[str(wid)] += _num(qty)

    sales_orders = _sales_order_rows(db, product_id, so_due_by)
    purchase_orders = _purchase_order_rows(db, product_id)
    spos = _spo_rows(db, product_id)
    incoming_pl = _incoming_pl(db, product_id)

    so_qty: dict[str, float] = defaultdict(float)
    po_qty: dict[str, float] = defaultdict(float)
    spo_qty: dict[str, float] = defaultdict(float)
    for row in sales_orders:
        so_qty[row["warehouse_id"]] += row["qty"]
    for row in purchase_orders:
        po_qty[row["warehouse_id"]] += row["qty"]
    for row in spos:
        spo_qty[row["warehouse_id"]] += row["qty"]

    locations = []
    for wid, wh in warehouses.items():
        figures = (on_hand.get(wid, 0.0), so_qty.get(wid, 0.0), po_qty.get(wid, 0.0),
                   spo_qty.get(wid, 0.0))
        if not any(figures):
            continue
        oh, so, po, spo = figures
        locations.append({
            "warehouse_id": wid,
            "warehouse_code": wh.warehouse_code,
            "warehouse_name": wh.warehouse_name,
            "is_active": bool(wh.is_active),
            "on_hand": oh,
            "so_qty": so,
            "po_qty": po,
            "spo_qty": spo,
            "forecast": oh - so + po + spo,
        })
    # Active first, then by code: one reading order on every read.
    locations.sort(key=lambda loc: (not loc["is_active"], loc["warehouse_code"] or ""))

    # Documents bound to a warehouse the caller cannot see (another company's, or none) are
    # not shown: their quantities are not in any row, so listing them would not add up.
    visible = set(warehouses)
    sales_orders = [r for r in sales_orders if r["warehouse_id"] in visible]
    purchase_orders = [r for r in purchase_orders if r["warehouse_id"] in visible]
    spos = [r for r in spos if r["warehouse_id"] in visible]

    totals = {
        key: sum(loc[key] for loc in locations)
        for key in ("on_hand", "so_qty", "po_qty", "spo_qty", "forecast")
    }
    totals["incoming_pl"] = sum(s["counted"] for s in incoming_pl)
    totals["forecast_incl_pl"] = totals["forecast"] + totals["incoming_pl"]

    as_of, _source = _stock_as_of(db, product_id)
    return {
        "product_id": str(product.id),
        "item_code": product.product_code,
        "description": product.product_name,
        "as_of": as_of,
        "so_due_by": _iso(so_due_by),
        "locations": locations,
        "totals": totals,
        "incoming_pl": incoming_pl,
        "sales_orders": sales_orders,
        "purchase_orders": purchase_orders,
        "spos": spos,
    }


def so_outstanding_clauses() -> tuple:
    """AutoCount's outstanding SO (R4), one definition for the panel and the item chips.

    Open lines of open orders still owing, and never an order AutoCount marks Transferable =
    F (not approved for the queue, Q7); NULL ("never said") counts like T. A line CS marked
    `covered` counts and `qty_required` plays no part: AutoCount knows neither.
    """
    return (
        SalesOrderLine.warehouse_id.isnot(None),
        SalesOrderLine.line_status == "open",
        SalesOrder.status == "open",
        SalesOrder.is_transferable.isnot(False),
        SalesOrderLine.qty_ordered > SalesOrderLine.qty_delivered,
    )


#: The most item ids one summary call answers; a page of a list is far below it.
MAX_SUMMARY_IDS = 200


def summaries(db: Session, product_ids: list[str]) -> list[dict[str, Any]]:
    """On hand and SO per item, on the panel's own rules, for a list's item chips.

    One read for a whole page of a list, where the full panel per row would be hundreds of
    queries. Ids that are not uuids are skipped; an item the caller's company does not hold
    is simply absent. `Stock` and `SalesOrderLine` rows are company-scoped themselves, so only
    the caller's company is counted, as in the panel.
    """
    ids = [pid for pid in dict.fromkeys(product_ids) if is_uuid(pid)]
    if not ids:
        return []
    known = [str(pid) for (pid,) in db.query(Product.id).filter(Product.id.in_(ids)).all()]
    if not known:
        return []
    on_hand: dict[str, float] = defaultdict(float)
    for pid, qty in (
        db.query(Stock.product_id, Stock.quantity_on_hand)
        .join(Warehouse, Warehouse.id == Stock.warehouse_id)
        .filter(Stock.product_id.in_(known))
        .all()
    ):
        on_hand[str(pid)] += _num(qty)
    so_qty: dict[str, float] = defaultdict(float)
    for pid, ordered, delivered in (
        db.query(SalesOrderLine.product_id, SalesOrderLine.qty_ordered, SalesOrderLine.qty_delivered)
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
        .join(Warehouse, Warehouse.id == SalesOrderLine.warehouse_id)
        .filter(SalesOrderLine.product_id.in_(known), *so_outstanding_clauses())
        .all()
    ):
        so_qty[str(pid)] += _num(ordered) - _num(delivered)
    return [
        {"product_id": pid, "on_hand": on_hand.get(pid, 0.0), "so_qty": so_qty.get(pid, 0.0)}
        for pid in ids if pid in known
    ]


def _sales_order_rows(db: Session, product_id: str, so_due_by: Optional[date]) -> list[dict]:
    """Counted SO lines (R4, R5): AutoCount's outstanding, Transferable F left out."""
    rows = (
        db.query(
            SalesOrderLine.id,
            SalesOrderLine.warehouse_id,
            SalesOrderLine.qty_ordered,
            SalesOrderLine.qty_delivered,
            SalesOrderLine.required_date,
            SalesOrderLine.purchasing_status,
            SalesOrder.id.label("sales_order_id"),
            SalesOrder.so_number,
            SalesOrder.debtor_code,
            SalesOrder.debtor_name,
            SalesOrder.project_label,
            SalesOrder.order_date,
            SalesOrder.requested_delivery_date,
            SalesAgent.sales_agent,
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderLine.sales_order_id)
        .outerjoin(SalesAgent, SalesAgent.id == SalesOrder.sales_agent_id)
        .filter(SalesOrderLine.product_id == product_id, *so_outstanding_clauses())
        .all()
    )
    out = []
    for r in rows:
        due = _due(r.required_date, r.requested_delivery_date)
        if so_due_by is not None and due is not None and due > so_due_by:
            continue
        out.append({
            "line_id": str(r.id),
            "sales_order_id": str(r.sales_order_id),
            "so_number": r.so_number,
            "debtor_code": r.debtor_code,
            "debtor_name": r.debtor_name,
            "project_label": r.project_label,
            "salesperson": r.sales_agent,
            "doc_date": _iso(r.order_date),
            "due_date": _iso(due),
            "qty": _num(r.qty_ordered) - _num(r.qty_delivered),
            "covered_in_crm": r.purchasing_status == "covered",
            "warehouse_id": str(r.warehouse_id),
        })
    out.sort(key=lambda d: (d["due_date"] is None, d["due_date"] or "", d["so_number"] or ""))
    return out


def _purchase_order_rows(db: Session, product_id: str) -> list[dict]:
    """Open PO lines, `OPEN_PO_SQL`'s rule, still to receive."""
    rows = (
        db.query(
            PurchaseOrderLine.id,
            PurchaseOrderLine.warehouse_id,
            PurchaseOrderLine.qty_ordered,
            PurchaseOrderLine.qty_received,
            PurchaseOrderLine.expected_date,
            PurchaseOrder.id.label("purchase_order_id"),
            PurchaseOrder.po_number,
            PurchaseOrder.issue_date,
            Supplier.supplier_code,
            Supplier.supplier_name,
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
        .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .filter(
            PurchaseOrderLine.product_id == product_id,
            PurchaseOrderLine.warehouse_id.isnot(None),
            PurchaseOrder.status.in_(OPEN_PO_STATUSES),
            PurchaseOrderLine.line_status == "open",
            PurchaseOrderLine.qty_ordered > PurchaseOrderLine.qty_received,
        )
        .all()
    )
    out = [{
        "line_id": str(r.id),
        "purchase_order_id": str(r.purchase_order_id),
        "po_number": r.po_number,
        "supplier_code": r.supplier_code,
        "supplier_name": r.supplier_name,
        "issue_date": _iso(r.issue_date),
        "expected_date": _iso(r.expected_date),
        "qty": _num(r.qty_ordered) - _num(r.qty_received),
        "warehouse_id": str(r.warehouse_id),
    } for r in rows]
    out.sort(key=lambda d: (d["expected_date"] is None, d["expected_date"] or "", d["po_number"] or ""))
    return out


def _spo_rows(db: Session, product_id: str) -> list[dict]:
    """Open SPO rows (`spo_supply.open_incoming_clauses`), allocated less received."""
    rows = (
        db.query(
            SPOAllocation.id,
            SPOAllocation.warehouse_id,
            SPOAllocation.spo_number,
            SPOAllocation.spo_line_number,
            SPOAllocation.allocated_quantity,
            SPOAllocation.quantity_received,
            SPOAllocation.issue_date,
            SPOAllocation.expected_date,
            SPOAllocation.container_number,
            SPOAllocation.supplier_id,
            InboundShipment.supplier_id.label("ship_supplier_id"),
            InboundShipment.eta_delay_date,
            InboundShipment.estimated_arrival_date,
        )
        .outerjoin(InboundShipment, InboundShipment.id == SPOAllocation.inbound_shipment_id)
        .filter(
            SPOAllocation.product_id == product_id,
            SPOAllocation.warehouse_id.isnot(None),
            *spo_supply.open_incoming_clauses(),
        )
        .all()
    )
    # The line's own supplier first, else the shipment's: one plain lookup, so the company
    # scope applies to it exactly as it does to every other read here.
    wanted = {str(x) for r in rows for x in (r.supplier_id, r.ship_supplier_id) if x}
    suppliers = {
        str(sid): (code, name)
        for sid, code, name in (
            db.query(Supplier.id, Supplier.supplier_code, Supplier.supplier_name)
            .filter(Supplier.id.in_(wanted))
            .all()
        )
    } if wanted else {}
    out = []
    for r in rows:
        balance = _num(r.allocated_quantity) - _num(r.quantity_received)
        if balance <= 0:
            continue
        arrival = r.eta_delay_date or r.estimated_arrival_date or r.expected_date
        code, name = suppliers.get(str(r.supplier_id or r.ship_supplier_id), (None, None))
        out.append({
            "allocation_id": str(r.id),
            "spo_number": r.spo_number,
            "spo_line_number": r.spo_line_number,
            "supplier_code": code,
            "supplier_name": name,
            "issue_date": _iso(r.issue_date),
            "arrival_date": _iso(arrival),
            "container_number": r.container_number,
            "qty": balance,
            "warehouse_id": str(r.warehouse_id),
        })
    out.sort(key=lambda d: (d["arrival_date"] is None, d["arrival_date"] or "", d["spo_number"] or ""))
    return out


def _incoming_pl(db: Session, product_id: str) -> list[dict]:
    """Packing lists still to land, net of what an SPO already carries (R3)."""
    lines = (
        db.query(
            InboundShipment.id,
            InboundShipment.shipment_number,
            InboundShipment.shipping_container_number,
            InboundShipment.estimated_arrival_date,
            InboundShipment.eta_delay_date,
            InboundShipmentLine.quantity_shipped,
            InboundShipmentLine.quantity_received,
        )
        .join(InboundShipment, InboundShipment.id == InboundShipmentLine.shipment_id)
        .filter(
            InboundShipmentLine.product_id == product_id,
            InboundShipmentLine.line_status != "received",
            InboundShipment.actual_arrival_date.is_(None),
            InboundShipment.shipment_status.notin_(_LANDED_SHIPMENT_STATES),
            InboundShipment.shipment_status != _DRAFT_SHIPMENT_STATUS,
        )
        .all()
    )
    shipments: dict[str, dict] = {}
    for ln in lines:
        still = max(_num(ln.quantity_shipped) - _num(ln.quantity_received), 0.0)
        if still <= 0:
            continue
        s = shipments.setdefault(str(ln.id), {
            "shipment_id": str(ln.id),
            "shipment_number": ln.shipment_number,
            "container_number": ln.shipping_container_number,
            "eta": _iso(ln.eta_delay_date or ln.estimated_arrival_date),
            "still_to_land": 0.0,
        })
        s["still_to_land"] += still
    if not shipments:
        return []

    ids = list(shipments)
    containers = {s["container_number"]: sid for sid, s in shipments.items() if s["container_number"]}
    match = [SPOAllocation.inbound_shipment_id.in_(ids)]
    if containers:
        match.append(SPOAllocation.container_number.in_(list(containers)))
    on_spo: dict[str, float] = defaultdict(float)
    for sid, box, qty in (
        db.query(SPOAllocation.inbound_shipment_id, SPOAllocation.container_number,
                 SPOAllocation.allocated_quantity)
        .filter(SPOAllocation.product_id == product_id, or_(*match))
        .all()
    ):
        # One SPO row counts against one packing list: its link wins, else its container.
        target = str(sid) if sid and str(sid) in shipments else containers.get(box)
        if target:
            on_spo[target] += _num(qty)

    out = []
    for sid, s in shipments.items():
        carried = on_spo.get(sid, 0.0)
        out.append({**s, "on_spo": carried, "counted": max(s["still_to_land"] - carried, 0.0)})
    out.sort(key=lambda d: (d["eta"] is None, d["eta"] or "", d["shipment_number"] or ""))
    return out
