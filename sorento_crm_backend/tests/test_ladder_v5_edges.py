"""Ladder v5 edges the captain's own walk-through (`PLAN-scm-cs-planning-uat.md` 1e) and the
two proof files (`tests/scm/test_ladder_v5.py`, `tests/test_ladder_v5_proof.py`) do not
already pin: a cap that genuinely binds rather than being trivially exceeded, the whole-line
rule's own sentence on the question that HAD stock, a late SPO's overdue days as the trail and
the drill-down table each treat them, a decided line carrying a frozen incoming component
beside a stale suggestion stamp, a payload-wide scan for leaked rung vocabulary, the other-group
block's zero-holding sibling and its exact arithmetic, and the query-count bound on a big board.

TESTER'S BRIEF: pin current behaviour. Where a fixture in the brief describes something the
ladder's own rules (as ruled in section 1e) make impossible - a PARTIAL cross-group take, for
one - the docstring says so and the test asserts what the code actually does instead, never
what the brief assumed.

Postgres via `blank_session`, every FK target seeded here (CI's database is empty).
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.services.project_supply_service import LADDER_VERSION
from app.services.error_handler import AppException

from ._pg_fixture import blank_session
from .test_fulfilment_board import (  # noqa: F401  (helpers, not fixtures)
    MARKER,
    TODAY,
    _adopt,
    _cell,
    _confirm,
    _incoming,
    _line,
    _order,
    _pool,
    _product,
    _service,
    _step,
    _stock,
    _uid,
    _user,
    _warehouse,
)
from .test_ladder_v5_proof import BUCKET, WHEN, _contribution, _locations, _policy


# --------------------------------------------------------------------------- #
# 1. Question 3's cap sentence, genuinely binding (not trivially exceeded)
# --------------------------------------------------------------------------- #


def test_question_three_takes_the_whole_line_where_the_cap_used_to_refuse_it():
    """The v5 case, inverted by v7.1 (R5, migration 443).

    Donor net 100, need 24. Under v5 a configured cap of 20 stood between the donor and a
    Yes, and the sentence had to quote it. The cap is gone - any ownership group may donate
    - so the only rule left is the binary whole-line one, and 100 covers 24 entire: the
    answer is Yes, 24, and there is no Buy at all.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTM{_uid()[:5]}-BB"[:20])
        donor = _warehouse(db, f"ZZTM{_uid()[:5]}-NTC"[:20])
        _stock(db, product, own, on_hand=0)
        _stock(db, product, donor, on_hand=100)
        _policy(db)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="24", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)

        # LADDER V7.1 (R5): another PROJECT group's FREE pile is step 1's second half, not
        # a borrow rung. Free means owed to nobody, so it is a Reserve and it raises no
        # order-back - which is why it is walked before either borrow step and before the
        # pool. The quantity and the donor are unchanged; what changed is which question
        # answers, and that is the whole of R5.
        step = _step(contribution, "own")
        assert step["answer"] == "yes"
        assert step["took"] == "24"
        assert donor.warehouse_code in step["why"]
        assert "cross-group borrow limit" not in step["why"]

        assert [s["kind"] for s in contribution["sources"]] == ["reserve"]
        assert contribution["sources"][0]["qty"] == "24"
        assert contribution["sources"][0]["location"] == donor.warehouse_code


# --------------------------------------------------------------------------- #
# 2. The whole-line rule's own sentence, on the question that HAD stock
# --------------------------------------------------------------------------- #


