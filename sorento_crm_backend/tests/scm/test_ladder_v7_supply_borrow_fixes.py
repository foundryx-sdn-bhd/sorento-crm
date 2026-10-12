"""S4 fix pass: the ten defects `/code-review` confirmed on ladder v7.1 step 3, one test each.

`documentation/plans/scm/PLAN-scm-borrow-ladder-v7-stock-debt.md` 3.2 step 3 + 3.3, UAC S4.
Written BEFORE the fixes, as PRINCIPLES step 4 requires, and each one names the defect it
pins rather than the code that repairs it.

The thread running through most of them is the same: a step-3 component is a claim on a
DOCUMENT, and a document is claimed in three different ledgers at once - the walk's own
(`supply_left`), the placement links (`order_inquiry_links`) and the instruction rows
purchasing reads. A fix that moves one and not the others leaves the quantity either
promised twice or promised to nobody, and both look like a plain arithmetic bug on screen.

Postgres via `blank_session`, every chain seeded here (CI's database has no data).
"""
from __future__ import annotations

import pytest

from datetime import date, timedelta
from decimal import Decimal

from app.models.project_so import (
    INQUIRY_CANCELLED,
    INQUIRY_RAISED,
    IV_ORDER_BACK,
    SO_STATUS_PUBLISHED,
    OrderInquiryLink,
    OrderInquiryRow,
)
from app.models.scm import OrderLinkClaim
from app.schemas.project_supply import (
    ConfirmBorrowComponent,
    ConfirmLine,
    ConfirmSupplyBody,
)
from app.services.project_order_inquiry_service import ProjectOrderInquiryService
from app.services.project_supply_service import ProjectSupplyService

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
from .test_ladder_v7_borrow import LEAD_DAYS, WINDOW_DAY, _policy
from .test_ladder_v7_supply_borrow import (  # noqa: F401  (helpers, not fixtures)
    ASKER_DAY,
    DONOR_DAY,
    LATE_ARRIVAL_DAY,
    _confirm_as_proposed,
    _donor_holding,
    _rung,
    _spo,
)
from .test_project_supply_service_ladder import (
    _components,
    _group_sites,
    _lead_time,
    _seed_line,
    _world,
)


def _borrow_line(line_id, warehouse, allocation, *, qty, arrives, donor_core_line_id=None):
    """One step-3 component posted the way an approved board line posts it."""
    return ConfirmLine(
        project_line_id=str(line_id),
        borrow=[
            ConfirmBorrowComponent(
                source="other_location",
                warehouse_id=str(warehouse.id),
                qty=Decimal(str(qty)),
                reason=f"Take {qty} arriving {arrives.isoformat()}",
                supply_key=f"spo:{allocation.id}",
                supply_document=f"SPO {allocation.spo_number}",
                arrival_date=arrives,
                donor_core_line_id=donor_core_line_id,
            )
        ],
    )


def _live(rows):
    return [row for row in rows if row.state != INQUIRY_CANCELLED]


# ------------------------------------------------------- 1. the stale cancel races the handoff


