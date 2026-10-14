"""STOCK-DEBT-PERF: the landed-pin read asks the database once for the whole board.

Measured on the shared dev DB (15 Oct 2026, origin/main a92baeb87): the default Stock Debt
request (`page=1&limit=25&only_debt=true&book=all`) issued 1,288 SQL statements in ~12 s.
1,258 of them came from `StockDebtService._landed_holds`, which called
`ProjectSupplyService._po_received_by_so_line_ref` once PER PRODUCT (629 products x 2
reads: the SPO receipt join and the order-inquiry placement join), ~7 s of the ~12 s.

Two guards, both over `_landed_holds` directly with hand-built supply and demand (the
walk's own dataclasses) and real receipts in the database:

- the read count does not grow with the product count (the red);
- parity: every hold equals what the single-key read (`_po_received_by_so_line_ref`, still
  the seam the board's other callers use) says landed, with receipts across two SPOs, an
  order-inquiry placement and a `source_ref` shared by two products' lines in the world.

Postgres only (`tests/_pg_fixture.py` via the `api` fixture), every FK seeded here.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import event

from app.models.procurement import SPOAllocation
from app.models.project_so import (
    INQUIRY_PLACED,
    IV_ORDER,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
)
from app.services.scm.stock_debt_service import StockDebtService
from app.services.scm.supply_assignment import KIND_ON_HAND, DemandLine, Hold, SupplyEvent

from tests.scm._own_arrival_fixture import po_line_bought_for, supplier_and_po
from tests.test_planning_changes import (  # noqa: F401  (api is the fixture)
    api,
    _core_line,
    _core_so,
    _product,
    _project_line,
    _project_so,
    _uid,
)

AS_OF = date(2026, 10, 15)
DUE = date(2027, 2, 1)


def _floor(warehouse) -> SupplyEvent:
    """On hand at `warehouse`: the floor a landed pin sits on."""
    return SupplyEvent(
        key=f"on_hand:{warehouse.id}", kind=KIND_ON_HAND, warehouse=str(warehouse.id),
        at=AS_OF, qty=500.0,
    )


def _line(key: str, warehouse, *, open_qty: float, source_ref: str) -> DemandLine:
    return DemandLine(
        key=key, so_number=f"ZZT-SO-{key}", line_no=1, warehouse=str(warehouse.id),
        agent_code=None, required_date=DUE, open_qty=open_qty, source_ref=source_ref,
    )


def _ref() -> str:
    return f"ZZT-SDP-{_uid()[:8]}"


def _statements(db, call):
    """`(call(), [every SQL statement it sent])`."""
    bind = db.get_bind()
    seen: list[str] = []

    def _before(_conn, _cursor, statement, _params, _context, _many):
        seen.append(statement)

    event.listen(bind, "before_cursor_execute", _before)
    try:
        result = call()
    finally:
        event.remove(bind, "before_cursor_execute", _before)
    return result, seen


def _landed_products(db, world, count: int):
    """`count` products, each with one open line at the world's bin, on hand there, and
    20 landed on an SPO against the line's own purchase-order line."""
    po = supplier_and_po(db, po_number=f"ZZT-PO-{_uid()[:8]}")
    supply, demand = {}, {}
    for index in range(count):
        product = _product(db)
        ref = _ref()
        po_line_bought_for(
            db, po, product, world.own_wh, from_so_line_ref=ref, qty_received=20,
            spo_number=f"ZZT-SPO-{index}",
        )
        supply[str(product.id)] = [_floor(world.own_wh)]
        demand[str(product.id)] = [_line(f"p{index}", world.own_wh, open_qty=50, source_ref=ref)]
    db.flush()
    return supply, demand


def test_the_landed_read_does_not_grow_with_the_product_count(api):
    """1,258 of the dev board's 1,288 statements were this read, two per product. One
    product and four must cost the same handful of reads."""
    _client, world = api
    db = world.db
    service = StockDebtService(db)
    one = _landed_products(db, world, 1)
    four = _landed_products(db, world, 4)

    holds_one, reads_one = _statements(db, lambda: service._landed_holds(*one, []))
    holds_four, reads_four = _statements(db, lambda: service._landed_holds(*four, []))

    # The read still finds every landing: one pin per product, 20 each.
    assert [hold.qty for hold in holds_one] == [20.0]
    assert [hold.qty for hold in holds_four] == [20.0] * 4
    assert len(reads_four) == len(reads_one), reads_four
    assert len(reads_four) <= 2, reads_four


