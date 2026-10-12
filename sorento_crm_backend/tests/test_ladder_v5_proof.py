"""The five-row proof, and the table it is checked against (ladder v5, section 1e).

`tests/scm/test_ladder_v5.py` pins what the ENGINE composes. This file pins what a planner
READS: the four questions plus Buy, each answered Yes or No with the deciding figure inside
the sentence, and the cell's location table beneath them.

One test per criterion, each with the captain's own case where he gave one:

* **AC-V1** five rows, in order, every one answered, and none of them named Incoming;
* **AC-V3** a cited other-group site brings its WHOLE group, each row with its own signed
  available and the subtotal carrying `donor_group_net`;
* **AC-V4** question 3 offers the donor now that the cross-group cap is gone (v7.1, R5),
  and the suggestion note never claims borrowing is possible where the proof says nothing
  is left;
* **AC-V5** question 4 is never proposed and names the donors it did not take from;
* **AC-V6** dealer hot-selling refuses the whole pile, DC1 and MWH included;
* **AC-V7** the pool is asked before another location: 24 needed, 268 free in the pile, 100
  at another group within the cap - the answer is Pool 24;
* **AC-V8** a line with no decision shows the LIVE suggestion, stamped with today's ladder.

Postgres via `blank_session`, every FK target seeded here. Helpers come from the board's
own suite so the two files cannot come to disagree about what a board looks like.
"""
from __future__ import annotations

import pytest

from datetime import date, timedelta
from decimal import Decimal

from app.services.project_supply_service import LADDER_VERSION
from app.services.scm import priority

from ._pg_fixture import blank_session
from .test_fulfilment_board import (  # noqa: F401  (helpers, not fixtures)
    MARKER,
    TODAY,
    _cell,
    _incoming,
    _line,
    _order,
    _pool,
    _product,
    _service,
    _step,
    _stock,
    _uid,
    _warehouse,
)

WHEN = date(2026, 9, 3)
BUCKET = "2026-08-31"


def _policy(db, *, overdue_grace_days=None, overdue_dead_days=None):
    """An active fulfilment-priority policy, so the ladder ranks against a real row.

    Was `_cap`: the cross-group borrow limit it used to set is gone with v7.1 (R5), and any
    ownership group may donate now.

    `overdue_grace_days` / `overdue_dead_days` default to None, which is `create_revision`'s
    own "unset" - the column's SHIPPED default (0 / 0, captain's ruling 3 Sep 2026) - so a
    caller proving R-O's grace RULE (rather than its shipped default) passes both explicitly.
    """
    priority.create_revision(
        db, name=f"{MARKER}-v5-{_uid()[:6]}", factors={}, demand_class_weights={},
        reorder_coverage_until=None,
        overdue_grace_days=overdue_grace_days,
        overdue_dead_days=overdue_dead_days,
    )
    db.commit()


def _contribution(db, order, product):
    board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
    return _cell(board, product.product_code, BUCKET)["contributions"][0]


def _locations(db, order, product):
    board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)
    return _cell(board, product.product_code, BUCKET)["locations"]


# --------------------------------------------------------------------------- AC-V1


@pytest.mark.usefixtures("ladder_steps_on")
def test_the_proof_is_five_rows_the_four_questions_and_buy():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTA{_uid()[:5]}-BB"[:20])
        _stock(db, product, own, on_hand=0)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)

        # LADDER V8 (R-A, review round 1 S5): the proof asks its questions in the WALK's
        # own order, and the walk asks the site pool first.
        assert [step["question"] for step in contribution["trail"]] == [
            "Can we take from the pool?",
            "Can we use our locations?",
            "Can we borrow on hand from a later order?",
            "Can we borrow incoming from a later order?",
            "Buy",
        ]
        # The rung names stay INTERNAL keys. Nothing renders them, and nothing here reads
        # "Incoming" - an SPO is inside question 1's own net (AC-V2).
        assert [step["kind"] for step in contribution["trail"]] == [
            "pool", "own", "order_borrow", "supply_borrow", "buy",
        ]
        for step in contribution["trail"]:
            assert step["answer"] in ("yes", "no")
            assert step["why"]
            assert step["took"] is not None


def test_a_yes_row_says_what_it_took_and_where_from():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTB{_uid()[:5]}-BB"[:20])
        _stock(db, product, own, on_hand=50)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=WHEN, warehouse=own)

        step = _step(_contribution(db, order, product), "own")

        assert step["answer"] == "yes"
        assert step["took"] == "10"
        assert step["from"] == own.warehouse_code
        assert "BB group nets" in step["why"]


# --------------------------------------------------------------------------- AC-V7


