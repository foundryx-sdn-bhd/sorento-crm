"""#1362: the own-arrival credit ledger is per (product, bin), never per bin alone.

Owner (29 Sep 2026): SO382618's SRT357 line, 100 landed for it on SPO-2026/06-0152 at
BRW-BB (on hand 261), was credited 40 "landed for this line" and the rest sent to the BRW
pool, then the confirm guard refused the line on that same 40. Another product's credit at
the same bin had eaten SRT357's own-arrival allowance.

The reproduction: ONE sales order, TWO products, each with its own PO line converted to an
SPO and fully received at the SAME bin, both lines open, walked in the board's own order.
The second product must be credited min(its landed, its need, its own on hand), whatever
the first product's credit drew off the bin. A competing order of each product drives the
group net far below zero, so nothing but the credit can cover either line: any shortfall in
the credit shows up as a Buy.

Postgres only (`tests/_pg_fixture.py`), every FK seeded here, never a borrowed row.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests._pg_fixture import blank_session
from tests.test_fulfilment_board import TODAY, _cell, _line, _product, _service, _uid
from tests.test_fulfilment_board import _stock as _board_stock
from tests.scm._own_arrival_fixture import (
    order_with_lines,
    own_arrival_group,
    own_arrival_warehouse,
    po_line_bought_for,
    supplier_and_po,
)

FIRST_REQUIRED = date(2026, 8, 20)
SECOND_REQUIRED = date(2026, 8, 27)


def _own_arrival_reserved(contribution: dict) -> Decimal:
    return sum(
        (
            Decimal(s["qty"])
            for s in contribution["sources"]
            if s.get("rung") == "group_take" and s.get("source") == "own_arrival"
        ),
        Decimal("0"),
    )


def _buy_qty(contribution: dict) -> Decimal:
    return sum(
        (Decimal(s["qty"]) for s in contribution["sources"] if s.get("kind") == "buy"),
        Decimal("0"),
    )


def _own_step(contribution: dict) -> dict:
    return next(s for s in contribution["trail"] if s["kind"] == "own")


def _two_products_one_bin(db):
    """Product A: on hand 150, line 150 due first, 150 landed for it. Product B: on hand
    100, line 100 due a week later, 100 landed for it on its own SPO. Same order, same bin.
    Walking A first, a bin-keyed ledger leaves B with nothing (or at most B's on hand less
    A's 150)."""
    group, product_a = own_arrival_group(db)
    product_b = _product(db, f"ZZT-{_uid()[:8]}")
    own = own_arrival_warehouse(db, group)
    _board_stock(db, product_a, own, on_hand=150)
    _board_stock(db, product_b, own, on_hand=100)
    order, (line_a,) = order_with_lines(
        db, product=product_a, warehouse=own,
        lines=[{"qty": "150", "required_date": FIRST_REQUIRED, "source_ref": "LA"}],
    )
    line_b = _line(
        db, order, product_b, qty="100", required_date=SECOND_REQUIRED, warehouse=own,
    )
    line_b.source_ref = "LB"
    db.flush()
    po_a = supplier_and_po(db, po_number="ZZT-PO-1362A")
    po_line_bought_for(
        db, po_a, product_a, own, from_so_line_ref="LA", qty_received=150,
        spo_number="SPO-2026/06-0151",
    )
    po_b = supplier_and_po(db, po_number="ZZT-PO-1362B")
    po_line_bought_for(
        db, po_b, product_b, own, from_so_line_ref="LB", qty_received=100,
        spo_number="SPO-2026/06-0152",
    )
    # Competing earlier demand of BOTH products at the same bin: each group net goes far
    # negative, so the ordinary group take offers nothing and only the credit can cover.
    for product in (product_a, product_b):
        order_with_lines(
            db, product=product, warehouse=own,
            lines=[{"qty": "5000", "required_date": date(2026, 4, 1), "source_ref": "OTHER"}],
        )
    return order, product_a, product_b, own


def test_1362_second_product_at_the_same_bin_is_credited_its_own_landed_goods_in_full():
    """#1362 regression pin: product B is credited min(100 landed, 100 needed, 100 on hand)
    = 100, not what product A's 150 credit left of a bin-wide counter."""
    with blank_session() as db:
        order, product_a, product_b, own = _two_products_one_bin(db)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        first = _cell(board, product_a.product_code, "2026-08-17")["contributions"][0]
        assert _own_arrival_reserved(first) == Decimal("150"), first["sources"]

        second = _cell(board, product_b.product_code, "2026-08-24")["contributions"][0]
        assert _own_arrival_reserved(second) == Decimal("100"), second["sources"]
        assert _buy_qty(second) == Decimal("0"), second["sources"]


def test_1362_the_sentence_states_what_landed_and_what_is_free_apart():
    """#1362 sentence: what landed on the shipment and how much of it is still free at the
    bin and taken first are two facts, said apart."""
    with blank_session() as db:
        order, _product_a, product_b, own = _two_products_one_bin(db)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        second = _cell(board, product_b.product_code, "2026-08-24")["contributions"][0]
        why = _own_step(second)["why"]
        assert (
            f"100 landed for this line on SPO-2026/06-0152; 100 free at "
            f"{own.warehouse_code}, taken first." in why
        ), why


def test_1362_free_part_smaller_than_landed_says_both_numbers():
    """When the bin holds less of the product than landed for the line, the sentence says
    both: 100 landed, 60 free, 60 taken first."""
    with blank_session() as db:
        group, product = own_arrival_group(db)
        own = own_arrival_warehouse(db, group)
        _board_stock(db, product, own, on_hand=60)
        order, _lines = order_with_lines(
            db, product=product, warehouse=own,
            lines=[{"qty": "100", "required_date": FIRST_REQUIRED, "source_ref": "LX"}],
        )
        po = supplier_and_po(db, po_number="ZZT-PO-1362X")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref="LX", qty_received=100,
            spo_number="SPO-2026/06-0153",
        )
        order_with_lines(
            db, product=product, warehouse=own,
            lines=[{"qty": "5000", "required_date": date(2026, 4, 1), "source_ref": "OTHER"}],
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        contribution = _cell(board, product.product_code, "2026-08-17")["contributions"][0]
        assert _own_arrival_reserved(contribution) == Decimal("60"), contribution["sources"]
        why = _own_step(contribution)["why"]
        assert (
            f"100 landed for this line on SPO-2026/06-0153; 60 free at "
            f"{own.warehouse_code}, taken first." in why
        ), why


# ============================================================================
# #1362 item 2 (owner, 29 Sep 2026): SO382618 line 400 read "on SPO-2026/06-0044" while
# its order-inquiry rows link SPO-2026/06-0092 and SPO-2026/07-0019. The PO line's
# receipts were summed across every SPO row but only the FIRST one found was named.
# ============================================================================

from datetime import timedelta  # noqa: E402

import pytest  # noqa: E402

from app.models.project_so import (  # noqa: E402
    INQUIRY_PLACED,
    IV_ORDER_BACK,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    SOSupplyDecisionDraft,
)
from app.schemas.project_supply import ConfirmLine, ConfirmSupplyBody  # noqa: E402
from app.services.error_handler import AppException  # noqa: E402
from app.services.project_supply_service import ProjectSupplyService  # noqa: E402
from tests.scm.test_project_supply_service_ladder import _world  # noqa: E402
from tests.test_so_supply_confirmation import (  # noqa: E402
    _core_line,
    _core_so,
    _project_line,
    _project_so,
    _stock,
    _warehouse,
)


def _second_shipment(db, spo, *, spo_number: str, received: int):
    """Another SPO row landing the SAME PO line (`from_po_line_ref`), the way a PO line
    received across two shipping orders looks."""
    from app.models.procurement import SPOAllocation

    row = SPOAllocation(
        id=_uid(), spo_number=spo_number, spo_line_number=int(spo.spo_line_number) + 5000,
        product_id=spo.product_id, warehouse_id=spo.warehouse_id,
        location_code=spo.location_code, allocated_quantity=received,
        quantity_received=received, receipt_status="fully_received", line_status="closed",
        from_po_line_ref=spo.from_po_line_ref, from_po_number=spo.from_po_number,
        company_id=spo.company_id,
    )
    db.add(row)
    db.flush()
    return row


def _confirm_world(db, *, need: str, on_hand: int):
    within_window = date.today() + timedelta(days=10)
    company_id, actor, project, product = _world(db)
    own = _warehouse(db, f"ZZT-OWN-{_uid()[:4]}")
    _stock(db, product, own, on_hand=on_hand)
    core_so = _core_so(db, company_id)
    core_line = _core_line(
        db, core_so, product, own, qty_ordered=need, required_date=within_window,
    )
    core_line.source_ref = f"ZZT-1362-{_uid()[:8]}"
    db.flush()
    order = _project_so(db, project, so_id=core_so.id)
    line = _project_line(db, order, line_no=1, product=product, core_line=core_line)
    return actor, product, own, core_so, core_line, order, line


def _landed_notice(db, order, line, actor, buy: str) -> dict:
    """#1362 (owner, 29 Sep 2026): a Buy over goods that landed for the line is confirmed as
    decided, with a notice naming what landed (it used to be refused, AC-S3-15)."""
    result = ProjectSupplyService(db).confirm(
        order,
        ConfirmSupplyBody(lines=[ConfirmLine(project_line_id=str(line.id), buy_qty=buy)]),
        actor_user_id=actor,
    )
    assert result["revision_no"] is not None, result
    notices = result.get("landed_buy_notices") or []
    assert len(notices) == 1, result
    return notices[0]


def _refusal(db, order, line, actor, buy: str) -> str:
    """What landed, as the confirm now says it (the notice's `landed` phrase)."""
    return _landed_notice(db, order, line, actor, buy)["landed"]


def test_1362_item2_a_po_line_landed_on_two_shipments_names_both_with_their_quantities():
    """No order inquiry: the PO line bought for the line landed 60 on SPO-2026/06-0092
    and 40 on SPO-2026/07-0019. The refusal names BOTH, each with what it landed - it
    used to name whichever row the query returned first."""
    with blank_session() as db:
        actor, product, own, _so, core_line, order, line = _confirm_world(
            db, need="100", on_hand=100
        )
        po = supplier_and_po(db, po_number=f"ZZT-PO-1362-2A-{_uid()[:6]}")
        _po_line, spo = po_line_bought_for(
            db, po, product, own, from_so_line_ref=core_line.source_ref,
            qty_received=100, qty_ordered=100, spo_received=60,
            spo_number="SPO-2026/06-0092",
        )
        _second_shipment(db, spo, spo_number="SPO-2026/07-0019", received=40)
        db.commit()

        message = _refusal(db, order, line, actor, buy="100")
        assert message == (
            "100 landed for this line: 60 on SPO-2026/06-0092, 40 on SPO-2026/07-0019"
        ), message


def test_1362_item2_the_order_inquiry_placement_names_its_own_shipments():
    """SO382618 line 400's shape: the PO line bought for the line landed 100 on
    SPO-2026/06-0044, but the line's order-inquiry row places it 60 on SPO-2026/06-0092 and
    40 on SPO-2026/07-0019, both received. The placement is the answer: the refusal names
    0092 and 0019 with their quantities - never 0044."""
    with blank_session() as db:
        actor, product, own, _so, core_line, order, line = _confirm_world(
            db, need="100", on_hand=100
        )
        po = supplier_and_po(db, po_number=f"ZZT-PO-1362-2B-{_uid()[:6]}")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref=core_line.source_ref,
            qty_received=100, qty_ordered=100, spo_number="SPO-2026/06-0044",
        )
        inquiry = OrderInquiry(id=_uid(), project_sales_order_id=order.id)
        db.add(inquiry)
        db.flush()
        for placed, spo_number in ((60, "SPO-2026/06-0092"), (40, "SPO-2026/07-0019")):
            other_po = supplier_and_po(db, po_number=f"ZZT-PO-1362-2C-{_uid()[:6]}")
            _other_line, linked_spo = po_line_bought_for(
                db, other_po, product, own, from_so_line_ref=f"ZZT-ELSE-{_uid()[:6]}",
                qty_received=placed, qty_ordered=placed, spo_number=spo_number,
            )
            row = OrderInquiryRow(
                id=_uid(), order_inquiry_id=inquiry.id, so_line_id=line.id,
                item_code=product.product_code, qty=Decimal(placed), verb=IV_ORDER_BACK,
                state=INQUIRY_PLACED,
            )
            db.add(row)
            db.flush()
            db.add(
                OrderInquiryLink(
                    id=_uid(), row_id=row.id, spo_allocation_id=linked_spo.id,
                    document=linked_spo.spo_number, qty=Decimal(placed),
                )
            )
        db.commit()

        message = _refusal(db, order, line, actor, buy="100")
        assert message == (
            "100 landed for this line: 60 on SPO-2026/06-0092, 40 on SPO-2026/07-0019"
        ), message