def test_landed_holds_match_the_single_key_read_for_every_product(api):
    """Parity. P1: a line landed across two SPOs (30 + 15) less 5 its own decision holds,
    and a line landed 25 of an open 10. P2: a line whose `source_ref` text is ALSO P1's
    first line's, landed 7 on P2's own purchase - P1 must not see the 7, P2 must not see
    P1's 45. P3: nothing on its PO, 20 landed through an order-inquiry placement (link 25,
    SPO received 20). P4: a line with nothing landed pins nothing."""
    _client, world = api
    db = world.db
    wh = world.own_wh
    p1, p2, p3, p4 = (_product(db) for _ in range(4))
    po = supplier_and_po(db, po_number=f"ZZT-PO-{_uid()[:8]}")

    shared_ref, p1_second_ref, p3_ref, p4_ref = _ref(), _ref(), _ref(), _ref()
    p1_po_line, _spo = po_line_bought_for(
        db, po, p1, wh, from_so_line_ref=shared_ref, qty_received=30,
        spo_number="ZZT-SPO-2026/09-0001",
    )
    db.add(
        SPOAllocation(
            id=_uid(), spo_number="ZZT-SPO-2026/10-0002", spo_line_number=9001,
            product_id=p1.id, warehouse_id=wh.id, location_code=wh.warehouse_code,
            allocated_quantity=15, quantity_received=15, receipt_status="fully_received",
            line_status="closed", from_po_line_ref=p1_po_line.source_ref,
            from_po_number=po.po_number, company_id=p1_po_line.company_id,
        )
    )
    po_line_bought_for(
        db, po, p1, wh, from_so_line_ref=p1_second_ref, qty_received=25,
        spo_number="ZZT-SPO-2026/09-0003",
    )
    po_line_bought_for(
        db, po, p2, wh, from_so_line_ref=shared_ref, qty_received=7,
        spo_number="ZZT-SPO-2026/09-0004",
    )

    # P3: the order inquiry places the line on a received SPO row of its own product.
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, p3, wh, qty_ordered="40", required_date=DUE)
    core_line.source_ref = p3_ref
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    project_line = _project_line(db, order, line_no=1, product=p3, core_line=core_line)
    placed_on = SPOAllocation(
        id=_uid(), spo_number="ZZT-SPO-2026/09-0005", spo_line_number=9002,
        product_id=p3.id, warehouse_id=wh.id, location_code=wh.warehouse_code,
        allocated_quantity=40, quantity_received=20, receipt_status="pending",
        line_status="open", company_id=world.company_id,
    )
    db.add(placed_on)
    db.flush()
    inquiry = OrderInquiry(
        id=_uid(), company_id=world.company_id, project_sales_order_id=order.id,
        amendment_id=None, state="raised", raised_by=world.actor,
    )
    db.add(inquiry)
    db.flush()
    row = OrderInquiryRow(
        id=_uid(), company_id=world.company_id, order_inquiry_id=inquiry.id,
        so_line_id=project_line.id, qty=Decimal("40"), verb=IV_ORDER, state=INQUIRY_PLACED,
        stock_location=wh.warehouse_code,
    )
    db.add(row)
    db.flush()
    db.add(
        OrderInquiryLink(
            id=_uid(), company_id=world.company_id, row_id=row.id,
            spo_allocation_id=placed_on.id, document=placed_on.spo_number, qty=Decimal("25"),
        )
    )
    db.flush()

    lines = {
        str(p1.id): [
            _line("p1-a", wh, open_qty=50, source_ref=shared_ref),
            _line("p1-b", wh, open_qty=10, source_ref=p1_second_ref),
        ],
        str(p2.id): [_line("p2", wh, open_qty=40, source_ref=shared_ref)],
        str(p3.id): [_line("p3", wh, open_qty=40, source_ref=p3_ref)],
        str(p4.id): [_line("p4", wh, open_qty=40, source_ref=p4_ref)],
    }
    supply = {product_id: [_floor(wh)] for product_id in lines}
    own_hold = Hold(
        line_key="p1-a", supply_key=f"on_hand:{wh.id}", qty=5.0, kind=KIND_ON_HAND,
        warehouse=str(wh.id),
    )

    service = StockDebtService(db)
    got = service._landed_holds(supply, lines, [own_hold])

    # The oracle: the single-key read, product by product, through the same arithmetic.
    expected = []
    for product_id, product_lines in lines.items():
        received = service.supply._po_received_by_so_line_ref(
            [line.source_ref for line in product_lines], product_id=product_id,
            company_id=None,
        )
        for line in product_lines:
            landed = float(received.get(line.source_ref, (Decimal("0"), None))[0])
            qty = min(landed, line.open_qty) - (5.0 if line.key == "p1-a" else 0.0)
            if qty > 1e-9:
                expected.append((line.key, f"on_hand:{wh.id}", qty))

    assert [(hold.line_key, hold.supply_key, hold.qty) for hold in got] == expected
    # And the oracle is not vacuous: the numbers the docstring names.
    assert expected == [
        ("p1-a", f"on_hand:{wh.id}", 40.0),
        ("p1-b", f"on_hand:{wh.id}", 10.0),
        ("p2", f"on_hand:{wh.id}", 7.0),
        ("p3", f"on_hand:{wh.id}", 20.0),
    ]
    assert all(hold.landed and hold.kind == KIND_ON_HAND for hold in got)
    assert all(hold.warehouse == str(wh.id) and not hold.lendable for hold in got)
