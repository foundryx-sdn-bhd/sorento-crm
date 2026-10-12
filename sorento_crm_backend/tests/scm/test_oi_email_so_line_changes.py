"""EMAIL-SO-CHANGES (owner, 12 Oct 2026): the order inquiry email prints a line's Was -> Now
off the change itself, never off the action label.

Real miss, SO400236 (UNIBASE TRADING SDN BHD (PROJECT), FARMFIELD / JB): line SRTWB246,
BRW-BB, qty 1, its one row linked to SPO-2026/05-0037, which had been received in full. The
book moved the line 01/10/2026 -> 02/11/2026. The Order Inquiries Was/Now dialog showed the
move (it reads the fresh row's `previous_qty` / `previous_delivery_date`), the email printed
DELIVERY 02/11/2026 with an empty NEW DATE cell.

Why: `_settle_row_in_place` declines a row whose link is fully received
(`_redirect_row_if_received`), the netting raises a fresh ORDER row that carries the old date
on `previous_delivery_date` ("Replaces 1 used"), the S2 join suppresses the line's DELAY row,
and `refresh_for_decision` records that fresh row as a plain `raised` line with `was=None`. The date move
reached the row and never reached the email.

Same harness as `tests/scm/test_oi_handover_line_total.py`.
"""
from __future__ import annotations

from decimal import Decimal

from app.models.project_so import (
    INQUIRY_CANCELLED,
    IV_CANCEL_BALANCE,
    IV_ORDER,
    OrderInquiryLink,
)

from ..test_order_inquiry_handover_automation import (
    _captured_dispatches,
    _handover_calls,
    _register,
)
from ..test_planning_change_apply_on_board import NOW, WAS_1, _rows_of, _uid, api, world
from .test_oi_date_move_settle import _one_row_fixture
from .test_oi_handover_line_total import _apply_change, _line_entries

__all__ = ["api", "world"]  # re-exported fixtures

WAS_FMT = WAS_1.strftime("%d/%m/%Y")
NOW_FMT = NOW.strftime("%d/%m/%Y")


def _srtwb246_shape(api):
    """One line, qty 1, its one row linked in full to a FULLY RECEIVED SPO allocation."""
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService
    from tests.scm._own_arrival_fixture import spo_allocation_fully_received

    _client, world = api
    db = world.db
    fixture = _one_row_fixture(api, qty="1")
    row = fixture["row"]
    spo = spo_allocation_fully_received(
        db, world.product, world.own_wh, qty=1, from_po_number=f"ZZT-PO-{_uid()[:8]}"
    )
    db.add(
        OrderInquiryLink(
            id=_uid(), company_id=world.company_id, row_id=row.id,
            spo_allocation_id=spo.id, document=spo.spo_number, qty=Decimal("1"),
        )
    )
    db.flush()
    ProjectOrderInquiryService(db).refresh_link_state([row])
    db.commit()
    return fixture


def test_redirected_line_prints_its_date_move_as_was_now(api, monkeypatch):
    """SO400236 / SRTWB246: a date-only move on a line whose one row was used elsewhere
    prints DELIVERY = the old date and NEW DATE = the new one, on the fresh ORDER line."""
    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _srtwb246_shape(api)
    line, row = fixture["line"], fixture["row"]
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], line,
        old_date=WAS_1, new_date=NOW, qty="1",
    )

    world.db.expire_all()
    world.db.refresh(row)
    assert row.redirected_to_pool is True, "the received link sends the row to the pool"
    fresh = [
        r for r in _rows_of(world, line)
        if r.state != INQUIRY_CANCELLED and r.id != row.id and r.verb == IV_ORDER
    ]
    assert len(fresh) == 1, fresh
    # The in-app Was/Now (Order Inquiries dialog) already reads this.
    assert fresh[0].previous_delivery_date == WAS_1
    assert fresh[0].delivery_date == NOW

    entries = _line_entries(calls, row.item_code)
    assert len(entries) == 1, f"one line for one SO line: {entries}"
    entry = entries[0]
    assert entry["delivery_date"] == NOW_FMT, entry
    assert (entry["was"] or {}).get("delivery_date") == WAS_FMT, (
        f"the email must carry the date move the row carries: {entry}"
    )
    # Same quantity before and after: no NEW QTY for it.
    assert "qty" not in (entry["was"] or {}), entry
    # The move names its verb in the headline (the line's own DELAY/ADVANCE row is
    # suppressed for a redirected line). NOW is earlier than WAS_1: ADVANCE.
    assert NOW < WAS_1
    assert "ADVANCE" in _handover_calls(calls)[-1]["context"]["handover"]["headline"]


