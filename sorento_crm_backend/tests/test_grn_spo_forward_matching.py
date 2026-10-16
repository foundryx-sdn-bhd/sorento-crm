"""The GRN and its SPO arrive in whatever order the supplier and the warehouse
produce them, and the link has to survive either order.

`grn_spo_matching` owns the allocation pool and the draw. The GRN lines import
draws from it going forwards; `forward_match_grn_lines_for_spo` draws from it
going backwards, over the lines a previous import stated but could not place.
Both directions run THE SAME code (AC-FM-26), which is what makes the two orders
produce the same rows (AC-FM-19).

Every test seeds its own chain from an empty schema - nothing here borrows a live
row, so the suite says the same thing in CI (empty database) as it does locally
(a copy of production).
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Optional
from unittest.mock import patch

import pytest
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.models.base import set_company_scope
from app.models.inventory import Warehouse
from app.models.procurement import (
    InboundShipment,
    PickingHeader,
    PickingLine,
    SPOAllocation,
)
from app.models.product import Product, ProductCategory, UnitOfMeasure
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from app.services.grn_spo_matching import (
    Draw,
    PoolEntry,
    build_allocation_pool,
    draw_fifo,
    forward_match_grn_lines_for_spo,
    forward_match_grn_lines_for_spo_best_effort,
)
from app.services.procurement_service import _spo_match_key

from ._pg_fixture import blank_session, unique_code

SPO = "SPO-2026/07-0012"


@pytest.fixture(autouse=True)
def _scope_listeners():
    register_company_scope_listeners()


class World:
    """The seeded chain, and the small vocabulary the tests speak in.

    Every row gets an EXPLICIT ``created_at``, one minute apart in creation order.
    ``func.now()`` is the TRANSACTION timestamp in Postgres, so rows written by one
    test would otherwise all share a created_at and "the older allocation" would be
    decided by the uuid tie-break - i.e. by a coin toss, which is how a FIFO
    assertion passes locally and fails in CI.
    """

    def __init__(self, db):
        self.db = db
        self._clock = datetime(2026, 6, 1, 8, 0, 0)
        self.category = str(uuid.uuid4())
        self.uom = str(uuid.uuid4())
        db.add(ProductCategory(id=self.category, category_code=unique_code("C")[:50], category_name="ZZT"))
        db.add(UnitOfMeasure(id=self.uom, uom_code=unique_code("U")[:20], uom_name="Each"))
        self.shipment = str(uuid.uuid4())
        # The container number is how the SPO Excel import finds this shipment, so
        # the tests that drive the REAL import can reach it.
        self.container = unique_code("CT")[:50]
        db.add(
            InboundShipment(
                id=self.shipment,
                shipment_number=unique_code("SHP")[:50],
                shipping_container_number=self.container,
                shipment_date=date(2026, 6, 1),
            )
        )
        db.flush()

    def company(self) -> str:
        """A second company, so a cross-company draw is representable at all."""
        from app.models.company import Company

        cid = str(uuid.uuid4())
        code = unique_code("CO")[:20]
        self.db.add(Company(id=cid, name=code, code=code, is_active=True))
        self.db.flush()
        return cid

    def _tick(self) -> datetime:
        self._clock += timedelta(minutes=1)
        return self._clock

    def product(self) -> str:
        pid = str(uuid.uuid4())
        code = unique_code("P")[:50]
        self.db.add(
            Product(
                id=pid, product_code=code, product_name=code, category_id=self.category,
                base_uom_id=self.uom, list_price=10, is_active=True,
            )
        )
        self.db.flush()
        return pid

    def warehouse(self) -> str:
        wid = str(uuid.uuid4())
        code = unique_code("W")[:50]
        self.db.add(Warehouse(id=wid, warehouse_code=code, warehouse_name=code))
        self.db.flush()
        return wid

    def allocation(
        self,
        *,
        product_id: str,
        warehouse_id: str,
        quantity: int,
        spo_number: str = SPO,
        received: int = 0,
        stated: Optional[int] = None,
        source_system: Optional[str] = None,
        company_id: Optional[str] = None,
        cancelled: int = 0,
    ) -> str:
        aid = str(uuid.uuid4())
        self.db.add(
            SPOAllocation(
                id=aid, spo_number=spo_number, inbound_shipment_id=self.shipment,
                warehouse_id=warehouse_id, product_id=product_id,
                allocated_quantity=quantity, quantity_received=received,
                stated_received=stated, source_system=source_system,
                created_at=self._tick(), company_id=company_id, qty_cancelled=cancelled,
            )
        )
        self.db.flush()
        return aid

    def grn(
        self,
        *,
        status: str = "draft",
        spo_number: Optional[str] = None,
        company_id: Optional[str] = None,
    ) -> str:
        hid = str(uuid.uuid4())
        self.db.add(
            PickingHeader(
                id=hid, picking_number=unique_code("GR")[:50], picking_type="goods_received",
                picking_date=date(2026, 7, 1), picking_status=status,
                inspection_status="pending", spo_number=spo_number,
                created_at=self._tick(), company_id=company_id,
            )
        )
        self.db.flush()
        return hid

    def line(
        self,
        *,
        header_id: str,
        product_id: str,
        warehouse_id: Optional[str],
        quantity: Optional[int] = None,
        quantity_expected: Optional[int] = None,
        quantity_picked: Optional[int] = None,
        allocation_id: Optional[str] = None,
        spo_number_raw: Optional[str] = SPO,
        company_id: Optional[str] = None,
    ) -> str:
        """``quantity`` fills BOTH quantity columns, which is what every writer of a
        whole receipt line does.

        The two are ALSO settable independently, because they have not always been
        equal: ``_create_grn_lines_with_spo_fifo`` used to put the whole document
        quantity on a split's first chunk and 0 on every later one, leaving the
        drawn quantity only in ``quantity_picked``. A helper that could only write
        them equal is exactly why the suite could not see that the pool was
        measuring the wrong column.
        """
        expected = quantity_expected if quantity_expected is not None else quantity
        picked = quantity_picked if quantity_picked is not None else quantity
        assert expected is not None and picked is not None, "give quantity, or both columns"
        lid = str(uuid.uuid4())
        self.db.add(
            PickingLine(
                id=lid, picking_header_id=header_id, product_id=product_id,
                source_warehouse_id=warehouse_id, quantity_expected=expected,
                quantity_picked=picked, spo_allocation_id=allocation_id,
                spo_number_raw=spo_number_raw, picked_condition="good",
                created_at=self._tick(), company_id=company_id,
            )
        )
        self.db.flush()
        return lid

    def lines_of(self, header_id: str) -> list[PickingLine]:
        return (
            self.db.query(PickingLine)
            .filter(PickingLine.picking_header_id == header_id)
            .order_by(PickingLine.created_at, PickingLine.id)
            .all()
        )

    def drawn_total(self, allocation_id: str) -> int:
        """``quantity_picked`` is the quantity a line drew - see the convention note
        on ``build_allocation_pool``."""
        return int(
            self.db.query(func.coalesce(func.sum(PickingLine.quantity_picked), 0))
            .filter(PickingLine.spo_allocation_id == allocation_id)
            .scalar()
        )


@pytest.fixture
def world():
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        yield World(db)


# ---------------------------------------------------------------------------
# The normalizer twins: Python `_spo_match_key` and the SQL expression the
# forward-matching filter (and its index) run. A candidate found in SQL that the
# Python comparison then rejects is the classic failure of a normalizer pair.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "variant",
    [
        "SPO-202606-0095",
        "SPO-2026/06-0095",
        "SPO-2026.06-0095",
        "SPO 2026 06 0095",
        "spo-2026/06-0095",
        "  SPO-2026/06-0095  ",
    ],
)
def test_the_python_and_sql_match_keys_agree(world, variant):
    """The same set stripped on both sides: `[^A-Za-z0-9]`, then uppercase."""
    in_sql = world.db.execute(
        text("SELECT upper(regexp_replace(:v, '[^A-Za-z0-9]', '', 'g'))"), {"v": variant}
    ).scalar()

    assert _spo_match_key(variant) == in_sql == "SPO2026060095"


# ---------------------------------------------------------------------------
# draw_fifo - the import's two-pass rule, extracted so both directions run it.
# ---------------------------------------------------------------------------

def test_the_pool_is_drawn_oldest_first():
    """AC-FM-14. A (60) was created before B (100); a line of 130 takes all of A
    and 70 of B, and nothing is left over."""
    pool = [PoolEntry("A", "W1", 60), PoolEntry("B", "W1", 100)]

    draws = draw_fifo(pool, warehouse_id="W1", quantity=130)

    assert draws == [Draw("A", 60), Draw("B", 70)]
    assert [entry.available for entry in pool] == [0, 30]


def test_the_lines_own_warehouse_is_preferred_over_age():
    """AC-FM-15. The import's two-pass rule: same warehouse first, then any."""
    pool = [PoolEntry("A", "W2", 100), PoolEntry("B", "W1", 100)]

    draws = draw_fifo(pool, warehouse_id="W1", quantity=150)

    assert draws == [Draw("B", 100), Draw("A", 50)]


def test_an_uncovered_remainder_is_a_single_trailing_unlinked_draw():
    """AC-FM-16. 160 available against a line of 200."""
    pool = [PoolEntry("A", "W1", 60), PoolEntry("B", "W1", 100)]

    draws = draw_fifo(pool, warehouse_id="W1", quantity=200)

    assert draws == [Draw("A", 60), Draw("B", 100), Draw(None, 40)]


def test_an_empty_pool_draws_the_whole_quantity_as_unlinked():
    assert draw_fifo([], warehouse_id="W1", quantity=25) == [Draw(None, 25)]


def test_one_pool_serves_a_whole_group_of_lines():
    """`available` is decremented in place, so the second line cannot re-draw what
    the first already took."""
    pool = [PoolEntry("A", "W1", 100)]

    first = draw_fifo(pool, warehouse_id="W1", quantity=70)
    second = draw_fifo(pool, warehouse_id="W1", quantity=70)

    assert first == [Draw("A", 70)]
    assert second == [Draw("A", 30), Draw(None, 40)]


# ---------------------------------------------------------------------------
# build_allocation_pool - the one behaviour change, and the reason for it.
# ---------------------------------------------------------------------------

def test_the_pool_holds_what_an_allocation_has_not_yet_been_drawn_for(world):
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)

    pool = build_allocation_pool(world.db, product_id=product, spo_number=SPO)

    assert [(e.allocation_id, e.available) for e in pool] == [(allocation, 100)]


def test_a_line_on_another_grn_consumes_capacity_even_unapproved(world):
    """AC-FM-17. Stored `quantity_received` is only written on APPROVAL, so a pool
    built from it would hand the same 50 out twice - which is exactly the
    double-link forward matching must never make."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=50)
    other = world.grn(status="draft")
    world.line(
        header_id=other, product_id=product, warehouse_id=warehouse,
        quantity=50, allocation_id=allocation,
    )

    pool = build_allocation_pool(world.db, product_id=product, spo_number=SPO)

    assert pool == []


def test_a_rejected_grns_lines_release_their_capacity(world):
    """AC-FM-17b at the pool, not only at the candidate filter. A GRN links while
    a draft (the normal imported state) and `update_grn` only unlinks on leaving
    APPROVED, so rejecting a draft keeps the link rows - but a rejected receipt
    must not consume allocation capacity, or the 50 it never took is withheld from
    every later GRN and from forward matching, forever."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=50)
    rejected = world.grn(status="rejected")
    world.line(
        header_id=rejected, product_id=product, warehouse_id=warehouse,
        quantity=50, allocation_id=allocation,
    )

    pool = build_allocation_pool(world.db, product_id=product, spo_number=SPO)

    assert [(e.allocation_id, e.available) for e in pool] == [(allocation, 50)]


