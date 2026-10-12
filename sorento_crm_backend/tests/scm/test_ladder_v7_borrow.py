"""Ladder v7.1: use -> borrow on hand -> borrow incoming -> pool -> buy.

`documentation/plans/scm/PLAN-scm-borrow-ladder-v7-stock-debt.md` section 3.2 and the UAC's
AC-S3-1 to AC-S3-14. S4 owns `supply_borrow`; here it is only ever asked and answered.

The rule that shapes every fixture below: **a step covers the WHOLE unit or gives nothing**
(R10/R33). Sources combine INSIDE one step - two bins of the group, two donors of step 2 -
and never across two, because half a promise off the free pile and half off somebody else's
order is two different stories about one delivery. The captain's own reading of AC-S4-2b
("the PO is taken whole ... the 16 on hand stays free") is what pins it.

The second rule that shapes them: under the ONE assignment both this ladder and the Stock
Debt view read (R21), a LATER order's supply is only "held by it" when something PINS it -
a confirmed decision or a placement link. Free stock is drawn at step 1 by whoever is due
first, whatever group it sits in, and raises no debt. So every `order_borrow` fixture here
confirms the donor's decision first: that is not scaffolding, it is the case (R9, R25).

Postgres via `blank_session`, every chain seeded here (CI's database has no data).
"""
from __future__ import annotations

import pytest

from datetime import date, timedelta
from decimal import Decimal

from app.models.project_so import (
    DECISION_ACTIVE,
    DECISION_SUPERSEDED,
    IV_BORROW_SHORTFALL,
    SO_STATUS_PUBLISHED,
    OrderInquiryRow,
    SOLineAllocation,
    SOSupplyDecision,
)
from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody
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
    _sorento,
    _stock,
    _uid,
    _user,
    _warehouse,
)
from .test_project_supply_service_ladder import (
    REQUIRED_DATE,
    _agent,
    _components,
    _group_sites,
    _lead_time,
    _seed_line,
    _world,
)

#: Every scenario states a 30-day lead so the reserve window is a number the fixture can
#: reason about: `today + 30 + 14`. A donor is eligible from day 44; the asking line at
#: `REQUIRED_DATE` (day 30) is inside its own window and therefore walks the ladder at all.
LEAD_DAYS = 30
WINDOW_DAY = LEAD_DAYS + 14


def _policy(
    db,
    name: str | None = None,
    *,
    overdue_grace_days: int | None = None,
    overdue_dead_days: int | None = None,
):
    priority.create_revision(
        db,
        name=name or f"zzt-v7-{_uid()[:6]}",
        factors={},
        demand_class_weights={},
        reorder_coverage_until=None,
        # None-means-unchanged all the way down to `create_revision`, which takes the
        # column's own SHIPPED default (0 / 0, captain's ruling 3 Sep 2026) when a caller
        # never asks for the grace explicitly - a caller that DOES care about it (R-O's own
        # suite) passes both.
        overdue_grace_days=overdue_grace_days,
        overdue_dead_days=overdue_dead_days,
    )
    db.commit()


def _decide(db, order, line, warehouse, qty, actor):
    """Confirm a Reserve for one line, which is what PINS its supply in the assignment.

    A decided line is exactly what step 2 borrows from (R9), and its decision is superseded
    inside the borrower's own Confirm (R25) - so the fixtures need a real one, not a stub.
    """
    ProjectSupplyService(db).confirm(
        order,
        ConfirmSupplyBody(
            lines=[
                ConfirmLine(
                    project_line_id=line.id,
                    reserve=[{"warehouse_id": str(warehouse.id), "qty": str(qty)}],
                )
            ]
        ),
        actor_user_id=actor,
    )
    db.commit()


def _options(proposal):
    return proposal["lines"][0]["options"]


# --------------------------------------------------------------------------- AC-S3-1


def test_the_own_group_covers_the_unit_from_its_own_bin_first_then_its_siblings():
    """AC-S3-1, first half: the own ownership group's free pile, in the draw order the
    ladder has always had - this line's own bin, then the siblings by code - and no debt."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        sibling, _sibling_pool = sites["MWH"]
        _stock(db, product, own, on_hand=10)
        _stock(db, product, sibling, on_hand=30)
        _lead_time(db, product, LEAD_DAYS)

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [(c["kind"], c["qty"], c["source_location"], c["rung"]) for c in components] == [
        ("reserve", "10", own.warehouse_code, "group_take"),
        ("reserve", "30", sibling.warehouse_code, "group_take"),
    ]
    assert not any(c.get("donor_so_number") for c in components)


def test_another_project_groups_free_pile_covers_the_unit_before_any_borrow_is_tried():
    """AC-S3-1, second half (R5): another PROJECT group's FREE pile is step 1's own second
    half. Free means owed to nobody, so it is a Reserve, it raises no order-back, and it is
    reached before either borrow step.

    LADDER V8 (R-A) puts the site pool in FRONT of step 1, so the pool holds nothing here -
    what this case is about is the free pile answering before a BORROW, and a pool that
    could also cover the unit would now answer first and test nothing.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        donor = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _stock(db, product, donor, on_hand=100)
        # The pool holds nothing (ladder v8, R-A: it is asked FIRST now).
        _stock(db, product, pool, on_hand=0)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)
        options = _options(proposal)

    assert [(c["kind"], c["qty"], c["source_location"], c["rung"]) for c in components] == [
        ("reserve", "40", donor.warehouse_code, "group_take"),
    ]
    assert [option["step"] for option in options if option["chosen"]] == ["use"]


# ----------------------------------------------------------------------- R40 (30 Aug)


