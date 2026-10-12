"""The source ladder, as the captain last ruled it: v3, 25 August 2026
(`documentation/plans/scm/PLAN-scm-cs-planning-uat.md` section 1b, and 1c for the borrow a
person picks). The file was `..._ladder_v2.py`; the version is out of the name because the
ladder has now been renumbered twice and a name that dates itself goes stale on the next
ruling rather than on the next rewrite.

Service-level (`ProjectSupplyService.proposal_for` / `.confirm`), not HTTP: every scenario
here is about the COMPOSITION and the write path, not the route layer, which
`tests/test_so_supply_confirmation.py` already covers end to end. Warehouse and world
helpers are imported from that suite so the two files cannot come to disagree about what a
Project SO looks like (the same convention `tests/test_supply_partial_confirmation.py`
already follows).

Postgres via `tests/_pg_fixture.py::blank_session`, every test seeding its own chain, per
PRINCIPLES.md and this repo's CI-is-empty lesson.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.models.project_so import SO_STATUS_PUBLISHED
from app.services import project_seed_service
from app.services.error_handler import AppException
from app.services.project_supply_service import ProjectSupplyService
from app.services.scm import priority

from .._pg_fixture import blank_session
from .._borrow_mode import borrow_from_order_on
from ..test_so_supply_confirmation import (  # noqa: F401  (helpers, not fixtures)
    _core_line,
    _core_so,
    _product,
    _project_line,
    _project_so,
    _second_company,  # noqa: F401
    _sorento,
    _stock,
    _uid,
    _user,
    _warehouse,
)

#: The delivery date every scenario here plans against.
#:
#: RELATIVE to today, and inside the ATP reserve window on purpose (see
#: `front_planning_engine.reserve_window_end`): the borrow rungs this file exists to pin are
#: not offered to a line due beyond `today + lead time + buffer`, so a fixed far-future date
#: would test the window rule instead of the rungs. The window's own behaviour is asserted in
#: `test_front_planning_engine.py` and in this file's own reserve-window scenarios.
REQUIRED_DATE = date.today() + timedelta(days=30)


def _world(db):
    """Company, project, product - the minimum every scenario below builds on."""
    from app.models.base import company_scope
    from app.services.project_service import register_project

    company_id = _sorento(db)
    project_seed_service.run(db, company_id=company_id)
    eling = _user(db, "zzt-ladder-v2 Eling")
    project = register_project(
        db, company_id=company_id, actor_user_id=eling, developer_party_id=None,
        title="zzt-ladder-v2 Tuju Residences",
    )
    from tests.test_so_supply_confirmation import _product

    product = _product(db)
    return company_id, eling, project, product


def _group_sites(db):
    """Three sites, one ownership group: `ZZT<SITE>-<GROUP>` at BRW/MWH/DC1, each with its
    own site pool `ZZT<SITE><suffix>` (no hyphen - the plain pool code)."""
    group = f"BB{_uid()[:4]}"
    sites = {}
    for site in ("BRW", "MWH", "DC1"):
        pool = _warehouse(db, f"ZZT{site}{_uid()[:4]}")
        own = _warehouse(db, f"ZZT{site}-{group}")
        own.pool_warehouse_id = pool.id
        db.flush()
        sites[site] = (own, pool)
    return group, sites


def _seed_line(db, company_id, project, product, warehouse, *, qty_ordered, required_date=REQUIRED_DATE,
          line_no=10, so_number=None, sales_agent_id=None):
    from app.models.order import SalesOrder

    core_so = _core_so(db, company_id)
    if so_number is not None:
        core_so.so_number = so_number
    if sales_agent_id is not None:
        core_so.sales_agent_id = sales_agent_id
    db.flush()
    core_line = _core_line(
        db, core_so, product, warehouse, qty_ordered=qty_ordered, required_date=required_date,
    )
    order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
    line = _project_line(db, order, line_no=line_no, product=product, core_line=core_line)
    db.commit()
    return order, line, core_so, core_line


def _agent(db, code: str, *, location_group=None):
    from app.models.sales_agent import SalesAgent

    row = SalesAgent(id=_uid(), sales_agent=code, source="manual", is_active=True,
                      location_group=location_group)
    db.add(row)
    db.flush()
    return row


def _components(proposal):
    return proposal["lines"][0]["components"]


# --------------------------------------------------------------------------- rung 0


def test_a_line_beyond_the_coverage_date_proposes_buy_all_only():
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=999)
        priority.create_revision(
            db, name="zzt-coverage", factors={}, demand_class_weights={},
            reorder_coverage_until=date(2026, 10, 31),
        )
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date(2029, 1, 1),
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)

    assert len(components) == 1
    assert components[0]["kind"] == "buy"
    assert components[0]["qty"] == "40"
    assert "coverage" in components[0]["reason"]


def test_a_line_on_or_before_the_coverage_date_runs_the_ladder_normally():
    """LADDER V8: the pool holds 80 rather than 40, because half of it is kept back for
    dealers now (R-B) and the point of this case is rung 0 NOT firing - a line inside the
    coverage date walking the ladder normally."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=80)
        priority.create_revision(
            db, name="zzt-coverage-2", factors={}, demand_class_weights={},
            reorder_coverage_until=date(2026, 10, 31),
        )
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date(2026, 10, 31),
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)

    assert len(components) == 1
    assert components[0]["kind"] == "reserve"
    assert components[0]["qty"] == "40"


# --------------------------------------------------------------------------- own location


def test_the_own_location_is_a_group_source_again_under_ladder_v3():
    """Section 1b rung 2, the captain 25 August 2026: "consider the group location first
    (only available quantity)". The line's own location is a location of its group, so a
    line standing on free stock reserves it rather than buying it - which is what v2's rule
    7 exclusion made impossible."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, own, on_hand=999)
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)

    assert len(components) == 1
    assert components[0]["kind"] == "reserve"
    assert components[0]["rung"] == "group_take"
    assert components[0]["source_location"] == own.warehouse_code
    assert components[0]["qty"] == "40"


# --------------------------------------------------------------------------- rung 2: pool


def test_the_asking_bins_pool_spares_its_share_and_another_site_covers_the_remainder():
    """LADDER V8 (R-A/R-B/R-L). Step 0 is the ASKING bin's own site pool and its own share:
    BRW's 15 spares 7. What is LEFT of the line then walks the ladder (R-C) - own locations,
    both borrows - and reaches the OTHER site pools last (R-L), where MWH's 100 spares 50
    and covers the remaining 33 whole.

    Under v7.1 this read "15 from BRW, 25 from MWH": the whole five-pool net was on the
    table for one project line as one draw. Under v8 each pool spares its own half, and the
    second pool answers as a step of its own rather than as an overflow of the first.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _other_own, other_pool = sites["MWH"]
        _stock(db, product, pool, on_hand=15)
        _stock(db, product, other_pool, on_hand=100)
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)

    reserves = {c["source_location"]: c for c in components if c["kind"] == "reserve"}
    assert set(reserves) == {pool.warehouse_code, other_pool.warehouse_code}
    assert reserves[pool.warehouse_code]["qty"] == "7", "BRW's own share, and no more"
    assert reserves[other_pool.warehouse_code]["qty"] == "33", "MWH covers the remainder"
    assert sum(Decimal(c["qty"]) for c in components) == Decimal("40")


def test_another_sites_pool_free_floor_is_spent_once_across_the_whole_walk():
    """AC-N.12 (R-N leftover, 3 Sep 2026): every pool's FREE FLOOR is one ledger.

    `compose_lines` carried a running balance for the asking bin's OWN site pool and one
    for each pool's project SHARE, and nothing at all for another pool's free floor - it
    was re-read live on every line. R-N made that path the common one: step 0 now walks
    the whole chain, so two lines of one walk each reached WH3 and each were told it held
    all 5 of its floor, and the walk promised 10 off a pool holding 5.

    Here MWH's pool holds 5 on hand with 600 on the water, so its allowance is 302 and its
    floor is 5 - the shape where the share ledger cannot stand in for the floor. The first
    line takes the 5; the second must be offered NOTHING by the pool and buy.
    """
    near = date.today() + timedelta(days=10)
    far = date.today() + timedelta(days=17)
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, own_pool = sites["BRW"]
        _other_own, other_pool = sites["MWH"]
        _stock(db, product, own_pool, on_hand=0)
        _stock(db, product, other_pool, on_hand=5)
        # 600 on the water at MWH's pool: the pool's AVAILABLE (and so its allowance) is
        # far above the 5 units actually standing on its floor.
        _spo_line(db, product, other_pool, qty=600, arrives=far + timedelta(days=30))
        db.commit()

        from tests.scm.test_ladder_v6_order_unit import _seed_order

        _core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[(1, "5", own, near), (2, "5", own, far)],
        )
        lines = {
            line["line_no"]: line
            for line in ProjectSupplyService(db).proposal_for(order)["lines"]
        }

    stated = {
        line_no: [(c["kind"], c["qty"], c["source_location"]) for c in line["components"]]
        for line_no, line in lines.items()
    }
    assert stated[1] == [("reserve", "5", other_pool.warehouse_code)], (
        "the first line takes the whole of MWH's floor"
    )
    assert stated[2] == [("buy", "5", None)], (
        "and the second is offered none of it again - one ledger per pool floor"
    )


