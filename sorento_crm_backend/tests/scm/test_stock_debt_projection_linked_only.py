"""STOCK-DEBT-PROJECTION, rule D (owner, 12 Oct 2026, 22:20).

In the project projection, pool supply (BRW and every other pool) counts for a project
ONLY when it is linked to that project line: the S/O ref on the PO/SPO line, an OI
placement, or a confirmed planning decision. Incoming or landed alike: a linked SPO that
lands becomes an on-hand hold and keeps covering, as Covered by does today. Unlinked pool
on hand and unlinked incoming POs/SPOs never count. The projection agrees with Covered by
line for line.

Crew answer (b), same day, following the owner's HQ decision (HQ-PROJECT-RESERVE, #1597): a
PROJECT RESERVE (a warehouse some location names as its pool, with `segment = project`) is
the one exception. Its UNLINKED incoming documents (PO on its Delivery date, SPO on its ETA)
are carried, display only, to the earliest project shortfall, labelled "<code> reserve PO".
Reserve on hand is #1597's walk, not this layer.

The shape is SRTWT2215's BRW SPO 333: a BRW-BB line owes 500 in two months, and SPO 333
at BRW arrives next month. Received or not, linked or not, the projection must read the
same before and after the goods are received (no jump on receipt).
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.scm.supply_assignment import month_key
from tests.scm.conftest import requires_pg
from tests.scm.test_stock_debt_projection_routes import (
    PROD_PROJECT_MONTHS,
    _balances,
    _prod_srtwt2215,
    _row,
    _seed,
    _warehouse,
)
from tests.scm.test_stock_debt_routes import (
    BASE,
    TODAY,
    _client,
    _demand,
    _months_ahead,
    _order_back_link_on_spo,
    _product,
    _project_line_for,
    _spo,
    _stock,
    _u,
)

pytestmark = requires_pg

BOOKS = ("all", "project")


def _spo_333(db, *, linked: bool, received: bool, placed: float = 333):
    """BRW (own pool), BRW-BB owing 500 in two months, SPO 333 at BRW due next month.
    `received`: the SPO is fully received and its 333 sit on BRW's floor. `linked`: an OI
    placement of `placed` of the 333 on the BB line."""
    marker = f"ZZTPD{_u()[:6]}".upper()
    pool = _warehouse(db, f"{marker}BRW", planning=False, segment="dealer")
    pool.pool_warehouse_id = pool.id
    bin_ = _warehouse(db, f"{marker}BRW-BB", planning=True, segment="project", pool=pool)
    db.flush()
    product = _product(db, f"{marker}-SRTWT2215")
    _order, line = _demand(
        db, product, bin_, qty=500, required_date=_months_ahead(2),
        so_number=f"{marker}-SO1",
    )
    allocation = _spo(
        db, product, pool, qty=333, arrives=_months_ahead(1),
        spo_number=f"{marker}-SPO333",
        received=333 if received else 0,
        receipt_status="fully_received" if received else "pending",
    )
    if received:
        _stock(db, product, pool, 333)
    if linked:
        project_order, project_line = _project_line_for(db, line)
        _order_back_link_on_spo(
            db, project_order, project_line, allocation=allocation, qty=placed
        )
    db.flush()
    return {"product": product, "marker": marker}


def _projected(app, seed, book):
    with TestClient(app) as c:
        row, _ = _row(c, seed, projected="true", book=book)
    return _balances(row)


def test_unlinked_spo_333_never_counts_before_or_after_receipt(scm_app):
    """No jump on receipt, and no credit either side: incoming it is unlinked pool supply,
    received it is unlinked pool on hand. The BB line's 500 stays owed."""
    app, db = _client(scm_app)
    incoming = _spo_333(db, linked=False, received=False)
    landed = _spo_333(db, linked=False, received=True)
    for book in BOOKS:
        assert _projected(app, incoming, book) == [0.0, 0.0, -500.0, 0.0], book
        assert _projected(app, landed, book) == [0.0, 0.0, -500.0, 0.0], book


def test_linked_spo_333_covers_the_same_before_and_after_receipt(scm_app):
    """The placement covers 333 of the 500 while the SPO is incoming; once received it is
    an on-hand hold at BRW (SPO-RECEIVED-PIN) and still covers 333. Both books."""
    app, db = _client(scm_app)
    incoming = _spo_333(db, linked=True, received=False)
    landed = _spo_333(db, linked=True, received=True)
    for book in BOOKS:
        assert _projected(app, incoming, book) == [0.0, 0.0, -167.0, 0.0], book
        assert _projected(app, landed, book) == [0.0, 0.0, -167.0, 0.0], book


def test_a_partly_placed_spo_counts_only_the_placed_part_either_side_of_receipt(scm_app):
    """The dev book's own shape (SPO-2026/09-0036, 333 at BRW): one order inquiry placed 36
    of it on a BRW-BB line. Only the 36 counts, incoming or landed; the other 297 are
    unlinked pool supply."""
    app, db = _client(scm_app)
    incoming = _spo_333(db, linked=True, received=False, placed=36)
    landed = _spo_333(db, linked=True, received=True, placed=36)
    for book in BOOKS:
        assert _projected(app, incoming, book) == [0.0, 0.0, -464.0, 0.0], book
        assert _projected(app, landed, book) == [0.0, 0.0, -464.0, 0.0], book