def test_an_undecided_line_leaves_the_other_groups_pile_alone_in_the_assignment():
    """R40's first half, at the ladder's own door: the OFFER is made, the DRAW is not.

    The captain, on a BB line of 507 with no inquiry and no planning that had eaten the IB
    pile: "who is us to decide those BB group takes our IB pile". So `assign()` reads the
    BB line as `short` and leaves IB's stock free for IB, while `use_candidates_for` still
    puts it on the table - the two are different acts and only the second is the ladder's.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _stock(db, product, donor, on_hand=100)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        order, _line, _cso, core_line = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        service = ProjectSupplyService(db)
        result = service.planning_assignments([str(product.id)])[str(product.id)]
        row = next(r for r in result.lines if r.line.key == str(core_line.id))
        status, uncovered = row.status, row.uncovered
        free_at_donor = result.free[f"on_hand:{donor.id}"]
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert (status, uncovered) == ("short", 40.0), (
        "the BB line draws its own group only, and its own group holds nothing"
    )
    assert free_at_donor == 100.0, "IR's pile is untouched by a line nobody decided"
    # ...and the ladder still OFFERS it, which is R40's other half.
    assert [(c["kind"], c["qty"], c["source_location"], c["rung"]) for c in components] == [
        ("reserve", "40", donor.warehouse_code, "group_take"),
    ]


def test_a_confirmed_cross_group_hold_depletes_that_groups_pile_for_the_next_asker():
    """R40's second half: the OFFER becomes an assumption the moment somebody Confirms it.

    RE-BLESSED under R-M (3 Sep 2026). The case used to hold 100 at the IR bin against IR's
    own later line of 80, let a BB line confirm 40 of it, and assert that IR's own asker
    then went `short` by 20. Under R-M that Confirm is refused before it is written ("IR now
    has 20 free for this line, and 40 was asked for"), because what another group may lend
    is bounded by its WHOLE open book and IR's book was 20. A cross-group Confirm can no
    longer make the lending group's own line go short - which is the ruling, not a loss of
    coverage here.

    So the same story is told with the numbers the cap allows: IR holds 120 against its own
    80, so its book spares exactly 40, and the BB line confirms all of it. IR's own line
    stays `covered` - R-M never lends away a group's own cover - and the PILE is gone: 40
    free at the IR bin before the Confirm, nothing after it, so the next asker is offered
    nothing at all.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _stock(db, product, donor, on_hand=120)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        asker, asker_line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40", line_no=1,
            so_number=f"ZZTSO-BB{_uid()[:5]}",
        )
        later, _later_line, _lcso, later_core = _seed_line(
            db, company_id, project, product, donor, qty_ordered="80", line_no=2,
            required_date=REQUIRED_DATE + timedelta(days=5),
            so_number=f"ZZTSO-IR{_uid()[:5]}",
        )

        service = ProjectSupplyService(db)
        before = service.planning_assignments([str(product.id)])[str(product.id)]
        before_status = next(
            r.status for r in before.lines if r.line.key == str(later_core.id)
        )
        before_free = before.free[f"on_hand:{donor.id}"]
        # The offer the BB line is made is the group's BOOK, not the date-bounded pile:
        # 120 less IR's own 80, which is exactly the 40 it asks for.
        asker_components = _components(
            ProjectSupplyService(db).proposal_for(asker)
        )

        # The BB line takes the offer: a Reserve at the IR bin, confirmed.
        _decide(db, asker, asker_line, donor, "40", eling)

        service = ProjectSupplyService(db)
        after = service.planning_assignments([str(product.id)])[str(product.id)]
        after_row = next(
            r for r in after.lines if r.line.key == str(later_core.id)
        )
        after_free = after.free[f"on_hand:{donor.id}"]
        later_components = _components(
            ProjectSupplyService(db).proposal_for(later)
        )
        donor_code = donor.warehouse_code

    assert before_status == "covered", "IR's own line had the whole pile before the Confirm"
    assert before_free == 40.0, "40 of IR's 120 is spare once its own 80 is met"
    assert [(c["kind"], c["qty"], c["source_location"]) for c in asker_components] == [
        ("reserve", "40", donor_code),
    ]
    assert (after_row.status, after_row.uncovered) == ("covered", 0.0), (
        "R-M lends only what IR's own book can spare, so IR's own line is never the payer"
    )
    assert after_free == 0.0, "the pile is spoken for: the next asker is offered nothing"
    assert [(c["kind"], c["qty"]) for c in later_components] == [("reserve", "80")], (
        "IR's own line still draws its own group's stock, which nobody took from it"
    )


# --------------------------------------------------------------------------- AC-S3-1b


def test_the_free_pile_is_read_at_the_askers_own_date_srtwb242():
    """AC-S3-1b, the captain's own book, measured on SRTWB242 (R24).

    BB holds 199 on hand. Its lines are due 15 Jul 144 (past due), 15 Sep 27, 15 Oct 12,
    26 Oct 32, 24 Dec 12, Jun 2027 501 and 1 Jan 2030 100.

    JEREMY's 27 due 15 September sees 199 - 144 = 55 free BY HIS DATE and reserves his 27,
    although plain Available across the whole book is -1,156. JAY's 32 due 26 October sees
    199 - 144 - 27 - 12 = 16, which is not the whole of his unit, so step 1 gives him
    nothing. Later lines never count against either of them.

    Dated relative to today so the fixture keeps its meaning: the two askers sit inside the
    reserve window, everything else is spread around them in the same order the real book
    has them.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _stock(db, product, own, on_hand=199)
        _lead_time(db, product, LEAD_DAYS)

        def other(qty, days):
            core_so = _core_so(db, company_id)
            core_so.so_number = f"ZZT-SO-{_uid()[:8]}"
            db.flush()
            _core_line(
                db, core_so, product, own, qty_ordered=str(qty),
                required_date=date.today() + timedelta(days=days),
            )

        other(144, -20)   # 15 Jul: past due, read at today, ahead of everybody
        other(12, 25)     # 15 Oct
        other(12, 90)     # 24 Dec
        other(501, 300)   # Jun 2027
        other(100, 1200)  # 1 Jan 2030
        db.commit()

        jeremy, jeremy_line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="27",
            required_date=date.today() + timedelta(days=10),
        )
        jay, _jay_line, _jcso, _jcline = _seed_line(
            db, company_id, project, product, own, qty_ordered="32",
            required_date=date.today() + timedelta(days=40),
        )

        jeremy_components = _components(ProjectSupplyService(db).proposal_for(jeremy))
        jay_components = _components(ProjectSupplyService(db).proposal_for(jay))

        # The pile behind both answers, off the ONE assignment the board and the Stock Debt
        # view share (R21): 199 less the 144 that is past due leaves 55 by JEREMY's date,
        # and 199 less 144, 27 and 12 leaves 16 by JAY's.
        service = ProjectSupplyService(db)
        result = service.planning_assignments([str(product.id)])[str(product.id)]
        short = {
            (row.line.so_number, str(row.line.open_qty)): row.short_at_date
            for row in result.lines
        }
        jay_short = [
            row.short_at_date
            for row in result.lines
            if abs(row.line.open_qty - 32.0) < 0.001
        ]

    assert [(c["kind"], c["qty"]) for c in jeremy_components] == [("reserve", "27")], (
        "55 is free by 15 September, so the 27 is reserved"
    )
    assert [(c["kind"], c["qty"]) for c in jay_components] == [("buy", "32")], (
        "16 is free by 26 October, which is not the whole unit, so step 1 gives nothing"
    )
    assert jay_short == [16.0], (
        f"JAY draws the 16 that is left and goes without the rest: {short}"
    )


# ------------------------------------------------ AC-S3-1b, the confirm side (30 Aug)
#
# The board proposes off the DATE-AWARE pile and the confirmation used to re-derive its
# Reserve capacity off ladder v4's UNDATED group offer, so on a book where later demand
# dominates (which is what AC-S3-1b is ABOUT) the two disagreed and the recheck refused the
# engine's own answer: SO381895's `Confirm (76)` came back "34 lines cannot be confirmed.
# Nothing was written", every refusal "<bin> has nothing free for this line now" in front of
# a row the board itself was proposing as `Use own location`.


def _reserve_payload(line, components):
    """The board's OWN proposal for one line, re-sent verbatim (R24, AC-S3-1b).

    Not a hand-typed body: whatever the walk proposed is what is posted back, which is the
    only thing that makes "a verbatim re-send always confirms" a statement about the engine
    rather than about the fixture.
    """
    return ConfirmLine(
        project_line_id=line.id,
        reserve=[
            {"warehouse_id": c["source_warehouse_id"], "qty": c["qty"]}
            for c in components
            if c["kind"] == "reserve"
        ],
        buy_qty=sum(
            (Decimal(c["qty"]) for c in components if c["kind"] == "buy"), Decimal("0")
        ),
    )


def _srtwb242_book(db, company_id, project, product, own):
    """SRTWB242's shape: 199 on the floor, 144 of it past due, and the rest of the book
    behind the asker - so plain Available is deeply negative while the pile IS free by an
    early enough date (AC-S3-1b)."""
    _stock(db, product, own, on_hand=199)
    _lead_time(db, product, LEAD_DAYS)

    def other(qty, days):
        core_so = _core_so(db, company_id)
        core_so.so_number = f"ZZT-SO-{_uid()[:8]}"
        db.flush()
        _core_line(
            db, core_so, product, own, qty_ordered=str(qty),
            required_date=date.today() + timedelta(days=days),
        )

    other(144, -20)   # past due, read at today, ahead of everybody
    other(12, 25)
    other(12, 90)
    other(501, 300)
    other(100, 1200)
    db.commit()


def test_a_date_aware_use_proposal_confirms_verbatim_although_available_is_negative():
    """AC-S3-1b on the CONFIRM side: the board proposes `Use own location` off the pile that
    is free by the asker's date, and the same payload is accepted.

    JEREMY's 27 is due inside the window with 55 free by his date, while the group's plain
    Available across the whole book is -602. The recheck seeded its capacity from the
    undated number, read `nothing free for this line now`, and refused the composition the
    ladder had just written."""
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _srtwb242_book(db, company_id, project, product, own)

        jeremy, jeremy_line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="27",
            required_date=date.today() + timedelta(days=10),
        )
        service = ProjectSupplyService(db)
        components = _components(service.proposal_for(jeremy))
        service.confirm(
            jeremy,
            ConfirmSupplyBody(lines=[_reserve_payload(jeremy_line, components)]),
            actor_user_id=eling,
        )
        db.commit()

        decision = (
            db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id == jeremy.id,
                SOSupplyDecision.state == DECISION_ACTIVE,
            )
            .one()
        )
        holds = [
            (str(a.warehouse_id), str(a.source_type), Decimal(str(a.qty)))
            for a in db.query(SOLineAllocation)
            .filter(SOLineAllocation.so_line_id == jeremy_line.id)
            .all()
        ]
        own_id = str(own.id)

    assert [(c["kind"], c["qty"], c["rung"]) for c in components] == [
        ("reserve", "27", "group_take")
    ], "55 is free by his date, so the whole 27 comes off the own group"
    assert decision.revision_no == 1
    assert holds == [(own_id, "own", Decimal("27"))], holds


def test_a_reserve_beyond_the_date_aware_pile_holds_only_the_pile_and_records_the_short():
    """The other half of the same seeding: the pile is the WALK's number, not the whole
    floor. JAY's 32 is due after JEREMY's 27 and the 12 behind it, so 16 is free by his
    date - and a hand-composed Reserve for the whole 32 holds the 16 and records 16 short.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _srtwb242_book(db, company_id, project, product, own)

        _jeremy, _jl, _jcso, _jcline = _seed_line(
            db, company_id, project, product, own, qty_ordered="27",
            required_date=date.today() + timedelta(days=10),
        )
        jay, jay_line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="32",
            required_date=date.today() + timedelta(days=40),
        )
        service = ProjectSupplyService(db)
        jay_components = _components(service.proposal_for(jay))
        result = service.confirm(
            jay,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=jay_line.id,
                        reserve=[{"warehouse_id": str(own.id), "qty": "32"}],
                    )
                ]
            ),
            actor_user_id=eling,
        )
        held = [
            Decimal(str(a.qty))
            for a in db.query(SOLineAllocation)
            .filter(SOLineAllocation.so_line_id == jay_line.id)
            .all()
        ]
        db.rollback()
        own_code = own.warehouse_code

    assert [(c["kind"], c["qty"]) for c in jay_components] == [("buy", "32")], (
        "16 is not the whole unit, so the board proposes a Buy"
    )
    [notice] = result["stock_short_notices"]
    assert notice["reason"] == (
        f"Reserve 32 at {own_code} confirmed as decided; only 16 free at {own_code} "
        "when confirmed, 16 short"
    ), notice
    assert held == [Decimal("16")], "32 was asked for off a pile that is 16 by his date"