# ============================================================================
# #1362 item 3 (owner, 29 Sep 2026): SO382618's SRTWCY7604-WEPLS line due 08/06 had 0
# to plan and a saved "Buy 100". Confirm-all still sent that composition and the R7 guard
# refused the WHOLE order.
# ============================================================================


def test_1362_item3_a_fulfilled_line_with_a_stale_decision_does_not_block_the_order():
    """A decided line, then fulfilled to zero open (`qty_required = 0`, 100 delivered),
    beside an ordinary open line. Confirm naming BOTH - the stale "Buy 100" included -
    succeeds: the fulfilled line is skipped, counted, and its saved decision cleared."""
    with blank_session() as db:
        actor, product, own, core_so, done_core, order, done_line = _confirm_world(
            db, need="100", on_hand=100
        )
        po = supplier_and_po(db, po_number=f"ZZT-PO-1362-3-{_uid()[:6]}")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref=done_core.source_ref,
            qty_received=100, qty_ordered=100, spo_number="SPO-2026/06-0044",
        )
        done_core.qty_delivered = Decimal("100")
        done_core.qty_required = Decimal("0")
        db.add(
            SOSupplyDecisionDraft(
                id=_uid(), sales_order_id=core_so.id, core_line_id=done_core.id,
                company_id=done_core.company_id, line_no=1,
                item_code=product.product_code, bucket_key="2026-06-08",
                decision={"verdict": "amended", "buy": "100"},
            )
        )
        open_core = _core_line(
            db, core_so, product, own, qty_ordered="5",
            required_date=date.today() + timedelta(days=20),
        )
        open_line = _project_line(db, order, line_no=2, product=product, core_line=open_core)
        db.commit()

        result = ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(project_line_id=str(done_line.id), buy_qty="100"),
                    ConfirmLine(project_line_id=str(open_line.id), buy_qty="5"),
                ]
            ),
            actor_user_id=actor,
        )
        assert result["lines_fulfilled_skipped"] == 1, result
        assert result["revision_no"] is not None, result
        left = (
            db.query(SOSupplyDecisionDraft)
            .filter(SOSupplyDecisionDraft.core_line_id == done_core.id)
            .count()
        )
        assert left == 0