def test_a_second_sites_own_pool_still_nets_its_own_claim_after_an_earlier_units_raw_seed():
    """Review fix round S2: `pool_free_left` seeded a pool's floor RAW the first time any
    unit's chain reached it - including a unit for which that pool is only an OTHER pool,
    read unclaimed - and never revisited it once seeded. The pool's OWNING unit then read
    that stale, unclaimed figure back off the ledger instead of its OWN netted floor.

    BRW's own bin is walked first (line 1) and its chain reaches MWH's pool as one of its
    OTHERS - raw, 10 on hand, seeded into the ledger unclaimed. MWH's own bin is walked
    second (line 2): a separate order's line sits directly on MWH's pool, due sooner, and
    claims 3 of its 10 ahead of line 2 - so line 2's own floor there is 7, not 10. Without
    the fix the ledger still says 10 and line 2's 8 comes whole off the pool; with it, line
    2's own reading intersects the ledger the moment its own seeding runs, and only 7 do.
    """
    near = date.today() + timedelta(days=10)
    far = date.today() + timedelta(days=17)
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own1, pool1 = sites["BRW"]
        own2, pool2 = sites["MWH"]
        _stock(db, product, pool1, on_hand=0)
        _stock(db, product, pool2, on_hand=10)
        # 20 on the water at MWH's pool, so its ALLOWANCE (available x share) is well above
        # the 7 its floor actually nets to - the floor, not the share, is what is under
        # test here.
        _spo_line(db, product, pool2, qty=20, arrives=far + timedelta(days=30))
        # A SEPARATE order's line, held directly at MWH's own pool and due sooner, so it
        # ranks ahead of line 2 and claims 3 of the pool's floor.
        theirs = _core_so(db, company_id)
        _core_line(db, theirs, product, pool2, qty_ordered="3", required_date=near)
        db.commit()

        from tests.scm.test_ladder_v6_order_unit import _seed_order

        _core, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[(1, "1", own1, far), (2, "8", own2, far)],
        )
        lines = {
            line["line_no"]: line
            for line in ProjectSupplyService(db).proposal_for(order)["lines"]
        }

    stated = {
        line_no: [(c["kind"], c["qty"], c["source_location"]) for c in line["components"]]
        for line_no, line in lines.items()
    }
    assert stated[2] == [
        ("reserve", "7", pool2.warehouse_code),
        ("buy", "1", None),
    ], "MWH's own claimed 3 units must never be offered to the line that owns its pool"


def test_confirm_reads_the_same_claim_netting_as_the_proposal_did():
    """Review fix round S2, the CONFIRM-time counterpart: `_check_line` reads its pool
    capacity through `_CapacityLedger`, which has the identical seed-once shape as
    `pool_free_left` above - an earlier line's chain reaches this pool as one of its
    OTHERS and seeds the ledger unclaimed, and the line that owns the pool then reads that
    stale figure back instead of its own claim-netted one. Same world as the proposal-time
    test above, checked at the write path the board actually calls.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    near = date.today() + timedelta(days=10)
    far = date.today() + timedelta(days=17)
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own1, pool1 = sites["BRW"]
        own2, pool2 = sites["MWH"]
        _stock(db, product, pool1, on_hand=0)
        _stock(db, product, pool2, on_hand=10)
        _spo_line(db, product, pool2, qty=20, arrives=far + timedelta(days=30))
        theirs = _core_so(db, company_id)
        _core_line(db, theirs, product, pool2, qty_ordered="3", required_date=near)
        db.commit()

        from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody
        from tests.scm.test_ladder_v6_order_unit import _seed_order

        _core, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[(1, "1", own1, far), (2, "8", own2, far)],
        )
        lines_by_no = {
            line.line_no: line for line in ProjectSupplyService(db).lines_of(str(order.id))
        }

        # Asking for the whole 8 holds only 7, the line's own netted floor - not the
        # ledger's stale, unclaimed 10 - and records the 1 unit short on that line.
        result = ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(project_line_id=lines_by_no[1].id, buy_qty="1"),
                    ConfirmLine(
                        project_line_id=lines_by_no[2].id,
                        reserve=[{"warehouse_id": str(pool2.id), "qty": "8"}],
                    ),
                ]
            ),
            actor_user_id=eling,
        )
        [notice] = result["stock_short_notices"]
        assert notice["line_no"] == 2, notice
        assert notice["reason"] == (
            f"Reserve 8 at {pool2.warehouse_code} confirmed as decided; only 7 free at "
            f"{pool2.warehouse_code} when confirmed, 1 short"
        ), notice
        from app.models.project_so import SOLineAllocation

        held = [
            (str(a.so_line_id), str(a.warehouse_id), Decimal(str(a.qty)))
            for a in db.query(SOLineAllocation)
            .filter(SOLineAllocation.so_line_id == lines_by_no[2].id)
            .all()
        ]
        assert held == [(str(lines_by_no[2].id), str(pool2.id), Decimal("7"))], held

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own1, pool1 = sites["BRW"]
        own2, pool2 = sites["MWH"]
        _stock(db, product, pool1, on_hand=0)
        _stock(db, product, pool2, on_hand=10)
        _spo_line(db, product, pool2, qty=20, arrives=far + timedelta(days=30))
        theirs = _core_so(db, company_id)
        _core_line(db, theirs, product, pool2, qty_ordered="3", required_date=near)
        db.commit()

        from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody
        from tests.scm.test_ladder_v6_order_unit import _seed_order

        _core, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[(1, "1", own1, far), (2, "8", own2, far)],
        )
        lines_by_no = {
            line.line_no: line for line in ProjectSupplyService(db).lines_of(str(order.id))
        }

        # The netted 7 itself is exactly what the line's own pool may still give it.
        result = ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(project_line_id=lines_by_no[1].id, buy_qty="1"),
                    ConfirmLine(
                        project_line_id=lines_by_no[2].id,
                        reserve=[{"warehouse_id": str(pool2.id), "qty": "7"}],
                        buy_qty="1",
                    ),
                ]
            ),
            actor_user_id=eling,
        )
    assert result["lines_decided"] == 2
    assert result["exceptions"] == []


def test_a_pool_with_negative_available_offers_nothing_not_a_floor_of_zero_read_as_some():
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=50)
        # The pool's own book already owes 80 there, ranked ahead of nobody in particular -
        # its SIGNED availability (on hand - SO qty) is -30.
        theirs_so, _theirs = _core_so(db, company_id), None
        _core_line(db, theirs_so, product, pool, qty_ordered="80")
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="10",
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)

    assert not any(c["kind"] == "reserve" for c in components)
    assert len(components) == 1
    assert components[0]["kind"] == "buy"
    assert components[0]["qty"] == "10"


# --------------------------------------------------------------------------- rung 3: group take


def test_group_take_covers_the_line_from_a_sibling_location_mwh_bb():
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        sibling, _sibling_pool = sites["MWH"]
        _stock(db, product, sibling, on_hand=50)
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)

    assert len(components) == 1
    assert components[0]["kind"] == "reserve"
    assert components[0]["rung"] == "group_take"
    assert components[0]["source_location"] == sibling.warehouse_code
    assert components[0]["qty"] == "50"


# --------------------------------------------------------------------------- rung 6: whole-line


def test_whole_line_rule_applies_to_what_is_left_after_the_pools_share():
    """LADDER V8 (R-C), which is where the whole-line rule now bites: the site pool spares
    its half of 213 - 106 - as its own sub-unit, and the REMAINING 252 is what the rest of
    the ladder must cover whole or not at all. Nothing else can, so 252 is bought.

    Under v7.1 the pool covered the unit whole or gave nothing, so this same case read a
    single Buy of 358 with 213 sitting in the pool.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=213)
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="358",
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)

    assert [(c["kind"], c["qty"]) for c in components] == [
        ("reserve", "106"),
        ("buy", "252"),
    ]
    assert components[0]["source_location"] == pool.warehouse_code
    assert "252" in components[1]["reason"], (
        "the Buy states what is left after the share, not the whole line"
    )


