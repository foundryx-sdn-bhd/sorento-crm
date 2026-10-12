"""HQ as a PROJECT RESERVE (`PLAN-hq-project-reserve-12oct.md`, PR #1597).

A project reserve is a warehouse that is some location's pool (`pool_warehouse_id`) AND
carries `segment = project`. Its stock belongs to projects in general, never to dealers:

* Fulfilment Planning never offers, draws or nets it (owner, 12 Oct: taking HQ is a Stock
  Debt step; stock reaches a project by a CS transfer in AutoCount);
* Stock Debt's Covered-by walk lets ANY project line use it after its own group's pile, and
  never a dealer (pool) line;
* the G12 unclaimed project-bin lock does not apply to it.

The `assign()` cases are pure; the board and G12 cases seed their own chain on
Postgres (`blank_session`) because CI's database has no data.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.scm.supply_assignment import DemandLine, SupplyEvent, assign
from tests.scm.conftest import scm_app  # noqa: F401  (pytest fixture)
from tests.test_order_inquiry_dedication import world  # noqa: F401  (pytest fixture)


def _dec(value) -> Decimal:
    return Decimal(str(value))


# --------------------------------------------------------------------------- Stock Debt


AS_OF = date(2026, 10, 1)
TBA = date(2029, 1, 1)


def _line(key, warehouse, required_date, qty, **kw) -> DemandLine:
    return DemandLine(
        key=key, so_number=f"SO{key}", line_no=1, warehouse=warehouse, agent_code="JAY",
        required_date=required_date, open_qty=qty, **kw,
    )


def test_reserve_stock_never_covers_a_dealer_line():
    """A dealer order at BRW is short while HQ holds 100: HQ stays put (Q3)."""
    result = assign(
        "p", as_of=AS_OF, tba_from=TBA, lead_days=90, supply=[_hq(100)],
        demand=[_line("dealer", "BRW", date(2026, 10, 20), 40, is_pool=True)],
    )

    (line,) = result.lines
    assert line.status == "short"
    assert line.assigned == ()


def _hq(qty, at=AS_OF, key="on_hand:HQ"):
    return SupplyEvent(
        key=key, kind="on_hand", warehouse="HQ", at=at, qty=qty,
        is_pool=True, is_reserve=True,
    )


def test_reserve_stock_covers_any_project_groups_line():
    """HQ is a pile any project line may use, whatever its group (owner, Q2)."""
    result = assign(
        "p", as_of=AS_OF, tba_from=TBA, lead_days=90, supply=[_hq(100)],
        demand=[
            _line("bb", "BRW-BB", date(2026, 10, 10), 30),
            _line("ib", "WH3-IB", date(2026, 10, 12), 50),
        ],
    )

    by_key = {row.line.key: row for row in result.lines}
    assert by_key["bb"].status == "covered"
    assert by_key["ib"].status == "covered"
    assert {a.event.key for a in by_key["ib"].assigned} == {"on_hand:HQ"}


def test_a_line_uses_its_own_group_before_the_reserve():
    own = SupplyEvent(key="on_hand:BRW-BB", kind="on_hand", warehouse="BRW-BB", at=AS_OF, qty=20)
    result = assign(
        "p", as_of=AS_OF, tba_from=TBA, lead_days=90, supply=[own, _hq(100)],
        demand=[_line("bb", "BRW-BB", date(2026, 10, 10), 50)],
    )

    (line,) = result.lines
    assert [(a.event.key, a.qty) for a in line.assigned] == [
        ("on_hand:BRW-BB", 20), ("on_hand:HQ", 30),
    ]


def test_the_earlier_project_line_takes_the_reserve_first_across_groups():
    result = assign(
        "p", as_of=AS_OF, tba_from=TBA, lead_days=90, supply=[_hq(40)],
        demand=[
            _line("ib-late", "WH3-IB", date(2026, 11, 1), 40),
            _line("bb-early", "BRW-BB", date(2026, 10, 5), 40),
        ],
    )

    by_key = {row.line.key: row for row in result.lines}
    assert by_key["bb-early"].status == "covered"
    assert by_key["ib-late"].status != "covered"


def test_a_reserve_arrival_clears_an_earlier_project_shortfall_late():
    """An HQ PO landing after a BB line's date clears it late, like any own-group arrival."""
    po = SupplyEvent(
        key="po:hq-1", kind="po", warehouse="HQ", at=date(2026, 10, 20), qty=100,
        is_pool=True, is_reserve=True,
    )
    result = assign(
        "p", as_of=AS_OF, tba_from=TBA, lead_days=90, supply=[po],
        demand=[_line("bb", "BRW-BB", date(2026, 10, 5), 60)],
    )

    (line,) = result.lines
    assert line.uncovered == 0
    assert [a.event.key for a in line.assigned] == ["po:hq-1"]