def test_redirected_line_prints_its_qty_move_too(api, monkeypatch):
    """Owner rule 2 covers QTY as well: the same SRTWB246 shape, the book also moves the
    line 1 -> 3. The fresh row carries `previous_qty` 1 and asks for 3."""
    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _srtwb246_shape(api)
    line, row = fixture["line"], fixture["row"]
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], line,
        old_date=WAS_1, new_date=NOW, qty="1", new_qty="3",
    )

    world.db.expire_all()
    fresh = [
        r for r in _rows_of(world, line)
        if r.state != INQUIRY_CANCELLED and r.id != row.id and r.verb == IV_ORDER
    ]
    assert len(fresh) == 1 and Decimal(str(fresh[0].qty)) == Decimal("3"), fresh
    assert Decimal(str(fresh[0].previous_qty)) == Decimal("1"), fresh[0].previous_qty

    entries = _line_entries(calls, row.item_code)
    assert len(entries) == 1, entries
    assert entries[0]["qty"] == "3", entries[0]
    assert entries[0]["was"] == {"qty": "1", "delivery_date": WAS_FMT}, entries[0]


def test_a_note_alone_is_never_parsed_into_a_was(api, monkeypatch):
    """Crew audit (2): before values never come from note text. A raised CANCEL BALANCE
    queued with no structured before/after prints its own quantity, whatever its note
    says; the writers pass `placed`/`need`, the delta's from/to, or the batch row's
    from/to qty instead (`tests/test_oi_email_qty_moves.py`)."""
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _one_row_fixture(api, qty="4")
    row = fixture["row"]
    row.verb = IV_CANCEL_BALANCE
    row.note = "Placed 10, new need 6; Was 10, now 6"
    world.db.flush()

    ProjectOrderInquiryService(world.db)._record_handover(
        row, kind="raised", actor_user_id=world.actor
    )
    world.db.commit()

    line = _handover_calls(calls)[-1]["context"]["handover"]["lines"][-1]
    assert line["qty"] == "4" and line["was"] is None, line


def test_book_change_qty_down_hands_its_from_and_to_qty_to_the_email(api):
    """Crew answer (b): the planning-change batch row's own from/to quantity (the "What
    changed" source) is what `derive_for_book_change` prints as QTY / NEW QTY."""
    from types import SimpleNamespace

    from app.services import planning_change_service

    fixture = _one_row_fixture(api, qty="10")
    row = fixture["row"]
    live_rows = [
        SimpleNamespace(
            kind="qty_down",
            project_line_id=str(fixture["line"].id),
            core_line_id=str(fixture["core"].id),
            item_code=row.item_code,
            from_json={"qty": "10", "required_date": WAS_1.isoformat()},
            to_json={"qty": "6", "required_date": WAS_1.isoformat()},
            held_json={},
        )
    ]
    demand_rows, _counts = planning_change_service._oi_demand_rows(
        fixture["world"].db, live_rows, fixture["core_so"].so_number, [],
        order_inquiry_id=str(row.order_inquiry_id),
    )
    assert len(demand_rows) == 1, demand_rows
    assert demand_rows[0]["qty"] == Decimal("4")
    assert (demand_rows[0]["from_qty"], demand_rows[0]["to_qty"]) == (Decimal("10"), Decimal("6"))


def _so400236_dev_shape(api):
    """SO400236 SRTWB246 as it stands on the shared dev DB (crew extract, 12 Oct): one
    ORDER row of 1, PLACED, linked to an SPO allocation off a PO bought FOR this line and
    received in full INTO the line's own location, with the goods on hand there. The own-
    arrival credit then covers the row (no redirect) and the re-run meets the line from
    that stock, so the apply's settle reduces the buy to nothing."""
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    from ..test_planning_changes import _stock
    from ._own_arrival_fixture import po_line_bought_for, supplier_and_po

    _client, world = api
    db = world.db
    fixture = _one_row_fixture(api, qty="1")
    row = fixture["row"]
    fixture["core"].source_ref = f"ZZT-L-{_uid()[:8]}"
    po = supplier_and_po(db)
    _po_line, spo = po_line_bought_for(
        db, po, world.product, world.own_wh, from_so_line_ref=fixture["core"].source_ref,
        qty_received=1, spo_number=f"ZZT-SPO-{_uid()[:6]}",
    )
    db.add(
        OrderInquiryLink(
            id=_uid(), company_id=world.company_id, row_id=row.id,
            spo_allocation_id=spo.id, document=spo.spo_number, qty=Decimal("1"),
        )
    )
    db.flush()
    ProjectOrderInquiryService(db).refresh_link_state([row])
    _stock(db, world.product, world.own_wh, on_hand=1)
    db.commit()
    return fixture