def test_two_lines_of_one_confirm_share_one_pile_and_the_second_is_capped():
    """The ledger, unchanged: the pile is seeded once for the confirmation and drawn down as
    its lines are checked (S7), so the second line sees what the first left.

    100 on the floor, 60 of it owed to an earlier order that has decided nothing - so it
    holds no stock a Reserve competes with, and only the DATED walk knows it is spoken for.
    The board gives line 10 its 30 and line 20 the 10 that is left; line 20 is hand-composed
    for 30 anyway, holds the 10 that remains (not what is on the floor) and is the only line
    recorded short.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _stock(db, product, own, on_hand=100)
        _lead_time(db, product, LEAD_DAYS)
        earlier = _core_so(db, company_id)
        earlier.so_number = f"ZZT-SO-{_uid()[:8]}"
        db.flush()
        _core_line(
            db, earlier, product, own, qty_ordered="60",
            required_date=date.today() + timedelta(days=3),
        )
        db.commit()

        order, first_line, core_so, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="30",
            required_date=date.today() + timedelta(days=10),
        )
        second_core = _core_line(
            db, core_so, product, own, qty_ordered="30",
            required_date=date.today() + timedelta(days=40),
        )
        second_line = _project_line(
            db, order, line_no=20, product=product, core_line=second_core
        )
        db.commit()

        service = ProjectSupplyService(db)
        proposal = service.proposal_for(order)
        stated = {
            row["line_no"]: [(c["kind"], c["qty"]) for c in row["components"]]
            for row in proposal["lines"]
        }
        result = service.confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=first_line.id,
                        reserve=[{"warehouse_id": str(own.id), "qty": "30"}],
                    ),
                    ConfirmLine(
                        project_line_id=second_line.id,
                        reserve=[{"warehouse_id": str(own.id), "qty": "30"}],
                    ),
                ]
            ),
            actor_user_id=eling,
        )
        held = {
            a.so_line_id: Decimal(str(a.qty))
            for a in db.query(SOLineAllocation)
            .filter(SOLineAllocation.so_line_id.in_([first_line.id, second_line.id]))
            .all()
        }
        first_id, second_id = first_line.id, second_line.id
        db.rollback()
        own_code = own.warehouse_code

    assert stated[10] == [("reserve", "30")], "30 is free by the first line's date"
    assert stated[20] == [("buy", "30")], "10 is left by the second's, which is not whole"
    assert [row["line_no"] for row in result["stock_short_notices"]] == [20], (
        f"only the second line is short: {result['stock_short_notices']}"
    )
    assert result["stock_short_notices"][0]["reason"] == (
        f"Reserve 30 at {own_code} confirmed as decided; only 10 free at {own_code} "
        "when confirmed, 20 short"
    ), result["stock_short_notices"]
    assert held == {first_id: Decimal("30"), second_id: Decimal("10")}, (
        "the second line asked for a pile the first had emptied and holds what was left"
    )


# --------------------------------------------------------------------------- AC-S3-2


def _borrow_world(db, *, donor_days=WINDOW_DAY + 20, donor_qty="30", ask="30"):
    """An asker whose own group holds nothing, and one DECIDED later order that does.

    The donor's stock is pinned by its own confirmed decision, which is what takes it out of
    the free pile step 1 reads and puts it on step 2's table (R9).
    """
    company_id, eling, project, product = _world(db)
    _group, sites = _group_sites(db)
    own, _pool = sites["BRW"]
    donor_bin = _warehouse(db, f"ZZTMWH-IB{_uid()[:3]}")
    _stock(db, product, donor_bin, on_hand=int(donor_qty))
    _lead_time(db, product, LEAD_DAYS)
    _policy(db)

    donor_order, donor_line, _dcso, donor_cline = _seed_line(
        db, company_id, project, product, donor_bin, qty_ordered=donor_qty,
        required_date=date.today() + timedelta(days=donor_days), line_no=2,
        so_number=f"ZZT-SO-DONOR{_uid()[:4]}",
    )
    _decide(db, donor_order, donor_line, donor_bin, donor_qty, eling)

    asker, asker_line, _cso, _cline = _seed_line(
        db, company_id, project, product, own, qty_ordered=ask, line_no=1,
        so_number=f"ZZT-SO-ASK{_uid()[:5]}",
    )
    return {
        "company_id": company_id, "eling": eling, "product": product, "own": own,
        "donor_bin": donor_bin, "donor_order": donor_order, "donor_line": donor_line,
        "donor_cline": donor_cline, "asker": asker, "asker_line": asker_line,
    }


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_decided_later_order_lends_its_on_hand_and_the_component_names_the_donor():
    """AC-S3-2: kind `borrow`, rung `order_borrow`, the donor named, the order-back sized."""
    with blank_session() as db:
        borrow_from_order_on(db)
        world = _borrow_world(db)
        proposal = ProjectSupplyService(db).proposal_for(world["asker"])
        components = _components(proposal)
        donor_so = world["donor_order"]

    assert len(components) == 1
    component = components[0]
    assert component["kind"] == "borrow"
    assert component["rung"] == "order_borrow"
    assert component["qty"] == "30"
    assert component["donor_so_number"]
    assert component["donor_required_date"]
    assert component["order_back_qty"] == "30" or component["qty"] == "30"
    assert "on hand at" in component["reason"]
    assert "its debt lands in" in component["reason"]
    assert donor_so is not None


@pytest.mark.usefixtures("ladder_steps_on")
def test_the_same_agent_donates_before_a_later_dated_donor_of_another_agent():
    """AC-S3-2 / R4: same agent first, THEN the latest required date.

    Both donors are decided and both are inside the window; the other agent's order is due
    later, which would win on date alone. It does not, because she can authorise moving
    stock between her OWN orders and cannot authorise moving somebody else's.
    """
    with blank_session() as db:
        borrow_from_order_on(db)
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        mine = _agent(db, f"CYNDI{_uid()[:4]}")
        theirs = _agent(db, f"JEREMY{_uid()[:4]}")
        near_bin = _warehouse(db, f"ZZTMWH-IB{_uid()[:3]}")
        far_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _stock(db, product, near_bin, on_hand=30)
        _stock(db, product, far_bin, on_hand=30)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        same_agent_order, same_agent_line, _a, _b = _seed_line(
            db, company_id, project, product, near_bin, qty_ordered="30",
            required_date=date.today() + timedelta(days=WINDOW_DAY + 20), line_no=2,
            so_number="ZZTSO-SAMEAGENT", sales_agent_id=mine.id,
        )
        _decide(db, same_agent_order, same_agent_line, near_bin, "30", eling)
        other_order, other_line, _c, _d = _seed_line(
            db, company_id, project, product, far_bin, qty_ordered="30",
            required_date=date.today() + timedelta(days=WINDOW_DAY + 200), line_no=3,
            so_number="ZZTSO-OTHERAGENT", sales_agent_id=theirs.id,
        )
        _decide(db, other_order, other_line, far_bin, "30", eling)

        asker, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="30", line_no=1,
            so_number="ZZTSO-ASKING", sales_agent_id=mine.id,
        )
        components = _components(ProjectSupplyService(db).proposal_for(asker))

    assert [c["rung"] for c in components] == ["order_borrow"]
    assert components[0]["donor_so_number"] == "ZZTSO-SAMEAGENT"
    assert components[0]["same_agent"] is True


# --------------------------------------------------------------------------- AC-S3-3


@pytest.mark.usefixtures("ladder_steps_on")
def test_between_two_other_agents_the_later_required_date_donates_first():
    """AC-S3-3: on equal agent standing, the order that can wait longest gives it up."""
    with blank_session() as db:
        borrow_from_order_on(db)
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        near_bin = _warehouse(db, f"ZZTMWH-IB{_uid()[:3]}")
        far_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _stock(db, product, near_bin, on_hand=30)
        _stock(db, product, far_bin, on_hand=30)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        near_order, near_line, _a, _b = _seed_line(
            db, company_id, project, product, near_bin, qty_ordered="30",
            required_date=date.today() + timedelta(days=WINDOW_DAY + 20), line_no=2,
            so_number="ZZTSO-NEARDATE",
        )
        _decide(db, near_order, near_line, near_bin, "30", eling)
        far_order, far_line, _c, _d = _seed_line(
            db, company_id, project, product, far_bin, qty_ordered="30",
            required_date=date.today() + timedelta(days=WINDOW_DAY + 200), line_no=3,
            so_number="ZZTSO-FARDATE",
        )
        _decide(db, far_order, far_line, far_bin, "30", eling)

        asker, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="30", line_no=1,
            so_number="ZZTSO-ASKING",
        )
        components = _components(ProjectSupplyService(db).proposal_for(asker))

    assert [c["rung"] for c in components] == ["order_borrow"]
    assert components[0]["donor_so_number"] == "ZZTSO-FARDATE"


# --------------------------------------------------------------------------- AC-S3-4


def test_a_donor_inside_the_window_past_due_tba_or_undated_is_never_offered():
    """AC-S3-4: four donors, four refusals, and the trail names the window date.

    None of them can be the one who waits: one is due before `today + lead + 14` (buying for
    a replacement would not land in time for it either), one is already past due, one is a
    2030 placeholder and one states no date at all.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        for label, days in (
            ("INSIDE", WINDOW_DAY - 5),
            ("PASTDUE", -10),
            ("TBA", 1200),
        ):
            donor_bin = _warehouse(db, f"ZZT{label[:3]}MWH-IB{_uid()[:3]}"[:20])
            _stock(db, product, donor_bin, on_hand=30)
            donor_order, donor_line, _a, _b = _seed_line(
                db, company_id, project, product, donor_bin, qty_ordered="30",
                required_date=date.today() + timedelta(days=days), line_no=2,
                so_number=f"ZZTSO-{label}",
            )
            _decide(db, donor_order, donor_line, donor_bin, "30", eling)

        undated_bin = _warehouse(db, f"ZZTUND-IB{_uid()[:3]}"[:20])
        _stock(db, product, undated_bin, on_hand=30)
        undated_order, undated_line, _a, _b = _seed_line(
            db, company_id, project, product, undated_bin, qty_ordered="30",
            required_date=None, line_no=2, so_number="ZZTSO-UNDATED",
        )
        _decide(db, undated_order, undated_line, undated_bin, "30", eling)

        asker, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="30", line_no=1,
            so_number="ZZTSO-ASKING",
        )
        service = ProjectSupplyService(db)
        components = _components(service.proposal_for(asker))
        facts = service._facts_for(asker, service.lines_of(str(asker.id)))
        fact = next(iter(facts.values()))
        donors = service.order_borrow_candidates_for(fact)

    assert donors == [], donors
    assert not any(c["rung"] == "order_borrow" for c in components), components