def test_the_pool_is_asked_before_another_location_and_answers_the_whole_line():
    """The captain's own numbers on SO381895: 24 needed, the pile free 268, another
    ownership group holding 100 within the cap. Pool 24, never Borrow 24."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        pool = _pool(db, f"ZZTC{_uid()[:5]}"[:20])
        own = _warehouse(db, f"ZZTC{_uid()[:5]}-BB"[:20])
        own.pool_warehouse_id = pool.id
        db.flush()
        donor = _warehouse(db, f"ZZTC{_uid()[:5]}-NTC"[:20])
        _stock(db, product, own, on_hand=0)
        _stock(db, product, pool, on_hand=268)
        _stock(db, product, donor, on_hand=100)
        _policy(db)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="24", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)

        # LADDER V8 (R-A) puts the pool back in FRONT, where v5 had it and v7.1 had moved
        # it from: the asking bin's own pool may spare 134 of its 268 and 24 fits inside
        # that, so the pool answers and the 100 at the `-NTC` site is never reached.
        assert [(s["kind"], s["qty"], s["location"]) for s in contribution["sources"]] == [
            ("reserve", "24", pool.warehouse_code),
        ]
        assert _step(contribution, "pool")["answer"] == "yes"
        assert _step(contribution, "pool")["took"] == "24"
        assert _step(contribution, "own")["answer"] == "no"
        assert _step(contribution, "buy")["answer"] == "no"


# --------------------------------------------------------------------------- AC-V6


def test_another_sites_pool_answers_a_line_whose_own_pool_is_empty():
    """AC-V6, RE-BLESSED TWICE BY LADDER V8. R-A retired the dealer hot-selling gate this
    case was written for - the SHARE keeps stock for dealers now - and R-L then answers the
    question the case asks: the asking bin's own pool holds nothing, so the OTHER site
    pool is asked for the remainder, under its own allowance, and its 500 covers a line of
    10 whole.

    The pool step therefore says YES here, where every earlier ladder said no.
    """
    from app.models.scm import ItemClassification

    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own_pool = _pool(db, f"ZZTD{_uid()[:5]}"[:20])
        other_pool = _pool(db, f"ZZTE{_uid()[:5]}"[:20])
        own = _warehouse(db, f"ZZTD{_uid()[:5]}-BB"[:20])
        elsewhere = _warehouse(db, f"ZZTE{_uid()[:5]}-BB"[:20])
        own.pool_warehouse_id = own_pool.id
        elsewhere.pool_warehouse_id = other_pool.id
        db.flush()
        _stock(db, product, own, on_hand=0)
        _stock(db, product, own_pool, on_hand=0)
        _stock(db, product, other_pool, on_hand=500)
        db.add(
            ItemClassification(
                id=_uid(), product_id=product.id, warehouse_id=own.id, abc_class_retail="A"
            )
        )
        db.flush()
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)

        pool = _step(contribution, "pool")
        assert pool["answer"] == "yes"
        assert pool["took"] == "10"
        assert _step(contribution, "buy")["took"] == "0", (
            "500 sits in the pile at the other site and R-L asks it for the remainder"
        )


# --------------------------------------------------------------------------- AC-V5


def test_question_four_is_never_proposed_and_names_the_donors_it_did_not_take():
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTF{_uid()[:5]}-BB"[:20])
        _stock(db, product, own, on_hand=40)
        # Another order holding stock at the same group location, ranked below ours.
        theirs = _order(db, so_number="ZZT-SO-DONOR", order_date=date(2026, 1, 1))
        _line(db, theirs, product, qty="30", required_date=date(2027, 6, 1), warehouse=own)
        ours = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, ours, product, qty="60", required_date=WHEN, warehouse=own)

        board = _service(db).build(
            [ours.so_number, theirs.so_number], granularity="week", as_of=TODAY
        )
        contribution = next(
            c for c in _cell(board, product.product_code, BUCKET)["contributions"]
            if c["so_number"] == ours.so_number
        )

        # LADDER V7.1 (R1): borrowing a later order's on hand is a STEP now. Nothing is on
        # a floor in this fixture, so it answers No - and it names the window date, which is
        # the fact that tells a planner which orders would have qualified (AC-S3-4).
        step = _step(contribution, "order_borrow")
        assert step["answer"] == "no"
        assert step["took"] == "0"
        assert "no later order" in step["why"].lower(), step["why"]


# --------------------------------------------------------------------------- AC-V4


def test_question_three_offers_the_donor_now_that_the_cap_is_gone():
    """v7.1 R5: any ownership group may donate, and its FREE stock is step 1's second half.

    The captain's own case, inverted: 60 needed, 500 at a `-NTC` site whose group owes
    nothing. Under v5 the small-quantity limit refused it and a Borrow was the only shape it
    could have had; under v7.1 free stock is owed to nobody, so it is a Reserve at question
    1 and it raises no order-back."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTG{_uid()[:5]}-BB"[:20])
        donor = _warehouse(db, f"ZZTG{_uid()[:5]}-NTC"[:20])
        _stock(db, product, own, on_hand=0)
        _stock(db, product, donor, on_hand=500)
        _policy(db)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="60", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)

        step = _step(contribution, "own")
        assert step["answer"] == "yes"
        assert step["took"] == "60"
        assert donor.warehouse_code in step["why"]
        # And no sentence claims a limit that no longer exists.
        assert "cross-group borrow limit" not in step["why"]
        assert [s["kind"] for s in contribution["sources"]] == ["reserve"]


