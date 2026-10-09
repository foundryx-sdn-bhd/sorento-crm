"""Section 3.I - one order inquiry row, many links.

`PLAN-scm-cs-planning-uat.md` section 3.I, `PLAN-scm-purchasing-uat-journey.md` section 4b,
UAC AC-I1 to AC-I10.

`blank_session` throughout, not `pg_session`: the walk this file tests is ranked by the
PURCHASE ORDER BOOK, and the shared local database holds the captain's real 80,000-line
one, so a fixture line for a real product would be judged against thousands of neighbours
and the assertions would move whenever somebody re-uploaded a book. A scratch schema is the
only substrate where "these are the four open lines for this product" is a sentence a test
may say out loud.

The one shape worth knowing before reading: every assertion about ORDER is about a row that
may name a purchase order line and nothing else, and every assertion about ORDER BACK is
about the ONE verb that may also name an `spo_allocations` row (captain, 25 August 2026).
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text

from app.models.base import company_scope
from app.models.project_so import OrderInquirySuggestedLink
from app.services.project_order_inquiry_service import (
    TIER_ELSEWHERE,
    TIER_POOL,
    TIER_SAME_GROUP,
    TIER_SAME_LOCATION,
    TIER_SIBLING,
    ProjectOrderInquiryService,
    link_location_tier,
)

from ._pg_fixture import blank_session

MARKER = "ZZT-LINKS"

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "alembic"
    / "versions"
    / "421_order_inquiry_links.py"
)

#: Relative to today, so an open SPO allocation does not quietly stop being one a few days
#: after this file was written (`spo_supply.open_incoming_clauses` reads the book, not the
#: clock, but the fixture's own dates are read by the walk's ordering).
SOON = date.today() + timedelta(days=30)


def _migration():
    spec = importlib.util.spec_from_file_location("zzt_migration_421", _MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _uid() -> str:
    return str(uuid.uuid4())


#: The `projects` schema THIS session writes to, bound once by the `world` fixture. A
#: module global rather than a parameter on twenty call sites: `blank_session` caches one
#: scratch schema for the whole run, so there is exactly one answer.
P = "projects"


def _projects(db) -> str:
    """Where `projects.*` lives under THIS session.

    `blank_session` builds `<scratch>_projects` and puts it on the search_path, but a
    hard-coded `projects.order_inquiry_rows` inside a `text()` statement is resolved by
    NAME and reaches the REAL schema - writing live data while the test believes it is
    isolated. The same trap migration 420 and 421 carry `_schema` for.
    """
    current = db.execute(text("select current_schema()")).scalar()
    return "projects" if current in (None, "public") else f'"{current}_projects"'


# --------------------------------------------------------------------------- the world


class _World:
    """Everything one test needs, named the way the fixture sheet names it."""

    def __init__(self, db):
        self.db = db
        self.company_id = db.execute(
            text("select id from companies where code = 'SRT'")
        ).scalar()
        self.warehouses: dict[str, str] = {}
        self.svc = ProjectOrderInquiryService(db)
        self._build()

    # -- masters ---------------------------------------------------------------

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
                "code": f"{MARKER}-7405",
                "cat": cat,
                "uom": uom,
            },
        )
        # BRW is a POOL because other locations point at it, which is the only authority
        # the tier rule reads - never the shape of the code.
        pool = _uid()
        db.execute(
            text(
                "INSERT INTO warehouses (id, company_id, warehouse_code, warehouse_name, "
                "is_active) VALUES (:i, :c, 'BRW', 'BRW', true)"
            ),
            {"i": pool, "c": self.company_id},
        )
        self.warehouses["BRW"] = pool
        # `segment = 'project'` on every one of these (S1, captain's ruling on the review
        # round): `is_site_pool(segment)` is what `cascadable` reads now, never
        # `pool_warehouse_id` membership alone, and on the real book EVERY non-pool
        # warehouse is `segment = 'project'` - measured against the 7 Sep prod copy, 55
        # project-segment against 5 dealer-segment (the pools themselves) and zero null.
        # A fixture warehouse left at NULL segment reads as a dealer location too
        # (`COALESCE(segment, 'dealer')`) and would be wrongly cascadable on its own
        # account, which is not the shape production has ever had.
        for code in ("BRW-IB", "DC1-IB", "BRW-BB"):
            wid = _uid()
            db.execute(
                text(
                    "INSERT INTO warehouses (id, company_id, warehouse_code, "
                    "warehouse_name, is_active, pool_warehouse_id, segment) "
                    "VALUES (:i, :c, :code, :code, true, :p, 'project')"
                ),
                {"i": wid, "c": self.company_id, "code": code, "p": pool},
            )
            self.warehouses[code] = wid
        self.supplier = _uid()
        db.execute(
            text(
                "INSERT INTO suppliers (id, company_id, supplier_code, supplier_name, "
                "is_active) VALUES (:i, :c, :code, :code, true)"
            ),
            {"i": self.supplier, "c": self.company_id, "code": f"{MARKER}-SUP"},
        )
        self.pso, self.inquiry = _uid(), _uid()
        db.execute(
            text(
                "INSERT INTO " + P + ".sales_orders (id, company_id, provisional_ref, "
                "status, created_at, updated_at) "
                "VALUES (:i, :c, :ref, 'published', now(), now())"
            ),
            {"i": self.pso, "c": self.company_id, "ref": f"{MARKER}-PSO"},
        )
        db.execute(
            text(
                "INSERT INTO " + P + ".order_inquiries (id, company_id, inquiry_no, "
                "project_sales_order_id, state, raised_at) "
                "VALUES (:i, :c, :no, :p, 'raised', now())"
            ),
            {
                "i": self.inquiry,
                "c": self.company_id,
                "no": f"OI-{_uid()[:6].upper()}",
                "p": self.pso,
            },
        )
        self.line = _uid()
        db.execute(
            text(
                "INSERT INTO " + P + ".sales_order_lines (id, company_id, "
                "project_sales_order_id, line_no, qty, unit_price, amount, product_id, "
                "created_at) VALUES (:i, :c, :p, 1, 100, 0, 0, :prod, now())"
            ),
            {"i": self.line, "c": self.company_id, "p": self.pso, "prod": self.product},
        )
        db.flush()

    # -- documents -------------------------------------------------------------

    def purchase_order(self, number: str, issue_date: date, lines) -> list[str]:
        """One purchase order and its lines. `lines` is (location, qty, expected, label)."""
        po = _uid()
        self.db.execute(
            text(
                "INSERT INTO purchase_orders (id, company_id, po_number, supplier_id, "
                "status, issue_date) VALUES (:i, :c, :n, :s, 'active', :d)"
            ),
            {
                "i": po,
                "c": self.company_id,
                "n": number,
                "s": self.supplier,
                "d": issue_date,
            },
        )
        ids = []
        for location, qty, expected, label in lines:
            lid = _uid()
            self.db.execute(
                text(
                    "INSERT INTO purchase_order_lines (id, company_id, "
                    "purchase_order_id, product_id, warehouse_id, qty_ordered, "
                    "qty_received, line_status, expected_date, source_ref) "
                    "VALUES (:i, :c, :po, :p, :w, :q, 0, 'open', :e, :r)"
                ),
                {
                    "i": lid,
                    "c": self.company_id,
                    "po": po,
                    "p": self.product,
                    "w": self.warehouses[location],
                    "q": qty,
                    "e": expected,
                    "r": label,
                },
            )
            ids.append(lid)
        self.db.flush()
        return ids

    def spo_allocation(
        self, number: str, location: str, qty, *, line_number=1, expected=None
    ) -> str:
        allocation = _uid()
        self.db.execute(
            text(
                "INSERT INTO spo_allocations (id, company_id, spo_number, "
                "spo_line_number, product_id, warehouse_id, allocated_quantity, "
                "quantity_received, quantity_rejected, receipt_status, line_status, "
                "issue_date, expected_date, synced_to_excel, created_at) "
                "VALUES (:i, :c, :n, :ln, :p, :w, :q, 0, 0, 'pending', 'open', :iss, "
                ":exp, false, now())"
            ),
            {
                "i": allocation,
                "c": self.company_id,
                "n": number,
                "ln": line_number,
                "p": self.product,
                "w": self.warehouses[location],
                "q": qty,
                "iss": date(2026, 8, 12),
                "exp": expected or SOON,
            },
        )
        self.db.flush()
        return allocation

    def stock(self, location: str, qty) -> None:
        """On hand at a location, for ladder v4's group net (section 1d)."""
        self.db.execute(
            text(
                "INSERT INTO stock (id, company_id, product_id, warehouse_id, "
                "quantity_on_hand, quantity_reserved, synced_to_excel) "
                "VALUES (:i, :c, :p, :w, :q, 0, false)"
            ),
            {
                "i": _uid(),
                "c": self.company_id,
                "p": self.product,
                "w": self.warehouses[location],
                "q": Decimal(str(qty)),
            },
        )
        self.db.flush()

    def demand(self, location: str, qty) -> None:
        """An OPEN core sales-order line somebody else already owes at a location.

        What makes a group's net negative, and the only thing that can: the netting reads
        the book, never a planning record.
        """
        so = _uid()
        self.db.execute(
            text(
                "INSERT INTO sales_orders (id, company_id, so_number, order_date, "
                "status, demand_class) VALUES (:i, :c, :n, current_date, 'open', "
                "'project')"
            ),
            {"i": so, "c": self.company_id, "n": f"{MARKER}-{_uid()[:6].upper()}"},
        )
        self.db.execute(
            text(
                "INSERT INTO sales_order_lines (id, company_id, sales_order_id, "
                "product_id, warehouse_id, qty_ordered, qty_delivered, line_status, "
                "required_date) VALUES (:i, :c, :so, :p, :w, :q, 0, 'open', :d)"
            ),
            {
                "i": _uid(),
                "c": self.company_id,
                "so": so,
                "p": self.product,
                "w": self.warehouses[location],
                "q": Decimal(str(qty)),
                "d": SOON,
            },
        )
        self.db.flush()

    def another_product(self, code: str) -> str:
        """A SECOND product on the same masters, for a pass that spans more than one."""
        cat = self.db.execute(
            text("SELECT id FROM product_categories WHERE category_code = :c"),
            {"c": f"{MARKER}-CAT"},
        ).scalar()
        uom = self.db.execute(
            text("SELECT id FROM units_of_measure WHERE uom_code = :c"),
            {"c": f"{MARKER}-UOM"},
        ).scalar()
        product = _uid()
        self.db.execute(
            text(
                "INSERT INTO products (id, company_id, product_code, product_name, "
                "category_id, base_uom_id, list_price) "
                "VALUES (:i, :c, :code, :code, :cat, :uom, 0)"
            ),
            {"i": product, "c": self.company_id, "code": code, "cat": cat, "uom": uom},
        )
        self.db.flush()
        return product

    def row(self, verb: str, qty, *, location="BRW-IB", cited=None, so_line=True,
            item_code=None, ack_state="acknowledged"):
        """One instruction, ACKNOWLEDGED by default (`PLAN-scm-oi-handshake.md`).

        Every test in this file is about where a row's quantity is LINKED, and since the
        handshake a link is only ever made on a row purchasing has taken on - the cascade
        and the Link dialog both refuse an awaiting one. Seeding these `awaiting` would
        make the file exercise nothing at all; `ack_state` is a parameter so a test can
        still ask for a row nobody has read.
        """
        rid = _uid()
        self.db.execute(
            text(
                "INSERT INTO " + P + ".order_inquiry_rows (id, company_id, "
                "order_inquiry_id, so_line_id, item_code, qty, verb, stock_location, "
                "cited_document, state, ack_state, redirected_to_pool, created_at) "
                "VALUES (:i, :c, :inq, :l, :code, :q, :v, :loc, :cd, 'raised', "
                ":ack, false, now())"
            ),
            {
                "i": rid,
                "c": self.company_id,
                "inq": self.inquiry,
                "l": self.line if so_line else None,
                "code": item_code or f"{MARKER}-7405",
                "q": Decimal(str(qty)),
                "v": verb,
                "loc": location,
                "cd": cited,
                "ack": ack_state,
            },
        )
        self.db.flush()
        return self.svc._row_or_404(rid)


