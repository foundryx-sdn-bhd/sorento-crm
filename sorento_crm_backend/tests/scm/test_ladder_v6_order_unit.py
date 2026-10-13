"""Ladder v6: one order's lines for one item, one location and one delivery date are ONE
planning unit (`documentation/plans/scm/PLAN-scm-order-unit-ladder-v6.md`).

The captain, reading SO381895 on the board: lines 31 and 32, same item, same location, same
delivery date, 10 and 20. The board proposed Borrow 10 for one and Buy 20 for the other.
"This is 1 order as a whole, so we should look at the order as a whole instead of line by
line ... for the same delivery date." Each line obeyed the whole-LINE rule; the order did
not.

What is pinned here (the UAC's own numbering):

* **AC-U1** two lines of one order, one item, one location, one date: covered whole from
  stock or bought whole, never "one borrows and the other buys";
* **AC-U2** the unit draws the shared pool ONCE and the draw is split across its lines;
* **AC-U3** a different delivery date is a different unit, and v5's answer stands for it;
* **AC-U4** a different fulfilment location is a different unit;
* **AC-U5** a component that straddles two lines keeps its kind, source, rung and reason on
  both halves, and the halves sum to it;
* **AC-U6** the sheet, the freeze and the board compose one order the same way;
* **AC-U8** `unit_qty` and `unit_line_count` reach the wire on both surfaces
  (`response_model` drops what a schema does not declare);
* **AC-U9** the cross-group donor is a running ledger across the walk: the first unit
  borrows it and the later units buy whole, which is what the confirmation was refusing.

AC-U7 (every ladder v4 / v5 test passes unchanged) is the existing suites, not a test here.

Postgres via `blank_session`, every chain seeded here (CI's database has no data).
"""
from __future__ import annotations

import pytest

from datetime import date, timedelta
from decimal import Decimal

from app.models.project_so import SO_STATUS_PUBLISHED
from app.services.project_supply_service import ProjectSupplyService
from app.services.scm.front_planning_engine import qty_text

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
from .test_ladder_v5 import _policy
from .test_project_supply_service_ladder import (
    REQUIRED_DATE,
    _group_sites,
    _seed_line,
    _spo_line,
    _world,
)

#: The second delivery date AC-U3 splits the order over. A week later, and still inside the
#: ATP reserve window, so what is being tested is the unit rule and never rung 0.
LATER_DATE = REQUIRED_DATE + timedelta(days=7)


def _seed_order(db, company_id, project, product, *, lines):
    """One core sales order and its project mirror, a line per entry.

    `lines` is a sequence of `(line_no, qty, warehouse, required_date)`. ONE core sales
    order, because the planning unit is a unit OF an order: two orders asking for the same
    item at the same location on the same day are two units and the ladder has always
    walked them as such.

    The mirror HOLDS the core order (`so_id`), which is what an adopted order looks like and
    what makes the board name these lines by the sheet's own line numbers
    (`_mirror_addressing`) rather than deriving its own.
    """
    core_so = _core_so(db, company_id)
    order = _project_so(db, project, status=SO_STATUS_PUBLISHED, so_id=core_so.id)
    mirrors = []
    for line_no, qty, warehouse, when in lines:
        core_line = _core_line(
            db, core_so, product, warehouse, qty_ordered=qty, required_date=when
        )
        mirrors.append(
            _project_line(
                db, order, line_no=line_no, product=product, core_line=core_line
            )
        )
    db.commit()
    return core_so, order, mirrors


def _sheet(db, order):
    """The sheet's lines, keyed by line number."""
    return {
        line["line_no"]: line
        for line in ProjectSupplyService(db).proposal_for(order)["lines"]
    }


def _board(db, core_so, *, as_of):
    """The board's contributions for one sales order, keyed by line number."""
    from app.services.project_fulfilment_board_service import FulfilmentBoardService

    board = FulfilmentBoardService(db).build(
        [core_so.so_number], granularity="week", as_of=as_of
    )
    return {row["line_no"]: row for row in board["contributions"]}


def _frozen(db, order):
    """What `confirm` would freeze as the engine's own proposal, keyed by line number.

    `_proposals_for` is the freeze path's own walk (`_compose_for_freeze` in the plan); its
    middle tuple member is the confirmation payload entry, which it does not read.
    """
    service = ProjectSupplyService(db)
    lines = service.lines_of(str(order.id))
    facts = service._facts_for(order, lines)
    proposals = service._proposals_for(
        lines, [(line, None, facts[str(line.id)]) for line in lines], facts=facts
    )
    return {line.line_no: proposals[str(line.id)] for line in lines}


def _stated(components):
    """A composition in one vocabulary, whatever surface stated it."""
    return [
        (
            component.kind,
            qty_text(component.qty),
            component.source_location,
            component.rung,
        )
        for component in components
    ]


def _stated_sheet(line):
    return [
        (c["kind"], c["qty"], c["source_location"], c["rung"])
        for c in line["components"]
    ]