def test_a_split_chunk_consumes_the_quantity_it_actually_drew(world):
    """The pool measures ``quantity_picked``, because that is the only column that
    is right on the rows already in the database.

    ``_create_grn_lines_with_spo_fifo`` used to write the whole document quantity
    onto a split's FIRST chunk and 0 onto every later one. A pool measured on
    ``quantity_expected`` therefore charged one line's entire draw to its first
    allocation and NOTHING to the rest - so the 70 the second allocation had
    already given up was offered again, to the next GRN or to forward matching.
    """
    product = world.product()
    w1, w2 = world.warehouse(), world.warehouse()
    first = world.allocation(product_id=product, warehouse_id=w1, quantity=30)
    second = world.allocation(product_id=product, warehouse_id=w2, quantity=100)
    grn = world.grn()
    world.line(
        header_id=grn, product_id=product, warehouse_id=w1,
        quantity_expected=100, quantity_picked=30, allocation_id=first,
    )
    world.line(
        header_id=grn, product_id=product, warehouse_id=w1,
        quantity_expected=0, quantity_picked=70, allocation_id=second,
    )

    pool = build_allocation_pool(world.db, product_id=product, spo_number=SPO)

    assert [(e.allocation_id, e.available) for e in pool] == [(second, 30)]


def test_a_grn_never_competes_with_itself(world):
    """`exclude_header_ids` is what makes a re-import idempotent: the GRN being
    written does not count as consumption of the capacity it is about to draw."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=50)
    mine = world.grn()
    world.line(
        header_id=mine, product_id=product, warehouse_id=warehouse,
        quantity=50, allocation_id=allocation,
    )

    pool = build_allocation_pool(
        world.db, product_id=product, spo_number=SPO, exclude_header_ids={mine}
    )

    assert [(e.allocation_id, e.available) for e in pool] == [(allocation, 50)]


def test_a_receipt_no_picking_line_explains_still_consumes(world):
    """`quantity_received` written by an integration, with no line behind it, is
    real stock received. It has to consume capacity, or the same goods are
    allocated twice."""
    product, warehouse = world.product(), world.warehouse()
    world.allocation(product_id=product, warehouse_id=warehouse, quantity=100, received=30)

    pool = build_allocation_pool(world.db, product_id=product, spo_number=SPO)

    assert [e.available for e in pool] == [70]


def test_a_stored_receipt_its_own_lines_explain_is_not_counted_twice(world):
    """The subtraction that makes re-importing an APPROVED GRN safe: without it
    the pool comes out empty and the import unlinks its own lines."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=100, received=100
    )
    mine = world.grn(status="approved")
    world.line(
        header_id=mine, product_id=product, warehouse_id=warehouse,
        quantity=100, allocation_id=allocation,
    )

    pool = build_allocation_pool(
        world.db, product_id=product, spo_number=SPO, exclude_header_ids={mine}
    )

    assert [(e.allocation_id, e.available) for e in pool] == [(allocation, 100)]


# ---------------------------------------------------------------------------
# The mirror statement (`stated_received`): PLAN-grn-link-ignores-mirror-received.
# An AutoCount SPO mirror push can state a receipt BEFORE the GRN import that
# reports the same goods arrives - the mirror's `stated_received` and the pool's
# own `quantity_received` are the SAME GRN seen from AutoCount, so it must not be
# subtracted twice: once as "stated" and again as "received with no line".
# ---------------------------------------------------------------------------

def test_a_mirror_stated_receipt_does_not_consume_capacity(world):
    """AC-MR-1. A reconciliation figure the mirror already wrote is not a receipt
    with no line behind it - the line that explains it just has not landed yet."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=100, received=100, stated=100,
    )

    pool = build_allocation_pool(world.db, product_id=product, spo_number=SPO)

    assert [(e.allocation_id, e.available) for e in pool] == [(allocation, 100)]


def test_a_mirror_stated_receipt_is_explained_once_the_line_links(world):
    """AC-MR-2. Once a GRN line links to the allocation the mirror already stated,
    a DIFFERENT GRN sees no capacity on it (the line took it), while the SAME GRN,
    excluded, sees all of it (re-import must not unlink itself)."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=100, received=100, stated=100,
    )
    grn_a = world.grn(status="approved")
    world.line(
        header_id=grn_a, product_id=product, warehouse_id=warehouse,
        quantity=100, allocation_id=allocation,
    )

    pool_for_b = build_allocation_pool(world.db, product_id=product, spo_number=SPO)
    pool_for_a = build_allocation_pool(
        world.db, product_id=product, spo_number=SPO, exclude_header_ids={grn_a}
    )

    assert pool_for_b == []
    assert [(e.allocation_id, e.available) for e in pool_for_a] == [(allocation, 100)]


def test_only_the_unexplained_part_of_a_stored_receipt_consumes(world):
    """AC-MR-3. AC-FM-28 kept: a write that is neither STATED nor LINKED is still a
    real receipt and still consumes capacity - only the mirror-explained part does
    not."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=100, received=130, stated=100,
    )

    pool = build_allocation_pool(world.db, product_id=product, spo_number=SPO)

    assert [e.available for e in pool] == [70]


def test_a_cancel_po_qty_is_not_capacity_for_a_later_grn(world):
    """AC-CONSIGN-CRM part C. AutoCount Cancel PO adds to TransferedQty (`stated_received`),
    which the mirror rule above leaves as capacity. The cancelled part never arrives, so a
    later GRN must not draw it: 100 allocated, 20 cancelled, nothing received yet = 80."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=100, received=20, stated=20,
        cancelled=20,
    )

    pool = build_allocation_pool(world.db, product_id=product, spo_number=SPO)

    assert [(e.allocation_id, e.available) for e in pool] == [(allocation, 80)]


def test_a_fully_cancelled_line_offers_nothing_once_its_grn_linked(world):
    """AC-CONSIGN-CRM part C. 100 allocated, 80 received on a linked GRN, 20 cancelled:
    another GRN finds nothing left on the line."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=100, received=100, stated=100,
        cancelled=20,
    )
    grn_a = world.grn(status="approved")
    world.line(header_id=grn_a, product_id=product, warehouse_id=warehouse,
               quantity=80, allocation_id=allocation)

    pool_for_b = build_allocation_pool(world.db, product_id=product, spo_number=SPO)
    pool_for_a = build_allocation_pool(
        world.db, product_id=product, spo_number=SPO, exclude_header_ids={grn_a}
    )

    assert pool_for_b == []
    assert [(e.allocation_id, e.available) for e in pool_for_a] == [(allocation, 80)]


def test_a_grn_imported_after_the_mirror_statement_links_end_to_end(world):
    """AC-MR-4. The mirror stated the receipt first; the GRN import runs the same
    draw a fresh import makes and must still place its line (one linked draw, no
    trailing unlinked remainder). Syncing the received total afterwards must not
    regress the figure the mirror already wrote (the D28c floor - `source_system`
    has to be `autocount` for that floor to apply at all, or the sync takes the
    non-AutoCount branch and the claim is untested)."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=329, received=329, stated=329,
        source_system="autocount",
    )
    world.db.get(SPOAllocation, allocation).spo_line_number = 1

    pool = build_allocation_pool(world.db, product_id=product, spo_number=SPO)
    draws = draw_fifo(pool, warehouse_id=warehouse, quantity=329)

    assert draws == [Draw(allocation, 329)]

    grn = world.grn(status="approved")
    world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity=329, allocation_id=allocation,
    )

    from app.services.procurement_service import PickingHeaderService

    PickingHeaderService(world.db).sync_grn_received_to_spo(grn)

    assert world.db.get(SPOAllocation, allocation).quantity_received == 329


def test_re_importing_an_orphan_grn_links_in_place_instead_of_doubling(world):
    """B1 (review round 2). An approved GRN imported before its SPO existed (or
    before the pool could draw it, the exact prod shape: mirror-stated,
    unlinked) leaves an ORPHAN line: stated but `spo_allocation_id IS NULL`. The
    mirror states the same receipt, and the office re-uploads the SAME sheet - a
    normal thing to do.

    `upsert_grn_line_for_import` used to match a draw's row on
    `(header, product, source_warehouse, spo_allocation_id)`. Once the pool (B1's
    sibling fix above) hands the draw a REAL allocation id, that tuple can never
    match the orphan row already sitting there with `spo_allocation_id IS NULL` -
    it inserted a SIBLING instead: the GRN read two lines for one sheet row,
    picked 200 for a 100-unit receipt, and the container reported 200 received.
    """
    from app.models.product import Product

    product, warehouse = world.product(), world.warehouse()
    product_code = world.db.get(Product, product).product_code
    warehouse_code = world.db.get(Warehouse, warehouse).warehouse_code

    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=100, received=100, stated=100,
        source_system="autocount",
    )
    world.db.get(SPOAllocation, allocation).spo_line_number = 1

    grn_number = unique_code("GR")[:50]
    header_id = str(uuid.uuid4())
    world.db.add(
        PickingHeader(
            id=header_id, picking_number=grn_number, picking_type="goods_received",
            picking_date=date(2026, 7, 1), picking_status="approved",
            inspection_status="pending", spo_number=SPO,
        )
    )
    world.db.add(
        PickingLine(
            id=str(uuid.uuid4()), picking_header_id=header_id, product_id=product,
            source_warehouse_id=warehouse, quantity_expected=100, quantity_picked=100,
            spo_allocation_id=None, spo_number_raw=SPO,
        )
    )
    world.db.commit()

    _import_grn_lines(
        world.db,
        _grn_lines_workbook(
            [[grn_number, "2026-07-01", product_code, warehouse_code, 100, SPO]]
        ),
    )

    lines = world.lines_of(header_id)
    assert len(lines) == 1, "the orphan was linked in place, not duplicated"
    assert lines[0].quantity_picked == 100
    assert lines[0].spo_allocation_id == allocation

    from app.services.procurement_service import InboundShipmentService

    received = InboundShipmentService(world.db).get_received_quantities_by_product(world.shipment)
    assert received == {product: 100}


def test_adoption_never_steals_an_orphan_that_states_another_spo(world):
    """B3 (review round 2). A multi-SPO GRN groups its rows by
    `(doc_no, product, effective_spo)` (`import_tasks.py`), so ONE import can
    produce an unlinked row stating one SPO alongside a linked draw for a
    DIFFERENT one on the SAME header/product/warehouse. The adoption branch
    (B1) must leave the first SPO's orphan for its OWN group's draw, or a
    100-unit sheet (40 on `SPO` unlinked + 60 on `other_spo` linked) lands as
    ONE line of 60 and the 40 is destroyed.
    """
    from app.models.product import Product

    other_spo = "SPO-2026/08-9999"
    product, warehouse = world.product(), world.warehouse()
    product_code = world.db.get(Product, product).product_code
    warehouse_code = world.db.get(Warehouse, warehouse).warehouse_code

    # SPO (the default) has NO allocation - its row stays unlinked.
    # other_spo has one - its draw carries a real allocation id.
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=100, spo_number=other_spo,
    )

    grn_number = unique_code("GR")[:50]
    header_id = str(uuid.uuid4())
    world.db.add(
        PickingHeader(
            id=header_id, picking_number=grn_number, picking_type="goods_received",
            picking_date=date(2026, 7, 1), picking_status="approved",
            inspection_status="pending", spo_number=SPO,
        )
    )
    world.db.commit()

    _import_grn_lines(
        world.db,
        _grn_lines_workbook(
            [
                [grn_number, "2026-07-01", product_code, warehouse_code, 40, SPO],
                [grn_number, "2026-07-01", product_code, warehouse_code, 60, other_spo],
            ]
        ),
    )

    lines = world.lines_of(header_id)
    assert len(lines) == 2
    assert sum(l.quantity_picked for l in lines) == 100
    unlinked = [l for l in lines if l.spo_allocation_id is None]
    assert [l.quantity_picked for l in unlinked] == [40]
    assert unlinked[0].spo_number_raw == SPO
    linked = [l for l in lines if l.spo_allocation_id == allocation]
    assert [l.quantity_picked for l in linked] == [60]


