"""STOCK-DEBT-PROJECTION (owner, 12 Oct 2026, option D): `projected=true` over the wire.

The SRTWT2215 shape, scaled down: a project bin (`-BB`) owed 100 this month, 200 in two
months and 300 in three; 20 on hand at the site pool BRW; an unlinked PO of 1,000 at HQ,
delivery four days ago. The pool seal keeps both off the BB lines.

The rule (`stock_debt_projection.project_months`): pool supply counts for a project line
only through a link, which the walk already pins (`test_stock_debt_projection_linked_only`).
The one thing the projection adds is a PROJECT RESERVE's unlinked incoming supply (crew
answer (b); HQ-PROJECT-RESERVE, #1597: a pool with `segment = project`), carried to the
earliest project shortfall. With HQ a reserve: 900, 900, 700, 400 in both books (BRW's 20
never counts). A double count would read 1,900.
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
    _spo,
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


def _seed(db, *, hq_in_span: bool, hq_demand: float = 0, reserve: bool = False):
    """`hq_in_span`: HQ is its own pool, so Book=All reads it. `reserve`: and its segment is
    `project`, which makes it a project reserve (needs `hq_in_span`)."""
    marker = f"ZZTPJ{_u()[:6]}".upper()
    pool = _warehouse(db, f"{marker}BRW", planning=False, segment="dealer")
    bin_ = _warehouse(db, f"{marker}BRW-BB", planning=True, segment="project", pool=pool)
    hq = _warehouse(
        db, f"{marker}HQ", planning=False, segment="project" if reserve else "dealer"
    )
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


BOOKS = ("all", "project")


def _projected(client, seed, book="all", **params):
    row, _ = _row(client, seed, projected="true", book=book, **params)
    return _balances(row), row


def test_a_reserve_po_carries_to_the_earliest_shortfall_in_both_books(scm_app):
    """HQ inside the span under Book=All, outside it under Book=Project: the same 900, 900,
    700, 400, and the row total is the project months plus the 1,000. Switch off, the
    month view is untouched."""
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True)
    with TestClient(app) as c:
        every, _ = _row(c, seed)
        results = {book: _projected(c, seed, book) for book in BOOKS}
    assert _balances(every) == [920.0, 0.0, -200.0, -300.0]
    for book, (balances, row) in results.items():
        assert balances == [900.0, 900.0, 700.0, 400.0], book
        assert row["total"] == 400.0, book


def test_switch_off_is_unchanged_and_only_debt_reads_the_projection(scm_app):
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True)
    with TestClient(app) as c:
        plain = c.get(BASE, params={"query": seed["marker"], "book": "project"}).json()
        projected = c.get(
            BASE, params={"query": seed["marker"], "book": "project", "projected": "true"}
        ).json()
    ids = lambda body: [row["product_id"] for row in body["data"]]  # noqa: E731
    assert str(seed["product"].id) in ids(plain)
    # Never below 0 once projected: not in debt in that view.
    assert str(seed["product"].id) not in ids(projected)


def test_tones_follow_the_projected_balance(scm_app):
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True)
    with TestClient(app) as c:
        _balances_, projected = _projected(c, seed, "project")
    assert all(month["tone"] != "red" for month in projected["months"])


def test_a_placement_on_the_reserve_po_is_not_offered_twice(scm_app):
    """An OI placement of 100 of the reserve PO on the 300 line: the walk pins it, and the
    projection carries only the 900 left."""
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True)
    project_order, project_line = _project_line_for(db, seed["line"])
    _order_back_link_on_po(db, project_order, project_line, po_line=seed["po_line"], qty=100)
    with TestClient(app) as c:
        for book in BOOKS:
            assert _projected(c, seed, book)[0] == [800.0, 800.0, 600.0, 400.0], book


def test_an_order_at_the_reserve_placed_on_its_po_is_netted_in_both_books(scm_app):
    """A 300 order AT HQ holds 300 of the reserve PO through a placement. Under
    Book=Project HQ is outside the page's span; the projection still sees the link, so
    both books carry 700: 600, 600, 400, 100."""
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True, hq_demand=300)
    project_order, project_line = _project_line_for(db, seed["hq_line"])
    _order_back_link_on_po(db, project_order, project_line, po_line=seed["po_line"], qty=300)
    with TestClient(app) as c:
        for book in BOOKS:
            assert _projected(c, seed, book)[0] == [600.0, 600.0, 400.0, 100.0], book


def test_unlinked_orders_at_a_pool_or_the_reserve_change_nothing(scm_app):
    """Pool lines are not project lines: a 15 dealer order at BRW and a 500 unlinked order
    at HQ are not asked of the projection (#1597: reserve supply skips dealer orders)."""
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True, hq_demand=500)
    _demand(
        db, seed["product"], seed["pool"], qty=15, required_date=TODAY,
        so_number=f"{seed['marker']}-SO-DEALER",
    )
    with TestClient(app) as c:
        for book in BOOKS:
            balances, row = _projected(c, seed, book)
            assert balances == [900.0, 900.0, 700.0, 400.0], book
            assert row["total"] == 400.0, book


