"""Incoming means SPO (31 Aug ruling, R-A).

`documentation/plans/scm/PLAN-scm-planning-feedback-31aug.md` S1,
`scm-planning-feedback-31aug-acceptance-criteria.md` AC-1.1..1.4. Written BEFORE the fix, as
PRINCIPLES step 4 requires.

The captain's own production row named `202607-S0067`, a real `purchase_orders` line, as
what "Borrow incoming" told a planner to take. A PO is still ON ORDER - it has a computed
date, not a delivery date - so step 3 must never offer one, whatever `supply_borrow_reason`
used to say about it. A unit only a PO could cover falls through to the pool step and then
to Buy: not a regression, the point.

The assignment itself (`supply_assignment.assign`, `StockDebtService.assignments_for`) is
untouched - a PO still nets the SPO cut from it, the stock table's "PO qty" column is the
same read, and Stock Debt is unaffected. Only step 3's OFFER changes.

Postgres via `blank_session`, every chain seeded here (CI's database has no data).
"""
from __future__ import annotations

import pytest

import json
from datetime import date, timedelta
from decimal import Decimal

from app.services.project_supply_service import ProjectSupplyService
from app.services.scm.front_planning_engine import date_text, month_text

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
from .test_ladder_v7_borrow import LEAD_DAYS, _options, _policy
from .test_ladder_v7_supply_borrow import (  # noqa: F401  (helpers, not fixtures)
    ASKER_DAY,
    DONOR_DAY,
    _donor_holding,
    _po,
    _rung,
    _spo,
)
from .test_project_supply_service_ladder import (
    _agent,
    _components,
    _group_sites,
    _lead_time,
    _seed_line,
    _world,
)


# --------------------------------------------------------------------------- AC-1.1


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_po_only_book_offers_no_supply_borrow_and_falls_through_to_buy():
    """AC-1.1: a unit whose only whole-covering document is a purchase-order line receives
    NO supply-borrow offer at all, and the walk falls through - no on-hand anywhere, no
    pool - to Buy."""
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        issue = date.today() + timedelta(days=ASKER_DAY - 5 - LEAD_DAYS)
        po, po_lines = _po(
            db, product, donor_bin, qty=20, issue_date=issue, lead_days=LEAD_DAYS,
            po_number="ZZT-202607-S0067",
        )
        _donor_holding(
            db, company_id, project, product, donor_bin, qty=20, days=DONOR_DAY,
            actor=eling, po_line=po_lines[0],
            so_number=f"ZZTSO-DONOR{_uid()[:4]}",
        )

        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="20",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )

        service = ProjectSupplyService(db)
        facts = service._facts_for(order, service.lines_of(str(order.id)))
        fact = facts[str(line.id)]
        candidates = service.supply_borrow_candidates_for(fact)

        proposal = service.proposal_for(order)
        components = _components(proposal)
        options = _options(proposal)

    assert candidates == [], "no PO document is ever a step-3 candidate"
    assert _rung(components, "supply_borrow") == []
    assert [(c["kind"], c["qty"]) for c in components] == [("buy", "20")], (
        "the PO cannot cover it, so the unit falls through to Buy"
    )
    step = next(option for option in options if option["step"] == "supply_borrow")
    assert step["whole"] is False
    assert step["fulfil_date"] is None and step["days_late"] is None


def test_a_po_only_book_with_a_pool_take_lands_on_pool_not_supply_borrow():
    """AC-1.1's other half: with a pool free to cover it, the unit lands on the pool step,
    not on the PO - "falls through to the pool step then Buy" from the plan, verified with
    an actual pool that catches it."""
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        # 40, not 20: ladder v8 lets a project line have HALF the pool (R-B), and this
        # case is about the unit landing on the pool step rather than on the PO.
        _stock(db, product, pool, on_hand=40)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        issue = date.today() + timedelta(days=ASKER_DAY - 5 - LEAD_DAYS)
        _po_doc, po_lines = _po(
            db, product, donor_bin, qty=20, issue_date=issue, lead_days=LEAD_DAYS,
        )
        _donor_holding(
            db, company_id, project, product, donor_bin, qty=20, days=DONOR_DAY,
            actor=eling, po_line=po_lines[0],
            so_number=f"ZZTSO-DONOR{_uid()[:4]}",
        )

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="20",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert _rung(components, "supply_borrow") == []
    assert [(c["kind"], c["qty"], c["rung"]) for c in components] == [
        ("reserve", "20", "pool")
    ]


# --------------------------------------------------------------------------- AC-1.2