def test_the_step_two_refusal_names_the_window_date():
    """AC-S3-4's sentence: "no later order dated on or after <date> holds any of this on
    hand". A refusal that only says "no donor" sends a planner nowhere."""
    from app.services.project_fulfilment_board_service import FulfilmentBoardService

    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)
        asker, _line, core_so, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="30", line_no=1,
            so_number=f"ZZTSO-WINDOW{_uid()[:4]}",
        )
        board = FulfilmentBoardService(db).build(
            [core_so.so_number], granularity="week", as_of=date.today()
        )
        contributions = [
            contribution
            for cell in board["cells"]
            for contribution in cell["contributions"]
        ]
        step = next(
            s for s in contributions[0]["trail"] if s["kind"] == "order_borrow"
        )
        window = date.today() + timedelta(days=WINDOW_DAY)

    assert step["answer"] == "no"
    assert f"{window.day} " in step["why"], step["why"]
    assert str(window.year) in step["why"], step["why"]


# --------------------------------------------------------------------------- AC-S3-5


@pytest.mark.usefixtures("ladder_steps_on")
def test_borrowing_from_a_decided_donor_supersedes_its_decision_and_raises_its_order_back():
    """AC-S3-5 (R9, R25). The donor's own decision is superseded in the SAME transaction,
    with the borrower named in the reason, and the donor gets an ORDER_BACK at its own
    required date. Its next board build re-proposes it, because it is no longer covered."""
    with blank_session() as db:
        borrow_from_order_on(db)
        world = _borrow_world(db)
        service = ProjectSupplyService(db)
        proposal = service.proposal_for(world["asker"])
        component = _components(proposal)[0]

        ProjectSupplyService(db).confirm(
            world["asker"],
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=world["asker_line"].id,
                        borrow=[
                            {
                                "source": "other_location",
                                "warehouse_id": str(world["donor_bin"].id),
                                "qty": component["qty"],
                                "reason": "Ladder v7.1 step 2.",
                                "donor_core_line_id": component["donor_core_line_id"],
                                "donor_so_number": component["donor_so_number"],
                                "donor_line_no": component["donor_line_no"],
                                "donor_required_date": component["donor_required_date"],
                            }
                        ],
                    )
                ]
            ),
            actor_user_id=world["eling"],
        )
        db.commit()

        donor_decisions = (
            db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id == world["donor_order"].id
            )
            .all()
        )
        rows = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
            .all()
        )
        donor_date = world["donor_line"]
        states = [row.state for row in donor_decisions]
        reasons = [row.superseded_reason for row in donor_decisions]
        raised = [(r.qty, r.stock_location, r.delivery_date) for r in rows]

    assert DECISION_SUPERSEDED in states, states
    assert any(
        reason and reason.startswith("Borrowed by SO") for reason in reasons
    ), reasons
    assert DECISION_ACTIVE not in states, (
        "a donor whose stock has been taken is not covered any more"
    )
    assert len(raised) == 1
    assert raised[0][0] == Decimal("30")
    assert donor_date is not None