def test_the_import_refreshes_the_container_by_spo_number_when_nothing_links(world):
    """AC-MR-5. Drives the REAL import task (`process_grn_lines_import`), not a
    model of its two calls, so this test fails if the `sync_received_for_spo_number`
    sweep (A1) is ever removed from it.

    The allocation's whole 50 units are already spoken for by an unexplained
    external receipt BEFORE the import runs (AC-FM-28: `received` with no picking
    line and no mirror statement behind it) - so the pool has zero capacity left
    when this GRN's own line draws, and that line comes out unlinked. That is the
    "could not link at all" case A1 exists for: `sync_grn_received_to_spo` walks
    the header's OWN linked lines only, which is none, so without the sweep the
    container the office is looking at never updates.
    """
    from app.models.procurement import InboundShipmentLine
    from app.models.product import Product

    product, warehouse = world.product(), world.warehouse()
    product_code = world.db.get(Product, product).product_code
    warehouse_code = world.db.get(Warehouse, warehouse).warehouse_code
    world.db.add(
        InboundShipmentLine(
            id=str(uuid.uuid4()), shipment_id=world.shipment, product_id=product,
            quantity_shipped=50, uom_id=world.uom,
        )
    )
    world.allocation(product_id=product, warehouse_id=warehouse, quantity=50, received=50)
    grn_number = unique_code("GR")[:50]
    header_id = str(uuid.uuid4())
    world.db.add(
        PickingHeader(
            id=header_id, picking_number=grn_number, picking_type="goods_received",
            picking_date=date(2026, 7, 1), picking_status="approved",
            inspection_status="pending", spo_number=SPO,
        )
    )
    world.db.commit()

    _import_grn_lines(
        world.db,
        _grn_lines_workbook(
            [[grn_number, "2026-07-01", product_code, warehouse_code, 50, SPO]]
        ),
    )

    lines = world.lines_of(header_id)
    assert len(lines) == 1
    assert lines[0].spo_allocation_id is None, "the allocation's capacity was already spoken for"

    line = (
        world.db.query(InboundShipmentLine)
        .filter(InboundShipmentLine.shipment_id == world.shipment)
        .one()
    )
    assert line.line_status == "received"
    assert world.db.get(InboundShipment, world.shipment).shipment_status == "fully_received"


def test_the_pool_tolerates_separator_style_and_only_that(world):
    """AC-FM-12."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=10, spo_number="SPO-202607-0012"
    )

    assert [e.allocation_id for e in build_allocation_pool(
        world.db, product_id=product, spo_number="SPO-2026/07-0012"
    )] == [allocation]
    assert build_allocation_pool(
        world.db, product_id=product, spo_number="SPO-2026/07-0013"
    ) == []


def test_a_blank_spo_number_pools_nothing(world):
    product, warehouse = world.product(), world.warehouse()
    world.allocation(product_id=product, warehouse_id=warehouse, quantity=10)

    assert build_allocation_pool(world.db, product_id=product, spo_number="  ") == []
    assert build_allocation_pool(world.db, product_id=product, spo_number=None) == []


# ---------------------------------------------------------------------------
# forward_match_grn_lines_for_spo - the other half of the journey.
# ---------------------------------------------------------------------------

def test_creating_the_allocation_links_the_waiting_line(world):
    """AC-FM-11. The GRN landed days before its SPO; the user re-opens it and the
    line is a link, having gone back to nothing and re-uploaded nothing."""
    product, warehouse = world.product(), world.warehouse()
    grn = world.grn()
    line = world.line(header_id=grn, product_id=product, warehouse_id=warehouse, quantity=40)
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)

    result = forward_match_grn_lines_for_spo(world.db, SPO)

    assert result.linked_lines == 1
    assert world.db.get(PickingLine, line).spo_allocation_id == allocation


def test_the_allocations_received_totals_are_re_synced(world):
    """AC-FM-11's second half - what every other stakeholder is told automatically."""
    product, warehouse = world.product(), world.warehouse()
    grn = world.grn(status="approved")
    world.line(header_id=grn, product_id=product, warehouse_id=warehouse, quantity=100)
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)

    forward_match_grn_lines_for_spo(world.db, SPO)

    row = world.db.get(SPOAllocation, allocation)
    assert row.quantity_received == 100
    assert row.receipt_status == "fully_received"


def test_matching_tolerates_separator_style_and_nothing_else(world):
    """AC-FM-12. `SPO-2026/07-0012` and `SPO-202607-0012` are the same SPO written
    two ways; `...-0013` is a different SPO."""
    product, warehouse = world.product(), world.warehouse()
    grn = world.grn()
    line = world.line(header_id=grn, product_id=product, warehouse_id=warehouse, quantity=10)
    world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=100, spo_number="SPO-2026/07-0013"
    )

    forward_match_grn_lines_for_spo(world.db, "SPO-2026/07-0013")
    assert world.db.get(PickingLine, line).spo_allocation_id is None

    compact = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=100, spo_number="SPO-202607-0012"
    )
    forward_match_grn_lines_for_spo(world.db, "SPO-202607-0012")

    assert world.db.get(PickingLine, line).spo_allocation_id == compact


def test_an_already_linked_line_is_left_exactly_as_it_is(world):
    """AC-FM-13."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=200)
    grn = world.grn()
    linked = world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity=30, allocation_id=allocation,
    )
    world.line(header_id=grn, product_id=product, warehouse_id=warehouse, quantity=20)
    world.db.commit()
    before = world.db.get(PickingLine, linked)
    before_updated_at = before.updated_at

    forward_match_grn_lines_for_spo(world.db, SPO)

    after = world.db.get(PickingLine, linked)
    assert after.spo_allocation_id == allocation
    assert (after.quantity_expected, after.quantity_picked) == (30, 30)
    assert after.updated_at == before_updated_at


def test_the_pool_is_drawn_oldest_first_end_to_end(world):
    """AC-FM-14, through the real query rather than a hand-built pool.

    The two allocations sit in DIFFERENT warehouses, neither of them the line's:
    `uk_spo_allocations_spo_number_product_warehouse` makes "two allocations for
    one SPO and product in the SAME warehouse" unrepresentable, so age is the only
    thing left to order them by - which is what this asserts.
    """
    product = world.product()
    warehouse, w1, w2 = world.warehouse(), world.warehouse(), world.warehouse()
    grn = world.grn()
    line = world.line(header_id=grn, product_id=product, warehouse_id=warehouse, quantity=130)
    first = world.allocation(product_id=product, warehouse_id=w1, quantity=60)
    second = world.allocation(product_id=product, warehouse_id=w2, quantity=100)

    forward_match_grn_lines_for_spo(world.db, SPO)

    placed = {
        (l.spo_allocation_id, l.quantity_expected) for l in world.lines_of(grn)
    }
    assert placed == {(first, 60), (second, 70)}
    assert all(l.spo_allocation_id is not None for l in world.lines_of(grn))
    assert world.db.get(PickingLine, line).spo_allocation_id == first


def test_the_lines_own_warehouse_beats_the_older_allocation(world):
    """AC-FM-15."""
    product = world.product()
    w1, w2 = world.warehouse(), world.warehouse()
    grn = world.grn()
    world.line(header_id=grn, product_id=product, warehouse_id=w1, quantity=50)
    world.allocation(product_id=product, warehouse_id=w2, quantity=100)
    same_warehouse = world.allocation(product_id=product, warehouse_id=w1, quantity=100)

    forward_match_grn_lines_for_spo(world.db, SPO)

    assert [l.spo_allocation_id for l in world.lines_of(grn)] == [same_warehouse]


def test_an_uncovered_remainder_stays_stated_but_unmatched(world):
    """AC-FM-16. The 40 nobody allocated still says which SPO it claims."""
    product = world.product()
    warehouse, other = world.warehouse(), world.warehouse()
    grn = world.grn()
    world.line(header_id=grn, product_id=product, warehouse_id=warehouse, quantity=200)
    world.allocation(product_id=product, warehouse_id=warehouse, quantity=60)
    world.allocation(product_id=product, warehouse_id=other, quantity=100)

    forward_match_grn_lines_for_spo(world.db, SPO)

    lines = world.lines_of(grn)
    assert sum(l.quantity_expected for l in lines if l.spo_allocation_id) == 160
    remainder = [l for l in lines if l.spo_allocation_id is None]
    assert [l.quantity_expected for l in remainder] == [40]
    assert remainder[0].spo_number_raw == SPO


def test_forward_matching_never_over_draws(world):
    """AC-FM-17. The 50 was already taken by a line on ANOTHER GRN, and the stored
    quantity_received says 0 because that GRN was never approved."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=50)
    other = world.grn()
    world.line(
        header_id=other, product_id=product, warehouse_id=warehouse,
        quantity=50, allocation_id=allocation,
    )
    mine = world.grn()
    waiting = world.line(header_id=mine, product_id=product, warehouse_id=warehouse, quantity=20)

    forward_match_grn_lines_for_spo(world.db, SPO)

    assert world.db.get(PickingLine, waiting).spo_allocation_id is None
    assert world.drawn_total(allocation) == 50
    assert len(world.lines_of(mine)) == 1, "nothing was split off either"


def test_linking_a_legacy_split_remainder_does_not_inflate_the_received_total(world):
    """A row written by the OLD splitter carries 0 in ``quantity_expected``, because
    its sibling chunk carries the whole document quantity. Raising it to the drawn
    quantity when the row is finally linked counts that quantity TWICE on the GRN,
    and ``compute_received_for_allocation`` then reports a receipt bigger than the
    one that happened - here flipping a 60-of-100 receipt to ``fully_received``.
    """
    product = world.product()
    w1, w2 = world.warehouse(), world.warehouse()
    partly_drawn = world.allocation(product_id=product, warehouse_id=w1, quantity=100)
    grn = world.grn(status="approved", spo_number=SPO)
    world.line(
        header_id=grn, product_id=product, warehouse_id=w1,
        quantity_expected=100, quantity_picked=60, allocation_id=partly_drawn,
    )
    remainder = world.line(
        header_id=grn, product_id=product, warehouse_id=w2,
        quantity_expected=0, quantity_picked=40,
    )
    covers_the_remainder = world.allocation(product_id=product, warehouse_id=w2, quantity=40)

    forward_match_grn_lines_for_spo(world.db, SPO)

    placed = world.db.get(PickingLine, remainder)
    assert placed.spo_allocation_id == covers_the_remainder
    assert placed.quantity_picked == 40
    assert placed.quantity_expected == 0, "the sibling chunk already states this quantity"
    partly = world.db.get(SPOAllocation, partly_drawn)
    assert (partly.quantity_received, partly.receipt_status) == (60, "pending")
    covering = world.db.get(SPOAllocation, covers_the_remainder)
    assert (covering.quantity_received, covering.receipt_status) == (40, "fully_received")


