"""Fulfilment Planning suggests FOUR options only (owner, 12 Oct 2026).

`documentation/plans/scm/PLAN-plan-four-options-12oct.md`. The planner considers Use own
location, Borrow (a location's), Use BRW and Buy. Three steps are DISABLED, not deleted,
each behind its own flag in `app.services.scm.ladder_steps` so it can be turned back on:

* Use incoming (step 1's water half, `timely_spo`) - `USE_INCOMING`;
* Borrow incoming (step 3, `supply_borrow`) - `SUPPLY_BORROW`;
* Borrow from another order: the PLANNER never names a donor, whatever the Settings toggle
  says - `ORDER_BORROW_NAMES_DONOR`. The toggle stays and governs the manual Decide menu.

The engine half is pure (no database); the toggle half walks a real proposal.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.services.scm import ladder_steps
from app.services.scm.front_planning_engine import (
    BUY,
    STEP_SUPPLY_BORROW,
    TIMELY_SPO,
    walk_line,
)

TODAY = date(2026, 10, 12)
DUE = TODAY + timedelta(days=30)


def _water(qty="10"):
    """The group's own SPO, landing before the line's date: step 1's water half."""
    return {
        "location": "BRW-BB",
        "qty": Decimal(qty),
        "water": True,
        "arrival_date": TODAY + timedelta(days=5),
    }


def _supply_borrow(qty="10"):
    """A later order's SPO that covers the whole unit: step 3."""
    return {
        "location": "BRW-BB",
        "qty": Decimal(qty),
        "arrival_date": TODAY + timedelta(days=5),
        "supply_kind": "spo",
        "supply_document": "SPO-1",
        "donor_so_number": "SO900001",
        "donor_line_no": 1,
        "donor_required_date": DUE + timedelta(days=60),
    }


def _walk(**candidates):
    return walk_line(
        open_qty=Decimal("10"),
        required_date=DUE,
        as_of=TODAY,
        lead_time_days=60,
        fulfilment_location="BRW-BB",
        group_code="BB",
        group_offer=Decimal("10"),
        **candidates,
    )


# ------------------------------------------------------------------- the flags


def test_the_three_disabled_steps_are_flags_and_all_off():
    assert ladder_steps.USE_INCOMING is False
    assert ladder_steps.SUPPLY_BORROW is False
    assert ladder_steps.ORDER_BORROW_NAMES_DONOR is False


# ------------------------------------------------------------- Use incoming (Q1)


def test_use_incoming_off_the_groups_water_is_not_taken_and_the_line_is_bought():
    walked = _walk(group_take_candidates=[_water()])

    assert [c.kind for c in walked.components] == [BUY]
    assert all(c.kind != TIMELY_SPO for c in walked.components)


def test_use_incoming_off_another_groups_water_is_not_taken_either():
    other = {**_water(), "location": "BRW-IB", "group": "IB"}
    walked = _walk(other_group_candidates=[other])

    assert all(c.kind != TIMELY_SPO for c in walked.components)


def test_use_incoming_off_floor_stock_is_still_used():
    floor = {"location": "BRW-BB", "qty": Decimal("10")}
    walked = _walk(group_take_candidates=[floor, _water()])

    assert [(c.kind, c.qty) for c in walked.components] == [("reserve", Decimal("10"))]


def test_use_incoming_back_on_restores_the_water_half(monkeypatch):
    monkeypatch.setattr(ladder_steps, "USE_INCOMING", True)
    walked = _walk(group_take_candidates=[_water()])

    assert [c.kind for c in walked.components] == [TIMELY_SPO]


# ----------------------------------------------------------- Borrow incoming (Q3)


def test_borrow_incoming_off_step_three_is_never_walked():
    walked = _walk(supply_borrow_candidates=[_supply_borrow()])

    assert [c.kind for c in walked.components] == [BUY]
    assert all(c.rung != "supply_borrow" for c in walked.components)


def test_borrow_incoming_off_its_option_row_is_hidden():
    walked = _walk(supply_borrow_candidates=[_supply_borrow()])

    steps = [option.step for option in walked.options]
    assert STEP_SUPPLY_BORROW not in steps
    assert steps == ["pool_share", "use", "order_borrow", "buy"]