def test_1362_item3_my_line_finds_a_fulfilled_line_in_the_stock_drawer():
    """The stock drawer opened for a delivered line: the ledger lists open claims only, so
    "My line" found nothing. The asking line is now listed, marked as this line, at zero,
    saying what was delivered - and it is not counted into SO Qty."""
    with blank_session() as db:
        group, product = own_arrival_group(db)
        own = own_arrival_warehouse(db, group)
        _board_stock(db, product, own, on_hand=100)
        order, (done, _open) = order_with_lines(
            db, product=product, warehouse=own,
            lines=[
                {"qty": "100", "required_date": date(2026, 6, 8), "source_ref": "LD",
                 "delivered": "100"},
                {"qty": "30", "required_date": FIRST_REQUIRED, "source_ref": "LO"},
            ],
        )

        detail = _service(db).stock_detail(
            str(product.id), str(own.id), line_ids=[str(done.id)]
        )

        mine = [row for row in detail["sales_orders"] if row["is_this_line"]]
        assert len(mine) == 1, detail["sales_orders"]
        assert mine[0]["line_id"] == str(done.id)
        assert mine[0]["fulfilled_qty"] == "100", mine[0]
        assert mine[0]["so_qty"] == "0", mine[0]
        assert detail["so_qty"] == "30", detail["so_qty"]


