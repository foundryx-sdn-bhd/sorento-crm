"""Tester probes for ladder v7.1 step 3 (S4), written against the coder's own
`test_ladder_v7_supply_borrow.py`, PLAN 3.2 step 3 / 3.3, UAC AC-S4-1..6.

Four scenarios the coder's suite does not already pin, each named for the AC/PLAN clause
it exercises:

* re-confirming the SAME line against a DIFFERENT document cancels the stale placement
  first (PLAN 3.3's "a row raised by an EARLIER revision ... is CANCELLED first, links and
  all"), so the document never reads as claimed twice;
* two lines of ONE confirm sharing ONE document, through the real DB write path
  (`_check_supply_borrow`'s per-confirmation ledger), not just `compose_lines`'s dict
  (already pinned by `test_two_units_of_one_walk_are_not_both_offered_the_same_document`);
* a donor who is not just link-holding but DECIDED (an active `SOSupplyDecision` of its
  own, from its own earlier confirm) - `_supersede_borrowed_donors` has to re-issue that
  revision minus only the borrowed line, and the document has to actually change hands;
* the manual candidate reading `place_supply_borrow` uses is not gated by ladder v4's
  group-deficit rule the way the AUTOMATIC reading is (PLAN 3.3's closing paragraph).

Postgres via `blank_session`, every chain seeded here (CI's database has no data).
"""
from __future__ import annotations

import pytest

from datetime import date, timedelta
from decimal import Decimal

from app.models.project_so import (
    DECISION_ACTIVE,
    DECISION_SUPERSEDED,
    INQUIRY_CANCELLED,
    IV_ORDER,
    IV_ORDER_BACK,
    SO_STATUS_PUBLISHED,
    OrderInquiryLink,
    OrderInquiryRow,
    SOLineAllocation,
    SOSupplyDecision,
)
from app.schemas.project_supply import ConfirmBorrowComponent, ConfirmLine, ConfirmSupplyBody
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
    _po,
    _spo,
)
from .test_project_supply_service_ladder import (
    _agent,
    _group_sites,
    _lead_time,
    _seed_line,
    _world,
)


# ------------------------------------------------------------ (a) re-confirm cleans up