def test_whole_line_rule_a_full_cover_keeps_its_composition():
    """LADDER V8: 716 in the pool, because the line may have half of it (R-B) and half of
    716 is the 358 this case is about covering whole."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=716)
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="358",
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)

    assert len(components) == 1
    assert components[0]["kind"] == "reserve"
    assert components[0]["qty"] == "358"


# ------------------------------------------------------------- rung 0b: the reserve window


def _lead_time(db, product, supplier_days: int) -> None:
    """State a supplier agreement for the product, which is where the lead time comes from."""
    import uuid as _uuid

    from app.models.procurement import ProductSupplier, Supplier

    supplier = Supplier(
        id=str(_uuid.uuid4()),
        supplier_code=f"ZZT-SUP-{_uuid.uuid4().hex[:8]}".upper(),
        supplier_name="ZZT lead-time supplier",
    )
    db.add(supplier)
    db.flush()
    db.add(
        ProductSupplier(
            id=str(_uuid.uuid4()),
            product_id=product.id,
            supplier_id=supplier.id,
            standard_lead_time_days=supplier_days,
        )
    )
    db.commit()


def test_a_line_beyond_the_reserve_window_buys_rather_than_borrowing_a_nearer_orders_stock():
    """The rule, end to end: a line due long after purchasing could simply buy for it does
    not take stock a nearer-dated order is holding. Its whole quantity is a Buy, and the
    reason names the window rather than the arithmetic."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _lead_time(db, product, 30)

        # A donor at the same location, due later still - the rung-4 candidate this line
        # would take from if it were inside its window.
        _seed_line(
            db, company_id, project, product, own, qty_ordered="145",
            required_date=date.today() + timedelta(days=400), line_no=2,
            so_number="SO371334",
        )
        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="145",
            # 30 days of lead time + 14 of buffer = a window of 44 days; this is well beyond.
            required_date=date.today() + timedelta(days=300), line_no=1,
            so_number="SO331506",
        )

        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert len(components) == 1
    assert components[0]["kind"] == "buy"
    assert components[0]["qty"] == "145"
    assert "beyond the lead time window" in components[0]["reason"]


def test_a_line_beyond_the_reserve_window_is_offered_no_donor_to_borrow_from():
    """Not composed AND not offered. The sheet ranked a donor list beside the line anyway, so
    Amend still put the one move the rule forbids in front of the planner - the same defect
    the board showed on SO414341, on the other screen."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        outside = _warehouse(db, f"ZZTHP-{_uid()[:4]}")
        _stock(db, product, outside, on_hand=650)
        _lead_time(db, product, 90)

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="441",
            # 174 days out, against 90 of lead time plus the 14-day buffer.
            required_date=date.today() + timedelta(days=174),
        )
        line = ProjectSupplyService(db).proposal_for(order)["lines"][0]

    assert line["components"][0]["kind"] == "buy"
    assert line["borrow_candidates"] == []


def test_a_line_beyond_the_window_buys_the_whole_line_however_much_sits_beside_it():
    """AC-L1: "if delivery date exceed lead time, directly buy". v2 still walked the two
    surplus rungs for such a line; v3 walks nothing. A pool holding exactly what the line
    owes is left alone, because a line 300 days out has months for a purchase order and the
    stock is kept for the orders that do not."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=358)
        _lead_time(db, product, 30)
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="358",
            required_date=date.today() + timedelta(days=300),
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert len(components) == 1
    assert components[0]["kind"] == "buy"
    assert components[0]["qty"] == "358"
    assert "beyond the lead time window" in components[0]["reason"]


def test_a_line_inside_the_window_is_offered_the_donor_the_far_line_is_not():
    """The near line still SEES the donor; the far line does not. Neither has it proposed:
    borrowing another sales order's committed quantity is a person's pick (AC-L3), so the
    near line reads Buy with the donor beside it, waiting in Amend."""
    with blank_session() as db:
        borrow_from_order_on(db)
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _lead_time(db, product, 30)

        _seed_line(
            db, company_id, project, product, own, qty_ordered="145",
            required_date=date.today() + timedelta(days=40), line_no=2,
            so_number="SO371334",
        )
        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="145",
            required_date=date.today() + timedelta(days=10), line_no=1,
            so_number="SO331506",
        )

        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)
        candidates = proposal["lines"][0]["borrow_candidates"]

    assert [c["kind"] for c in components] == ["buy"]
    assert any(c.get("donor_so_number") == "SO371334" for c in candidates)


def test_a_product_nobody_states_a_lead_time_for_uses_the_documented_default():
    """No supplier agreement, no measured performance: 90 days (the median of the live book)
    plus the 14-day buffer, so a line 300 days out is still outside its window and a line 60
    days out is still inside it."""
    from app.services.scm.front_planning_engine import DEFAULT_LEAD_TIME_DAYS

    assert DEFAULT_LEAD_TIME_DAYS == 90

    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        # Deliberately no `_lead_time` call.

        _seed_line(
            db, company_id, project, product, own, qty_ordered="145",
            required_date=date.today() + timedelta(days=400), line_no=2,
            so_number="SO371334",
        )
        far, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="145",
            required_date=date.today() + timedelta(days=300), line_no=1,
            so_number="SO331506",
        )
        far_components = _components(ProjectSupplyService(db).proposal_for(far))

    assert far_components[0]["kind"] == "buy"
    assert "beyond the lead time window" in far_components[0]["reason"]


# ------------------------------------------ group borrow: offered, never a rung (AC-L3)


def test_group_borrow_is_offered_not_proposed_and_a_manual_pick_raises_the_order_back():
    """AC-L3 + AC-L6, ruled 25 August 2026: the engine never composes a group borrow, so a
    line the group and the pool cannot cover reads Buy - with the donor offered beside it.
    Picking that donor in Amend and confirming still raises the order-back on the donor's
    own line, at the donor's own date, exactly as it always did."""
    from app.models.project_so import IV_BORROW_SHORTFALL, OrderInquiryRow
    from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody

    with blank_session() as db:
        borrow_from_order_on(db)
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]

        # The donor: another sales order at the SAME location, due LATER (so it ranks
        # below on need_by_date, the default seeded weight).
        donor_order, _donor_line, donor_cso, donor_cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="145",
            required_date=REQUIRED_DATE + timedelta(days=60), line_no=2,
            so_number="SO371334",
        )

        # This line, due SOONER - ranked ahead of the donor.
        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="145",
            required_date=REQUIRED_DATE, line_no=1, so_number="SO331506",
        )

        proposal = ProjectSupplyService(db).proposal_for(order)
        components = proposal["lines"][0]["components"]
        assert [c["kind"] for c in components] == ["buy"]
        candidates = proposal["lines"][0]["borrow_candidates"]
        donor_candidate = next(
            c for c in candidates if c.get("donor_so_number") == "SO371334"
        )
        assert donor_candidate["rung"] == "group_borrow"
        assert donor_candidate["donor_line_no"] == 2
        # B4: urgency = the DONOR's own required date, so the order-back the manual pick
        # raises is dated by the order it is taken from, not by the one taking it.
        assert donor_candidate["donor_required_date"] == REQUIRED_DATE + timedelta(days=60)

        service = ProjectSupplyService(db)
        result = service.confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=line.id,
                        borrow=[
                            {
                                "source": "other_location",
                                "warehouse_id": str(own.id),
                                "qty": "145",
                                "reason": "Authorised by agent CYNDI: urgent site delivery.",
                                "donor_core_line_id": str(donor_cline.id),
                                "donor_so_number": "SO371334",
                                "donor_line_no": 2,
                                "donor_required_date": (
                                    REQUIRED_DATE + timedelta(days=60)
                                ).isoformat(),
                            }
                        ],
                    )
                ]
            ),
            actor_user_id=eling,
        )
        assert result["inquiry_rows_created"] == 1

        rows = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
            .all()
        )
        assert len(rows) == 1
        row = rows[0]
        assert str(row.qty) in ("145", "145.0000")
        assert row.stock_location == own.warehouse_code
        assert "SO371334" in row.note
        assert "line 2" in row.note
        assert row.delivery_date == REQUIRED_DATE + timedelta(days=60)