def test_the_stock_drawer_leaves_an_autocount_cancel_out_of_fulfilled():
    """AC-CANCEL-CRM (review M15): TransferedQty 100 of which 40 is AutoCount Cancel SO. The
    drawer's "My line" says 60 went out, not 100."""
    with blank_session() as db:
        group, product = own_arrival_group(db)
        own = own_arrival_warehouse(db, group)
        _board_stock(db, product, own, on_hand=100)
        order, (done, _open) = order_with_lines(
            db, product=product, warehouse=own,
            lines=[
                {"qty": "100", "required_date": date(2026, 6, 8), "source_ref": "LD",
                 "delivered": "100"},
                {"qty": "30", "required_date": FIRST_REQUIRED, "source_ref": "LO"},
            ],
        )
        done.qty_cancelled = 40
        db.flush()

        detail = _service(db).stock_detail(
            str(product.id), str(own.id), line_ids=[str(done.id)]
        )

        mine = [row for row in detail["sales_orders"] if row["is_this_line"]]
        assert mine[0]["fulfilled_qty"] == "60", mine[0]


# ============================================================================
# #1362 fix round 4 (owner evidence, 29 Sep 2026). SO382618: PO 202607-S0077's B2154-NL
# line bought 200 for SO line 1648 (from_so_line_ref = line 1648's source_ref 43495333,
# 100 pieces, due 19/10/2026) and landed all 200 on SPO-2026/09-0036. Line 2912 (the
# January line, planning row 110, no order inquiry) is credited the 100 surplus as tier-2
# sibling spare, and the board said "100 landed for this line" of it.
# ============================================================================

SO382618_1648_REF = "AED_SORENTO:43494637:43495333"
SO382618_2912_REF = "AED_SORENTO:43494637:43495999"


def _so382618_shape(db, *, unnumbered: bool = False):
    """Two open B2154-NL-shaped lines of one order at one bin: line 1648 (100, due first)
    and line 2912 (100, due a week later). One PO line references line 1648 and landed
    200 on SPO-2026/09-0036: 200 bought for 100. Competing earlier demand drives the group
    net far below zero, so only the credit covers either line.

    `unnumbered` adds the owner's unnumbered 200-piece line (no AutoCount No.), which is
    what drops the board's planning numbering to positional 1..n."""
    group, product = own_arrival_group(db)
    own = own_arrival_warehouse(db, group)
    _board_stock(db, product, own, on_hand=400)
    specs = [
        {"qty": "100", "required_date": FIRST_REQUIRED, "source_ref": SO382618_1648_REF},
        {"qty": "100", "required_date": SECOND_REQUIRED, "source_ref": SO382618_2912_REF},
    ]
    if unnumbered:
        specs.append(
            {"qty": "200", "required_date": date(2026, 9, 14), "source_ref": "AED_X:1"}
        )
    order, lines = order_with_lines(db, product=product, warehouse=own, lines=specs)
    lines[0].line_no = 1648
    lines[1].line_no = 2912
    db.flush()
    po = supplier_and_po(db, po_number=f"202607-S0077-{_uid()[:4]}")
    po_line_bought_for(
        db, po, product, own, from_so_line_ref=SO382618_1648_REF, qty_received=200,
        spo_number="SPO-2026/09-0036",
    )
    order_with_lines(
        db, product=product, warehouse=own,
        lines=[{"qty": "5000", "required_date": date(2026, 4, 1), "source_ref": "OTHER"}],
    )
    return order, product, own, lines