def test_forward_matching_keeps_an_unsplit_lines_short_receipt(world):
    """The convention note is explicit that a receipt SPLIT across allocations
    cannot carry an expected-vs-picked discrepancy, and that an UNSPLIT one still
    can. Forward matching overwrote ``quantity_expected`` with the draw on every
    line it linked, split or not - so a line that expected 100 and received 60
    came out expecting 60, the 40 that never arrived disappeared, and
    ``quantity_discrepancy`` fell to 0. A short receipt is exactly the thing the
    office needs to see.
    """
    product, warehouse = world.product(), world.warehouse()
    grn = world.grn()
    line = world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity_expected=100, quantity_picked=60,
    )
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)

    forward_match_grn_lines_for_spo(world.db, SPO)

    placed = world.db.get(PickingLine, line)
    assert placed.spo_allocation_id == allocation
    assert (placed.quantity_expected, placed.quantity_picked) == (100, 60)
    assert len(world.lines_of(grn)) == 1, "one draw is not a split"
    # Only what was picked drew on the allocation.
    assert world.drawn_total(allocation) == 60


def test_a_split_receipt_cannot_carry_a_discrepancy(world):
    """The other side of the same rule, unchanged: once the receipt IS split across
    allocations, the split is the fact, so every chunk states the quantity it
    drew in both columns."""
    product = world.product()
    w1, w2 = world.warehouse(), world.warehouse()
    world.allocation(product_id=product, warehouse_id=w1, quantity=40)
    world.allocation(product_id=product, warehouse_id=w2, quantity=100)
    grn = world.grn()
    world.line(
        header_id=grn, product_id=product, warehouse_id=w1,
        quantity_expected=100, quantity_picked=60,
    )

    forward_match_grn_lines_for_spo(world.db, SPO)

    assert sorted(
        (l.quantity_expected, l.quantity_picked) for l in world.lines_of(grn)
    ) == [(20, 20), (40, 40)]


def test_a_rejected_grn_is_never_forward_matched(world):
    """AC-FM-17b. A rejected receipt must not consume allocation capacity. A draft
    one is matched - that is the normal state of an imported GRN."""
    product, warehouse = world.product(), world.warehouse()
    rejected = world.grn(status="rejected")
    on_rejected = world.line(
        header_id=rejected, product_id=product, warehouse_id=warehouse, quantity=10
    )
    draft = world.grn(status="draft")
    on_draft = world.line(header_id=draft, product_id=product, warehouse_id=warehouse, quantity=10)
    world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)

    forward_match_grn_lines_for_spo(world.db, SPO)

    assert world.db.get(PickingLine, on_rejected).spo_allocation_id is None
    assert world.db.get(PickingLine, on_draft).spo_allocation_id is not None


def test_running_it_again_changes_nothing(world):
    """AC-FM-18. The hooks fire on every allocation write, so "again" is the
    normal case, not an edge one."""
    product, warehouse = world.product(), world.warehouse()
    grn = world.grn()
    world.line(header_id=grn, product_id=product, warehouse_id=warehouse, quantity=200)
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=60)
    forward_match_grn_lines_for_spo(world.db, SPO)
    before = sorted(
        (l.id, l.spo_allocation_id, l.quantity_expected) for l in world.lines_of(grn)
    )

    second = forward_match_grn_lines_for_spo(world.db, SPO)

    assert second.linked_lines == 0
    assert second.created_lines == 0
    assert sorted(
        (l.id, l.spo_allocation_id, l.quantity_expected) for l in world.lines_of(grn)
    ) == before
    assert world.drawn_total(allocation) == 60


def test_a_line_that_stated_nothing_is_not_a_candidate(world):
    product, warehouse = world.product(), world.warehouse()
    grn = world.grn()
    line = world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity=10, spo_number_raw=None,
    )
    world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)

    forward_match_grn_lines_for_spo(world.db, SPO)

    assert world.db.get(PickingLine, line).spo_allocation_id is None


def test_a_blank_spo_number_matches_nothing(world):
    assert forward_match_grn_lines_for_spo(world.db, "  ").candidate_lines == 0
    assert forward_match_grn_lines_for_spo(world.db, None).candidate_lines == 0


# ---------------------------------------------------------------------------
# Company isolation. `_resolve_api_key_scope` returns None - "all companies" - for
# an X-API-Key principal, so on that path the scope layer adds no predicate at all
# and every company's lines and allocations are visible in one query.
# ---------------------------------------------------------------------------

def test_a_line_never_draws_on_another_companys_allocation(world):
    """Under the all-companies scope the candidate query saw company B's line and
    the pool saw company A's allocation, and nothing between them said no."""
    other = world.company()
    product, warehouse = world.product(), world.warehouse()
    world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)
    grn = world.grn(company_id=other)
    line = world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity=40, company_id=other,
    )
    world.db.commit()
    set_company_scope(world.db, None)

    forward_match_grn_lines_for_spo(world.db, SPO, company_id=DEFAULT_COMPANY_ID)

    assert world.db.get(PickingLine, line).spo_allocation_id is None


def test_a_split_row_lands_on_its_own_grns_company(world):
    """The rows a split creates carried no company_id, so the insert hook stamped
    them - and under the all-companies scope that stamp is the INCUMBENT company.
    A remainder split off a company-B GRN then sat in Sorento: invisible on its own
    GRN, while still consuming that GRN's allocation."""
    other = world.company()
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=60, company_id=other
    )
    grn = world.grn(company_id=other)
    world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity=100, company_id=other,
    )
    world.db.commit()
    set_company_scope(world.db, None)

    forward_match_grn_lines_for_spo(world.db, SPO, company_id=other)

    rows = world.lines_of(grn)
    assert sorted(l.quantity_picked for l in rows) == [40, 60]
    assert [l.spo_allocation_id for l in rows if l.spo_allocation_id] == [allocation]
    assert {str(l.company_id) for l in rows} == {other}


# ---------------------------------------------------------------------------
# The other GRN-line writers. `update_grn` DELETES every picking line and
# recreates them on two branches, so left alone a single GRN edit would wipe the
# stated SPO off an imported GRN and un-forward-matchable it.
# ---------------------------------------------------------------------------

def _update(db, grn_id: str, **payload):
    from app.schemas.procurement import PickingHeaderUpdate
    from app.services.procurement_service import PickingHeaderService

    return PickingHeaderService(db).update_grn(grn_id, PickingHeaderUpdate(**payload))


def test_editing_a_grn_restates_the_headers_spo_on_every_recreated_line(world):
    """AC-FM-9b / AC-FM-9c, payload branch. The client sent no stated SPO, so the
    header's single-SPO value is what the recreated lines say."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)
    grn = world.grn(status="draft", spo_number=SPO)
    world.line(header_id=grn, product_id=product, warehouse_id=warehouse, quantity=10)

    _update(
        world.db, grn,
        picking_status="approved",
        picking_lines=[
            {
                "product_id": product, "quantity_expected": 10, "quantity_picked": 10,
                "source_warehouse_id": warehouse,
            }
        ],
    )

    lines = world.lines_of(grn)
    assert [l.spo_number_raw for l in lines] == [SPO]
    assert [l.spo_allocation_id for l in lines] == [allocation]


def test_approving_a_grn_splits_both_quantity_columns_together(world):
    """The two GRN-line writers now agree on where the drawn quantity lives.

    ``_create_grn_lines_with_spo_fifo`` used to leave the whole document quantity
    on the first chunk and write 0 on the rest, while the import writes the drawn
    quantity on every chunk. AC-FM-19 compares ``quantity_expected``, so the same
    receipt would come out differently depending on which writer produced it.
    """
    product = world.product()
    w1, w2 = world.warehouse(), world.warehouse()
    same_warehouse = world.allocation(product_id=product, warehouse_id=w1, quantity=30)
    elsewhere = world.allocation(product_id=product, warehouse_id=w2, quantity=100)
    grn = world.grn(status="draft", spo_number=SPO)

    _update(
        world.db, grn,
        picking_status="approved",
        picking_lines=[
            {
                "product_id": product, "quantity_expected": 100, "quantity_picked": 100,
                "source_warehouse_id": w1,
            }
        ],
    )

    placed = sorted(
        (l.spo_allocation_id, l.quantity_expected, l.quantity_picked)
        for l in world.lines_of(grn)
    )
    assert placed == sorted([(same_warehouse, 30, 30), (elsewhere, 70, 70)])


def test_approving_an_unsplit_short_receipt_keeps_its_discrepancy(world):
    """AC-FM-30 at the approval writer, the same guard forward matching applies:
    only a receipt actually SPLIT across allocations collapses
    ``quantity_expected`` to the draw. A single fully-covering draw is not a
    split, so a line that expected 100 and received 60 must come out (100, 60) -
    writing the draw into both columns erased the 40-unit shortfall, which is
    exactly the thing the office needs to see."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)
    grn = world.grn(status="draft", spo_number=SPO)

    _update(
        world.db, grn,
        picking_status="approved",
        picking_lines=[
            {
                "product_id": product, "quantity_expected": 100, "quantity_picked": 60,
                "source_warehouse_id": warehouse,
            }
        ],
    )

    lines = world.lines_of(grn)
    assert [
        (l.spo_allocation_id, l.quantity_expected, l.quantity_picked) for l in lines
    ] == [(allocation, 100, 60)]
    # Only what was picked drew on the allocation.
    assert world.drawn_total(allocation) == 60


def test_approving_a_grn_cannot_over_draw_what_a_draft_already_took(world):
    """AC-FM-17 held at the approval writer, not only at the import and forward
    matching.

    ``_create_grn_lines_with_spo_fifo`` sized availability with
    ``compute_received_for_allocation``, which counts APPROVED headers only - so a
    draft GRN's draw was invisible to it. A draft is the normal state of an
    imported GRN, so the office approving an unrelated receipt through the screen
    could hand out capacity the draft had already taken: 160 units drawing on a
    100-unit allocation, with nothing on either GRN saying so.
    """
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)
    draft = world.grn(status="draft", spo_number=SPO)
    world.line(
        header_id=draft, product_id=product, warehouse_id=warehouse,
        quantity=60, allocation_id=allocation,
    )
    approving = world.grn(status="draft", spo_number=SPO)

    _update(
        world.db, approving,
        picking_status="approved",
        picking_lines=[
            {
                "product_id": product, "quantity_expected": 100, "quantity_picked": 100,
                "source_warehouse_id": warehouse,
            }
        ],
    )

    placed = {
        (l.spo_allocation_id, l.quantity_expected, l.quantity_picked)
        for l in world.lines_of(approving)
    }
    assert placed == {(allocation, 40, 40), (None, 60, 60)}
    # The 60 the draft took plus the 40 this approval drew - the allocation is
    # exactly full, never over-drawn.
    assert world.drawn_total(allocation) == 100


