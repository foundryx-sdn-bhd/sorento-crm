"""S4: a PO event may never supply ANY walk offer (captain ruling, 1 Sep 2026).

`documentation/plans/scm/PLAN-scm-planning-feedback-31aug.md` S4,
`scm-planning-feedback-31aug-acceptance-criteria.md` AC-4.1..AC-4.5. Written BEFORE the fix,
as PRINCIPLES step 4 requires.

S1 (`test_ladder_v7_incoming_spo_only.py`) cured step 3 (`supply_borrow`) only. The captain's
own live repro on 1 Sep: a suggestion read "Use incoming 15 from BRW-BB, arriving 6 Sep
2026" with no incoming row anywhere to click - BRW-BB (another project group) had SPO qty 0
and PO qty 978, so the 15 and its 6 Sep arrival came from a PO event flowing through
`ProjectSupplyService._other_group_free_at_own_date` (step 1's OFFER half). The ruling:
**incoming is strictly SPO; a PO event may not supply any walk offer** - own group's own
draw included, since `_drawn_at_own_date` reads the ASSIGNMENT's actual draw for the unit,
and the assignment nets every kind together. A unit only a PO could cover now falls through
to the pool step and then to Buy - not a regression, the point (R-A, 31 Aug; reaffirmed 1
Sep).

Task 2 (option label follows composition) and task 3 (use-incoming names and links its SPO)
are pinned here too: they are the two places the fix is visible on screen, and the captain's
own screenshot was the label disagreeing with the card for the exact case AC-4.1 now refuses
to offer at all.

The assignment itself (`supply_assignment.assign`, `StockDebtService.assignments_for`) is
untouched - PO netting, the stock table's "PO qty" column and Stock Debt are unaffected.
Only the WALK's own candidate lists changed.

Postgres via `blank_session`, every chain seeded here (CI's database has no data).
"""
from __future__ import annotations

import pytest

import json
from datetime import date, timedelta

from app.services.project_supply_service import ProjectSupplyService
from app.services.scm.sales_agent_service import group_of_warehouse_code

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
from .test_ladder_v7_supply_borrow import ASKER_DAY, _po  # noqa: F401  (helper, not a fixture)
from .test_project_supply_service_ladder import (
    _components,
    _group_sites,
    _lead_time,
    _seed_line,
    _world,
)


# --------------------------------------------------------------------------- AC-4.1


def test_a_po_backed_other_groups_free_pile_gives_no_offer_and_falls_through_to_buy():
    """AC-4.1, the captain's own repro: another project group's free pile backed only by a
    PO must never be offered as "Use incoming" - the unit falls through to Buy, and the PO's
    own number appears nowhere in the rendered proposal."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        issue = date.today() + timedelta(days=ASKER_DAY - 5 - LEAD_DAYS)
        po, _po_lines = _po(
            db, product, donor_bin, qty=40, issue_date=issue, lead_days=LEAD_DAYS,
            po_number=f"ZZT-PO-CROSS{_uid()[:6]}",
        )

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)
        options = _options(proposal)
        po_number = po.po_number

    assert [(c["kind"], c["qty"]) for c in components] == [("buy", "40")], (
        "nothing at the other group's PO-backed bin may cover the line"
    )
    use = next(option for option in options if option["step"] == "use")
    assert use["whole"] is False
    assert use["fulfil_date"] is None and use["days_late"] is None
    payload = json.dumps(proposal, default=str)
    assert po_number not in payload, (
        "the PO's own number must not appear anywhere in the proposal",
        po_number,
    )


def test_a_po_backed_own_groups_own_draw_gives_no_offer_and_falls_through_to_buy():
    """AC-4.1's own-group half: the ASSIGNMENT can net a PO into this unit's own draw
    (`_drawn_at_own_date` reads `row.assigned`, which nets every kind the group's pile
    holds), and step 1's own half must refuse it exactly as the offer half does - not only
    when the PO sits at another group's bin."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        issue = date.today() + timedelta(days=ASKER_DAY - 5 - LEAD_DAYS)
        po, _po_lines = _po(
            db, product, own, qty=40, issue_date=issue, lead_days=LEAD_DAYS,
            po_number=f"ZZT-PO-OWN{_uid()[:6]}",
        )

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)
        po_number = po.po_number

    assert [(c["kind"], c["qty"]) for c in components] == [("buy", "40")], (
        "the own group's ONLY cover is a PO, so step 1's own half gives nothing"
    )
    payload = json.dumps(proposal, default=str)
    assert po_number not in payload


