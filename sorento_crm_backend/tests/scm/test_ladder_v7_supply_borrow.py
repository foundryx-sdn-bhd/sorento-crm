"""Ladder v7.1 step 3: BORROW THE DOCUMENT a later order is waiting on (S4).

`documentation/plans/scm/PLAN-scm-borrow-ladder-v7-stock-debt.md` section 3.2 step 3 and
3.3, UAC AC-S4-1 to AC-S4-4. Written before the code, as PRINCIPLES step 4 requires.

Four rules shape every fixture below.

**ONE DOCUMENT, WHOLE UNIT** (R33). An SPO of 5 beside a PO of 7 does not cover a unit of
12: the step gives nothing and the walk moves on. Sources combine inside step 2 (two on-hand
donors are one timing) and never here, because two documents landing on two dates are two
different promises about one delivery.

**SPO BEFORE PO, AND A PO ONLY WHEN NO SINGLE SPO COVERS** (R27, R30, R35). An SPO is
ARRIVING - it has been cut from a purchase order and put on a shipment; a PO is still ON
ORDER. Nearest arrival first inside each family.

**ELIGIBLE MEANS IT BEATS BUYING** (R32). A document is taken when it arrives by the asker's
date OR before a fresh purchase would land (`as_of + lead`). The captain, on AC-S4-2b's row:
"if buy, it is going to arrive even later". Such a line is `late`, and it is earlier than
every alternative the ladder has left.

**A DOCUMENT REACHES STEP 3 IN EXACTLY TWO WAYS**, and every fixture below is one of them.
Under the ONE assignment the board and the Stock Debt view share (R21), step 1 already
answers with a document that is FREE BY THE ASKER'S OWN DATE - its own group's water
(`_drawn_at_own_date`) or another project group's free pile at that date (`free_piles_at`,
R40). So step 3 is what is left:

* the document lands AFTER the asker's date, so no free pile holds it when the asker asks,
  and it is taken only because it still beats buying (R32); or
* the document is PINNED to a later order - a confirmed decision or a placement link - so it
  is free to nobody, whatever its date. That is the same discovery step 2's own suite
  records: "a LATER order's supply is only held by it when something PINS it", and it is not
  scaffolding, it is the case.

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
from app.schemas.project_supply import (
    ConfirmBorrowComponent,
    ConfirmLine,
    ConfirmSupplyBody,
)
from app.services.project_supply_service import ProjectSupplyService
from app.services.scm.front_planning_engine import date_text, month_text, qty_text

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
from .test_ladder_v7_borrow import LEAD_DAYS, WINDOW_DAY, _options, _policy
from .test_project_supply_service_ladder import (
    _agent,
    _components,
    _group_sites,
    _lead_time,
    _seed_line,
    _world,
)

#: The asking line's own date in every relative fixture: 20 days out, inside its own reserve
#: window (`today + 30 + 14`), so the ladder is walked at all.
#:
#: DELIBERATELY EARLIER THAN THE LEAD TIME. R32's whole case lives in the gap between the
#: asker's own date and the day a purchase raised today would land (`today + 30`): a document
#: arriving inside that gap is late for the asker and still earlier than buying, which is the
#: only reason step 3 takes it. With the two dates equal there is no gap and no case.
ASKER_DAY = 20
#: A document landing after the asker needs it (day 20) and before a fresh buy would (day 30).
LATE_ARRIVAL_DAY = ASKER_DAY + 5
#: The first day a donor may be due on (R12): `as_of + lead + 14`.
DONOR_DAY = WINDOW_DAY + 10


# --------------------------------------------------------------------------- fixtures


def _spo(db, product, warehouse, *, qty, arrives, spo_number=None, line_no=1):
    """One open shipping-order allocation with no container booked - every SPO document on
    the live book since migration 420, which is why `_spo_rows` outer-joins the shipment."""
    from app.models.procurement import SPOAllocation

    row = SPOAllocation(
        id=_uid(),
        spo_number=spo_number or f"ZZT-SPO-{_uid()[:8]}",
        spo_line_number=line_no,
        product_id=product.id,
        warehouse_id=warehouse.id,
        allocated_quantity=Decimal(str(qty)),
        quantity_received=Decimal("0"),
        receipt_status="pending",
        line_status="open",
        expected_date=arrives,
    )
    db.add(row)
    db.flush()
    return row


def _po(db, product, warehouse, *, qty, issue_date, lead_days, po_number=None, lines=1):
    """A purchase order whose supplier states `lead_days`, so its arrival is computable.

    Returns `(po, [lines])`. The arrival every reader dates it by is
    `issue_date + lead_days` (R29): `expected_date` on the line is the SO delivery date the
    buyer typed against it and is never an arrival.
    """
    from app.models.procurement import (
        ProductSupplier,
        PurchaseOrder,
        PurchaseOrderLine,
        Supplier,
    )

    supplier = Supplier(
        id=_uid(),
        supplier_code=f"ZZT-SUP-{_uid()[:8]}".upper(),
        supplier_name="ZZT document supplier",
        is_active=True,
    )
    db.add(supplier)
    db.flush()
    db.add(
        ProductSupplier(
            id=_uid(),
            product_id=product.id,
            supplier_id=supplier.id,
            standard_lead_time_days=lead_days,
        )
    )
    po = PurchaseOrder(
        id=_uid(),
        po_number=po_number or f"ZZT-PO-{_uid()[:8]}".upper(),
        supplier_id=supplier.id,
        issue_date=issue_date,
        status="active",
    )
    db.add(po)
    db.flush()
    written = []
    for index in range(lines):
        row = PurchaseOrderLine(
            id=_uid(),
            purchase_order_id=po.id,
            product_id=product.id,
            warehouse_id=warehouse.id,
            qty_ordered=Decimal(str(qty)),
            qty_received=Decimal("0"),
            line_status="open",
        )
        db.add(row)
        db.flush()
        written.append(row)
    return po, written


def _donor_holding(
    db, company_id, project, product, warehouse, *, qty, days, actor,
    allocation=None, po_line=None, so_number=None, agent_id=None,
):
    """A later order that HOLDS a document, through a placement link of its own.

    The shape purchasing's Link SPO writes, and the shape S4's own Confirm writes: an
    ORDER_BACK row on the order's line with an `order_inquiry_links` row naming the
    document. It is what makes the document "held" rather than free - `assign()` reads a
    link as a pinned hold (R21), so `free_piles_at` nets it out of every free pile and
    step 3 is the only rung that can reach it.

    Returns `(core sales order, core line, project line, link)`.
    """
    from app.models.project_so import INQUIRY_PLACED, INQUIRY_RAISED, OrderInquiry

    core_so = _core_so(db, company_id)
    core_so.so_number = so_number or f"ZZT-SO-{_uid()[:8]}"
    if agent_id is not None:
        core_so.sales_agent_id = agent_id
    db.flush()
    core_line = _core_line(
        db, core_so, product, warehouse, qty_ordered=str(qty),
        required_date=date.today() + timedelta(days=days),
    )
    order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
    line = _project_line(db, order, line_no=1, product=product, core_line=core_line)
    db.flush()
    inquiry = OrderInquiry(
        company_id=company_id,
        project_sales_order_id=order.id,
        state=INQUIRY_RAISED,
        raised_by=actor,
    )
    db.add(inquiry)
    db.flush()
    row = OrderInquiryRow(
        company_id=company_id,
        order_inquiry_id=inquiry.id,
        so_line_id=line.id,
        qty=Decimal(str(qty)),
        verb=IV_ORDER_BACK,
        state=INQUIRY_PLACED,
    )
    db.add(row)
    db.flush()
    link = OrderInquiryLink(
        company_id=company_id,
        row_id=row.id,
        spo_allocation_id=allocation.id if allocation is not None else None,
        po_line_id=po_line.id if po_line is not None else None,
        document=(
            allocation.spo_number if allocation is not None else None
        ),
        qty=Decimal(str(qty)),
        linked_by=actor,
    )
    db.add(link)
    db.commit()
    return core_so, core_line, line, link


def _confirm_as_proposed(db, order, actor, *, service=None):
    """Post the proposal VERBATIM, the way `fulfilmentBoard.ts` posts an approved line.

    Every proposed component travels as it was offered - the engine's own reason, its donor,
    and (step 3) the document it names - because an approved line is the engine's answer and
    not a person's composition.
    """
    service = service or ProjectSupplyService(db)
    proposal = service.proposal_for(order)
    lines = []
    for line in proposal["lines"]:
        reserve = []
        borrow = []
        timely = Decimal("0")
        buy = Decimal("0")
        for component in line["components"]:
            qty = Decimal(component["qty"])
            if component["kind"] == "buy":
                buy += qty
                continue
            if component["kind"] == "timely_spo":
                timely += qty
                continue
            if component["kind"] == "borrow":
                borrow.append(
                    ConfirmBorrowComponent(
                        source="other_location",
                        warehouse_id=component["source_warehouse_id"],
                        qty=qty,
                        reason=component["reason"],
                        donor_core_line_id=component.get("donor_core_line_id"),
                        donor_so_number=component.get("donor_so_number"),
                        donor_line_no=component.get("donor_line_no"),
                        donor_agent_code=component.get("donor_agent_code"),
                        same_agent=bool(component.get("same_agent")),
                        donor_required_date=component.get("donor_required_date"),
                        supply_key=component.get("supply_key"),
                        supply_document=component.get("supply_document"),
                        arrival_date=component.get("arrival_date"),
                    )
                )
                continue
            reserve.append(
                {"warehouse_id": component["source_warehouse_id"], "qty": qty}
            )
        lines.append(
            ConfirmLine(
                project_line_id=line["project_line_id"],
                timely_spo_qty=timely,
                reserve=reserve,
                borrow=borrow,
                buy_qty=buy,
            )
        )
    result = ProjectSupplyService(db).confirm(
        order, ConfirmSupplyBody(lines=lines), actor_user_id=actor
    )
    db.commit()
    return result


def _rung(components, rung):
    return [c for c in components if c.get("rung") == rung]


# --------------------------------------------------------------------------- AC-S4-1


@pytest.mark.usefixtures("ladder_steps_on")
def test_an_spo_a_later_order_is_waiting_on_is_borrowed_whole_and_names_its_debt():
    """AC-S4-1, first half: the nearest SPO arriving by the asker's date, held by a later
    order, is offered whole - kind `borrow`, rung `supply_borrow`, the document addressed by
    its own key, and the sentence naming the arrival, the document, the donor and the month
    the debt lands in."""
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        # The donor sits in a group of its own AND holds the document through a placement,
        # so no free pile - this line's or anybody else's - can reach it at the asker's date.
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
    ], "one document, the whole unit"
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
    assert chosen[0]["days_late"] == 0, "it lands before the asker's own date"
    assert chosen[0]["debt_so_number"] == donor_number


def test_a_po_line_a_later_order_holds_is_never_offered_and_the_unit_buys():
    """AC-S4-1, second half - RETIRED 31 Aug (`PLAN-scm-planning-feedback-31aug.md` S1,
    R-A, `scm-planning-feedback-31aug-acceptance-criteria.md` AC-1.1): a PO line is ON
    ORDER, not arriving, and the captain's own production row (`202607-S0067`, a real PO)
    is what "Borrow incoming" must never name again. A PO a later order holds is not a
    step-3 candidate at all, whatever its computed arrival, and the unit falls to Buy.
    """
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
        po_number = po.po_number

    assert _rung(components, "supply_borrow") == []
    assert [(c["kind"], c["qty"]) for c in components] == [("buy", "20")]
    assert po_number not in components[0]["reason"]


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_free_document_is_taken_rather_than_borrowed_and_owes_nobody():
    """PLAN 3.2 step 3: a document nobody is waiting on raises no debt, so the sentence says
    "Take" rather than "Borrow" and no order-back travels with it."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        # Another group's bin: free there, and step 1b offers only what has ARRIVED by the
        # asker's date, so a document landing after it is step 3's business.
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=ASKER_DAY + 5)
        allocation = _spo(db, product, other, qty=40, arrives=arrives)

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))
        spo_number = allocation.spo_number

    borrowed = _rung(components, "supply_borrow")
    assert [(c["kind"], c["qty"]) for c in borrowed] == [("borrow", "40")]
    assert borrowed[0]["donor_so_number"] is None
    assert borrowed[0]["order_back_qty"] is None
    assert borrowed[0]["reason"] == (
        f"Take 40 arriving {date_text(arrives)} (SPO {spo_number})"
    )