def test_a_same_agent_donor_is_offered_even_when_ranked_above_but_never_auto_proposed():
    with blank_session() as db:
        borrow_from_order_on(db)
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        agent = _agent(db, f"JEREMY{_uid()[:4]}", location_group=_group)
        db.commit()

        # The donor is due SOONER (ranked ABOVE this line) but shares the same agent.
        _donor_order, _donor_line, donor_cso, _donor_cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE - timedelta(days=30), line_no=2,
            so_number="SO900001", sales_agent_id=agent.id,
        )
        order, _line_obj, cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE, line_no=1, so_number="SO900002",
            sales_agent_id=agent.id,
        )
        db.commit()

        proposal = ProjectSupplyService(db).proposal_for(order)
        components = proposal["lines"][0]["components"]
        # Not auto-proposed: a higher-ranked donor never composes, whole-line falls to Buy.
        assert len(components) == 1
        assert components[0]["kind"] == "buy"

        candidates = proposal["lines"][0]["borrow_candidates"]
        same_agent_rows = [c for c in candidates if c.get("same_agent")]
        assert same_agent_rows, "same-agent donor must still be OFFERED"
        assert same_agent_rows[0]["donor_so_number"] == "SO900001"
        assert same_agent_rows[0].get("lower_ranked") is False


def test_a_higher_ranked_donor_of_ANOTHER_agent_is_not_offered_at_all():
    """AC-L6 (section 1c): "a donor sharing the line's sales agent is offered at ANY rank
    ... another agent's order only when ranked below".

    The offer list used to carry every donor in the group. A higher-ranked order belonging
    to somebody else is not this planner's to take - offering it puts a row on the screen
    whose only honest use is to phone another agent and ask, which is not what the dialog
    says it is for.
    """
    with blank_session() as db:
        borrow_from_order_on(db)
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        mine = _agent(db, f"CYNDI{_uid()[:4]}", location_group=_group)
        theirs = _agent(db, f"JEREMY{_uid()[:4]}", location_group=_group)
        db.commit()

        # Ranked ABOVE this line (due sooner), and somebody else's.
        _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE - timedelta(days=30), line_no=2,
            so_number="SO910001", sales_agent_id=theirs.id,
        )
        # Ranked BELOW this line, and also somebody else's: that one IS offered.
        _seed_line(
            db, company_id, project, product, own, qty_ordered="70",
            required_date=REQUIRED_DATE + timedelta(days=30), line_no=3,
            so_number="SO910002", sales_agent_id=theirs.id,
        )
        order, _line_obj, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE, line_no=1, so_number="SO910003",
            sales_agent_id=mine.id,
        )
        db.commit()

        candidates = ProjectSupplyService(db).proposal_for(order)["lines"][0][
            "borrow_candidates"
        ]

    offered = {
        c["donor_so_number"] for c in candidates if c.get("rung") == "group_borrow"
    }
    assert offered == {"SO910002"}


# --------------------------------------------------------------------- rung 4: cross-group


def test_cross_group_borrow_is_no_longer_capped_by_a_quantity_limit():
    """v7.1 R5 (migration 443): the small-quantity cap is dropped - any ownership group may
    donate - so the line that used to be refused for exceeding it now borrows whole.

    What still refuses a cross-group borrow is the whole-line rule and the donor group's own
    net, both asserted in their own tests. This one pins that SIZE alone no longer does.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        outside = _warehouse(db, f"ZZTHP-{_uid()[:4]}")
        _stock(db, product, outside, on_hand=999)
        priority.create_revision(
            db, name="zzt-cap", factors={}, demand_class_weights={},
            reorder_coverage_until=None,
        )
        db.commit()

        # Two INDEPENDENT groups, so the small and the big line never compete for the
        # same group_borrow pile - each is judged on its own.
        _group_1, sites_1 = _group_sites(db)
        own_small, _pool_1 = sites_1["BRW"]
        _group_2, sites_2 = _group_sites(db)
        own_big, _pool_2 = sites_2["BRW"]

        # A small line: 15.
        order_small, _l1, _c1, _cl1 = _seed_line(
            db, company_id, project, product, own_small, qty_ordered="15", line_no=1,
        )
        proposal_small = ProjectSupplyService(db).proposal_for(order_small)
        small_components = proposal_small["lines"][0]["components"]

        # A bigger one: 25, which the retired cap of 20 used to refuse.
        order_big, _l2, _c2, _cl2 = _seed_line(
            db, company_id, project, product, own_big, qty_ordered="25", line_no=1,
        )
        proposal_big = ProjectSupplyService(db).proposal_for(order_big)
        big_components = proposal_big["lines"][0]["components"]

    assert len(small_components) == 1
    assert small_components[0]["kind"] == "reserve"
    assert small_components[0]["rung"] == "group_take"
    assert small_components[0]["source_location"] == outside.warehouse_code

    assert len(big_components) == 1
    # v7.1 (R5): size alone no longer refuses it, and free stock outside the group is a
    # RESERVE at step 1 rather than a Borrow - free means owed to nobody.
    assert big_components[0]["kind"] == "reserve"
    assert big_components[0]["rung"] == "group_take"
    assert big_components[0]["qty"] == "25"


# --------------------------------------------------------------------------- review fixes (B3/S1/S2/S3/S8, nits)


def test_group_borrow_from_a_sibling_location_raises_the_order_back_only_once():
    """B3: a group borrow whose donor sits at a SIBLING location (not this line's own)
    used to be fed BOTH into the unconditional order-back AND the location-pile
    shortfall loop, so `donor_availability`'s own netting of the donor's `so_qty` looked
    like a second hole and raised a second `IV_BORROW_SHORTFALL` row for the same take."""
    from app.models.project_so import IV_BORROW_SHORTFALL, OrderInquiryRow
    from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody

    with blank_session() as db:
        borrow_from_order_on(db)
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        sibling, _sibling_pool = sites["MWH"]

        donor_order, _donor_line, _dcso, donor_cline = _seed_line(
            db, company_id, project, product, sibling, qty_ordered="90",
            required_date=REQUIRED_DATE + timedelta(days=60), line_no=3,
            so_number="SO500001",
        )
        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE, line_no=1, so_number="SO500002",
        )

        proposal = ProjectSupplyService(db).proposal_for(order)
        # Offered, never proposed (AC-L3): the planner picks it in Amend.
        assert any(
            c.get("donor_so_number") == "SO500001"
            and c["warehouse_code"] == sibling.warehouse_code
            for c in proposal["lines"][0]["borrow_candidates"]
        )

        ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=line.id,
                        borrow=[
                            {
                                "source": "other_location",
                                "warehouse_id": str(sibling.id),
                                "qty": "90",
                                "reason": "Authorised by agent CYNDI: urgent site delivery.",
                                "donor_core_line_id": str(donor_cline.id),
                                "donor_so_number": "SO500001",
                                "donor_line_no": 3,
                            }
                        ],
                    )
                ]
            ),
            actor_user_id=eling,
        )

        rows = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
            .all()
        )
        assert len(rows) == 1, "a sibling-location group borrow must raise exactly one order-back"


def test_borrow_shortfall_netting_consumes_an_actioned_row_once_not_per_entry():
    """B3: `_raise_borrow_shortfalls`'s `placed` pool is CONSUMED as each shortfall entry
    draws on it, not restated in full against every entry sharing its (item, location)
    key. Two lines of one confirmation each open their own hole at the SAME donor
    location; purchasing has only actioned 50 of the 90 total. Netting the WHOLE 50 off
    BOTH entries (the bug) makes the second entry's 40 look covered when only the first
    entry's 50 was; consuming the 50 once (the fix) still raises the 40 that is real.
    """
    from app.models.project_so import IV_BORROW_SHORTFALL, INQUIRY_ACTIONED, OrderInquiryRow
    from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        sibling, _sibling_pool = sites["MWH"]

        donor_a_order, _da, _dacso, donor_a_cline = _seed_line(
            db, company_id, project, product, sibling, qty_ordered="200",
            required_date=REQUIRED_DATE + timedelta(days=90), line_no=1,
            so_number="SO860001",
        )
        donor_b_order, _db_, _dbcso, donor_b_cline = _seed_line(
            db, company_id, project, product, sibling, qty_ordered="200",
            required_date=REQUIRED_DATE + timedelta(days=90), line_no=1,
            so_number="SO860002",
        )

        core_so = _core_so(db, company_id)
        core_so.so_number = "SO860003"
        db.flush()
        core_line_1 = _core_line(
            db, core_so, product, own, qty_ordered="50", required_date=REQUIRED_DATE,
        )
        core_line_2 = _core_line(
            db, core_so, product, own, qty_ordered="40", required_date=REQUIRED_DATE,
        )
        order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
        line_1 = _project_line(db, order, line_no=1, product=product, core_line=core_line_1)
        line_2 = _project_line(db, order, line_no=2, product=product, core_line=core_line_2)
        db.commit()

        def borrow_payload():
            return [
                ConfirmLine(
                    project_line_id=line_1.id,
                    borrow=[
                        {
                            "source": "other_location",
                            "warehouse_id": str(sibling.id),
                            "qty": "50",
                            "reason": "Group borrow.",
                            "donor_core_line_id": str(donor_a_cline.id),
                            "donor_so_number": "SO860001",
                            "donor_line_no": 1,
                        }
                    ],
                ),
                ConfirmLine(
                    project_line_id=line_2.id,
                    borrow=[
                        {
                            "source": "other_location",
                            "warehouse_id": str(sibling.id),
                            "qty": "40",
                            "reason": "Group borrow.",
                            "donor_core_line_id": str(donor_b_cline.id),
                            "donor_so_number": "SO860002",
                            "donor_line_no": 1,
                        }
                    ],
                ),
            ]

        service = ProjectSupplyService(db)
        service.confirm(
            order, ConfirmSupplyBody(lines=borrow_payload()), actor_user_id=eling,
        )
        db.commit()

        rows = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL, OrderInquiryRow.state == "raised")
            .all()
        )
        assert len(rows) == 2
        by_qty = {str(r.qty).split(".")[0]: r for r in rows}
        # Purchasing has only actioned the 50 - not the 40.
        by_qty["50"].state = INQUIRY_ACTIONED
        db.commit()

        service_2 = ProjectSupplyService(db)
        service_2.confirm(
            order, ConfirmSupplyBody(lines=borrow_payload()), actor_user_id=eling,
        )
        db.commit()

        still_raised = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL, OrderInquiryRow.state == "raised")
            .all()
        )
        # The 50 already placed nets out; the 40 that was NEVER placed is raised again.
        assert len(still_raised) == 1, still_raised
        assert str(still_raised[0].qty).split(".")[0] == "40"


def test_a_second_confirmation_cannot_borrow_what_an_earlier_one_already_holds():
    """S1: `_donor_line_ledger` only guarded two lines of the SAME confirmation. A
    second, separate confirmation re-read the donor's live open quantity fresh and could
    borrow the same units again - seeded now net of what an earlier confirmed decision
    already holds from that donor line (`_group_borrow_held_qty`)."""
    from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]

        donor_order, _donor_line, _dcso, donor_cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE + timedelta(days=60), line_no=9,
            so_number="SO600001",
        )
        order_a, line_a, _cso_a, _cline_a = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE, line_no=1, so_number="SO600002",
        )
        order_b, line_b, _cso_b, _cline_b = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE, line_no=1, so_number="SO600003",
        )

        borrow_payload = [
            {
                "source": "other_location",
                "warehouse_id": str(own.id),
                "qty": "90",
                "reason": "Group borrow.",
                "donor_core_line_id": str(donor_cline.id),
                "donor_so_number": "SO600001",
                "donor_line_no": 9,
            }
        ]

        ProjectSupplyService(db).confirm(
            order_a,
            ConfirmSupplyBody(
                lines=[ConfirmLine(project_line_id=line_a.id, borrow=list(borrow_payload))]
            ),
            actor_user_id=eling,
        )
        db.commit()

        with pytest.raises(AppException):
            ProjectSupplyService(db).confirm(
                order_b,
                ConfirmSupplyBody(
                    lines=[
                        ConfirmLine(project_line_id=line_b.id, borrow=list(borrow_payload))
                    ]
                ),
                actor_user_id=eling,
            )


def test_a_sibling_line_of_the_same_sales_order_is_never_an_auto_donor():
    """S2: borrowing from another line of the SAME sales order and raising an
    order-back against it is a borrow "against itself" - never offered, auto or not."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]

        core_so = _core_so(db, company_id)
        core_so.so_number = "SO700001"
        db.flush()
        donor_core = _core_line(
            db, core_so, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE + timedelta(days=60),
        )
        asking_core = _core_line(
            db, core_so, product, own, qty_ordered="90", required_date=REQUIRED_DATE,
        )
        order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
        _project_line(db, order, line_no=2, product=product, core_line=donor_core)
        asking_line = _project_line(db, order, line_no=1, product=product, core_line=asking_core)
        db.commit()

        proposal = ProjectSupplyService(db).proposal_for(order)
        by_id = {l["project_line_id"]: l for l in proposal["lines"]}
        asking = by_id[str(asking_line.id)]

    assert not any(c["kind"] == "borrow" for c in asking["components"])
    assert asking["components"][0]["kind"] == "buy"
    assert not any(
        c.get("donor_core_line_id") == str(donor_core.id)
        for c in asking["borrow_candidates"]
    ), "a sibling line of the SAME sales order must never be offered as a donor"


def test_a_line_covered_by_another_active_decision_is_not_offered_as_a_donor():
    """S2, parity with `_pile_book`'s own `_decided_elsewhere` exclusion: a line already
    covered by another active decision is not a stable donor for a different one."""
    from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]

        donor_order, donor_line, _dcso, donor_cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE + timedelta(days=60), line_no=5,
            so_number="SO800001",
        )
        ProjectSupplyService(db).confirm(
            donor_order,
            ConfirmSupplyBody(
                lines=[ConfirmLine(project_line_id=donor_line.id, buy_qty="90")]
            ),
            actor_user_id=eling,
        )
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE, line_no=1, so_number="SO800002",
        )

        proposal = ProjectSupplyService(db).proposal_for(order)
        components = proposal["lines"][0]["components"]

    assert not any(c["kind"] == "borrow" for c in components)
    assert components[0]["kind"] == "buy"
    assert not any(
        c.get("donor_core_line_id") == str(donor_cline.id)
        for c in proposal["lines"][0]["borrow_candidates"]
    )