def test_dealer_stock_at_brw_still_covers_the_dealer_line():
    """The control: an ordinary pool's stock covers its dealer line exactly as before."""
    brw = SupplyEvent(
        key="on_hand:BRW", kind="on_hand", warehouse="BRW", at=AS_OF, qty=100, is_pool=True,
    )
    result = assign(
        "p", as_of=AS_OF, tba_from=TBA, lead_days=90, supply=[brw],
        demand=[_line("dealer", "BRW", date(2026, 10, 20), 40, is_pool=True)],
    )

    (line,) = result.lines
    assert line.status == "covered"


# --------------------------------------------------------------------------- the board


def _reserve(db, code_stem="ZZTHQ"):
    """HQ's live shape: a bare code that is its own pool (migration 311), `segment=project`."""
    from tests.test_so_supply_confirmation import _uid, _warehouse

    hq = _warehouse(db, f"{code_stem}{_uid()[:4]}", segment="project", fulfilment_planning=False)
    hq.pool_warehouse_id = hq.id
    db.flush()
    return hq


def _pool_components(proposal):
    return [
        (c["kind"], c["qty"], c["source_location"])
        for c in proposal["lines"][0]["components"]
    ]


def test_the_board_never_offers_or_draws_the_reserve():
    """Owner, 12 Oct: taking HQ is a Stock Debt step, never a planning step. HQ holds 500
    and nothing else does: the line reads Buy, with no HQ component and HQ not in the
    planner's pool set."""
    from app.services.project_supply_service import ProjectSupplyService

    from tests._pg_fixture import blank_session
    from tests.scm.test_project_supply_service_ladder import _group_sites, _seed_line, _world
    from tests.test_so_supply_confirmation import _stock

    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _own_pool = sites["BRW"]
        hq = _reserve(db)
        _stock(db, product, hq, on_hand=500)
        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own,
            qty_ordered="450", required_date=date.today() + timedelta(days=10),
        )
        service = ProjectSupplyService(db)
        components = _pool_components(service.proposal_for(order))
        pools = service.site_pool_warehouses()
        reserves = service.project_reserve_warehouses()
        hq_id, hq_code = str(hq.id), hq.warehouse_code

    assert hq_code not in [location for _kind, _qty, location in components]
    assert hq_id not in pools
    assert hq_id in reserves


def test_the_use_brw_step_is_unchanged_and_the_reserve_is_outside_the_pool_net():
    """BRW holds 400 and MWH owes dealers 500, so the dealer pools net -100; HQ's 1,000 does
    not prop that net up, and BRW lends nothing - exactly as with no HQ at all."""
    from app.services.project_supply_service import ProjectSupplyService

    from tests._pg_fixture import blank_session
    from tests.scm.test_project_supply_service_ladder import _group_sites, _seed_line, _world
    from tests.test_so_supply_confirmation import _core_line, _core_so, _stock

    when = date.today() + timedelta(days=10)
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, brw_pool = sites["BRW"]
        _mwh_own, mwh_pool = sites["MWH"]
        hq = _reserve(db)
        _stock(db, product, brw_pool, on_hand=400)
        _stock(db, product, mwh_pool, on_hand=0)
        _stock(db, product, hq, on_hand=1000)
        _core_line(db, _core_so(db, company_id), product, mwh_pool, qty_ordered="500",
                   required_date=when)
        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="300", required_date=when,
        )
        service = ProjectSupplyService(db)
        components = _pool_components(service.proposal_for(order))
        pool_net = service.netting().pools_net(str(product.id)).net
        hq_code, brw_code = hq.warehouse_code, brw_pool.warehouse_code

    assert pool_net == Decimal("-100"), "HQ is not inside the pool net"
    locations = [location for _kind, _qty, location in components]
    assert hq_code not in locations
    assert brw_code not in locations, "a pool net of -100 lends nothing"