def test_the_approval_writer_draws_through_the_shared_matcher(world, monkeypatch):
    """AC-FM-26 across all THREE writers. ``_create_grn_lines_with_spo_fifo`` was a
    third hand-rolled copy of the two-pass warehouse-then-age rule, and a copy is
    exactly how the writers come to disagree about which allocation a line drew
    from."""
    import app.services.grn_spo_matching as matching

    pooled: list[tuple] = []
    drawn: list[tuple] = []
    real_pool, real_draw = matching.build_allocation_pool, matching.draw_fifo

    def _pool_spy(db, **kwargs):
        pooled.append((kwargs.get("product_id"), kwargs.get("spo_number")))
        return real_pool(db, **kwargs)

    def _draw_spy(pool, *, warehouse_id, quantity):
        drawn.append((warehouse_id, quantity))
        return real_draw(pool, warehouse_id=warehouse_id, quantity=quantity)

    monkeypatch.setattr(matching, "build_allocation_pool", _pool_spy)
    monkeypatch.setattr(matching, "draw_fifo", _draw_spy)

    product, warehouse = world.product(), world.warehouse()
    world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)
    grn = world.grn(status="draft", spo_number=SPO)

    _update(
        world.db, grn,
        picking_status="approved",
        picking_lines=[
            {
                "product_id": product, "quantity_expected": 10, "quantity_picked": 10,
                "source_warehouse_id": warehouse,
            }
        ],
    )

    assert pooled == [(product, SPO)]
    assert drawn == [(warehouse, 10)]


def test_the_approval_writer_does_not_compete_with_the_grn_it_is_rewriting(world):
    """The rebuild DELETES and recreates this GRN's lines, so it must not see the
    rows it is about to replace as capacity somebody else took - the same reason
    the import excludes itself. Without the exclusion, re-approving a GRN whose
    lines are already linked would find an empty pool and unlink them.
    """
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=50)
    grn = world.grn(status="draft", spo_number=SPO)
    world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity=50, allocation_id=allocation,
    )

    _update(world.db, grn, picking_status="approved")

    lines = world.lines_of(grn)
    assert [(l.spo_allocation_id, l.quantity_picked) for l in lines] == [(allocation, 50)]


def test_re_editing_an_approved_grn_does_not_strand_its_allocation(world):
    """The same "do not compete with yourself" rule, but for the GRN that is
    ALREADY approved - the case the draft-GRN test above cannot reach.

    ``build_allocation_pool`` protects a re-write by SUBTRACTING the caller's own
    linked rows from the stored ``quantity_received`` before treating the excess as
    an integration's receipt. That subtraction only works while those rows are
    still in the table. ``update_grn`` deletes the lines FIRST and builds the pool
    afterwards, so on an approved GRN - whose ``quantity_received`` its own
    approval wrote - the stored 50 suddenly explains nothing, is read as an
    external receipt, and swallows the whole allocation. The GRN's line comes back
    UNLINKED, ``sync_grn_received_to_spo`` walks only linked lines and so leaves
    the allocation reporting 50 with nothing on any GRN explaining it, and the
    allocation is un-drawable by every later pool build. Nothing self-heals.
    """
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=50, received=50
    )
    grn = world.grn(status="approved", spo_number=SPO)
    world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity=50, allocation_id=allocation,
    )

    _update(
        world.db, grn,
        picking_lines=[
            {
                "product_id": product, "quantity_expected": 50, "quantity_picked": 50,
                "source_warehouse_id": warehouse,
            }
        ],
    )

    lines = world.lines_of(grn)
    assert [(l.spo_allocation_id, l.quantity_picked) for l in lines] == [(allocation, 50)]
    assert world.drawn_total(allocation) == 50
    # And the stored figure still has picking lines explaining it.
    assert world.db.get(SPOAllocation, allocation).quantity_received == 50


def test_re_pointing_an_approved_grn_releases_the_spo_it_left(world):
    """A regression guard for the early release above, not a second pin of it -
    this case passes either way, because when the SPO KEY changes the
    end-of-update sweep re-syncs both the old number and the new one.

    It is here because the early release runs AFTER the header's new
    ``spo_number`` has been flushed, so it re-syncs the SPO the GRN is being
    pointed AT rather than the one it is leaving. That is only safe while the
    end-of-update sweep still covers the one it left, and this is what says so.
    """
    other_spo = "SPO-2026/07-0013"
    product, warehouse = world.product(), world.warehouse()
    was_drawn = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=50, received=50
    )
    now_drawn = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=50, spo_number=other_spo
    )
    grn = world.grn(status="approved", spo_number=SPO)
    world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity=50, allocation_id=was_drawn,
    )

    _update(
        world.db, grn,
        spo_number=other_spo,
        picking_lines=[
            {
                "product_id": product, "quantity_expected": 50, "quantity_picked": 50,
                "source_warehouse_id": warehouse,
            }
        ],
    )

    assert [l.spo_allocation_id for l in world.lines_of(grn)] == [now_drawn]
    assert world.db.get(SPOAllocation, was_drawn).quantity_received == 0
    assert world.db.get(SPOAllocation, now_drawn).quantity_received == 50


def test_the_approval_writer_stamps_its_own_grns_company(world):
    """AC-FM-27, at the approval writer rather than at forward matching.

    The pool half was fixed first: ``_create_grn_lines_with_spo_fifo`` confines its
    allocations to the GRN header's company, so under the ``X-API-Key`` NULL
    ("all companies") scope a company-B GRN can no longer draw on company A's
    capacity. The WRITE half was still leaving ``company_id`` to the insert hook,
    which under that same NULL scope stamps the INCUMBENT company - so every row
    the approval created landed in Sorento: invisible on the company-B GRN it
    belongs to, while still consuming that GRN's allocation. Half-fixed is worse
    than consistently wrong, because it reads as fixed.

    Both kinds of row the writer produces are covered here: the chunk that drew on
    the allocation, and the remainder no allocation covered.
    """
    other = world.company()
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=60, company_id=other
    )
    grn = world.grn(status="draft", spo_number=SPO, company_id=other)
    world.db.commit()
    set_company_scope(world.db, None)

    _update(
        world.db, grn,
        picking_status="approved",
        picking_lines=[
            {
                "product_id": product, "quantity_expected": 100, "quantity_picked": 100,
                "source_warehouse_id": warehouse,
            }
        ],
    )

    rows = world.lines_of(grn)
    assert sorted(l.quantity_picked for l in rows) == [40, 60]
    assert [l.spo_allocation_id for l in rows if l.spo_allocation_id] == [allocation]
    assert {str(l.company_id) for l in rows} == {other}


def test_two_payload_lines_of_one_product_share_one_pool(world):
    """AC-FM-29. The pool is built ONCE per product and drawn down across the
    payload's lines, and that is load-bearing rather than an optimisation.

    A pool rebuilt per line would hand the same capacity to both, because this
    writer excludes the GRN it is rewriting from its own consumption - so the rows
    the first line just wrote would not count as taken. Every other test here uses
    one line per product, which is exactly the shape that cannot see it.
    """
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)
    grn = world.grn(status="draft", spo_number=SPO)

    _update(
        world.db, grn,
        picking_status="approved",
        picking_lines=[
            {
                "product_id": product, "quantity_expected": 70, "quantity_picked": 70,
                "source_warehouse_id": warehouse,
            },
            {
                "product_id": product, "quantity_expected": 60, "quantity_picked": 60,
                "source_warehouse_id": warehouse,
            },
        ],
    )

    rows = world.lines_of(grn)
    # The second line draws only the 30 the first one left, and its uncovered
    # remainder is written unlinked rather than silently dropped.
    assert sorted(l.quantity_picked for l in rows if l.spo_allocation_id == allocation) == [30, 70]
    assert sorted(l.quantity_picked for l in rows if l.spo_allocation_id is None) == [30]
    # The whole receipt is still 130 units; only 100 of it drew on the allocation.
    assert sum(l.quantity_picked for l in rows) == 130
    assert world.drawn_total(allocation) == 100


def test_a_payload_that_states_its_own_spo_keeps_it_exactly(world):
    """AC-FM-9b. The FE round-trips the value it read, and what the client sent
    beats a value re-derived from the header."""
    product, warehouse = world.product(), world.warehouse()
    grn = world.grn(status="draft", spo_number=SPO)
    world.line(header_id=grn, product_id=product, warehouse_id=warehouse, quantity=10)

    _update(
        world.db, grn,
        picking_lines=[
            {
                "product_id": product, "quantity_expected": 10, "quantity_picked": 10,
                "source_warehouse_id": warehouse, "spo_number_raw": "SPO-2026/07-0099",
            }
        ],
    )

    assert [l.spo_number_raw for l in world.lines_of(grn)] == ["SPO-2026/07-0099"]


def test_an_unapproved_edit_also_states_the_headers_spo(world):
    """The same payload branch, before approval - the state an imported GRN is
    normally in, and the one from which it must stay forward-matchable."""
    product, warehouse = world.product(), world.warehouse()
    grn = world.grn(status="draft", spo_number=SPO)

    _update(
        world.db, grn,
        picking_lines=[
            {
                "product_id": product, "quantity_expected": 10, "quantity_picked": 10,
                "source_warehouse_id": warehouse,
            }
        ],
    )

    assert [l.spo_number_raw for l in world.lines_of(grn)] == [SPO]


def test_approving_a_grn_rebuilds_its_lines_without_losing_the_stated_text(world):
    """AC-FM-9b, rebuild branch: no payload, the GRN just became approved, and the
    rows are deleted and recreated from themselves."""
    product, warehouse = world.product(), world.warehouse()
    world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)
    grn = world.grn(status="draft", spo_number=SPO)
    world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity=10, spo_number_raw="SPO-2026/07-0077",
    )

    _update(world.db, grn, picking_status="approved")

    assert [l.spo_number_raw for l in world.lines_of(grn)] == ["SPO-2026/07-0077"]


def test_a_multi_spo_header_states_nothing_on_the_recreated_lines(world):
    """AC-FM-25 held at the line writers too: a joined header value names no single
    SPO, so it is never what a line claims."""
    product, warehouse = world.product(), world.warehouse()
    grn = world.grn(status="draft", spo_number="SPO-2026/07-0012, SPO-2026/07-0013")

    _update(
        world.db, grn,
        picking_status="approved",
        picking_lines=[
            {
                "product_id": product, "quantity_expected": 10, "quantity_picked": 10,
                "source_warehouse_id": warehouse,
            }
        ],
    )

    assert [l.spo_number_raw for l in world.lines_of(grn)] == [None]


def test_de_approving_a_grn_leaves_the_stated_text_behind(world):
    """`_unlink_grn_from_spo` clears the link. The claim survives, so the lines read
    as stated-but-unmatched and become forward-match candidates again - the same
    answer a fresh import of that GRN would give."""
    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)
    grn = world.grn(status="approved", spo_number=SPO)
    world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity=10, allocation_id=allocation,
    )

    _update(world.db, grn, picking_status="draft")

    lines = world.lines_of(grn)
    assert [l.spo_allocation_id for l in lines] == [None]
    assert [l.spo_number_raw for l in lines] == [SPO]


def _create_grn(world: World, *, spo_number: Optional[str], lines: list[dict]):
    from app.schemas.procurement import PickingHeaderCreate
    from app.services.procurement_service import PickingHeaderService

    return PickingHeaderService(world.db).create_grn(
        PickingHeaderCreate(
            picking_number=unique_code("GR")[:50],
            picking_type="goods_received",
            picking_date=date(2026, 7, 1),
            spo_number=spo_number,
            picking_lines=lines,
        )
    )