def test_question_one_names_the_whole_line_rule_when_it_had_stock_but_not_enough():
    """The sentence pinned by name: `_WHOLE_LINE_RULE_DROPPED` - "It had stock to give,
    but the questions together could not cover the whole line, so none of it is taken and
    the line is bought entire." Own group free 10, no pool, no donor anywhere else, need
    24: 10 of 24 is short, so nothing is taken and the whole line buys - and question 1 is
    the one that HAD something and gave it back, so it is the one that must carry this
    sentence rather than a plain "nothing here".
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTN{_uid()[:5]}-BB"[:20])
        pool = _pool(db, f"ZZTN{_uid()[:5]}"[:20])
        own.pool_warehouse_id = pool.id
        db.flush()
        _stock(db, product, own, on_hand=10)
        _stock(db, product, pool, on_hand=0)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="24", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)

        own_step = _step(contribution, "own")
        assert own_step["answer"] == "no"
        assert own_step["took"] == "0"
        assert "could not cover the whole line" in own_step["why"]
        assert "bought entire" in own_step["why"]

        buy = _step(contribution, "buy")
        assert buy["answer"] == "yes"
        assert buy["took"] == "24"
        assert [s["kind"] for s in contribution["sources"]] == ["buy"]
        assert contribution["sources"][0]["qty"] == "24"


# --------------------------------------------------------------------------- #
# 3. A late SPO backing the group's only cover: what the trail says, and does not
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("ladder_steps_on")
def test_an_overdue_promise_that_still_lands_in_time_is_drawn_as_water_and_dated():
    """R-O (3 September 2026, #586) SUPERSEDES R31 (29 August 2026) for a document that is
    late but not dead.

    R31 read an overdue promise as not supply at all - the captain's ruling that every one
    of the 725 open SPO lines on the live book was dated August 2026 or earlier, and
    promising against a passed date was promising against paperwork nobody believed. R-O
    keeps that distrust but gives it a number: a document whose arrival has passed with
    nothing received still counts, landing on `as_of + overdue_grace_days` rather than the
    date it stated. This fixture dates the document a FIXED number of days before `as_of`
    (`TODAY`, 18 Aug 2026), not off the wall clock, so the walk's own lateness
    (`days_late` on the trail's sentence) never drifts with the calendar. Its ASSUMED
    arrival - 1 Sep, off the RECOMMENDED 14-day grace this test activates explicitly -
    lands before the line's own 3 Sep, so question 1 draws it whole, dated at the assumed
    day, and the sentence states the lateness. A document past `overdue_dead_days` (90,
    also activated explicitly) is still not supply (R31 stands for the dead).

    R-O SHIPS at 0 / 0 (captain's ruling, 3 Sep 2026), so this fixture activates the
    RECOMMENDED 14 / 90 itself rather than relying on the shipped default - it is proving
    the grace RULE, not the number production starts at.

    The DRILL-DOWN table (`stock_detail`, behind the frontend's `StockDocumentsPanel`) keeps
    carrying `overdue_days` off the WALL CLOCK instead of `as_of`, which is where "go and
    chase this one" belongs - proved below on its own document, dated off `date.today()`,
    so that proof stays self-consistent (both the fixture and the measurement read the same
    real clock) without ever needing to agree with the walk's `TODAY`-relative document above.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTP{_uid()[:5]}-BB"[:20])
        _policy(db, overdue_grace_days=14, overdue_dead_days=90)
        # Fixed offset from `TODAY` (the walk's own `as_of`), so `days_late` on the trail's
        # sentence is a constant, not TODAY-minus-wall-clock. 40 days keeps the wording
        # unambiguously plural and stays well inside the 90-day dead grace.
        arrives = TODAY - timedelta(days=40)
        _incoming(
            db, product, own, spo_number="ZZT-SPO-LATE", allocated=40, received=0,
            arrives=arrives,
        )
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="40", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)

        own_step = _step(contribution, "own")
        assert own_step["answer"] == "yes", "R-O: a late-but-alive document is supply again"
        assert own_step["took"] == "40"
        assert [(s["kind"], s["rung"]) for s in contribution["sources"]] == [
            ("timely_spo", "group_take")
        ]
        source = contribution["sources"][0]
        # The ASSUMED date (`as_of` + the 14-day grace), not the date the paperwork states.
        assert source["arrival_date"] == TODAY + timedelta(days=14)
        expected_late = (TODAY - arrives).days
        assert f"is {expected_late} days late, assumed by" in source["reason"], source["reason"]

    # A SEPARATE document for the drill-down's wall-clock proof: `stock_detail` reads
    # `spo_supply.overdue_days()` against real `date.today()`, never `as_of`, so dating this
    # one off `date.today()` too keeps `overdue_days == 40` true on every date it runs,
    # without needing to also satisfy the walk's fixed-`TODAY` document above.
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTP{_uid()[:5]}-BB"[:20])
        _policy(db, overdue_grace_days=14, overdue_dead_days=90)
        _stock(db, product, own, on_hand=0)
        wall_clock_arrives = date.today() - timedelta(days=40)
        late = _incoming(
            db, product, own, spo_number="ZZT-SPO-LATE-DRILLDOWN", allocated=40,
            received=0, arrives=wall_clock_arrives,
        )

        detail = _service(db).stock_detail(str(product.id), str(own.id))
        incoming_row = next(
            row for row in detail["incoming"] if row["spo_number"] == late.spo_number
        )
        assert incoming_row["overdue_days"] == 40, (
            "chasing the supplier is the table's job, and it still carries the raw fact"
        )


@pytest.mark.usefixtures("ladder_steps_on")
def test_water_arriving_after_the_required_date_is_named_with_its_date_and_never_drawn():
    """THE CAPTAIN'S RULING ON THE WATER, second half: "30 on the water, arrives 1 Mar, not
    counted".

    The group's NET counts this SPO (it is the group's position, date-blind by design), so
    the offer question 1 computes is positive - and not a unit of it may be drawn, because
    it lands after the line needs it. Both facts are in the one sentence, because either on
    its own reads as a defect: a positive net with a Buy beside it looks like arithmetic
    nobody did, and a silent refusal looks like stock that does not exist.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTW{_uid()[:5]}-BB"[:20])
        _stock(db, product, own, on_hand=0)
        arrives = WHEN + timedelta(days=180)
        _incoming(
            db, product, own, spo_number="ZZT-SPO-AFTER", allocated=30, received=0,
            arrives=arrives,
        )
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="30", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)

        own_step = _step(contribution, "own")
        assert own_step["answer"] == "no"
        assert own_step["took"] == "0"
        assert "30 to " + own.warehouse_code in own_step["why"], own_step["why"]
        assert f"arriving {arrives.day} " in own_step["why"], own_step["why"]
        assert "after the required date" in own_step["why"]
        assert [s["kind"] for s in contribution["sources"]] == ["buy"]
        assert contribution["qty_proposed_buy"] == "30"


# --------------------------------------------------------------------------- #
# 4. A decided line with a frozen incoming component, and a stale suggestion stamp
# --------------------------------------------------------------------------- #


def test_a_decided_incoming_component_totals_on_the_contribution_and_stales_correctly():
    """AC-V8 + section 3.D's own accounting, wired through the board endpoint.

    `timely_spo_qty` is still an accepted `confirm` field (`ConfirmLine.timely_spo_qty`)
    even though the LIVE ladder never proposes one under v5 - a planner can still record a
    decision that way. `qty_proposed_incoming` on the DECIDED contribution totals it
    (`_apply_frozen` reads it straight off `decision["timely_spo_qty"]`), and it still
    renders.

    The frozen SUGGESTION beside it is downgraded here (direct model write, the same
    technique `test_so_supply_confirmation.py`'s own ladder-stamp test uses) to simulate
    one written before v5 shipped - what `BoardCellBreakdownDialog`'s `suggestionIsStale`
    reads to print "Suggestion (before ladder v5)". A second, undecided line on the same
    order carries no such staleness: every part of its live suggestion is stamped
    the LIVE `ladder` stamp (AC-V8's other half).
    """
    from app.models.project_so import ProjectSalesOrderLine, SOSupplyDecision

    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTQ{_uid()[:5]}-BB"[:20])
        _incoming(
            db, product, own, spo_number="ZZT-SPO-DEC", allocated=15, received=0,
            arrives=WHEN - timedelta(days=5),
        )
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        decided_core = _line(
            db, order, product, qty="15", required_date=WHEN, warehouse=own,
        )

        other_product = _product(db, f"ZZT-{_uid()[:6]}")
        other_own = _warehouse(db, f"ZZTQ{_uid()[:5]}-BB"[:20])
        _line(
            db, order, other_product, qty="8", required_date=WHEN, warehouse=other_own,
        )

        pso_id = _adopt(db, str(order.id))
        mirror = (
            db.query(ProjectSalesOrderLine)
            .filter(
                ProjectSalesOrderLine.project_sales_order_id == pso_id,
                ProjectSalesOrderLine.core_sales_order_line_id == decided_core.id,
            )
            .first()
        )
        actor = _user(db, f"{MARKER} planner")
        _confirm(
            db, pso_id, actor,
            [{"project_line_id": str(mirror.id), "timely_spo_qty": "15"}],
        )

        decision = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == pso_id)
            .order_by(SOSupplyDecision.revision_no.desc())
            .first()
        )
        # DEEP copy, not `list(...)`: a shallow copy shares the same nested dicts, so
        # mutating them in place changes the ORIGINAL value too and SQLAlchemy's history
        # comparison (before == after, by content) sees no change and skips the UPDATE.
        import copy

        snapshots = copy.deepcopy(decision.line_snapshots)
        for snapshot in snapshots:
            if snapshot.get("core_line_id") == str(decided_core.id):
                for component in snapshot.get("proposed_components") or []:
                    component["ladder"] = "v4"
        decision.line_snapshots = snapshots
        db.commit()

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        decided_contribution = _cell(board, product.product_code, BUCKET)["contributions"][0]
        assert decided_contribution["covered"] is True
        assert decided_contribution["qty_proposed_incoming"] == "15"
        proposed_parts = decided_contribution["proposed"]["components"]
        assert proposed_parts, "a covered line still carries a suggestion beside its decision"
        # Captain, 28 Aug 2026 (ruling 1): a covered line's suggestion is TODAY'S ladder,
        # not the snapshot frozen with the decision. The downgraded snapshot above is the
        # record and stays in `line_snapshots`; what the board serves is live and so is
        # stamped by the current ladder, which is why "Suggestion (before ladder v5)" can
        # no longer be produced from this field.
        assert {part.get("ladder") for part in proposed_parts} == {LADDER_VERSION}, (
            "a covered line's suggestion is the live ladder, never the frozen snapshot"
        )

        undecided_contribution = _cell(
            board, other_product.product_code, BUCKET
        )["contributions"][0]
        assert undecided_contribution["covered"] is False
        assert {
            part["ladder"] for part in undecided_contribution["proposed"]["components"]
        } == {LADDER_VERSION}, "an undecided line never carries a stale stamp"


# --------------------------------------------------------------------------- #
# 5. The trail payload's shape, and no leaked rung vocabulary anywhere in its prose
# --------------------------------------------------------------------------- #

#: The engine's INTERNAL rung keys. Legitimate as the VALUE of `kind` / `rung` (addressing),
#: never inside a rendered sentence (`why` / `note` / `reason`) - section 1e's own words:
#: "the rung names are internal keys under ladder v5 and never reach a reader".
_RUNG_TOKENS = ("group_take", "order_borrow", "supply_borrow", "timely_spo")


def _prose_strings(contribution) -> list:
    """Every rendered sentence in a contribution payload - never the addressing fields."""
    out = []
    for step in contribution["trail"]:
        if step.get("why"):
            out.append(step["why"])
        if step.get("note"):
            out.append(step["note"])
    for source in contribution["sources"]:
        if source.get("reason"):
            out.append(source["reason"])
    proposed = contribution.get("proposed") or {}
    for component in proposed.get("components") or []:
        if component.get("reason"):
            out.append(component["reason"])
    return out


@pytest.mark.usefixtures("ladder_steps_on")
def test_the_trail_is_five_rows_with_no_leaked_rung_vocabulary_and_a_note_that_agrees():
    """AC-V1 shape (five rows, each with `answer` / `took` / `from` / `why`) plus two
    things the existing proof pins do not: a scan of EVERY rendered sentence in the whole
    payload for the engine's internal rung tokens (not merely the trail's own `kind`
    list), and a POSITIVE case for "the suggestion note equals what the trail rows
    imply" - AC-V4's own pin only checks the negative (a cap-refused donor is never named
    in the Buy). Here a donor is NOT refused by the cap, only too small to finish the
    line alone, so question 3 says "could not cover the whole line" and the Buy's own
    sentence is EXPECTED to say borrowing from it is possible - the two must agree in
    both directions, not only when the answer is silence.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTR{_uid()[:5]}-BB"[:20])
        pool = _pool(db, f"ZZTR{_uid()[:5]}"[:20])
        own.pool_warehouse_id = pool.id
        db.flush()
        donor = _warehouse(db, f"ZZTR{_uid()[:5]}-NTC"[:20])
        _stock(db, product, own, on_hand=0)
        _stock(db, product, pool, on_hand=0)
        _stock(db, product, donor, on_hand=10)
        _policy(db)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="24", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)

        # ---- shape ----
        # LADDER V8 (R-A, review round 1 S5): five rows still, in the WALK's own order -
        # the site pool's share leads it now.
        assert [step["question"] for step in contribution["trail"]] == [
            "Can we take from the pool?",
            "Can we use our locations?",
            "Can we borrow on hand from a later order?",
            "Can we borrow incoming from a later order?",
            "Buy",
        ]
        for step in contribution["trail"]:
            assert step["answer"] in ("yes", "no")
            assert isinstance(step["took"], str)
            assert "from" in step
            assert step["why"]

        # ---- no leaked vocabulary anywhere rendered ----
        for text in _prose_strings(contribution):
            for token in _RUNG_TOKENS:
                assert token not in text, (token, text)

        # ---- the donor was offered, not refused by the cap, and the whole line still
        #      buys because 10 alone cannot finish 24 ----
        # The donor is another group's FREE stock, so it is step 1's business now (R5) -
        # and 10 alone cannot finish 24, so the whole-unit rule takes it back.
        used = _step(contribution, "own")
        assert used["answer"] == "no"
        assert used["took"] == "0"
        assert "could not cover the whole line" in used["why"]
        assert "bought entire" in used["why"]

        buy = _step(contribution, "buy")
        assert buy["answer"] == "yes"
        buy_reason = next(s["reason"] for s in contribution["sources"] if s["kind"] == "buy")
        assert "Borrowing is possible" in buy_reason
        assert donor.warehouse_code in buy_reason


# --------------------------------------------------------------------------- #
# 6. The other-group block: a zero-holding sibling, and the exact arithmetic
# --------------------------------------------------------------------------- #


def test_a_donor_groups_sibling_holding_nothing_still_lists_with_the_exact_arithmetic():
    """AC-V3, the two edges the existing pin (`test_ladder_v5_proof.py`'s own
    `test_a_cited_other_group_site_brings_its_whole_group_with_its_own_net`, whose
    siblings hold 40 and 7) does not cover: a sibling holding LITERALLY NOTHING - no
    `Stock` row at all, not merely a small one - still appears, at 0/0/0/0; and a
    sibling with real movement is checked against the exact formula the table promises,
    on hand + SPO - SO, rather than merely "whatever the stock row said".
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTS{_uid()[:5]}-BB"[:20])
        stem = _uid()[:5]
        drawn = _warehouse(db, f"ZZTT{stem}-NTC"[:20])
        sibling_zero = _warehouse(db, f"ZZTU{stem}-NTC"[:20])
        sibling_moves = _warehouse(db, f"ZZTV{stem}-NTC"[:20])
        _stock(db, product, own, on_hand=0)
        _stock(db, product, drawn, on_hand=40)
        _stock(db, product, sibling_moves, on_hand=50)
        _incoming(
            db, product, sibling_moves, spo_number="ZZT-SPO-SIB", allocated=10, received=0,
            arrives=WHEN + timedelta(days=5),
        )
        other_order = _order(
            db, so_number=f"ZZT-SO-B{_uid()[:6]}", order_date=date(2026, 1, 1)
        )
        _line(
            db, other_order, product, qty="15", required_date=date(2027, 1, 1),
            warehouse=sibling_moves,
        )
        _policy(db)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=WHEN, warehouse=own)

        locations = _locations(db, order, product)

        other = {entry["location"]: entry for entry in locations if entry["where"] == "other_group"}
        assert {
            drawn.warehouse_code, sibling_zero.warehouse_code, sibling_moves.warehouse_code,
        } <= set(other), "every active sibling of the donor group is listed"

        zero_row = other[sibling_zero.warehouse_code]
        assert zero_row["qty_on_hand"] == "0"
        assert zero_row["spo_qty"] == "0"
        assert zero_row["so_qty"] == "0"
        assert zero_row["available_qty"] == "0"

        moves_row = other[sibling_moves.warehouse_code]
        assert moves_row["qty_on_hand"] == "50"
        assert moves_row["spo_qty"] == "10"
        assert moves_row["so_qty"] == "15"
        assert moves_row["available_qty"] == "45"
        assert Decimal(moves_row["available_qty"]) == (
            Decimal(moves_row["qty_on_hand"])
            + Decimal(moves_row["spo_qty"])
            - Decimal(moves_row["so_qty"])
        ), "available == on hand + SPO - SO, the formula the table promises"

        # The subtotal is the GROUP's net, over every site including the zero one.
        assert {entry["net"] for entry in other.values()} == {"85"}
        assert {entry["net_of"] for entry in other.values()} == {"NTC"}


# --------------------------------------------------------------------------- #
# 7. A big board's stock reads stay batched, not per line
# --------------------------------------------------------------------------- #


def test_a_board_of_76_lines_does_not_scale_its_query_count_with_the_line_count():
    """Section 1e's own note: "the cell table's batched stock reads now cover every active
    warehouse, because which group is the donor is not known until the engine has
    composed" - widening the SET a board reads must not turn the read itself from one
    round trip per kind of fact into one per LINE. A board of 76 lines, spread over a
    handful of locations and dates, counts its statements with a real
    `before_cursor_execute` listener (the same technique
    `tests/scm/test_project_supply_service_ladder.py`'s own O(1)-queries pin uses) and
    the count must not scale with the number of lines: a fixed, generous bound (40) - not
    a number a colleague's `for line in lines: db.query(...)` could still sneak under a
    smaller board's count.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        grp = f"BB{_uid()[:4]}"
        own = _warehouse(db, f"ZZTW{_uid()[:4]}-{grp}"[:20])
        siblings = [
            _warehouse(db, f"ZZTX{_uid()[:3]}{i}-{grp}"[:20]) for i in range(4)
        ]
        for warehouse in [own, *siblings]:
            _stock(db, product, warehouse, on_hand=5)

        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        line_count = 76
        for i in range(line_count):
            _line(
                db, order, product, qty="1",
                required_date=WHEN + timedelta(days=(i % 10) * 7),
                warehouse=own, line_status="open",
            )
        db.commit()

        calls = {"n": 0}

        def _count(conn, cursor, statement, parameters, context, executemany):
            calls["n"] += 1

        connection = db.connection()
        event.listen(connection, "before_cursor_execute", _count)
        try:
            board = _service(db).build(
                [order.so_number], granularity="week", as_of=TODAY
            )
        finally:
            event.remove(connection, "before_cursor_execute", _count)

        seen_lines = sum(len(cell["contributions"]) for cell in board["cells"])
        assert seen_lines == line_count, "the seed actually reached the board"
        # MEASURED 37 on this fixture (27 August 2026), after `_warehouses_for_groups`
        # stopped scanning the warehouse table once per CELL, 43 on 30 August, after
        # ladder v7.1 added the ONE assignment both the board and the Stock Debt view read
        # (six batched reads for the whole board: stock, demand, SPO, PO, allocations,
        # links) - and 44 on main before this lane (an unrecorded drift from 43). Local-
        # supplier-oi-routing adds ONE more, `buy_origin_by_product`, read once per BUILD
        # (not per line, `test_buy_origin_computed_once_per_board_build` guards that): 45
        # measured at 76 lines and 45 again at 152 lines - flat, which is the property this
        # bound exists to guard, not the number itself. The SO change-management lane
        # (13 Sep 2026) adds one more: `_order_plan_status` reads the adopted record and
        # any pending planning-change batch for the WHOLE selection in a single query, not
        # per line - 46 at the cap. Brand-flows-to-purchasing (22 Sep 2026) adds ONE more:
        # `buy_origin_by_product` now runs a brand-blocked-products read BEFORE the
        # toggle gate, so the statement fires even with the toggle off (where the
        # resolver previously ran none) - 47 at the cap. BORROW-LOCATION-ONLY (8 Oct 2026)
        # adds ONE more: the "Borrow from another order" switch, read once per
        # `ProjectSupplyService` (`borrow_from_order`), so once per build - 48 at the cap.
        # One statement of slack, and no more: the bound is here to catch a
        # `for line in lines: db.query(...)`, and a generous one lets 76 lines' worth of
        # per-line reads hide under it.
        assert calls["n"] <= 48, calls["n"]


# --------------------------------------------------------------------------- #
# 8. One ledger behind question 1's two halves (the water ruling, 27 Aug 2026)
# --------------------------------------------------------------------------- #


@pytest.mark.usefixtures("ladder_steps_on")
def test_the_floor_and_the_water_of_question_one_come_off_one_ledger_at_confirm():
    """Review finding S6/S7. 80 owed, 40 on the floor and 40 on the water arriving in time:
    the group nets 0 and offers this line 80, which is exactly 40 + 40 and not 80 of each.

    The confirm-time recheck used to bound `timely_spo_qty` at the whole `group_offer`
    without subtracting the floor share, so a planner could post `reserve 40 + timely_spo 80`
    and promise the same pile twice. It is bounded at the WATER SHARE question 1 actually
    offered now, and the Reserve half is bounded at the floor candidates alone.
    """
    from app.models.project_so import ProjectSalesOrderLine

    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTL{_uid()[:5]}-BB"[:20])
        _stock(db, product, own, on_hand=40)
        _incoming(
            db, product, own, spo_number="ZZT-SPO-HALF", allocated=40, received=0,
            arrives=WHEN - timedelta(days=3),
        )
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        core = _line(db, order, product, qty="80", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)
        # The engine's own answer: half floor, half water, both under question 1.
        assert [(s["kind"], s["qty"], s["rung"]) for s in contribution["sources"]] == [
            ("reserve", "40", "group_take"),
            ("timely_spo", "40", "group_take"),
        ]

        pso_id = _adopt(db, str(order.id))
        mirror = (
            db.query(ProjectSalesOrderLine)
            .filter(
                ProjectSalesOrderLine.project_sales_order_id == pso_id,
                ProjectSalesOrderLine.core_sales_order_line_id == core.id,
            )
            .first()
        )
        actor = _user(db, f"{MARKER} planner")

        with pytest.raises(AppException) as refused:
            _confirm(
                db, pso_id, actor,
                [
                    {
                        "project_line_id": str(mirror.id),
                        "reserve": [{"warehouse_id": str(own.id), "qty": "40"}],
                        "timely_spo_qty": "80",
                    }
                ],
            )
        detail = refused.value.detail
        said = str(detail.get("failing_lines") or detail)
        assert "Timely SPO cover is now 40, not 80" in said, said


def test_the_composition_question_one_offered_confirms_without_complaint():
    """The other side of S6/S7: `reserve 40 + timely_spo 40` is exactly what the engine
    proposed, so the recheck it is judged by must accept it. A bound that refused the
    proposal's own answer would be a worse defect than the one it was tightened to close."""
    from app.models.project_so import ProjectSalesOrderLine, SOSupplyDecision

    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTL{_uid()[:5]}-BB"[:20])
        _stock(db, product, own, on_hand=40)
        _incoming(
            db, product, own, spo_number="ZZT-SPO-HALF2", allocated=40, received=0,
            arrives=WHEN - timedelta(days=3),
        )
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        core = _line(db, order, product, qty="80", required_date=WHEN, warehouse=own)

        pso_id = _adopt(db, str(order.id))
        mirror = (
            db.query(ProjectSalesOrderLine)
            .filter(
                ProjectSalesOrderLine.project_sales_order_id == pso_id,
                ProjectSalesOrderLine.core_sales_order_line_id == core.id,
            )
            .first()
        )
        actor = _user(db, f"{MARKER} planner")
        _confirm(
            db, pso_id, actor,
            [
                {
                    "project_line_id": str(mirror.id),
                    "reserve": [{"warehouse_id": str(own.id), "qty": "40"}],
                    "timely_spo_qty": "40",
                }
            ],
        )

        decision = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == pso_id)
            .order_by(SOSupplyDecision.revision_no.desc())
            .first()
        )
        snapshot = next(
            snap for snap in decision.line_snapshots
            if snap.get("core_line_id") == str(core.id)
        )
        assert snapshot["reserve_qty"] == "40"
        assert snapshot["timely_spo_qty"] == "40"

        # And the board reads that water back UNDER QUESTION 1, per document location, so
        # the decided side of the strip files it exactly where the suggested side does.
        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
        covered = _cell(board, product.product_code, BUCKET)["contributions"][0]
        assert covered["covered"] is True
        assert covered["decision"]["timely_spo_qty"] == "40"
        assert covered["decision"]["incoming"] == [
            {
                "warehouse_id": str(own.id),
                "location": own.warehouse_code,
                "qty": "40",
                "rung": "group_take",
            }
        ]
        water = next(s for s in covered["sources"] if s["kind"] == "timely_spo")
        assert (water["rung"], water["location"]) == ("group_take", own.warehouse_code), (
            "a rebuilt frozen composition keeps the question that drew it"
        )


@pytest.mark.usefixtures("ladder_steps_on")
def test_the_water_is_drawn_at_the_location_it_is_coming_to_not_at_the_lines_own():
    """Review finding S2. The group's SPO used to be summed across every `*-<group>` site
    and booked wholesale at `fact.own_code`, so a cell read "40 from BRW-BB" beside a BRW-BB
    holding 20 - a number the location table directly contradicts.

    It is distributed now, per location, in the same order the floor is drawn: floor first
    (stock a picker can walk to), then the water at the site it is actually coming to.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        stem = _uid()[:4]
        own = _warehouse(db, f"ZZTA{stem}-BB"[:20])
        sibling = _warehouse(db, f"ZZTB{stem}-BB"[:20])
        _stock(db, product, own, on_hand=20)
        _stock(db, product, sibling, on_hand=0)
        arrives = WHEN - timedelta(days=1)
        _incoming(
            db, product, sibling, spo_number="ZZT-SPO-SIB", allocated=20, received=0,
            arrives=arrives,
        )
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="40", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)

        assert [
            (s["kind"], s["qty"], s["location"]) for s in contribution["sources"]
        ] == [
            ("reserve", "20", own.warehouse_code),
            ("timely_spo", "20", sibling.warehouse_code),
        ]
        own_step = _step(contribution, "own")
        assert own_step["answer"] == "yes"
        assert own_step["took"] == "40"
        assert own_step["from"] == f"{own.warehouse_code}, {sibling.warehouse_code}"
        assert (
            f"20 from {own.warehouse_code}, 20 on the water to {sibling.warehouse_code}"
            in own_step["why"]
        ), own_step["why"]
        water_source = contribution["sources"][1]
        assert f"arriving {arrives.day} " in water_source["reason"], water_source["reason"]