@pytest.fixture()
def world():
    global P
    with blank_session() as db:
        P = _projects(db)
        built = _World(db)
        with company_scope(db, frozenset({built.company_id})):
            yield built


# ------------------------------------------------------------------- the tier rule (Q5)


def test_link_location_tier_ranks_the_four_tiers_and_the_pool_sub_rank():
    """Q5, ruled 25 August 2026: "same location, same group location, pool, then sibling
    location". The sub-rank exists for the pool tier alone and puts the row's OWN site
    pool ahead of the others - "BRW, then the other pools" - which one integer cannot
    say."""
    pools = {"BRW", "MWH", "DC1", "WH3", "RSW"}

    assert link_location_tier("BRW-IB", "BRW-IB", pools) == (TIER_SAME_LOCATION, 0)
    assert link_location_tier("BRW-IB", "DC1-IB", pools) == (TIER_SAME_GROUP, 0)
    assert link_location_tier("BRW-IB", "MWH-IB", pools) == (TIER_SAME_GROUP, 0)
    assert link_location_tier("BRW-IB", "BRW", pools) == (TIER_POOL, 0)
    assert link_location_tier("BRW-IB", "MWH", pools) == (TIER_POOL, 1)
    assert link_location_tier("BRW-IB", "BRW-BB", pools) == (TIER_SIBLING, 0)
    assert link_location_tier("BRW-IB", "RSW-QQ", pools) == (TIER_ELSEWHERE, 0)


def test_link_location_tier_ranks_nothing_when_either_side_names_no_location():
    """A row that names no location can be ranked by nothing, so every candidate is the
    last tier and the DATES decide - honest, rather than claiming a fit nobody stated."""
    pools = {"BRW"}
    assert link_location_tier(None, "BRW-IB", pools) == (TIER_ELSEWHERE, 0)
    assert link_location_tier("BRW-IB", None, pools) == (TIER_ELSEWHERE, 0)


def test_a_pool_is_decided_by_the_foreign_key_not_by_the_shape_of_the_code():
    """Every pool on the live book happens to be a plain site code with no hyphen, and
    that is a naming convention the data does not enforce. A code nobody points at is a
    sibling location, whatever it looks like."""
    assert link_location_tier("BRW-IB", "BRW", set()) == (TIER_SIBLING, 0)


# -------------------------------------------------- the candidate walk (AC-I2, AC-I10)


def _two_purchase_orders(world) -> dict:
    """The fixture sheet's own shape: an APRIL order whose BRW-IB line is late, and an
    AUGUST order whose BRW-IB line arrives EARLIER. The April one must still win, because
    the cascade orders on the purchase order's own issue date first (Q7)."""
    april = world.purchase_order(
        "202604-S0083",
        date(2026, 4, 28),
        [
            ("BRW-BB", 27, date(2026, 9, 15), "7"),
            ("BRW", 165, date(2026, 5, 10), "5"),
            ("DC1-IB", 40, date(2026, 10, 1), "3"),
            ("BRW-IB", 25, date(2026, 8, 19), "1"),
        ],
    )
    august = world.purchase_order(
        "202608-S0015", date(2026, 8, 7), [("BRW-IB", 500, date(2026, 6, 1), "1")]
    )
    return {"april": april, "august": august}


