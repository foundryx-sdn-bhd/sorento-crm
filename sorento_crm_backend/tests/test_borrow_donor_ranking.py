"""Borrow donors, ranked by how little the borrow hurts them (PLAN 13.11).

The captain, reading a donor list that said only "MWH-IB 6990 free, 10 committed":

> "Here is the available donor. Before I decide to borrow, I need to know I am not hurting
>  them, so you need to let me know also what's their available, SO qty, SPO and PO qty,
>  and available quantity, and what's the impact of borrowing. I assume this list is ranked
>  by recommendation, is it?"

It was not ranked, and it stated free stock alone - which on this database is very nearly
raw on-hand, because free nets only reserved and confirmed holds and ignores the whole
outstanding book. So a donor with 6,990 free and 47,000 owed read as the safest one to take
from.

What is pinned here:

  * every donor carries AutoCount's own triple beside the engine's figures - `qty_on_hand`,
    `so_qty`, `spo_qty`, `available_qty` (SIGNED), `qty_free`, `qty_committed`;
  * the list is ORDERED by `available_after` (available less the whole of what this donor
    could give) descending, then by free descending, and the first one - and only the first
  - is `recommended`;
  * one builder serves both surfaces, so the sheet's donor list and the board's are the
    same list in the same order.

Postgres, blank scratch schema, every FK target seeded here (PRINCIPLES).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import text

from app.models.inventory import Stock, Warehouse
from app.models.order import SalesOrder, SalesOrderLine
from app.models.procurement import InboundShipment, SPOAllocation
from app.models.product import Product, ProductCategory, UnitOfMeasure

from ._pg_fixture import blank_session

MARKER = "zzt-donor"
TODAY = date(2026, 8, 18)
REQUIRED = date(2026, 9, 3)


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _product(db) -> Product:
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT{_uid()[:6]}", uom_name="Unit")
    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:8]}", category_name=f"{MARKER} cat"
    )
    db.add_all([uom, category])
    db.flush()
    row = Product(
        id=_uid(),
        product_code=f"ZZT-{_uid()[:6]}",
        product_name=f"{MARKER} basin",
        category_id=category.id,
        base_uom_id=uom.id,
        list_price=Decimal("100.00"),
    )
    db.add(row)
    db.flush()
    return row


def _warehouse(db, code: str) -> Warehouse:
    row = Warehouse(
        id=_uid(),
        warehouse_code=code,
        warehouse_name=code,
        is_active=True,
        segment="project",
        fulfilment_planning=True,
    )
    db.add(row)
    db.flush()
    return row


def _stock(db, product: Product, warehouse: Warehouse, *, on_hand: int, reserved: int = 0):
    row = Stock(
        id=_uid(),
        product_id=product.id,
        warehouse_id=warehouse.id,
        quantity_on_hand=on_hand,
        quantity_reserved=reserved,
    )
    db.add(row)
    db.flush()
    return row


def _order(db, *, so_number: str) -> SalesOrder:
    row = SalesOrder(
        id=_uid(),
        so_number=so_number,
        order_date=date(2026, 1, 1),
        demand_class="project",
        status="open",
    )
    db.add(row)
    db.flush()
    return row


def _line(db, order, product, warehouse, *, qty: str, required_date=REQUIRED):
    row = SalesOrderLine(
        id=_uid(),
        sales_order_id=order.id,
        product_id=product.id,
        warehouse_id=warehouse.id,
        qty_ordered=Decimal(qty),
        qty_delivered=Decimal("0"),
        required_date=required_date,
        line_status="open",
        purchasing_status="not_reviewed",
    )
    db.add(row)
    db.flush()
    return row


def _spo(db, company_id, product, warehouse, *, qty: int, arrival: date):
    shipment = InboundShipment(
        id=_uid(),
        company_id=company_id,
        shipment_date=arrival,
        estimated_arrival_date=arrival,
        shipment_status="in_transit",
    )
    db.add(shipment)
    db.flush()
    db.add(
        SPOAllocation(
            id=_uid(),
            company_id=company_id,
            spo_number=f"ZZT-SPO-{_uid()[:6]}",
            spo_line_number=1,
            inbound_shipment_id=shipment.id,
            warehouse_id=warehouse.id,
            product_id=product.id,
            allocated_quantity=qty,
        )
    )
    db.flush()


def _candidates(db, product, own, *, key="line-1", qty="10", need="21"):
    """The one builder both surfaces call, driven the way the board drives it.

    `need` is the asking line's residual at the borrow rung - its proposed Buy - because that
    is the quantity the ranking is about. A donor is judged on what IT is left with once THIS
    line is met, never on what it would be left with if the line took every free unit it has.
    """
    from app.services.project_supply_service import ProjectSupplyService

    supply = ProjectSupplyService(db)
    facts = supply.demand_facts(
        [
            {
                "key": key,
                "product_id": str(product.id),
                "warehouse_id": str(own.id),
                "open_qty": Decimal(qty),
                "required_date": REQUIRED,
                "item_code": product.product_code,
            }
        ]
    )
    return supply.borrow_candidates_for(facts[key], need=Decimal(need))


def _world(db):
    """One line owing 10 at a location with nothing in it, and three donors.

    Shaped after the live board (SO403765 line 3, `B2155-NL-BLUE`), because that is where the
    worst-case rule was caught recommending the wrong donor:

      * BIG      on hand 11000, 140 owed          -> available 10860, free 11000
      * TIGHT    on hand  7000, nothing owed, 10 reserved -> available 7000, free 6990
      * PINCHED  on hand    50,  30 owed          -> available    20, free    50

    Ranked by what the DONOR keeps once this line's residual (21) is met, BIG comes first with
    10839 left. Ranked by the superseded worst case - availability less the donor's WHOLE free
    stock - TIGHT came first on +10 and BIG fell to third on -140, which is the recommendation
    the captain called wrong: a location holding 11,000 with 140 owed against it is plainly the
    safest place to take 21 units from.
    """
    company_id = _sorento(db)
    product = _product(db)
    own = _warehouse(db, f"ZZTOWN{_uid()[:6]}"[:20])
    big = _warehouse(db, f"ZZTBIG{_uid()[:6]}"[:20])
    tight = _warehouse(db, f"ZZTTIG{_uid()[:6]}"[:20])
    pinched = _warehouse(db, f"ZZTPIN{_uid()[:6]}"[:20])
    _stock(db, product, own, on_hand=0)
    _stock(db, product, big, on_hand=11000)
    # Reserved, so free (6990) is BELOW availability (7000). That gap is what made the worst
    # case rank this donor first, and it is why the fixture keeps it.
    _stock(db, product, tight, on_hand=7000, reserved=10)
    _stock(db, product, pinched, on_hand=50)

    mine = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}")
    _line(db, mine, product, own, qty="10")
    # The book's own demand at two of the donors, on a different sales order.
    theirs = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}")
    _line(db, theirs, product, big, qty="140")
    _line(db, theirs, product, pinched, qty="30")
    return company_id, product, own, big, tight, pinched, mine


def test_a_donor_states_autocounts_triple_beside_the_engines_own_figures():
    with blank_session() as db:
        company_id, product, own, big, _tight, _pinched, _mine = _world(db)
        _spo(db, company_id, product, big, qty=15, arrival=date(2026, 8, 25))

        candidates = _candidates(db, product, own)
        donor = {c["warehouse_code"]: c for c in candidates}[big.warehouse_code]

        assert donor["qty_on_hand"] == "11000"
        assert donor["so_qty"] == "140"
        assert donor["spo_qty"] == "15"
        # on hand - SO + SPO, and never clamped.
        assert donor["available_qty"] == "10875"
        assert donor["qty_free"] == "11000"
        assert donor["qty_committed"] == "0"


def test_a_donor_the_book_has_already_sold_reports_a_negative_available():
    """Free stock alone said 50; the book has sold 30 of it. Both numbers are stated."""
    with blank_session() as db:
        _company_id, product, own, _big, _tight, pinched, _mine = _world(db)

        donor = next(
            c
            for c in _candidates(db, product, own)
            if c["warehouse_code"] == pinched.warehouse_code
        )
        assert donor["qty_free"] == "50"
        assert donor["so_qty"] == "30"
        assert donor["available_qty"] == "20"
        assert donor["free_qty"] == "50"


def test_a_donor_states_what_meeting_this_line_would_leave_it_with():
    """The need is the asking line's residual, and it travels beside the answer so the screen
    can show the default "After borrow" figure before anybody types a quantity."""
    with blank_session() as db:
        _company_id, product, own, big, tight, pinched, _mine = _world(db)

        by_code = {c["warehouse_code"]: c for c in _candidates(db, product, own)}

        assert by_code[big.warehouse_code]["need_qty"] == "21"
        assert by_code[big.warehouse_code]["available_after_need"] == "10839"
        assert by_code[tight.warehouse_code]["available_after_need"] == "6979"
        # Signed, like availability itself: this donor cannot meet the line without going short.
        assert by_code[pinched.warehouse_code]["available_after_need"] == "-1"


def test_donors_are_ranked_by_what_meeting_this_line_leaves_them():
    """The correction. `available_after_need` descending, then availability, then free.

    The superseded rule judged a donor on giving away the WHOLE of its free stock, so a
    location holding 11,000 with 140 owed against it ranked BELOW one holding 7,000 with
    nothing owed - and recommending the second to cover 21 units is the wrong answer.
    """
    with blank_session() as db:
        _company_id, product, own, big, tight, pinched, _mine = _world(db)

        candidates = _candidates(db, product, own)

        assert [c["warehouse_code"] for c in candidates] == [
            big.warehouse_code,
            tight.warehouse_code,
            pinched.warehouse_code,
            # BORROW-OWN-LOC: the line's own location, always offered, always last.
            own.warehouse_code,
        ]


def test_only_the_first_donor_is_recommended():
    with blank_session() as db:
        _company_id, product, own, _big, _tight, _pinched, _mine = _world(db)

        candidates = _candidates(db, product, own)

        assert [c["recommended"] for c in candidates] == [True, False, False, False]


def test_the_board_ranks_against_the_buy_it_is_offering_a_borrow_for():
    """The board narrows the candidate to named keys, so a field it forgets is a field the
    screen cannot show - and the need it ranks against is the line's own proposed Buy (10
    here: nothing is at the line's own location)."""
    from app.services.project_fulfilment_board_service import FulfilmentBoardService

    with blank_session() as db:
        _company_id, product, own, big, tight, pinched, mine = _world(db)
        db.flush()

        board = FulfilmentBoardService(db).build(
            [mine.so_number], granularity="week", as_of=TODAY
        )
        cell = next(c for c in board["cells"] if c["item_code"] == product.product_code)
        contribution = cell["contributions"][0]
        candidates = contribution["borrow_candidates"]

        assert contribution["qty_proposed_buy"] == "10"
        assert [c["warehouse_code"] for c in candidates] == [
            big.warehouse_code,
            tight.warehouse_code,
            pinched.warehouse_code,
            # BORROW-OWN-LOC: the line's own location, always offered, always last.
            own.warehouse_code,
        ]
        assert candidates[0]["recommended"] is True
        assert candidates[0]["need_qty"] == "10"
        assert candidates[0]["available_qty"] == "10860"
        assert candidates[0]["available_after_need"] == "10850"
        assert candidates[2]["available_after_need"] == "10"
        assert candidates[2]["so_qty"] == "30"
