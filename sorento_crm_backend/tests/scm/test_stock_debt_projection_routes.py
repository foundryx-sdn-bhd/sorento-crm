"""STOCK-DEBT-PROJECTION (owner, 12 Oct 2026): `projected=true` over the wire.

The SRTWT2215 shape, scaled down: a project bin (`-BB`) owed 100 this month, 200 in two
months and 300 in three; 20 on hand at the site pool; an unlinked PO of 1,000 at an HQ-like
dealer warehouse, delivery four days ago. The pool seal keeps both off the BB lines, so
today's months read -80, 0, -200, -300 when HQ is outside the span.

The rule (`stock_debt_projection.project_months`, owner's "pool a"): pool supply covers the
pool's own (dealer) orders of its month first, then project shortfalls, earliest first, and
only a surplus carries. It must give the SAME answer whether the HQ PO is inside the span or
outside it: 920, 920, 720, 420. A double count would read 1,920.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.services.scm.supply_assignment import month_key
from tests.scm.conftest import SORENTO_COMPANY_ID, requires_pg
from tests.scm.test_stock_debt_routes import (
    BASE,
    TODAY,
    _client,
    _demand,
    _months_ahead,
    _order_back_link_on_po,
    _product,
    _project_line_for,
    _stock,
    _u,
)

pytestmark = requires_pg


def _warehouse(db, code, *, planning, segment, pool=None, is_active=True):
    from app.models.inventory import Warehouse

    row = Warehouse(
        id=_u(), warehouse_code=code, warehouse_name=code, location="ZZT",
        is_active=is_active, fulfilment_planning=planning, segment=segment,
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(row)
    db.flush()
    if pool is not None:
        row.pool_warehouse_id = pool.id
        db.flush()
    return row


def _po_line(db, product, warehouse, *, qty, delivery, number):
    from app.models.procurement import PurchaseOrder, PurchaseOrderLine

    po = PurchaseOrder(
        id=_u(), po_number=number, issue_date=delivery - timedelta(days=30),
        status="active", company_id=SORENTO_COMPANY_ID,
    )
    db.add(po)
    db.flush()
    line = PurchaseOrderLine(
        id=_u(), purchase_order_id=po.id, product_id=product.id,
        warehouse_id=warehouse.id, qty_ordered=Decimal(str(qty)),
        qty_received=Decimal("0"), line_status="open", expected_date=delivery,
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(line)
    db.flush()
    return line


def _seed(db, *, hq_in_span: bool, hq_demand: float = 0):
    marker = f"ZZTPJ{_u()[:6]}".upper()
    pool = _warehouse(db, f"{marker}BRW", planning=False, segment="dealer")
    bin_ = _warehouse(db, f"{marker}BRW-BB", planning=True, segment="project", pool=pool)
    hq = _warehouse(db, f"{marker}HQ", planning=False, segment="dealer")
    if hq_in_span:
        # The dev book's shape: a dealer warehouse is its own pool.
        hq.pool_warehouse_id = hq.id
        db.flush()
    product = _product(db, f"{marker}-SRTWT2215")
    _stock(db, product, pool, 20)
    _demand(db, product, bin_, qty=100, required_date=TODAY, so_number=f"{marker}-SO1")
    _demand(db, product, bin_, qty=200, required_date=_months_ahead(2), so_number=f"{marker}-SO2")
    order, line = _demand(
        db, product, bin_, qty=300, required_date=_months_ahead(3), so_number=f"{marker}-SO3"
    )
    hq_line = None
    if hq_demand:
        _hq_order, hq_line = _demand(
            db, product, hq, qty=hq_demand, required_date=TODAY,
            so_number=f"{marker}-SO-HQ",
        )
    po_line = _po_line(
        db, product, hq, qty=1000, delivery=TODAY - timedelta(days=4),
        number=f"{marker}-202610-S0042",
    )
    return {
        "product": product, "marker": marker, "po_line": po_line, "order": order,
        "line": line, "hq": hq, "hq_line": hq_line, "pool": pool,
    }


def _row(client, seed, **params):
    body = client.get(
        BASE, params={"query": seed["marker"], "only_debt": "false", **params}
    ).json()
    rows = [row for row in body["data"] if row["product_id"] == str(seed["product"].id)]
    assert len(rows) == 1, body
    return rows[0], body


def _balances(row, count=4):
    by_key = {month["key"]: month["balance"] for month in row["months"]}
    return [by_key.get(month_key(_months_ahead(n)), 0.0) for n in range(count)]


def test_hq_outside_the_span_is_added_and_carried(scm_app):
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=False)
    with TestClient(app) as c:
        today, _ = _row(c, seed)
        projected, body = _row(c, seed, projected="true")
    assert _balances(today) == [-80.0, 0.0, -200.0, -300.0]
    assert _balances(projected) == [920.0, 920.0, 720.0, 420.0]
    # Total is conserved arithmetic: today's total plus the pool supply added.
    assert projected["total"] == today["total"] + 1000.0
    assert body["totals"]["months"][month_key(_months_ahead(3))] == 420.0


def test_hq_inside_the_span_is_not_counted_twice(scm_app):
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True)
    with TestClient(app) as c:
        today, _ = _row(c, seed)
        projected, _ = _row(c, seed, projected="true")
    # The page already credits the 1,000 (and the 20) to this month.
    assert _balances(today) == [920.0, 0.0, -200.0, -300.0]
    assert _balances(projected) == [920.0, 920.0, 720.0, 420.0]
    assert projected["total"] == today["total"]


def test_switch_off_is_unchanged_and_only_debt_reads_the_projection(scm_app):
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=False)
    with TestClient(app) as c:
        plain = c.get(BASE, params={"query": seed["marker"]}).json()
        projected = c.get(BASE, params={"query": seed["marker"], "projected": "true"}).json()
    ids = lambda body: [row["product_id"] for row in body["data"]]  # noqa: E731
    assert str(seed["product"].id) in ids(plain)
    # Never below 0 once projected: not in debt in that view.
    assert str(seed["product"].id) not in ids(projected)


def test_tones_follow_the_projected_balance(scm_app):
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=False)
    with TestClient(app) as c:
        projected, _ = _row(c, seed, projected="true")
    assert all(month["tone"] != "red" for month in projected["months"])


def test_a_placement_pins_but_the_last_month_lands_the_same(scm_app):
    """An OI placement of 100 of the HQ PO on the 300 line (HQ in span) moves where the
    month figures show it, and the projection carries the rest; the final month is the
    same 420 either way."""
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True)
    project_order, project_line = _project_line_for(db, seed["line"])
    _order_back_link_on_po(db, project_order, project_line, po_line=seed["po_line"], qty=100)
    with TestClient(app) as c:
        projected, _ = _row(c, seed, projected="true")
    assert _balances(projected) == [820.0, 820.0, 620.0, 420.0]


def test_drill_states_the_projection_and_lists_the_added_pool_document(scm_app):
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=False)
    month = month_key(_months_ahead(2))
    current = month_key(TODAY)
    with TestClient(app) as c:
        cell = c.get(
            f"{BASE}/{seed['product'].id}/cell",
            params={"month": month, "projected": "true"},
        ).json()
        now = c.get(
            f"{BASE}/{seed['product'].id}/cell",
            params={"month": current, "projected": "true"},
        ).json()
        plain = c.get(
            f"{BASE}/{seed['product'].id}/cell", params={"month": current}
        ).json()
    assert cell["projection"] == {
        "carried_in": 920.0, "month_balance": -200.0, "pool_supply": 0.0,
        "pool_sources": [], "dealer_demand": 0.0, "projected": 720.0,
        "carried_out": 720.0,
    }
    assert now["projection"]["pool_supply"] == 1020.0
    assert now["projection"]["pool_sources"] == [
        {"label": f"{seed['marker']}BRW on hand", "qty": 20.0},
        {"label": f"{seed['marker']}HQ PO", "qty": 1000.0},
    ]
    refs = [row["po_number"] for row in now["supply"]]
    assert f"{seed['marker']}-202610-S0042" in refs
    assert plain.get("projection") is None
    assert f"{seed['marker']}-202610-S0042" not in [row["po_number"] for row in plain["supply"]]


def test_an_outside_pool_nets_its_own_orders_like_an_inside_one(scm_app):
    """Review B1: a 500 order at HQ, due today. HQ's own order is netted whether the span
    holds HQ or not, so both read the same: 420, 420, 220, -80."""
    app, db = _client(scm_app)
    inside = _seed(db, hq_in_span=True, hq_demand=500)
    outside = _seed(db, hq_in_span=False, hq_demand=500)
    with TestClient(app) as c:
        projected_in, _ = _row(c, inside, projected="true")
        projected_out, _ = _row(c, outside, projected="true")
    assert _balances(projected_in) == [420.0, 420.0, 220.0, -80.0]
    assert _balances(projected_out) == [420.0, 420.0, 220.0, -80.0]


def test_a_placement_on_an_outside_pool_po_is_not_spent_twice(scm_app):
    """Review B2: the page's walk honours a placement on the out-of-span PO (AC-S2-1b);
    the projection takes that 100 off the PO's spare before it carries."""
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=False)
    project_order, project_line = _project_line_for(db, seed["line"])
    _order_back_link_on_po(db, project_order, project_line, po_line=seed["po_line"], qty=100)
    with TestClient(app) as c:
        projected, _ = _row(c, seed, projected="true")
    assert _balances(projected) == [820.0, 820.0, 620.0, 420.0]