def test_a_reconfirm_that_buys_what_it_borrowed_leaves_one_live_instruction():
    """FINDING 1. Revision 1 borrows a document; revision 2 buys the same quantity with
    Order back ticked.

    The handoff settles revision 1's step-3 row IN PLACE (same row, new quantity, its link
    kept) and therefore raises nothing new - and the stale cancel then ran AFTER it and
    cancelled the very row the handoff had just re-used, links and all. The 40 ended up
    with no live purchasing instruction at all: not raised, not placed, not cancelled with
    a replacement. Whatever the shape, exactly ONE live row has to carry the 40.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        allocation = _spo(db, product, other, qty=40, arrives=arrives)
        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )

        service = ProjectSupplyService(db)
        service.confirm(
            order,
            ConfirmSupplyBody(
                lines=[_borrow_line(line.id, other, allocation, qty=40, arrives=arrives)]
            ),
            actor_user_id=eling,
        )
        db.commit()

        # Revision 2: the planner changed their mind and BUYS it, marking the Buy as an
        # order back - the shape that makes the handoff own the step-3 row's own verb.
        ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=str(line.id),
                        buy_qty=Decimal("40"),
                        order_back=True,
                    )
                ]
            ),
            actor_user_id=eling,
        )
        db.commit()

        rows = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == line.id)
            .all()
        )
        links = db.query(OrderInquiryLink).all()

    live = _live(rows)
    assert len(live) == 1, [(r.verb, r.state, str(r.qty), r.covered_by) for r in rows]
    assert live[0].qty == Decimal("40.0000")
    assert live[0].verb == IV_ORDER_BACK
    assert not (live[0].covered_by or "").strip(), (
        "the borrow is gone: the row purchasing holds now is a Buy, and nothing covers it"
    )
    assert links == [], "the document is free again - nobody is borrowing it any more"


# --------------------------------------------------- 2. one borrow, one hole


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_document_donor_is_short_once_not_twice():
    """FINDING 2. A donor holding the document through a placement of its OWN gets its
    link taken down on Confirm, which puts its own row straight back to `raised` for that
    quantity - so purchasing already has the hole, at the donor's own date, on the donor's
    own line.

    Raising the asker-side ORDER_BACK as well told purchasing to buy 100 for a donor that
    is short 50. One borrow, one hole.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=ASKER_DAY - 5)
        allocation = _spo(db, product, donor_bin, qty=50, arrives=arrives)
        _donor_so, _donor_core, donor_mirror, _link = _donor_holding(
            db, company_id, project, product, donor_bin, qty=50, days=DONOR_DAY,
            actor=eling, allocation=allocation,
            so_number=f"ZZTSO-DONOR{_uid()[:4]}",
        )

        order, line, _cso, _core = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        _confirm_as_proposed(db, order, eling)

        asker_rows = _live(
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == line.id)
            .all()
        )
        donor_rows = _live(
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == donor_mirror.id)
            .all()
        )

    # The donor's OWN row is the hole, back where purchasing can see it.
    assert [(r.state, str(r.qty)) for r in donor_rows] == [
        (INQUIRY_RAISED, "50.0000")
    ]
    # The asker's line carries its placement row and NOTHING else: the second ORDER_BACK
    # was the same 50 said twice.
    assert len(asker_rows) == 1, [
        (r.verb, r.state, str(r.qty), r.covered_by, r.note) for r in asker_rows
    ]
    assert asker_rows[0].covered_by is not None
    worklist = sum(
        float(row.qty) for row in (asker_rows + donor_rows) if not (row.covered_by or "")
    )
    assert worklist == 50.0, "the donor's shortfall reaches purchasing once"


# --------------------------------------------------- 6. a line that leaves the revision


