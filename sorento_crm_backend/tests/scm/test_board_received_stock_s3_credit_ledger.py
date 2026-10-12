"""AC-S3-11 (`board-received-stock-own-arrival-acceptance-criteria.md`, added 21 Sep after the
security review): the own-arrival credit (R7) must draw through the SAME capacity ledger every
other rung draws through, so two lines competing for the same physical pile cannot both be
told yes.

Finding pinned here (security reviewer, 21 Sep 2026): in `app/services/project_supply_service.py`

- `own_arrival_credit_for` caps its credit by `fact.group_net_by_location`'s
  RAW `on_hand` (a live physical figure, netted only against what an EARLIER
  line of the SAME product+location already drew as CREDIT in this same walk/call via
  `own_arrival_left`) - never against what an ordinary `RUNG_GROUP_TAKE` draw already took in
  the SAME walk.
- At compose time, the per-unit drawdown loop (inside `compose_lines`) walks every
  `RUNG_GROUP_TAKE` component and charges it against `own_group`/`other_group` EXCEPT an
  own-arrival one, which `continue`s without charging anything. So where one
  unit's ordinary draw (rank tie-break, `_pile_order`) and another unit's own-arrival credit
  compete for the identical bin, the credit is invisible to the ledger the ordinary draw reads
  and writes: both units are told yes.
- At confirm time, `_check_line` adds the credit straight onto its own local
  `capacity[fact.own_code]`, never through `capacity_left` (the `_CapacityLedger`
  every other rung's capacity check draws through, declared once per `confirm()` call and
  threaded through every line of the payload) - the same shape of bypass, one layer
  lower.

Both tests below use `own_arrival_group`/`own_arrival_warehouse` (one ownership group, one
physical bin) so both lines' Reserve is a claim on the exact same on-hand.

CONTRACT CHOICES this file pins:

- AC-S3-11's own text says "order A" / "order B" for the compose (ladder) half - two DIFFERENT
  sales orders sharing one product and one bin, walked together in a single
  `FulfilmentBoardService.build()` call (the board's own unit key names the order,
  per `_unit_key`'s own docstring, so `own_group` truly is a PER-ORDER ledger there and
  the only thing that can still cap the group's book position across two orders sharing a
  ranking tie is the drawdown loop the finding names).
- The two lines are given the SAME `required_date` (a genuine rank tie) and DETERMINISTIC
  `so_number`s, because `_pile_order`'s tie-break is "sales order number, then line number"
  - a tie broken by a RANDOM uuid-suffixed `so_number` (`order_with_lines`'s
  default) makes which line the ordinary rung serves first non-deterministic, and the credit
  bypass is only OBSERVABLE when the credited line loses that tie (the ordinary rung serves
  the OTHER line first, and the credit still hands the loser a second, uncharged copy of the
  same units). `ORDER B`'s number sorts before `ORDER A`'s so the tie-break always serves B
  first ordinarily, and A's own-arrival credit is what is under test.
- The CONFIRM half is measured, not assumed, against three shapes before being written as a
  pin: two SEPARATE orders confirmed in sequence (B first, A second; and the reverse, A first,
  B second) and ONE order with two lines confirmed together in a single `confirm()` call. All
  three are refused as `ReserveOverHand` (`SupplyLinesRefused`, 409, code
  `supply_reserve_over_hand`) - `_check_reserve_against_on_hand` (R14) is an
  INDEPENDENT guard that reads real `Stock` and real confirmed `SOLineAllocation` rows rather
  than either walk-scoped ledger the finding names, and it does not distinguish an
  own-arrival-sourced Reserve request from an ordinary one: every `entry.reserve` item in the
  payload competes against the same "on hand less already held" arithmetic regardless of why
  the caller asked for it. `test_a_reason_does_not_push_a_reserve_past_on_hand`
  (`tests/test_so_supply_confirmation.py`) already pins the same guard for a different bypass
  (a CS override reason) on the identical two-order, sequential-HTTP-confirm shape, so this is
  not a new mechanism, only a new caller of it. This test is therefore written as a GREEN
  REGRESSION GUARD for the confirm path (`AC-S3-11`'s confirm half is satisfied by an existing,
  independent guard, not by fixing `_check_line`'s own ledger) rather than forced red - the
  compose half above is where the finding's damage is real and observable (a planner-facing
  proposal that promises 80 units from an on-hand 40, which is exactly the wrong thing to
  learn from a board that "writes nothing" per `test_fulfilment_board.py`'s own docstring).

Postgres only (`tests/_pg_fixture.py`), every FK seeded here, never a borrowed row.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody
from app.services.error_handler import AppException
from app.services.project_supply_service import ProjectSupplyService

from tests._pg_fixture import blank_session
from tests.test_fulfilment_board import TODAY, _cell, _service, _stock as _board_stock
from tests.scm._own_arrival_fixture import (
    order_with_lines,
    own_arrival_group,
    own_arrival_warehouse,
    po_line_bought_for,
    supplier_and_po,
)
from tests.scm.test_project_supply_service_ladder import _world
from tests.test_so_supply_confirmation import (  # noqa: F401  (helpers, not fixtures)
    _core_line,
    _core_so,
    _project_line,
    _project_so,
    _stock,
    _uid,
    _warehouse,
)

TIE_DATE = date(2026, 8, 20)
TIE_BUCKET = "2026-08-17"  # the Monday-anchored week bucket_key_for(TIE_DATE, ...) lands in


def _reserved(contribution: dict) -> Decimal:
    return sum(
        (
            Decimal(s["qty"])
            for s in contribution["sources"]
            if s.get("rung") == "group_take"
        ),
        Decimal("0"),
    )


def test_ac_s3_11_compose_never_covers_the_same_units_twice():
    """AC-S3-11 compose half: on hand 40 at one location, group net positive (no competing
    demand beyond the two lines), ORDER B's line needs 40 with no PO of its own, ORDER A's
    line needs 40 and its own PO line received 40 (the credit). Both required on the SAME
    date, a genuine rank tie `_pile_order` breaks by `so_number` - ORDER B's number sorts
    first, so B's plain need is served by the ordinary `RUNG_GROUP_TAKE` rung before A is
    even reached.

    MEASURED green after the fix: `compose_lines`'s own-arrival drawdown loop
    now charges the SAME `own_arrival_left` ledger `own_arrival_credit_for`
    reads, for every ORDINARY `RUNG_GROUP_TAKE` Reserve at that bin - not only for the
    credit's own component. B is walked first (its `so_number` wins the tie), its ordinary
    Reserve of 40 takes the whole bin AND spends the own-arrival ledger for that bin down to
    0. When A is walked, `own_arrival_credit_for` finds nothing left of the physical pile to
    credit, so A's credit is 0 and its 40 composes elsewhere (Buy, since this world offers
    no other rung) - never a second, uncharged Reserve of 40 at the identical bin.

    #1362 round 5 (owner ruling, 29 Sep 2026) AMENDS the winner, not the invariant: A's 40
    landed for A and is pinned to it in the assignment, so B's ordinary rung finds nothing
    free and B buys; A is credited its 40. Still never 80 drawn from 40.
    """
    with blank_session() as db:
        group, product = own_arrival_group(db)
        own = own_arrival_warehouse(db, group)
        _board_stock(db, product, own, on_hand=40)

        # ORDER B: plain demand, no PO of its own. so_number sorts BEFORE order A's, so the
        # tie-break always serves B first on the ordinary rung - deterministic, not left to
        # the fixture helper's random uuid suffix.
        order_b, (line_b,) = order_with_lines(
            db, so_number="ZZT-S311-1-ORDERB", product=product, warehouse=own,
            lines=[{"qty": "40", "required_date": TIE_DATE, "source_ref": "S311-LB"}],
        )
        # ORDER A: its own PO line received 40 - tier-1 own-arrival credit.
        order_a, (line_a,) = order_with_lines(
            db, so_number="ZZT-S311-2-ORDERA", product=product, warehouse=own,
            lines=[{"qty": "40", "required_date": TIE_DATE, "source_ref": "S311-LA"}],
        )
        po = supplier_and_po(db, po_number="ZZT-PO-S311")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref="S311-LA", qty_received=40, qty_ordered=40,
        )

        board = _service(db).build(
            [order_a.so_number, order_b.so_number], granularity="week", as_of=TODAY,
        )
        cell = _cell(board, product.product_code, TIE_BUCKET)
        by_so = {c["so_number"]: c for c in cell["contributions"]}
        reserved_a = _reserved(by_so[order_a.so_number])
        reserved_b = _reserved(by_so[order_b.so_number])

        assert reserved_a + reserved_b <= Decimal("40"), (
            "on hand is 40 at this bin; ORDER A's own-arrival credit and ORDER B's "
            f"ordinary Reserve must share it, never both draw the full 40: "
            f"A={reserved_a} B={reserved_b}, sources A={by_so[order_a.so_number]['sources']} "
            f"B={by_so[order_b.so_number]['sources']}"
        )
        # The ladder's actual outcome, pinned exactly so a regression is caught by a
        # CHANGED number here rather than a silent pass. #1362 round 5 (owner ruling, 29 Sep
        # 2026, "we cannot snatch, what's ordered against the SO should stay belonged to
        # it") reversed who wins: A's 40 landed for A, so it is pinned to A before anybody
        # queues, and B - served first by the tie-break - finds nothing free and buys. It
        # used to be B taking the whole bin and A's credit finding nothing left.
        assert reserved_b == Decimal("0"), by_so[order_b.so_number]["sources"]
        assert reserved_a == Decimal("40"), by_so[order_a.so_number]["sources"]
        own_arrival_sources = [
            s
            for s in by_so[order_a.so_number]["sources"]
            if s.get("rung") == "group_take" and s.get("source") == "own_arrival"
        ]
        assert sum(Decimal(s["qty"]) for s in own_arrival_sources) == Decimal("40"), (
            by_so[order_a.so_number]["sources"]
        )
        buy_sources = [
            s for s in by_so[order_b.so_number]["sources"] if s.get("kind") == "buy"
        ]
        assert buy_sources and Decimal(buy_sources[0]["qty"]) == Decimal("40"), by_so[
            order_b.so_number
        ]["sources"]


def test_ac_s3_11_confirm_never_holds_the_same_units_twice():
    """AC-S3-11 confirm half: same world (on hand 40, one bin, two orders each needing 40,
    one with its own PO line received 40). A live `ProjectSupplyService.confirm()` covering
    ORDER B first (ordinary Reserve 40) and then ORDER A (own-arrival Reserve 40, asked for
    exactly as the board proposes it in the test above) - `confirm()` is scoped to ONE order
    (`order: ProjectSalesOrder`, `app/services/project_supply_service.py`), so
    "a confirm covering both" is two confirms in sequence, sharing the same on-hand.

    MEASURED green today (not forced red - see this file's CONTRACT CHOICES): B's confirm
    commits a real `SOLineAllocation` (`source_type=ALLOC_SOURCE_OWN`) for its
    40. A's confirm then asks for the SAME 40 as an own-arrival Reserve, and
    `_check_reserve_against_on_hand` (R14) - independent of `_check_line`'s own
    ledger, the seam AC-S3-11's finding actually names - reads live `Stock` and B's now-real
    hold and finds 0 free: A confirms as decided, holds nothing and is recorded 40 short
    (owner rule 12 Oct 2026, OI-CLOSED-LINE: a stock shortfall is a warning, never a
    refusal), so the 40 units are held once, by B. The reverse order (A confirms
    first, B second) and a single `confirm()` call naming both of ONE order's two lines were
    also measured and recorded short the same way; this test pins the shape closest to
    AC-S3-11's own wording (two orders, B first).

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.
    """
    within_window = date.today() + timedelta(days=10)
    with blank_session() as db:
        company_id, actor, project, product = _world(db)
        own = _warehouse(db, f"ZZT-OWN-{_uid()[:4]}")
        _stock(db, product, own, on_hand=40)

        # ORDER B: plain demand, no credit, confirms FIRST.
        core_so_b = _core_so(db, company_id)
        core_line_b = _core_line(
            db, core_so_b, product, own, qty_ordered="40", required_date=within_window,
        )
        order_b = _project_so(db, project, so_id=core_so_b.id)
        line_b = _project_line(db, order_b, line_no=1, product=product, core_line=core_line_b)
        db.commit()

        result_b = ProjectSupplyService(db).confirm(
            order_b,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=str(line_b.id),
                        reserve=[{"warehouse_id": str(own.id), "qty": "40"}],
                    ),
                ]
            ),
            actor_user_id=actor,
        )
        db.commit()
        assert result_b["exceptions"] == [], result_b
        assert result_b["lines_decided"] == 1, result_b

        # ORDER A: its own PO line received 40 (own-arrival credit), confirms SECOND asking
        # for the SAME 40 at the SAME bin.
        core_so_a = _core_so(db, company_id)
        core_line_a = _core_line(
            db, core_so_a, product, own, qty_ordered="40", required_date=within_window,
        )
        core_line_a.source_ref = f"ZZT-S311-CONFIRM-LA-{_uid()[:8]}"
        db.flush()
        po = supplier_and_po(db, po_number=f"ZZT-PO-S311-CONFIRM-{_uid()[:8]}")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref=core_line_a.source_ref,
            qty_received=40, qty_ordered=40,
        )
        order_a = _project_so(db, project, so_id=core_so_a.id)
        line_a = _project_line(db, order_a, line_no=1, product=product, core_line=core_line_a)
        db.commit()

        result_a = ProjectSupplyService(db).confirm(
            order_a,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=str(line_a.id),
                        reserve=[{"warehouse_id": str(own.id), "qty": "40"}],
                    ),
                ]
            ),
            actor_user_id=actor,
        )
        [notice] = result_a["stock_short_notices"]
        assert notice["line_no"] == 1, notice
        assert f"only 0 free at {own.warehouse_code}" in notice["reason"], notice
        assert ", 40 short" in notice["reason"], notice
        from app.models.project_so import SOLineAllocation

        db.expire_all()
        held = {
            row.so_line_id: Decimal(str(row.qty))
            for row in db.query(SOLineAllocation).filter(
                SOLineAllocation.so_line_id.in_([line_a.id, line_b.id])
            )
        }
        assert held == {line_b.id: Decimal("40")}, (
            "the 40 on hand are held once, by the order that confirmed first"
        )


# ============================================================================
# Second review round (21 Sep) - guards for the two seams AC-S3-11's fix landed in, so
# a regression that removes either is caught here rather than rediscovered live.
# ============================================================================


def test_single_line_credit_never_exceeds_the_floor():
    """B2 (compose): ONE line, needing 80, with its OWN PO line received 40 (the
    tier-1 credit) at a bin holding only 40 on hand, and no other demand at all. The
    credit and the ordinary group-take rung are both reading the SAME 40-unit floor for
    the SAME line - there is no second line to lose a rank tie here, so this pins the
    seam `_less_drawn` guards on its own, without AC-S3-11's two-order shape around it.

    GREEN today via `_less_drawn` (`app/services/scm/front_planning_engine.py`):
    the credit's own draw comes OFF `group_take_candidates` at that same
    bin before question 1 (the ordinary rung) reads them, so question 1 finds nothing
    left and the line's remainder (40) composes as Buy - Reserve 40 (source own_arrival)
    + Buy 40, never a second, uncharged Reserve of 40 off the identical floor (Reserve
    80 total, which is more than physically exists at the bin).

    Regression guard: if `_less_drawn`'s subtraction were removed (or the
    seam otherwise stopped netting the credit's own draw off the ordinary candidates),
    question 1 would read the bin's full, un-netted 40 again and this line would
    compose Reserve 80 from an on-hand of 40 - this test would go red on the
    `reserved == Decimal("40")` / `buy_sources` assertions below.
    """
    with blank_session() as db:
        group, product = own_arrival_group(db)
        own = own_arrival_warehouse(db, group)
        _board_stock(db, product, own, on_hand=40)

        order, (line,) = order_with_lines(
            db, product=product, warehouse=own,
            lines=[{"qty": "80", "required_date": TIE_DATE, "source_ref": "B2-L1"}],
        )
        po = supplier_and_po(db, po_number="ZZT-PO-B2-SINGLE")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref="B2-L1", qty_received=40, qty_ordered=40,
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        cell = _cell(board, product.product_code, TIE_BUCKET)
        contribution = cell["contributions"][0]
        reserved = _reserved(contribution)

        assert reserved <= Decimal("40"), (
            "on hand is 40 at this bin; the line's own credit and the ordinary rung "
            f"must not both draw the full 40 for a Reserve of 80: sources="
            f"{contribution['sources']}"
        )
        assert reserved == Decimal("40"), contribution["sources"]
        own_arrival_sources = [
            s
            for s in contribution["sources"]
            if s.get("rung") == "group_take" and s.get("source") == "own_arrival"
        ]
        assert own_arrival_sources and Decimal(own_arrival_sources[0]["qty"]) == Decimal(
            "40"
        ), contribution["sources"]
        buy_sources = [s for s in contribution["sources"] if s.get("kind") == "buy"]
        assert buy_sources and Decimal(buy_sources[0]["qty"]) == Decimal("40"), contribution[
            "sources"
        ]


def test_confirm_credit_is_stated_through_the_capacity_ledger():
    """B1 (round 3, re-aimed): a second reading of ONE bin must intersect with the
    ordinary reading there, never SUM with it.

    On hand 100 at one bin. A competing core sales-order line for 60 at that same bin,
    an EARLIER required date, so the ordinary date-aware reading `_check_line` seeds
    `capacity_left` with for THIS line's own bin is 40 (100 on hand, less the earlier
    60-unit demand it has to honour first - the AC-S3-1 date-aware shape). This line
    itself needs 80, and its own PO line received 40 - a tier-1 own-arrival credit of 40
    (`own_arrival_credit_for` caps the credit by the RAW physical on hand, 100, per this
    file's own docstring above, never by the netted 40). `proposal_for` composes it
    Reserve 40 (own_arrival) + Buy 40 - the credit alone does not clear the whole 80.

    A confirm posting Reserve 80 at that bin holds only 40 and records 40 short: the bin
    truly holds only 40 free once the earlier, competing demand is honoured, and the credit
    is a second STATEMENT about that same physical pile ("this line's own PO put at least 40
    of it there"), not a second, additional 40 stacked on top of the ordinary reading. If
    the two were summed the line would hold 80 and report nothing short.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE): a stock shortfall is a warning, never a refusal.

    R14 (`_check_reserve_against_on_hand`) cannot be the guard that catches this: the
    competing line is an open, UNCONFIRMED core SO line with no `SOLineAllocation` of its
    own, so R14's own "silent when nobody else holds here" clause leaves it untouched -
    this shape isolates `_check_line`'s own capacity ledger, the seam this file's
    docstring names, from R14's separate one.

    RED today: `_check_line`'s confirm-time recheck (`project_supply_service.py`) calls
    `capacity_left.state_at_least(fact.product_id, str(source.id), before + credit_qty)`
    - `before` (40, the ordinary reading already seeded into the ledger) PLUS the credit
    (40) - so the bin's stated floor is raised to 80 and Reserve 80 wrongly clears it.
    The right call states the credit as its OWN reading of the bin -
    `state_at_least(..., credit_qty)`, i.e. the pile becomes `max(before, credit_qty)`
    (`state_at_least`'s own semantics), never their sum - so the bin's stated floor stays
    at 40 (the larger of the two readings, 40 and 40) and Reserve 80 is refused.

    Control: `test_confirm_credit_reserve_within_capacity_is_accepted` below (a sibling,
    not this same world - a Reserve here summing to LESS than the line's whole open qty
    would first be refused by the unrelated "components add up" / "whole line either
    wholly stock or wholly Buy" rules, which would test those rules rather than this
    ledger) posts a Reserve fully within what the bin supports and is accepted, so the
    fix is pinned as "the two readings intersect", not "the credit is ignored outright".
    """
    within_window = date.today() + timedelta(days=10)
    dominant = date.today() + timedelta(days=5)
    with blank_session() as db:
        company_id, actor, project, product = _world(db)
        own = _warehouse(db, f"ZZT-OWN-{_uid()[:4]}")
        _stock(db, product, own, on_hand=100)

        # The competing demand: an earlier-dated, UNCONFIRMED core SO line for 60 at the
        # SAME bin, no PO of its own and no allocation of its own - this is what nets the
        # ordinary date-aware reading down to 40 without ever giving R14 a holder to name.
        competing_so = _core_so(db, company_id)
        _core_line(
            db, competing_so, product, own, qty_ordered="60", required_date=dominant,
        )

        core_so = _core_so(db, company_id)
        core_line = _core_line(
            db, core_so, product, own, qty_ordered="80", required_date=within_window,
        )
        core_line.source_ref = f"ZZT-B1-{_uid()[:8]}"
        db.flush()
        po = supplier_and_po(db, po_number=f"ZZT-PO-B1-{_uid()[:8]}")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref=core_line.source_ref,
            qty_received=40, qty_ordered=40,
        )

        order = _project_so(db, project, so_id=core_so.id)
        line = _project_line(db, order, line_no=1, product=product, core_line=core_line)
        db.commit()

        proposal = ProjectSupplyService(db).proposal_for(order)
        components = proposal["lines"][0]["components"]
        reserve = sum(
            (Decimal(c["qty"]) for c in components if c["kind"] == "reserve"), Decimal("0"),
        )
        buy = sum(
            (Decimal(c["qty"]) for c in components if c["kind"] == "buy"), Decimal("0"),
        )
        assert reserve == Decimal("40") and buy == Decimal("40"), (
            "the board's own proposal: Reserve 40 (own_arrival credit) + Buy 40 - "
            f"components={components}"
        )

        result = ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=str(line.id),
                        reserve=[{"warehouse_id": str(own.id), "qty": "80"}],
                    ),
                ]
            ),
            actor_user_id=actor,
        )
        [notice] = result["stock_short_notices"]
        assert str(line.line_no) == str(notice["line_no"]), notice
        reason = notice["reason"]
        assert f"only 40 free at {own.warehouse_code}" in reason and ", 40 short" in reason, (
            "the bin's true free floor (40, the ordinary reading and the credit stating "
            f"the SAME pile, never their sum) caps an 80-unit ask: reason={reason!r}"
        )
        from app.models.project_so import SOLineAllocation

        db.expire_all()
        held = sum(
            (Decimal(str(row.qty)) for row in db.query(SOLineAllocation).filter(
                SOLineAllocation.so_line_id == line.id
            )),
            Decimal("0"),
        )
        assert held == Decimal("40"), "held once at the stated floor, never 80 off a pile of 40"


def test_confirm_credit_reserve_within_capacity_is_accepted():
    """B1's control, re-aimed (round-4 brief): the earlier version of this control let
    the ordinary reading ALONE satisfy the Reserve, because it set `before` (the
    ordinary reading) equal to `credit_qty` (40 == 40) - a change that dropped the
    credit-through-the-ledger fix ENTIRELY (`own_arrival_left`/`state_at_least` never
    consulted at all) would still pass it, since `before` alone already covers Reserve
    40. That is not a real control on the fix; it only pins that a WORKING credit does
    not over-tighten the ledger.

    Re-aimed so the ordinary reading is genuinely BELOW the Reserve and ONLY the credit
    can lift it high enough to clear: on hand 100 at one bin, a competing, earlier-
    required, UNCONFIRMED core SO line for the WHOLE 100 at that SAME bin, so the
    ordinary date-aware reading `_check_line` seeds `capacity_left` with for THIS
    line's own bin nets to 0 (100 on hand, 100 claimed ahead of it by an earlier date -
    the AC-S3-1 date-aware shape, scaled to consume the whole pile rather than leaving
    40 the ordinary reading could clear alone). This line needs 40, matching its own
    PO's receipt (tier-1 credit 40) exactly - a Reserve of 40 is the WHOLE line, no Buy
    beside it, so the "components add up" and "wholly stock or wholly Buy" rules never
    enter into it, and the confirm exercises `_check_line`'s capacity ledger alone.

    ACCEPTED only because the credit is read: `state_at_least(..., credit_qty=40)`
    raises the bin's stated floor from `before=0` to `max(0, 40) = 40`, which clears
    Reserve 40. Reasoned through `_check_line` directly (not by disabling the block):
    if the own-arrival credit block were skipped, or the ordinary `capacity[own_code]`
    were never raised past `before`, this same Reserve of 40 would read a floor of 0
    and be refused as `ReserveOverHand` / a capacity refusal - so this control is now a
    real one, not one the ordinary reading alone would also pass. GREEN today (S3's own
    fix already reads the credit through `state_at_least`, per this file's B1 test
    above); pinned so a fix that over-tightens the ledger - refusing a legitimate,
    fully credit-backed Reserve - is caught here too.
    """
    within_window = date.today() + timedelta(days=10)
    dominant = date.today() + timedelta(days=5)
    with blank_session() as db:
        company_id, actor, project, product = _world(db)
        own = _warehouse(db, f"ZZT-OWN-{_uid()[:4]}")
        _stock(db, product, own, on_hand=100)

        competing_so = _core_so(db, company_id)
        _core_line(
            db, competing_so, product, own, qty_ordered="100", required_date=dominant,
        )

        core_so = _core_so(db, company_id)
        core_line = _core_line(
            db, core_so, product, own, qty_ordered="40", required_date=within_window,
        )
        core_line.source_ref = f"ZZT-B1-CTRL-{_uid()[:8]}"
        db.flush()
        po = supplier_and_po(db, po_number=f"ZZT-PO-B1-CTRL-{_uid()[:8]}")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref=core_line.source_ref,
            qty_received=40, qty_ordered=40,
        )

        order = _project_so(db, project, so_id=core_so.id)
        line = _project_line(db, order, line_no=1, product=product, core_line=core_line)
        db.commit()

        proposal = ProjectSupplyService(db).proposal_for(order)
        components = proposal["lines"][0]["components"]
        reserve = sum(
            (Decimal(c["qty"]) for c in components if c["kind"] == "reserve"), Decimal("0"),
        )
        assert reserve == Decimal("40"), (
            "the board's own proposal must offer the whole line as Reserve (the "
            f"own-arrival credit 40 covers it exactly, the ordinary reading here is "
            f"0): components={components}"
        )

        result = ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=str(line.id),
                        reserve=[{"warehouse_id": str(own.id), "qty": "40"}],
                    ),
                ]
            ),
            actor_user_id=actor,
        )
        assert result["exceptions"] == [], result
        assert result["lines_decided"] == 1, result


# ============================================================================
# S5 (second review round, 21 Sep): the ledger must be charged with what was DRAWN,
# never with the whole theoretical credit computed at candidate-build time.
# ============================================================================


def test_credit_ledger_is_charged_with_what_was_drawn_not_the_whole_credit():
    """S5: `own_arrival_credit_for` is called from `walk()` at candidate-BUILD time
    (`project_supply_service.py`), BEFORE `front_planning_engine.walk_line`'s own
    pool-share sub-step (step 0, drawn ahead of the own-arrival sub-step)
    has told the walk how much of the line's need is already covered from elsewhere.
    `own_arrival_credit_for` computes its credit against `fact.open_qty` - the line's
    WHOLE open quantity - and charges the on-hand ledger (`own_arrival_left[own_code]`)
    for that FULL amount immediately. `walk_line` then draws the own-arrival
    candidate only up to `need` (the line's remainder AFTER the pool share), which can be
    SMALLER than what was already charged - so a sibling line reading the SAME bin's
    on-hand ledger afterwards sees less left than physically true.

    MEASURED SHAPE (`pool_share` fed via `own.pool_warehouse_id`, the same mechanism
    `tests.scm.test_project_supply_service_ladder._group_sites` uses -
    `pool_share_capacity` / `_draw_pool_share`, `front_planning_engine.py`):
    one order, two lines at the SAME own bin, on DIFFERENT required dates so they are two
    separate planning units (`_unit_key` = product + warehouse + date,
    `project_supply_service.py`) and each is walked, and its own-arrival
    credit computed, separately - line 1's PO receipt is not read a second time as line
    2's own tier-1 credit; this file's own S1/S2/B2 tests already pin the tier-1/tier-2
    split, so this test isolates the DIFFERENT bug S5 names by giving line 2 its OWN
    separate PO line rather than relying on line 1's tier-2 spare (see note below on why
    the brief's literal "tier-2 spare from line 1" phrasing does not reproduce against the
    real formula).

    - on hand at the shared own bin: 70.
    - a site pool linked to that bin, on hand 60: default policy (50% dealer retention,
      `DEFAULT_POOL_SHARE_PCT`) spares an allowance of 30 - the whole of it, since the
      pool's OWN floor (60) exceeds the allowance.
    - line 1 needs 40 (own PO line received 40 = tier 1): pool share takes 30 first
      (step 0), leaving a remainder/need of 10 for own-arrival - the credit's ACTUAL draw
      is 10, and line 1 is fully covered (Reserve 30 pool + Reserve 10 own_arrival, Buy 0).
    - line 2 needs 40 too (its OWN separate PO line, also received 40 = tier 1) - the
      pool's project share is already spent by line 1 (one ledger per pool, v8 R-B), so
      line 2 gets nothing from the pool and its need is the whole 40. The group's ordinary
      take at this bin is ALREADY negative before any credit is subtracted (on hand 70,
      the two lines' own combined SO demand 80), so the ordinary rung (question 1) offers
      line 2 nothing either - own-arrival is the only capacity either line has, exactly
      like AC-S3-1's own shape, without needing a third competing order to force it.

    CORRECT (physical): line 1 only ever draws 10 off the bin's on-hand credit-wise (the
    other 30 of its need came from the POOL, a different physical location); 70 - 10 = 60
    remains for line 2's own tier-1 credit, comfortably covering its 40 - Reserve 40
    (own_arrival), Buy 0.

    BEFORE THE FIX: line 1's own-arrival credit was computed as `min(tier1_qty=40,
    open_qty=40) = 40` at candidate-build time and the ON-HAND ledger was charged the
    full 40 - not the 10 `walk_line` actually draws once the pool share is netted out.
    Line 2 then read a falsely-drained ledger (70 - 40 = 30, not the true 70 - 10 = 60)
    and its own credit was capped at `min(40, 30) = 30` - a false, uncharged Buy of 10
    where physically there is none. This test guards the fix: the ledger is charged with
    what `walk_line` actually drew (10) rather than the theoretical credit computed
    before the pool share's own draw was known.

    STATED PLAINLY (the brief's own "if you cannot make the pool share cover part of line
    1 ... report the shape you found" clause): line 1 needing 40 with an own PO line
    received exactly 40 gives it a real tier-2 SPARE of `40 - min(40, qty_ordered 40) = 0`
    by `own_arrival_credit_for`'s own formula (`project_supply_service.py` -
    `spare = sibling_received - min(sibling_received, sibling.qty_ordered)`), which nets
    a sibling's spare against its OWN `qty_ordered` and is entirely blind to whether a
    DIFFERENT rung (pool share) covered part of that sibling's need - so "line 1's unused
    pool-covered 30 becomes line 2's tier-2 spare" is not a shape the real tier-2 formula
    produces; only a line whose PO received MORE than it ordered has real tier-2 spare,
    which is a different, already-covered bug (MB2, `test_tier2_spare_is_spent_once_
    across_siblings`). The reproducible shape for S5's OWN bug (ledger charged the
    theoretical credit, not the actual draw) is the ON-HAND ledger two SEPARATE lines
    share at the SAME bin, pinned above, and giving line 2 its own tier-1 PO rather than a
    tier-2 read off line 1 is what isolates it from the already-covered tier-2 bugs.
    """
    within_window = date.today() + timedelta(days=10)
    later_window = date.today() + timedelta(days=11)
    with blank_session() as db:
        company_id, actor, project, product = _world(db)
        own = _warehouse(db, f"ZZT-OWN-{_uid()[:4]}")
        pool = _warehouse(db, f"ZZT-POOL-{_uid()[:4]}")
        own.pool_warehouse_id = pool.id
        db.flush()
        _stock(db, product, own, on_hand=70)
        _stock(db, product, pool, on_hand=60)

        core_so = _core_so(db, company_id)
        core_line_1 = _core_line(
            db, core_so, product, own, qty_ordered="40", required_date=within_window,
        )
        core_line_1.source_ref = f"ZZT-S5-L1-{_uid()[:8]}"
        db.flush()
        po1 = supplier_and_po(db, po_number=f"ZZT-PO-S5-L1-{_uid()[:8]}")
        po_line_bought_for(
            db, po1, product, own, from_so_line_ref=core_line_1.source_ref,
            qty_received=40, qty_ordered=40,
        )

        core_line_2 = _core_line(
            db, core_so, product, own, qty_ordered="40", required_date=later_window,
        )
        core_line_2.source_ref = f"ZZT-S5-L2-{_uid()[:8]}"
        db.flush()
        po2 = supplier_and_po(db, po_number=f"ZZT-PO-S5-L2-{_uid()[:8]}")
        po_line_bought_for(
            db, po2, product, own, from_so_line_ref=core_line_2.source_ref,
            qty_received=40, qty_ordered=40,
        )

        order = _project_so(db, project, so_id=core_so.id)
        line_1 = _project_line(
            db, order, line_no=1, product=product, core_line=core_line_1,
        )
        line_2 = _project_line(
            db, order, line_no=2, product=product, core_line=core_line_2,
        )
        db.commit()

        proposal = ProjectSupplyService(db).proposal_for(order)
        by_line_no = {line["line_no"]: line for line in proposal["lines"]}
        components_1 = by_line_no[line_1.line_no]["components"]
        components_2 = by_line_no[line_2.line_no]["components"]

        reserve_1 = sum(
            (Decimal(c["qty"]) for c in components_1 if c["kind"] == "reserve"), Decimal("0"),
        )
        buy_1 = sum(
            (Decimal(c["qty"]) for c in components_1 if c["kind"] == "buy"), Decimal("0"),
        )
        assert reserve_1 == Decimal("40") and buy_1 == Decimal("0"), (
            f"line 1 (pool share 30 + own-arrival credit 10) should be fully covered: "
            f"reserve={reserve_1} buy={buy_1} components={components_1}"
        )

        reserve_2 = sum(
            (Decimal(c["qty"]) for c in components_2 if c["kind"] == "reserve"), Decimal("0"),
        )
        buy_2 = sum(
            (Decimal(c["qty"]) for c in components_2 if c["kind"] == "buy"), Decimal("0"),
        )
        own_arrival_2 = sum(
            (
                Decimal(c["qty"])
                for c in components_2
                if c.get("rung") == "group_take" and c.get("source") == "own_arrival"
            ),
            Decimal("0"),
        )
        assert buy_2 == Decimal("0") and reserve_2 == Decimal("40"), (
            "line 1 only ever draws 10 off the shared bin's on-hand for its own-arrival "
            "credit (the other 30 of its need is the POOL's, a different location); 60 "
            "physically remains for line 2's own separate PO receipt (40), so line 2 "
            f"must be Reserve 40 / Buy 0, not a false Buy from an over-charged ledger: "
            f"reserve={reserve_2} buy={buy_2} own_arrival={own_arrival_2} "
            f"components={components_2}"
        )


# ============================================================================
# AC-S3-15 (round-4 fix round, browser-pass finding, 21 Sep): R7's Buy-over-credit
# refusal is wired ONLY into `set_row_decision` (`planning_change_service.py`'s
# `_refuse_buy_over_own_arrival`, the planning-changes batch amend path). A draft save
# is deliberately lenient by design ("a draft claims no stock", nothing to refuse there).
# But the ORDINARY board Confirm (`POST .../fulfilment-planning/confirm-all` ->
# `ProjectSupplyService.confirm` -> `_check_line`, the SAME seam `_check_line`'s own
# `own_arrival_left`/`state_at_least` credit block already reads for Reserve) has no
# equivalent check at all - `_check_line`'s buy handling never reads
# `own_arrival_credit_for`/`own_arrival_left` the way its Reserve half does, so CS can
# confirm "Buy 20" straight through on a line whose own PO already landed 20, the exact
# thing R7 exists to stop `set_row_decision`'s amend from doing.
# ============================================================================


def test_ac_s3_15_board_confirm_refuses_a_buy_over_own_arrival():
    """AC-S3-15: line needs 20, its own PO line received 40, 40 on hand, no competing
    demand at all - the line's own-arrival credit (`min(tier1 40, open_qty 20)`, capped
    by the 40 on hand) is a clean 20, the WHOLE line. A confirm naming `buy_qty=20` and
    NO Reserve at the credited bin at all must be refused the same way
    `_refuse_buy_over_own_arrival` refuses an amend of the identical shape - reused
    code `planning_change_buy_over_own_arrival`, 409, message naming the credited
    quantity (20) and the document goods landed on (the SPO, per the R7 follow-up
    `PLAN-r7-landed-reads-spo-received.md`, R3 - never the PO).

    RED today: `_check_line`'s buy handling (`project_supply_service.py`) never reads
    the line's own-arrival credit at all - `ProjectSupplyService(db).confirm(...)`
    (the same call the board's own `POST .../fulfilment-planning/confirm-all` route
    makes into `_check_line`) simply succeeds, `exceptions == []`, `lines_decided ==
    1`, and `pytest.raises(AppException)` below fails with "DID NOT RAISE" - there is
    no refusal to catch, because the seam that would raise one does not exist at
    confirm time, only at `set_row_decision`'s amend time.

    #1362 (owner ruling, 29 Sep 2026): no longer refused. "this good is on hand, and is
    covering the line, but, from fulfilment planning, is kind of requesting it to be
    delayed while the link is intact, then only purchasing will do the adjustment in the
    linkage" - the Buy is confirmed as decided and the confirm returns a notice naming
    what landed, which is what this test now pins.
    """
    within_window = date.today() + timedelta(days=10)
    with blank_session() as db:
        company_id, actor, project, product = _world(db)
        own = _warehouse(db, f"ZZT-OWN-{_uid()[:4]}")
        _stock(db, product, own, on_hand=40)

        core_so = _core_so(db, company_id)
        core_line = _core_line(
            db, core_so, product, own, qty_ordered="20", required_date=within_window,
        )
        core_line.source_ref = f"ZZT-S315-{_uid()[:8]}"
        db.flush()
        po = supplier_and_po(db, po_number=f"ZZT-PO-S315-{_uid()[:8]}")
        _po_line, spo = po_line_bought_for(
            db, po, product, own, from_so_line_ref=core_line.source_ref,
            qty_received=40, qty_ordered=40,
        )

        order = _project_so(db, project, so_id=core_so.id)
        line = _project_line(db, order, line_no=1, product=product, core_line=core_line)
        db.commit()

        spo_number = spo.spo_number
        result = ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(project_line_id=str(line.id), buy_qty="20"),
                ]
            ),
            actor_user_id=actor,
        )
        assert result["revision_no"] is not None, result
        (notice,) = result["landed_buy_notices"]
        assert notice["landed"] == f"20 landed for this line on {spo_number}", notice


def test_ac_s3_15_control_buy_beside_a_fully_reserved_credit_is_accepted():
    """AC-S3-15's control: the SAME line shape (needs 20, own PO line received 40) but
    on hand only 15, so the credit is capped at 15 (not the whole line) - a confirm
    that Reserves exactly the credited 15 at the credited bin and Buys the remaining 5
    (which the credit never claimed) must be ACCEPTED, both before and after AC-S3-15's
    own fix: nothing about this Buy is "over" the credit, since the credit's own 15 is
    fully Reserved. Pinned so a fix that over-tightens the eventual buy-refusal into
    blocking ANY Buy beside a partially-credited line - rather than only the part that
    would drop reserve below what is credited - is caught here.
    """
    within_window = date.today() + timedelta(days=10)
    with blank_session() as db:
        company_id, actor, project, product = _world(db)
        own = _warehouse(db, f"ZZT-OWN-{_uid()[:4]}")
        _stock(db, product, own, on_hand=15)

        core_so = _core_so(db, company_id)
        core_line = _core_line(
            db, core_so, product, own, qty_ordered="20", required_date=within_window,
        )
        core_line.source_ref = f"ZZT-S315-CTRL-{_uid()[:8]}"
        db.flush()
        po = supplier_and_po(db, po_number=f"ZZT-PO-S315-CTRL-{_uid()[:8]}")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref=core_line.source_ref,
            qty_received=40, qty_ordered=40,
        )

        order = _project_so(db, project, so_id=core_so.id)
        line = _project_line(db, order, line_no=1, product=product, core_line=core_line)
        db.commit()

        result = ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=str(line.id),
                        reserve=[{"warehouse_id": str(own.id), "qty": "15"}],
                        buy_qty="5",
                        # AC-L5 (`_check_line`'s own "wholly stock or wholly Buy" rule):
                        # a mix reaching confirm is refused without a reason - unrelated
                        # to AC-S3-15's own seam, so it is named here rather than left to
                        # trip this control on the wrong guard.
                        amend_reason="ZZT AC-S3-15 control - partial credit reserved, remainder bought",
                    ),
                ]
            ),
            actor_user_id=actor,
        )
        assert result["exceptions"] == [], result
        assert result["lines_decided"] == 1, result


# ============================================================================
# Round-5 reds (owner rulings, 22 Sep): B-2 (merge-blocking, browser-pass finding) and
# S-1, two more seams inside AC-S3-15's own confirm-time refusal
# (`_check_line`, `project_supply_service.py`).
# ============================================================================


def test_ac_s3_15_refusal_names_the_credited_quantity_not_the_uncovered_part():
    """S-1: AC-S3-15's own refusal message must name the CREDITED quantity, the same
    wording `_refuse_buy_over_own_arrival` (`planning_change_service.py`, the amend-path
    sibling of this same rule) already states for its own seam - "N landed for this line
    on ...", N being what landed for the line, not whatever a partial Reserve happened
    to leave uncovered of it.

    Line needs 20, its own PO line received 40, 40 on hand (the credit is a clean 20 -
    the line's own open qty caps the theoretical credit before on hand ever does). A
    confirm posts Reserve 8 at the credited bin plus Buy 12 (8 + 12 = 20, the whole
    line): refused as `planning_change_buy_over_own_arrival`, and the message must read
    "20 landed for this line on ...", not "12 landed for this line on ...".

    RED today: `_check_line`'s message names `uncovered` (`credit_qty -
    reserved_at_credit_bin` = 20 - 8 = 12) rather than `credit_qty` (20) itself, so the
    message reads "12 landed for this line on ..." - the part the posted Reserve left
    short, not what actually landed. Document name updated by the R7 follow-up
    (`PLAN-r7-landed-reads-spo-received.md`): the message names the SPO, never the PO.

    #1362 (owner ruling, 29 Sep 2026): no longer refused. "this good is on hand, and is
    covering the line, but, from fulfilment planning, is kind of requesting it to be
    delayed while the link is intact, then only purchasing will do the adjustment in the
    linkage" - the Buy is confirmed as decided and the confirm returns a notice naming
    what landed, which is what this test now pins.
    """
    within_window = date.today() + timedelta(days=10)
    with blank_session() as db:
        company_id, actor, project, product = _world(db)
        own = _warehouse(db, f"ZZT-OWN-{_uid()[:4]}")
        _stock(db, product, own, on_hand=40)

        core_so = _core_so(db, company_id)
        core_line = _core_line(
            db, core_so, product, own, qty_ordered="20", required_date=within_window,
        )
        core_line.source_ref = f"ZZT-S315-NAME-{_uid()[:8]}"
        db.flush()
        po = supplier_and_po(db, po_number=f"ZZT-PO-S315-NAME-{_uid()[:8]}")
        _po_line, spo = po_line_bought_for(
            db, po, product, own, from_so_line_ref=core_line.source_ref,
            qty_received=40, qty_ordered=40,
        )

        order = _project_so(db, project, so_id=core_so.id)
        line = _project_line(db, order, line_no=1, product=product, core_line=core_line)
        db.commit()

        spo_number = spo.spo_number
        result = ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=str(line.id),
                        reserve=[{"warehouse_id": str(own.id), "qty": "8"}],
                        buy_qty="12",
                        amend_reason=(
                            "ZZT AC-S3-15 - partial reserve at the credited bin, "
                            "remainder bought"
                        ),
                    ),
                ]
            ),
            actor_user_id=actor,
        )
        (notice,) = result["landed_buy_notices"]
        message = notice["reason"]
        assert "20 landed for this line on" in message and spo_number in message, (
            f"the message must name the credited quantity (20), not the uncovered part "
            f"(12) the posted Reserve happened to leave: message={message!r}"
        )
        assert "12 landed" not in message, message


def test_ac_s3_16_confirm_never_refuses_the_boards_own_buy_for_a_line_outside_the_reserve_window():
    """AC-S3-16 (B-2, merge-blocking, browser-pass finding): the confirm-time refusal
    must follow the SAME reserve-window verdict the composer already reads
    (`outside_reserve_window`, `walk()`'s own `if not outside_window:` gate around
    `_own_arrival_credit_components`) - a line due beyond `as_of + lead time +
    RESERVE_BUFFER_DAYS` is never credited at all, composed or confirmed, so its Buy is
    never "over" a credit that was never on offer for it.

    Line needs 20, required 400 days out (well beyond the default 90-day lead time plus
    14-day buffer window, ~104 days), its own PO line received 40, 40 on hand at its bin
    - the credit's physical shape is exactly AC-S3-15's own (a clean 20), but the LINE
    itself is outside the reserve window. `proposal_for` must compose a pure Buy 20,
    reason naming the lead-time window, and a confirm posting `buy_qty=20` (no Reserve
    at all) must be ACCEPTED, `lines_decided == 1`.

    RED today: `_check_line`'s own credit re-derivation (`own_arrival_credit_for` ->
    `_own_arrival_credit_components`) never reads `outside_reserve_window` at all - it is
    a plain function of tier 1/tier 2 receipts and on hand, with no date gate - so the
    confirm still sees `credit_qty = 20` for a line the composer itself refuses to
    touch, and wrongly refuses the board's own Buy proposal as
    `planning_change_buy_over_own_arrival`.
    """
    outside_window = date.today() + timedelta(days=400)
    with blank_session() as db:
        company_id, actor, project, product = _world(db)
        own = _warehouse(db, f"ZZT-OWN-{_uid()[:4]}")
        _stock(db, product, own, on_hand=40)

        core_so = _core_so(db, company_id)
        core_line = _core_line(
            db, core_so, product, own, qty_ordered="20", required_date=outside_window,
        )
        core_line.source_ref = f"ZZT-S316-{_uid()[:8]}"
        db.flush()
        po = supplier_and_po(db, po_number=f"ZZT-PO-S316-{_uid()[:8]}")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref=core_line.source_ref,
            qty_received=40, qty_ordered=40,
        )

        order = _project_so(db, project, so_id=core_so.id)
        line = _project_line(db, order, line_no=1, product=product, core_line=core_line)
        db.commit()

        proposal = ProjectSupplyService(db).proposal_for(order)
        components = proposal["lines"][0]["components"]
        reserve = sum(
            (Decimal(c["qty"]) for c in components if c["kind"] == "reserve"), Decimal("0"),
        )
        buy_components = [c for c in components if c["kind"] == "buy"]
        buy = sum((Decimal(c["qty"]) for c in buy_components), Decimal("0"))
        assert reserve == Decimal("0") and buy == Decimal("20"), (
            "a line due 400 days out is outside the reserve window: the whole 20 must "
            f"compose as Buy, own-arrival credit never offered for it: components={components}"
        )
        assert buy_components and "lead time window" in (
            buy_components[0].get("reason") or ""
        ), buy_components

        result = ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(project_line_id=str(line.id), buy_qty="20"),
                ]
            ),
            actor_user_id=actor,
        )
        assert result["exceptions"] == [], result
        assert result["lines_decided"] == 1, result