def _stated_board(contribution):
    return [
        (c["kind"], c["qty"], c["location"], c["rung"])
        for c in contribution["sources"]
    ]


# --------------------------------------------------------------------------- AC-U1


def test_the_smaller_line_of_a_unit_takes_what_the_pile_can_cover_and_the_bigger_one_buys():
    """AC-U1, RE-BLESSED BY LADDER V8 (R-E). 10 and 20 of one item at one location on one
    date; the group nets -30 and offers nothing, the pools net 0, and another group holds 12.

    v5 walked the lines in line order: 31 took the 10 it needed and 32 found 2 left and
    bought. v6 planned the unit as ONE quantity of 30, could cover only 12 of it, and bought
    BOTH lines - which is what the captain asked for then, and what he asked to be undone on
    2 September after SO419208 read "Buy 1440" with 135 sitting on the floor.

    v8 walks the unit's lines one at a time, SMALLEST FIRST, each fed what the previous one
    left: the 10 is covered whole from the donor group's free pile, and the 20 finds 2 left
    - not the whole of its own need - so it buys whole. The unit is still one board cell.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor = _warehouse(db, f"ZZTDC1-NT{_uid()[:3]}")
        _stock(db, product, donor, on_hand=12)
        _policy(db, f"zzt-v6-{_uid()[:6]}")

        _core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, REQUIRED_DATE),
            ],
        )
        lines = _sheet(db, order)

    assert _stated_sheet(lines[31]) == [
        ("reserve", "10", donor.warehouse_code, "group_take")
    ]
    assert _stated_sheet(lines[32]) == [("buy", "20", None, "buy")]
    assert "Only 2 of 20" in lines[32]["components"][0]["reason"], (
        "the reason states what was left for THIS line when it was walked, which is what "
        "R-E's per-line walk makes true"
    )


def test_the_unit_is_covered_whole_when_the_ladder_reaches_the_whole_of_it():
    """AC-U1, the other half of the rule: the donor holding 30 covers both lines rather than
    neither. The unit is bought whole ONLY when the ladder cannot reach the whole of it."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor = _warehouse(db, f"ZZTDC1-NT{_uid()[:3]}")
        _stock(db, product, donor, on_hand=30)
        _policy(db, f"zzt-v6-{_uid()[:6]}")

        _core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, REQUIRED_DATE),
            ],
        )
        lines = _sheet(db, order)

    assert _stated_sheet(lines[31]) == [
        ("reserve", "10", donor.warehouse_code, "group_take")
    ]
    assert _stated_sheet(lines[32]) == [
        ("reserve", "20", donor.warehouse_code, "group_take")
    ]


# --------------------------------------------------------------------------- AC-U2


def test_the_unit_draws_the_pool_once_and_the_draw_is_split_in_line_order():
    """AC-U2. The site pools net 63 and this line's own pool holds all of it: the unit takes
    30 in ONE draw and the ledger is 33 for whatever asks next, never 63 twice or 43."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=63)

        _core_so, order, mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, REQUIRED_DATE),
            ],
        )
        lines = _sheet(db, order)

        # The ledger itself, which the payload cannot show: both members of the unit are
        # composed against the pool as it stood BEFORE the unit's own draw.
        service = ProjectSupplyService(db)
        mirror_lines = service.lines_of(str(order.id))
        facts = service._facts_for(order, mirror_lines)
        composed = service.compose_lines(
            [
                (
                    line.line_no,
                    facts[str(line.id)],
                    (
                        facts[str(line.id)].product_id,
                        str(facts[str(line.id)].warehouse.id),
                        facts[str(line.id)].required_date,
                    ),
                )
                for line in mirror_lines
            ]
        )

    assert _stated_sheet(lines[31]) == [
        ("reserve", "10", pool.warehouse_code, "pool")
    ]
    assert _stated_sheet(lines[32]) == [
        ("reserve", "20", pool.warehouse_code, "pool")
    ]
    # AC-N.12: the walk's pool ledger is one entry PER POOL LOCATION now (it carried the
    # asking bin's own pool alone until step 0 started walking the whole chain), so the
    # proof reads the asking pool's own entry out of it.
    assert composed[31][1][pool.warehouse_code] == Decimal("63")
    assert composed[32][1][pool.warehouse_code] == Decimal("53"), (
        "LADDER V8 (R-E): the second line of a unit reads the pool as the FIRST LINE LEFT "
        "it - 63 less the 10 that line took - because the unit's lines walk one at a time "
        "now. Under v6 the unit drew once and both members reported the same opening pile."
    )


# --------------------------------------------------------------------------- AC-U3


def test_two_delivery_dates_are_two_units_and_the_v5_answer_stands():
    """AC-U3. The same two lines a week apart: two units, walked as v5 walked them - the
    earlier one borrows the 10 it can cover whole, the later one cannot and buys."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor = _warehouse(db, f"ZZTDC1-NT{_uid()[:3]}")
        _stock(db, product, donor, on_hand=12)
        _policy(db, f"zzt-v6-{_uid()[:6]}")

        _core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, LATER_DATE),
            ],
        )
        lines = _sheet(db, order)

    assert _stated_sheet(lines[31]) == [
        ("reserve", "10", donor.warehouse_code, "group_take")
    ]
    assert _stated_sheet(lines[32]) == [("buy", "20", None, "buy")]
    assert [lines[31]["unit_line_count"], lines[32]["unit_line_count"]] == [1, 1]
    assert [lines[31]["unit_qty"], lines[32]["unit_qty"]] == ["10", "20"]