def test_the_supply_sheet_route_serialises_a_step_two_donor_through_its_response_model():
    """The review's HIGH: `_order_borrow_offer_rows` built donor rows with no
    `donor_impact`, which `BorrowCandidate` requires - so `GET .../supply` 500'd with a
    `ResponseValidationError` for EVERY line that had a step-2 donor at all.

    Through the real route and the real `response_model`, because that is where it broke: a
    hand-built schema round-trip never sees the dict the service actually produced.
    """
    from app.models.base import company_scope

    from tests.test_fulfilment_board import BASE, VIEW, _client, _restore

    with blank_session() as db:
        borrow_from_order_on(db)
        world = _borrow_world(db)
        db.commit()
        client, originals = _client(db, world["eling"], [VIEW])
        try:
            with company_scope(db, frozenset({world["company_id"]})):
                response = client.get(
                    f"{BASE}/sales-orders/{world['asker'].id}/supply"
                )
        finally:
            _restore(originals)
        assert response.status_code == 200, response.text
        body = response.json()
        line = body["lines"][0]
        donors = [
            candidate
            for candidate in line["borrow_candidates"]
            if candidate.get("rung") == "order_borrow"
        ]

    assert donors, line["borrow_candidates"]
    donor = donors[0]
    assert donor["donor_impact"]["committed_qty"] == "30"
    assert donor["donor_impact"]["free_before"] is not None
    assert donor["donor_so_number"]
    # AC-S3-11 / `BorrowCandidate`: EXACTLY ONE row is the recommendation, and it is the
    # one the engine itself would take.
    heads = [c for c in line["borrow_candidates"] if c["recommended"]]
    assert len(heads) == 1, line["borrow_candidates"]
    assert heads[0]["rung"] == "order_borrow"