def test_the_suggestion_note_is_silent_about_a_donor_whose_own_group_nets_nothing():
    """AC-V4's second half, WHOLE - one source of truth: a donor the proof refused is not a
    donor a person may pick, so the Buy's own sentence must not name it while question 3 in
    the row directly above says nothing outside the group may be drawn.

    It used to share this job with a sibling that made the CAP the refusal. The cap is gone
    (v7.1, R5, migration 443), so the only refusal left at this rung is the one this test
    already pinned, and the sibling retired into it rather than becoming a copy of it.

    The case the cap filter never caught (review blocker B1): the donor site holds 500, so a
    filter that only looked at the cap kept naming it. What refuses it is ladder v4's own
    rule - the NTC group as a whole nets -100, so nothing of it may be lent, and question 3
    says exactly that one row above. The note is written from what questions 3 and 4
    offered, and they offered nothing.
    """
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        stem = _uid()[:5]
        own = _warehouse(db, f"ZZTM{stem}-BB"[:20])
        donor = _warehouse(db, f"ZZTN{stem}-NTC"[:20])
        elsewhere = _warehouse(db, f"ZZTO{stem}-NTC"[:20])
        _stock(db, product, own, on_hand=0)
        _stock(db, product, donor, on_hand=500)
        _stock(db, product, elsewhere, on_hand=0)
        # The NTC group's own book: 500 on the floor at one site against 600 owed at
        # another, DUE EARLIER than this line. Under v7.1 the free pile is served
        # first-come by required date (R24), so the NTC group's own order takes the whole
        # 500 before this line's date is reached and there is nothing left to lend - which
        # is the same refusal ladder v4's group net used to make, made by the date instead.
        theirs = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(
            db, theirs, product, qty="600",
            required_date=WHEN - timedelta(days=10), warehouse=elsewhere,
        )
        _policy(db)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="60", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)

        step = _step(contribution, "own")
        assert step["answer"] == "no"
        assert "nothing" in step["why"].lower(), step["why"]
        # The donor is still SEEN - it is on the row's own candidate list, and the cap is
        # nowhere near binding on it - and that is exactly why the note used to name it.
        assert donor.warehouse_code in [
            c["warehouse_code"] for c in contribution["borrow_candidates"]
        ]
        buy = next(s for s in contribution["sources"] if s["kind"] == "buy")
        assert "Borrowing is possible" not in buy["reason"], buy["reason"]


# --------------------------------------------------------------------------- AC-V3


def test_a_cited_other_group_site_brings_its_whole_group_with_its_own_net():
    """The captain, on the popover table: the donor's offer is its GROUP's net, so listing
    only the site the ladder drew from shows a number the subtotal cannot be checked
    against. Every `*-NTC` sibling is listed, each with its own signed available."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTI{_uid()[:5]}-BB"[:20])
        stem = _uid()[:5]
        drawn = _warehouse(db, f"ZZTJ{stem}-NTC"[:20])
        sibling = _warehouse(db, f"ZZTK{stem}-NTC"[:20])
        _stock(db, product, own, on_hand=0)
        _stock(db, product, drawn, on_hand=40)
        _stock(db, product, sibling, on_hand=7)
        _policy(db)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=WHEN, warehouse=own)

        locations = _locations(db, order, product)

        other = {
            entry["location"]: entry
            for entry in locations
            if entry["where"] == "other_group"
        }
        assert drawn.warehouse_code in other, "the site the ladder drew from"
        assert sibling.warehouse_code in other, (
            "and the sibling it never named, because the offer was the GROUP's"
        )
        # Its OWN signed available per row, and the group's net as the subtotal.
        assert other[sibling.warehouse_code]["available_qty"] == "7"
        assert other[drawn.warehouse_code]["available_qty"] == "40"
        assert {entry["net"] for entry in other.values()} == {"47"}
        assert {entry["net_of"] for entry in other.values()} == {"NTC"}


# --------------------------------------------------------------------------- AC-V8


def test_an_undecided_line_shows_the_live_suggestion_stamped_with_todays_ladder():
    """A frozen `proposed_components` is read only beside a DECIDED line. An undecided one
    shows what the engine says now, and every component of it carries the ladder that wrote
    it - so the screen can tell history from today's answer without guessing."""
    with blank_session() as db:
        product = _product(db, f"ZZT-{_uid()[:6]}")
        own = _warehouse(db, f"ZZTL{_uid()[:5]}-BB"[:20])
        _stock(db, product, own, on_hand=50)
        order = _order(db, so_number=f"ZZT-SO-{_uid()[:8]}", order_date=date(2026, 1, 1))
        _line(db, order, product, qty="10", required_date=WHEN, warehouse=own)

        contribution = _contribution(db, order, product)

        assert contribution["covered"] is False
        assert contribution["proposed"]["components"] == contribution["sources"]
        assert {c["ladder"] for c in contribution["proposed"]["components"]} == {
            LADDER_VERSION
        }