# --------------------------------------------------------------------------- AC-U4


def test_two_fulfilment_locations_are_two_units():
    """AC-U4. One order, one item, one date, two locations of the same ownership group:
    stock does not move between locations without a transfer, so each location is planned on
    its own."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        sibling, _sibling_pool = sites["MWH"]
        donor = _warehouse(db, f"ZZTDC1-NT{_uid()[:3]}")
        _stock(db, product, donor, on_hand=12)
        _policy(db, f"zzt-v6-{_uid()[:6]}")

        _core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", sibling, REQUIRED_DATE),
            ],
        )
        lines = _sheet(db, order)

    assert [lines[31]["unit_line_count"], lines[32]["unit_line_count"]] == [1, 1]
    assert _stated_sheet(lines[31]) == [
        ("reserve", "10", donor.warehouse_code, "group_take")
    ]
    assert _stated_sheet(lines[32]) == [("buy", "20", None, "buy")]


# --------------------------------------------------------------------------- AC-U5


def test_each_line_of_a_unit_draws_the_shared_bin_for_itself_and_says_so():
    """AC-U5, RE-BLESSED BY LADDER V8 (R-E). The unit of 30 is covered by 5 at its own
    location and 25 at a sibling of its ownership group, and the two lines share both bins.

    Under v6 the unit was composed once and SPLIT across the lines, so the sibling's
    component straddled the boundary and both halves carried one sentence - they were one
    component described twice. Under v8 each line walks for itself off what the previous one
    left, so there is no straddle: the 10 takes 5 and 5, the 20 takes the 20 that is left,
    and each sentence is a share of ITS OWN line's offer (5 of 30, then 20 of 20). The
    quantities are the same; what changed is that each line's row now describes its own draw.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        sibling, _sibling_pool = sites["MWH"]
        _stock(db, product, own, on_hand=5)
        _stock(db, product, sibling, on_hand=25)

        _core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, REQUIRED_DATE),
            ],
        )
        lines = _sheet(db, order)

    assert _stated_sheet(lines[31]) == [
        ("reserve", "5", own.warehouse_code, "group_take"),
        ("reserve", "5", sibling.warehouse_code, "group_take"),
    ]
    assert _stated_sheet(lines[32]) == [
        ("reserve", "20", sibling.warehouse_code, "group_take")
    ]
    first_half = lines[31]["components"][1]
    second_half = lines[32]["components"][0]
    assert first_half["source_warehouse_id"] == second_half["source_warehouse_id"], (
        "one bin, drawn by both lines"
    )
    assert Decimal(first_half["qty"]) + Decimal(second_half["qty"]) == Decimal("25"), (
        "and the two draws still add up to what that bin had"
    )
    assert first_half["reason"] != second_half["reason"], (
        "each line's sentence is a share of its OWN offer now (R-E), not one component's "
        "sentence printed twice"
    )
    assert "5 of the 30" in first_half["reason"], first_half["reason"]
    assert "20 of the 20" in second_half["reason"], second_half["reason"]


# --------------------------------------------------------------------------- AC-U6