def test_a_grn_created_in_the_ui_states_its_headers_spo(world):
    """AC-FM-9c for the CREATE path. ``create_grn`` built its lines straight from
    the payload, so a draft GRN that arrived through the UI or the external API
    before its SPO stated nothing and was not forward-matchable until somebody
    approved it - which is the wrong way round: the GRN arriving first is exactly
    the case this feature exists for."""
    product, warehouse = world.product(), world.warehouse()

    grn = _create_grn(
        world, spo_number=SPO,
        lines=[
            {
                "product_id": product, "quantity_expected": 10, "quantity_picked": 10,
                "source_warehouse_id": warehouse,
            }
        ],
    )

    assert [l.spo_number_raw for l in world.lines_of(str(grn.id))] == [SPO]


def test_a_grn_created_with_a_multi_spo_header_states_nothing(world):
    """AC-FM-8 / AC-FM-25 on the same path: a joined header value names no single
    allocation, so it is never what a line claims."""
    product, warehouse = world.product(), world.warehouse()

    grn = _create_grn(
        world, spo_number="SPO-2026/07-0012, SPO-2026/07-0013",
        lines=[
            {
                "product_id": product, "quantity_expected": 10, "quantity_picked": 10,
                "source_warehouse_id": warehouse,
            }
        ],
    )

    assert [l.spo_number_raw for l in world.lines_of(str(grn.id))] == [None]


def test_a_created_line_that_states_its_own_spo_keeps_it(world):
    """What the client sent wins over a value re-derived from the header."""
    product, warehouse = world.product(), world.warehouse()

    grn = _create_grn(
        world, spo_number=SPO,
        lines=[
            {
                "product_id": product, "quantity_expected": 10, "quantity_picked": 10,
                "source_warehouse_id": warehouse, "spo_number_raw": "SPO-2026/07-0099",
            }
        ],
    )

    assert [l.spo_number_raw for l in world.lines_of(str(grn.id))] == ["SPO-2026/07-0099"]


def test_a_grn_created_before_its_spo_is_forward_matchable(world):
    """The whole point of stating it on create: the allocation turns up afterwards
    and the line links itself, with nobody re-uploading or re-approving anything."""
    from app.services.procurement_service import SPOAllocationService

    product, warehouse = world.product(), world.warehouse()
    grn = _create_grn(
        world, spo_number=SPO,
        lines=[
            {
                "product_id": product, "quantity_expected": 10, "quantity_picked": 10,
                "source_warehouse_id": warehouse,
            }
        ],
    )

    SPOAllocationService(world.db).create_allocation(
        _allocation_payload(world, product_id=product, warehouse_id=warehouse, quantity=100),
        created_by=None,
    )

    assert all(l.spo_allocation_id is not None for l in world.lines_of(str(grn.id)))


def test_forward_matching_is_best_effort(world, monkeypatch):
    """AC-FM-21. A post-commit side effect must not turn a successful allocation
    write into a 500 - the caller would retry, take the idempotent path, and never
    backfill the missed effect."""
    import app.services.grn_spo_matching as matching

    def _boom(*_args, **_kwargs):
        raise RuntimeError("forward matching exploded")

    monkeypatch.setattr(matching, "forward_match_grn_lines_for_spo", _boom)

    assert forward_match_grn_lines_for_spo_best_effort(world.db, SPO) is None


# ---------------------------------------------------------------------------
# Where it fires: every path that brings an spo_allocations row into existence.
# ---------------------------------------------------------------------------

def _allocation_payload(world: World, *, product_id: str, warehouse_id: str, quantity: int):
    from app.schemas.procurement import SPOAllocationCreate

    return SPOAllocationCreate(
        spo_number=SPO,
        inbound_shipment_id=world.shipment,
        warehouse_id=warehouse_id,
        product_id=product_id,
        allocated_quantity=quantity,
    )


def _waiting_line(world: World, *, quantity: int = 40):
    product, warehouse = world.product(), world.warehouse()
    grn = world.grn()
    line = world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse, quantity=quantity
    )
    world.db.commit()
    return product, warehouse, line


def test_the_ui_and_import_create_path_links_the_waiting_line(world):
    """AC-FM-22, first two paths. The SPO Excel import reaches this through
    `upsert_allocation`'s "created" branch, so one hook serves both."""
    from app.services.procurement_service import SPOAllocationService

    product, warehouse, line = _waiting_line(world)

    allocation = SPOAllocationService(world.db).create_allocation(
        _allocation_payload(world, product_id=product, warehouse_id=warehouse, quantity=100),
        created_by=None,
    )

    assert world.db.get(PickingLine, line).spo_allocation_id == str(allocation.id)


def test_the_import_upsert_created_branch_links_the_waiting_line(world):
    """AC-FM-22, the SPO Excel import as the import itself calls it."""
    from app.services.procurement_service import SPOAllocationService

    product, warehouse, line = _waiting_line(world)

    action, allocation = SPOAllocationService(world.db).upsert_allocation(
        _allocation_payload(world, product_id=product, warehouse_id=warehouse, quantity=100),
        created_by=None,
    )

    assert action == "created"
    assert world.db.get(PickingLine, line).spo_allocation_id == str(allocation.id)


def test_raising_an_allocations_quantity_releases_the_waiting_line(world):
    """AC-FM-23. A corrected SPO file re-imports the allocation larger; the freed
    capacity goes to the line that was waiting for it."""
    from app.services.procurement_service import SPOAllocationService

    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=50)
    drawn = world.grn()
    world.line(
        header_id=drawn, product_id=product, warehouse_id=warehouse,
        quantity=50, allocation_id=allocation,
    )
    waiting_grn = world.grn()
    waiting = world.line(
        header_id=waiting_grn, product_id=product, warehouse_id=warehouse, quantity=40
    )
    world.db.commit()

    action, _row = SPOAllocationService(world.db).upsert_allocation(
        _allocation_payload(world, product_id=product, warehouse_id=warehouse, quantity=90),
        created_by=None,
    )

    assert action == "updated"
    assert world.db.get(PickingLine, waiting).spo_allocation_id == allocation


def test_the_scm_suggestion_writer_matches_once_for_the_whole_decision(world):
    """AC-FM-19 at the fourth allocation-creation path.

    Approving an SCM allocation suggestion writes one allocation PER SPLIT - which
    is several allocations, in several warehouses, under one SPO number, in one
    action. That is the exact shape AC-FM-19 names, and firing the per-row hook
    inside the loop places a waiting GRN line against whichever allocation happened
    to be written first rather than the one covering its warehouse: the line ends
    up split 30/70 across both instead of drawing its whole 100 from the allocation
    that names its own warehouse. The SPO Excel import already suppresses the hook
    and sweeps once at the end; this path did not.
    """
    from app.models.procurement import InboundShipment, InboundShipmentLine
    from app.services.scm import allocation_suggestion_service

    product = world.product()
    w1, w2 = world.warehouse(), world.warehouse()
    shipment_line = str(uuid.uuid4())
    world.db.add(
        InboundShipmentLine(
            id=shipment_line, shipment_id=world.shipment, product_id=product,
            quantity_shipped=130, uom_id=world.uom,
        )
    )
    # This writer names the SPO after the shipment.
    spo_number = world.db.get(InboundShipment, world.shipment).shipment_number
    grn = world.grn()
    world.line(
        header_id=grn, product_id=product, warehouse_id=w2, quantity=100,
        spo_number_raw=spo_number,
    )
    world.db.commit()

    allocation_suggestion_service.approve(
        world.db,
        world.shipment,
        [
            {
                "shipment_line_id": shipment_line,
                "splits": [
                    {"warehouse_id": w1, "qty": 30},
                    {"warehouse_id": w2, "qty": 100},
                ],
            }
        ],
    )

    covers_the_lines_warehouse = (
        world.db.query(SPOAllocation)
        .filter(
            SPOAllocation.spo_number == spo_number,
            SPOAllocation.warehouse_id == w2,
        )
        .one()
    )
    assert [
        (l.spo_allocation_id, l.quantity_picked) for l in world.lines_of(grn)
    ] == [(str(covers_the_lines_warehouse.id), 100)]


def test_an_unchanged_re_import_writes_nothing_and_so_matches_nothing(world):
    """The "unchanged" branch returns before any write, so there is no moment of
    allocation to react to."""
    from app.services.procurement_service import SPOAllocationService

    product, warehouse = world.product(), world.warehouse()
    world.allocation(product_id=product, warehouse_id=warehouse, quantity=50)
    grn = world.grn()
    line = world.line(header_id=grn, product_id=product, warehouse_id=warehouse, quantity=10)
    world.db.commit()
    calls: list = []
    import app.services.grn_spo_matching as matching

    original = matching.forward_match_grn_lines_for_spo_best_effort

    # `**kwargs`, not a bare `(db, spo_number)`: every caller passes `company_id=`,
    # so a narrower spy would raise TypeError and the test would "fail" on its own
    # stand-in rather than on the `calls == []` assertion it exists to make.
    def _spy(db, spo_number, **kwargs):
        calls.append(spo_number)
        return original(db, spo_number, **kwargs)

    matching.forward_match_grn_lines_for_spo_best_effort = _spy
    try:
        action, _row = SPOAllocationService(world.db).upsert_allocation(
            _allocation_payload(world, product_id=product, warehouse_id=warehouse, quantity=50),
            created_by=None,
        )
    finally:
        matching.forward_match_grn_lines_for_spo_best_effort = original

    assert action == "unchanged"
    assert calls == []
    assert world.db.get(PickingLine, line).spo_allocation_id is None


def test_the_external_bulk_endpoint_links_the_waiting_line(world):
    """AC-FM-22, third path: n8n / AutoCount posting allocations in bulk. It builds
    the rows itself and commits them, so it needs its own hook."""
    from app.api.v1.external.spo_allocations import create_spo_allocations
    from app.models.product import Product
    from app.schemas.external.procurement import SPOAllocationItem, SPOAllocationRequest

    product, warehouse, line = _waiting_line(world)
    product_code = world.db.get(Product, product).product_code

    create_spo_allocations(
        payload=SPOAllocationRequest(
            spo_allocations=[
                SPOAllocationItem(
                    spo_number=SPO,
                    inbound_shipment_id=world.shipment,
                    warehouse_id=warehouse,
                    allocated_quantity=100,
                    product_code=product_code,
                )
            ]
        ),
        current_user={"id": None},
        db=world.db,
    )

    assert world.db.get(PickingLine, line).spo_allocation_id is not None


def test_a_grn_posted_by_the_external_api_states_its_spo(world):
    """AC-FM-9c on the n8n / AutoCount route.

    That route builds its `PickingLineCreate` by hand and never set
    `spo_number_raw`, so every GRN line it wrote stated NOTHING - and `create_grn`
    deliberately drops `spo_allocation_id` (a GRN links on approval, not on
    create), so those lines were left neither linked nor stated: unmatchable
    forever, on the one path where the sheet's own SPO text is right there in the
    payload.
    """
    from app.api.v1.external.grn import create_grn as create_grn_external
    from app.models.product import Product
    from app.schemas.external.procurement import GRNHeader, GRNLine, GRNRequest

    product, warehouse = world.product(), world.warehouse()
    # The route rejects a line naming an allocation that does not exist, so the
    # allocation is present here; what the line carries out is the stated text.
    world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)
    product_code = world.db.get(Product, product).product_code
    picking_number = unique_code("GR")[:50]
    world.db.commit()

    create_grn_external(
        payload=GRNRequest(
            goods_receive_notes=GRNHeader(
                picking_number=picking_number, picking_date="2026-07-01"
            ),
            grn_lines=[
                GRNLine(
                    spo_allocation=SPO, warehouse_id=warehouse,
                    product_code=product_code, quantity=40,
                )
            ],
        ),
        current_user={"id": None},
        db=world.db,
    )

    header = (
        world.db.query(PickingHeader)
        .filter(PickingHeader.picking_number == picking_number)
        .one()
    )
    lines = world.lines_of(str(header.id))
    assert [l.spo_number_raw for l in lines] == [SPO]
    # Still unlinked - this route creates, it does not approve - so the stated text
    # is the only thing that makes the line forward-matchable at all.
    assert [l.spo_allocation_id for l in lines] == [None]