def test_group_borrow_donors_at_one_location_prefer_the_lowest_ranked_over_the_biggest():
    """Nit: within one location, `_group_borrow_donors` must OFFER the LOWEST-ranked
    donor first (the safest one to draw from), not the one holding the most."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]

        # donor_far ranks BELOW donor_near (a much later required date) at the SAME
        # location, despite holding less - it is the safer donor to draw from first.
        _far_order, _far_line, _far_cso, far_cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="10",
            required_date=REQUIRED_DATE + timedelta(days=120), line_no=2,
            so_number="SO840001",
        )
        _near_order, _near_line, _near_cso, _near_cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE + timedelta(days=10), line_no=3,
            so_number="SO840002",
        )
        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="10",
            required_date=REQUIRED_DATE, line_no=1, so_number="SO840003",
        )

        proposal = ProjectSupplyService(db).proposal_for(order)
        candidates = [
            c
            for c in proposal["lines"][0]["borrow_candidates"]
            if c.get("rung") == "group_borrow"
        ]

    # The order is what matters, not a composition: nothing is auto-composed any more
    # (AC-L3), so "safest first" is a statement about the list a person reads.
    assert candidates
    assert candidates[0]["donor_core_line_id"] == str(far_cline.id)


def test_group_borrow_refuses_a_donor_line_of_a_different_product():
    """S8: the donor line named has to hold the SAME product this line is asking for."""
    from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        other_product = _product(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]

        core_so = _core_so(db, company_id)
        core_so.so_number = "SO810001"
        db.flush()
        donor_core = _core_line(db, core_so, other_product, own, qty_ordered="90")
        db.commit()

        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90", line_no=1,
        )

        with pytest.raises(AppException):
            ProjectSupplyService(db).confirm(
                order,
                ConfirmSupplyBody(
                    lines=[
                        ConfirmLine(
                            project_line_id=line.id,
                            borrow=[
                                {
                                    "source": "other_location",
                                    "warehouse_id": str(own.id),
                                    "qty": "90",
                                    "reason": "Group borrow.",
                                    "donor_core_line_id": str(donor_core.id),
                                }
                            ],
                        )
                    ]
                ),
                actor_user_id=eling,
            )


def test_group_borrow_refuses_a_donor_line_at_a_bin_in_no_ownership_group():
    """S8, as ladder v7.1 leaves it (R5): ANY ownership group may donate now, so what is
    refused is a donor line at a bin that is in no group AT ALL and is not a site pool -
    nothing nets it, and the order-back would be owed to a place with no book.

    A SITE POOL donor is allowed, because step 4b borrows a later pool order's on hand
    (R34); `tests/scm/test_ladder_v7_borrow.py` pins that end to end.
    """
    from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        # A bare bin nobody's `pool_warehouse_id` points at: no group suffix and not a pool.
        nowhere = _warehouse(db, f"ZZTBARE{_uid()[:6]}"[:20])

        core_so = _core_so(db, company_id)
        core_so.so_number = "SO820001"
        db.flush()
        donor_core = _core_line(db, core_so, product, nowhere, qty_ordered="90")
        db.commit()

        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90", line_no=1,
        )

        with pytest.raises(AppException):
            ProjectSupplyService(db).confirm(
                order,
                ConfirmSupplyBody(
                    lines=[
                        ConfirmLine(
                            project_line_id=line.id,
                            borrow=[
                                {
                                    "source": "other_location",
                                    "warehouse_id": str(nowhere.id),
                                    "qty": "90",
                                    "reason": "Group borrow.",
                                    "donor_core_line_id": str(donor_core.id),
                                }
                            ],
                        )
                    ]
                ),
                actor_user_id=eling,
            )


def test_group_borrow_refuses_a_donor_line_that_is_no_longer_open_demand():
    """S8: a donor line already fully delivered is no longer open demand and cannot be
    borrowed from, reusing `is_open_demand()`."""
    from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]

        core_so = _core_so(db, company_id)
        core_so.so_number = "SO830001"
        db.flush()
        donor_core = _core_line(
            db, core_so, product, own, qty_ordered="90", qty_delivered="90",
        )
        db.commit()

        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90", line_no=1,
        )

        with pytest.raises(AppException):
            ProjectSupplyService(db).confirm(
                order,
                ConfirmSupplyBody(
                    lines=[
                        ConfirmLine(
                            project_line_id=line.id,
                            borrow=[
                                {
                                    "source": "other_location",
                                    "warehouse_id": str(own.id),
                                    "qty": "90",
                                    "reason": "Group borrow.",
                                    "donor_core_line_id": str(donor_core.id),
                                }
                            ],
                        )
                    ]
                ),
                actor_user_id=eling,
            )


def test_group_pile_runs_o1_queries_for_a_board_of_n_lines_of_one_product_group():
    """S3: `_group_pile_members` is cached per (product, group), not per line - a board
    of N lines sharing one product/group must run its own query ONCE, not N times.

    Counted by the queries in this call graph that join `sales_agents` - two since ladder
    v7.1: `_group_pile_members`'s own ranked read, and the ONE assignment read the board and
    the Stock Debt view share (R21), which names the agent on every demand line so a borrow
    can say whose order it is taking. Both are per (product, group) or per walk; neither
    scales with the line count, which is what this test exists to catch. A real
    `before_cursor_execute` listener, not an inference from timing.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]

        # A donor, so the group pile is a real read with more than one row to rank.
        _donor_order, _donor_line, _dcso, _dcline = _seed_line(
            db, company_id, project, product, own, qty_ordered="500",
            required_date=REQUIRED_DATE + timedelta(days=120), line_no=99,
            so_number="SO850000",
        )

        # N lines of ONE order, same product/location - one group pile.
        core_so = _core_so(db, company_id)
        core_so.so_number = "SO850001"
        db.flush()
        order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
        n = 8
        for i in range(1, n + 1):
            core_line = _core_line(
                db, core_so, product, own, qty_ordered="10",
                required_date=REQUIRED_DATE + timedelta(days=i),
            )
            _project_line(db, order, line_no=i, product=product, core_line=core_line)
        db.commit()

        calls = {"group_pile": 0}

        def _count(conn, cursor, statement, parameters, context, executemany):
            if "sales_agents" in statement:
                calls["group_pile"] += 1

        connection = db.connection()
        event.listen(connection, "before_cursor_execute", _count)
        try:
            proposal = ProjectSupplyService(db).proposal_for(order)
        finally:
            event.remove(connection, "before_cursor_execute", _count)

    assert proposal["lines_total"] == n
    assert calls["group_pile"] == 2, calls