def test_the_sheet_the_freeze_and_the_board_compose_one_order_the_same_way():
    """AC-U6. Three walks, one answer. The sheet is what a planner reads on the order, the
    board is what they read across orders, and the freeze is what `confirm` writes down as
    the engine's own suggestion; a unit that composed differently on any of them would make
    the three disagree about the same promise."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=63)

        core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, REQUIRED_DATE),
            ],
        )
        sheet = _sheet(db, order)
        frozen = _frozen(db, order)
        board = _board(db, core_so, as_of=date.today())

    expected = {
        31: [("reserve", "10", pool.warehouse_code, "pool")],
        32: [("reserve", "20", pool.warehouse_code, "pool")],
    }
    for line_no, composition in expected.items():
        assert _stated_sheet(sheet[line_no]) == composition, f"sheet line {line_no}"
        assert _stated(frozen[line_no]) == composition, f"freeze line {line_no}"
        assert _stated_board(board[line_no]) == composition, f"board line {line_no}"


# --------------------------------------------------------------------------- AC-U8


def test_the_sheet_line_states_the_unit_it_was_planned_in():
    """AC-U8, the sheet's half, through the ROUTE: `response_model` drops a field no schema
    declares, so a service that states the unit and a schema that does not is a payload the
    screen never sees."""
    from app.models.base import company_scope

    from ..test_so_supply_confirmation import BASE, _client, _restore

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=63)
        _core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, REQUIRED_DATE),
            ],
        )
        client, originals = _client(db, eling)
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(f"{BASE}/sales-orders/{order.id}/supply")
        finally:
            _restore(originals)

    assert response.status_code == 200, response.text
    lines = {line["line_no"]: line for line in response.json()["lines"]}
    assert lines[31]["unit_qty"] == "30"
    assert lines[31]["unit_line_count"] == 2
    assert lines[32]["unit_qty"] == "30"
    assert lines[32]["unit_line_count"] == 2


def test_the_board_contribution_states_the_unit_it_was_planned_in():
    """AC-U8, the board's half, through the route, for the same reason."""
    from app.models.base import company_scope

    from ..test_fulfilment_board import BASE as BOARD_BASE
    from ..test_fulfilment_board import VIEW
    from ..test_fulfilment_board import _client as _board_client
    from ..test_fulfilment_board import _restore as _board_restore

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=63)
        core_so, _order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, REQUIRED_DATE),
            ],
        )
        client, originals = _board_client(db, eling, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BOARD_BASE}/fulfilment-planning/board",
                    params={
                        "orders": core_so.so_number,
                        "granularity": "week",
                        "as_of": date.today().isoformat(),
                    },
                )
        finally:
            _board_restore(originals)

    assert response.status_code == 200, response.text
    rows = {row["line_no"]: row for row in response.json()["contributions"]}
    assert rows[31]["unit_qty"] == "30"
    assert rows[31]["unit_line_count"] == 2
    assert rows[32]["unit_qty"] == "30"
    assert rows[32]["unit_line_count"] == 2


# --------------------------------------------------------------------------- AC-U9


def test_one_donor_is_borrowed_once_across_the_walk_and_the_later_dates_buy():
    """AC-U9, the live failure of 28 August: "0 of 1 orders confirmed ... BRW-SYNT has 0
    free, and 10 was asked for" on four lines that had each been proposed a Borrow of 10
    from a donor holding 10 in all.

    Rung 5 read the donor's free stock fresh for every line, so every delivery date was
    offered the same 10. The pool has had a running ledger across the walk since the sheet
    existed; the cross-group donor now has one too, and the dates after the first buy whole -
    which is the captain's own expectation: the donor is "occupied by the first borrow".
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor = _warehouse(db, f"ZZTDC1-NT{_uid()[:3]}")
        _stock(db, product, donor, on_hand=10)
        _policy(db, f"zzt-v6-{_uid()[:6]}")

        core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (1, "10", own, REQUIRED_DATE),
                (2, "12", own, REQUIRED_DATE + timedelta(days=14)),
                (3, "10", own, REQUIRED_DATE + timedelta(days=21)),
                (4, "5", own, REQUIRED_DATE + timedelta(days=28)),
            ],
        )
        board = _board(db, core_so, as_of=date.today())
        frozen = _frozen(db, order)

    assert _stated_board(board[1]) == [
        ("reserve", "10", donor.warehouse_code, "group_take")
    ]
    assert _stated_board(board[2]) == [("buy", "12", None, "buy")]
    assert _stated_board(board[3]) == [("buy", "10", None, "buy")]
    assert _stated_board(board[4]) == [("buy", "5", None, "buy")]
    # The confirm path is the one that refused these, so it has to agree line for line.
    for line_no in (1, 2, 3, 4):
        assert _stated(frozen[line_no]) == _stated_board(board[line_no]), (
            f"the freeze and the board disagree about line {line_no}"
        )


def test_the_pools_net_is_one_pile_across_the_walk_and_the_later_dates_buy():
    """AC-U10, the live failure of 28 August, one rung up from AC-U9: SO381895's TPE-9204
    on three delivery dates (30, 30, 15) at BRW-IB, the site pools netting 31 - and the board
    proposed "Pool BRW lends 30 of the 31" to ALL THREE. The confirmation then refused two:
    "BRW now has 1 free for this line, and 30 was asked for".

    `compose_lines` drew the pool's FREE stock down across the walk, but rung 2 is bounded by
    the pile's NET (`pool_reserve_capacity`, ladder v4), and that number was read fresh off
    the fact for every unit. The net is a running ledger across the walk now, exactly as the
    donor is: the earliest date takes the 30, the pile has 1 left, and the later dates buy
    whole (the whole-line rule; the captain: "we don't partial fulfil with stock and partial
    fulfil with buy").
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        # The live shape: the pool holds plenty (3365), and the pool's OWN book owes 3334 of
        # it on a date AFTER every line here - so nothing is ranked ahead of these lines and
        # each one's `pool_free` reads the full pile, while the pile's net is 31. It is the
        # net that bounds rung 2, and the net is what nobody drew down.
        _stock(db, product, pool, on_hand=3365)
        _seed_line(
            db, company_id, project, product, pool,
            qty_ordered="3334", required_date=REQUIRED_DATE + timedelta(days=60),
        )
        _policy(db, f"zzt-v6-{_uid()[:6]}")

        core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (1, "30", own, REQUIRED_DATE),
                (2, "30", own, REQUIRED_DATE + timedelta(days=7)),
                (3, "15", own, REQUIRED_DATE + timedelta(days=14)),
            ],
        )
        board = _board(db, core_so, as_of=date.today())
        frozen = _frozen(db, order)
        sheet = _sheet(db, order)

    # LADDER V8 (R-B): the pile nets 31 and a PROJECT may have half of it - 15 - so the
    # first date takes the 15 and buys the other 15 (R-C: the share is its own sub-unit),
    # and the later dates find the share spent and buy whole. The ledger this case is about
    # is now two ledgers, both running across the walk: the pile's net, and the share of it
    # projects may have.
    assert _stated_board(board[1]) == [
        ("reserve", "15", pool.warehouse_code, "pool"),
        ("buy", "15", None, "buy"),
    ]
    assert _stated_board(board[2]) == [("buy", "30", None, "buy")]
    assert _stated_board(board[3]) == [("buy", "15", None, "buy")]
    # The proof states the pile as the walk found it, not the live net: question 2 on the
    # second date is answered from what the first date left, never from the opening 31.
    pool_step = next(step for step in board[2]["trail"] if step["kind"] == "pool")
    assert "of the 31" not in pool_step["why"], pool_step["why"]
    for line_no in (1, 2, 3):
        assert _stated(frozen[line_no]) == _stated_board(board[line_no]), (
            f"the freeze and the board disagree about line {line_no}"
        )
        assert _stated_sheet(sheet[line_no]) == _stated_board(board[line_no]), (
            f"the sheet and the board disagree about line {line_no}"
        )