def test_the_use_brw_step_still_lends_brws_share_beside_a_reserve():
    """The control: BRW 400 (half kept for dealers) lends 200 to a line of 200, HQ or not."""
    from app.services.project_supply_service import ProjectSupplyService

    from tests._pg_fixture import blank_session
    from tests.scm.test_project_supply_service_ladder import _group_sites, _seed_line, _world
    from tests.test_so_supply_confirmation import _stock

    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, brw_pool = sites["BRW"]
        hq = _reserve(db)
        _stock(db, product, brw_pool, on_hand=400)
        _stock(db, product, hq, on_hand=1000)
        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own,
            qty_ordered="200", required_date=date.today() + timedelta(days=10),
        )
        components = _pool_components(ProjectSupplyService(db).proposal_for(order))
        brw_code = brw_pool.warehouse_code

    assert components == [("reserve", "200", brw_code)]


# --------------------------------------------------------------------------- G12


def test_an_open_unclaimed_line_at_the_reserve_is_not_g12_locked():
    """An unclaimed PO line at HQ is general project stock, not a project bin's own."""
    from app.models.base import company_scope
    from app.services.scm.project_bin_lock import count_unclaimed_project_bin_lines

    from tests._pg_fixture import blank_session
    from tests.scm.test_project_bin_lock import MARKER, _World

    with blank_session() as db:
        world = _World(db)
        with company_scope(db, frozenset({world.company_id})):
            world.pool_wh.segment = "project"
            world.pool_wh.pool_warehouse_id = world.pool_wh.id
            db.flush()
            world.purchase_order(f"{MARKER}-PO-HQ")

            assert count_unclaimed_project_bin_lines(db, world.company_id) == 1, (
                "only the project bin's line is counted, never the reserve's"
            )


# --------------------------------------------------------------------------- the OI link pass


def test_an_unclaimed_reserve_line_stays_cascadable_for_an_order_inquiry_row(world):
    """The automatic Order Inquiry pass may take an unclaimed HQ PO line like any pool line:
    `segment = project` on a POOL makes it a reserve, not a G12-locked project bin."""
    from sqlalchemy import text as _text

    from tests.test_order_inquiry_dedication import SOON

    world.db.execute(
        _text("UPDATE warehouses SET segment = 'project' WHERE id = :i"),
        {"i": world.warehouses["BRW"]},
    )
    world.db.flush()
    lines = world.purchase_order("ZZT-PO-HQR-1", date(2026, 8, 1), [("BRW", 40, SOON, "1")])
    world.set_own_so_number("ZZT-SO-HQR-OWN")
    row = world.row("ORDER", 10, location="BRW-IB")

    candidates = world.svc._candidates_for_row(row)
    assert candidates[0]["target_id"] == lines[0]
    assert candidates[0]["cascadable"] is True
    assert candidates[0]["unattributed"] is False


# --------------------------------------------------------------------------- supply_claim


def test_a_plan_buy_at_the_reserve_writes_no_claim():
    """A plan-raised PO line at HQ stays general project stock, exactly as an ordinary pool
    line does (AC-6.10): `segment = project` on a pool does not make it a claimed bin."""
    import uuid as _uuid

    from sqlalchemy import text as _text

    from app.models.base import company_scope
    from app.services.scm.purchase_order_service import PurchaseOrderService
    from tests._pg_fixture import pg_session
    from tests.scm.conftest import SORENTO_COMPANY_ID, ensure_reference_data, seed_user
    from tests.scm.test_write_time_supply_claim import (
        MARKER, _claims_on, _draft_line, _pool_warehouse, _two_sizing_rows,
    )

    with pg_session() as db:
        ensure_reference_data(db)
        with company_scope(db, frozenset({SORENTO_COMPANY_ID})):
            actor = seed_user(db, None)
            hq_id = _pool_warehouse(db, f"{MARKER}HQ{_uuid.uuid4().hex[:6].upper()}")
            db.execute(
                _text("UPDATE warehouses SET segment = 'project' WHERE id = :i"),
                {"i": hq_id},
            )
            db.flush()
            pid, _x, _y = _two_sizing_rows(db, bin_id=hq_id)
            po_id, line_id = _draft_line(db, product_id=pid, warehouse_id=hq_id, qty=114)

            PurchaseOrderService(db).bulk_confirm([po_id], actor=actor)

            assert _claims_on(db, line_id) == []


# --------------------------------------------------------------------------- reorder / low stock