def test_uncovering_a_line_takes_its_step_three_placement_down():
    """FINDING 6. A step-3 row belongs to a DECISION, so it may not outlive the line's
    place in it. Undecided through `uncover_lines` (purchasing rejected the row), the
    placement was retired by nobody and went on pinning the document forever.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        allocation = _spo(db, product, other, qty=40, arrives=arrives)

        core_so = _core_so(db, company_id)
        core_so.so_number = f"ZZTSO-ASK{_uid()[:4]}"
        db.flush()
        order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
        borrowing_core = _core_line(
            db, core_so, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        borrowing = _project_line(
            db, order, line_no=1, product=product, core_line=borrowing_core
        )
        buying_core = _core_line(
            db, core_so, product, own, qty_ordered="7",
            required_date=date.today() + timedelta(days=ASKER_DAY + 1),
        )
        buying = _project_line(
            db, order, line_no=2, product=product, core_line=buying_core
        )
        db.commit()

        ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    _borrow_line(borrowing.id, other, allocation, qty=40, arrives=arrives),
                    ConfirmLine(project_line_id=str(buying.id), buy_qty=Decimal("7")),
                ]
            ),
            actor_user_id=eling,
        )
        db.commit()
        assert db.query(OrderInquiryLink).count() == 1

        ProjectSupplyService(db).uncover_lines(
            order,
            [str(borrowing.id)],
            actor_user_id=eling,
            reason="Purchasing rejected the placement.",
        )
        db.commit()

        rows = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == borrowing.id)
            .all()
        )
        links = db.query(OrderInquiryLink).all()
        survivor = _live(
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == buying.id)
            .all()
        )

    assert _live(rows) == [], [(r.verb, r.state, r.covered_by) for r in rows]
    assert links == [], "the document is free for whoever needs it next"
    assert len(survivor) == 1, "the other line's own instruction is untouched"


# --------------------------------------------- 7. the walk's ledger is keyed by the DOCUMENT


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_document_the_first_asker_may_not_touch_is_still_whole_for_the_second():
    """FINDING 7. `supply_left` was SEEDED with the first asker's own budget, and an
    asker's budget is personal: a document held by a line of the asker's OWN sales order is
    worth nothing to it (an order does not borrow from itself) and the whole 100 to the
    next order along.

    Seeding 0 from the first capped the second at 0, so a document nobody had spoken for
    reached no asker at all. The ledger has to count what the walk has DRAWN, the way the
    donor ledger one rung up does.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        allocation = _spo(db, product, donor_bin, qty=100, arrives=arrives)
        donor_core_so, _donor_core, _donor_mirror, _link = _donor_holding(
            db, company_id, project, product, donor_bin, qty=100, days=DONOR_DAY,
            actor=eling, allocation=allocation,
            so_number=f"ZZTSO-ONE{_uid()[:4]}",
        )

        # The first asker is a line of the DONOR'S OWN sales order: an order does not borrow
        # from itself, so the document is worth nothing to it and everything to the next.
        first_order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
        first_line = _project_line(
            db, first_order, line_no=1, product=product,
            core_line=_core_line(
                db, donor_core_so, product, own, qty_ordered="100",
                required_date=date.today() + timedelta(days=ASKER_DAY),
            ),
        )
        db.commit()
        second_order, second_line, _cso2, _core2 = _seed_line(
            db, company_id, project, product, own, qty_ordered="100",
            required_date=date.today() + timedelta(days=ASKER_DAY + 1),
            so_number=f"ZZTSO-TWO{_uid()[:4]}",
        )

        service = ProjectSupplyService(db)
        entries = []
        for order, mirror in ((first_order, first_line), (second_order, second_line)):
            facts = service._facts_for(order, service.lines_of(str(order.id)))
            fact = facts[str(mirror.id)]
            entries.append(
                (str(mirror.id), fact, (str(order.id), service._unit_key(fact)))
            )
        composed = service.compose_lines(entries)
        first = composed[str(first_line.id)][0]
        second = composed[str(second_line.id)][0]

    assert [c.kind for c in first] == ["buy"], (
        "an order does not borrow a document its own line is holding"
    )
    assert [(c.kind, c.rung) for c in second] == [("borrow", "supply_borrow")], (
        "the document was never drawn, so the next asker still finds it whole"
    )


# ------------------------------------------- 8. a carried step-3 borrow keeps its document