def test_project_bin_spare_does_not_carry(scm_app):
    """Review B3: 50 spare at a BB bin, a 30 order at an IB bin two months out. The seal
    keeps BB off IB, and the projection does not carry project spare either."""
    app, db = _client(scm_app)
    marker = f"ZZTPJ{_u()[:6]}".upper()
    pool = _warehouse(db, f"{marker}BRW", planning=False, segment="dealer")
    bb = _warehouse(db, f"{marker}BRW-BB", planning=True, segment="project", pool=pool)
    ib = _warehouse(db, f"{marker}BRW-IB", planning=True, segment="project", pool=pool)
    product = _product(db, f"{marker}-SPARE")
    _stock(db, product, bb, 50)
    _demand(db, product, ib, qty=30, required_date=_months_ahead(2), so_number=f"{marker}-SO")
    seed = {"product": product, "marker": marker}
    with TestClient(app) as c:
        today, _ = _row(c, seed)
        projected, _ = _row(c, seed, projected="true")
    assert _balances(today, 3) == [50.0, 0.0, -30.0]
    assert _balances(projected, 3) == [50.0, 0.0, -30.0]


def test_date_from_does_not_hand_earlier_orders_pool_stock(scm_app):
    """Review should-fix 1: with the board starting two months out, the 100 due today
    still takes its share before the pool stock carries, so the first column reads 720,
    as it does unfiltered."""
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=False)
    start = _months_ahead(2).replace(day=1)
    with TestClient(app) as c:
        projected, body = _row(c, seed, projected="true", date_from=start.isoformat())
    assert body["months"][0] == month_key(start)
    by_key = {month["key"]: month["balance"] for month in projected["months"]}
    assert by_key[month_key(start)] == 720.0