def test_reorder_does_not_count_the_reserves_stock_as_dealer_on_hand():
    """Reorder and low stock read pool on hand for the DEALER book; HQ's stock is project
    stock, so a reserve reads 0 there (the `segment` test already does this - pinned)."""
    from app.services.scm import reorder_run_service as svc
    from tests._pg_fixture import pg_session
    from tests.scm.test_pool_only_on_hand import _product, _stock, _warehouse

    with pg_session() as db:
        p = _product(db)
        hq = _warehouse(db, segment="project")
        hq.pool_warehouse_id = hq.id
        db.flush()
        _stock(db, p.id, hq.id, 5000)

        rows = svc._planning_rows(db, [str(hq.id)], [p.id])

    assert len(rows) == 1
    assert rows[0]["quantity_on_hand"] == 0


# --------------------------------------------------------------------------- Stock Debt page


def _reserve_world(scm_app):
    from app.models.inventory import Warehouse
    from tests.scm.conftest import SORENTO_COMPANY_ID
    from tests.scm.test_stock_debt_routes import (
        _client, _demand, _months_ahead, _product, _stock, _u, _warehouse,
    )

    app, db = _client(scm_app)
    marker = f"ZZTHQR{_u()[:6]}".upper()
    hq = Warehouse(
        id=_u(), warehouse_code=f"ZZTHQ{_u()[:6]}", warehouse_name="ZZT HQ", location="ZZT",
        is_active=True, fulfilment_planning=False, segment="project",
        company_id=SORENTO_COMPANY_ID,
    )
    db.add(hq)
    db.flush()
    hq.pool_warehouse_id = hq.id
    bb = _warehouse(db, f"ZZTBRW{_u()[:4]}-BB")
    product = _product(db, f"{marker}-A")
    _stock(db, product, hq, 500)
    _demand(db, product, bb, qty=40, required_date=_months_ahead(1), so_number=f"{marker}-SO1")
    db.flush()
    return app, marker, product, db, hq, bb


@pytest.mark.parametrize("book", ["all", "project"])
def test_the_reserves_on_hand_covers_a_project_line_on_the_stock_debt_page(scm_app, book):
    """HQ's 500 on hand covers the BB line's 40 (owner Q2), in Book=All and Book=Project."""
    from fastapi.testclient import TestClient

    from app.services.scm.supply_assignment import month_key
    from tests.scm.test_stock_debt_routes import BASE, _months_ahead, _row_of

    app, marker, product, _db, _hq_row, _bb = _reserve_world(scm_app)
    with TestClient(app) as c:
        got = c.get(BASE, params={"query": marker, "only_debt": False, "book": book})
    assert got.status_code == 200, got.text

    row = _row_of(got.json(), product.product_code)
    balances = {m["key"]: m["balance"] for m in row["months"]}
    assert balances[month_key(_months_ahead(1))] >= 0, balances


# --------------------------------------------------------------------------- review round 1


def test_a_reserve_arrival_clears_the_earliest_shortfall_across_groups_first():
    """BB due 1 Oct, IB due 3 Oct, BB due 5 Oct, all short; one HQ PO of 80 lands 20 Oct
    and clears them by date across groups (BB 1 Oct, then IB), not group by group."""
    po = SupplyEvent(
        key="po:hq-1", kind="po", warehouse="HQ", at=date(2026, 10, 20), qty=80,
        is_pool=True, is_reserve=True,
    )
    result = assign(
        "p", as_of=AS_OF, tba_from=TBA, lead_days=90, supply=[po],
        demand=[
            _line("bb-1", "BRW-BB", date(2026, 10, 1), 40),
            _line("ib", "WH3-IB", date(2026, 10, 3), 40),
            _line("bb-5", "BRW-BB", date(2026, 10, 5), 40),
        ],
    )

    by_key = {row.line.key: row for row in result.lines}
    assert by_key["bb-1"].uncovered == 0
    assert by_key["ib"].uncovered == 0
    assert by_key["bb-5"].uncovered == 40


def test_a_line_takes_the_reserves_free_stock_before_another_lines_lendable_claim():
    """Rule 6 across piles: BRW-BB's 100 is a far line's lendable landed claim and HQ holds
    500 free, so a near BB line of 50 takes HQ and lends nobody's goods."""
    from app.services.scm.supply_assignment import Hold

    bin_floor = SupplyEvent(
        key="on_hand:BRW-BB", kind="on_hand", warehouse="BRW-BB", at=AS_OF, qty=100,
    )
    far = _line("far", "BRW-BB", date(2027, 6, 1), 100)
    near = _line("near", "BRW-BB", date(2026, 10, 10), 50)
    claim = Hold(
        line_key="far", supply_key="on_hand:BRW-BB", qty=100, kind="on_hand",
        warehouse="BRW-BB", landed=True, lendable=True,
    )
    result = assign(
        "p", as_of=AS_OF, tba_from=TBA, lead_days=90, supply=[bin_floor, _hq(500)],
        demand=[far, near], pinned=[claim],
    )

    by_key = {row.line.key: row for row in result.lines}
    assert [a.event.key for a in by_key["near"].assigned] == ["on_hand:HQ"]
    assert by_key["far"].lent_qty == 0