# --------------------------------------------------------------------------- AC-S4-2


@pytest.mark.usefixtures("ladder_steps_on")
def test_the_nearest_arriving_spo_is_taken_first():
    """AC-S4-2, first clause: two eligible SPO documents, and the one landing first wins."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        # Both land AFTER the asker's own date, so no free pile holds either of them when
        # he asks, and both still beat a purchase raised today (day 30 + 30 = day 60).
        near = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        far = date.today() + timedelta(days=LATE_ARRIVAL_DAY + 3)
        _spo(db, product, other, qty=30, arrives=far, spo_number="ZZT-SPO-FAR")
        nearest = _spo(db, product, other, qty=30, arrives=near, spo_number="ZZT-SPO-NEAR")

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="30",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))
        nearest_id = nearest.id

    borrowed = _rung(components, "supply_borrow")
    assert [c["supply_key"] for c in borrowed] == [f"spo:{nearest_id}"]


def test_a_po_that_would_have_covered_the_unit_is_never_reached():
    """AC-S4-2, second clause - RETIRED 31 Aug (S1, R-A): an SPO that covers PART of the
    unit is not offered at all (R33 stands), and there is no PO fallback to reach for the
    rest any more - the unit falls straight to Buy."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        _spo(db, product, other, qty=5, arrives=date.today() + timedelta(days=LATE_ARRIVAL_DAY))
        # Landing two days after the SPO, and still before a purchase raised today would.
        issue = date.today() + timedelta(days=LATE_ARRIVAL_DAY + 2 - LEAD_DAYS)
        _po(
            db, product, other, qty=30, issue_date=issue, lead_days=LEAD_DAYS,
        )

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="30",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert _rung(components, "supply_borrow") == []
    assert [(c["kind"], c["qty"]) for c in components] == [("buy", "30")]