def test_a_carried_free_document_raises_no_phantom_order_back():
    """FINDING 8. A carried line's components are rebuilt from the frozen snapshot, and the
    rebuild dropped `supply_key` - so on the next partial confirm `_borrow_shortfalls` no
    longer recognised the component as step 3 and fell through to the location-pile block,
    which read the bin the container is BOUND for as a donor group and raised an order-back
    against a group that had lost nothing.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        allocation = _spo(db, product, other, qty=40, arrives=arrives)

        core_so = _core_so(db, company_id)
        core_so.so_number = f"ZZTSO-ASK{_uid()[:4]}"
        db.flush()
        order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
        taking_core = _core_line(
            db, core_so, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        taking = _project_line(
            db, order, line_no=1, product=product, core_line=taking_core
        )
        later_core = _core_line(
            db, core_so, product, own, qty_ordered="7",
            required_date=date.today() + timedelta(days=ASKER_DAY + 1),
        )
        later = _project_line(db, order, line_no=2, product=product, core_line=later_core)
        db.commit()

        ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    _borrow_line(taking.id, other, allocation, qty=40, arrives=arrives)
                ]
            ),
            actor_user_id=eling,
        )
        db.commit()

        # A SECOND confirmation naming only the other line: line 1 rides along carried, and
        # its step-3 component is rebuilt off the snapshot.
        ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[ConfirmLine(project_line_id=str(later.id), buy_qty=Decimal("7"))]
            ),
            actor_user_id=eling,
        )
        db.commit()

        holes = [
            row
            for row in _live(
                db.query(OrderInquiryRow)
                .filter(OrderInquiryRow.verb == IV_ORDER_BACK)
                .all()
            )
            if not (row.covered_by or "").strip()
        ]

    assert holes == [], (
        "nobody was waiting on that document, so nobody is owed anything back",
        [(str(r.qty), r.stock_location, r.note) for r in holes],
    )


# ------------------------------------------------- 9. a released placement takes its claim


@pytest.mark.usefixtures("ladder_steps_on")
def test_releasing_a_placement_takes_its_claim_down_with_it():
    """FINDING 9. `release_supply_borrow` deleted the link row by hand and left the
    `order_link_claims` row it wrote standing - an orphan claim on a document the donor no
    longer holds. Every other unlink in this service goes through `_remove_links`, which is
    where that rule lives.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=ASKER_DAY - 5)
        allocation = _spo(db, product, donor_bin, qty=50, arrives=arrives)
        _donor_so, _donor_core, donor_mirror, donor_link = _donor_holding(
            db, company_id, project, product, donor_bin, qty=50, days=DONOR_DAY,
            actor=eling, allocation=allocation,
            so_number=f"ZZTSO-DONOR{_uid()[:4]}",
        )
        # Re-written the way purchasing's own Link SPO writes it, so the link carries the
        # audit claim this test is about.
        donor_row = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.id == donor_link.row_id)
            .one()
        )
        db.delete(donor_link)
        donor_row.state = INQUIRY_RAISED
        db.flush()
        ProjectOrderInquiryService(db).place_on_po_allocations(
            str(donor_row.id),
            [{"spo_allocation_id": str(allocation.id), "qty": Decimal("50")}],
            actor_user_id=eling,
        )
        db.commit()
        donor_claim_id = str(
            db.query(OrderInquiryLink.claim_id)
            .filter(OrderInquiryLink.row_id == donor_row.id)
            .scalar()
        )

        order, _line, _cso, _core = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        _confirm_as_proposed(db, order, eling)

        donor_links = (
            db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.row_id == donor_row.id)
            .all()
        )
        survivor = (
            db.query(OrderLinkClaim)
            .filter(OrderLinkClaim.id == donor_claim_id)
            .first()
        )
        _ = donor_mirror

    assert donor_claim_id != "None", "the donor's own placement wrote one claim"
    assert donor_links == [], "the donor gave the document up"
    assert survivor is None, "and its claim went with it"


# ------------------------------- 10. a hold on a document outside the span is not an arrival


def test_a_document_outside_the_assignment_span_is_not_offered_as_arriving_today():
    """FINDING 10. `assign()` honours a hold whose document is outside the read span by
    standing an event up FROM THE HOLD (AC-S2-1b) - no warehouse, dated `as_of`, because
    the span it was given holds no arrival to place it on.

    Step 3 read that as a real document landing TODAY and offered it, which is a promise
    about a delivery date nobody knows. A supply event with no bin is not a document this
    step may name.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        outside = _warehouse(
            db, f"ZZTOFF-IR{_uid()[:3]}", fulfilment_planning=False
        )
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        allocation = _spo(db, product, outside, qty=50, arrives=arrives)
        _donor_holding(
            db, company_id, project, product, donor_bin, qty=50, days=DONOR_DAY,
            actor=eling, allocation=allocation,
            so_number=f"ZZTSO-DONOR{_uid()[:4]}",
        )

        order, _line, _cso, _core = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert _rung(components, "supply_borrow") == [], (
        "a hold's stand-in event is not a document arriving today"
    )
    assert [(c["kind"], c["qty"]) for c in components] == [("buy", "50")]


# ------------------------------------ the tester's gap: the asker's own claim on a document


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_document_the_walk_already_gave_the_asker_covers_its_own_unit():
    """THE SO414244 SHAPE. The walk assigns the asker's own group's incoming document to the
    asker's own line - it lands after the line's date, so the line reads `late` and step 1
    (which may promise only what is there BY the date) offers nothing.

    Step 3 then counted only what was FREE on that document and read 0, so a line one
    document covers whole was bought instead. A document's availability to an asker is what
    is free PLUS what the walk has already given that asker on it plus what eligible later
    orders hold: the asker is not borrowing from anybody, it is taking what it already has.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        allocation = _spo(db, product, own, qty=50, arrives=arrives)
        order, _line, _cso, _core = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))
        document = f"SPO {allocation.spo_number}"

    taken = _rung(components, "supply_borrow")
    assert [(c["kind"], c["qty"]) for c in taken] == [("borrow", "50")]
    assert taken[0]["supply_document"] == document
    assert taken[0]["donor_so_number"] is None, (
        "nobody is owed it back: the walk had already given it to this line"
    )
    assert [c["kind"] for c in components] == ["borrow"], "no Buy is left"