def test_1362_item4_the_spare_of_an_over_buy_names_the_line_it_was_bought_for():
    """Line 1648 reads its own tier-1 credit; line 2912 reads the surplus as line 1648's
    spare, 200 bought for 100 - never "landed for this line"."""
    with blank_session() as db:
        order, product, own, _lines = _so382618_shape(db)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        first = _cell(board, product.product_code, "2026-08-17")["contributions"][0]
        assert _own_arrival_reserved(first) == Decimal("100"), first["sources"]
        assert (
            f"100 landed for this line on SPO-2026/09-0036; 100 free at "
            f"{own.warehouse_code}, taken first." in _own_step(first)["why"]
        ), _own_step(first)["why"]

        second = _cell(board, product.product_code, "2026-08-24")["contributions"][0]
        assert _own_arrival_reserved(second) == Decimal("100"), second["sources"]
        why = _own_step(second)["why"]
        assert (
            f"100 spare from line 1648's purchase (200 bought for 100) landed on "
            f"SPO-2026/09-0036; 100 free at {own.warehouse_code}, taken first." in why
        ), why
        assert "landed for this line" not in why, why
        # The component's own reason (what the drawer's Suggestion card prints) says the
        # same thing as the trail.
        reasons = [
            s.get("reason") or "" for s in second["sources"] if s.get("source") == "own_arrival"
        ]
        assert any("spare from line 1648's purchase" in r for r in reasons), second["sources"]
        # And the source dict carries it for the amend refusal.
        texts = [s.get("landed_text") for s in second["sources"] if s.get("source") == "own_arrival"]
        assert texts and texts[0].startswith("100 spare from line 1648's purchase"), texts


def test_1362_item4_the_confirm_refusal_on_a_spare_names_the_line_it_was_bought_for():
    """The confirm recheck's R7 refusal on line 2912 says the same thing as the board."""
    with blank_session() as db:
        within_window = date.today() + timedelta(days=10)
        company_id, actor, project, product = _world(db)
        own = _warehouse(db, f"ZZT-OWN-{_uid()[:4]}")
        _stock(db, product, own, on_hand=400)
        core_so = _core_so(db, company_id)
        bought_for = _core_line(
            db, core_so, product, own, qty_ordered="100", required_date=within_window,
        )
        bought_for.source_ref = f"ZZT-1648-{_uid()[:8]}"
        bought_for.line_no = 1648
        january = _core_line(
            db, core_so, product, own, qty_ordered="100",
            required_date=within_window + timedelta(days=5),
        )
        january.source_ref = f"ZZT-2912-{_uid()[:8]}"
        january.line_no = 2912
        db.flush()
        order = _project_so(db, project, so_id=core_so.id)
        _project_line(db, order, line_no=1, product=product, core_line=bought_for)
        line = _project_line(db, order, line_no=2, product=product, core_line=january)
        po = supplier_and_po(db, po_number=f"ZZT-PO-1362-4-{_uid()[:6]}")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref=bought_for.source_ref,
            qty_received=200, qty_ordered=200, spo_number="SPO-2026/09-0036",
        )
        db.commit()

        message = _refusal(db, order, line, actor, buy="100")
        assert message == (
            "100 spare from line 1648's purchase (200 bought for 100) landed on "
            "SPO-2026/09-0036"
        ), message


# ---------------------------------------------------------------------------- item 5


def test_1362_item5_the_board_carries_the_autocount_line_number_beside_its_address():
    """An unnumbered line on the order drops the board's planning numbering to positional
    1..n; `so_line_no` is still AutoCount's own number, the one the screen prints."""
    with blank_session() as db:
        order, product, _own, lines = _so382618_shape(db, unnumbered=True)

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        by_line = {
            c["line_id"]: c for cell in board["cells"] for c in cell["contributions"]
        }
        first, january = by_line[str(lines[0].id)], by_line[str(lines[1].id)]
        assert first["so_line_no"] == 1648, first
        assert january["so_line_no"] == 2912, january
        # The address is the positional number, and it differs: that is the mix-up.
        assert january["line_no"] != 2912, january
        assert by_line[str(lines[2].id)]["so_line_no"] is None