def test_candidates_rank_by_location_tier_then_by_the_purchase_orders_own_date(world):
    """AC-I2 and AC-I10. Within a document BRW-IB ranks before DC1-IB, before BRW, before
    BRW-BB; ACROSS documents the April order's BRW-IB line beats the August one's even
    though the August line is promised eleven weeks earlier (Q7: "PO document date first,
    then line expected date"). Location NEVER filters a candidate out - all five lines are
    offered."""
    _two_purchase_orders(world)
    row = world.row("ORDER", 8)

    candidates = world.svc.po_candidates_for_row(row.id)

    assert [(c["po_number"], c["location"], c["tier"]) for c in candidates] == [
        ("202604-S0083", "BRW-IB", TIER_SAME_LOCATION),
        ("202608-S0015", "BRW-IB", TIER_SAME_LOCATION),
        ("202604-S0083", "DC1-IB", TIER_SAME_GROUP),
        ("202604-S0083", "BRW", TIER_POOL),
        ("202604-S0083", "BRW-BB", TIER_SIBLING),
    ]


def test_a_candidate_states_both_dates_and_its_line_label(world):
    """AC-I10: "Candidate list shows both dates". A list that showed one could not be
    checked against the rule that orders on the other."""
    _two_purchase_orders(world)
    row = world.row("ORDER", 8)

    first = world.svc.po_candidates_for_row(row.id)[0]

    assert first["issue_date"] == date(2026, 4, 28)
    assert first["expected_date"] == date(2026, 8, 19)
    assert first["line_label"] == "L1"
    # AC-H8: the top-ranked candidate is the row's own BRW-IB line - a project location,
    # never cascadable - so the cascade's own preview reads zero for it. `default_take`
    # lands on the BRW POOL line further down the same list instead (AC-D2: 165 on hand
    # comfortably covers the 8 needed).
    assert first["default_take"] == "0", "a project-location line is never auto-taken"
    pool_candidate = next(
        c for c in world.svc.po_candidates_for_row(row.id) if c["tier"] == TIER_POOL
    )
    assert pool_candidate["default_take"] == "8", "the pool line covers it instead"


def test_default_take_reads_zero_for_every_candidate_the_cascade_will_not_deal(world):
    """AC-D5. A row needing more than every cascadable candidate combined - the pool
    line's own 165 is short of the 200 needed here - is the all-or-nothing case, and
    EVERY candidate's `default_take` has to read zero, not just the ones the walk would
    otherwise have skipped: the dialog's own preview must never promise a take the pass
    itself will not make, or the two read as two different opinions."""
    _two_purchase_orders(world)
    row = world.row("ORDER", 200)

    candidates = world.svc.po_candidates_for_row(row.id)

    assert len(candidates) == 5, "every location tier is still LISTED, never filtered out"
    assert {c["default_take"] for c in candidates} == {"0"}, (
        "the cascadable total (165, the pool line) is short of the 200 needed, so the "
        "cascade takes nothing - and the preview has to agree candidate by candidate"
    )


def test_an_order_row_is_offered_the_shipping_order_before_any_purchase_order(world):
    """REVERSED by `PLAN-scm-oi-draft-links.md` R5 (captain, 27 Aug 2026): "SPO link is
    always one, always SPO first then PO".

    The rule used to be the captain's own of 25 August - "only for order back, we link to
    SPO allocations" - on the reading that an SPO answers a shortfall against something
    already ordered. The 27 August reading is simpler and matches the book: an open
    shipping-order allocation is stock already bought and on its way, so an ORDER should be
    answered by it before a new purchase order is dealt out.
    """
    _two_purchase_orders(world)
    world.spo_allocation("SPO-2026/08-0061", "BRW", 332)
    row = world.row("ORDER", 8)

    candidates = world.svc.po_candidates_for_row(row.id)

    assert candidates[0]["kind"] == "spo"
    assert [c["kind"] for c in candidates[1:]] == ["po"] * 5


def test_an_spo_candidate_names_its_source_pos_supplier_not_its_own(world):
    """OI-SUPPLIER-FROM-PO (owner, 9 Oct 2026: "the supplier always take from PO, not
    the SPO"): the link dialog's SPO candidate - and the note `_write_link` writes off
    it - names the supplier of the PO the SPO line was raised from (`from_po_number`),
    not the allocation's own `supplier_id`."""
    _two_purchase_orders(world)
    spo_supplier = _uid()
    world.db.execute(
        text(
            "INSERT INTO suppliers (id, company_id, supplier_code, supplier_name, "
            "is_active) VALUES (:i, :c, :code, :code, true)"
        ),
        {"i": spo_supplier, "c": world.company_id, "code": f"{MARKER}-SPO-SUP"},
    )
    allocation = world.spo_allocation("SPO-2026/08-0061", "BRW", 332)
    world.db.execute(
        text(
            "UPDATE spo_allocations SET supplier_id = :s, from_po_number = :n "
            "WHERE id = :i"
        ),
        {"s": spo_supplier, "n": "202608-S0015", "i": allocation},
    )
    world.db.flush()
    row = world.row("ORDER", 8)

    [spo] = [c for c in world.svc.po_candidates_for_row(row.id) if c["kind"] == "spo"]

    assert spo["supplier_name"] == f"{MARKER}-SUP"


def test_an_order_back_walks_its_cited_document_first_then_spo_then_purchase_orders(world):
    """AC-I2 + part 2 section 4b, against the fixture's SRTWCY7405-PJ row: the document CS
    cited comes before everything, then SPO allocations, then purchase order lines."""
    _two_purchase_orders(world)
    world.spo_allocation("SPO-2026/08-0061", "BRW", 332, line_number=2)
    row = world.row("ORDER_BACK", 10, cited="SPO-2026/08-0061")

    candidates = world.svc.po_candidates_for_row(row.id)

    assert (candidates[0]["kind"], candidates[0]["po_number"]) == (
        "spo",
        "SPO-2026/08-0061",
    )
    assert candidates[0]["cited"] is True
    assert candidates[0]["line_label"] == "L2"
    assert [c["kind"] for c in candidates[1:]] == ["po"] * 5


def test_a_cited_purchase_order_beats_an_uncited_one_of_the_same_tier(world):
    """The citation is the most specific thing anybody knows about the row, so it is the
    OUTERMOST key - ahead of the tier and ahead of both dates."""
    _two_purchase_orders(world)
    row = world.row("ORDER_BACK", 8, cited="202608-S0015")

    candidates = world.svc.po_candidates_for_row(row.id)

    assert candidates[0]["po_number"] == "202608-S0015"
    assert candidates[0]["cited"] is True


# ------------------------------------------------------------- linking (AC-I6, AC-I7)


def test_the_cascade_links_across_two_lines_and_never_splits_the_row(world):
    """AC-I6, the captain on SO414285: "1 line here should correspond to 1 line in sales
    order, so 1 line can be placed by multiple PO and SPO". 8 needed, 5 on the first line
    and 3 on the second - ONE row, two links, and the row keeps its 8."""
    orders = _two_purchase_orders(world)
    first, second = orders["april"][3], orders["august"][0]
    row = world.row("ORDER", 8)

    world.svc.place_on_po_allocations(
        row.id,
        [
            {"po_line_id": first, "qty": Decimal("5")},
            {"po_line_id": second, "qty": Decimal("3")},
        ],
        actor_user_id=None,
    )
    world.db.flush()
    world.db.refresh(row)

    body = world.svc.serialize_rows([row])[0]
    assert body["qty"] == "8"
    assert body["state"] == "placed"
    assert body["linked_qty"] == "8"
    assert [(link["qty"], link["document"]) for link in body["links"]] == [
        ("5", "202604-S0083"),
        ("3", "202608-S0015"),
    ]
    assert (
        world.db.execute(
            text(
                "SELECT count(*) FROM " + P + ".order_inquiry_rows "
                "WHERE order_inquiry_id = :i"
            ),
            {"i": world.inquiry},
        ).scalar()
        == 1
    )