def _hq_placement_on_a_pool_po(db, seed):
    """A 300 PO at the in-span pool, placed (order inquiry) on the HQ order."""
    pool_po = _po_line(
        db, seed["product"], seed["pool"], qty=300, delivery=TODAY - timedelta(days=4),
        number=f"{seed['marker']}-POOLPO",
    )
    project_order, project_line = _project_line_for(db, seed["hq_line"])
    _order_back_link_on_po(db, project_order, project_line, po_line=pool_po, qty=300)


def test_an_outside_order_placed_on_inside_supply_is_not_spent_twice(scm_app):
    """Review R1: the HQ order's placement takes the pool PO in the outside walk; the
    page's own month must not still read that PO spare."""
    app, db = _client(scm_app)
    inside = _seed(db, hq_in_span=True, hq_demand=500)
    outside = _seed(db, hq_in_span=False, hq_demand=500)
    _hq_placement_on_a_pool_po(db, inside)
    _hq_placement_on_a_pool_po(db, outside)
    with TestClient(app) as c:
        projected_in, _ = _row(c, inside, projected="true")
        projected_out, _ = _row(c, outside, projected="true")
    assert _balances(projected_in) == [720.0, 720.0, 520.0, 220.0]
    assert _balances(projected_out) == _balances(projected_in)