def test_an_allocation_write_survives_a_failing_forward_match(world, monkeypatch):
    """AC-FM-21 at the call site: the caller gets its allocation and a success
    return, and the failure is a warning rather than a 500 for an operation that
    actually succeeded."""
    import app.services.grn_spo_matching as matching
    from app.services.procurement_service import SPOAllocationService

    product, warehouse, line = _waiting_line(world)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("forward matching exploded")

    monkeypatch.setattr(matching, "forward_match_grn_lines_for_spo", _boom)

    allocation = SPOAllocationService(world.db).create_allocation(
        _allocation_payload(world, product_id=product, warehouse_id=warehouse, quantity=100),
        created_by=None,
    )

    assert allocation is not None
    assert (
        world.db.query(SPOAllocation).filter(SPOAllocation.spo_number == SPO).count() == 1
    ), "the allocation write must stand even when the side effect fails"
    assert world.db.get(PickingLine, line).spo_allocation_id is None


# ---------------------------------------------------------------------------
# The point of the feature: the upload order leaves no trace. These run the REAL
# GRN lines import, not a stand-in for it.
# ---------------------------------------------------------------------------

GRN_SHEET_HEADERS = ["Doc No", "Date", "Item Code", "Location", "Qty", "Our PO No."]


def _grn_lines_workbook(rows: list[list]) -> bytes:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(GRN_SHEET_HEADERS)
    for row in rows:
        sheet.append(row)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


IMPORT_USER_ID = "13004397-4f58-51da-b134-833583c4a584"


def _seed_import_job(
    db, *, job_type: str, filename: str, company_id: Optional[str] = DEFAULT_COMPANY_ID
) -> str:
    """``company_id=None`` is the LEGACY branch: a job with no company snapshot runs
    ``set_company_scope(db, None)`` - "all companies", where the scope layer adds no
    predicate and the insert hook stamps the INCUMBENT company instead."""
    from app.models.job import ImportJob

    job_db_id = uuid.uuid4()
    db.add(
        ImportJob(
            id=job_db_id, job_id=unique_code("job"), job_type=job_type,
            user_id=IMPORT_USER_ID,
            company_id=uuid.UUID(company_id) if company_id else None,
            filename=filename,
        )
    )
    db.commit()
    return str(job_db_id)


def _sessions_on_this_connection(db):
    """The task opens its own session (and ImportOutcome another), so both have to
    land on this test's connection or they would write to the real database."""
    bind = db.get_bind()

    def _session():
        session = Session(bind=bind, join_transaction_mode="create_savepoint")
        session.execute(text("SELECT 1"))
        return session

    return _session


def _import_grn_lines(
    db, workbook_bytes: bytes, *, job_company_id: Optional[str] = DEFAULT_COMPANY_ID
) -> None:
    """Drive the real task against this test's connection."""
    from app.tasks.import_tasks import process_grn_lines_import

    job_db_id = _seed_import_job(
        db, job_type="grn_lines_import", filename="grn.xlsx", company_id=job_company_id
    )

    with patch("app.tasks.import_tasks.SessionLocal", _sessions_on_this_connection(db)):
        process_grn_lines_import(job_db_id, workbook_bytes, "grn.xlsx", IMPORT_USER_ID)


def _spo_workbook(world: World, rows: list[tuple[str, str, int]]) -> bytes:
    """The SPO allocation sheet as AutoCount emits it: the container number is the
    text after the first space in Loading Date, and the SPO number is the FILENAME."""
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["Item Code", "Location", "Qty", "Loading Date"])
    for product_code, warehouse_code, quantity in rows:
        sheet.append([product_code, warehouse_code, quantity, f"2026-06-01 {world.container}"])
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _import_spo_allocations(db, world: World, rows: list[tuple[str, str, int]]) -> None:
    """Drive the real SPO Excel import - the path that writes one allocation per
    (product, warehouse) group, several per file."""
    from app.tasks.import_tasks import process_spo_import

    filename = f"{SPO}.xlsx"
    job_db_id = _seed_import_job(db, job_type="spo_import", filename=filename)

    with patch("app.tasks.import_tasks.SessionLocal", _sessions_on_this_connection(db)):
        process_spo_import(job_db_id, _spo_workbook(world, rows), filename, IMPORT_USER_ID)


def _placed_rows(db, header_id: str, names: dict[str, str]) -> list[tuple]:
    """The multiset AC-FM-19 compares: (product, warehouse, allocation, qty, stated).

    Every uuid is resolved through ``names`` to the role it plays, because the two
    runs are two separately seeded databases - comparing raw ids would compare the
    seeding, not the outcome. The allocation is named by the warehouse it belongs
    to, so "linked to the allocation that covers this warehouse" and "linked to
    some other allocation" are not the same answer.
    """
    rows = [
        (
            names[str(line.product_id)],
            names[str(line.source_warehouse_id)],
            names[str(line.spo_allocation_id)] if line.spo_allocation_id else None,
            line.quantity_expected,
            line.spo_number_raw,
        )
        for line in db.query(PickingLine)
        .filter(PickingLine.picking_header_id == header_id)
        .all()
    ]
    return sorted(rows, key=lambda row: tuple(str(value) for value in row))


def _order_independence_run(*, allocation_first: bool) -> list[tuple]:
    """The same GRN sheet and the same SPO file, in one order or the other.

    TWO allocations in TWO warehouses, because one allocation cannot show the
    failure: the SPO file is imported one (product, warehouse) group at a time, so
    a hook that fires per allocation ROW runs while the rest of the file does not
    exist yet, and places the waiting line against whichever allocation happened to
    be written first rather than the one that covers its warehouse.
    """
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        world = World(db)
        product = world.product()
        w1, w2 = world.warehouse(), world.warehouse()
        product_code = db.get(Product, product).product_code
        w1_code = db.get(Warehouse, w1).warehouse_code
        w2_code = db.get(Warehouse, w2).warehouse_code
        grn_number = unique_code("GR")[:50]
        header = PickingHeader(
            id=str(uuid.uuid4()), picking_number=grn_number, picking_type="goods_received",
            picking_date=date(2026, 7, 1), picking_status="approved",
            inspection_status="pending", spo_number=SPO,
        )
        db.add(header)
        db.commit()

        def _import_spo():
            _import_spo_allocations(
                db, world, [(product_code, w1_code, 30), (product_code, w2_code, 100)]
            )

        def _import_grn():
            _import_grn_lines(
                db,
                _grn_lines_workbook(
                    [[grn_number, "2026-07-01", product_code, w2_code, 100, SPO]]
                ),
            )

        if allocation_first:
            _import_spo()
            _import_grn()
        else:
            _import_grn()
            _import_spo()

        names = {product: "product", w1: "W1", w2: "W2"}
        for allocation in db.query(SPOAllocation).filter(SPOAllocation.spo_number == SPO).all():
            names[str(allocation.id)] = f"the {names[str(allocation.warehouse_id)]} allocation"
        return _placed_rows(db, header.id, names)


def test_the_upload_order_leaves_no_trace():
    """AC-FM-19. Two databases, the same two files, opposite orders - and the same
    picking lines out. This is the whole feature in one assertion."""
    grn_then_allocation = _order_independence_run(allocation_first=False)
    allocation_then_grn = _order_independence_run(allocation_first=True)

    assert grn_then_allocation == allocation_then_grn
    # And it is not the same because both did nothing: the 100 received into W2 is
    # ONE row drawn from the allocation that covers W2, not a 30 from W1's plus a
    # 70 from W2's.
    assert grn_then_allocation == [("product", "W2", "the W2 allocation", 100, SPO)]


def test_the_grn_lines_import_stamps_the_grns_own_company(world):
    """AC-FM-27's "both halves, or neither" - at the import writer, which got the
    read half without the write half.

    The import confines its POOL to the header's company, but left the line's own
    ``company_id`` to the insert hook. Under the legacy no-company-snapshot job the
    task runs ``set_company_scope(db, None)`` ("all companies"), where that hook
    stamps the INCUMBENT company - so a company-B GRN drew on company-B capacity
    and wrote the rows into Sorento. Invisible on the GRN they belong to, and,
    because the consumption query filters on ``company_id`` too, never counted as
    consumption either: the next import of the same file over-draws.
    """
    from app.models.product import Product

    other = world.company()
    product, warehouse = world.product(), world.warehouse()
    product_code = world.db.get(Product, product).product_code
    warehouse_code = world.db.get(Warehouse, warehouse).warehouse_code
    header_id = str(uuid.uuid4())
    grn_number = unique_code("GR")[:50]
    world.db.add(
        PickingHeader(
            id=header_id, picking_number=grn_number, picking_type="goods_received",
            picking_date=date(2026, 7, 1), picking_status="approved",
            inspection_status="pending", spo_number=SPO, company_id=other,
        )
    )
    allocation = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=100, company_id=other
    )
    world.db.commit()
    set_company_scope(world.db, None)

    _import_grn_lines(
        world.db,
        _grn_lines_workbook(
            [[grn_number, "2026-07-01", product_code, warehouse_code, 40, SPO]]
        ),
        job_company_id=None,
    )

    lines = world.lines_of(header_id)
    assert [(l.spo_allocation_id, l.quantity_picked) for l in lines] == [(allocation, 40)]
    assert {str(l.company_id) for l in lines} == {other}


def test_a_re_import_after_forward_matching_is_a_no_op():
    """AC-FM-20. The office re-uploads the same export - a normal thing to do - and
    the rows forward matching produced are updated in place, not duplicated."""
    from app.services.procurement_service import SPOAllocationService

    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        world = World(db)
        product, warehouse = world.product(), world.warehouse()
        product_code = db.get(Product, product).product_code
        warehouse_code = db.get(Warehouse, warehouse).warehouse_code
        grn_number = unique_code("GR")[:50]
        header = PickingHeader(
            id=str(uuid.uuid4()), picking_number=grn_number, picking_type="goods_received",
            picking_date=date(2026, 7, 1), picking_status="approved",
            inspection_status="pending", spo_number=SPO,
        )
        db.add(header)
        db.commit()
        sheet = _grn_lines_workbook(
            [[grn_number, "2026-07-01", product_code, warehouse_code, 100, SPO]]
        )

        _import_grn_lines(db, sheet)
        allocation = SPOAllocationService(db).create_allocation(
            _allocation_payload(world, product_id=product, warehouse_id=warehouse, quantity=60),
            created_by=None,
        )
        names = {
            product: "product",
            warehouse: "warehouse",
            str(allocation.id): "the allocation",
        }
        after_forward_match = _placed_rows(db, header.id, names)

        _import_grn_lines(db, sheet)

        assert _placed_rows(db, header.id, names) == after_forward_match
        assert len(after_forward_match) == 2, "one linked row plus the uncovered remainder"