# --------------------------------------------------------- the donor ranking's tie-breaks


def _donor_row(**over):
    """A `_ranked` row with every key the sort reads, so a case states only what it varies."""
    row = {
        "source": "other_location",
        "warehouse_code": "BRW-IB",
        "warehouse_id": "wh-ib",
        "free_qty": "100",
        "available_after_need": "60",
        "available_qty": "80",
        "rank_index": None,
    }
    row.update(over)
    return row


def test_the_queue_position_breaks_a_tie_between_two_donor_lines_at_one_pile():
    """Two donor LINES at one pile agree about the pile, because availability is a fact
    about the pile and not about the line. The safest to take from is the one furthest DOWN
    the ranked queue - not the one holding the most, which is usually the nearest-dated."""
    ranked = ProjectSupplyService._ranked(
        [
            _donor_row(
                warehouse_code="MWH-BB", rung="group_borrow", donor_so_number="SO-NEAR",
                free_qty="90", rank_index=1,
            ),
            _donor_row(
                warehouse_code="MWH-BB", rung="group_borrow", donor_so_number="SO-FAR",
                free_qty="10", rank_index=7,
            ),
        ]
    )

    assert [row["donor_so_number"] for row in ranked] == ["SO-FAR", "SO-NEAR"]
    assert ranked[0]["recommended"] is True


def test_a_donor_lines_queue_position_never_pushes_free_stock_down_the_list():
    """The tie-break is between two ROWS THAT BOTH CARRY ONE. Free stock at a location has
    no queue position - there is no donor line to rank - and a sentinel standing in for the
    absence sorted it behind every group-borrow donor it tied with, which recommended
    somebody else's committed quantity over stock nobody had claimed."""
    ranked = ProjectSupplyService._ranked(
        [
            _donor_row(
                warehouse_code="MWH-BB", rung="group_borrow", donor_so_number="SO-DONOR",
                rank_index=7,
            ),
            _donor_row(warehouse_code="BRW-IB", source="other_location"),
        ]
    )

    assert [row["warehouse_code"] for row in ranked] == ["BRW-IB", "MWH-BB"]
    assert ranked[0]["recommended"] is True
    assert ranked[1]["recommended"] is False


def test_a_donor_project_hold_is_not_pushed_behind_a_donor_line_either():
    """The same absence, the other shape: a hold another PROJECT carries names no
    sales-order line, so it carries no queue position."""
    ranked = ProjectSupplyService._ranked(
        [
            _donor_row(
                warehouse_code="MWH-BB", rung="group_borrow", donor_so_number="SO-DONOR",
                rank_index=7,
            ),
            _donor_row(
                warehouse_code="JB", source="other_project",
                donor_project_ref="PRJ-0052 Seri Emas Phase 2",
            ),
        ]
    )

    assert [row["warehouse_code"] for row in ranked] == ["JB", "MWH-BB"]
    assert ranked[0]["recommended"] is True


def test_availability_still_outranks_the_queue_position():
    """The tie-break is a TIE-break: a donor that keeps more once this line is met still
    wins, whatever either row's queue position says."""
    ranked = ProjectSupplyService._ranked(
        [
            _donor_row(
                warehouse_code="MWH-BB", rung="group_borrow", donor_so_number="SO-DEEP",
                available_after_need="10", rank_index=9,
            ),
            _donor_row(
                warehouse_code="DC1-BB", rung="group_borrow", donor_so_number="SO-ROOMY",
                available_after_need="500", rank_index=2,
            ),
        ]
    )

    assert [row["donor_so_number"] for row in ranked] == ["SO-ROOMY", "SO-DEEP"]


# ------------------------------------ authorising a same-agent borrow (section 1c, AC-L6)


def _same_agent_world(db):
    """This line and a donor line of the SAME agent, the donor ranked ABOVE it.

    Ranked above is the whole point: a donor BELOW this line in the queue is ordinary
    group borrow and needs no special permission. A donor above it is only reachable
    because the agent who owns both orders can say so.
    """
    company_id, eling, project, product = _world(db)
    _group, sites = _group_sites(db)
    own, _pool = sites["BRW"]
    agent = _agent(db, f"CYNDI{_uid()[:4]}", location_group=_group)
    db.commit()

    _donor_order, _dl, _dcso, donor_cline = _seed_line(
        db, company_id, project, product, own, qty_ordered="90",
        required_date=REQUIRED_DATE - timedelta(days=30), line_no=2,
        so_number="SO920001", sales_agent_id=agent.id,
    )
    order, line, _cso, _cline = _seed_line(
        db, company_id, project, product, own, qty_ordered="90",
        required_date=REQUIRED_DATE, line_no=1, so_number="SO920002",
        sales_agent_id=agent.id,
    )
    db.commit()
    return company_id, eling, order, line, donor_cline, own