def test_1362_item5_a_confirm_refusal_names_the_autocount_line_number():
    """The confirm's per-line notice carries AutoCount's No. (2912), not the project line's
    positional number (1), so the board can say "Line 2912, ...". (It was a refusal until
    the owner's 29 Sep ruling made a Buy over landed goods a notice.)"""
    with blank_session() as db:
        actor, product, own, _so, core_line, order, line = _confirm_world(
            db, need="100", on_hand=100
        )
        core_line.line_no = 2912
        po = supplier_and_po(db, po_number=f"ZZT-PO-1362-5-{_uid()[:6]}")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref=core_line.source_ref,
            qty_received=100, qty_ordered=100, spo_number="SPO-2026/09-0036",
        )
        db.commit()

        notice = _landed_notice(db, order, line, actor, buy="100")
        assert notice["so_line_no"] == 2912, notice
        assert notice["line_no"] == 1, notice


# ---------------------------------------------------------------------------- item 6


def test_1362_item6_same_date_lines_do_not_both_read_the_first_lines_purchase():
    """Round 1's finding. Two lines of one product due the SAME day form one planning
    unit. Line A (100) has a PO that landed 100 for it; line B (60) has none, and A's PO
    bought exactly A's need, so there is no spare. Each member used to read the unit's
    FIRST core line for tier 1, so B was credited A's receipt too: 100 landed became 160
    credited. B must be credited nothing and buy its 60."""
    with blank_session() as db:
        group, product = own_arrival_group(db)
        own = own_arrival_warehouse(db, group)
        _board_stock(db, product, own, on_hand=160)
        order, (line_a, line_b) = order_with_lines(
            db, product=product, warehouse=own,
            lines=[
                {"qty": "100", "required_date": FIRST_REQUIRED, "source_ref": "L6A"},
                {"qty": "60", "required_date": FIRST_REQUIRED, "source_ref": "L6B"},
            ],
        )
        po = supplier_and_po(db, po_number="ZZT-PO-1362-6")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref="L6A", qty_received=100,
            spo_number="SPO-2026/06-0160",
        )
        order_with_lines(
            db, product=product, warehouse=own,
            lines=[{"qty": "5000", "required_date": date(2026, 4, 1), "source_ref": "OTHER"}],
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        by_line = {
            c["line_id"]: c
            for c in _cell(board, product.product_code, "2026-08-17")["contributions"]
        }
        a, b = by_line[str(line_a.id)], by_line[str(line_b.id)]
        credited = _own_arrival_reserved(a) + _own_arrival_reserved(b)
        assert credited == Decimal("100"), (a["sources"], b["sources"])
        assert _own_arrival_reserved(a) == Decimal("100"), a["sources"]
        assert _own_arrival_reserved(b) == Decimal("0"), b["sources"]
        assert _buy_qty(b) == Decimal("60"), b["sources"]


# ============================================================================
# #1362 round 5, owner ruling (29 Sep 2026): "we cannot snatch, what's ordered against the
# SO should stay belonged to it". Goods that landed on a PO bought for an SO line belong to
# that line: an earlier-due line of the same product at the same bin may NOT draw them,
# and every other line draws only the truly free stock (on hand less what landed for other
# lines and is still owed to them). AC-S3-11 changes accordingly.
#
# The SRT357 shape: 261 on hand at the bin, 100 landed for the later line on its own
# purchase, an earlier line of the same product with no purchase of its own asking 250.
# The earlier line used to take 250 through the ordinary queue, leaving the later line 11
# of its own 100 (the owner saw 40 of 100 on the live book, with 221 taken).
# ============================================================================

from app.models.project_so import IV_ORDER  # noqa: E402
from app.schemas.project_supply import ConfirmReserveComponent  # noqa: E402
from app.services.project_order_inquiry_service import (  # noqa: E402
    ProjectOrderInquiryService,
)


def _reserved_at(contribution: dict, code: str) -> Decimal:
    return sum(
        (
            Decimal(s["qty"])
            for s in contribution["sources"]
            if s.get("kind") == "reserve" and s.get("location") == code
            and s.get("source") != "own_arrival"
        ),
        Decimal("0"),
    )


def test_1362_round5_an_earlier_line_may_not_draw_goods_landed_for_a_later_line():
    """Board walk: the earlier line may draw only the 161 truly free, which cannot meet its
    250 whole, so it buys; it no longer takes the later line's goods. The later line keeps
    all 100 that landed for it, taken first, and buys nothing."""
    with blank_session() as db:
        group, product = own_arrival_group(db)
        own = own_arrival_warehouse(db, group)
        _board_stock(db, product, own, on_hand=261)
        order, (_early, _later) = order_with_lines(
            db, product=product, warehouse=own,
            lines=[
                {"qty": "250", "required_date": FIRST_REQUIRED, "source_ref": "R5E"},
                {"qty": "100", "required_date": SECOND_REQUIRED, "source_ref": "R5L"},
            ],
        )
        po = supplier_and_po(db, po_number="ZZT-PO-1362-R5")
        po_line_bought_for(
            db, po, product, own, from_so_line_ref="R5L", qty_received=100,
            spo_number="SPO-2026/06-0152",
        )

        board = _service(db).build([order.so_number], granularity="week", as_of=TODAY)

        later = _cell(board, product.product_code, "2026-08-24")["contributions"][0]
        assert _own_arrival_reserved(later) == Decimal("100"), later["sources"]
        assert _buy_qty(later) == Decimal("0"), later["sources"]
        assert (
            f"100 landed for this line on SPO-2026/06-0152; 100 free at "
            f"{own.warehouse_code}, taken first." in _own_step(later)["why"]
        ), _own_step(later)["why"]

        early = _cell(board, product.product_code, "2026-08-17")["contributions"][0]
        assert _reserved_at(early, own.warehouse_code) == Decimal("0"), early["sources"]
        assert _buy_qty(early) == Decimal("250"), early["sources"]


def _ruling_confirm_world(db):
    within_window = date.today() + timedelta(days=10)
    company_id, actor, project, product = _world(db)
    own = _warehouse(db, f"ZZT-OWN-{_uid()[:4]}")
    _stock(db, product, own, on_hand=261)
    core_so = _core_so(db, company_id)
    early_core = _core_line(
        db, core_so, product, own, qty_ordered="250", required_date=within_window,
    )
    early_core.source_ref = f"ZZT-R5E-{_uid()[:8]}"
    later_core = _core_line(
        db, core_so, product, own, qty_ordered="100",
        required_date=within_window + timedelta(days=7),
    )
    later_core.source_ref = f"ZZT-R5L-{_uid()[:8]}"
    db.flush()
    order = _project_so(db, project, so_id=core_so.id)
    early = _project_line(db, order, line_no=1, product=product, core_line=early_core)
    later = _project_line(db, order, line_no=2, product=product, core_line=later_core)
    po = supplier_and_po(db, po_number=f"ZZT-PO-1362-R5C-{_uid()[:6]}")
    po_line_bought_for(
        db, po, product, own, from_so_line_ref=later_core.source_ref,
        qty_received=100, qty_ordered=100, spo_number="SPO-2026/06-0152",
    )
    db.commit()
    return actor, company_id, project, product, own, order, early, later


def test_1362_round5_the_confirm_recheck_holds_only_the_free_part_and_spares_the_landed_goods():
    """Confirm recheck: the earlier line reserving 250 (what the old walk proposed) would
    take 89 of the 100 that landed for the later line. It confirms as decided, holds only the
    161 that are free and records 89 short; the landed 100 stay untouched for the later line.

    Owner rule 12 Oct 2026 (OI-CLOSED-LINE, #1580): a stock shortfall is a warning, never a
    refusal. Before it this was refused outright; #1580 restated its siblings and missed this
    one, which then read DID NOT RAISE on main."""
    from app.models.project_so import SOLineAllocation

    with blank_session() as db:
        actor, _c, _p, _product, own, order, early, later = _ruling_confirm_world(db)

        result = ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=str(early.id),
                        reserve=[
                            ConfirmReserveComponent(warehouse_id=str(own.id), qty="250")
                        ],
                    )
                ]
            ),
            actor_user_id=actor,
        )

        notices = result.get("stock_short_notices") or []
        assert len(notices) == 1, result
        assert "only 161 free at" in notices[0]["reason"], notices
        assert ", 89 short" in notices[0]["reason"], notices
        db.expire_all()
        held = {
            row.so_line_id: row.qty
            for row in db.query(SOLineAllocation).filter(
                SOLineAllocation.so_line_id.in_([early.id, later.id])
            )
        }
        assert held == {early.id: Decimal("161")}, held


