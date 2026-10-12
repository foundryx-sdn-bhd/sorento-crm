"""STOCK-DEBT-PROJECTION (owner, 12 Oct 2026): `projected=true` over the wire.

The SRTWT2215 shape, scaled down: a project bin (`-BB`) owed 100 this month, 200 in two
months and 300 in three; 20 on hand at the site pool; an unlinked PO of 1,000 at an HQ-like
dealer warehouse, delivery four days ago. The pool seal keeps both off the BB lines, so
today's months read -80, 0, -200, -300 when HQ is outside the span.

The rule (`stock_debt_projection.project_months`) carries the POOL's spare forward and adds
pool supply the page does not count. It must give the SAME answer whether the HQ PO is
inside the span (already in this month's figure) or outside it (added): 920, 920, 720, 420.
A double count would read 1,920 in the first case.
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


def _seed(db, *, hq_in_span: bool):
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
    po_line = _po_line(
        db, product, hq, qty=1000, delivery=TODAY - timedelta(days=4),
        number=f"{marker}-202610-S0042",
    )
    return {
        "product": product, "marker": marker, "po_line": po_line, "order": order,
        "line": line, "hq": hq,
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
        "carried_in": 920.0, "month_balance": -200.0, "pool_added": 0.0,
        "projected": 720.0,
    }
    assert now["projection"]["pool_added"] == 1000.0
    refs = [row["po_number"] for row in now["supply"]]
    assert f"{seed['marker']}-202610-S0042" in refs
    assert plain.get("projection") is None
    assert f"{seed['marker']}-202610-S0042" not in [row["po_number"] for row in plain["supply"]]