def test_the_engines_own_step_two_proposal_passes_the_confirm_gate_unchanged():
    """Review finding 10, reproduced as far as it can be: `_check_group_borrow`'s
    same-agent authorisation gate refusing the ENGINE's own step-2 proposal.

    The gate refuses a donor that shares this line's sales agent and is ranked AHEAD of it
    unless the reason says "authorised by" - and the server's own `order_borrow_reason`
    says no such thing, so a proposal confirmed AS PROPOSED would 422.

    It does not fire, and the reason is structural rather than lucky. The gate is judged on
    `_group_borrow_donors`, which (a) reads THIS line's ownership group only and (b)
    excludes any line another active decision already covers (`_decided_elsewhere`). A
    step-2 donor is by definition a line the assignment reads as `covered` or `pinned` off
    ON HAND - and on hand is pinned only by a confirmed decision (`stock_debt_service
    ._holds`; a placement link pins a DOCUMENT, which is step 3's business). So every
    decided donor is out of the gate's list, and an UNDECIDED covered donor of the asker's
    own group cannot exist while the asker is short: the asker is earlier, so the walk gives
    it the group's pile first.

    This test builds the strongest case the rule allows - same agent, same ownership group,
    ranked ahead, decided - and confirms the proposal verbatim, reason and all.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        sibling, _sp = sites["MWH"]
        mine = _agent(db, f"CYNDI{_uid()[:4]}")
        _stock(db, product, sibling, on_hand=30)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        donor_order, donor_line, _a, _b = _seed_line(
            db, company_id, project, product, sibling, qty_ordered="30",
            required_date=date.today() + timedelta(days=WINDOW_DAY + 20), line_no=2,
            so_number=f"ZZTSO-SAMEAG{_uid()[:4]}", sales_agent_id=mine.id,
        )
        _decide(db, donor_order, donor_line, sibling, "30", eling)

        asker, asker_line, _c, _d = _seed_line(
            db, company_id, project, product, own, qty_ordered="30", line_no=1,
            so_number=f"ZZTSO-ASK{_uid()[:5]}", sales_agent_id=mine.id,
        )
        component = _components(ProjectSupplyService(db).proposal_for(asker))[0]
        assert component["rung"] == "order_borrow", component

        # Confirmed EXACTLY as proposed, with the server's own sentence as the reason -
        # which is the thing the gate would have refused.
        ProjectSupplyService(db).confirm(
            asker,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=asker_line.id,
                        borrow=[
                            {
                                "source": "other_location",
                                "warehouse_id": str(sibling.id),
                                "qty": component["qty"],
                                "reason": component["reason"],
                                "donor_core_line_id": component["donor_core_line_id"],
                                "donor_so_number": component["donor_so_number"],
                                "donor_line_no": component["donor_line_no"],
                                "donor_required_date": component["donor_required_date"],
                            }
                        ],
                    )
                ]
            ),
            actor_user_id=eling,
        )
        db.commit()
        written = (
            db.query(SOLineAllocation)
            .filter(SOLineAllocation.so_line_id == asker_line.id)
            .all()
        )
        sources = sorted({row.source_type for row in written})

    assert sources == ["other_location"], sources


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_multi_line_donor_keeps_every_other_lines_hold_when_one_line_is_borrowed():
    """R25 as fixed 30 Aug (review finding: `_supersede_borrowed_donors`).

    The donor order covers FIVE lines. Line 3 is borrowed, so line 3 stops being covered -
    and lines 1, 2, 4 and 5 keep the stock they were promised. Flipping the donor's whole
    active revision to SUPERSEDED released all five, silently, with an ORDER_BACK raised for
    one: four customers' promises disappeared because a fifth's was taken.
    """
    with blank_session() as db:
        borrow_from_order_on(db)
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTMWH-IB{_uid()[:3]}")
        _stock(db, product, donor_bin, on_hand=50)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        # ONE donor order, five lines of 10 at the same bin, all due beyond the window.
        donor_core = _core_so(db, company_id)
        donor_core.so_number = f"ZZTSO-FIVE{_uid()[:4]}"
        db.flush()
        donor_order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
        donor_lines = []
        for line_no in range(1, 6):
            core_line = _core_line(
                db, donor_core, product, donor_bin, qty_ordered="10",
                required_date=date.today() + timedelta(days=WINDOW_DAY + 20 + line_no),
            )
            donor_lines.append(
                _project_line(
                    db, donor_order, line_no=line_no, product=product,
                    core_line=core_line,
                )
            )
        db.commit()
        # One line at a time, which is how the board decides them: the group nets to zero
        # with all five open, so its offer to any ONE line is that line's own quantity, and
        # `confirm`'s carry-forward keeps the ones already decided.
        for line in donor_lines:
            _decide(db, donor_order, line, donor_bin, "10", eling)

        asker, asker_line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="10", line_no=1,
            so_number=f"ZZTSO-ASK{_uid()[:5]}",
        )
        proposal = ProjectSupplyService(db).proposal_for(asker)
        component = _components(proposal)[0]
        assert component["rung"] == "order_borrow", proposal
        # WHICHEVER line R19 picked (latest required date first), not one the fixture
        # decided: the point is that the other four keep their holds, not which one went.
        borrowed_line = next(
            line
            for line in donor_lines
            if str(line.core_sales_order_line_id)
            == str(component["donor_core_line_id"])
        )

        ProjectSupplyService(db).confirm(
            asker,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=asker_line.id,
                        borrow=[
                            {
                                "source": "other_location",
                                "warehouse_id": str(donor_bin.id),
                                "qty": component["qty"],
                                "reason": "Ladder v7.1 step 2.",
                                "donor_core_line_id": component["donor_core_line_id"],
                                "donor_so_number": component["donor_so_number"],
                                "donor_line_no": component["donor_line_no"],
                                "donor_required_date": component["donor_required_date"],
                            }
                        ],
                    )
                ]
            ),
            actor_user_id=eling,
        )
        db.commit()

        borrowed_id = str(component["donor_core_line_id"])
        active = (
            db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id == donor_order.id,
                SOSupplyDecision.state == DECISION_ACTIVE,
            )
            .one_or_none()
        )
        superseded = (
            db.query(SOSupplyDecision)
            .filter(
                SOSupplyDecision.project_sales_order_id == donor_order.id,
                SOSupplyDecision.state == DECISION_SUPERSEDED,
            )
            .all()
        )
        still_covered = (
            sorted(
                snapshot["project_line_id"] for snapshot in (active.line_snapshots or [])
            )
            if active is not None
            else []
        )
        held = {
            str(row.so_line_id)
            for row in db.query(SOLineAllocation)
            .filter(SOLineAllocation.decision_id == (active.id if active else None))
            .all()
        }
        expected = sorted(
            str(line.id) for line in donor_lines if str(line.id) != str(borrowed_line.id)
        )
        borrowed_no = borrowed_line.line_no
        expected_reason = (
            f"Borrowed by SO{asker.provisional_ref} line {asker_line.line_no}"
        )
        superseded_reasons = [row.superseded_reason for row in superseded]

    assert borrowed_id, borrowed_id
    assert borrowed_no in (1, 2, 3, 4, 5)
    assert active is not None, "the donor's other four lines are still decided"
    assert still_covered == expected, still_covered
    assert held == set(expected), (
        "every surviving line's hold moves to the re-issued revision, or it stops holding"
    )
    # Four of the five superseded rows are the donor's own re-confirmations ("Reconfirmed by
    # CS."), one per line; exactly ONE is the re-issue this borrow caused, and it names it.
    assert superseded_reasons.count(expected_reason) == 1, superseded_reasons


# --------------------------------------------------------------------------- AC-S3-6


def test_a_partial_borrow_is_dropped_and_the_unit_buys_whole():
    """AC-S3-6 (R10): the donor holds 20 of a unit of 30, so nothing is borrowed at all and
    the whole unit is bought - and the Buy's own sentence states how much was found."""
    with blank_session() as db:
        world = _borrow_world(db, donor_qty="20", ask="30")
        components = _components(ProjectSupplyService(db).proposal_for(world["asker"]))

    assert [(c["kind"], c["qty"]) for c in components] == [("buy", "30")]
    assert "20" in components[0]["reason"] and "30" in components[0]["reason"]


# --------------------------------------------------------------------------- AC-S3-7


def test_the_pool_is_the_last_stock_step_and_its_free_pile_raises_nothing():
    """AC-S3-7, first half (R34): the pool's free pile covers the whole unit, so it is a
    `reserve` at rung `pool` and nobody is owed it back.

    LADDER V8: 120 in the pool rather than 60, because a project line may take half of it
    (R-B) and this case is about what a WHOLE pool draw owes, which is nothing.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=120)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="60",
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

        ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=line.id,
                        reserve=[{"warehouse_id": str(pool.id), "qty": "60"}],
                    )
                ]
            ),
            actor_user_id=eling,
        )
        raised = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
            .count()
        )

    assert [(c["kind"], c["rung"]) for c in components] == [("reserve", "pool")]
    assert raised == 0, "a FREE pool draw is owed to nobody (AC-L13's surviving half)"


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_later_pool_order_lends_its_on_hand_and_is_owed_it_back():
    """AC-S3-7, second half (R34): when the pool's free pile cannot cover the unit, a LATER
    pool order holding stock there lends it - and that half is a `borrow` which raises an
    ORDER_BACK at the pool order's own date. This is what retires "a pool draw raises
    nothing" as a blanket statement."""
    with blank_session() as db:
        borrow_from_order_on(db)
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=60)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        # The pool's OWN book: a later dealer order that the pool's 60 covers whole. It
        # needs no decision to hold it - the pool is its own group inside the assignment
        # (R34), so its own line takes its own stock and the pile nets to nothing.
        _pool_order, _pool_line, _a, _b = _seed_line(
            db, company_id, project, product, pool, qty_ordered="60",
            required_date=date.today() + timedelta(days=WINDOW_DAY + 60), line_no=9,
            so_number=f"ZZTSO-POOL{_uid()[:4]}",
        )

        asker, asker_line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="60", line_no=1,
            so_number=f"ZZTSO-ASK{_uid()[:5]}",
        )
        service = ProjectSupplyService(db)
        components = _components(service.proposal_for(asker))
        component = components[0]

        ProjectSupplyService(db).confirm(
            asker,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=asker_line.id,
                        borrow=[
                            {
                                "source": "other_location",
                                "warehouse_id": str(pool.id),
                                "qty": component["qty"],
                                "reason": "Ladder v7.1 step 4b.",
                                "donor_core_line_id": component["donor_core_line_id"],
                                "donor_so_number": component["donor_so_number"],
                                "donor_required_date": component["donor_required_date"],
                            }
                        ],
                    )
                ]
            ),
            actor_user_id=eling,
        )
        db.commit()
        raised = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
            .all()
        )
        locations = [row.stock_location for row in raised]
        pool_code = pool.warehouse_code

    assert [(c["kind"], c["rung"]) for c in components] == [("borrow", "pool")]
    assert components[0]["donor_so_number"]
    assert len(raised) == 1
    assert locations == [pool_code]