def test_a_line_booked_at_the_reserve_draws_the_reserves_own_stock():
    """An SO line booked AT HQ is in HQ's own pile, like any line at its own location."""
    line = DemandLine(
        key="at-hq", so_number="SOhq", line_no=1, warehouse="HQ", agent_code="JAY",
        required_date=date(2026, 10, 10), open_qty=30, is_pool=True, is_reserve=True,
    )
    result = assign(
        "p", as_of=AS_OF, tba_from=TBA, lead_days=90, supply=[_hq(100)], demand=[line],
    )

    (row,) = result.lines
    assert row.status == "covered"


def test_a_book_so_on_an_hq_po_pins_a_project_line_and_never_a_dealer_line():
    """R42 for the reserve: HQ's pile is one any project line may use (`may_draw`)."""
    from app.services.scm.stock_debt_service import book_so_pins

    po = SupplyEvent(
        key="po:hq-1", kind="po", warehouse="HQ", at=date(2026, 10, 20), qty=50,
        is_pool=True, is_reserve=True,
    )

    def _so_line(key, warehouse, *, is_pool=False):
        return DemandLine(
            key=key, so_number="SO1", line_no=1, warehouse=warehouse, agent_code="JAY",
            required_date=date(2026, 10, 10), open_qty=20, is_pool=is_pool,
            sales_order_id="so-1",
        )

    project = book_so_pins(
        [("p", po, "so-1", 0.0)], {"p": [_so_line("bb", "BRW-BB")]}, [], tba_from=TBA,
    )
    dealer = book_so_pins(
        [("p", po, "so-1", 0.0)], {"p": [_so_line("brw", "BRW", is_pool=True)]}, [],
        tba_from=TBA,
    )

    assert [(h.line_key, h.qty) for h in project] == [("bb", 20)]
    assert dealer == []


def test_the_order_inquiry_netting_keeps_the_reserve_out_of_the_pool_net():
    """`netting_for_products` (the OI link walk and the chatbot's door) nets the dealer
    pools without HQ, the same as the planner's own netting."""
    from app.services.scm.group_netting import netting_for_products

    from tests._pg_fixture import blank_session
    from tests.scm.test_project_supply_service_ladder import _group_sites, _world
    from tests.test_so_supply_confirmation import _stock

    with blank_session() as db:
        _company_id, _eling, _project, product = _world(db)
        _group, sites = _group_sites(db)
        _own, brw_pool = sites["BRW"]
        hq = _reserve(db)
        _stock(db, product, brw_pool, on_hand=40)
        _stock(db, product, hq, on_hand=1000)

        net = netting_for_products(db, [str(product.id)]).pools_net(str(product.id)).net

    assert net == Decimal("40")


def test_g12_skips_the_reserve_for_spo_lines_and_the_po_list_filter():
    from app.models.base import company_scope
    from app.services.scm.project_bin_lock import count_unclaimed_project_bin_lines
    from app.services.scm.purchase_order_service import PurchaseOrderService

    from tests._pg_fixture import blank_session
    from tests.scm.test_project_bin_lock import MARKER, _World

    with blank_session() as db:
        world = _World(db)
        with company_scope(db, frozenset({world.company_id})):
            # The fixture's "bin" becomes HQ's shape: segment project AND a pool.
            world.bin_wh.pool_warehouse_id = world.bin_wh.id
            db.flush()
            world.spo_allocation(f"{MARKER}-SPO-HQ")
            made = world.purchase_order(f"{MARKER}-PO-HQF")

            assert count_unclaimed_project_bin_lines(db, world.company_id) == 0
            listed = PurchaseOrderService(db).list(
                page=1, limit=50, sort="po_number", direction="asc",
                query=made["po"].po_number, status=None, supplier=None,
                unclaimed_project_bin=True,
            )
            assert listed["data"] == []


