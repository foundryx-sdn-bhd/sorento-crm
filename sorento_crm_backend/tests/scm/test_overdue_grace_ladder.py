"""R-O: an overdue document counts as supply after a grace period, and says so.

`PLAN-scm-pool-chain-first.md` ruling R-O (issue #586); AC-O.1 to AC-O.4 in
`documentation/plans/scm/scm-pool-chain-first-acceptance-criteria.md`. Written before the
wiring, as PRINCIPLES step 4 requires.

The captain on SO419417, 3 September 2026: "Available for Project" at BRW read 355 off 725
SPO units dated 24 July and 6 August and still unreceived, while the ladder lent 4 units off
the 11 standing on the floor. The display was the honest one - R31 counted a late document
as nothing at all, so the walk ignored goods the whole business is still expecting.

Under R-O a document whose arrival has passed with nothing received counts as supply landing
on `today + overdue_grace_days`, and one later than `overdue_dead_days` counts as nothing,
which is R31 kept for the dead. The ASSUMED date is what the walk plans against, so it is
the component's `arrival_date` and the fulfil date follows it; the sentence names the
lateness, because a promise dated three weeks out that rests on paperwork two months
overdue is a promise the reader has to be able to see through.

SHIPPED policy is 0 / 0 (captain's ruling, 3 Sep 2026) - dead at 0 reproduces R31 exactly,
so production keeps today's behaviour until someone raises the two numbers. This suite
proves the RULE, so every fixture below activates a policy with the RECOMMENDED 14 / 90
explicitly (`GRACE_DAYS` / `DEAD_DAYS`) rather than relying on the shipped default -
`test_overdue_grace_setting.py` covers the 0 / 0 default itself.

The world is `_group_sites` + `_seed_line` - one fixture carrying two rungs (the own bin's
own water and a group sibling's), rather than four worlds for four rungs.

Postgres via `blank_session`, every chain seeded here (CI's database has no data).
"""
from __future__ import annotations

import pytest

from datetime import date, timedelta

from app.services.project_supply_service import ProjectSupplyService
from app.services.scm.front_planning_engine import date_text

from .._pg_fixture import blank_session
from ..test_so_supply_confirmation import (  # noqa: F401  (helpers, not fixtures)
    _stock,
    _uid,
    _warehouse,
)
from .test_ladder_v7_borrow import _policy
from .test_project_supply_service_ladder import (
    _components,
    _group_sites,
    _seed_line,
    _spo_line,
    _world,
)

#: The policy defaults R-O ships with, restated so the arithmetic below is readable.
GRACE_DAYS = 14
DEAD_DAYS = 90

#: How late the captain's own document was on the day he read it (24 July against
#: 3 September). Alive, and comfortably so.
LATE_DAYS = 41
#: Later than the dead line, so R31 still governs it.
DEAD_LATE_DAYS = 125

TODAY = date.today()
STATED = TODAY - timedelta(days=LATE_DAYS)
ASSUMED = TODAY + timedelta(days=GRACE_DAYS)


def _late_sentence(spo_number: str) -> str:
    # `spo_number` (`_spo_line`'s own fixture) already carries the book's own "SPO-"
    # prefix, and the sentence must not double it (captain, 3 Sep 2026, SO418869
    # SRTWCX7405-RL-S-PJ) - `stock_debt_service._spo_ref` carries the same guard.
    return f"{spo_number} is {LATE_DAYS} days late, assumed by {date_text(ASSUMED)}"


# --------------------------------------------------------------------------- AC-O.1


@pytest.mark.usefixtures("ladder_steps_on")
def test_an_alive_late_document_covers_the_line_at_its_assumed_date():
    """AC-O.1. 50 due 17 days out at BRW-BB with nothing on the floor, against an SPO of
    100 into that same bin whose arrival passed 41 days ago.

    The document lands on the assumed date - `today + 14` - which is on or before the
    line's own, so question 1's WATER half answers it whole. The component is dated by the
    assumed arrival, because that is the date a planner promises against, and its sentence
    names the lateness rather than stating an arrival the paperwork does not support.
    """
    due = TODAY + timedelta(days=GRACE_DAYS + 3)
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _policy(db, overdue_grace_days=GRACE_DAYS, overdue_dead_days=DEAD_DAYS)
        spo_number = _spo_line(db, product, own, qty=100, arrives=STATED).spo_number
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=due,
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [(c["kind"], c["qty"], c["source_location"]) for c in components] == [
        ("timely_spo", "50", own.warehouse_code)
    ], "a late-but-alive document is supply again (R-O supersedes R31)"
    assert components[0]["arrival_date"] == ASSUMED, (
        "the ASSUMED date, so every fulfil date downstream follows it"
    )
    assert _late_sentence(spo_number) in components[0]["reason"], components[0]["reason"]