def test_1362_round5_the_confirm_recheck_accepts_the_free_part_and_the_landed_line_in_full():
    """The ruling's answer confirms: the earlier line buys its 250 whole (161 free cannot
    meet it), the later line reserves all 100 that landed for it."""
    with blank_session() as db:
        actor, _c, _p, _product, own, order, early, later = _ruling_confirm_world(db)

        result = ProjectSupplyService(db).confirm(
            order,
            ConfirmSupplyBody(
                lines=[
                    ConfirmLine(
                        project_line_id=str(early.id),
                        buy_qty="250",
                    ),
                    ConfirmLine(
                        project_line_id=str(later.id),
                        reserve=[ConfirmReserveComponent(warehouse_id=str(own.id), qty="100")],
                    ),
                ]
            ),
            actor_user_id=actor,
        )
        assert result["revision_no"] is not None, result


def test_1362_round5_the_order_inquiry_credits_the_landed_line_in_full():
    """Order inquiry: the path picker's credit for the later line's row is the whole 100,
    whatever the earlier line of the same product at the same bin asks. Its ledger is
    spent by credits only, never by an ordinary draw, so this reader already honoured the
    ruling; pinned so it keeps doing so."""
    with blank_session() as db:
        actor, company_id, _p, _product, own, order, _early, later = _ruling_confirm_world(db)
        inquiry = OrderInquiry(
            id=_uid(), company_id=company_id, project_sales_order_id=order.id,
            amendment_id=None, state="raised", raised_by=actor,
        )
        db.add(inquiry)
        db.flush()
        row = OrderInquiryRow(
            id=_uid(), company_id=company_id, order_inquiry_id=inquiry.id,
            so_line_id=later.id, qty=Decimal("100"), verb=IV_ORDER, state=INQUIRY_PLACED,
            stock_location=own.warehouse_code,
        )
        db.add(row)
        db.commit()

        credit = ProjectOrderInquiryService(db)._own_arrival_credit_for_row(
            row, need=Decimal("100")
        )
        assert credit == Decimal("100"), credit