def _page_balance(app, marker, product, month_date, **params):
    from fastapi.testclient import TestClient

    from app.services.scm.supply_assignment import month_key
    from tests.scm.test_stock_debt_routes import BASE, _row_of

    with TestClient(app) as c:
        got = c.get(BASE, params={"query": marker, "only_debt": False, **params})
    assert got.status_code == 200, got.text
    row = _row_of(got.json(), product.product_code)
    return {m["key"]: m["balance"] for m in row["months"]}[month_key(month_date)]


def test_a_group_filter_reads_the_groups_own_stock_without_the_reserve(scm_app):
    """`group=BB` answers what BB holds of its own (AC-S2-6): HQ is no group's stock, so
    the BB line reads short its 40 there, and covered in the unfiltered book."""
    from app.services.scm.group_netting import group_of_warehouse_code
    from tests.scm.test_stock_debt_routes import _months_ahead

    app, marker, product, _db, _hq_row, bb = _reserve_world(scm_app)
    group = group_of_warehouse_code(bb.warehouse_code)

    assert _page_balance(app, marker, product, _months_ahead(1), book="project") >= 0
    assert (
        _page_balance(app, marker, product, _months_ahead(1), book="project", group=group)
        == -40
    )


def test_a_line_booked_at_hq_is_covered_by_hqs_own_stock_on_the_page(scm_app):
    from tests.scm.test_stock_debt_routes import _demand, _months_ahead

    app, marker, product, db, hq, _bb = _reserve_world(scm_app)
    _demand(db, product, hq, qty=30, required_date=_months_ahead(2),
            so_number=f"{marker}-SOHQ")
    db.flush()

    assert _page_balance(app, marker, product, _months_ahead(2), book="project") >= 0


# --------------------------------------------------------------------------- per company


def test_a_second_companys_hq_with_segment_dealer_stays_a_normal_pool():
    """Owner, 12 Oct 23:50: the reserve is per company and decided by warehouse settings
    alone. Sorento's HQ (self-pool, `segment = project`) is a reserve; a second company's
    warehouse with the SAME code `HQ` that is a self-pool with `segment = dealer` (Mocha's
    shape) stays an ordinary site pool: planner pool, inside the pool net, no reserve."""
    import uuid as _uuid

    from app.models.base import company_scope
    from app.models.company import Company
    from app.models.inventory import Warehouse
    from app.services.project_supply_service import ProjectSupplyService
    from app.services.scm.group_netting import netting_for_products
    from app.services.scm.pool_predicate import is_project_reserve

    from tests._pg_fixture import blank_session
    from tests.scm.test_project_supply_service_ladder import _world

    code = f"ZZHQ{_uuid.uuid4().hex[:4].upper()}"
    with blank_session() as db:
        srt_id, _eling, _project, product = _world(db)
        other = Company(id=str(_uuid.uuid4()), name="ZZT Mocha", code=f"ZM{_uuid.uuid4().hex[:6]}")
        db.add(other)
        db.flush()

        def _hq(company_id, segment):
            row = Warehouse(
                id=str(_uuid.uuid4()), company_id=company_id, warehouse_code=code,
                warehouse_name=code, is_active=True, segment=segment,
                fulfilment_planning=False,
            )
            db.add(row)
            db.flush()
            row.pool_warehouse_id = row.id
            db.flush()
            return row

        srt_hq = _hq(srt_id, "project")
        mch_hq = _hq(other.id, "dealer")
        from app.models.inventory import Stock

        db.add(Stock(id=str(_uuid.uuid4()), company_id=other.id, product_id=product.id,
                     warehouse_id=mch_hq.id, quantity_on_hand=70, quantity_reserved=0))
        db.flush()

        with company_scope(db, frozenset({str(srt_id)})):
            srt = ProjectSupplyService(db)
            srt_pools, srt_reserves = set(srt.site_pool_warehouses()), set(
                srt.project_reserve_warehouses()
            )
        with company_scope(db, frozenset({str(other.id)})):
            mch = ProjectSupplyService(db)
            mch_pools, mch_reserves = set(mch.site_pool_warehouses()), set(
                mch.project_reserve_warehouses()
            )
            mch_net = netting_for_products(db, [str(product.id)]).pools_net(str(product.id))

    assert is_project_reserve("project", is_pool=True)
    assert not is_project_reserve("dealer", is_pool=True)
    assert srt_hq.id in srt_reserves and srt_hq.id not in srt_pools
    assert mch_hq.id in mch_pools and mch_hq.id not in mch_reserves
    assert str(mch_hq.id) in {entry.warehouse_id for entry in mch_net.by_location}
    assert mch_net.net == Decimal("70")