@pytest.mark.usefixtures("ladder_steps_on")
def test_the_same_document_at_a_group_sibling_reads_the_same_sentence():
    """The second rung this one fixture carries: the late document sits at MWH-BB, a
    sibling of the asking bin's own ownership group, so question 1's water half answers
    from there instead - same builder, same words, a different location."""
    due = TODAY + timedelta(days=GRACE_DAYS + 3)
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        sibling, _sibling_pool = sites["MWH"]
        _policy(db, overdue_grace_days=GRACE_DAYS, overdue_dead_days=DEAD_DAYS)
        spo_number = _spo_line(db, product, sibling, qty=100, arrives=STATED).spo_number
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=due,
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [(c["kind"], c["qty"], c["source_location"]) for c in components] == [
        ("timely_spo", "50", sibling.warehouse_code)
    ]
    assert _late_sentence(spo_number) in components[0]["reason"], components[0]["reason"]


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_bucket_of_two_late_documents_still_states_the_lateness():
    """The captain's own cell, 3 September 2026: BRW-BB on SO419417 carried 725 units
    across TWO late SPOs (24 Jul and 6 Aug) and `group_water_reason` printed ", arriving
    17 Sep 2026" with no lateness clause at all, because the code that carried `late_days`
    only fired when the bucket resolved to exactly one document (`single`).

    Two late documents sharing one bucket are each late on their own, so the sentence must
    still carry "days late, assumed by" even though neither is named - the same clause
    `late_document_reason(None, days, arrival)` already produces for that case.
    """
    due = TODAY + timedelta(days=GRACE_DAYS + 3)
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _policy(db, overdue_grace_days=GRACE_DAYS, overdue_dead_days=DEAD_DAYS)
        # Neither document alone covers the line, so the walk draws from BOTH and the
        # bucket resolves to two documents rather than `single`.
        _spo_line(db, product, own, qty=30, arrives=STATED)
        _spo_line(db, product, own, qty=40, arrives=STATED - timedelta(days=5))
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=due,
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [(c["kind"], c["qty"], c["source_location"]) for c in components] == [
        ("timely_spo", "50", own.warehouse_code)
    ]
    reason = components[0]["reason"]
    # The MORE overdue of the two (46 days, from the 6 Aug document) - the bucket's
    # lateness cannot be less than its worst document's.
    assert reason.endswith(
        f". the document is 46 days late, assumed by {date_text(ASSUMED)}"
    ), reason


# --------------------------------------------------------------------------- AC-O.2


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_line_due_inside_the_grace_is_offered_nothing_by_the_late_document():
    """AC-O.2. The same document against a line due in 7 days: the goods are not assumed
    to be there before day 14, so QUESTION 1 is offered none of them and the walk carries
    on down the ladder.

    What it finds there is step 3, which may take a document that lands after the asker's
    own date as long as it still beats buying (R32) - and this one does, by a fortnight.
    So the answer is a `Take`, dated by the assumed arrival and stating the lateness in the
    same words question 1 would have: the UAC's "the walk continues (borrow / buy)".
    """
    due = TODAY + timedelta(days=7)
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _policy(db, overdue_grace_days=GRACE_DAYS, overdue_dead_days=DEAD_DAYS)
        spo_number = _spo_line(db, product, own, qty=100, arrives=STATED).spo_number
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=due,
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert not [c for c in components if c["kind"] == "timely_spo"], (
        "question 1 offers nothing: the goods are not assumed to be there by the 7th"
    )
    assert [(c["kind"], c["rung"]) for c in components] == [
        ("borrow", "supply_borrow")
    ], "the walk continued, and step 3 took the document whole (R32/R33)"
    assert components[0]["arrival_date"] == ASSUMED
    assert _late_sentence(spo_number) in components[0]["reason"], components[0]["reason"]


# --------------------------------------------------------------------------- AC-O.3


def test_a_dead_document_is_not_supply_at_all():
    """AC-O.3. 125 days late against a 90-day dead line: R31 stands, the line buys, and
    nothing on the composition mentions a document nobody can plan against."""
    due = TODAY + timedelta(days=GRACE_DAYS + 3)
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _policy(db, overdue_grace_days=GRACE_DAYS, overdue_dead_days=DEAD_DAYS)
        _spo_line(
            db, product, own, qty=100,
            arrives=TODAY - timedelta(days=DEAD_LATE_DAYS),
        )
        db.commit()

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=due,
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [(c["kind"], c["qty"]) for c in components] == [("buy", "50")]
    assert "late" not in components[0]["reason"]


# --------------------------------------------------------------------------- the sentences


def test_every_incoming_rung_states_the_lateness_in_the_same_words():
    """One clause, four rungs (R-O). The three builders behind every incoming component -
    the own group's water, another group's water, and step 3's document borrow - all read
    the lateness out of `late_document_reason`, so a planner meets one sentence wherever
    the late document turns up. The pool rung is not here because it composes only its
    FREE FLOOR, which is stock on a shelf and never a document.
    """
    from app.services.scm.front_planning_engine import (
        group_water_reason,
        late_document_reason,
        other_group_reason,
        supply_borrow_reason,
    )
    from decimal import Decimal

    clause = late_document_reason("SPO 2026/07-0031", 41, ASSUMED)
    assert clause == f"SPO 2026/07-0031 is 41 days late, assumed by {date_text(ASSUMED)}"

    own = group_water_reason(
        "BRW-BB", Decimal("50"), "BB", Decimal("100"), ASSUMED,
        "SPO 2026/07-0031", late_days=41,
    )
    assert own.endswith(f". {clause}")
    assert "arriving" not in own, "the stated arrival is not restated beside the assumed one"

    other = other_group_reason(
        "BRW-IB", Decimal("529"), "BB", ASSUMED, "SPO 2026/07-0031",
        lending_group="IB", late_days=41,
    )
    assert other.endswith(f". {clause}")

    borrowed = supply_borrow_reason(
        Decimal("50"), kind="spo", document="SPO 2026/07-0031", arrival_date=ASSUMED,
        donor_so_number="SO414285", donor_line_no=4, donor_agent_code="JEREMY",
        donor_required_date=TODAY + timedelta(days=60), late_days=41,
    )
    assert f"({clause})" in borrowed
    assert borrowed.startswith("Borrow 50 (")


def test_one_day_late_is_singular():
    from app.services.scm.front_planning_engine import late_document_reason

    assert late_document_reason("SPO X", 1, ASSUMED).startswith("SPO X is 1 day late")
