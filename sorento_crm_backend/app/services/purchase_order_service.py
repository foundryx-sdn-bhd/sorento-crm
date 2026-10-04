"""Purchase orders placed but not yet received (A5, chatbot-growth-r1).

`documentation/plans/chatbot/PLAN-chatbot-growth-r1.md` Slice A;
`documentation/plans/chatbot/chatbot-growth-r1-acceptance-criteria.md` AC-907, AC-909.

One focused module rather than growing `procurement_service.py` (already ~6,200
lines) - this reads two tables and writes nothing.

PO and SPO are never netted (`spo_allocations.po_line_id` is NULL on every row,
decision 6 Aug 2026, see `PLAN-chatbot-growth-r1.md` A5), so `outstanding_qty`
here is `qty_ordered - qty_received` ONLY - incoming SPO receipts never reduce
it.

Item 5 (8 Sep 2026): an UNSHIPPED SPO allocation is "on order from the supplier" too.
Measured on the prod copy: 721 `spo_allocations` rows over 191 products with
`receipt_status='pending'`, no `inbound_shipment_id` and `allocated_quantity -
quantity_received > 0`, visible to no rung - this module read PO lines only and the
last-receipt tool reads `fully_received` only. They now come back here as rows of the
SAME shape with `kind = "spo"` (PO rows say `kind = "po"`): `po_number` is the SPO
number, `po_date` its `issue_date`, `expected_date` its promised arrival,
`outstanding_qty` the unreceived remainder, `supplier` from `supplier_id`,
`ordered_qty` its `allocated_quantity`, `location` its warehouse code else
`location_code` (owner ruling 11 Sep 2026).

NO `po_line_id` DEDUPE (review round 2, S3): measured 0 of 80,468 `spo_allocations`
carry `po_line_id`, so a "child of an open PO line" cannot exist today and the extra
column-only, unscoped SELECT it needed could only ever suppress an in-scope row on
another company's PO line. Re-add the dedupe (an allocation whose `po_line_id` points
at an open PO line already in the rows is that line's shipment plan, not more supply)
the day `spo_allocations.po_line_id` is populated - the trigger is named in the PLAN's
as-built section.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.services.contact_brand_scope import product_in_scope_clauses
from app.models.inventory import Warehouse
from app.models.procurement import PurchaseOrder, PurchaseOrderLine, SPOAllocation, Supplier
from app.models.product import Product
from app.services.company_scope import build_company_predicate, get_company_scope

PO_GROUP_BY_AXES: frozenset[str] = frozenset({"product", "supplier", "date"})

#: PLAN-po-spo-warehouse-29sep O1: the sort keys and directions `/placed` accepts. The ROUTE
#: validates against these (a 422), so the `.get(sort, ...)` fallbacks below never fire.
PO_SORT_KEYS: frozenset[str] = frozenset(
    {"expected_date", "product", "supplier", "outstanding_qty", "po_date", "ordered_qty"}
)
PO_SORT_DIRS: frozenset[str] = frozenset({"asc", "desc"})


def warehouse_codes_for(db: Session, warehouse_ids: Optional[list[str]]) -> list[str]:
    """The `warehouse_code`s of these warehouse ids (PLAN-po-spo-warehouse-29sep W3).
    One COLUMN-ONLY read, so the company predicate is ANDed in by hand (the same reason
    `purchase_orders_placed_summary` gives): a warehouse id from outside the caller's
    scope resolves to no code."""
    if not warehouse_ids:
        return []
    q = db.query(Warehouse.warehouse_code).filter(Warehouse.id.in_(warehouse_ids))
    predicate = build_company_predicate(Warehouse, get_company_scope(db))
    if predicate is not None:
        q = q.filter(predicate)
    return [code for (code,) in q.all() if code]


def spo_warehouse_predicate(warehouse_ids: list[str], codes: list[str]):
    """An SPO line is at a warehouse by its `warehouse_id` OR by the book's raw
    `location_code` (`warehouse_id` is absent on lines whose location we hold no row
    for; PLAN-po-spo-warehouse-29sep W3/W4)."""
    by_id = SPOAllocation.warehouse_id.in_(warehouse_ids)
    if not codes:
        return by_id
    return or_(by_id, SPOAllocation.location_code.in_(codes))


def _plain_number(v: Any) -> Any:
    if v is None:
        return None
    from decimal import Decimal

    try:
        d = Decimal(str(v))
    except Exception:  # noqa: BLE001
        return v
    return int(d) if d == d.to_integral_value() else float(d)


def purchase_orders_placed_rows(
    db: Session,
    *,
    product_ids: Optional[list[str]] = None,
    warehouse_ids: Optional[list[str]] = None,
    expected_date_from=None,
    expected_date_to=None,
    sort: str = "expected_date",
    dir: str = "asc",
    limit: int = 500,
) -> list[dict]:
    """Open PO lines: `qty_ordered - qty_received > 0`, `line_status='open'`.

    One row per line - PO number, product, ordered qty, outstanding qty, expected
    date (line, else header), supplier, location (the line's warehouse). `supplier`
    is ALWAYS populated here; the restricted-field drop that hides it from a dealer
    runs downstream in `output_structurer` (`restricted_fields`), never here - the
    MCP itself stays unfiltered (Slice A design).
    """
    delta = PurchaseOrderLine.qty_ordered - PurchaseOrderLine.qty_received
    q = (
        db.query(PurchaseOrderLine, PurchaseOrder, Product, Supplier, Warehouse)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
        .join(Product, Product.id == PurchaseOrderLine.product_id)
        .outerjoin(Supplier, Supplier.id == PurchaseOrder.supplier_id)
        .outerjoin(Warehouse, Warehouse.id == PurchaseOrderLine.warehouse_id)
        .filter(PurchaseOrderLine.line_status == "open", delta > 0)
    )
    if product_ids:
        q = q.filter(PurchaseOrderLine.product_id.in_(product_ids))
    if warehouse_ids:
        q = q.filter(PurchaseOrderLine.warehouse_id.in_(warehouse_ids))
    # Line `expected_date` else header's - COALESCE, not a Python fallback, so
    # the date filter, the sort and the SUMMARY all see the same effective value.
    effective_date = _effective_expected_date()
    q = _apply_expected_date_window(q, expected_date_from, expected_date_to)

    sort_col = {
        "expected_date": effective_date,
        "product": Product.product_code,
        "supplier": Supplier.supplier_name,
        "outstanding_qty": delta,
        "po_date": PurchaseOrder.issue_date,
        "ordered_qty": PurchaseOrderLine.qty_ordered,
    }.get(sort, effective_date)
    order = sort_col.desc() if dir == "desc" else sort_col.asc()
    rows = (
        q.order_by(order.nulls_last(), PurchaseOrder.po_number.asc())
        .limit(limit)
        .all()
    )
    out: list[dict] = []
    for line, po, product, supplier, warehouse in rows:
        expected = line.expected_date or po.expected_date
        out.append(
            {
                "kind": "po",
                "po_number": po.po_number,
                "product_id": str(product.id),
                "product_code": product.product_code,
                "product_name": product.product_name,
                "outstanding_qty": _plain_number(line.qty_ordered - line.qty_received),
                "expected_date": expected.isoformat() if expected else None,
                "supplier": supplier.supplier_name if supplier else None,
                # The PO DOCUMENT date (`purchase_orders.issue_date`, the header's own
                # date; `expected_date` above is the line's else the header's arrival
                # estimate). Owner ruling 8 Sep 2026: the chatbot's PO rung says when the
                # PO was raised, not only when the goods are due. Additive; every field
                # above is byte-identical.
                "po_date": po.issue_date.isoformat() if po.issue_date else None,
                # Owner ruling 11 Sep 2026: the line's own ordered quantity and
                # warehouse, alongside the outstanding remainder above. `location` is
                # None when the line has no warehouse, or its warehouse is outside the
                # caller's company scope (the scope predicate lands in the join's ON
                # clause, so the row still returns).
                "ordered_qty": _plain_number(line.qty_ordered),
                "location": warehouse.warehouse_code if warehouse else None,
            }
        )

    # Item 5: the unshipped SPO allocations, same scope and window, same shape.
    spo_q = _unshipped_spo_query(db, product_ids=product_ids, warehouse_ids=warehouse_ids)
    spo_q = _apply_spo_expected_date_window(spo_q, expected_date_from, expected_date_to)
    spo_delta = _spo_delta()
    spo_sort_col = {
        "expected_date": SPOAllocation.expected_date,
        "product": Product.product_code,
        "supplier": Supplier.supplier_name,
        "outstanding_qty": spo_delta,
        "po_date": SPOAllocation.issue_date,
        "ordered_qty": SPOAllocation.allocated_quantity,
    }.get(sort, SPOAllocation.expected_date)
    spo_order = spo_sort_col.desc() if dir == "desc" else spo_sort_col.asc()
    spo_rows = (
        spo_q.order_by(spo_order.nulls_last(), SPOAllocation.spo_number.asc()).limit(limit).all()
    )
    if not spo_rows:
        return out  # PO-only data: the SQL order above is the answer, byte-identical
    for alloc, product, supplier, warehouse in spo_rows:
        out.append(
            {
                "kind": "spo",
                "po_number": alloc.spo_number,
                "product_id": str(product.id),
                "product_code": product.product_code,
                "product_name": product.product_name,
                "outstanding_qty": _plain_number(alloc.allocated_quantity - alloc.quantity_received),
                "expected_date": alloc.expected_date.isoformat() if alloc.expected_date else None,
                "supplier": supplier.supplier_name if supplier else None,
                "po_date": alloc.issue_date.isoformat() if alloc.issue_date else None,
                # Owner ruling 11 Sep 2026: `ordered_qty` is the allocation's own
                # quantity; `location` is its warehouse code, else the book's raw
                # `location_code`, else None (see `_unshipped_spo_query`'s docstring
                # for why `warehouse_id` is absent on most rows) - also None when the
                # allocation's warehouse is outside the caller's company scope (the
                # scope predicate lands in the join's ON clause, so the row still
                # returns).
                "ordered_qty": _plain_number(alloc.allocated_quantity),
                "location": (warehouse.warehouse_code if warehouse else None) or alloc.location_code,
            }
        )
    return _merge_sorted(out, sort=sort, dir=dir, limit=limit)


_SORT_KEY_FIELD = {
    "expected_date": "expected_date",
    "product": "product_code",
    "supplier": "supplier",
    "outstanding_qty": "outstanding_qty",
    "po_date": "po_date",
    "ordered_qty": "ordered_qty",
}


def _merge_sorted(rows: list[dict], *, sort: str, dir: str, limit: int) -> list[dict]:
    """Two SQL-sorted lists (PO lines, SPO allocations) folded into ONE order: the asked
    key, nulls last whichever direction, then the document number ascending - the same
    order each query took on its own. Each side was capped at `limit`, so the merged
    top-`limit` is complete. Strings compare case-insensitively, the closest a Python sort
    comes to the database collation the PO-only path still uses."""
    field = _SORT_KEY_FIELD.get(sort, "expected_date")

    def key_value(row: dict):
        v = row.get(field)
        return v.casefold() if isinstance(v, str) else v

    with_value = [r for r in rows if row_has(r, field)]
    without = [r for r in rows if not row_has(r, field)]
    with_value.sort(key=lambda r: str(r.get("po_number") or ""))
    without.sort(key=lambda r: str(r.get("po_number") or ""))
    with_value.sort(key=key_value, reverse=(dir == "desc"))
    return (with_value + without)[:limit]


def row_has(row: dict, field: str) -> bool:
    return row.get(field) is not None


def _spo_delta():
    return SPOAllocation.allocated_quantity - SPOAllocation.quantity_received


def _unshipped_spo_query(
    db: Session,
    *,
    product_ids: Optional[list[str]],
    warehouse_ids: Optional[list[str]] = None,
):
    """`(SPOAllocation, Product, Supplier)` for every allocation still on order from the
    supplier: pending, not yet on a shipment, quantity unreceived (see the module
    docstring for the `po_line_id` dedupe that is deliberately NOT here).

    R7/R8/AC-E2: this had NO line-status test at all before `visible_line_clauses()` was
    added - only `receipt_status == 'pending'` plus the quantity test above, both of
    which a retired-and-closed line can still pass. Behind `crm_procurement_po_placed_
    list`, so without it the chatbot stated a retired line's quantity as a live fact.
    """
    from app.services.scm import spo_supply

    q = (
        db.query(SPOAllocation, Product, Supplier, Warehouse)
        .join(Product, Product.id == SPOAllocation.product_id)
        .outerjoin(Supplier, Supplier.id == SPOAllocation.supplier_id)
        .outerjoin(Warehouse, Warehouse.id == SPOAllocation.warehouse_id)
        .filter(
            SPOAllocation.receipt_status == "pending",
            SPOAllocation.inbound_shipment_id.is_(None),
            _spo_delta() > 0,
            *spo_supply.visible_line_clauses(),
        )
    )
    if product_ids:
        q = q.filter(SPOAllocation.product_id.in_(product_ids))
    if warehouse_ids:
        q = q.filter(spo_warehouse_predicate(warehouse_ids, warehouse_codes_for(db, warehouse_ids)))
    return q


def _apply_spo_expected_date_window(q, expected_date_from, expected_date_to):
    """The SPO side of `_apply_expected_date_window`: the allocation's own promised date.
    A null date passes when no window is asked for and fails one when it is - the same
    rule the PO COALESCE gives."""
    if expected_date_from is not None:
        q = q.filter(SPOAllocation.expected_date >= expected_date_from)
    if expected_date_to is not None:
        q = q.filter(SPOAllocation.expected_date <= expected_date_to)
    return q


#: The effective expected date: the LINE's, else the header's. COALESCE rather than a
#: Python fallback so the filter, the sort and the summary all read one value.
def _effective_expected_date():
    return func.coalesce(PurchaseOrderLine.expected_date, PurchaseOrder.expected_date)


def _apply_expected_date_window(q, expected_date_from, expected_date_to):
    """The `expected_date_from/to` window, on any query that has both tables joined.

    ONE helper, called by the rows query and by the summary, because the review found the
    summary with no window at all and a second hand-written copy is how they drift again.
    """
    effective_date = _effective_expected_date()
    if expected_date_from is not None:
        q = q.filter(effective_date >= expected_date_from)
    if expected_date_to is not None:
        q = q.filter(effective_date <= expected_date_to)
    return q


def purchase_orders_placed_summary(
    db,
    *,
    product_ids: Optional[list[str]] = None,
    warehouse_ids: Optional[list[str]] = None,
    expected_date_from: Optional[str] = None,
    expected_date_to: Optional[str] = None,
) -> dict:
    """`po_placed_qty` / `po_placed_count` over EXACTLY the rows the list returned.

    The date window is applied here too (review, should-fix 6). Without it, "PO for X
    arriving this month" listed the month's lines and summarised the whole open book, so
    the summary contradicted the list it sat under - the one failure mode a summary has.
    The window is built by the same `_expected_date_window` the rows use, so the two
    cannot read one date differently.
    """
    # COMPANY SCOPE, BY HAND (review round 2, B1). Both legs below are column-only
    # aggregates, and a column-only query is where the session's do_orm_execute scope is
    # lost (`order_service.stamp_order_summary` measured it: a Sorento-only read summed a
    # Mocha row). The rows path keeps the scope because it selects the entities; the
    # summary ANDs the same predicate in itself so the two never disagree on company.
    _scope = get_company_scope(db)
    _p_line = build_company_predicate(PurchaseOrderLine, _scope)
    _p_po = build_company_predicate(PurchaseOrder, _scope)
    _p_spo = build_company_predicate(SPOAllocation, _scope)
    _scoped = lambda q, pred: q.filter(pred) if pred is not None else q  # noqa: E731

    delta = PurchaseOrderLine.qty_ordered - PurchaseOrderLine.qty_received
    q = (
        db.query(func.sum(delta), func.count(PurchaseOrderLine.id))
        # JOINED even when no window is asked for: the effective date COALESCEs the
        # header's column, so the join has to be there for the filter to resolve, and a
        # conditional join would make the unfiltered count depend on whether a date was
        # passed. Every line has a header (FK, not null), so the join drops no row.
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.purchase_order_id)
        .filter(PurchaseOrderLine.line_status == "open", delta > 0)
    )
    q = _scoped(_scoped(q, _p_line), _p_po)
    if product_ids:
        q = q.filter(PurchaseOrderLine.product_id.in_(product_ids))
    # CONTACT-BRAND-SCOPE: a column-only aggregate, where the session criterion cannot reach.
    q = q.filter(*product_in_scope_clauses(db, PurchaseOrderLine.product_id))
    if warehouse_ids:
        q = q.filter(PurchaseOrderLine.warehouse_id.in_(warehouse_ids))
    q = _apply_expected_date_window(q, expected_date_from, expected_date_to)
    qty, count = q.one()
    # Item 5: the unshipped SPO allocations count too, under the same scope and window the
    # rows take - a summary that counts fewer rows than the list under it is the one
    # failure mode a summary has.
    spo_q = _unshipped_spo_query(
        db, product_ids=product_ids, warehouse_ids=warehouse_ids
    ).with_entities(
        func.sum(_spo_delta()), func.count(SPOAllocation.id)
    )
    spo_q = _scoped(spo_q, _p_spo)
    spo_q = spo_q.filter(*product_in_scope_clauses(db, SPOAllocation.product_id))
    spo_q = _apply_spo_expected_date_window(spo_q, expected_date_from, expected_date_to)
    spo_qty, spo_count = spo_q.one()
    return {
        "po_placed_qty": (_plain_number(qty) or 0) + (_plain_number(spo_qty) or 0),
        "po_placed_count": int(count or 0) + int(spo_count or 0),
    }


def group_rows(rows: list[dict], *, group_by: str) -> list[dict]:
    axis_key = {"product": "product_code", "supplier": "supplier", "date": "expected_date"}.get(
        group_by
    )
    if axis_key is None:
        return []
    order: list[str] = []
    buckets: dict[str, list[dict]] = {}
    for row in rows:
        label = row.get(axis_key) or "Not specified"
        if label not in buckets:
            buckets[label] = []
            order.append(label)
        buckets[label].append(row)
    return [{"key": label, "label": label, "rows": buckets[label]} for label in order]