@pytest.mark.usefixtures("ladder_steps_on")
def test_an_spo_covered_book_is_unchanged_by_the_po_edit():
    """AC-1.2: a unit covered by a single SPO behaves exactly as before this change - same
    offer, same sentence, same fulfil date. Pinned against the same numbers
    `test_an_spo_a_later_order_is_waiting_on_is_borrowed_whole_and_names_its_debt` already
    pins, so the two suites cannot silently drift apart."""
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=ASKER_DAY - 5)
        allocation = _spo(db, product, donor_bin, qty=50, arrives=arrives)
        agent = _agent(db, f"ZZTJER{_uid()[:4]}")
        donor_so, _donor_core, _donor_mirror, _link = _donor_holding(
            db, company_id, project, product, donor_bin, qty=50, days=DONOR_DAY,
            actor=eling, allocation=allocation,
            so_number=f"ZZTSO-DONOR{_uid()[:4]}", agent_id=agent.id,
        )

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)
        options = _options(proposal)
        donor_due = date.today() + timedelta(days=DONOR_DAY)
        spo_number = allocation.spo_number
        donor_number = donor_so.so_number
        agent_code = agent.sales_agent

    borrowed = _rung(components, "supply_borrow")
    assert [(c["kind"], c["qty"], c["source_location"]) for c in borrowed] == [
        ("borrow", "50", donor_bin.warehouse_code)
    ]
    assert borrowed[0]["supply_key"] == f"spo:{allocation.id}"
    assert borrowed[0]["supply_document"] == f"SPO {spo_number}"
    assert borrowed[0]["arrival_date"] == arrives
    assert borrowed[0]["donor_so_number"] == donor_number
    assert borrowed[0]["order_back_qty"] == "50"
    assert borrowed[0]["reason"] == (
        f"Borrow 50 arriving {date_text(arrives)} (SPO {spo_number}) from "
        f"{donor_number} line 1 ({agent_code}, due {date_text(donor_due)}); "
        f"its debt lands in {month_text(donor_due)}"
    )
    chosen = [option for option in options if option["chosen"]]
    assert [option["step"] for option in chosen] == ["supply_borrow"]
    assert chosen[0]["fulfil_date"] == arrives.isoformat()
    assert chosen[0]["days_late"] == 0


# --------------------------------------------------------------------------- AC-1.3


def test_no_rendered_sentence_names_the_eligible_pos_document():
    """AC-1.3: with an eligible PO in the book, no option, no suggestion and no composition
    sentence the engine produces contains that PO's number - grep-level, over the whole
    proposal payload."""
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        issue = date.today() + timedelta(days=ASKER_DAY - 5 - LEAD_DAYS)
        po, po_lines = _po(
            db, product, donor_bin, qty=20, issue_date=issue, lead_days=LEAD_DAYS,
            po_number=f"ZZT-PO-EL{_uid()[:6]}",
        )
        _donor_holding(
            db, company_id, project, product, donor_bin, qty=20, days=DONOR_DAY,
            actor=eling, po_line=po_lines[0],
            so_number=f"ZZTSO-DONOR{_uid()[:4]}",
        )

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="20",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        po_number = po.po_number

    payload = json.dumps(proposal, default=str)
    assert po_number not in payload, (
        "the eligible PO's own number must not appear anywhere in the proposal",
        po_number,
    )


# --------------------------------------------------------------------------- AC-1.4


def test_the_assignment_still_nets_purchase_orders_untouched():
    """AC-1.4: `assignments_for` (read through `planning_assignments`) still reads purchase
    orders - PO netting, the stock table's "PO qty" column and Stock Debt's own inputs are
    byte-identical for a book with no SPOs borrowed. Only step 3's OFFER changed; the
    assignment underneath it did not."""
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        issue = date.today() + timedelta(days=ASKER_DAY - 5 - LEAD_DAYS)
        po, po_lines = _po(
            db, product, own, qty=35, issue_date=issue, lead_days=LEAD_DAYS,
            po_number=f"ZZT-PO-NET{_uid()[:6]}",
        )

        service = ProjectSupplyService(db)
        result = service.planning_assignments([str(product.id)])[str(product.id)]
        po_line_id = po_lines[0].id
        po_number = po.po_number

    events = {str(event.key): event for event in result.supply}
    po_key = f"po:{po_line_id}"
    assert po_key in events, "the PO line is still a supply event in the assignment"
    po_event = events[po_key]
    assert po_event.kind == "po"
    assert po_number in (po_event.ref or "")
    assert result.free.get(po_key) == Decimal("35"), (
        "the PO's own netted balance is unchanged - nothing here borrowed it"
    )