def test_the_dealer_hot_selling_gate_is_retired_and_the_share_keeps_the_stock_instead():
    """AC-S3-7's last sentence, RETIRED BY LADDER V8 (R-A). Hot at retail used to empty the
    whole pool step; what keeps stock for dealers now is the SHARE (R-B), which keeps a
    percentage of every pool from every project line rather than the whole of one pool from
    the hot items alone.

    500 in the pool, half of it on offer, and a line of 10 fits inside that - so the line
    that used to buy takes the pool. The captain's own AC-2.7 is this case with WESERP10B's
    own numbers.
    """
    from app.models.scm import ItemClassification

    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=500)
        _lead_time(db, product, LEAD_DAYS)
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
    ]


# --------------------------------------------------------------------------- AC-S3-8


def test_a_borrow_at_another_warehouse_writes_one_proposed_transfer():
    """AC-S3-8: the goods have to be carried to the asking line's bin before anything can be
    delivered, and the Transfers page is where a person approves that deliberately."""
    from app.models.stock_transfer import StockTransfer

    with blank_session() as db:
        world = _borrow_world(db)
        component = _components(
            ProjectSupplyService(db).proposal_for(world["asker"])
        )[0]
        ProjectSupplyService(db).confirm(
            world["asker"],
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=world["asker_line"].id,
                        borrow=[
                            {
                                "source": "other_location",
                                "warehouse_id": str(world["donor_bin"].id),
                                "qty": component["qty"],
                                "reason": "Ladder v7.1 step 2.",
                                "donor_core_line_id": component["donor_core_line_id"],
                                "donor_so_number": component["donor_so_number"],
                            }
                        ],
                    )
                ]
            ),
            actor_user_id=world["eling"],
        )
        db.commit()
        transfers = db.query(StockTransfer).all()
        states = [t.state for t in transfers]
        froms = [str(t.from_warehouse_id) for t in transfers]
        tos = [str(t.to_warehouse_id) for t in transfers]
        donor_id, own_id = str(world["donor_bin"].id), str(world["own"].id)

    assert len(transfers) == 1, transfers
    assert states == ["proposed"]
    assert froms == [donor_id]
    assert tos == [own_id]


# --------------------------------------------------------------------------- AC-S3-9


def test_one_donor_line_is_offered_once_across_the_walk_and_the_later_unit_buys():
    """AC-S3-9: the donor ledger is keyed by DONOR LINE now, so two units naming the same
    donor draw it down between them rather than each being offered the whole of it."""
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTMWH-IB{_uid()[:3]}")
        _stock(db, product, donor_bin, on_hand=10)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        donor_order, donor_line, _a, _b = _seed_line(
            db, company_id, project, product, donor_bin, qty_ordered="10",
            required_date=date.today() + timedelta(days=WINDOW_DAY + 60), line_no=9,
            so_number=f"ZZTSO-DONOR{_uid()[:4]}",
        )
        _decide(db, donor_order, donor_line, donor_bin, "10", eling)

        core_so = _core_so(db, company_id)
        core_so.so_number = f"ZZTSO-TWODATES{_uid()[:4]}"
        db.flush()
        first_core = _core_line(
            db, core_so, product, own, qty_ordered="10",
            required_date=REQUIRED_DATE,
        )
        second_core = _core_line(
            db, core_so, product, own, qty_ordered="10",
            required_date=REQUIRED_DATE + timedelta(days=1),
        )
        asker = _project_so(db, project)
        _project_line(db, asker, line_no=1, product=product, core_line=first_core)
        _project_line(db, asker, line_no=2, product=product, core_line=second_core)
        db.commit()

        lines = ProjectSupplyService(db).proposal_for(asker)["lines"]
        stated = [
            [(c["kind"], c["qty"], c["rung"]) for c in line["components"]]
            for line in lines
        ]

    assert stated[0] == [("borrow", "10", "order_borrow")]
    assert stated[1] == [("buy", "10", "buy")], (
        "the donor was spent by the first unit and the second buys whole"
    )


# --------------------------------------------------------------------------- AC-S3-10


def test_the_retired_cross_group_settings_are_read_by_no_code_path():
    """AC-S3-10: the two cap columns are gone with migration 443 and nothing reads them; the
    rung CONSTANT survives, because a frozen snapshot taken under v4/v5 still renders."""
    import inspect

    from app.services import project_supply_service as service_module
    from app.services.scm import front_planning_engine as engine

    assert engine.RUNG_CROSS_GROUP_BORROW == "cross_group_borrow"
    assert engine.RUNG_ORDER_BORROW == "order_borrow"
    assert engine.RUNG_SUPPLY_BORROW == "supply_borrow"

    for module in (service_module, engine):
        source = inspect.getsource(module)
        assert "cross_group_borrow_max_qty" not in source, module.__name__
        assert "cross_group_borrow_max_pct" not in source, module.__name__


def test_a_frozen_snapshot_carrying_the_retired_rung_still_renders():
    """AC-S3-10's other half: a decision frozen under v5 names `cross_group_borrow`, and the
    board prints what was promised rather than refusing to read it."""
    from app.schemas.project_board import BoardTrailStep

    step = BoardTrailStep(
        step=3,
        kind="cross_group_borrow",
        question="Can we borrow from another location?",
        answer="no",
    )
    assert step.kind == "cross_group_borrow"


# --------------------------------------------------------------------------- AC-S3-14


