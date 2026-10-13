"""Ladder v5: four questions, and SPO is not one of them (`PLAN-scm-cs-planning-uat.md` 1e).

The engine half. `tests/test_ladder_v5_trail.py` is the board half (the proof rows, the
other-group block, staleness); `tests/scm/test_front_planning_golden.py` still owns the
golden numbers.

What v5 changes, and so what is pinned here:

* **AC-V2** incoming is NOT a rung. An SPO is inside the ownership group's net already
  (AutoCount's Available counts it), so a line whose group nets positive only because of an
  SPO is served from the GROUP - question 1. The KIND survives there (`timely_spo` under
  `rung: group_take`): what is on the water is not a hold on a floor, and only the share of
  it landing by the required date is drawn at all. Under v4 the same line was covered by
  rung 1 and the group rung was told the SPO had spent its offer.
* **AC-V7** the pool is walked before another group's stock, and a line that both could
  cover comes off the pool.
* **AC-V6** dealer hot-selling refuses the WHOLE pile, not the line's own site's share of
  it.

Postgres via `blank_session`, every chain seeded here (CI's database has no data).
"""
from __future__ import annotations

import pytest

from datetime import timedelta
from decimal import Decimal

from app.services.project_supply_service import ProjectSupplyService
from app.services.scm import priority

from .._pg_fixture import blank_session
from ..test_so_supply_confirmation import (  # noqa: F401  (helpers, not fixtures)
    _core_line,
    _core_so,
    _product,
    _project_line,
    _project_so,
    _sorento,
    _stock,
    _uid,
    _user,
    _warehouse,
)
from .test_project_supply_service_ladder import (
    REQUIRED_DATE,
    _components,
    _group_sites,
    _seed_line,
    _spo_line,
    _world,
)


def _policy(db, name: str):
    """An active fulfilment-priority policy. Was `_cap`: the cross-group borrow limit it
    used to set is gone with v7.1 (R5)."""
    priority.create_revision(
        db, name=name, factors={}, demand_class_weights={},
        reorder_coverage_until=None,
    )
    db.commit()


# --------------------------------------------------------------------------- AC-V2


@pytest.mark.usefixtures("ladder_steps_on")
def test_an_spo_covers_its_line_through_the_group_net_not_through_a_rung_of_its_own():
    """AC-V2. The SPO of 40 at the line's own group location makes the IB group net 0 with
    this line's own 40 netted out, so the group offers the whole 40 - and the answer is
    question 1, `group_take`, not a rung called Incoming.

    Under v4 this same fixture answered from a rung of its own. The quantity is identical
    and so is the KIND (`timely_spo` - these goods are on the water and a Reserve would be a
    hold on stock nobody can pick, captain 27 August 2026); what changed is which question
    answers it, and that is the whole of 1e's first bullet.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _spo_line(db, product, own, qty=40, arrives=REQUIRED_DATE - timedelta(days=5))
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [c["rung"] for c in components] == ["group_take"], (
        "the SPO is inside the group's net, so the group answers the line"
    )
    assert [c["kind"] for c in components] == ["timely_spo"], (
        "and what it hands over is water, not a hold on a floor"
    )
    assert components[0]["qty"] == "40"
    assert components[0]["source_location"] == own.warehouse_code
    assert "group" in components[0]["reason"]
    assert "on the water" in components[0]["reason"]


@pytest.mark.usefixtures("ladder_steps_on")
def test_no_composition_the_engine_writes_carries_the_retired_incoming_rung():
    """AC-V2, the general form: whatever the facts, `incoming` is a RUNG the v5 engine never
    walks. It survives only on frozen snapshots taken under an older ladder.

    The KIND `timely_spo` does still appear, under question 1's own rung, because part of
    what the ownership group's net can cover a line with is on the water rather than on a
    floor (captain, 27 August 2026). A rung is a question the ladder asks; a kind is what
    the answer is made of, and only the first of those was retired.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=5)
        _spo_line(db, product, own, qty=100, arrives=REQUIRED_DATE - timedelta(days=5))
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert "incoming" not in [c["rung"] for c in components]
    # LADDER V8 (R-A/R-C): the site pool is asked FIRST and spares its half of the 5 it
    # holds, and the 38 left of the line is what question 1 answers with the water. The
    # rung under test is unchanged - what the SPO reaches this line as is `group_take`.
    assert [(c["kind"], c["rung"]) for c in components] == [
        ("reserve", "pool"),
        ("timely_spo", "group_take"),
    ]


def test_a_line_beyond_the_window_buys_whole_even_when_an_spo_would_cover_it():
    """AC-V2 at rung 0. Incoming was the one rung that ran beyond the lead-time window; with
    no such rung, a far line is a whole-line Buy and purchasing links the SPO on its order
    inquiry row instead."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        far = REQUIRED_DATE + timedelta(days=900)
        _spo_line(db, product, own, qty=40, arrives=far - timedelta(days=5))
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40", required_date=far,
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [c["kind"] for c in components] == ["buy"]
    assert components[0]["qty"] == "40"


# --------------------------------------------------------------------------- AC-V7


def test_the_site_pools_share_is_asked_before_another_groups_free_pile_under_v8():
    """AC-V7, reversed by v7.1 (R1, R5) and reversed BACK by ladder v8 (R-A).

    Same case, same numbers - 24 needed, the site pools free 268, another group holding 100.
    v5 answered from the pool, v7.1 moved the pool last so the donor answered, and v8 asks
    the asking bin's own pool FIRST again: it may spare 134 of its 268 and 24 fits inside
    that, so the pool answers and the other group's 100 is never reached.

    What kept the pool for the orders with nowhere else to go under v7.1 is the SHARE now
    (R-B): the pool never gives a project line more than half of itself, whoever asks.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=268)
        # Another ownership group at the same site, holding 100 free and within the cap.
        donor = _warehouse(db, f"ZZTDC1-NT{_uid()[:3]}")
        _stock(db, product, donor, on_hand=100)
        _policy(db, f"zzt-v5-order-{_uid()[:6]}")

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="24",
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [(c["kind"], c["rung"], c["qty"]) for c in components] == [
        ("reserve", "pool", "24"),
    ]
    assert components[0]["source_location"] == pool.warehouse_code
    assert donor is not None, "the donor held 100 free and was not reached"


# --------------------------------------------------------------------------- AC-V6


def test_another_sites_pool_answers_when_the_asking_sites_pool_is_empty():
    """AC-V6, RE-BLESSED TWICE. The dealer hot-selling gate it was written for is retired
    (v8, R-A), and R-L then reverses what was left of it: the asking site's pool holds
    nothing, so the OTHER site pools are asked for the remainder - after the group and both
    borrows - each under its own allowance, and 1000 between DC1 and MWH covers a line of
    10 rather than leaving it to Buy.

    The five pools are still ONE pile bounded by one net; what R-L adds is that an empty
    pool at the asking site is not the end of the step.
    """
    from app.models.scm import ItemClassification

    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, own_pool = sites["BRW"]
        _, dc1_pool = sites["DC1"]
        _, mwh_pool = sites["MWH"]
        _stock(db, product, own_pool, on_hand=0)
        _stock(db, product, dc1_pool, on_hand=500)
        _stock(db, product, mwh_pool, on_hand=500)
        db.add(
            ItemClassification(
                id=_uid(), product_id=product.id, warehouse_id=own.id,
                abc_class_retail="A",
            )
        )
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="10",
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [(c["kind"], c["qty"], c["rung"]) for c in components] == [
        ("reserve", "10", "pool")
    ], "1000 sits in the pile at DC1 and MWH, and R-L asks them for the remainder"
