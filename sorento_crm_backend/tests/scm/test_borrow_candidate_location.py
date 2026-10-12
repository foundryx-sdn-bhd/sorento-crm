"""Borrow candidate carries the board's own Location row (S4, PLAN-local-supplier-oi-routing.md,
UAC AC-2.20 - AC-2.21).

RED for AC-2.20: `borrow_candidates[]` has no `location` key yet, so the board test below
fails on a `KeyError`. Fixtures copied straight from `tests/test_fulfilment_board.py`
(`_product`, `_warehouse`, `_stock`, `_order`, `_line`, `_service`, `_cell`, `_uid`,
`TODAY`) - same scenario as that file's own
`test_a_board_borrow_candidate_carries_what_it_takes_to_confirm_it`, extended to compare
the candidate's new `location` against the cell's existing `locations` table entry for the
same donor warehouse.

AC-2.21 is `stock_detail`'s existing cross-group behaviour (the endpoint takes an
arbitrary `warehouse_id` with no group restriction already) - written here as the
regression proof the plan calls for; if it turns out already green against current code
that is reported to the captain rather than forced red, per the tester brief.

`test_supply_sheet_line_carries_buy_origin_and_candidate_location` is the third guard, over
`GET /project-sales/sales-orders/{pso_id}/supply` (`ProjectSupplyService.proposal_for`) rather
than the board's `build()` - the coder's own commit message (feat(scm): per-order sheet lines
carry buy_origin and candidate location, S3/S4) named this exact assertion as the one guard
missing before HEAD. The board-side fixtures above build a plain core `SalesOrder`, which this
route cannot address (it takes a `ProjectSalesOrder` id), so this test additionally pulls the
project-sales `api` fixture and helpers from `tests/test_so_supply_confirmation.py` (the file
that already exercises this exact route) plus the supplier/country helpers `tests/scm/
test_supply_origin.py` and `tests/scm/test_summary_order_service.py` already established.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.procurement import PurchaseOrder, PurchaseOrderLine
from app.models.project_so import INQUIRY_PLACED, IV_ORDER, OrderInquiry, OrderInquiryRow

from tests.test_fulfilment_board import (  # noqa: F401  (fixtures/helpers, not tests)
    TODAY,
    _cell,
    _line,
    _order,
    _product,
    _service,
    _stock,
    _uid,
    _warehouse,
)
from tests._pg_fixture import blank_session
from tests.scm.test_summary_order_service import _link, _supplier
from tests.scm.test_supply_origin import _country
from tests.test_so_supply_confirmation import (
    BASE,
    _core_line,
    _core_so,
    _project_line,
    _project_so,
    _suffix,
    api,
)
from tests.test_so_supply_confirmation import _stock as _pso_stock
from tests.test_so_supply_confirmation import _warehouse as _pso_warehouse

LOCATION_KEYS = (
    "qty_on_hand", "so_qty", "spo_qty", "available_qty", "available_for_project",
    "po_open_qty", "where", "warehouse_id",
)


def test_candidate_carries_location_in_cell_shape():
    """Fix round, group 7 (`2e677c1e8`) reverted an out-of-plan board change that had
    started listing every OFFERED donor in the Grid Location table: a donor now gets a
    row there only when the confirmed proposal actually CITES it (`_cited_locations`).
    So `own` and `donor` are put in the SAME ownership group (`-ZZ` suffix on both codes,
    `sales_agent_service.group_of_warehouse_code`) - `own`'s own-group draw (rung 1a) then
    cites `donor` for real, and it lands in `contribution["locations"]` the way any group
    member does, rather than only being offered as a manual Borrow candidate."""
    with blank_session() as db:
        sfx = _uid()[:6]
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZA{sfx}-ZZ")
        donor = _warehouse(db, f"ZB{sfx}-ZZ")
        _stock(db, product, own, on_hand=0)
        _stock(db, product, donor, on_hand=25)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=date(2026, 9, 3), warehouse=own)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-31")["contributions"][0]
        candidate = contribution["borrow_candidates"][0]

        assert "location" in candidate, "borrow_candidates[] must carry a location object"
        location = candidate["location"]
        for key in LOCATION_KEYS:
            assert key in location, key

        assert location["warehouse_id"] == str(donor.id)

        # Same figures as the Grid Location table for the SAME warehouse, same request.
        grid_row = next(
            row for row in contribution["locations"] if row["warehouse_id"] == str(donor.id)
        )
        for key in ("qty_on_hand", "so_qty", "spo_qty", "available_qty", "po_open_qty"):
            assert location[key] == grid_row[key], key


def test_stock_detail_cross_group_donor_marks_this_line():
    """`stock_detail(product_id, warehouse_id, line_ids=)` for a warehouse OUTSIDE the
    asking line's own ownership group still correctly flags that line's own row
    `is_this_line` - a plain membership check, with no group-scoping that could silently
    exclude a genuine cross-group donor read."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        donor = _warehouse(db, f"ZZTD{_uid()[:6]}"[:20])
        earlier_order = _order(db, so_number=f"ZZT-SO-EARLY-{_uid()[:6]}", order_date=date(2026, 3, 1))
        _line(db, earlier_order, product, qty="1", required_date=date(2026, 4, 1), warehouse=donor)
        asking_order = _order(db, so_number=f"ZZT-SO-ASK-{_uid()[:6]}", order_date=date(2026, 1, 1))
        asking_line = _line(
            db, asking_order, product, qty="24", required_date=date(2026, 6, 29), warehouse=donor,
        )

        detail = _service(db).stock_detail(
            str(product.id), str(donor.id), line_ids=[str(asking_line.id)]
        )

        marked = [row for row in detail["sales_orders"] if row["is_this_line"]]
        assert len(marked) == 1
        assert marked[0]["line_id"] == str(asking_line.id)