@pytest.mark.usefixtures("ladder_steps_on")
def test_every_walked_unit_carries_five_options_in_step_order_with_one_chosen():
    """AC-S3-14 (R36), the whole contract in one read: five entries, always, in step order;
    at most one `chosen`; `fulfil_date` and `days_late` null together; `days_late` never
    negative; `debt_*` on the borrow steps only."""
    with blank_session() as db:
        borrow_from_order_on(db)
        world = _borrow_world(db)
        options = _options(ProjectSupplyService(db).proposal_for(world["asker"]))

    # LADDER V8 (R-A): the site pool leads the walk and is named after the pool it asks;
    # `pool` is gone from a live walk and survives only on a frozen trail.
    assert [option["step"] for option in options] == [
        "pool_share", "use", "order_borrow", "supply_borrow", "buy",
    ]
    assert [option["label"] for option in options][1] == "Use our locations"
    assert options[0]["label"].startswith("Use "), options[0]["label"]
    assert sum(1 for option in options if option["chosen"]) == 1
    chosen = next(option for option in options if option["chosen"])
    assert chosen["step"] == "order_borrow"
    assert chosen["whole"] is True
    assert chosen["debt_so_number"]
    assert chosen["debt_month"] and len(chosen["debt_month"]) == 7
    for option in options:
        assert (option["fulfil_date"] is None) == (option["days_late"] is None), option
        if option["days_late"] is not None:
            assert option["days_late"] >= 0, option
        if option["step"] in ("use", "buy"):
            assert option["debt_so_number"] is None
            assert option["debt_month"] is None
    buy = next(option for option in options if option["step"] == "buy")
    assert buy["whole"] is True
    assert buy["fulfil_date"] == (
        (date.today() + timedelta(days=LEAD_DAYS)).isoformat()
    )
    supply_borrow = next(
        option for option in options if option["step"] == "supply_borrow"
    )
    assert supply_borrow["whole"] is False, "S4 builds this step; S3 offers nothing"
    assert supply_borrow["fulfil_date"] is None


def test_the_board_payload_declares_the_options_so_the_response_model_keeps_them():
    """AC-S3-14 `[BE]`: `response_model` silently drops an undeclared field, so the field is
    asserted through the SCHEMA rather than off the service dict alone."""
    from app.schemas.project_board import BoardContribution

    assert "options" in BoardContribution.model_fields
    parsed = BoardContribution.model_validate(
        {
            "key": "k",
            "sales_order_id": "so-1",
            "so_number": "ZZT-SO-1",
            "line_no": 1,
            "item_code": "ZZT-ITEM",
            "qty": "10",
            "rank_score": 0.0,
            "options": [
                {
                    "step": "use",
                    "label": "Use our locations",
                    "whole": True,
                    "fulfil_date": "2026-09-01",
                    "days_late": 0,
                    "chosen": True,
                }
            ],
        }
    )
    assert parsed.options[0].step == "use"
    assert parsed.options[0].debt_so_number is None

    # The SHEET carries the same table, and its own response model has to declare it too:
    # the decision panel renders the options on either surface.
    from app.schemas.project_supply import SupplyLine

    assert "options" in SupplyLine.model_fields


# --------------------------------------------------- the review's trail + option fixes


@pytest.mark.usefixtures("ladder_steps_on")
def test_the_pool_step_of_the_trail_states_the_borrow_half_and_names_the_donor():
    """Review finding: the pool step read only the FREE pile, so a step-4b borrow printed
    `answer=yes, took=60` beside `offered=0` and "No shared pool holds this product." over
    a borrow the engine had just composed. The step states the half that fired."""
    from ..test_fulfilment_board import _step

    with blank_session() as db:
        borrow_from_order_on(db)
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        _stock(db, product, pool, on_hand=60)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        _pool_order, _pool_line, pool_core_so, _b = _seed_line(
            db, company_id, project, product, pool, qty_ordered="60",
            required_date=date.today() + timedelta(days=WINDOW_DAY + 60), line_no=9,
            so_number=f"ZZTSO-POOL{_uid()[:4]}",
        )
        _asker, _asker_line, asker_core_so, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="60", line_no=1,
            so_number=f"ZZTSO-ASK{_uid()[:5]}",
        )
        db.commit()

        from app.services.project_fulfilment_board_service import FulfilmentBoardService

        board = FulfilmentBoardService(db).build(
            [asker_core_so.so_number], granularity="week", as_of=date.today()
        )
        contribution = next(
            contribution
            for cell in board["cells"]
            for contribution in cell["contributions"]
        )
        step = _step(contribution, "pool")
        donor_so = pool_core_so.so_number

    assert step["answer"] == "yes"
    assert step["took"] == "60"
    assert "No shared pool holds this product." not in (step["why"] or "")
    # The component's own sentence, which names the pool order that lent and the month its
    # debt lands in - not a second description of the pile the quantity did not come from.
    assert "Borrow 60 on hand at pool" in (step["why"] or ""), step["why"]
    assert donor_so in (step["why"] or ""), step["why"]
    assert donor_so is not None


@pytest.mark.usefixtures("ladder_steps_on")
def test_another_groups_incoming_is_offered_as_water_and_dates_the_option_by_its_arrival():
    """Review finding: step 1's other-group half dropped `(arrival, kind)`, so another
    group's INCOMING SPO composed as an immediate Reserve and the option table dated the
    whole proposal today. It is `timely_spo`, dated by the arrival, exactly as the own
    half's water is."""
    from ..test_fulfilment_board import _incoming

    arrival = date.today() + timedelta(days=20)
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _incoming(
            db, product, donor,
            spo_number=f"ZZT-SPO-{_uid()[:6]}", allocated=40, received=0, arrives=arrival,
        )
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=40),
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)
        options = _options(proposal)

    assert [(c["kind"], c["rung"]) for c in components] == [("timely_spo", "group_take")]
    assert components[0]["source_location"] == donor.warehouse_code
    assert "arriving" in components[0]["reason"], components[0]["reason"]
    use = next(option for option in options if option["step"] == "use")
    assert use["chosen"] is True
    assert use["fulfil_date"] == arrival.isoformat(), (
        "the option is dated by the document's arrival, not by today"
    )


def test_a_borrow_split_across_a_units_lines_splits_its_order_back_with_it():
    """Review finding (`_split_unit`): a straddling BORROW carried the WHOLE unit's
    `order_back_qty` onto both halves, so a unit of 30 split 10/20 told the screen it owed
    its donor 60. The figure moves with the quantity."""
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTMWH-IB{_uid()[:3]}")
        _stock(db, product, donor_bin, on_hand=30)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        donor_order, donor_line, _a, _b = _seed_line(
            db, company_id, project, product, donor_bin, qty_ordered="30",
            required_date=date.today() + timedelta(days=WINDOW_DAY + 20), line_no=9,
            so_number=f"ZZTSO-DONOR{_uid()[:4]}",
        )
        _decide(db, donor_order, donor_line, donor_bin, "30", eling)

        # ONE planning unit: same order, same item, same bin, same date (ladder v6).
        core_so = _core_so(db, company_id)
        core_so.so_number = f"ZZTSO-UNIT{_uid()[:4]}"
        db.flush()
        first = _core_line(
            db, core_so, product, own, qty_ordered="10", required_date=REQUIRED_DATE,
        )
        second = _core_line(
            db, core_so, product, own, qty_ordered="20", required_date=REQUIRED_DATE,
        )
        asker = _project_so(db, project, status=SO_STATUS_PUBLISHED)
        _project_line(db, asker, line_no=1, product=product, core_line=first)
        _project_line(db, asker, line_no=2, product=product, core_line=second)
        db.commit()

        lines = ProjectSupplyService(db).proposal_for(asker)["lines"]
        split = [
            (c["kind"], c["qty"], c.get("order_back_qty"))
            for line in lines
            for c in line["components"]
        ]

    assert split == [("borrow", "10", "10"), ("borrow", "20", "20")], split