def test_a_part_covered_row_reads_partly_linked_and_keeps_its_quantity(world):
    """AC-I7. 8 needed, 5 linked: the row is PARTLY LINKED at 8, and the dialog opens
    looking for the 3 nobody has covered."""
    orders = _two_purchase_orders(world)
    row = world.row("ORDER", 8)

    world.svc.place_on_po_allocations(
        row.id, [{"po_line_id": orders["april"][3], "qty": Decimal("5")}], actor_user_id=None
    )
    world.db.flush()
    world.db.refresh(row)

    assert row.state == "partly_linked"
    assert row.qty == Decimal("8")
    assert world.svc._unlinked_need(row) == Decimal("3")
    assert row.po_ref == "202604-S0083", "the first link is the derived display"


def test_auto_link_finishes_a_partly_linked_row_and_a_second_pass_links_nothing(world):
    """The cascade's idempotence, which the three automatic triggers all rely on - and
    the widening the links table brought: a PARTLY LINKED row is in scope, because it is
    exactly the row a fresh purchase order should finish.

    S3 reversal: the remainder the walk finds is a SUGGESTION now, never a real link,
    so the row stays `partly_linked` rather than moving to `placed`, and it re-enters
    the walk on every pass (a suggestion is not a placement that settles the row) - so
    `placed_rows` does NOT drop to 0 on the second pass any more. What idempotence
    means now is that an unchanged answer is not deleted and rewritten
    (`_same_placement`): the suggestion itself carries the SAME id and `suggested_at`
    across both passes.
    """
    orders = _two_purchase_orders(world)
    row = world.row("ORDER", 8)
    world.svc.place_on_po_allocations(
        row.id, [{"po_line_id": orders["april"][3], "qty": Decimal("5")}], actor_user_id=None
    )
    world.db.flush()

    first = world.svc.auto_place_for_products(None, actor_user_id=None, trigger="zzt")
    world.db.flush()
    world.db.refresh(row)
    assert first["placed_rows"] == 1
    assert row.state == "partly_linked"
    first_suggestion = (
        world.db.query(OrderInquirySuggestedLink)
        .filter(OrderInquirySuggestedLink.row_id == row.id)
        .one()
    )
    assert first_suggestion.qty == Decimal("3")

    second = world.svc.auto_place_for_products(None, actor_user_id=None, trigger="zzt")
    assert second["placed_rows"] == 1
    second_suggestion = (
        world.db.query(OrderInquirySuggestedLink)
        .filter(OrderInquirySuggestedLink.row_id == row.id)
        .one()
    )
    assert second_suggestion.id == first_suggestion.id, (
        "an unchanged answer is not deleted and rewritten"
    )
    assert second_suggestion.suggested_at == first_suggestion.suggested_at


def test_an_spo_line_outside_the_pool_is_never_offered(world):
    """R11: "show every location in the lightbox, link only from POOL locations".

    An SPO is allocated at a pool and moved out from there; a line already sitting at a
    site's own group is spoken for by that site, and dealing it out would promise it twice.
    Never OFFERED, so neither the walk nor the dialog can take it - a person naming one by
    hand is still let through, exactly as `manual` already reaches a purchase order that is
    not yet active, and the container planner's ticks depend on that.
    """
    _two_purchase_orders(world)
    allocation = world.spo_allocation("SPO-2026/08-0061", "BRW-BB", 332)
    row = world.row("ORDER", 8)

    assert all(c["kind"] == "po" for c in world.svc.po_candidates_for_row(row.id))

    world.svc.place_on_po_allocations(
        row.id,
        [{"spo_allocation_id": allocation, "qty": Decimal("8")}],
        actor_user_id=None,
    )
    world.db.flush()

    assert [link.spo_allocation_id for link in world.svc._links_of(row.id)] == [allocation]


def test_an_order_back_may_link_to_an_spo_allocation(world):
    """The verb the SPO side was built for, still doing what it did (R5 widened the set,
    it took nothing away)."""
    allocation = world.spo_allocation("SPO-2026/08-0061", "BRW", 332)
    row = world.row("ORDER_BACK", 10, cited="SPO-2026/08-0061")

    world.svc.place_on_po_allocations(
        row.id,
        [{"spo_allocation_id": allocation, "qty": Decimal("10")}],
        actor_user_id=None,
    )
    world.db.flush()
    world.db.refresh(row)

    body = world.svc.serialize_rows([row])[0]
    assert row.state == "placed"
    assert body["links"][0]["kind"] == "spo"
    assert body["links"][0]["document"] == "SPO-2026/08-0061"


def test_a_row_whose_only_cover_is_an_spo_still_offers_a_link(world):
    """The flag the row action reads. With no purchase order in the world at all, a row
    still has somewhere to go, and a flag that counted purchase orders alone hid the Link
    button on the one row the feature was built for.

    BOTH verbs since R5 (`PLAN-scm-oi-draft-links.md`): an ORDER is answered by an open
    shipping order as readily as an order back is.
    """
    world.spo_allocation("SPO-2026/08-0061", "BRW", 332)
    back = world.row("ORDER_BACK", 10)
    plain = world.row("ORDER", 10)

    candidates = world.svc.link_candidate_products([world.product])

    assert candidates["po"] == set()
    assert candidates["spo"] == {world.product}
    assert world.svc.has_link_candidate(back.verb, world.product, candidates) is True
    assert world.svc.has_link_candidate(plain.verb, world.product, candidates) is True


def test_an_spo_outside_the_pool_offers_no_link_at_all(world):
    """The other half of R11, on the flag: a line the walk will never take must not put a
    Link on a row, or the dialog opens empty."""
    world.spo_allocation("SPO-2026/08-0061", "BRW-BB", 332)
    world.row("ORDER_BACK", 10)

    candidates = world.svc.link_candidate_products([world.product])

    assert candidates["spo"] == set()


def test_unlinking_one_link_leaves_the_others_and_the_row_partly_linked(world):
    """A row sitting on two documents can give ONE back. Before the child table, Unplace
    was the only move and it took the whole placement with it."""
    orders = _two_purchase_orders(world)
    row = world.row("ORDER", 8)
    world.svc.place_on_po_allocations(
        row.id,
        [
            {"po_line_id": orders["april"][3], "qty": Decimal("5")},
            {"po_line_id": orders["august"][0], "qty": Decimal("3")},
        ],
        actor_user_id=None,
    )
    world.db.flush()

    links = world.svc._links_of(row.id)
    world.svc.unplace(row.id, actor_user_id=None, link_id=links[1].id)
    world.db.refresh(row)

    assert row.state == "partly_linked"
    assert [link.qty for link in world.svc._links_of(row.id)] == [Decimal("5.0000")]

    world.svc.unplace(row.id, actor_user_id=None)
    world.db.refresh(row)
    assert row.state == "raised"
    assert world.svc._links_of(row.id) == []
    assert row.po_ref is None and row.po_line_id is None


def test_a_link_never_claims_quantity_another_row_already_holds(world):
    """`remaining` nets every OTHER link on the same line, which is what stops two rows
    being pointed at the same purchase-order quantity."""
    orders = _two_purchase_orders(world)
    line = orders["april"][3]  # BRW-IB, 25 ordered
    first = world.row("ORDER", 20)
    world.svc.place_on_po_allocations(
        first.id, [{"po_line_id": line, "qty": Decimal("20")}], actor_user_id=None
    )
    world.db.flush()

    second = world.row("ORDER", 20)
    candidate = next(
        c
        for c in world.svc.po_candidates_for_row(second.id)
        if c["po_line_id"] == line
    )

    assert candidate["remaining"] == "5"
    assert candidate["already_tagged"] == "20"


# ------------------------------------------------------------ the worklist and SO detail