def test_donor_location_reads_site_pool_share_and_netted_po_open_qty():
    """`ProjectSupplyService.donor_location` (S4): the site-pool half of the SAME shape
    `test_candidate_carries_location_in_cell_shape` already proves for a plain group
    donor.

    Not reachable through `borrow_candidates_for` / `_borrow_candidates` on either real
    surface (the board's `_donors_for`, the per-order sheet's own `borrow_candidates`):
    `pools_net` nets EVERY registered pool in the book as one shared pile (its own
    docstring, "not five separate answers to `is there shared stock`"), so
    `_reserve_location_ids` always counts a spare pool as INSIDE Reserve's reach and
    `_borrow_candidates`'s free-stock walk excludes everything `inside` - a pool with
    ANY capacity left is drawn automatically rather than ever being offered as a manual
    Borrow. Measured empirically (a pool-donor line here reserved fully from the pool
    and both real callers returned `borrow_candidates: []`) rather than assumed; flagged
    to the captain as a design question (dead branch vs. a caller that does not exist
    yet), not silently worked around.

    So this reads `donor_location` the way its own docstring says a caller must:
    `_pile_facts`'s span warmed by a real `demand_facts` walk (never a bare
    `ProjectSupplyService` with no request behind it), which is the same precondition
    `borrow_candidates_for` states for itself - a real public sequence, just not the
    ladder's OWN selection of what to offer.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        donor = _warehouse(db, f"ZZTP{_uid()[:6]}"[:20])
        donor.segment = None  # a site pool, never a project reserve (HQ)
        other_owner = _warehouse(db, f"ZZTQ{_uid()[:6]}"[:20])
        other_owner.pool_warehouse_id = donor.id
        db.flush()
        _stock(db, product, donor, on_hand=100)

        counted_po = PurchaseOrder(id=_uid(), po_number=f"PO-{_uid()[:8]}", status="active")
        db.add(counted_po)
        db.flush()
        counted_line = PurchaseOrderLine(
            id=_uid(), purchase_order_id=counted_po.id, product_id=product.id,
            warehouse_id=donor.id, qty_ordered=Decimal("50"), qty_received=Decimal("10"),
            line_status="open",
        )
        db.add(counted_line)
        db.flush()

        # An SPO-history PO at the SAME donor: `open_po_balance` excludes it by po_number
        # prefix, so it must not move `po_open_qty` at all.
        spo_po = PurchaseOrder(id=_uid(), po_number=f"SPO-{_uid()[:8]}", status="active")
        db.add(spo_po)
        db.flush()
        db.add(PurchaseOrderLine(
            id=_uid(), purchase_order_id=spo_po.id, product_id=product.id,
            warehouse_id=donor.id, qty_ordered=Decimal("999"), qty_received=Decimal("0"),
            line_status="open",
        ))

        # An OI header/row is required FK plumbing for `open_po_balance`'s netting query
        # (`OrderInquiryRow.po_line_id`); the project-sales chain it belongs to plays no
        # other part in this test, so the smallest header this model accepts is used.
        from app.services import project_seed_service
        from app.services.project_service import register_project
        from tests.test_so_supply_confirmation import _project_so, _sorento, _user

        company_id = _sorento(db)
        project_seed_service.run(db, company_id=company_id)
        actor_id = _user(db, f"ZZT-{_uid()[:6]}")
        project = register_project(
            db, company_id=company_id, actor_user_id=actor_id, developer_party_id=None,
            title=f"ZZT-{_uid()[:6]} project",
        )
        inquiry_order = _project_so(db, project)
        inquiry = OrderInquiry(
            id=_uid(), project_sales_order_id=inquiry_order.id, inquiry_no=f"ZZT-OI-{_uid()[:8]}",
        )
        db.add(inquiry)
        db.flush()
        db.add(OrderInquiryRow(
            id=_uid(), order_inquiry_id=inquiry.id, po_line_id=counted_line.id,
            item_code=product.product_code, qty=Decimal("15"), verb=IV_ORDER,
            state=INQUIRY_PLACED,
        ))
        db.commit()

        from app.services.project_supply_service import ProjectSupplyService

        supply = ProjectSupplyService(db)
        supply.demand_facts([{
            "key": "zzt-1", "product_id": product.id, "warehouse_id": donor.id,
            "open_qty": "0", "required_date": TODAY, "item_code": product.product_code,
        }])
        location = supply.donor_location(str(product.id), str(donor.id))

        assert location["where"] == "site_pool"
        assert location["po_open_qty"] == "25", location  # 50 ordered - 10 received - 15 placed
        assert Decimal(location["available_for_project"]) < Decimal(location["available_qty"]), location


def test_supply_sheet_line_carries_buy_origin_and_candidate_location(api):
    """AC-2.14 / AC-2.20 over the per-order sheet route, `GET .../supply`.

    A product whose PRIMARY `product_suppliers` link names a Malaysian supplier ("local"
    origin, `supply_origin.buy_origin_by_product`), with a donor warehouse elsewhere in
    the book holding stock, and no stock at the line's own location - so the borrow rung
    finds exactly the one candidate this test names.
    """
    client, world = api
    db = world.db

    # PLAN-local-buy-routing-toggle.md: the resolver now reads the setting first, off
    # by default, so it must be ON here for this test's "local" pin to hold.
    from app.models.user import SystemSetting

    setting_row = db.query(SystemSetting).first() or SystemSetting(id=_suffix())
    db.add(setting_row)
    db.flush()
    db.query(SystemSetting).filter(SystemSetting.id == setting_row.id).update(
        {SystemSetting.local_buy_routing_enabled: True}
    )

    home = _country(db, "MY", "Malaysia")
    supplier = _supplier(db, f"ZZT local sdn bhd {_suffix()}")
    supplier.country_id = home.id
    db.flush()
    # `lead=90` keeps the reserve window (today + lead + RESERVE_BUFFER_DAYS, see
    # `front_planning_engine.reserve_window_end`) well past the line's `REQUIRED_DATE`
    # (today + 30, `test_so_supply_confirmation`'s own fixed-relative date) - the
    # `_link` default of 14 shrinks that window to today + 28, which pushed this line
    # OUTSIDE it and made the whole thing a plain Buy with no borrow rung walked at all.
    _link(db, world.product, supplier, primary=True, lead=90)

    donor = _pso_warehouse(db, f"ZZT-DONOR-{_suffix()}")
    _pso_stock(db, world.product, donor, on_hand=25)

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="10")
    _project_line(db, order, line_no=10, product=world.product, core_line=core_line)
    db.commit()

    response = client.get(f"{BASE}/sales-orders/{order.id}/supply")
    assert response.status_code == 200, response.text
    line = response.json()["lines"][0]

    assert line["buy_origin"] in ("local", "overseas"), line["buy_origin"]
    assert line["buy_origin"] == "local", (
        "the primary link names a Malaysian supplier, so this line must resolve local"
    )

    candidates = line["borrow_candidates"]
    assert candidates, "the donor's free stock should surface as a borrow candidate"
    location = candidates[0]["location"]
    assert location["warehouse_id"] == str(donor.id)
    assert location["qty_on_hand"] == "25", location