def _borrow_body(line, warehouse, donor_cline, reason):
    from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody

    return ConfirmSupplyBody(
        lines=[
            ConfirmLine(
                project_line_id=line.id,
                borrow=[
                    {
                        "source": "other_location",
                        "warehouse_id": str(warehouse.id),
                        "qty": "90",
                        "reason": reason,
                        "donor_core_line_id": str(donor_cline.id),
                        "donor_so_number": "SO920001",
                        "donor_line_no": 2,
                    }
                ],
            )
        ]
    )


def test_a_same_agent_donor_ranked_above_this_line_is_refused_without_an_authorisation():
    """AC-L6: the donor is offered at any rank BECAUSE the agent can authorise moving stock
    between her own orders - so confirming it has to say she did.

    Judged on the SERVER's own donor list, never on the payload's `same_agent` flag: that
    flag is a client claim, and the rule it gates is a permission.
    """
    with blank_session() as db:
        borrow_from_order_on(db)
        company_id, eling, order, line, donor_cline, own = _same_agent_world(db)

        with pytest.raises(AppException) as refused:
            ProjectSupplyService(db).confirm(
                order,
                _borrow_body(line, own, donor_cline, "The site is waiting."),
                actor_user_id=eling,
            )

    failing = refused.value.detail["failing_lines"]
    assert len(failing) == 1
    assert failing[0]["reason"] == (
        "SO920001 line 2 shares this line's sales agent and is ranked ahead of it, so it "
        "can only be borrowed with that agent's authorisation. Say who authorised it in "
        "the reason."
    )


def test_the_same_borrow_confirms_once_the_reason_names_who_authorised_it():
    with blank_session() as db:
        borrow_from_order_on(db)
        company_id, eling, order, line, donor_cline, own = _same_agent_world(db)

        result = ProjectSupplyService(db).confirm(
            order,
            _borrow_body(
                line, own, donor_cline,
                "Authorised by agent CYNDI: agreed on the phone. The site is waiting.",
            ),
            actor_user_id=eling,
        )
        assert result["revision_no"] == 1

        # And it is stored where AC-L6 says: beside the quantity it justifies.
        from app.models.project_so import SOLineAllocation

        rows = (
            db.query(SOLineAllocation)
            .filter(SOLineAllocation.so_line_id == line.id)
            .all()
        )
        assert [r.reason for r in rows] == [
            "Authorised by agent CYNDI: agreed on the phone. The site is waiting."
        ]


def test_another_agents_donor_ranked_below_needs_no_authorisation():
    """The rule is narrow on purpose. A donor BELOW this line in the queue is ordinary
    group borrow - the ladder would have taken it automatically under v2 - so demanding an
    authorisation for it would make a mandatory field a rubber stamp."""
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        mine = _agent(db, f"CYNDI{_uid()[:4]}", location_group=_group)
        theirs = _agent(db, f"JEREMY{_uid()[:4]}", location_group=_group)
        db.commit()

        _donor_order, _dl, _dcso, donor_cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE + timedelta(days=30), line_no=2,
            so_number="SO920001", sales_agent_id=theirs.id,
        )
        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE, line_no=1, so_number="SO920002",
            sales_agent_id=mine.id,
        )
        db.commit()

        result = ProjectSupplyService(db).confirm(
            order,
            _borrow_body(line, own, donor_cline, "Their hand-over is in December."),
            actor_user_id=eling,
        )
        assert result["revision_no"] == 1


def test_a_same_agent_donor_ranked_BELOW_this_line_needs_no_authorisation_either():
    """Same agent, but the donor is behind this line in the queue: no permission is being
    exercised, so none is demanded."""
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        agent = _agent(db, f"CYNDI{_uid()[:4]}", location_group=_group)
        db.commit()

        _donor_order, _dl, _dcso, donor_cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE + timedelta(days=30), line_no=2,
            so_number="SO920001", sales_agent_id=agent.id,
        )
        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="90",
            required_date=REQUIRED_DATE, line_no=1, so_number="SO920002",
            sales_agent_id=agent.id,
        )
        db.commit()

        result = ProjectSupplyService(db).confirm(
            order,
            _borrow_body(line, own, donor_cline, "Her later order can wait a week."),
            actor_user_id=eling,
        )
        assert result["revision_no"] == 1


# --------------------------------------------------------------------------- rung 1


def _shipment(db, *, eta, arrived=None):
    """A booked container. Once one exists the arrival is TRACKED, which is what takes the
    row out of the staleness rule (`spo_supply`)."""
    from app.models.procurement import InboundShipment

    row = InboundShipment(
        id=_uid(),
        shipment_number=f"ZZT-SHIP-{_uid()[:8]}",
        shipment_date=date.today(),
        estimated_arrival_date=eta,
        actual_arrival_date=arrived,
        shipment_status="in_transit",
    )
    db.add(row)
    db.flush()
    return row


def _spo_line(db, product, warehouse, *, qty, arrives, spo_number=None, line_no=1,
              shipment=None):
    """One open SPO line: a shipping order with no container booked, which is what every
    SPO document is until somebody books one (section K, migration 420)."""
    from app.models.procurement import SPOAllocation

    row = SPOAllocation(
        id=_uid(),
        spo_number=spo_number or f"SPO-2026/08-{_uid()[:4]}",
        spo_line_number=line_no,
        product_id=product.id,
        warehouse_id=warehouse.id,
        inbound_shipment_id=shipment.id if shipment is not None else None,
        allocated_quantity=qty,
        quantity_received=0,
        receipt_status="pending",
        line_status="open",
        source_system="scm_spo_history",
        expected_date=arrives,
    )
    db.add(row)
    db.flush()
    return row