def test_dev_shape_date_move_prints_delay_never_a_cancel_to_zero(api, monkeypatch):
    """Crew audit of 6872c888 (preview on dev): SO400236 SRTWB246 rendered CANCEL BALANCE,
    `1 | 0 | 01/10/2026`, no NEW DATE. The book moved only the date; the email must say
    DELIVERY old -> NEW DATE new with DELAY, the quantity unchanged."""
    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _so400236_dev_shape(api)
    row = fixture["row"]
    later = WAS_1.replace(month=WAS_1.month + 2)
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], fixture["line"],
        old_date=WAS_1, new_date=later, qty="1",
    )

    entries = _line_entries(calls, row.item_code)
    assert len(entries) == 1, entries
    assert entries[0]["qty"] == "1", f"never a cancel to 0: {entries[0]}"
    assert entries[0]["delivery_date"] == later.strftime("%d/%m/%Y"), entries[0]
    assert entries[0]["was"] == {"delivery_date": WAS_FMT}, entries[0]
    headline = _handover_calls(calls)[-1]["context"]["handover"]["headline"]
    assert "DELAY" in headline and "CANCEL" not in headline, headline


def test_redirected_and_met_from_stock_delay_notice_carries_its_was(api, monkeypatch):
    """The other stock-met shape: the row's received link is NOT the line's own arrival
    (redirected to the pool) and free stock meets the line, so no buy row is raised and
    the apply's DELAY notice row is the line's only email line. It prints DELIVERY old ->
    NEW DATE new off the batch row's own from date."""
    from ..test_planning_changes import _stock

    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _srtwb246_shape(api)
    _stock(world.db, world.product, world.own_wh, on_hand=1)
    world.db.commit()
    later = WAS_1.replace(month=WAS_1.month + 2)
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], fixture["line"],
        old_date=WAS_1, new_date=later, qty="1",
    )

    # Every line of the mail: the notice row carries the batch row's own item code (the
    # harness's `_change` names "ZZT-ITEM"), not the product's.
    entries = _handover_calls(calls)[-1]["context"]["handover"]["lines"]
    dated = [e for e in entries if (e["was"] or {}).get("delivery_date")]
    assert dated, f"the date move must reach the email: {entries}"
    assert dated[0]["was"]["delivery_date"] == WAS_FMT, dated[0]
    assert dated[0]["delivery_date"] == later.strftime("%d/%m/%Y"), dated[0]
    assert not any(e["qty"] == "0" for e in entries), entries


def test_qty_up_met_from_stock_prints_its_numbers_but_never_order(api, monkeypatch):
    """Reviewer B1 (dev-shape round): the book moves the line 1 -> 2 and later, landed
    stock meets all of it, so nothing is bought. The email prints QTY 1 / NEW QTY 2 and
    the date move, with DELAY in the headline and never ORDER."""
    from app.models.inventory import Stock

    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _so400236_dev_shape(api)
    stock = world.db.query(Stock).filter(
        Stock.product_id == world.product.id, Stock.warehouse_id == world.own_wh.id
    ).one()
    stock.quantity_on_hand = 2
    world.db.commit()
    later = WAS_1.replace(month=WAS_1.month + 2)
    calls.clear()

    _apply_change(
        world, fixture["core"], fixture["order"], fixture["core_so"], fixture["line"],
        old_date=WAS_1, new_date=later, qty="1", new_qty="2",
    )

    live = [r for r in _rows_of(world, fixture["line"]) if r.state != INQUIRY_CANCELLED]
    assert not [r for r in live if r.verb == IV_ORDER], "sanity: stock meets it, no buy"
    entries = _line_entries(calls, fixture["row"].item_code)
    assert len(entries) == 1, entries
    assert entries[0]["qty"] == "2", entries[0]
    assert entries[0]["was"] == {"qty": "1", "delivery_date": WAS_FMT}, entries[0]
    headline = _handover_calls(calls)[-1]["context"]["handover"]["headline"]
    assert "DELAY" in headline and "ORDER" not in headline, headline