def test_an_outside_pool_short_month_reads_short(scm_app):
    """An HQ order of 1,200 next month: the pool surplus carried into November (920) meets
    it first ("pool a"), so November reads -280, whether the span holds HQ or not."""
    app, db = _client(scm_app)
    rows = []
    for in_span in (True, False):
        seed = _seed(db, hq_in_span=in_span)
        _demand(
            db, seed["product"], seed["hq"], qty=1200, required_date=_months_ahead(1),
            so_number=f"{seed['marker']}-SO-HQ-LATER",
        )
        rows.append(seed)
    with TestClient(app) as c:
        inside, _ = _row(c, rows[0], projected="true")
        outside, _ = _row(c, rows[1], projected="true")
    assert _balances(inside) == [920.0, -280.0, -200.0, -300.0]
    assert _balances(outside) == [920.0, -280.0, -200.0, -300.0]


def test_a_planning_bin_is_never_read_as_pool(scm_app):
    """A bin flagged for planning whose segment says dealer is a project location: under
    `group=BB` it is out of the span and must not be added as pool supply."""
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True)
    other = _warehouse(
        db, f"{seed['marker']}BRW-IB", planning=True, segment="dealer", pool=seed["pool"]
    )
    _stock(db, seed["product"], other, 500)
    with TestClient(app) as c:
        projected, _ = _row(c, seed, projected="true", group="BB")
    assert _balances(projected) == [920.0, 920.0, 720.0, 420.0]


# --------------------------------------------------------------------------- prod shape

PROD_PROJECT_MONTHS = [-38.0, -17.0, -1436.0, -1613.0, -480.0, -797.0, -368.0]


def _prod_srtwt2215(db):
    """The owner's prod row (12 Oct 2026), HQ made its own pool: BRW on hand 191, PO
    202610-S0042 5,000 at HQ delivered 8 Oct (past, so it lands today), and BRW-BB orders
    that read -38, -17, -1,436, -1,613, -480, -797, -368 under Book=Project. A 15 dealer
    order at BRW this month makes Book=All read +5,138 in October, as prod does."""
    marker = f"ZZTPJ{_u()[:6]}".upper()
    pool = _warehouse(db, f"{marker}BRW", planning=False, segment="dealer")
    pool.pool_warehouse_id = pool.id
    bin_ = _warehouse(db, f"{marker}BRW-BB", planning=True, segment="project", pool=pool)
    hq = _warehouse(db, f"{marker}HQ", planning=False, segment="dealer")
    hq.pool_warehouse_id = hq.id
    db.flush()
    product = _product(db, f"{marker}-SRTWT2215")
    _stock(db, product, pool, 191)
    _demand(db, product, pool, qty=15, required_date=TODAY, so_number=f"{marker}-SO-DLR")
    for n, qty in enumerate(PROD_PROJECT_MONTHS):
        _demand(
            db, product, bin_, qty=-qty, required_date=TODAY if n == 0 else _months_ahead(n),
            so_number=f"{marker}-SO{n}",
        )
    _po_line(
        db, product, hq, qty=5000, delivery=TODAY - timedelta(days=4),
        number=f"{marker}-202610-S0042",
    )
    return {"product": product, "marker": marker}