def test_an_spo_arriving_before_the_required_date_covers_its_line_through_the_group():
    """AC-K4, as ladder v5 answers it (section 1e). The SPO has no shipment and no ETA of
    its own beyond the line's `expected_date`, which is the only date an imported shipping
    order carries.

    THERE IS NO RUNG 1 ANY MORE. The document is inside the ownership group's net, where
    AutoCount already counts it (`on hand + SPO - SO`), so what answers the line is question
    1 - our own location. The KIND is still `timely_spo` (captain, 27 August 2026): these
    goods are on the water, and a Reserve would be a hold on stock no picker can walk to.
    Which document it is stands on the cell's own location table and on the order-inquiry
    row, where Link SPO ties a particular SPO to a particular line.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        spo_number = _spo_line(
            db, product, own, qty=40, arrives=REQUIRED_DATE - timedelta(days=5)
        ).spo_number
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)

    assert spo_number  # the document exists; the ladder simply does not have a rung for it
    assert [c["kind"] for c in components] == ["timely_spo"]
    assert components[0]["qty"] == "40"
    assert components[0]["rung"] == "group_take"
    assert components[0]["source_location"] == own.warehouse_code
    assert "on the water" in components[0]["reason"]
    assert "group" in components[0]["reason"]


def test_a_late_spo_is_inside_the_groups_net_and_outside_what_the_line_may_draw():
    """THE TWO HALVES OF THE WATER RULE (captain, 27 August 2026), in one fixture.

    The NET stays date-blind: `group_net` is `on hand - SO + SPO` over every open incoming
    row with no arrival-date term at all (`group_netting`'s docstring says so), because it
    states the GROUP's position and not this line's promise. The DRAW is not: an SPO landing
    after the required date covers nothing on that date, so question 1 does not offer it and
    the line buys.

    Before this ruling the same fixture proposed `group_take 40` off water arriving five
    days late, which is a promise the goods cannot keep.

    WHAT LADDER V7.1 STEP 3 DOES WITH IT INSTEAD (S4 fix pass, 30 Aug 2026). Question 1
    still offers nothing - that half of the ruling is untouched - but the document does not
    then vanish: step 3 takes it WHOLE and says so, because arriving 5 days late beats a
    fresh purchase landing 60 days late (R32, the captain on AC-S4-2b: "if buy, it is going
    to arrive even later"). It reaches step 3 through the term this fixture is the smallest
    case of - what the walk has ALREADY GIVEN THIS ASKER on the document, which is the whole
    40 here and nobody else's to lend. Counting only `free` read 0 and bought the lot, which
    is the SO414244 defect. The component names the document, no donor and no order-back:
    the line is taking what it already had.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _spo_line(db, product, own, qty=40, arrives=REQUIRED_DATE + timedelta(days=5))
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        service = ProjectSupplyService(db)
        components = _components(service.proposal_for(order))
        # Read AFTER the proposal, so the netting reader is the one the ladder just used.
        net = service.netting().group_net(str(product.id), group).net

    # The net counts the water: 40 coming against this line's own 40 owed.
    assert net == Decimal("0")
    # Question 1 draws not a unit of it, because it lands after the required date...
    assert "group_take" not in [c["rung"] for c in components]
    # ...and step 3 takes the whole document rather than buying against a later date (R32).
    assert [(c["kind"], c["rung"], c["qty"]) for c in components] == [
        ("borrow", "supply_borrow", "40")
    ]
    assert components[0]["arrival_date"] == REQUIRED_DATE + timedelta(days=5)
    assert components[0]["donor_so_number"] is None, "nobody is owed it back"


def test_a_dead_promise_is_still_inside_the_groups_net_and_still_draws_nothing():
    """TRUST THE BOOK (captain, 26 August 2026). The goods are owed until a re-uploaded PO
    and SPO book says they arrived, so an overdue promise is still supply the group holds.

    LADDER V7.1 REVERSES THE DRAW (R31, 29 August 2026), and keeps the net. Such a document
    is still inside `group_net` - AutoCount counts it and the trail names it - but it is NOT
    supply a proposal may promise against until somebody re-dates it.

    R-O (3 September 2026) narrows that to the DEAD: a document less than
    `overdue_dead_days` late is planned against `today + overdue_grace_days` now, so this
    fixture's own document was moved from 25 days late to 125 to keep stating the rule it
    was written for. The alive half is `tests/scm/test_overdue_grace_ladder.py`.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        spo_number = _spo_line(
            db, product, own, qty=40, arrives=date.today() - timedelta(days=125)
        ).spo_number
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert spo_number
    assert [c["kind"] for c in components] == ["buy"]
    assert components[0]["qty"] == "40"


def test_a_promise_dated_today_covers_its_line():
    """The boundary, said out loud: a row dated today arrives today, and an arrival on the
    required date itself is cover. An exclusive comparison on either would look like a
    rounding detail and cost a whole day."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _spo_line(db, product, own, qty=40, arrives=date.today())
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date.today(),
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [c["rung"] for c in components] == ["group_take"]
    assert components[0]["qty"] == "40"


def test_a_fully_received_line_is_not_supply_however_open_its_date_looks():
    """The other half of the rule. What makes a row supply is what is still TO COME on it,
    and a line the book states as received has nothing: it is out, and no date changes
    that."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        row = _spo_line(db, product, own, qty=40, arrives=REQUIRED_DATE - timedelta(days=5))
        row.quantity_received = 40
        row.receipt_status = "fully_received"
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [c["kind"] for c in components] == ["buy"]


def test_a_shipment_backed_row_is_supply_the_group_holds():
    """Once a container is booked the arrival is tracked. Under v5 the arrival date no
    longer decides whether the row is cover - the group's net counts every open incoming -
    so what this pins is that a shipment-backed row is still counted, and counted once."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        shipment = _shipment(db, eta=REQUIRED_DATE - timedelta(days=5))
        _spo_line(db, product, own, qty=40, arrives=date.today() - timedelta(days=400),
                  shipment=shipment)
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [c["rung"] for c in components] == ["group_take"]
    assert components[0]["qty"] == "40"


# ------------------------------------------------------------------- R-M (3 Sep 2026)


def _other_group_book(db, company_id, product, bin_, *, near, far):
    """One other group's whole open book: what it holds, what is owed before the asker's
    date, and what is owed after it."""
    for qty, days in ((near, 20), (far, 90)):
        if not qty:
            continue
        core_so = _core_so(db, company_id)
        core_so.so_number = f"ZZT-SO-{_uid()[:8]}"
        db.flush()
        _core_line(
            db, core_so, product, bin_, qty_ordered=str(qty),
            required_date=date.today() + timedelta(days=days),
        )


def test_another_groups_free_pile_is_capped_by_that_groups_own_open_book():
    """R-M, the captain's production cell (SO419417, SRTWT7443, 3 September 2026).

    The other group holds 2,237 and owes 2,684 - 1,708 before the asker's date and 976
    after - so it is 447 short on its own book. The date-bounded pile still reads 529 free
    on the asker's day, because demand due AFTER that day never counted against it, and
    that 529 is what the ladder used to offer a BB line of 4. A group whose whole book is
    short gives NOTHING, and the step says why.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IB{_uid()[:3]}")
        _stock(db, product, other, on_hand=2237)
        _other_group_book(db, company_id, product, other, near=1708, far=976)
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="4",
        )
        service = ProjectSupplyService(db)
        facts = service._facts_for(order, service.lines_of(str(order.id)))
        fact = next(iter(facts.values()))
        _own, cross, _offer, short = service.use_candidates_for(fact)
        other_code = other.warehouse_code
        # The suffix, upper-cased, which is `group_of_warehouse_code`'s own rule.
        other_group = other_code.split("-", 1)[1].upper()

    assert sum(
        (Decimal(str(c["qty"])) for c in cross if c["location"] == other_code),
        Decimal("0"),
    ) == Decimal("0"), "an oversold group has nothing free to offer, whatever the date says"
    assert short == {other_group: Decimal("447")}


def test_another_groups_free_pile_stands_where_that_groups_book_is_whole():
    """The same cell with the far order gone: 2,237 against 1,708 owed is a book of 529, so
    the pile the asker's date measured is genuinely free and the offer stands at 529."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IB{_uid()[:3]}")
        _stock(db, product, other, on_hand=2237)
        _other_group_book(db, company_id, product, other, near=1708, far=0)
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="4",
        )
        service = ProjectSupplyService(db)
        facts = service._facts_for(order, service.lines_of(str(order.id)))
        fact = next(iter(facts.values()))
        _own, cross, _offer, short = service.use_candidates_for(fact)
        other_code = other.warehouse_code

    assert [(c["location"], c["qty"]) for c in cross] == [
        (other_code, Decimal("529")),
    ]
    assert short == {}


def test_one_lending_groups_book_is_spent_once_across_the_whole_walk():
    """R-M's cap is a statement about the GROUP, so a BOARD spends it once (3 Sep 2026).

    The cap was applied per unit, and the walk's own offer ledger is keyed by BIN, so two
    units whose dates bring DIFFERENT bins of one lending group into view were each handed
    the whole of that group's spare book. Here IB holds 100 on its floor and has 100 more
    arriving on day 50, and owes 100 on day 45 and 60 on day 100: its book spares 40. The
    BB line due on day 30 sees the floor (the day-45 order has not queued yet), the BB line
    due on day 60 sees only the arrival (the floor is gone by then), and each was proposed
    40 - 80 out of a book with 40 in it, both confirmable.

    One budget for the group, spent once: the first unit takes the 40, the second is offered
    nothing free, and the walk carries on down the rungs to a BORROW off the later IB order
    holding that arrival - a take with a named donor and an order back, which is exactly the
    continuation R-M rules for a group that has nothing to spare.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]

        lender = f"IB{_uid()[:4]}"
        floor = _warehouse(db, f"ZZTLA-{lender}")
        water = _warehouse(db, f"ZZTLB-{lender}")
        _stock(db, product, floor, on_hand=100)
        _spo_line(db, product, water, qty=100, arrives=date.today() + timedelta(days=50))
        for qty, days in ((100, 45), (60, 100)):
            core_so = _core_so(db, company_id)
            core_so.so_number = f"ZZT-SO-{_uid()[:8]}"
            db.flush()
            _core_line(
                db, core_so, product, floor, qty_ordered=str(qty),
                required_date=date.today() + timedelta(days=days),
            )

        core_so = _core_so(db, company_id)
        core_so.so_number = f"ZZT-SO-{_uid()[:8]}"
        db.flush()
        near = _core_line(
            db, core_so, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=30),
        )
        far = _core_line(
            db, core_so, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=60),
        )
        order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
        _project_line(db, order, line_no=1, product=product, core_line=near)
        _project_line(db, order, line_no=2, product=product, core_line=far)
        db.commit()

        proposal = ProjectSupplyService(db).proposal_for(order)
        lines = {row["line_no"]: row["components"] for row in proposal["lines"]}
        floor_code, water_code = floor.warehouse_code, water.warehouse_code

    assert [
        (c["rung"], c["qty"], c["source_location"]) for c in lines[1]
    ] == [("group_take", "40", floor_code)]
    assert not [c for c in lines[2] if c["rung"] == "group_take"], (
        "the lending group's whole book went to the first unit, so its other bin is not "
        "free stock for the second"
    )
    assert [(c["rung"], c["qty"], c["source_location"]) for c in lines[2]] == [
        ("supply_borrow", "40", water_code)
    ]