def test_the_pools_net_ledger_holds_for_a_site_with_no_pool_of_its_own():
    """AC-U10 on the live book's own shape. BRW-IB names NO `pool_warehouse_id` (a bare site
    code is its own pool, migration 311), so its lines reach BRW through rung 2's "other
    site pools" chain rather than as their own pool - and a ledger keyed off the own pool
    alone would have skipped them, which is exactly how the first cut of this fix passed
    its test and still proposed 30 / 30 / 15 to SO381895.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _own_pool = sites["BRW"]
        own.pool_warehouse_id = None
        db.flush()
        _mwh, pool = sites["MWH"]
        _stock(db, product, pool, on_hand=3365)
        _seed_line(
            db, company_id, project, product, pool,
            qty_ordered="3334", required_date=REQUIRED_DATE + timedelta(days=60),
        )
        _policy(db, f"zzt-v6-{_uid()[:6]}")

        core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (1, "30", own, REQUIRED_DATE),
                (2, "30", own, REQUIRED_DATE + timedelta(days=7)),
                (3, "15", own, REQUIRED_DATE + timedelta(days=14)),
            ],
        )
        board = _board(db, core_so, as_of=date.today())
        frozen = _frozen(db, order)

    # The same v8 shape as the case above (R-B/R-C), and the point this case adds: the site
    # with NO pool of its own reaches MWH through the chain, and the share ledger is seeded
    # off that same chain - a ledger keyed off the own pool alone would have seeded 0 and
    # offered the pile twice.
    assert _stated_board(board[1]) == [
        ("reserve", "15", pool.warehouse_code, "pool"),
        ("buy", "15", None, "buy"),
    ]
    assert _stated_board(board[2]) == [("buy", "30", None, "buy")]
    assert _stated_board(board[3]) == [("buy", "15", None, "buy")]
    for line_no in (1, 2, 3):
        assert _stated(frozen[line_no]) == _stated_board(board[line_no]), (
            f"the freeze and the board disagree about line {line_no}"
        )


# ------------------------------------------------------- the confirmation (review B1)


def _payload_line(line):
    """This line's own proposal, posted back unamended - what "Confirm" sends.

    Built from the sheet's own components so the test cannot invent a composition the
    engine never offered: the whole question here is whether what was PROPOSED can be
    CONFIRMED.
    """
    components = line["components"]
    return {
        "project_line_id": line["project_line_id"],
        "timely_spo_qty": qty_text(
            sum(
                (Decimal(c["qty"]) for c in components if c["kind"] == "timely_spo"),
                Decimal("0"),
            )
        ),
        "reserve": [
            {"warehouse_id": c["source_warehouse_id"], "qty": c["qty"]}
            for c in components
            if c["kind"] == "reserve"
        ],
        # A Borrow the ladder proposed is posted back like every other component. The
        # engine only ever composes the cross-group rung, so the source is the location;
        # a group borrow is a person's pick and never arrives here from a proposal.
        "borrow": [
            {
                "source": "other_location",
                "warehouse_id": c["source_warehouse_id"],
                "qty": c["qty"],
                "reason": c["reason"],
                "donor_core_line_id": c.get("donor_core_line_id"),
            }
            for c in components
            if c["kind"] == "borrow"
        ],
        "buy_qty": qty_text(
            sum(
                (Decimal(c["qty"]) for c in components if c["kind"] == "buy"),
                Decimal("0"),
            )
        ),
    }


def _confirm(db, company_id, actor, order, payload_lines):
    """POST the confirmation, the way the board and the sheet both do."""
    from app.models.base import company_scope

    from ..test_so_supply_confirmation import BASE, _client, _restore

    client, originals = _client(db, actor)
    try:
        with company_scope(db, frozenset({company_id})):
            return client.post(
                f"{BASE}/sales-orders/{order.id}/confirm", json={"lines": payload_lines}
            )
    finally:
        _restore(originals)


def test_a_unit_split_across_two_lines_confirms_exactly_as_proposed():
    """B1. The proposal is composed for the UNIT, so the recheck has to be too.

    AC-U5's own fixture: 5 at the line's own bin, 25 at a sibling of its ownership group,
    lines 31 and 32 wanting 10 and 20. The unit's offer is the group's position measured
    against the WHOLE 30; the LINE's own offer measured against 10 alone is 0, and a recheck
    that re-derived it per line refused the proposal it had just made ("has nothing free for
    this line now"). A proposal that cannot be confirmed is worth nothing.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        sibling, _sibling_pool = sites["MWH"]
        _stock(db, product, own, on_hand=5)
        _stock(db, product, sibling, on_hand=25)

        _core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, REQUIRED_DATE),
            ],
        )
        lines = _sheet(db, order)
        response = _confirm(
            db, company_id, eling, order,
            [_payload_line(lines[31]), _payload_line(lines[32])],
        )
        own_code, sibling_code = own.warehouse_code, sibling.warehouse_code

    assert _stated_sheet(lines[31]) == [
        ("reserve", "5", own_code, "group_take"),
        ("reserve", "5", sibling_code, "group_take"),
    ]
    assert _stated_sheet(lines[32]) == [("reserve", "20", sibling_code, "group_take")]
    assert response.status_code == 200, response.text
    assert response.json()["revision_no"] == 1


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_unit_met_from_the_floor_and_the_water_confirms_as_proposed():
    """B1, the water half. The group nets 0 with 10 on a floor and an SPO of 20 landing in
    time, so the unit of 30 is Reserve 10 + Timely SPO 20 - and the split hands the whole of
    the water to line 32, whose own line-level water share is only 10.

    The timely cap is the UNIT's now, drawn down as its members are checked, so the two
    halves of one question cannot be counted twice and cannot refuse their own proposal.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _stock(db, product, own, on_hand=10)
        _spo_line(db, product, own, qty=20, arrives=REQUIRED_DATE - timedelta(days=5))
        db.commit()
        own_code = own.warehouse_code

        _core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, REQUIRED_DATE),
            ],
        )
        lines = _sheet(db, order)
        response = _confirm(
            db, company_id, eling, order,
            [_payload_line(lines[31]), _payload_line(lines[32])],
        )

    assert _stated_sheet(lines[31]) == [("reserve", "10", own_code, "group_take")]
    assert _stated_sheet(lines[32]) == [("timely_spo", "20", own_code, "group_take")]
    assert response.status_code == 200, response.text


# --------------------------------------------------------- the frozen proposal (S2)


def test_confirming_one_line_of_a_unit_freezes_the_proposal_the_sheet_showed():
    """S2. The frozen suggestion is what the planner was shown, so it is composed over the
    ORDER, not over the lines the payload happens to name.

    25 in the pool against a unit of 30. Under ladder v8 the walk takes the pool's share
    line by line (R-B/R-E): the 10 goes whole off the 12 the pool may lend a project, and
    line 32 finds 2 left and buys the other 18. Confirming line 32 on its own must freeze
    THAT - the composition of the line as it stood in its unit's walk - and not the
    composition of a line planned alone, which is not a line anybody was ever shown.
    """
    from ..test_so_supply_confirmation import _active_snapshots, _shape

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=25)

        _core_so, order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, REQUIRED_DATE),
            ],
        )
        # Read inside the session: the code is compared after it closes.
        pool_code = pool.warehouse_code
        lines = _sheet(db, order)
        response = _confirm(db, company_id, eling, order, [_payload_line(lines[32])])
        assert response.status_code == 200, response.text
        snapshots = {
            snapshot["line_no"]: snapshot for snapshot in _active_snapshots(db, order.id)
        }

    # The pool nets 25 and may lend a project 12 of it. Line 31 walks first (R-E, smallest
    # first) and takes 10, so line 32 finds 2 left of the share and buys the other 18.
    assert _stated_sheet(lines[32]) == [
        ("reserve", "2", pool_code, "pool"),
        ("buy", "18", None, "buy"),
    ]
    assert _shape(snapshots[32]["proposed_components"]) == _stated_sheet(lines[32])


# ------------------------------------------------------------- covered lines (S3)


def _cover(db, order, line, actor, *, components, hold=None):
    """An ACTIVE revision covering one line of the order, as a confirmation leaves it.

    `hold` is `(warehouse, qty)`: the allocation row a Reserve actually writes. Without one
    the decision covers the line but holds no stock, which is a decision that cannot show
    whether the lines around it are being offered stock it is standing on.
    """
    from datetime import datetime

    from app.models.project_so import SOLineAllocation, SOSupplyDecision

    decision = SOSupplyDecision(
        id=_uid(),
        company_id=order.company_id,
        project_sales_order_id=order.id,
        revision_no=1,
        state="active",
        line_snapshots=[
            {
                "project_line_id": str(line.id),
                "core_line_id": str(line.core_sales_order_line_id),
                "line_no": line.line_no,
                "open_qty": qty_text(line.qty),
                "components": components,
            }
        ],
        confirmed_by=actor,
        confirmed_at=datetime.utcnow(),
    )
    db.add(decision)
    db.flush()
    if hold is not None:
        warehouse, qty = hold
        db.add(
            SOLineAllocation(
                id=_uid(),
                company_id=order.company_id,
                so_line_id=line.id,
                source_type="own",
                warehouse_id=warehouse.id,
                qty=Decimal(str(qty)),
                decision_id=decision.id,
                confirmed_at=datetime.utcnow(),
            )
        )
    db.commit()
    return decision


def test_a_covered_line_is_out_of_the_unit_on_the_sheet_as_it_is_on_the_board():
    """S3. A covered line is not re-planned - the captain's standing rule - so it is not in
    anybody's planning unit either.

    The board already leaves it out of `proposable`; the sheet was still folding its
    quantity into the unit, so 25 in the pool covered the open line of 20 on the board and
    bought it on the sheet. Same order, same facts, two answers.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=25)

        core_so, order, mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, REQUIRED_DATE),
            ],
        )
        _cover(db, order, mirrors[0], eling, components=[])
        sheet = _sheet(db, order)
        board = _board(db, core_so, as_of=date.today())

    # LADDER V8 (R-B): the pool nets 25 and a project line may have 12 of it, so the open
    # line takes 12 and buys 8. What this case is about is unchanged - the covered line is
    # out of the unit on BOTH surfaces, so both give the open line the same answer.
    assert _stated_sheet(sheet[32]) == [
        ("reserve", "12", pool.warehouse_code, "pool"),
        ("buy", "8", None, "buy"),
    ]
    assert _stated_board(board[32]) == _stated_sheet(sheet[32])
    assert sheet[32]["unit_line_count"] == 1
    assert board[32]["unit_line_count"] == 1