def _split_receipt_run(*, through_import: bool) -> list[tuple]:
    """One receipt of 100 into W1, against a 30 in W1 and a 100 in W2 - the case
    that forces a SPLIT, and the only case where the two-pass rule and the row
    shape are both visible. Written once by the GRN lines import, once by
    approving the GRN through ``update_grn``.
    """
    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        world = World(db)
        product = world.product()
        w1, w2 = world.warehouse(), world.warehouse()
        product_code = db.get(Product, product).product_code
        w1_code = db.get(Warehouse, w1).warehouse_code
        a1 = world.allocation(product_id=product, warehouse_id=w1, quantity=30)
        a2 = world.allocation(product_id=product, warehouse_id=w2, quantity=100)
        grn_number = unique_code("GR")[:50]
        header = PickingHeader(
            id=str(uuid.uuid4()), picking_number=grn_number, picking_type="goods_received",
            picking_date=date(2026, 7, 1),
            picking_status="approved" if through_import else "draft",
            inspection_status="pending", spo_number=SPO,
        )
        db.add(header)
        db.commit()

        if through_import:
            _import_grn_lines(
                db,
                _grn_lines_workbook(
                    [[grn_number, "2026-07-01", product_code, w1_code, 100, SPO]]
                ),
            )
        else:
            world.line(
                header_id=str(header.id), product_id=product, warehouse_id=w1, quantity=100,
            )
            db.commit()
            _update(db, str(header.id), picking_status="approved")

        names = {
            product: "product", w1: "W1", w2: "W2",
            a1: "the W1 allocation", a2: "the W2 allocation",
        }
        return _placed_rows(db, header.id, names)


def test_the_approval_rebuild_writes_the_rows_an_import_would_have():
    """The swap onto the shared matcher must not change what the approval path
    writes. Same receipt, same allocations, two writers - and the same rows out,
    down to which allocation each chunk drew from.
    """
    through_import = _split_receipt_run(through_import=True)
    through_approval = _split_receipt_run(through_import=False)

    assert through_import == through_approval
    # And it is not the same because both did nothing: the 100 into W1 is drawn
    # 30 from W1's allocation first (the line's own warehouse beats age) and the
    # remaining 70 from W2's.
    assert through_import == sorted(
        [
            ("product", "W1", "the W1 allocation", 30, SPO),
            ("product", "W1", "the W2 allocation", 70, SPO),
        ],
        key=lambda row: tuple(str(value) for value in row),
    )


def test_both_directions_run_the_same_draw(monkeypatch):
    """AC-FM-26. Not "they behave the same" - the import and forward matching call
    the same two functions, so a second copy of the two-pass rule cannot exist to
    drift from the first."""
    import app.services.grn_spo_matching as matching
    import app.tasks.import_tasks as import_tasks
    from app.services.procurement_service import SPOAllocationService

    assert import_tasks.draw_fifo is matching.draw_fifo
    assert import_tasks.build_allocation_pool is matching.build_allocation_pool

    seen: list[str] = []
    real_draw = matching.draw_fifo

    def _spy(pool, *, warehouse_id, quantity):
        seen.append(f"{warehouse_id}:{quantity}")
        return real_draw(pool, warehouse_id=warehouse_id, quantity=quantity)

    monkeypatch.setattr(matching, "draw_fifo", _spy)
    monkeypatch.setattr(import_tasks, "draw_fifo", _spy)

    with blank_session() as db:
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        world = World(db)
        product, warehouse = world.product(), world.warehouse()
        product_code = db.get(Product, product).product_code
        warehouse_code = db.get(Warehouse, warehouse).warehouse_code
        grn_number = unique_code("GR")[:50]
        db.add(
            PickingHeader(
                id=str(uuid.uuid4()), picking_number=grn_number, picking_type="goods_received",
                picking_date=date(2026, 7, 1), picking_status="approved",
                inspection_status="pending", spo_number=SPO,
            )
        )
        db.commit()

        _import_grn_lines(
            db,
            _grn_lines_workbook(
                [[grn_number, "2026-07-01", product_code, warehouse_code, 100, SPO]]
            ),
        )
        drew_on_import = len(seen)

        SPOAllocationService(db).create_allocation(
            _allocation_payload(world, product_id=product, warehouse_id=warehouse, quantity=60),
            created_by=None,
        )

    assert drew_on_import >= 1, "the import did not draw through the shared matcher"
    assert len(seen) > drew_on_import, "forward matching did not draw through it either"


# ---------------------------------------------------------------------------
# AC-FM-28 at the LAST reader. `get_received_quantities_by_product` persists
# `inbound_shipment_lines.quantity_received` and `line_status`, and it was the one
# reader still summing `quantity_expected` - so the shipment and the allocation
# reported different numbers for the same goods the moment picked != expected.
# ---------------------------------------------------------------------------

def _shipment_line(world: World, *, product_id: str, quantity_shipped: int) -> str:
    from app.models.procurement import InboundShipmentLine

    lid = str(uuid.uuid4())
    world.db.add(
        InboundShipmentLine(
            id=lid, shipment_id=world.shipment, product_id=product_id,
            quantity_shipped=quantity_shipped, uom_id=world.uom,
        )
    )
    world.db.flush()
    return lid


def test_the_shipment_and_the_allocation_agree_on_what_was_received(world):
    """A short receipt - 60 of the 100 the sheet expected - forward matched onto
    its allocation. The allocation reports 60, so the shipment line has to as
    well; 100 there says the container is fully received when 40 of it never
    arrived."""
    from app.services.procurement_service import InboundShipmentService

    product, warehouse = world.product(), world.warehouse()
    shipment_line = _shipment_line(world, product_id=product, quantity_shipped=100)
    grn = world.grn(status="approved", spo_number=SPO)
    line = world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity_expected=100, quantity_picked=60,
    )
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=100)

    forward_match_grn_lines_for_spo(world.db, SPO)

    assert world.db.get(PickingLine, line).spo_allocation_id == allocation
    assert world.db.get(SPOAllocation, allocation).quantity_received == 60
    service = InboundShipmentService(world.db)
    assert service.get_received_quantities_by_product(world.shipment) == {product: 60}
    from app.models.procurement import InboundShipmentLine

    assert world.db.get(InboundShipmentLine, shipment_line).quantity_received == 60


def test_an_unmatched_line_also_reports_what_it_picked(world):
    """The orphan branch - a line no allocation covers, counted through its GRN
    header's SPO number - measures the same column as the linked one. Two readers
    of the same shipment cannot use two different columns."""
    from app.services.procurement_service import InboundShipmentService

    product, warehouse = world.product(), world.warehouse()
    _shipment_line(world, product_id=product, quantity_shipped=100)
    # An allocation for a DIFFERENT warehouse, so it names the SPO on this shipment
    # without the line ever linking to it.
    world.allocation(product_id=product, warehouse_id=world.warehouse(), quantity=0)
    grn = world.grn(status="approved", spo_number=SPO)
    world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity_expected=100, quantity_picked=60,
    )
    world.db.commit()

    received = InboundShipmentService(world.db).get_received_quantities_by_product(
        world.shipment
    )

    assert received == {product: 60}


# ---------------------------------------------------------------------------
# AC-FM-24 - the stated text reaches the frontend, on both surfaces.
# ---------------------------------------------------------------------------

def test_the_grn_detail_response_carries_the_stated_text(world):
    from app.schemas.procurement import PickingHeaderResponse
    from app.services.procurement_service import PickingHeaderService

    product, warehouse = world.product(), world.warehouse()
    grn = world.grn(status="draft", spo_number=SPO)
    world.line(header_id=grn, product_id=product, warehouse_id=warehouse, quantity=10)
    world.db.commit()

    response = PickingHeaderResponse.model_validate(PickingHeaderService(world.db).get_grn(grn))

    assert [line.spo_number_raw for line in response.picking_lines] == [SPO]


def test_the_picking_lines_listing_carries_it_and_searches_on_it(world):
    """The operator's way of finding stated-but-unmatched lines: type the SPO
    number the sheet stated, even though no allocation carries it yet."""
    from app.services.procurement_service import PickingHeaderService

    product, warehouse = world.product(), world.warehouse()
    grn = world.grn(status="draft", spo_number=SPO)
    world.line(header_id=grn, product_id=product, warehouse_id=warehouse, quantity=10)
    other = world.grn(status="draft")
    world.line(
        header_id=other, product_id=product, warehouse_id=warehouse,
        quantity=10, spo_number_raw="SPO-2026/07-0999",
    )
    world.db.commit()

    result = PickingHeaderService(world.db).list_picking_lines(query=SPO)

    assert [line.spo_number_raw for line in result["data"]] == [SPO]


# ---------------------------------------------------------------------------
# AC-X25 (D28 release list, spo-xlsx-supersede reviewer round) - deleting a
# GRN releases the receipt it drew, whether one at a time (`delete_grn`) or
# in bulk (`bulk_delete_grns`); a sibling allocation with no picking line at
# all keeps its stored value either way.
# ---------------------------------------------------------------------------


def test_delete_grn_drops_the_released_allocations_receipt_to_zero(world):
    """AC-X25. A GRN with one approved picking line of 5 against an
    allocation; deleting it via `delete_grn` must drop that allocation's
    `quantity_received` to 0. A sibling allocation with no picking line at
    all keeps its stored value.
    """
    from app.services.procurement_service import PickingHeaderService

    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=10)
    sibling = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=7, received=7
    )
    grn = world.grn(status="approved", spo_number=SPO)
    world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity=5, allocation_id=allocation,
    )
    world.db.commit()

    svc = PickingHeaderService(world.db)
    svc.sync_grn_received_to_spo(grn)
    world.db.expire_all()
    before = world.db.execute(
        text("SELECT quantity_received FROM spo_allocations WHERE id = :id"), {"id": allocation}
    ).scalar()
    assert before == 5, before

    svc.delete_grn(grn)

    world.db.expire_all()
    after = world.db.execute(
        text("SELECT quantity_received FROM spo_allocations WHERE id = :id"), {"id": allocation}
    ).scalar()
    sibling_after = world.db.execute(
        text("SELECT quantity_received FROM spo_allocations WHERE id = :id"), {"id": sibling}
    ).scalar()
    assert after == 0, after
    assert sibling_after == 7, sibling_after


def test_bulk_delete_grns_drops_the_released_allocations_receipt_to_zero(world):
    """AC-X25. Same property as `delete_grn`, via `bulk_delete_grns`."""
    from app.services.procurement_service import PickingHeaderService

    product, warehouse = world.product(), world.warehouse()
    allocation = world.allocation(product_id=product, warehouse_id=warehouse, quantity=10)
    sibling = world.allocation(
        product_id=product, warehouse_id=warehouse, quantity=7, received=7
    )
    grn = world.grn(status="approved", spo_number=SPO)
    world.line(
        header_id=grn, product_id=product, warehouse_id=warehouse,
        quantity=5, allocation_id=allocation,
    )
    world.db.commit()

    svc = PickingHeaderService(world.db)
    svc.sync_grn_received_to_spo(grn)
    world.db.expire_all()

    svc.bulk_delete_grns([grn])

    world.db.expire_all()
    after = world.db.execute(
        text("SELECT quantity_received FROM spo_allocations WHERE id = :id"), {"id": allocation}
    ).scalar()
    sibling_after = world.db.execute(
        text("SELECT quantity_received FROM spo_allocations WHERE id = :id"), {"id": sibling}
    ).scalar()
    assert after == 0, after
    assert sibling_after == 7, sibling_after