def test_the_linked_filter_answers_po_spo_and_none(world):
    """AC-I5. WHERE a row is linked is a different question from what STATE it is in: a
    buyer asking "what have I still not put on anything" wants `none`."""
    from app.services.order_inquiry_worklist_service import OrderInquiryWorklistService

    orders = _two_purchase_orders(world)
    allocation = world.spo_allocation("SPO-2026/08-0061", "BRW", 332)

    on_po = world.row("ORDER", 8)
    world.svc.place_on_po_allocations(
        on_po.id, [{"po_line_id": orders["april"][3], "qty": Decimal("8")}], actor_user_id=None
    )
    on_spo = world.row("ORDER_BACK", 10)
    world.svc.place_on_po_allocations(
        on_spo.id, [{"spo_allocation_id": allocation, "qty": Decimal("10")}], actor_user_id=None
    )
    unlinked = world.row("ORDER", 4)
    world.db.flush()

    worklist = OrderInquiryWorklistService(world.db)

    def ids(**filters):
        return {
            entry["id"] for entry in worklist.list_rows(limit=100, **filters)["data"]
        }

    assert on_po.id in ids(linked="po") and on_spo.id not in ids(linked="po")
    assert on_spo.id in ids(linked="spo") and on_po.id not in ids(linked="spo")
    assert unlinked.id in ids(linked="none")
    assert {on_po.id, on_spo.id} & ids(linked="none") == set()


def test_the_worklist_row_carries_its_links_and_the_flow_reads_them(world):
    """"Taken from PO" and "Remaining" come off the LINKS now: the pair used to be "sum
    the placed siblings" against "sum the raised siblings", which was only true because
    the cascade had split the line into two rows."""
    from app.services.order_inquiry_worklist_service import OrderInquiryWorklistService

    orders = _two_purchase_orders(world)
    row = world.row("ORDER", 19)
    world.svc.place_on_po_allocations(
        row.id, [{"po_line_id": orders["april"][3], "qty": Decimal("5")}], actor_user_id=None
    )
    world.db.flush()

    entry = next(
        item
        for item in OrderInquiryWorklistService(world.db).list_rows(limit=100)["data"]
        if item["id"] == row.id
    )

    assert entry["linked_qty"] == "5"
    assert entry["taken_from_po"] == "5"
    assert entry["remaining_open"] == "14"
    assert [link["document"] for link in entry["links"]] == ["202604-S0083"]


def test_the_sales_order_detail_states_where_each_lines_buy_sits(world):
    """AC-I9, on the wire. `None` when no inquiry row covers the line at all, `[]` when
    one does and holds no link: "nobody was told" and "told, nothing linked" are different
    answers and the column prints each in its own words."""
    from app.models.order import SalesOrder
    from app.models.procurement import PurchaseOrder
    from app.services.scm.sales_order_service import SalesOrderService

    orders = _two_purchase_orders(world)
    core_so, core_line = _uid(), _uid()
    world.db.execute(
        text(
            "INSERT INTO customers (id, company_id, customer_code, customer_name, "
            "is_active) VALUES (:i, :c, :code, :code, true)"
        ),
        {"i": (customer := _uid()), "c": world.company_id, "code": f"{MARKER}-CUST"},
    )
    world.db.execute(
        text(
            "INSERT INTO sales_orders (id, company_id, so_number, customer_id, status, "
            "demand_class, order_date) "
            "VALUES (:i, :c, :n, :cu, 'open', 'project', :d)"
        ),
        {
            "i": core_so,
            "c": world.company_id,
            "n": f"{MARKER}-SO",
            "cu": customer,
            "d": date(2026, 8, 1),
        },
    )
    world.db.execute(
        text(
            "INSERT INTO sales_order_lines (id, company_id, sales_order_id, product_id, "
            "warehouse_id, qty_ordered, qty_required, qty_delivered, line_status, "
            "purchasing_status, required_date) "
            "VALUES (:i, :c, :so, :p, :w, 8, 8, 0, 'open', 'pending', :d)"
        ),
        {
            "i": core_line,
            "c": world.company_id,
            "so": core_so,
            "p": world.product,
            "w": world.warehouses["BRW-IB"],
            "d": date(2026, 9, 1),
        },
    )
    world.db.execute(
        text(
            "UPDATE " + P + ".sales_order_lines SET core_sales_order_line_id = :core "
            "WHERE id = :i"
        ),
        {"core": core_line, "i": world.line},
    )
    row = world.row("ORDER", 8)
    world.svc.place_on_po_allocations(
        row.id, [{"po_line_id": orders["april"][3], "qty": Decimal("5")}], actor_user_id=None
    )
    world.db.flush()

    order = world.db.query(SalesOrder).filter(SalesOrder.id == core_so).first()
    body = SalesOrderService(world.db).serialize(order, line_planning=True)
    april_po_id = str(
        world.db.query(PurchaseOrder.id)
        .filter(PurchaseOrder.po_number == "202604-S0083")
        .scalar()
    )

    linked = body["lines"][0]["linked_to"]
    assert linked == [
        {
            "kind": "po",
            "document": "202604-S0083",
            "line_label": "L1",
            # L4 (review round): the header id, so `document` can be a link.
            "purchase_order_id": april_po_id,
            "qty": "5",
            "location": "BRW-IB",
            # AC-P3-7: the document lands 2026-08-19 and the line wants it 2026-09-01, so
            # it is early. The flag reaches this reader too, not only the worklist, and so
            # does the day count beside it (AC-D17) - `None` on a document that is in time.
            "late": False,
            "late_days": None,
            "expected_date": "2026-08-19",
            # Declared on `SalesOrderLineLink` since the 17 Sep derived-SPO fix; False on
            # a stored link.
            "derived": False,
        }
    ]


# --------------------------------------------------------- migration 421's data half


def _placed_row(world, qty, *, po_line=None, document=None, created="now()"):
    rid = _uid()
    world.db.execute(
        text(
            "INSERT INTO " + P + ".order_inquiry_rows (id, company_id, order_inquiry_id, "
            "so_line_id, item_code, qty, verb, po_ref, po_line_id, state, "
            "redirected_to_pool, created_at) "
            f"VALUES (:i, :c, :inq, :l, :code, :q, 'ORDER', :ref, :pl, 'placed', false, "
            f"{created})"
        ),
        {
            "i": rid,
            "c": world.company_id,
            "inq": world.inquiry,
            "l": world.line,
            "code": f"{MARKER}-7405",
            "q": Decimal(str(qty)),
            "ref": document,
            "pl": po_line,
        },
    )
    world.db.flush()
    return rid


def test_migration_421_writes_one_link_per_placed_row_and_clears_the_scalar(world):
    """AC-I8, first step. The row's own quantity IS the fragment's allocation, which is
    what a split row was."""
    migration = _migration()
    line = world.purchase_order(
        "202607-S0105", date(2026, 7, 29), [("BRW-BB", 20, date(2026, 8, 3), "3")]
    )[0]
    _placed_row(world, 5, po_line=line, document="202607-S0105")

    written = migration.links_from_placed_rows(world.db.connection())

    assert written == 1
    assert world.db.execute(
        text(
            "SELECT qty, document FROM " + P + ".order_inquiry_links "
            "WHERE po_line_id = :l"
        ),
        {"l": line},
    ).fetchall() == [(Decimal("5.0000"), "202607-S0105")]
    assert (
        world.db.execute(
            text(
                "SELECT count(*) FROM " + P + ".order_inquiry_rows "
                "WHERE order_inquiry_id = :i AND po_line_id IS NOT NULL"
            ),
            {"i": world.inquiry},
        ).scalar()
        == 0
    ), "the scalar stops being written the moment the links carry it"


def test_migration_421_merges_a_split_pair_into_one_row_with_two_links(world):
    """AC-I8, the shape the captain saw on SO414285: M310-CR-PJ's 8 is two rows, 5 + 3 on
    two lines of 202607-S0105, and it must come back as ONE instruction."""
    migration = _migration()
    lines = world.purchase_order(
        "202607-S0105",
        date(2026, 7, 29),
        [("BRW-BB", 20, date(2026, 8, 3), "3"), ("BRW-BB", 20, date(2026, 8, 3), "7")],
    )
    _placed_row(
        world, 5, po_line=lines[0], document="202607-S0105",
        created="now() - interval '2 hours'",
    )
    _placed_row(
        world, 3, po_line=lines[1], document="202607-S0105",
        created="now() - interval '1 hour'",
    )
    bind = world.db.connection()
    migration.links_from_placed_rows(bind)

    merged = migration.merge_split_rows(bind)
    migration.refresh_link_states(bind)

    assert merged == {"merged_groups": 1, "rows_removed": 1}
    rows = world.db.execute(
        text(
            "SELECT qty, state, po_ref FROM " + P + ".order_inquiry_rows "
            "WHERE order_inquiry_id = :i"
        ),
        {"i": world.inquiry},
    ).fetchall()
    assert rows == [(Decimal("8.0000"), "placed", "202607-S0105")]
    assert (
        world.db.execute(
            text("SELECT count(*) FROM " + P + ".order_inquiry_links")
        ).scalar()
        == 2
    )