def test_prod_srtwt2215_book_project_carries_the_pool_po(scm_app):
    """Owner, 12 Oct 2026: under Book=Project the projection starts from the PROJECT
    book's months and carries in pool supply (BRW on hand, the HQ PO), although that
    book's walk reads no pool bin. Pool spare is 191 + 5,000 less the 15 dealer order =
    5,176, so: Oct -38 + 5,176 = 5,138, then 5,121, 3,685, 2,072, 1,592, 795, 427.
    Book=All reads the same projection from its own months (Oct +5,138)."""
    app, db = _client(scm_app)
    seed = _prod_srtwt2215(db)
    expected = [5138.0, 5121.0, 3685.0, 2072.0, 1592.0, 795.0, 427.0]
    with TestClient(app) as c:
        project, _ = _row(c, seed, book="project")
        project_projected, _ = _row(c, seed, book="project", projected="true")
        every, _ = _row(c, seed)
        every_projected, _ = _row(c, seed, projected="true")
    assert _balances(project, 7) == PROD_PROJECT_MONTHS
    assert _balances(every, 7)[0] == 5138.0
    assert _balances(project_projected, 7) == expected
    assert _balances(every_projected, 7) == expected


def test_pool_a_dealer_orders_take_their_month_first(scm_app):
    """Owner, "pool a": a 300 dealer order at BRW this month takes 300 of the 1,020 pool
    supply before the project's 100 short; 620 carries. Same inside the span or out."""
    app, db = _client(scm_app)
    rows = []
    for in_span in (True, False):
        seed = _seed(db, hq_in_span=in_span)
        _demand(
            db, seed["product"], seed["pool"], qty=300, required_date=TODAY,
            so_number=f"{seed['marker']}-SO-DEALER",
        )
        rows.append(seed)
    with TestClient(app) as c:
        inside, _ = _row(c, rows[0], projected="true", book="project")
        outside, _ = _row(c, rows[1], projected="true")
        every, _ = _row(c, rows[0], projected="true")
    expected = [620.0, 620.0, 420.0, 120.0]
    assert _balances(inside) == expected
    assert _balances(outside) == expected
    assert _balances(every) == expected


def test_c_fhss12_pool_supply_applied_to_the_earliest_shortfall(scm_app):
    """Owner's hand test, Book=Project: BRW on hand 57 and a BRW PO of 15 meet October's
    377 short; -305, and later months are unchanged (no surplus, debt does not carry).
    The drill names the pool supply by location and kind."""
    app, db = _client(scm_app)
    marker = f"ZZTPJ{_u()[:6]}".upper()
    pool = _warehouse(db, f"{marker}BRW", planning=False, segment="dealer")
    pool.pool_warehouse_id = pool.id
    bin_ = _warehouse(db, f"{marker}BRW-BB", planning=True, segment="project", pool=pool)
    db.flush()
    product = _product(db, f"{marker}-C-FHSS12")
    _stock(db, product, pool, 57)
    _po_line(
        db, product, pool, qty=15, delivery=TODAY - timedelta(days=2),
        number=f"{marker}-PO15",
    )
    for n, qty in enumerate([377, 20, 5]):
        _demand(
            db, product, bin_, qty=qty, required_date=TODAY if n == 0 else _months_ahead(n),
            so_number=f"{marker}-SO{n}",
        )
    seed = {"product": product, "marker": marker}
    with TestClient(app) as c:
        today, _ = _row(c, seed, book="project")
        projected, _ = _row(c, seed, book="project", projected="true")
        cell = c.get(
            f"{BASE}/{product.id}/cell",
            params={"month": month_key(TODAY), "projected": "true", "book": "project"},
        ).json()
    assert _balances(today, 3) == [-377.0, -20.0, -5.0]
    assert _balances(projected, 3) == [-305.0, -20.0, -5.0]
    assert cell["projection"]["pool_supply"] == 72.0
    assert cell["projection"]["pool_sources"] == [
        {"label": f"{marker}BRW on hand", "qty": 57.0},
        {"label": f"{marker}BRW PO", "qty": 15.0},
    ]
    assert cell["projection"]["carried_out"] == 0.0