# ------------------------------------- 3. a planning change re-posts the document it froze


def test_a_planning_change_reconfirms_a_step_three_borrow_as_the_document_it_was():
    """FINDING 3. `_to_confirm_line` turns a composition dict into the body the
    confirmation takes.

    The three step-3 fields were dropped on the way through, so a keep re-confirmed a
    borrow OFF A DOCUMENT as an ordinary free-stock borrow at whatever bin the document is
    bound for: the placement link came down and the confirmation re-checked the quantity
    against on-hand capacity at a bin that has never held it.

    `_confirm_payload` (which used to build this dict from a frozen `components` list) is
    retired - Slice C applies a row from the composition CS confirmed, never from a verb
    Apply re-decides off a frozen snapshot - so the composition dict it would have built
    for this one borrow component is built by hand here instead (review round C7).
    """
    from app.services.planning_change_service import _to_confirm_line

    payload = {
        "project_line_id": "33333333-3333-3333-3333-333333333333",
        "timely_spo_qty": Decimal("0"),
        "reserve": [],
        "borrow": [
            {
                "source": "other_location",
                "warehouse_id": "11111111-1111-1111-1111-111111111111",
                "donor_project_id": None,
                "qty": Decimal("40"),
                "reason": "Taking the container",
                "supply_key": "spo:22222222-2222-2222-2222-222222222222",
                "supply_document": "SPO 202607-S0105",
                "arrival_date": "2026-09-15",
            }
        ],
        "buy_qty": Decimal("0"),
    }
    line = _to_confirm_line(payload)

    assert [
        (c.supply_key, c.supply_document, c.arrival_date) for c in line.borrow
    ] == [("spo:22222222-2222-2222-2222-222222222222", "SPO 202607-S0105", date(2026, 9, 15))]


# --------------------------------- 4. the document survives the response model, end to end


def test_the_board_route_states_the_document_a_confirmed_step_three_line_named():
    """FINDING 4. `response_model` drops what it does not declare, and `BoardDecisionBorrow`
    declared none of the three - so a confirmed step-3 line came back off the wire
    documentless and the editor re-posted it as a free-stock borrow.

    Through the route, because that is where the trap is: the service had the fields all
    along.
    """
    from app.models.base import company_scope

    from ..test_fulfilment_board import BASE, VIEW, _client, _restore

    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        allocation = _spo(db, product, other, qty=40, arrives=arrives)
        order, line, core_so, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        # The board only addresses a planning record that HOLDS the core order
        # (`_mirror_addressing`), which is what the adoption sets.
        order.so_id = core_so.id
        db.commit()
        ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[_borrow_line(line.id, other, allocation, qty=40, arrives=arrives)]
            ),
            actor_user_id=eling,
        )
        db.commit()

        client, originals = _client(db, eling, [VIEW])
        try:
            with company_scope(db, frozenset({company_id})):
                response = client.get(
                    f"{BASE}/fulfilment-planning/board",
                    params={
                        "orders": core_so.so_number,
                        "granularity": "week",
                        "as_of": date.today().isoformat(),
                    },
                )
        finally:
            _restore(originals)

        assert response.status_code == 200, response.text
        body = response.json()
        supply_key = f"spo:{allocation.id}"
        document = f"SPO {allocation.spo_number}"
        landing = arrives.isoformat()

    contribution = body["contributions"][0]
    borrow = contribution["decision"]["borrow"]
    assert [
        (row["supply_key"], row["supply_document"], row["arrival_date"]) for row in borrow
    ] == [(supply_key, document, landing)]