# ----------------------------------------------------------------- the proof (S1)


def test_the_proof_never_offers_a_donor_the_walk_has_already_spent():
    """S1. AC-U9's board, read as a planner reads it: the donor's 10 went to the first
    delivery date, so the Buy on the later ones must not say it can be borrowed.

    Question 3 was recomputing its candidate list without the walk's donor ledger, so the
    proof said "free stock at DC1-NT, within the cross-group borrow limit" beside a Buy the
    same ledger had just forced.
    """
    from ..test_fulfilment_board import _step

    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor = _warehouse(db, f"ZZTDC1-NT{_uid()[:3]}")
        _stock(db, product, donor, on_hand=10)
        _policy(db, f"zzt-v6-{_uid()[:6]}")

        core_so, _order, _mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (1, "10", own, REQUIRED_DATE),
                (2, "12", own, REQUIRED_DATE + timedelta(days=14)),
            ],
        )
        board = _board(db, core_so, as_of=date.today())

    took = _step(board[1], "own")
    assert took["answer"] == "yes" and took["took"] == "10"
    spent = _step(board[2], "own")
    assert spent["answer"] == "no"
    assert "within the cross-group borrow limit" not in spent["why"], (
        "the donor was spent by the first date, so question 3 has nothing to offer"
    )
    buy = next(source for source in board[2]["sources"] if source["kind"] == "buy")
    assert donor.warehouse_code not in buy["reason"], (
        "and the Buy must not send a planner to a donor with nothing left"
    )


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_covered_siblings_hold_is_not_offered_again_to_the_open_line():
    """B-new. Taking the covered line out of the WALK is not the same as taking it out of
    the FACTS: its hold is still on the floor.

    `proposal_for` read the order with `replacing=None`, which un-nets every line's hold on
    the grounds that the sheet proposes for all of them. Once the covered line stopped being
    proposed for, that reading was simply wrong: 25 at the sibling with 10 of them held by
    the covered line is 15, and the sheet offered the open line all 25 - a Reserve the board
    never showed and the confirmation refuses, because `confirm` nets the hold (the line it
    replaces is the named one, not its sibling).

    The group nets 15 here (25 on a floor, 30 owed, 20 on the water), so the honest answer
    is 15 off the floor and 5 off the water, which is what all three surfaces now say.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        sibling, _sibling_pool = sites["MWH"]
        _stock(db, product, sibling, on_hand=25)
        _spo_line(db, product, own, qty=20, arrives=REQUIRED_DATE - timedelta(days=5))
        db.commit()

        core_so, order, mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "20", own, REQUIRED_DATE),
            ],
        )
        _cover(
            db, order, mirrors[0], eling,
            components=[
                {
                    "kind": "reserve",
                    "qty": "10",
                    "source_location": sibling.warehouse_code,
                    "source_warehouse_id": str(sibling.id),
                    "rung": "group_take",
                }
            ],
            hold=(sibling, 10),
        )
        sheet = _sheet(db, order)
        board = _board(db, core_so, as_of=date.today())
        response = _confirm(db, company_id, eling, order, [_payload_line(sheet[32])])
        own_code, sibling_code = own.warehouse_code, sibling.warehouse_code

    assert _stated_sheet(sheet[32]) == [
        ("reserve", "15", sibling_code, "group_take"),
        ("timely_spo", "5", own_code, "group_take"),
    ]
    assert _stated_board(board[32]) == _stated_sheet(sheet[32])
    assert response.status_code == 200, response.text
    # The covered line's OWN live proposal (what Amend starts from) is read with only its
    # own hold un-netted: the 10 it holds at the sibling is offered back to it, and nothing
    # more (AC-U12, second sentence).
    assert _stated_sheet(sheet[31]) == [("reserve", "10", sibling_code, "group_take")]


def test_a_released_lines_hold_is_free_for_the_line_the_board_gave_it_to():
    """Round 3 S1. A line named in `uncover_line_ids` stops holding its stock when this
    transaction commits, so `confirm` must not net that hold from the free stock the payload
    is judged against - the planning-change apply previews the released line exactly so
    (`exclude_covered_line_ids`), the board proposes the freed stock to the open line, and
    a confirm that still netted the hold refused the composition it had just shown.

    10 on the floor, all of it held by covered line 32. Releasing 32 and reserving the 10
    for line 31 in one confirm is the board's own proposal, and it goes through.
    """
    from app.schemas.project_supply import ConfirmSupplyBody

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _stock(db, product, own, on_hand=10)

        _core_so, order, mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "10", own, REQUIRED_DATE),
                (32, "10", own, REQUIRED_DATE),
            ],
        )
        _cover(
            db, order, mirrors[1], eling,
            components=[
                {
                    "kind": "reserve",
                    "qty": "10",
                    "source_location": own.warehouse_code,
                    "source_warehouse_id": str(own.id),
                    "rung": "group_take",
                }
            ],
            hold=(own, 10),
        )
        service = ProjectSupplyService(db)
        result = service.confirm(
            service.get_order(str(order.id)),
            ConfirmSupplyBody(
                lines=[
                    {
                        "project_line_id": str(mirrors[0].id),
                        "reserve": [{"warehouse_id": str(own.id), "qty": "10"}],
                    }
                ]
            ),
            actor_user_id=eling,
            uncover_line_ids=[str(mirrors[1].id)],
        )
        db.commit()

    assert result["revision_no"] == 2


def test_an_uncovered_line_is_in_the_unit_the_recheck_judges_against():
    """S-a. One covered set, for the freeze walk and for the recheck.

    A line named in `uncover_line_ids` is being RELEASED by this same transaction: it is not
    carried, so the frozen proposal composes it, and it therefore has to be in the unit the
    recheck seeds its ledgers from as well. Built from two sets, the freeze planned a unit of
    30 while the recheck judged line 31 as a unit of 20 - whose own group offer is 0 - and
    refused the 5 the proposal had just given it.
    """
    from app.schemas.project_supply import ConfirmSupplyBody

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, own, on_hand=5)
        _stock(db, product, pool, on_hand=25)

        _core_so, order, mirrors = _seed_order(
            db, company_id, project, product,
            lines=[
                (31, "20", own, REQUIRED_DATE),
                (32, "10", own, REQUIRED_DATE),
            ],
        )
        _cover(db, order, mirrors[1], eling, components=[])
        service = ProjectSupplyService(db)
        result = service.confirm(
            service.get_order(str(order.id)),
            ConfirmSupplyBody(
                lines=[
                    {
                        "project_line_id": str(mirrors[0].id),
                        "reserve": [
                            {"warehouse_id": str(own.id), "qty": "5"},
                            {"warehouse_id": str(pool.id), "qty": "15"},
                        ],
                    }
                ]
            ),
            actor_user_id=eling,
            uncover_line_ids=[str(mirrors[1].id)],
        )
        db.commit()

    assert result["revision_no"] == 2