def test_borrow_incoming_back_on_restores_the_step_and_its_row(monkeypatch):
    monkeypatch.setattr(ladder_steps, "SUPPLY_BORROW", True)
    walked = _walk(supply_borrow_candidates=[_supply_borrow()])

    assert [c.rung for c in walked.components] == ["supply_borrow"]
    assert STEP_SUPPLY_BORROW in [option.step for option in walked.options]


# ------------------------------------------------ Borrow from another order (Q2)


@pytest.fixture
def borrow_world():
    from .._pg_fixture import blank_session
    from .test_borrow_location_only import _borrow_order_on
    from .test_ladder_v7_borrow import _borrow_world

    with blank_session() as db:
        world = _borrow_world(db)
        _borrow_order_on(db)
        yield db, world


def test_toggle_on_the_planner_still_proposes_a_location_borrow_naming_no_donor(borrow_world):
    from app.services.project_supply_service import ProjectSupplyService

    from .test_borrow_location_only import DONOR_FIELDS
    from .test_project_supply_service_ladder import _components

    db, world = borrow_world
    components = _components(ProjectSupplyService(db).proposal_for(world["asker"]))

    borrows = [c for c in components if c["kind"] == "borrow"]
    assert borrows, components
    for component in borrows:
        for field in DONOR_FIELDS:
            assert component[field] is None, (field, component[field])
        assert "from SO" not in component["reason"], component["reason"]
        assert component["donor_core_line_id"], "stays internal, as under OFF"


def test_toggle_on_the_manual_borrow_list_still_names_the_donor(borrow_world):
    """The toggle keeps its meaning for a PERSON's pick (Decide menu, Borrow dialog)."""
    from app.services.project_supply_service import ProjectSupplyService

    db, world = borrow_world
    rows = ProjectSupplyService(db).proposal_for(world["asker"])["lines"][0]["borrow_candidates"]
    assert [c for c in rows if c.get("rung") == "order_borrow" and c.get("donor_so_number")], rows


def test_names_donor_back_on_restores_the_order_borrow(borrow_world, monkeypatch):
    from app.services.project_supply_service import ProjectSupplyService

    from .test_project_supply_service_ladder import _components

    monkeypatch.setattr(ladder_steps, "ORDER_BORROW_NAMES_DONOR", True)
    db, world = borrow_world
    components = _components(ProjectSupplyService(db).proposal_for(world["asker"]))

    assert [c for c in components if c["kind"] == "borrow" and c["donor_so_number"]], components


def test_toggle_on_the_pools_later_order_is_also_proposed_as_a_location_borrow():
    """Step 4b: a later POOL order's on hand, the same rule as step 2."""
    from app.services.project_supply_service import ProjectSupplyService

    from .._pg_fixture import blank_session
    from ..test_so_supply_confirmation import _stock, _uid
    from .test_borrow_location_only import DONOR_FIELDS, _borrow_order_on
    from .test_ladder_v7_borrow import LEAD_DAYS, WINDOW_DAY, _policy
    from .test_project_supply_service_ladder import (
        _components,
        _group_sites,
        _lead_time,
        _seed_line,
        _world,
    )

    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=60)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)
        _seed_line(
            db, company_id, project, product, pool, qty_ordered="60",
            required_date=date.today() + timedelta(days=WINDOW_DAY + 60), line_no=9,
            so_number=f"ZZTSO-POOL{_uid()[:4]}",
        )
        asker, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="60", line_no=1,
            so_number=f"ZZTSO-ASK{_uid()[:5]}",
        )
        _borrow_order_on(db)
        components = _components(ProjectSupplyService(db).proposal_for(asker))

    assert [(c["kind"], c["rung"]) for c in components] == [("borrow", "pool")]
    for field in DONOR_FIELDS:
        assert components[0][field] is None, (field, components[0])


# ------------------------------------------------------- the board's own rows (Q5)