def test_migration_421_is_idempotent(world):
    """The shared dev database converges through `create_all` rather than `upgrade`, so
    every step has to be a no-op the second time it is asked."""
    migration = _migration()
    line = world.purchase_order(
        "202607-S0105", date(2026, 7, 29), [("BRW-BB", 20, date(2026, 8, 3), "3")]
    )[0]
    _placed_row(world, 5, po_line=line, document="202607-S0105")
    bind = world.db.connection()
    migration.links_from_placed_rows(bind)
    migration.merge_split_rows(bind)
    migration.refresh_link_states(bind)

    assert migration.links_from_placed_rows(bind) == 0
    assert migration.merge_split_rows(bind) == {"merged_groups": 0, "rows_removed": 0}
    assert (
        world.db.execute(
            text("SELECT count(*) FROM " + P + ".order_inquiry_links")
        ).scalar()
        == 1
    )


def test_migration_421_restates_a_part_covered_row_as_partly_linked(world):
    """The state is DERIVED from the links, never carried over: a row whose links cover
    part of it is the middle the table made expressible."""
    migration = _migration()
    line = world.purchase_order(
        "202607-S0105", date(2026, 7, 29), [("BRW-BB", 20, date(2026, 8, 3), "3")]
    )[0]
    row_id = _placed_row(world, 8, po_line=line, document="202607-S0105")
    bind = world.db.connection()
    migration.links_from_placed_rows(bind)
    world.db.execute(
        text("UPDATE " + P + ".order_inquiry_links SET qty = 5 WHERE row_id = :r"),
        {"r": row_id},
    )

    migration.refresh_link_states(bind)

    assert world.db.execute(
        text("SELECT state FROM " + P + ".order_inquiry_rows WHERE id = :i"),
        {"i": row_id},
    ).scalar() == "partly_linked"


def test_migration_421_downgrade_splits_the_row_back_one_row_per_link(world):
    """A merge cannot be undone row for row, but the SHAPE the pre-421 code reads can be
    rebuilt exactly: one placed row per link, at that link's quantity and line."""
    migration = _migration()
    lines = world.purchase_order(
        "202607-S0105",
        date(2026, 7, 29),
        [("BRW-BB", 20, date(2026, 8, 3), "3"), ("BRW-BB", 20, date(2026, 8, 3), "7")],
    )
    _placed_row(
        world, 5, po_line=lines[0], document="202607-S0105",
        created="now() - interval '2 hours'",
    )
    _placed_row(
        world, 3, po_line=lines[1], document="202607-S0105",
        created="now() - interval '1 hour'",
    )
    bind = world.db.connection()
    migration.links_from_placed_rows(bind)
    migration.merge_split_rows(bind)
    migration.refresh_link_states(bind)

    migration.split_rows_from_links(bind)

    assert sorted(
        world.db.execute(
            text(
                "SELECT qty, state, po_ref FROM " + P + ".order_inquiry_rows "
                "WHERE order_inquiry_id = :i ORDER BY qty"
            ),
            {"i": world.inquiry},
        ).fetchall()
    ) == [
        (Decimal("3.0000"), "placed", "202607-S0105"),
        (Decimal("5.0000"), "placed", "202607-S0105"),
    ]


def test_migration_421_round_trips_an_spo_link_through_spo_ref(world):
    """An SPO link has no `po_line_id` to restore, so the pre-421 shape for it is the
    row's own `spo_ref` - the only column that ever named a shipping order. The upgrade
    reads it back, so downgrade-then-upgrade is a round trip rather than a loss."""
    migration = _migration()
    allocation = world.spo_allocation("SPO-2026/08-0061", "BRW", 332)
    row = world.row("ORDER_BACK", 10, cited="SPO-2026/08-0061")
    world.svc.place_on_po_allocations(
        row.id,
        [{"spo_allocation_id": allocation, "qty": Decimal("10")}],
        actor_user_id=None,
    )
    world.db.flush()
    bind = world.db.connection()

    migration.split_rows_from_links(bind)
    world.db.execute(text("DELETE FROM " + P + ".order_inquiry_links"))
    assert world.db.execute(
        text("SELECT spo_ref, po_ref FROM " + P + ".order_inquiry_rows WHERE id = :i"),
        {"i": row.id},
    ).fetchone() == ("SPO-2026/08-0061", None)

    assert migration.links_from_placed_rows(bind) == 1
    restored = world.db.execute(
        text(
            "SELECT spo_allocation_id, document FROM " + P + ".order_inquiry_links "
            "WHERE row_id = :r"
        ),
        {"r": row.id},
    ).fetchone()
    assert (str(restored[0]), restored[1]) == (allocation, "SPO-2026/08-0061")


def test_migration_421_never_turns_an_already_inbound_coverage_note_into_a_link(world):
    """`spo_ref` is also the coverage reference the netting engine writes on an ALREADY
    INBOUND row - a note about what already covers the quantity, never a placement.
    Turning one into a link would have retired demand nobody linked."""
    migration = _migration()
    world.spo_allocation("SPO-2026/08-0061", "BRW", 332)
    rid = _uid()
    world.db.execute(
        text(
            "INSERT INTO " + P + ".order_inquiry_rows (id, company_id, order_inquiry_id, "
            "so_line_id, item_code, qty, verb, spo_ref, state, redirected_to_pool, "
            "created_at) VALUES (:i, :c, :inq, :l, :code, 10, 'ALREADY_INBOUND', "
            "'SPO-2026/08-0061', 'raised', false, now())"
        ),
        {
            "i": rid,
            "c": world.company_id,
            "inq": world.inquiry,
            "l": world.line,
            "code": f"{MARKER}-7405",
        },
    )
    world.db.flush()

    assert migration.links_from_placed_rows(world.db.connection()) == 0
    assert world.db.execute(
        text("SELECT spo_ref FROM " + P + ".order_inquiry_rows WHERE id = :i"), {"i": rid}
    ).scalar() == "SPO-2026/08-0061"


# ------------------------------------------------- the order back through a re-confirm


def test_a_partly_linked_order_back_is_netted_half_on_the_next_revision(world):
    """The hole a borrow left is re-judged on every confirmation, and a row that already
    has PART of it on a document must not be asked for that part a second time.

    Before this, the netting recognised `actioned` and `placed` and read a partly linked
    row as untouched: the whole hole was raised again on top of a row already half
    covered, so purchasing was told to buy the covered quantity twice. The row is now
    shrunk to what is linked and only the uncovered remainder is re-raised.
    """
    orders = _two_purchase_orders(world)
    row = world.row("ORDER_BACK", 15, location="BRW-BB")
    world.svc.place_on_po_allocations(
        row.id,
        [{"po_line_id": orders["april"][0], "qty": Decimal("10")}],
        actor_user_id=None,
    )
    world.db.flush()
    world.db.refresh(row)
    assert row.state == "partly_linked"

    class _Decision:
        id = None
        revision_no = 2

    raised = world.svc._raise_borrow_shortfalls(
        type("O", (), {"company_id": world.company_id})(),
        type("I", (), {"id": world.inquiry})(),
        _Decision(),
        [
            {
                "item_code": f"{MARKER}-7405",
                "stock_location": "BRW-BB",
                "qty": Decimal("15"),
                "required_date": None,
                "line": None,
                "note": "hole",
            }
        ],
    )
    world.db.flush()
    world.db.refresh(row)

    assert raised == 1, "only the 5 nobody covered is raised again"
    assert row.state == "placed", "the covered part stands, at the covered quantity"
    assert row.qty == Decimal("10")
    fresh = (
        world.db.execute(
            text(
                "SELECT qty FROM " + P + ".order_inquiry_rows "
                "WHERE order_inquiry_id = :i AND state = 'raised' AND verb = 'ORDER_BACK'"
            ),
            {"i": world.inquiry},
        ).fetchall()
    )
    assert [entry[0] for entry in fresh] == [Decimal("5.0000")]