def test_reconfirming_the_same_line_against_a_different_document_cancels_the_stale_placement():
    """PLAN 3.3: "A row raised by an EARLIER revision of this order for the same line is
    CANCELLED first, links and all". Confirming the same line twice, naming a DIFFERENT
    document the second time, must not leave the first document still claimed - the second
    confirm has to come down before the second writes, or the document reads as claimed
    twice and a real re-decision (a different donor picked, a document going stale between
    board loads) leaves a phantom hold behind.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        doc_a = _spo(db, product, other, qty=40, arrives=arrives, spo_number=f"ZZT-SPO-A{_uid()[:4]}")
        doc_b = _spo(db, product, other, qty=40, arrives=arrives, spo_number=f"ZZT-SPO-B{_uid()[:4]}")

        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="40",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )

        def confirm_borrowing(doc, *, revision_hint):
            component = ConfirmBorrowComponent(
                source="other_location",
                warehouse_id=str(other.id),
                qty=Decimal("40"),
                reason=f"Take 40 arriving {arrives.isoformat()} (SPO {doc.spo_number})",
                supply_key=f"spo:{doc.id}",
                supply_document=f"SPO {doc.spo_number}",
                arrival_date=arrives,
            )
            ProjectSupplyService(db).confirm(
                order,
                ConfirmSupplyBody(
                    lines=[ConfirmLine(project_line_id=line.id, borrow=[component])]
                ),
                actor_user_id=eling,
            )
            db.commit()

        confirm_borrowing(doc_a, revision_hint=1)
        confirm_borrowing(doc_b, revision_hint=2)

        rows = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == line.id)
            .all()
        )
        links = db.query(OrderInquiryLink).all()
        doc_a_id = str(doc_a.id)
        doc_b_id = str(doc_b.id)

    active_rows = [r for r in rows if r.state != INQUIRY_CANCELLED]
    cancelled_rows = [r for r in rows if r.state == INQUIRY_CANCELLED]
    assert len(rows) == 2, [(r.state, r.covered_by) for r in rows]
    assert len(active_rows) == 1, "exactly one live placement row for the line"
    assert active_rows[0].covered_by == f"SPO {doc_b.spo_number}"
    assert active_rows[0].verb == IV_ORDER_BACK
    assert len(cancelled_rows) == 1
    assert cancelled_rows[0].covered_by == f"SPO {doc_a.spo_number}", (
        "the FIRST revision's row, not the second's, is the one cancelled"
    )
    # "Unlinked from SPO A; Superseded by revision 2": the retirement goes through
    # `_remove_links` since the S4 fix pass, so the row's own history says which document
    # it was holding as well as what replaced it.
    assert "Superseded by revision" in (cancelled_rows[0].note or "")
    assert "Unlinked from" in (cancelled_rows[0].note or "")
    # The document itself: doc_a holds NO link any more (came down with the stale row),
    # doc_b holds exactly one, for the full 40 - never two rows fighting over one document.
    assert [str(l.spo_allocation_id) for l in links] == [doc_b_id], (
        "doc_a's link must be gone and doc_b's the only one left",
        [(str(l.spo_allocation_id), str(l.qty)) for l in links],
    )
    assert float(links[0].qty) == 40.0


# --------------------------------------------------- (b) one document, two lines, one confirm


@pytest.mark.usefixtures("ladder_steps_on")
def test_two_lines_of_one_confirm_split_one_document_without_double_writing():
    """PLAN 3.2 step 3 / `_check_supply_borrow`'s own docstring: "two lines of one
    confirmation asking for the same document draw it down between them rather than each
    being told it is whole". The coder's `test_two_units_of_one_walk_are_not_both_offered_
    the_same_document` pins this at `compose_lines`'s dict; this pins it through the real
    write (`confirm` -> `_check_supply_borrow` -> `_place_supply_borrows`), where a
    double-credit bug would either refuse the second line outright or write a link that
    over-claims the document.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        other = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        allocation = _spo(db, product, other, qty=50, arrives=arrives)

        core_so = _core_so(db, company_id)
        core_so.so_number = f"ZZTSO-ASK{_uid()[:4]}"
        db.flush()
        order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
        # Two units (different dates, ladder v6), together asking EXACTLY the document's
        # whole balance - the boundary case: neither may be refused, and nothing may be
        # double-counted so the sum could exceed 50.
        line1_core = _core_line(
            db, core_so, product, own, qty_ordered="30",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        line1 = _project_line(db, order, line_no=1, product=product, core_line=line1_core)
        line2_core = _core_line(
            db, core_so, product, own, qty_ordered="20",
            required_date=date.today() + timedelta(days=ASKER_DAY + 1),
        )
        line2 = _project_line(db, order, line_no=2, product=product, core_line=line2_core)
        db.commit()

        _confirm_as_proposed(db, order, eling)

        links = (
            db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.spo_allocation_id == allocation.id)
            .all()
        )
        rows = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id.in_([line1.id, line2.id]))
            .all()
        )

    assert len(links) == 2, [(str(l.row_id), str(l.qty)) for l in links]
    assert sorted(float(l.qty) for l in links) == [20.0, 30.0]
    assert sum(float(l.qty) for l in links) == 50.0, "never more than the document holds"
    assert len(rows) == 2
    assert all(r.covered_by is not None for r in rows)
    assert all(r.state != INQUIRY_CANCELLED for r in rows)


# --------------------------------------------------------- (c) a DECIDED donor's document


@pytest.mark.usefixtures("ladder_steps_on")
def test_a_decided_donors_document_moves_and_its_revision_is_reissued_minus_that_line():
    """PLAN 3.2 step 3's "PINNED ... by a confirmed decision" half, and 3.3's "the donor's
    own placement comes down first ... the donor's ORDER_BACK row raised at its own date".

    The coder's own AC-S4-3 tests build the donor's hold with a bare `OrderInquiryLink`
    (`_donor_holding`), never behind an actual confirmed decision. Here the donor's hold on
    the document is the RESULT of the donor's OWN earlier Confirm (a free step-3 Take,
    landing after ITS OWN required date but still inside R32's window) - so the donor's
    order carries an ACTIVE `SOSupplyDecision` covering TWO lines, only one of which sits
    on the borrowed document. Borrowing it must re-issue that decision keeping the OTHER
    line's own reserve untouched (`_supersede_borrowed_donors` / `_reissue_without_line`),
    and the document's only link must end up on the asker, not still (even partly) on the
    donor's now-superseded placement.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        donor_bin = _warehouse(db, f"ZZTDC1-IR{_uid()[:3]}")
        other = _warehouse(db, f"ZZTDOC-IR{_uid()[:3]}")
        # A SECOND, unrelated group for the donor's other line's on-hand reserve, so it
        # cannot be mistaken for a draw against the asker's own group.
        _group2, sites2 = _group_sites(db)
        reserve_bin, _pool2 = sites2["BRW"]
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        # --- The donor's own order: two lines, confirmed together.
        donor_core_so = _core_so(db, company_id)
        donor_core_so.so_number = f"ZZTSO-DONOR{_uid()[:4]}"
        db.flush()
        donor_order = _project_so(db, project, status=SO_STATUS_PUBLISHED)
        donor_required = date.today() + timedelta(days=DONOR_DAY)

        # Line A: nothing covers it (no group water, no order-borrow, no document eligible
        # for ITS OWN date), so donor's own confirm proposes a plain Buy - an `ORDER`-verb
        # row. `donor_bin` is unrelated to `doc1`'s own location on purpose: this is the
        # OTHER route PLAN 3.2 step 3 names besides a self-borrowed Take - "a confirmed
        # decision" holding the document through an ordinary manual placement (purchasing's
        # Link SPO, run here directly rather than through the route layer, which
        # `_candidates_for_row`'s automatic-cascade timing is not the point of this test).
        donor_a_core = _core_line(
            db, donor_core_so, product, donor_bin, qty_ordered="50",
            required_date=donor_required,
        )
        donor_a_line = _project_line(
            db, donor_order, line_no=1, product=product, core_line=donor_a_core
        )

        # Line B: an ordinary on-hand reserve at an unrelated group, unrelated to the
        # borrow - the line whose cover must survive the re-issue untouched.
        _stock(db, product, reserve_bin, on_hand=15)
        donor_b_core = _core_line(
            db, donor_core_so, product, reserve_bin, qty_ordered="15",
            required_date=donor_required,
        )
        donor_b_line = _project_line(
            db, donor_order, line_no=2, product=product, core_line=donor_b_core
        )
        db.commit()

        _confirm_as_proposed(db, donor_order, eling)

        service = ProjectSupplyService(db)
        donor_decision_1 = service.active_decision(str(donor_order.id))
        assert donor_decision_1 is not None
        assert len(donor_decision_1.line_snapshots or []) == 2, (
            "both donor lines must be in the one active decision",
            donor_decision_1.line_snapshots,
        )
        donor_decision_1_id = donor_decision_1.id
        donor_b_allocations_before = (
            db.query(SOLineAllocation)
            .filter(SOLineAllocation.so_line_id == donor_b_line.id)
            .all()
        )
        assert donor_b_allocations_before, "line B must hold its on-hand reserve"

        # Donor A's own Buy, raised by the confirm's handoff - the row purchasing manually
        # links to a document (`place_on_po_allocations`' MANUAL reading, IV_ORDER is one
        # of `_LINKABLE_VERBS`), exactly as it would on the real worklist.
        donor_a_order_row = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == donor_a_line.id)
            .one()
        )
        assert donor_a_order_row.verb == IV_ORDER
        doc1_arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        allocation = _spo(db, product, other, qty=50, arrives=doc1_arrives)
        ProjectOrderInquiryService(db).place_on_po_allocations(
            str(donor_a_order_row.id),
            [{"spo_allocation_id": str(allocation.id), "qty": Decimal("50")}],
            actor_user_id=eling,
        )
        db.commit()

        # --- The asker: needs the whole of doc1, and donor line A is now an eligible
        # holder of it (window rule, R12/R3: required_date DONOR_DAY is inside the window
        # and short of tba_from) through that placement.
        order, _asker_line, _cso, _asker_core = _seed_line(
            db, company_id, project, product, own, qty_ordered="50",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )
        _confirm_as_proposed(db, order, eling)

        donor_decisions = (
            db.query(SOSupplyDecision)
            .filter(SOSupplyDecision.project_sales_order_id == donor_order.id)
            .all()
        )
        by_id = {str(d.id): d for d in donor_decisions}
        original = by_id[str(donor_decision_1_id)]
        fresh = [d for d in donor_decisions if str(d.id) != str(donor_decision_1_id)]

        links = (
            db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.spo_allocation_id == allocation.id)
            .all()
        )
        donor_b_allocations_after = (
            db.query(SOLineAllocation)
            .filter(SOLineAllocation.so_line_id == donor_b_line.id)
            .all()
        )

    # The ORIGINAL revision falls, naming the asker as the reason.
    assert original.state == DECISION_SUPERSEDED
    assert (original.superseded_reason or "").startswith("Borrowed by SO")
    # A FRESH active revision is issued for what is left of the donor's order (line B).
    assert len(fresh) == 1, [d.state for d in donor_decisions]
    assert fresh[0].state == DECISION_ACTIVE
    reissued_line_ids = {
        str(snap.get("project_line_id"))
        for snap in (fresh[0].line_snapshots or [])
    }
    assert reissued_line_ids == {str(donor_b_line.id)}, (
        "line A (borrowed) must be dropped from the re-issue; line B must survive",
        reissued_line_ids,
    )
    # Line B's own hold is unaffected: the original revision's row for it survives
    # untouched (`_reissue_without_line` copies rather than moves), and a fresh row for
    # the SAME quantity/warehouse is now the one an ACTIVE decision owns.
    fresh_b_rows = [
        a for a in donor_b_allocations_after if str(a.decision_id) == str(fresh[0].id)
    ]
    assert len(fresh_b_rows) == len(donor_b_allocations_before)
    assert sorted(float(a.qty) for a in fresh_b_rows) == sorted(
        float(a.qty) for a in donor_b_allocations_before
    )

    # The document has moved: exactly ONE link on it, naming the ASKER's own row.
    assert len(links) == 1, [(str(l.row_id), str(l.qty)) for l in links]
    assert float(links[0].qty) == 50.0


# --------------------------------------------------- (d) manual reading, group in deficit


@pytest.mark.usefixtures("ladder_steps_on")
def test_the_placement_still_writes_when_the_askers_own_group_is_in_deficit():
    """PLAN 3.3's closing paragraph: the link the AUTOMATIC step-3 reading writes on
    Confirm is not gated by ladder v4's group-deficit rule (`_groups_in_deficit`) -
    "a group in deficit is the ordinary case for the very unit this step is borrowing for".

    Document swapped PO -> SPO 31 Aug (`PLAN-scm-planning-feedback-31aug.md` S1, R-A): a PO
    is no longer offered by step 3 at all, so the donor-held document a deficit-group asker
    can still borrow through the AUTOMATIC reading is an SPO. Unlike a PO, an open SPO
    counts in `group_net` regardless of who holds it, so the fixture's deficit assertion
    reads the net as computed rather than adding the document's qty back by hand.
    """
    with blank_session() as db:
        company_id, eling, project, product = _world(db)
        _group, sites = _group_sites(db)
        own, _pool = sites["BRW"]
        # A SIBLING SITE of the asker's OWN ownership group (`group_of_warehouse_code`
        # reads the suffix, which both sites share), not an ungrouped warehouse: the
        # deficit gate keys off the GROUP, and `_groups_in_deficit` skips a document whose
        # warehouse carries no group at all (every OTHER S4 fixture's `other`/`donor_bin`
        # is ungrouped for exactly that reason, so none of them exercises this gate).
        donor_bin, _pool2 = sites["MWH"]
        # The PO fixture this test used to seed a `ProductSupplier` lead time as a side
        # effect (`_po`'s own `lead_days`); the SPO fixture does not, so it is set
        # explicitly - without it the product falls back to the 90-day default lead,
        # which pushes the donor's window past `DONOR_DAY` and makes it ineligible.
        _lead_time(db, product, LEAD_DAYS)
        _policy(db)

        # A later order at the SAME group HOLDS the SPO through a placement, its own
        # SO_QTY counting against the group same as the asker's.
        arrives = date.today() + timedelta(days=LATE_ARRIVAL_DAY)
        allocation = _spo(db, product, donor_bin, qty=30, arrives=arrives)
        agent = _agent(db, f"ZZTDEF{_uid()[:4]}")
        donor_so, _donor_core, _donor_mirror, _link = _donor_holding(
            db, company_id, project, product, donor_bin, qty=30, days=DONOR_DAY,
            actor=eling, allocation=allocation,
            so_number=f"ZZTSO-DEFICIT{_uid()[:4]}", agent_id=agent.id,
        )

        order, line, _cso, _cline = _seed_line(
            db, company_id, project, product, own, qty_ordered="30",
            required_date=date.today() + timedelta(days=ASKER_DAY),
        )

        from app.services.scm.group_netting import netting_for_products

        net_before = netting_for_products(db, [str(product.id)]).group_net(
            str(product.id), _group
        ).net
        assert net_before < 0, (
            "fixture must actually put the group in deficit before asserting the write "
            "survives it",
            net_before,
        )

        _confirm_as_proposed(db, order, eling)

        links = (
            db.query(OrderInquiryLink)
            .filter(OrderInquiryLink.spo_allocation_id == allocation.id)
            .all()
        )
        rows = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == line.id)
            .all()
        )

    assert len(links) == 1, "the placement must write despite the group's own deficit"
    assert float(links[0].qty) == 30.0
    assert any(r.covered_by for r in rows)
