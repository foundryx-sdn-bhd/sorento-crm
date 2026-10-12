"""HQ as a PROJECT RESERVE (`PLAN-hq-project-reserve-12oct.md`, PR #1597).

A project reserve is a warehouse that is some location's pool (`pool_warehouse_id`) AND
carries `segment = project`. Its stock belongs to projects in general, never to dealers:

* the planner lends a project line its whole own net (0% dealer share), and the reserve sits
  outside the dealer `pools_net`, so it neither props up nor is capped by a dealer backlog;
* Stock Debt never sets reserve supply against a dealer (pool) line;
* the G12 unclaimed project-bin lock does not apply to it.

The engine and `assign()` cases are pure; the board and G12 cases seed their own chain on
Postgres (`blank_session`) because CI's database has no data.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.scm.supply_assignment import DemandLine, SupplyEvent, assign
from tests.test_order_inquiry_dedication import world  # noqa: F401  (pytest fixture)


def _dec(value) -> Decimal:
    return Decimal(str(value))


# --------------------------------------------------------------------------- the engine


def test_a_reserve_lends_its_whole_available_whatever_the_dealer_net():
    """0% dealer share, and the dealer pools' net (here oversold) does not cap it."""
    from app.services.scm.front_planning_engine import pool_share_capacity

    chain = pool_share_capacity(
        pools=[{"location": "HQ", "free": 500, "available": 500, "reserve": True}],
        pools_net=-100,
        pool_share_pct=50,
    )

    assert [(loc, _dec(cap)) for loc, cap, _allowance in chain] == [("HQ", Decimal("500"))]


def test_a_reserve_does_not_spend_the_dealer_pools_net():
    """BRW keeps its own share rule against the dealer net; HQ answers beside it, and what
    HQ lent is not taken off the net BRW is bounded by."""
    from app.services.scm.front_planning_engine import pool_share_capacity

    chain = pool_share_capacity(
        pools=[
            {"location": "HQ", "free": 500, "available": 500, "reserve": True},
            {"location": "BRW", "free": 400, "available": 400},
        ],
        pools_net=400,
        pool_share_pct=50,
    )

    assert {loc: _dec(cap) for loc, cap, _allowance in chain} == {
        "HQ": Decimal("500"),
        "BRW": Decimal("200"),
    }


def test_the_dealer_share_rule_is_unchanged_for_an_ordinary_pool():
    from app.services.scm.front_planning_engine import available_for_project

    assert available_for_project(47, 1000, 50) == Decimal("23")


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
    hq = SupplyEvent(
        key="on_hand:HQ", kind="on_hand", warehouse="HQ", at=AS_OF, qty=100,
        is_pool=True, is_reserve=True,
    )
    result = assign(
        "p", as_of=AS_OF, tba_from=TBA, lead_days=90, supply=[hq],
        demand=[_line("dealer", "BRW", date(2026, 10, 20), 40, is_pool=True)],
    )

    (line,) = result.lines
    assert line.status == "short"
    assert line.assigned == ()


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


def test_the_board_lends_a_project_line_the_reserves_whole_stock():
    """HQ 500, nothing anywhere else: a line of 450 reads HQ 450, not HQ's half and a Buy."""
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
        components = _pool_components(ProjectSupplyService(db).proposal_for(order))
        hq_code = hq.warehouse_code

    assert components == [("reserve", "450", hq_code)]


def test_the_reserve_does_not_prop_up_an_oversold_dealer_pool_net():
    """BRW holds 400 but MWH owes dealers 500, so the dealer pools net -100 and lend
    nothing; HQ (1,000) is outside that net and covers the line on its own."""
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
        dealer_net = service.netting().pools_net(str(product.id)).net
        hq_code = hq.warehouse_code

    assert dealer_net == Decimal("-100"), "HQ is not inside the dealer pools' net"
    assert components == [("reserve", "300", hq_code)]


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