@pytest.mark.usefixtures("ladder_steps_on")
def test_an_spo_of_five_beside_a_po_of_seven_covers_no_unit_of_twelve():
    """AC-S4-2, third clause (R33): never half and half. Neither document covers the unit,
    so the step gives nothing and the walk moves on to the pool and then to Buy."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        _spo(db, product, other, qty=5, arrives=date.today() + timedelta(days=LATE_ARRIVAL_DAY))
        _po(
            db, product, other, qty=7,
            issue_date=date.today() + timedelta(days=LATE_ARRIVAL_DAY + 2 - LEAD_DAYS),
            lead_days=LEAD_DAYS,
        )

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="12",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)
        options = _options(proposal)

    assert [(c["kind"], c["qty"]) for c in components] == [("buy", "12")]
    step = next(option for option in options if option["step"] == "supply_borrow")
    assert step["whole"] is False
    assert step["fulfil_date"] is None and step["days_late"] is None


def test_a_document_landing_after_a_fresh_buy_would_is_not_eligible():
    """R32 read from the other side: a document that lands LATER than buying is no help, so
    it is not offered and the unit buys."""
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        # A buy placed today lands on day 30; this SPO lands on day 40, so it helps nobody.
        _spo(db, product, other, qty=25, arrives=date.today() + timedelta(days=LEAD_DAYS + 10))

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="25",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [(c["kind"], c["qty"]) for c in components] == [("buy", "25")]


def test_a_donor_inside_the_window_or_past_due_does_not_lend_its_document():
    """AC-S4-2's donor half, which is step 2's window applied to step 3 (R3, R12): a donor
    that cannot itself wait is no donor, so a document assigned to one is not on the table.
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
        # Due INSIDE the window (day 40 < day 44): purchasing cannot buy in time for this
        # order either, so it is not somebody who can wait - and it holds the document, so
        # no free pile can reach it either.
        _donor_holding(
            db, company_id, project, product, donor_bin, qty=50, days=WINDOW_DAY - 4,
            actor=eling, allocation=allocation,
            so_number=f"ZZTSO-NEAR{_uid()[:4]}",
        )

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))

    assert [(c["kind"], c["qty"]) for c in components] == [("buy", "50")]