# ============================================================================
# #1362 hand test (owner, 29 Sep 2026): "Confirmed 0 orders; 1 refused" because ONE line
# carried a saved Buy over goods that landed for it. Owner: "i think we are too
# restrictive already". One line's stale or conflicting decision must not refuse the
# whole order: confirm-all holds the refused line back and confirms the rest.
# ============================================================================

from app.schemas.project_supply import ConfirmManyOrderBody  # noqa: E402


def test_1362_confirm_all_holds_back_a_refused_line_and_confirms_the_rest():
    with blank_session() as db:
        actor, _c, _p, product, own, order, early, later = _ruling_confirm_world(db)
        later_core = db.get(type(later), later.id).core_sales_order_line_id

        results = ProjectSupplyService(db).confirm_many(
            [
                ConfirmManyOrderBody(
                    pso_id=str(order.id),
                    lines=[
                        # A saved decision that no longer adds up to the line (50 of 100).
                        ConfirmLine(project_line_id=str(later.id), buy_qty="50"),
                        ConfirmLine(project_line_id=str(early.id), buy_qty="250"),
                    ],
                )
            ],
            actor_user_id=actor,
            assert_can_act=lambda _session, _order: None,
        )

        (result,) = results
        assert result["ok"] is True, result
        assert result["decision_revision"] is not None, result
        held = result["lines_held_back"]
        assert held and len(held) == 1, result
        assert held[0]["line_no"] == 2, held
        assert "add up to 50" in held[0]["reason"], held
        assert "project_line_id" not in held[0], held
        assert later_core


def test_1362_confirm_all_still_refuses_when_every_named_line_is_refused():
    with blank_session() as db:
        actor, _c, _p, _product, _own, order, _early, later = _ruling_confirm_world(db)

        (result,) = ProjectSupplyService(db).confirm_many(
            [
                ConfirmManyOrderBody(
                    pso_id=str(order.id),
                    lines=[ConfirmLine(project_line_id=str(later.id), buy_qty="50")],
                )
            ],
            actor_user_id=actor,
            assert_can_act=lambda _session, _order: None,
        )
        assert result["ok"] is False, result
        assert result["failing_lines"], result


def test_1362_a_buy_over_landed_goods_is_confirmed_and_purchasing_is_told_on_the_row():
    """Owner (29 Sep 2026): "this good is on hand, and is covering the line, but, from
    fulfilment planning, is kind of requesting it to be delayed while the link is intact,
    then only purchasing will do the adjustment in the linkage". The hand test's row 29: a
    saved Buy 100 over 100 landed for the line. Confirm succeeds with the Buy as decided,
    the PO link is untouched, and the Buy's own order inquiry row (born awaiting, so a
    buyer must acknowledge it) carries the landed fact for purchasing."""
    with blank_session() as db:
        actor, product, own, _so, core_line, order, line = _confirm_world(
            db, need="100", on_hand=100
        )
        po = supplier_and_po(db, po_number=f"ZZT-PO-1362-HT-{_uid()[:6]}")
        po_line, _spo = po_line_bought_for(
            db, po, product, own, from_so_line_ref=core_line.source_ref,
            qty_received=100, qty_ordered=100, spo_number="SPO-2026/06-0131",
        )
        db.commit()

        notice = _landed_notice(db, order, line, actor, buy="100")
        assert notice["reason"] == (
            "Buy 100 confirmed as decided; 100 landed for this line on SPO-2026/06-0131 "
            "stay linked to it, for purchasing to adjust"
        ), notice
        db.expire_all()
        assert db.get(type(po_line), po_line.id).from_so_line_ref == core_line.source_ref
        rows = (
            db.query(OrderInquiryRow)
            .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.state != "cancelled")
            .all()
        )
        notes = [row.note or "" for row in rows]
        assert any(
            "Planning keeps this Buy: 100 landed for this line on SPO-2026/06-0131 stay "
            "linked to this line; adjust the linkage if the Buy replaces them" in note
            for note in notes
        ), notes