def test_drill_states_the_projection_and_lists_the_reserve_document(scm_app):
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True)
    month = month_key(_months_ahead(2))
    current = month_key(TODAY)
    po_number = f"{seed['marker']}-202610-S0042"

    def cell(c, month, **params):
        return c.get(
            f"{BASE}/{seed['product'].id}/cell", params={"month": month, **params}
        ).json()

    with TestClient(app) as c:
        later = cell(c, month, projected="true", book="project")
        now = cell(c, current, projected="true", book="project")
        now_all = cell(c, current, projected="true")
        plain = cell(c, current, book="project")
    # Listed once in each book, whether the page's own span shows it (Book=All; and
    # Book=Project once #1597 puts the reserve in that span) or the projection adds it.
    assert later["projection"] == {
        "carried_in": 900.0, "month_balance": -200.0, "reserve_supply": 0.0,
        "reserve_sources": [], "projected": 700.0, "carried_out": 700.0,
    }
    assert now["projection"]["reserve_supply"] == 1000.0
    assert now["projection"]["reserve_sources"] == [
        {"label": f"{seed['marker']}HQ reserve PO", "qty": 1000.0},
    ]
    [listed] = [row for row in now["supply"] if row.get("po_number") == po_number]
    assert listed["free_qty"] == 1000.0
    assert [row.get("po_number") for row in now_all["supply"]].count(po_number) == 1
    assert plain.get("projection") is None


def test_reserve_sources_read_po_before_spo(scm_app):
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True)
    _spo(db, seed["product"], seed["hq"], qty=50, arrives=TODAY - timedelta(days=1))
    with TestClient(app) as c:
        now = c.get(
            f"{BASE}/{seed['product'].id}/cell",
            params={"month": month_key(TODAY), "projected": "true", "book": "project"},
        ).json()
    assert now["projection"]["reserve_sources"] == [
        {"label": f"{seed['marker']}HQ reserve PO", "qty": 1000.0},
        {"label": f"{seed['marker']}HQ reserve SPO", "qty": 50.0},
    ]


def test_project_bin_spare_does_not_carry(scm_app):
    """50 spare at a BB bin, a 30 order at an IB bin two months out. The seal keeps BB off
    IB, and the projection does not carry project spare either."""
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


def test_date_from_does_not_hand_earlier_orders_reserve_stock(scm_app):
    """With the board starting two months out, the 100 due today still takes its share
    before the reserve stock carries, so the first column reads 700, as it does
    unfiltered."""
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True)
    start = _months_ahead(2).replace(day=1)
    with TestClient(app) as c:
        projected, body = _row(c, seed, projected="true", date_from=start.isoformat())
    assert body["months"][0] == month_key(start)
    by_key = {month["key"]: month["balance"] for month in projected["months"]}
    assert by_key[month_key(start)] == 700.0


def test_a_project_bin_is_never_read_as_a_reserve(scm_app):
    """#1597's predicate: a reserve is a POOL with `segment = project`. A planning bin
    with `segment = project` that no location names as its pool is a project bin: under
    `group=BB` it is out of the span, and its unlinked PO is not added as reserve supply."""
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True)
    other = _warehouse(db, f"{seed['marker']}BRW-IB", planning=True, segment="project")
    _po_line(
        db, seed["product"], other, qty=500, delivery=TODAY - timedelta(days=4),
        number=f"{seed['marker']}-IBPO",
    )
    with TestClient(app) as c:
        projected, _ = _row(c, seed, projected="true", group="BB")
    assert _balances(projected) == [-100.0, 0.0, -200.0, -300.0]


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