# -------------------------------------------------------------------------- AC-S4-2b

#: The captain's own row, with its own calendar (AC-S4-2b). Pinned rather than relative,
#: because the figures in the AC - 21 days late against 32 - are the whole assertion.
JAY_AS_OF = date(2026, 8, 29)
JAY_REQUIRED = date(2026, 10, 26)
JAY_PO_ISSUE = date(2026, 8, 18)
JAY_LEAD = 90


@pytest.mark.usefixtures("ladder_steps_on")
def test_jay_no_longer_reaches_the_po_and_buys_instead():
    """AC-S4-2b - RETIRED 31 Aug (`PLAN-scm-planning-feedback-31aug.md` S1, R-A). This IS
    the captain's own production row: `202608-S0041` (this test's own stand-in for the real
    `202607-S0067`) is a `purchase_orders` line, not an SPO - "on order", not "arriving" -
    and the captain's ruling on the real one was that OFFERING it as "Borrow incoming" is
    the defect. JAY's 32 no longer reaches the PO at all; the 16 on hand stays free (one
    step covers the whole unit or gives nothing, R10/R33) and the whole 32 buys.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        _stock(db, product, own, on_hand=16)
        _lead_time(db, product, JAY_LEAD)
        _policy(db)

        _po_doc, _po_lines = _po(
            db, product, own, qty=100, issue_date=JAY_PO_ISSUE, lead_days=JAY_LEAD,
            po_number="ZZT-202608-S0041",
        )

        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="32",
            required_date=JAY_REQUIRED,
        )
        service = ProjectSupplyService(db)
        mirrors = service.lines_of(str(order.id))
        facts = service._facts_for(order, mirrors)
        fact = facts[str(line.id)]
        composed = service.compose_lines(
            [(str(line.id), fact, service._unit_key(fact))], as_of=JAY_AS_OF
        )
        components, *_rest = composed[str(line.id)]
        options = composed[str(line.id)][4]

    assert [(c.kind, qty_text(c.qty), c.rung) for c in components] == [
        ("buy", "32", "buy")
    ]
    assert not any(c.rung == "group_take" for c in components), (
        "the 16 on hand stays free - one step covers the whole unit or gives nothing, "
        "and sources never combine across two"
    )

    by_step = {option.step: option for option in options}
    assert by_step["supply_borrow"].whole is False, "the PO is never a step-3 candidate"
    assert by_step["supply_borrow"].fulfil_date is None
    assert by_step["buy"].whole is True
    assert by_step["buy"].fulfil_date == date(2026, 11, 27)
    assert by_step["buy"].days_late == 32
    assert by_step["use"].whole is False, "16 on hand is not the whole of 32"


# --------------------------------------------------------------------------- AC-S4-3


@pytest.mark.usefixtures("ladder_steps_on")
def test_confirming_a_supply_borrow_moves_the_placement_to_the_asker():
    """AC-S4-3 (PLAN 3.3, R8): the whole transaction, in one Confirm.

    The asker gets an ORDER_BACK-verb row carrying a LINK on the document; the donor's own
    link on the same placement is taken down for that quantity; the donor is short at ITS
    date. And because `assign()` reads links as pinned supply, the next read of the same
    book has the asker `covered` off that SPO and the donor `short` - with nothing further
    to write, which is the point of doing it through the links.

    WHERE THE DONOR'S HOLE IS RAISED, corrected in the S4 fix pass (30 Aug). This donor
    holds the document through a PLACEMENT of its own, so taking that placement down puts
    the donor's own row back to `raised` for the quantity, on the donor's own line, at the
    donor's own date - and a second ORDER_BACK on the ASKER's line for the same 50 was the
    same shortfall told to purchasing twice. `_borrow_shortfalls` nets it, and the row it
    still raises is the one for a donor whose hold is a confirmed DECISION and no link,
    which re-raises nothing by itself.
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
        _donor_so, donor_core, _donor_mirror, _donor_link = _donor_holding(
            db, company_id, project, product, donor_bin, qty=50, days=DONOR_DAY,
            actor=eling, allocation=allocation,
            so_number=f"ZZTSO-DONOR{_uid()[:4]}",
        )

        order, line, _cso, asker_core = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        _confirm_as_proposed(db, order, eling)

        rows = [
            row
            for row in db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == line.id)
            .all()
            if row.state != INQUIRY_CANCELLED
        ]
        donor_row_after = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == _donor_mirror.id)
            .one()
        )
        links = db.query(OrderInquiryLink).all()
        service = ProjectSupplyService(db)
        after = service.planning_assignments([str(product.id)])[str(product.id)]
        asker_row = next(r for r in after.lines if r.line.key == str(asker_core.id))
        donor_row = next(r for r in after.lines if r.line.key == str(donor_core.id))
        allocation_id = str(allocation.id)
        donor_due = date.today() + timedelta(days=DONOR_DAY)

    # ONE link, on the placement, for the borrowed quantity.
    assert [(str(link.spo_allocation_id), str(link.qty)) for link in links] == [
        (allocation_id, "50.0000")
    ]
    linked_row = next(row for row in rows if str(row.id) == str(links[0].row_id))
    assert linked_row.verb == IV_ORDER_BACK
    assert linked_row.covered_by is not None, (
        "the asker's row names what covers it, which is what tells it apart from a hole"
    )
    # ...and NOT a second one beside it: the donor's own row is the hole (see the docstring).
    assert [row.id for row in rows] == [linked_row.id], [
        (row.verb, row.state, str(row.qty), row.covered_by) for row in rows
    ]
    assert (donor_row_after.state, donor_row_after.qty) == (
        INQUIRY_RAISED, Decimal("50.0000")
    )
    assert donor_row_after.delivery_date is None or donor_row_after.delivery_date == donor_due

    # The pin the link IS, read back by the one assignment both surfaces share.
    assert asker_row.status == "pinned" and asker_row.uncovered == 0.0
    assert donor_row.status == "short" and donor_row.uncovered == 50.0


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_partly_held_document_lends_the_donors_share_and_takes_its_free_share():
    """AC-S4-3's middle clause, and R33 inside one document.

    The document holds 50; a later order has 20 of it through a placement of its own and the
    other 30 is free. The unit of 50 is covered off THAT ONE DOCUMENT - free share first,
    because it owes nobody, then the donor's - and the donor's link is taken down for what
    it gave up. Two rows pointing at one quantity is what the link table exists to prevent.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        allocation = _spo(db, product, donor_bin, qty=50, arrives=arrives)
        _donor_so, donor_core, _donor_mirror, donor_link = _donor_holding(
            db, company_id, project, product, donor_bin, qty=20, days=DONOR_DAY,
            actor=eling, allocation=allocation,
            so_number=f"ZZTSO-DONOR{_uid()[:4]}",
        )

        order, _line, _cso, asker_core = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        components = _components(ProjectSupplyService(db).proposal_for(order))
        _confirm_as_proposed(db, order, eling)

        remaining = (
            db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.id == donor_link.id)
            .first()
        )
        links = (
            db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.spo_allocation_id == allocation.id)
            .all()
        )
        service = ProjectSupplyService(db)
        after = service.planning_assignments([str(product.id)])[str(product.id)]
        asker_row = next(r for r in after.lines if r.line.key == str(asker_core.id))
        donor_row = next(r for r in after.lines if r.line.key == str(donor_core.id))

    borrowed = _rung(components, "supply_borrow")
    assert [c["qty"] for c in borrowed] == ["30", "20"], (
        "the free share of the document first, then the donor's"
    )
    assert [c["donor_so_number"] is None for c in borrowed] == [True, False]
    assert remaining is None, "the donor gave up the whole of its placement"
    assert sum(float(link.qty) for link in links) == 50.0
    assert asker_row.status == "pinned"
    assert donor_row.status == "short" and donor_row.uncovered == 20.0


# --------------------------------------------------------------------------- AC-S4-4


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_document_that_covers_the_unit_answers_when_the_pool_has_nothing_to_spare():
    """AC-S4-4 (R13), RE-BLESSED BY LADDER V8 (R-A): the pool is asked FIRST now, so the
    order this case pinned is reversed - a pool with 500 free would answer before any
    document.

    What survives, and is what the case is really about, is that a single document covering
    the unit is a whole-unit answer: the pool here holds nothing to spare, so step 3 is
    reached and takes the document whole.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _stock(db, product, pool, on_hand=0)
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        _spo(db, product, other, qty=40, arrives=arrives)

        order, _line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        proposal = ProjectSupplyService(db).proposal_for(order)
        components = _components(proposal)
        options = _options(proposal)

    assert [(c["kind"], c["qty"], c["rung"]) for c in components] == [
        ("borrow", "40", "supply_borrow")
    ]
    assert [option["step"] for option in options if option["chosen"]] == ["supply_borrow"]
    assert not any(c["rung"] == "pool" for c in components), (
        "the pool had nothing to spare, so it gave nothing"
    )