# ------------------------------------------------------- ladder v4 (AC-L11, section 1d)


def test_a_group_in_deficit_offers_none_of_its_purchase_order_lines(world):
    """AC-L11, `B2155-NL-BLUE` measured 26 August 2026.

    The IB group nets -15514 and its own open purchase orders come to 860, so every unit on
    order is already owed to demand the group carries. Linking a raised row to one of those
    lines says a quantity is covered while the group is 14,654 short of covering what it
    already owes - so the lines are not offered and the row stays raised and buys.

    The POOL line is offered in the same breath, which is what makes this a rule about the
    group rather than a rule about purchase orders: a pool belongs to no ownership group,
    so nobody's backlog has a prior claim on it.

    The asking row sits at the POOL, and that is load-bearing since the captain's 27 August
    ruling: a group holding an ACKNOWLEDGED, UNLINKED row of this product is never in
    deficit, because that row is the demand somebody bought its purchase order for. The IB
    group's backlog here is core sales-order demand, which nobody has acknowledged an
    instruction for, so the deficit stands. The exception has its own test below.
    """
    world.stock("BRW-IB", 5290)
    world.demand("BRW-IB", 27804)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 860, SOON, "3")]
    )
    world.purchase_order("202607-S0039", date(2026, 7, 2), [("BRW", 9, SOON, "1")])

    candidates = world.svc._candidates_for_row(world.row("ORDER", 60, location="BRW"))

    assert [c["location"] for c in candidates] == ["BRW"]
    assert [c["document"] for c in candidates] == ["202607-S0039"]


def test_the_same_group_offers_its_lines_again_once_the_orders_cover_its_backlog(world):
    """The control for AC-L11: it is the DEFICIT that withholds the line, not the location.

    Same book, same row, same purchase order - the group's backlog is the only thing that
    moves, and the BRW-IB line comes back the moment what is on order covers it.
    """
    world.stock("BRW-IB", 5290)
    world.demand("BRW-IB", 5000)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 860, SOON, "3")]
    )

    candidates = world.svc._candidates_for_row(world.row("ORDER", 60))

    assert [c["location"] for c in candidates] == ["BRW-IB"]


def test_a_deficit_group_line_is_withheld_from_the_auto_link_cascade_too(world):
    """The dialog and the cascade walk ONE list, so the cascade cannot place what the
    dialog refuses to offer.

    The row is at the POOL, so the IB group holds no acknowledged instruction of its own -
    see the ruling in the test above.
    """
    world.stock("BRW-IB", 10)
    world.demand("BRW-IB", 9000)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )
    row = world.row("ORDER", 60, location="BRW")

    world.svc.auto_place_for_products(
        [world.product], actor_user_id=None, trigger="test"
    )

    world.db.refresh(row)
    assert row.state == "raised"


def test_has_link_candidate_applies_the_same_deficit_rule(world):
    """AC-L11 on the LISTING: the row action must not offer a Link the dialog would then
    show as empty, which is what a flag computed by a different rule would do."""
    world.stock("BRW-IB", 10)
    world.demand("BRW-IB", 9000)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )

    withheld = world.svc.link_candidate_products([world.product])
    assert world.product not in withheld["po"]

    # One pool line, and the same product answers yes again. No cache to clear: the netting
    # is over stock and the book, and a new purchase order moves neither.
    world.purchase_order("202607-S0039", date(2026, 7, 2), [("BRW", 9, SOON, "1")])
    offered = world.svc.link_candidate_products([world.product])
    assert world.product in offered["po"]


def test_the_listing_flag_offers_a_group_bought_to_exactly_its_backlog(world):
    """The listing flag and the walk are one rule, at the BOUNDARY too (captain, 27 Aug:
    "a group is offered when net + remaining >= 0").

    `_groups_in_deficit` has offered zero since that ruling, because a purchase order
    raised off the plan lands the group on exactly zero; the flag still asked for strictly
    more than zero, so the row action hid a Link the dialog would have filled.
    """
    world.stock("BRW-IB", 0)
    world.demand("BRW-IB", 500)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )
    row = world.row("ORDER", 60, location="BRW")

    assert [c["location"] for c in world.svc._candidates_for_row(row)] == ["BRW-IB"]
    assert world.product in world.svc.link_candidate_products([world.product])["po"]


def test_the_listing_flag_follows_the_acknowledged_row_exemption(world):
    """The other half of the same ruling on the same flag. The group is genuinely short,
    and it holds the acknowledged row the purchase order was bought for - so the walk
    offers that row its line, and a flag that said "nowhere to link" would hide the action
    on the one row the exemption exists for.

    Answered per PRODUCT, which is all this flag has: it cannot tell the exempt group's
    own row from a neighbour's, so it errs towards offering. The dialog is exact.
    """
    world.stock("BRW-IB", 10)
    world.demand("BRW-IB", 9000)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )
    world.row("ORDER", 60, location="BRW-IB")

    assert world.product in world.svc.link_candidate_products([world.product])["po"]


def test_the_listing_flag_stops_offering_once_the_exempt_row_is_linked(world):
    """The memo is cleared when a link is written (item 4, re-review 27 Aug 2026).

    `_rows_awaiting_a_link` is remembered per product on the SERVICE INSTANCE, and the
    route that writes a link serialises the answer through that same instance - so the
    listing read the awaiting rows as they were BEFORE the write and went on offering a
    Link for a row it had just fully covered. The exemption is the only thing offering this
    group's line (it is 8,990 short), so once the row that earned it is linked, the flag
    must stop.
    """
    world.stock("BRW-IB", 10)
    world.demand("BRW-IB", 9000)
    (line_id,) = world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )
    row = world.row("ORDER", 60, location="BRW-IB")

    assert world.product in world.svc.link_candidate_products([world.product])["po"]

    world.svc.place_on_po(str(row.id), line_id, actor_user_id=None)

    assert world.product not in world.svc.link_candidate_products([world.product])["po"], (
        "the listing offered a Link for a row this very instance had just fully linked"
    )


def test_a_person_may_still_link_a_deficit_group_line_by_hand(world):
    """The deficit rule governs what is OFFERED, not what a person may insist on.

    `manual` already lets a hand-made link reach a purchase order that is not yet active,
    for the same reason: this dialog is override and audit rather than the workflow. A
    person who types the line has looked at it, and refusing them would be a narrowing
    nobody asked for.
    """
    world.stock("BRW-IB", 10)
    world.demand("BRW-IB", 9000)
    (line_id,) = world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )
    row = world.row("ORDER", 60, location="BRW")

    assert world.svc._candidates_for_row(row) == []
    by_hand = world.svc._candidates_for_row(row, manual=True)

    assert [c["target_id"] for c in by_hand] == [line_id]


def test_a_group_bought_to_exactly_its_backlog_still_offers_its_line(world):
    """The BOUNDARY, and the ordinary case (captain, 27 Aug 2026).

    A purchase order raised off the plan buys exactly what the plan said the group was
    short, so the group lands on `group_net + remaining == 0` every time. Read as "at or
    below zero is deficit", that group was refused its own purchase order and the rows that
    sized it stayed raised forever. Nothing is promised twice at zero: the demand the buy
    covers is the demand the group carries.
    """
    world.stock("BRW-IB", 0)
    world.demand("BRW-IB", 500)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )

    candidates = world.svc._candidates_for_row(world.row("ORDER", 60, location="BRW"))

    assert [c["location"] for c in candidates] == ["BRW-IB"]