def test_prod_srtwt2215_switch_off_reads_as_prod(scm_app):
    """The fixture is the owner's row: Book=Project reads the prod months and Book=All's
    October reads +5,138 (HQ's 5,000 and BRW's 176 left after the dealer's 15)."""
    app, db = _client(scm_app)
    seed = _prod_srtwt2215(db)
    with TestClient(app) as c:
        project, _ = _row(c, seed, book="project")
        every, _ = _row(c, seed)
    assert _balances(project, 7) == PROD_PROJECT_MONTHS
    assert _balances(every, 7)[0] == 5138.0


def test_an_order_at_another_pool_placed_on_the_reserve_po_is_netted_in_both_books(scm_app):
    """Review round 1, blocker: every pool shares one ownership group, so a 300 order at
    BRW can hold 300 of HQ's reserve PO through a placement. Under Book=Project neither
    BRW nor HQ is in the page's span; the projection still nets that link, so both books
    carry 700: 600, 600, 400, 100."""
    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True)
    _order, brw_line = _demand(
        db, seed["product"], seed["pool"], qty=300, required_date=TODAY,
        so_number=f"{seed['marker']}-SO-BRW",
    )
    project_order, project_line = _project_line_for(db, brw_line)
    _order_back_link_on_po(db, project_order, project_line, po_line=seed["po_line"], qty=300)
    with TestClient(app) as c:
        for book in BOOKS:
            assert _projected(c, seed, book)[0] == [600.0, 600.0, 400.0, 100.0], book


def test_the_project_figure_is_the_pages_own_whatever_the_reserve_walk_caps(scm_app):
    """Review round 1, should-fix: a placement of 300 on a reserve PO with only 200
    outstanding. Under Book=Project the page honours the out-of-span link uncapped (its
    Covered by says 300), so the projection's project figure is the page's: the 300 line
    is covered and the PO has nothing left to carry. It reads exactly the switch-off
    Book=Project months."""
    from decimal import Decimal

    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True)
    seed["po_line"].qty_ordered = Decimal("200")
    db.flush()
    project_order, project_line = _project_line_for(db, seed["line"])
    _order_back_link_on_po(db, project_order, project_line, po_line=seed["po_line"], qty=300)
    with TestClient(app) as c:
        plain, _ = _row(c, seed, book="project")
        balances, _row_ = _projected(c, seed, "project")
    # The page's own figure, however its book reads the link (uncapped while HQ is out of
    # Book=Project's span; capped once #1597 puts it in).
    assert balances == _balances(plain)


def test_a_second_companys_hq_with_the_same_code_is_not_a_reserve(scm_app):
    """Owner, 12 Oct: the reserve is per company, decided by warehouse settings alone.
    Sorento's HQ is a reserve; a second company's warehouse with the SAME code, a dealer
    self-pool holding an unlinked 700 PO of the same product, is not, so its PO is never
    read as reserve supply (documents are told apart by key, never by code)."""
    import uuid as _uuid

    from app.models.company import Company
    from app.models.inventory import Warehouse

    app, db = _client(scm_app)
    seed = _seed(db, hq_in_span=True, reserve=True)
    other = Company(
        id=str(_uuid.uuid4()), name="ZZT Mocha", code=f"ZM{_uuid.uuid4().hex[:6]}"
    )
    db.add(other)
    db.flush()
    twin = Warehouse(
        id=_u(), warehouse_code=seed["hq"].warehouse_code,
        warehouse_name=seed["hq"].warehouse_code, location="ZZT", is_active=True,
        fulfilment_planning=False, segment="dealer", company_id=other.id,
    )
    db.add(twin)
    db.flush()
    twin.pool_warehouse_id = twin.id
    db.flush()
    line = _po_line(
        db, seed["product"], twin, qty=700, delivery=TODAY - timedelta(days=4),
        number=f"{seed['marker']}-MOCHAPO",
    )
    line.company_id = other.id
    db.flush()
    with TestClient(app) as c:
        for book in BOOKS:
            balances, row = _projected(c, seed, book)
            assert balances == [900.0, 900.0, 700.0, 400.0], book