def test_a_po_backed_book_with_a_pool_take_lands_on_pool_not_use():
    """AC-4.1's other half, mirroring S1's own pool fixture: with a pool free to cover it,
    the unit lands on the pool step, not on the PO wherever it sits."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        # 80, not 40: ladder v8 lets a project line have HALF the pool (R-B), and this
        # case is about the unit landing on the pool step rather than on the PO.
        _stock(db, product, pool, on_hand=80)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        issue = date.today() + timedelta(days=ASKER_DAY - 5 - LEAD_DAYS)
        _po(
            db, product, donor_bin, qty=40, issue_date=issue, lead_days=LEAD_DAYS,
        )

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [(c["kind"], c["qty"], c["rung"]) for c in components] == [
        ("reserve", "40", "pool")
    ]


# --------------------------------------------------------------------------- AC-4.2


@pytest.mark.usefixtures("ladder_steps_on")
def test_an_spo_backed_other_groups_free_pile_is_unchanged_and_names_its_document():
    """AC-4.2: the SPO half of the offer is untouched by the PO exclusion. Task 3: the
    component now carries the document's own address, the way a step-3 component always
    has. Task 2: the option's own label follows the composition it actually drew - another
    group, on the water - so the row and the card can no longer disagree the way the
    captain's screenshot did."""
    from ..test_fulfilment_board import _incoming

    arrival = date.today() + timedelta(days=20)
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        allocation = _incoming(
            db, product, donor,
            spo_number=f"ZZT-SPO-{_uid()[:6]}", allocated=40, received=0, arrives=arrival,
        )
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)
        donor_group = group_of_warehouse_code(donor.warehouse_code)

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=40),
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)
        options = _options(proposal)
        spo_number = allocation.spo_number

    assert [(c["kind"], c["rung"]) for c in components] == [("timely_spo", "group_take")]
    assert components[0]["source_location"] == donor.warehouse_code
    assert components[0]["supply_document"] == f"SPO {spo_number}", components[0]
    assert components[0]["arrival_date"] == arrival
    assert f"SPO {spo_number}" in components[0]["reason"], components[0]["reason"]

    use = next(option for option in options if option["step"] == "use")
    assert use["chosen"] is True
    assert use["fulfil_date"] == arrival.isoformat()
    assert use["label"] == f"Use incoming from {donor_group} group", use["label"]


@pytest.mark.usefixtures("ladder_steps_on")
def test_an_spo_backed_own_group_water_is_unchanged_and_labelled_use_incoming():
    """AC-4.2's own-group half, and task 2's own-group water label: the option reads "Use
    incoming" (never the static "Use our locations") when the own group's cover is on the
    water, whatever the PO exclusion did elsewhere."""
    from ..test_fulfilment_board import _incoming

    arrival = date.today() + timedelta(days=10)
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _incoming(
            db, product, own,
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
    use = next(option for option in options if option["step"] == "use")
    assert use["chosen"] is True
    assert use["label"] == "Use incoming", use["label"]


def test_a_floor_take_keeps_the_static_use_our_locations_label():
    """AC-4.4's baseline: an ordinary floor Reserve keeps the unchanged static label - the
    dynamic label only ever REPLACES it, never applies where nothing changed."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _stock(db, product, own, on_hand=40)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
        )
        options = _options(ProjectSupplyService(db).proposal_for(order))

    use = next(option for option in options if option["step"] == "use")
    assert use["chosen"] is True
    assert use["label"] == "Use our locations"


# --------------------------------------------------------------------------- AC-4.3


def test_the_assignment_still_nets_a_po_at_another_groups_bin_untouched():
    """AC-4.3: `assignments_for` (read through `planning_assignments`) still reads a PO
    sitting at another project group's bin - the exclusion is in the WALK's own candidate
    lists, never in the assignment underneath them (mirrors S1's own AC-1.4 test)."""
    with blank_session() as db:
        _company_id, _eling, _project, product = _world(db)
        _group, sites = _group_sites(db)
        _own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        issue = date.today() + timedelta(days=ASKER_DAY - 5 - LEAD_DAYS)
        po, po_lines = _po(
            db, product, donor_bin, qty=40, issue_date=issue, lead_days=LEAD_DAYS,
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
    assert result.free.get(po_key) == 40.0, (
        "the PO's own netted balance is unchanged - nothing here borrowed it"
    )