def test_the_projection_agrees_with_covered_by_line_for_line(scm_app):
    """The month-2 drill under the switch: the BB line is covered 333 by the SPO before
    receipt and by BRW on hand after it, short 167 both times, and the projected figure
    is exactly what Covered by leaves owed. No pool supply is applied on top."""
    app, db = _client(scm_app)
    month = month_key(_months_ahead(2))
    for received, kind in ((False, "spo"), (True, "on_hand")):
        seed = _spo_333(db, linked=True, received=received)
        for book in BOOKS:
            with TestClient(app) as c:
                cell = c.get(
                    f"{BASE}/{seed['product'].id}/cell",
                    params={"month": month, "projected": "true", "book": book},
                ).json()
            [line] = cell["demand"]
            assert (line["assigned_qty"], line["short_qty"]) == (333, 167), (received, book)
            assert [entry["kind"] for entry in line["assigned_from"]] == [kind]
            projection = cell["projection"]
            assert projection["projected"] == -167.0
            assert projection["reserve_supply"] == 0.0
            assert projection["carried_in"] == 0.0


def test_prod_srtwt2215_unlinked_hq_po_and_brw_on_hand_never_count(scm_app):
    """The owner's prod row: BRW on hand 191 and the 5,000 HQ PO (202610-S0042) carry no
    S/O ref, no placement, no decision. Under rule D the projection is the project months
    as they stand, in both books, and the drill offers no pool document."""
    app, db = _client(scm_app)
    seed = _prod_srtwt2215(db)
    with TestClient(app) as c:
        project, _ = _row(c, seed, projected="true", book="project")
        every, _ = _row(c, seed, projected="true")
        cell = c.get(
            f"{BASE}/{seed['product'].id}/cell",
            params={"month": month_key(TODAY), "projected": "true", "book": "project"},
        ).json()
    assert _balances(project, 7) == PROD_PROJECT_MONTHS
    assert _balances(every, 7) == PROD_PROJECT_MONTHS
    assert project["total"] == sum(PROD_PROJECT_MONTHS)
    assert f"{seed['marker']}-202610-S0042" not in [
        row.get("po_number") for row in cell["supply"]
    ]
    assert cell["projection"]["reserve_supply"] == 0.0


def test_unlinked_pool_po_never_counts_inside_or_outside_the_span(scm_app):
    """The scaled shape: BB owes 100 now, 200 and 300 later; BRW's 20 on hand and the
    unlinked 1,000 HQ PO count nothing, wherever HQ sits."""
    app, db = _client(scm_app)
    outside = _seed(db, hq_in_span=False)
    inside = _seed(db, hq_in_span=True)
    for seed in (outside, inside):
        for book in BOOKS:
            assert _projected(app, seed, book) == [-100.0, 0.0, -200.0, -300.0], book


def test_a_placement_on_the_hq_po_counts_only_what_it_places(scm_app):
    """An OI placement of 100 of the HQ PO on the 300 line: that line is owed 200; nothing
    else of the PO counts."""
    from tests.scm.test_stock_debt_routes import _order_back_link_on_po

    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True)
    project_order, project_line = _project_line_for(db, seed["line"])
    _order_back_link_on_po(db, project_order, project_line, po_line=seed["po_line"], qty=100)
    for book in BOOKS:
        assert _projected(app, seed, book) == [-100.0, 0.0, -200.0, -200.0], book



def _make_reserve(db, marker):
    from app.models.inventory import Warehouse

    hq = db.query(Warehouse).filter(Warehouse.warehouse_code == f"{marker}HQ").one()
    hq.segment = "project"
    db.flush()
    return hq


def test_prod_srtwt2215_with_hq_a_project_reserve_carries_the_5000(scm_app):
    """HQ made a project reserve (its own pool, `segment = project`): the unlinked 5,000 PO
    (delivered 8 Oct, so it lands today) carries to the earliest shortfalls: 4,962, 4,945,
    3,509, 1,896, 1,416, 619, 251, in both books. BRW's 191 still counts nothing. The
    drill names it as reserve supply."""
    app, db = _client(scm_app)
    seed = _prod_srtwt2215(db)
    _make_reserve(db, seed["marker"])
    expected = [4962.0, 4945.0, 3509.0, 1896.0, 1416.0, 619.0, 251.0]
    with TestClient(app) as c:
        project, _ = _row(c, seed, projected="true", book="project")
        every, _ = _row(c, seed, projected="true")
        cell = c.get(
            f"{BASE}/{seed['product'].id}/cell",
            params={"month": month_key(TODAY), "projected": "true", "book": "project"},
        ).json()
    assert _balances(project, 7) == expected
    assert _balances(every, 7) == expected
    assert cell["projection"]["reserve_supply"] == 5000.0
    assert cell["projection"]["reserve_sources"] == [
        {"label": f"{seed['marker']}HQ reserve PO", "qty": 5000.0},
    ]
    assert f"{seed['marker']}-202610-S0042" in [row.get("po_number") for row in cell["supply"]]


def test_a_reserve_spo_counts_from_its_eta(scm_app):
    """An unlinked SPO of 250 at the reserve arriving in two months: the 200 owed then is
    met and 50 carries into the third month's 300."""
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True)
    db.delete(seed["po_line"])
    hq = _make_reserve(db, seed["marker"])
    _spo(db, seed["product"], hq, qty=250, arrives=_months_ahead(2))
    db.flush()
    for book in BOOKS:
        assert _projected(app, seed, book) == [-100.0, 0.0, 50.0, -250.0], book


def test_only_a_pool_with_segment_project_is_a_reserve(scm_app):
    """The predicate, both halves. HQ as its own pool but `segment = dealer`: a pool, not a
    reserve, so its PO counts nothing. A `segment = project` warehouse that no location
    names as its pool: not a reserve either."""
    app, db = _client(scm_app)
    dealer_pool = _seed(db, hq_in_span=True)
    not_a_pool = _seed(db, hq_in_span=False)
    not_a_pool["hq"].segment = "project"
    db.flush()
    for seed in (dealer_pool, not_a_pool):
        for book in BOOKS:
            assert _projected(app, seed, book) == [-100.0, 0.0, -200.0, -300.0], book