# ---------------------------------------------------------------- the walk's own ledger


@pytest.mark.usefixtures("ladder_steps_on")
def test_two_units_of_one_walk_are_not_both_offered_the_same_document():
    """The ledger every other step of this ladder keeps, kept for step 3 too.

    Without it each unit reads the document whole, both are proposed it, and `confirm`
    refuses all but the first - the defect four delivery dates of SO381895 hit on the donor
    rung on 28 August 2026.
    """
    with blank_session() as db:
        company_id, _eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        _spo(db, product, other, qty=40, arrives=date.today() + timedelta(days=LATE_ARRIVAL_DAY))

        core_so = _core_so(db, company_id)
        core_so.so_number = f"ZZTSO-ASK{_uid()[:4]}"
        db.flush()
        order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
        mirrors = []
        for index, when in enumerate(
            (
                date.today() + timedelta(days=ASKER_DAY),
                date.today() + timedelta(days=ASKER_DAY + 1),
            ),
            start=1,
        ):
            core_line = _core_line(
                db, core_so, product, own, qty_ordered="40", required_date=when,
            )
            mirrors.append(
                _project_line(
                    db, order, line_no=index, product=product, core_line=core_line
                )
            )
        db.commit()

        service = ProjectSupplyService(db)
        facts = service._facts_for(order, service.lines_of(str(order.id)))
        composed = service.compose_lines(
            [
                (str(mirror.id), facts[str(mirror.id)],
                 service._unit_key(facts[str(mirror.id)]))
                for mirror in mirrors
            ]
        )
        first = composed[str(mirrors[0].id)][0]
        second = composed[str(mirrors[1].id)][0]

    assert [(c.kind, qty_text(c.qty), c.rung) for c in first] == [
        ("borrow", "40", "supply_borrow")
    ]
    assert [(c.kind, qty_text(c.qty)) for c in second] == [("buy", "40")], (
        "the document was spent by the first unit of the walk"
    )
