"""Section 3.G - what an order inquiry has OCCUPIED on a purchase order.

`PLAN-scm-cs-planning-uat.md` section 3.G, UAC AC-G1 to AC-G5.

The captain, 25 August 2026, walking PO-2026/07-0029: "when an order inquiry occupies
quantity on a PO, the PO must show how much of its outstanding is occupied, by which OI and
SO, and at which location (PO line says DC1; the demand is at BRW-BB). It must live BESIDE
the PO line, not in it: the user re-keys the split in AutoCount and re-uploads, and an upload
overwriting our split would lose it."

`blank_session`, not `pg_session`: the assertions here are "this purchase order has exactly
these three placements" and "the list reports exactly these two orders", and the shared local
database holds the captain's real 80,000-line book. A scratch schema is the only substrate
where those are sentences a test may say out loud.

The wire shape is asserted through `PurchaseOrder.model_validate`, never off the service dict
alone: `response_model` silently DROPS a field the schema does not declare, which is exactly
how a carefully built block goes out empty.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.base import company_scope
from app.models.procurement import PurchaseOrderLine
from app.schemas.scm_orders import PurchaseOrder as PurchaseOrderSchema
from app.services.scm.purchase_order_service import PurchaseOrderService

from ._pg_fixture import blank_session

MARKER = "ZZT-OCC"

#: The `projects` schema THIS session writes to, bound once by the `world` fixture. Same
#: module-global as `test_order_inquiry_links.py` and for the same reason: a hard-coded
#: `projects.order_inquiry_rows` inside a `text()` statement resolves by NAME and reaches the
#: REAL schema, writing live data while the test believes it is isolated.
P = "projects"


def _uid() -> str:
    return str(uuid.uuid4())


def _projects(db) -> str:
    current = db.execute(text("select current_schema()")).scalar()
    return "projects" if current in (None, "public") else f'"{current}_projects"'


class _World:
    """One purchase order, one sales order, and the inquiry rows that occupy it."""

    def __init__(self, db):
        self.db = db
        self.company_id = db.execute(
            text("select id from companies where code = 'SRT'")
        ).scalar()
        self.warehouses: dict[str, str] = {}
        #: The inquiry series this world mints, so two sales orders never collide on
        #: `uq_project_order_inquiry_no`.
        self._inquiries = 0
        self.svc = PurchaseOrderService(db)
        self._build()

    def _build(self) -> None:
        db = self.db
        cat, uom = _uid(), _uid()
        db.execute(
            text(
                "INSERT INTO product_categories (id, category_code, category_name) "
                "VALUES (:i, :c, :c)"
            ),
            {"i": cat, "c": f"{MARKER}-CAT"},
        )
        db.execute(
            text("INSERT INTO units_of_measure (id, uom_code, uom_name) VALUES (:i, :c, :c)"),
            {"i": uom, "c": f"{MARKER}-UOM"},
        )
        self.product_code = f"{MARKER}-WESERP10B"
        self.product = _uid()
        db.execute(
            text(
                "INSERT INTO products (id, company_id, product_code, product_name, "
                "category_id, base_uom_id, list_price) "
                "VALUES (:i, :c, :code, :code, :cat, :uom, 0)"
            ),
            {
                "i": self.product,
                "c": self.company_id,
                "code": self.product_code,
                "cat": cat,
                "uom": uom,
            },
        )
        # BRW is a POOL because the other two point at it - the only authority the tier
        # rule reads, never the shape of the code.
        pool = _uid()
        db.execute(
            text(
                "INSERT INTO warehouses (id, company_id, warehouse_code, warehouse_name, "
                "is_active) VALUES (:i, :c, 'BRW', 'BRW', true)"
            ),
            {"i": pool, "c": self.company_id},
        )
        self.warehouses["BRW"] = pool
        for code in ("BRW-BB", "DC1"):
            wid = _uid()
            db.execute(
                text(
                    "INSERT INTO warehouses (id, company_id, warehouse_code, "
                    "warehouse_name, is_active, pool_warehouse_id) "
                    "VALUES (:i, :c, :code, :code, true, :p)"
                ),
                {"i": wid, "c": self.company_id, "code": code, "p": pool},
            )
            self.warehouses[code] = wid
        self.supplier = _uid()
        db.execute(
            text(
                "INSERT INTO suppliers (id, company_id, supplier_code, supplier_name, "
                "is_active) VALUES (:i, :c, :code, :name, true)"
            ),
            {
                "i": self.supplier,
                "c": self.company_id,
                "code": f"{MARKER}-SUP",
                "name": f"{MARKER} SUPPLIER",
            },
        )
        self.customer = _uid()
        db.execute(
            text(
                "INSERT INTO customers (id, company_id, customer_code, customer_name, "
                "is_active) VALUES (:i, :c, :code, :name, true)"
            ),
            {
                "i": self.customer,
                "c": self.company_id,
                "code": f"{MARKER}-CUS",
                "name": "YOTU BUILDER SDN BHD",
            },
        )
        self.agent = _uid()
        db.execute(
            text(
                "INSERT INTO sales_agents (id, company_id, sales_agent, person_label, "
                "is_active) VALUES (:i, :c, :code, :label, true)"
            ),
            {
                "i": self.agent,
                "c": self.company_id,
                "code": f"{MARKER}-AG",
                "label": "CONTACT N",
            },
        )
        db.flush()

    # -- documents -------------------------------------------------------------

    def purchase_order(self, number: str, lines) -> tuple[str, list[str]]:
        """One ACTIVE purchase order. `lines` is (location, ordered, received)."""
        po = _uid()
        self.db.execute(
            text(
                "INSERT INTO purchase_orders (id, company_id, po_number, supplier_id, "
                "status, issue_date, source_system) "
                "VALUES (:i, :c, :n, :s, 'active', :d, 'scm_upload')"
            ),
            {
                "i": po,
                "c": self.company_id,
                "n": number,
                "s": self.supplier,
                "d": date(2026, 7, 5),
            },
        )
        ids = []
        for location, ordered, received in lines:
            lid = _uid()
            self.db.execute(
                text(
                    "INSERT INTO purchase_order_lines (id, company_id, "
                    "purchase_order_id, product_id, warehouse_id, qty_ordered, "
                    "qty_received, line_status, expected_date) "
                    "VALUES (:i, :c, :po, :p, :w, :q, :r, 'open', :e)"
                ),
                {
                    "i": lid,
                    "c": self.company_id,
                    "po": po,
                    "p": self.product,
                    "w": self.warehouses[location],
                    "q": ordered,
                    "r": received,
                    "e": date(2026, 8, 4),
                },
            )
            ids.append(lid)
        self.db.flush()
        return po, ids

    def sales_order(self, so_number: str) -> tuple[str, str]:
        """A core sales order, its planning mirror, and the inquiry raised on it."""
        core = _uid()
        self.db.execute(
            text(
                "INSERT INTO sales_orders (id, company_id, so_number, customer_id, "
                "sales_agent_id, status, order_date, demand_class) "
                "VALUES (:i, :c, :n, :cus, :ag, 'open', :d, 'project')"
            ),
            {
                "i": core,
                "c": self.company_id,
                "n": so_number,
                "cus": self.customer,
                "ag": self.agent,
                "d": date(2025, 12, 10),
            },
        )
        pso = _uid()
        self.db.execute(
            text(
                "INSERT INTO " + P + ".sales_orders (id, company_id, provisional_ref, "
                "autocount_doc_no, so_id, status, created_at, updated_at) "
                "VALUES (:i, :c, :ref, :ref, :so, 'adopted', now(), now())"
            ),
            {"i": pso, "c": self.company_id, "ref": so_number, "so": core},
        )
        inquiry = _uid()
        self._inquiries += 1
        self.db.execute(
            text(
                "INSERT INTO " + P + ".order_inquiries (id, company_id, inquiry_no, "
                "project_sales_order_id, state, raised_at) "
                "VALUES (:i, :c, :no, :p, 'raised', now())"
            ),
            {
                "i": inquiry,
                "c": self.company_id,
                "no": f"OI-{self._inquiries:06d}",
                "p": pso,
            },
        )
        self.db.flush()
        return pso, inquiry

    def row(self, inquiry: str, qty, *, location: str) -> str:
        rid = _uid()
        self.db.execute(
            text(
                "INSERT INTO " + P + ".order_inquiry_rows (id, company_id, "
                "order_inquiry_id, item_code, qty, verb, stock_location, state, "
                "redirected_to_pool, created_at) "
                "VALUES (:i, :c, :inq, :code, :q, 'ORDER', :loc, 'placed', false, now())"
            ),
            {
                "i": rid,
                "c": self.company_id,
                "inq": inquiry,
                "code": self.product_code,
                "q": Decimal(str(qty)),
                "loc": location,
            },
        )
        self.db.flush()
        return rid

    def link(self, row_id: str, po_line_id: str, qty, document: str) -> str:
        lid = _uid()
        self.db.execute(
            text(
                "INSERT INTO " + P + ".order_inquiry_links (id, company_id, row_id, "
                "po_line_id, document, qty, auto, linked_at, created_at) "
                "VALUES (:i, :c, :r, :l, :d, :q, true, now(), now())"
            ),
            {
                "i": lid,
                "c": self.company_id,
                "r": row_id,
                "l": po_line_id,
                "d": document,
                "q": Decimal(str(qty)),
            },
        )
        self.db.flush()
        return lid


@pytest.fixture()
def world():
    global P
    with blank_session() as db:
        P = _projects(db)
        built = _World(db)
        with company_scope(db, frozenset({built.company_id})):
            yield built


@pytest.fixture()
def occupied(world):
    """PO-2026/07-0029 as the captain walked it: one DC1 line of 500, fully occupied.

    Three placements: SO416191 6 at BRW, SO416191 7 at BRW, SO324132 487 at BRW-BB. Every
    one of them wants the goods somewhere other than the DC1 the line names, which is the
    whole point of the panel - that difference IS the split instruction for AutoCount.
    """
    po, (line,) = world.purchase_order("PO-2026/07-0029", [("DC1", 500, 0)])
    _pso_a, inquiry_a = world.sales_order("SO416191")
    _pso_b, inquiry_b = world.sales_order("SO324132")
    world.link(world.row(inquiry_a, 6, location="BRW"), line, 6, "PO-2026/07-0029")
    world.link(world.row(inquiry_a, 7, location="BRW"), line, 7, "PO-2026/07-0029")
    world.link(world.row(inquiry_b, 932, location="BRW-BB"), line, 487, "PO-2026/07-0029")
    return {"po": po, "line": line}


# --------------------------------------------------------------------- the panel (AC-G1)


def test_the_detail_reports_outstanding_allocated_and_free_per_line(world, occupied):
    """AC-G1: outstanding 500, allocated 500, free 0.

    Three figures rather than one, because there are three questions. `outstanding` is what
    is still to arrive on the LINE, `allocated` is what order inquiries have claimed of it,
    and `free` is what a buyer may still promise somebody. A screen that printed only the
    first would let the same 500 be promised twice.
    """
    payload = world.svc.get_one(occupied["po"])
    (block,) = payload["allocations"]

    assert block["line_id"] == occupied["line"]
    assert block["sku"] == world.product_code
    assert block["warehouse_code"] == "DC1"
    assert block["outstanding"] == 500
    assert block["allocated"] == 500
    assert block["free"] == 0


def test_every_placement_names_the_inquiry_the_order_the_customer_and_the_agent(
    world, occupied
):
    """AC-G1: three placements, each carrying who is waiting and where they want it.

    No ids anywhere: the inquiry by its number, the sales order by its document number, the
    customer and the agent by the labels the order-inquiry worklist already prints. A UUID
    on this panel would tell the buyer nothing they could act on.
    """
    (block,) = world.svc.get_one(occupied["po"])["allocations"]
    placements = sorted(block["placements"], key=lambda p: (p["so_number"], p["qty"]))

    assert [(p["so_number"], p["qty"], p["needed_at"]) for p in placements] == [
        ("SO324132", 487, "BRW-BB"),
        ("SO416191", 6, "BRW"),
        ("SO416191", 7, "BRW"),
    ]
    assert {p["customer"] for p in placements} == {"YOTU BUILDER SDN BHD"}
    assert {p["agent"] for p in placements} == {"CONTACT N"}
    assert all(p["inquiry_no"] for p in placements)


def test_a_placement_whose_location_differs_from_the_po_line_is_marked(world, occupied):
    """AC-G1/AC-G2: the PO line says DC1 and every demand wants it elsewhere.

    Marked, never filtered out. The mark IS the split instruction the buyer re-keys in
    AutoCount, so hiding the row would remove the only reason the panel exists.
    """
    (block,) = world.svc.get_one(occupied["po"])["allocations"]
    assert all(p["location_differs"] for p in block["placements"])


def test_a_placement_at_the_po_lines_own_location_is_not_marked(world):
    """The other half of the same rule, or "location differs" would be decoration."""
    po, (line,) = world.purchase_order(f"{MARKER}-PO-SAME", [("BRW-BB", 100, 0)])
    _pso, inquiry = world.sales_order(f"{MARKER}-SO-SAME")
    world.link(world.row(inquiry, 40, location="BRW-BB"), line, 40, f"{MARKER}-PO-SAME")

    (block,) = world.svc.get_one(po)["allocations"]
    (placement,) = block["placements"]
    assert placement["needed_at"] == "BRW-BB"
    assert placement["location_differs"] is False


def test_a_purchase_order_nobody_is_waiting_on_reports_an_empty_panel(world):
    """The empty state is a fact, not an absent key: the panel is always rendered."""
    po, _lines = world.purchase_order(f"{MARKER}-PO-FREE", [("DC1", 80, 0)])
    assert world.svc.get_one(po)["allocations"] == []


def test_only_lines_that_carry_a_placement_appear_in_the_panel(world):
    """The panel answers "who is waiting on this order", so a line nobody is waiting on has
    nothing to say there. The lines grid above it already prints every line."""
    po, (busy, _idle) = world.purchase_order(
        f"{MARKER}-PO-MIXED", [("DC1", 100, 0), ("BRW", 60, 0)]
    )
    _pso, inquiry = world.sales_order(f"{MARKER}-SO-MIXED")
    world.link(world.row(inquiry, 25, location="BRW-BB"), busy, 25, f"{MARKER}-PO-MIXED")

    blocks = world.svc.get_one(po)["allocations"]
    assert [b["line_id"] for b in blocks] == [busy]


def test_free_never_goes_negative_and_a_received_line_nets_its_receipt(world):
    """`outstanding` is what is still to ARRIVE, so a part-received line has less of it to
    promise - and a line promised more than it has left reads free 0, never a negative,
    which would read as a credit the buyer does not have."""
    po, (line,) = world.purchase_order(f"{MARKER}-PO-RECD", [("DC1", 100, 40)])
    _pso, inquiry = world.sales_order(f"{MARKER}-SO-RECD")
    world.link(world.row(inquiry, 90, location="DC1"), line, 90, f"{MARKER}-PO-RECD")

    (block,) = world.svc.get_one(po)["allocations"]
    assert (block["outstanding"], block["allocated"], block["free"]) == (60, 90, 0)


def test_a_cancelled_inquiry_rows_links_are_history_and_occupy_nothing(world):
    """A cancelled row's quantity is not owed any more, so it does not sit on the line.

    The same rule `links_for_rows` already applies for the worklist and the SO detail: two
    readers of one fact must not disagree about whether a superseded revision still holds
    somebody's supply.
    """
    po, (line,) = world.purchase_order(f"{MARKER}-PO-CANX", [("DC1", 100, 0)])
    _pso, inquiry = world.sales_order(f"{MARKER}-SO-CANX")
    row = world.row(inquiry, 30, location="DC1")
    world.link(row, line, 30, f"{MARKER}-PO-CANX")
    world.db.execute(
        text("UPDATE " + P + ".order_inquiry_rows SET state = 'cancelled' WHERE id = :i"),
        {"i": row},
    )
    world.db.flush()

    assert world.svc.get_one(po)["allocations"] == []


# ------------------------------------------------------------------- the wire (AC-G1)


def test_the_response_model_carries_the_panel_rather_than_dropping_it(world, occupied):
    """`response_model` silently drops a field the schema does not declare, so the block is
    asserted THROUGH the schema and not off the service dict - which is exactly how a
    carefully built payload goes out empty."""
    wire = PurchaseOrderSchema.model_validate(world.svc.get_one(occupied["po"]))

    assert wire.allocated_qty == 500
    (block,) = wire.allocations
    assert (block.outstanding, block.allocated, block.free) == (500, 500, 0)
    assert len(block.placements) == 3
    assert {p.location_differs for p in block.placements} == {True}
    assert {p.customer for p in block.placements} == {"YOTU BUILDER SDN BHD"}


# ---------------------------------------------------------- the list column + filter (G4)


def test_the_list_reports_what_each_order_has_allocated(world, occupied):
    """AC-G4: the sum per ORDER, on the list, so a buyer sees at a glance which orders are
    already spoken for without opening each one."""
    world.purchase_order(f"{MARKER}-PO-EMPTY", [("DC1", 90, 0)])

    rows = {
        row["po_number"]: row
        for row in world.svc.list(1, 50, None, "desc", None, None, None)["data"]
    }
    assert rows["PO-2026/07-0029"]["allocated_qty"] == 500
    assert rows[f"{MARKER}-PO-EMPTY"]["allocated_qty"] == 0


def test_the_allocated_filter_keeps_one_side_and_drops_the_other(world, occupied):
    """AC-G4: Allocated = yes | no. The EXACT predicate the column prints, so the filter and
    the figure beside it can never disagree."""
    world.purchase_order(f"{MARKER}-PO-EMPTY", [("DC1", 90, 0)])

    def numbers(allocated):
        return {
            row["po_number"]
            for row in world.svc.list(
                1, 50, None, "desc", None, None, None, allocated=allocated
            )["data"]
        }

    assert numbers(True) == {"PO-2026/07-0029"}
    assert numbers(False) == {f"{MARKER}-PO-EMPTY"}
    assert numbers(None) == {"PO-2026/07-0029", f"{MARKER}-PO-EMPTY"}


def test_the_wire_carries_allocated_qty_on_every_list_row(world, occupied):
    """The same `response_model` trap, on the list this time."""
    payload = world.svc.list(1, 50, None, "desc", None, None, None)
    wire = [PurchaseOrderSchema.model_validate(row) for row in payload["data"]]
    assert {row.allocated_qty for row in wire} == {500}


# ------------------------------------------------------------------ read only (AC-G5)


def test_nothing_in_the_occupancy_read_writes_to_purchase_order_lines(world, occupied):
    """AC-G5. The buyer re-keys the split in AutoCount and re-uploads the book; a read that
    quietly stamped a figure onto the line would be overwritten by that upload and would
    have moved supply in the meantime."""
    before = {
        (row.id, float(row.qty_ordered), float(row.qty_received), row.line_status,
         row.warehouse_id)
        for row in world.db.query(PurchaseOrderLine).all()
    }

    world.svc.get_one(occupied["po"])
    world.svc.list(1, 50, None, "desc", None, None, None, allocated=True)
    world.db.flush()

    after = {
        (row.id, float(row.qty_ordered), float(row.qty_received), row.line_status,
         row.warehouse_id)
        for row in world.db.query(PurchaseOrderLine).all()
    }
    assert before == after