def test_a_group_holding_an_acknowledged_unlinked_row_is_offered_its_own_line(world):
    """The second half of the same ruling. The group is genuinely short - it owes far more
    than its open purchase orders can cover - but it holds an ACKNOWLEDGED row nobody has
    linked, and that row is the demand somebody bought this purchase order for. Withholding
    the line leaves the buy sitting open beside the instruction it answers.
    """
    world.stock("BRW-IB", 10)
    world.demand("BRW-IB", 9000)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )

    candidates = world.svc._candidates_for_row(world.row("ORDER", 60, location="BRW-IB"))

    assert [c["location"] for c in candidates] == ["BRW-IB"]


def test_the_deficit_exemption_belongs_to_the_row_that_earned_it(world):
    """B1 (code review, 27 Aug 2026). The exemption is per ROW, never per PRODUCT.

    The IB group owes 9,000 against 500 on order, so it is deep in deficit, and it holds
    ONE acknowledged unlinked row of its own - the demand somebody bought that 500 for.
    Lifting the whole product out of the deficit set instead let the row walked FIRST take
    the line whatever group it sat at: a BB row here, whose own group never bought
    anything, walks ahead of the IB row on the earlier delivery date and helped itself to
    IB's purchase order while IB's 8,500-short backlog stayed unpromised.

    The candidates are only RANKED by location, never filtered by it, so nothing further
    down the walk would have stopped it.

    SLICE H, 8 Sep 2026: the exemption still decides what is OFFERED - only the owner's
    candidate list carries the BRW-IB line - but a BRW-IB line is never `cascadable` any
    more (AC-H1), owning row or not, so the automatic pass takes neither row's line and
    BOTH stay raised. The exemption's effect is visible in the offered list above, and
    would still matter to a MANUAL link (AC-H7) - it no longer changes what the automatic
    pass writes.
    """
    world.stock("BRW-IB", 10)
    world.demand("BRW-IB", 9000)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )
    stranger = world.row("ORDER", 10, location="BRW-BB")
    owner = world.row("ORDER", 10, location="BRW-IB")
    # Walked first, which is the whole point: the earlier date wins the scarce line.
    stranger.delivery_date = date(2026, 9, 1)
    owner.delivery_date = date(2026, 11, 1)
    world.db.flush()

    assert world.svc._candidates_for_row(stranger) == [], (
        "the BB row took a purchase order the IB group is 8,500 short without"
    )
    assert [c["location"] for c in world.svc._candidates_for_row(owner)] == ["BRW-IB"]

    world.svc.auto_place_for_products(
        [world.product], actor_user_id=None, trigger="test"
    )
    world.db.refresh(stranger)
    world.db.refresh(owner)

    # AC-H1: a project-location line - BRW-IB - is never taken by the automatic pass,
    # whatever the deficit exemption offers it. Both rows stay raised.
    assert stranger.state == "raised"
    assert owner.state == "raised"


def test_a_deficit_group_offers_its_line_to_its_own_acknowledged_row(world):
    """S4: AC-L11's ORIGINAL scenario - the asking row at BRW-IB - with the outcome the
    27 August ruling gives it.

    `B2155-NL-BLUE` measured 26 August 2026: the IB group nets -22,514 and its own open
    purchase orders come to 860. Before the ruling the group was refused its own lines
    outright, whoever was asking. Now the row doing the asking IS the group's own
    acknowledged, unlinked instruction, so the 860 is what somebody bought for it and the
    line is offered - ahead of the pool line, on the same-location tier.
    """
    world.stock("BRW-IB", 5290)
    world.demand("BRW-IB", 27804)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 860, SOON, "3")]
    )
    world.purchase_order("202607-S0039", date(2026, 7, 2), [("BRW", 9, SOON, "1")])

    candidates = world.svc._candidates_for_row(world.row("ORDER", 60, location="BRW-IB"))

    assert [c["location"] for c in candidates] == ["BRW-IB", "BRW"]


def test_the_cascade_never_takes_a_deficit_groups_own_project_location_line(world):
    """S4 used to be the cascade half of the 27 August ruling: the dialog offering the
    group's own row a line was, back then, a line the cascade placed too, because the
    dialog and the cascade walked one list with one meaning of `cascadable`.

    SLICE H, 8 Sep 2026, reverses that half: `_candidates_for_row` still OFFERS the
    group's own line (asserted on the same book, `test_a_deficit_group_offers_its_line_
    to_its_own_acknowledged_row`), but BRW-IB is a project location, never a pool, and the
    automatic pass may only take from the pool (AC-H1). The row's own exemption from the
    deficit rule no longer buys it an automatic link - only a MANUAL one, which is what
    AC-H7 keeps open in the Link dialog.
    """
    world.stock("BRW-IB", 10)
    world.demand("BRW-IB", 9000)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )
    row = world.row("ORDER", 60, location="BRW-IB")

    world.svc.auto_place_for_products(
        [world.product], actor_user_id=None, trigger="test"
    )

    world.db.refresh(row)
    assert row.state == "raised", "AC-H1: a project-location line is never auto-taken"


def test_the_groups_own_row_needs_no_override_to_reach_its_line(world):
    """S4, the override half. A person may still take a deficit group's line by hand
    (`manual`, the test above), but the group's OWN acknowledged row never has to: the
    automatic walk offers it the identical list.
    """
    world.stock("BRW-IB", 10)
    world.demand("BRW-IB", 9000)
    (line_id,) = world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )
    row = world.row("ORDER", 60, location="BRW-IB")

    automatic = world.svc._candidates_for_row(row)
    by_hand = world.svc._candidates_for_row(row, manual=True)

    assert [c["target_id"] for c in automatic] == [line_id]
    assert [c["target_id"] for c in by_hand] == [line_id]


def test_an_awaiting_row_at_the_group_does_not_lift_the_deficit(world):
    """The exception is ACKNOWLEDGED demand, not demand. An awaiting row is an instruction
    purchasing has not read, so it is no evidence that anybody bought this purchase order
    for it - and the cascade would refuse to link it anyway.
    """
    world.stock("BRW-IB", 10)
    world.demand("BRW-IB", 9000)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )
    world.row("ORDER", 60, location="BRW-IB", ack_state="awaiting")

    candidates = world.svc._candidates_for_row(world.row("ORDER", 60, location="BRW"))

    assert candidates == []


def test_the_cascade_builds_the_availability_reader_once_for_the_whole_pass(world, monkeypatch):
    """Ladder v4: `netting_for_products` is three queries over the products it is given,
    and `_netting` rebuilds whenever it meets one it has not seen.

    A pass that met them one row at a time would therefore rebuild per product, over a
    growing list - three queries, then six, then nine - on a call that names thousands of
    rows. `auto_place_for_products` primes it once from the whole row set instead, and this
    counts the builds across a pass spanning TWO products, where an unprimed loop would
    have built twice.
    """
    import app.services.project_order_inquiry_service as module

    second = world.another_product(f"{MARKER}-8605")
    world.stock("BRW-IB", 10)
    world.demand("BRW-IB", 9000)
    world.purchase_order(
        "202607-S0067", date(2026, 7, 1), [("BRW-IB", 500, SOON, "3")]
    )
    world.row("ORDER", 60)
    world.row("ORDER", 20, item_code=f"{MARKER}-8605", so_line=False)

    builds = []
    real = module.netting_for_products

    def counted(db, product_ids):
        builds.append(sorted(str(p) for p in product_ids))
        return real(db, product_ids)

    monkeypatch.setattr(module, "netting_for_products", counted)
    world.svc.auto_place_for_products(
        [world.product, second], actor_user_id=None, trigger="test"
    )

    assert len(builds) == 1, f"one build for the whole pass, not {len(builds)}"
    assert builds[0] == sorted([str(world.product), str(second)])
