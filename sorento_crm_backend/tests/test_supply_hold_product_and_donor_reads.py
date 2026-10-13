"""Two facts about how the supply service reads stock.

**A hold is keyed by the CORE line's product.** `_facts_for` judges a remapped mirror line
against its core line's product, and `_lock_stock` locks both spellings for that reason;
the hold that confirmation writes has to net out of the SAME product's free stock, or a
remapped line's Reserve leaves the core product reading as free as it was and the mirror's
product 20 short of nothing.

**A donor list is read from what the request already knows.** The board asks for donors
once per line, and each ask used to re-read the warehouse and project rows behind the
donors from the database. Once the caches behind a proposal are filled, a second ask
against a different quantity issues no SQL at all.

Postgres via `tests/_pg_fixture.py::blank_session`, seeding its own chain, with the Stage
1C suite's own helpers.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import event

from app.services.project_supply_service import ProjectSupplyService

from .test_so_supply_confirmation import (  # noqa: F401  (api is a fixture)
    BASE,
    _core_line,
    _core_so,
    _line_payload,
    _product,
    _project_line,
    _project_so,
    _stock,
    _uid,
    _warehouse,
    api,
)


def test_a_remapped_lines_hold_nets_out_of_the_core_products_free_stock(api):
    client, world = api
    db = world.db
    mirror_product = world.product
    core_product = _product(db)
    _stock(db, mirror_product, world.pool_wh, on_hand=100)
    _stock(db, core_product, world.pool_wh, on_hand=100)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core = _core_line(db, core_so, core_product, world.own_wh, qty_ordered="50")
    # The mirror still names the product it was drafted with; reconciliation pointed it
    # at a core line for a different one.
    line = _project_line(db, order, line_no=10, product=mirror_product, core_line=core)
    db.commit()

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={
            "lines": [
                # Wholly from stock (AC-L5): a line is met entirely from stock or entirely
                # bought, never a mix.
                _line_payload(
                    line.id,
                    reserve=[{"warehouse_id": world.pool_wh.id, "qty": "50"}],
                )
            ]
        },
    )
    assert response.status_code == 200, response.text

    supply = ProjectSupplyService(db)
    held = supply.held_stock_by_location([mirror_product.id, core_product.id])
    assert held == {(str(core_product.id), str(world.pool_wh.id)): Decimal("50")}, (
        "the hold is keyed by the CORE line's product, the one the stock was judged against"
    )
    free = supply.free_stock_by_location([mirror_product.id, core_product.id])
    assert free[(str(core_product.id), str(world.pool_wh.id))] == Decimal("50")
    assert free[(str(mirror_product.id), str(world.pool_wh.id))] == Decimal("100"), (
        "the mirror's own product was never promised and stays whole"
    )


def test_a_second_donor_read_against_another_need_issues_no_sql(api):
    _client, world = api
    db = world.db
    donor_wh = _warehouse(db, f"ZZT-DONOR-{_uid()[:4]}")
    _stock(db, world.product, world.own_wh, on_hand=10)
    _stock(db, world.product, donor_wh, on_hand=100)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    _project_line(db, order, line_no=10, product=world.product, core_line=core)
    db.commit()

    supply = ProjectSupplyService(db)
    facts = supply.demand_facts(
        [
            {
                "key": "line",
                "product_id": str(world.product.id),
                "warehouse_id": str(world.own_wh.id),
                "open_qty": Decimal("50"),
                "required_date": core.required_date,
                "item_code": world.product.product_code,
                "line_id": str(core.id),
            }
        ]
    )
    fact = facts["line"]
    first = supply.borrow_candidates_for(fact, need=Decimal("40"))
    # BORROW-OWN-LOC: the line's own location is always the last row.
    assert [row["warehouse_code"] for row in first] == [
        donor_wh.warehouse_code, world.own_wh.warehouse_code,
    ]
    assert first[0]["available_after_need"] == "60"

    statements = []

    def count(_conn, _cursor, statement, *_rest):
        statements.append(statement)

    connection = db.connection()
    event.listen(connection, "before_cursor_execute", count)
    try:
        second = supply.borrow_candidates_for(fact, need=Decimal("45"))
    finally:
        event.remove(connection, "before_cursor_execute", count)

    assert [row["warehouse_code"] for row in second] == [
        donor_wh.warehouse_code, world.own_wh.warehouse_code,
    ]
    assert second[0]["available_after_need"] == "55"
    assert statements == [], "the donors are read from the request's own caches"