def _board_contribution():
    from app.models.base import company_scope

    from .._pg_fixture import blank_session
    from ..test_fulfilment_board import (
        BASE,
        MARKER,
        TODAY as BOARD_TODAY,
        VIEW,
        _board_world,
        _client,
        _restore,
        _sorento,
        _user,
    )

    with blank_session() as db:
        company_id = _sorento(db)
        actor = _user(db, f"{MARKER} Eling")
        order, _product = _board_world(db)
        db.commit()
        client, originals = _client(db, actor, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={
                        "orders": order.so_number,
                        "granularity": "week",
                        "as_of": BOARD_TODAY.isoformat(),
                    },
                )
        finally:
            _restore(originals)
    assert response.status_code == 200, response.text
    return response.json()["cells"][0]["contributions"][0]


def test_the_board_hides_the_borrow_incoming_question_and_option_row():
    contribution = _board_contribution()

    assert "supply_borrow" not in [step["kind"] for step in contribution["trail"]]
    assert "supply_borrow" not in [option["step"] for option in contribution["options"]]


def test_the_board_asks_borrow_incoming_again_once_it_is_back_on(monkeypatch):
    monkeypatch.setattr(ladder_steps, "SUPPLY_BORROW", True)
    contribution = _board_contribution()

    assert "supply_borrow" in [step["kind"] for step in contribution["trail"]]
    assert "supply_borrow" in [option["step"] for option in contribution["options"]]


# ------------------------------------- the suggestion confirms as displayed (Q2, (a))


def test_toggle_on_confirming_the_planners_location_borrow_owes_it_back_on_this_line(
    borrow_world,
):
    """Owner, 12 Oct 2026: what the planner suggests confirms exactly as displayed.

    With the toggle ON the planner still proposes a location's Borrow ("ordered back to X on
    this line"), so Confirm must write that: the order-back on the BORROWING line, and the
    donor's own decision left alone. Before the fix it followed the toggle and wrote the
    order-borrow shape instead (the reviewer's probe: the row on the donor line, the donor
    decision superseded)."""
    from app.models.project_so import DECISION_SUPERSEDED, SOSupplyDecision

    from .test_borrow_location_only import (
        _assert_location_order_back,
        _component_to_payload,
        _confirm_borrow,
        _order_backs,
        _step_two_borrow,
    )

    db, world = borrow_world
    component = _step_two_borrow(db, world)
    assert component["donor_so_number"] is None, "the planner names no donor"
    assert "on this line" in component["reason"], component["reason"]
    _confirm_borrow(db, world, _component_to_payload(component, warehouse=world["donor_bin"]))

    rows = _order_backs(db)
    donor_states = [
        d.state
        for d in db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == world["donor_order"].id)
        .all()
    ]
    assert len(rows) == 1, [(r.so_line_id, r.qty, r.note) for r in rows]
    _assert_location_order_back(
        rows[0],
        line=world["asker_line"],
        order=world["asker"],
        code=world["donor_bin"].warehouse_code,
    )
    assert DECISION_SUPERSEDED not in donor_states, donor_states


def test_toggle_on_a_borrow_picked_with_the_donor_named_keeps_the_order_borrow(
    borrow_world, monkeypatch,
):
    """A person's "Borrow from another order" (the donor named) keeps today's ON shape."""
    from app.models.project_so import DECISION_SUPERSEDED, SOSupplyDecision

    from .test_borrow_location_only import (
        _component_to_payload,
        _confirm_borrow,
        _order_backs,
        _step_two_borrow,
    )

    monkeypatch.setattr(ladder_steps, "ORDER_BORROW_NAMES_DONOR", True)
    db, world = borrow_world
    component = _step_two_borrow(db, world)
    assert component["donor_so_number"]
    _confirm_borrow(db, world, _component_to_payload(component, warehouse=world["donor_bin"]))

    rows = _order_backs(db)
    donor_states = [
        d.state
        for d in db.query(SOSupplyDecision)
        .filter(SOSupplyDecision.project_sales_order_id == world["donor_order"].id)
        .all()
    ]
    assert len(rows) == 1
    assert str(rows[0].so_line_id) == str(world["donor_line"].id)
    assert DECISION_SUPERSEDED in donor_states
