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

import pytest

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


@pytest.mark.parametrize(
    "note, was_qty, now_qty",
    [
        ("Placed 10, new need 6", "10", "6"),
        ("Was 280, now 0", "280", "0"),
        ("Superseded by revision 3; Was 12.5, now 4", "12.5", "4"),
    ],
)
def test_raised_cancel_balance_prints_the_lines_before_and_after(
    api, monkeypatch, note, was_qty, now_qty
):
    """Reviewer B1: with REMARK gone, a raised CANCEL BALANCE row printing only its own
    quantity (the drop) reads like an order for it beside a real ORDER line. It prints
    the line's before and after, off the stamp its writer put on it (the confirm's
    exception row, an SO amendment's or a book change's qty decrease)."""
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _one_row_fixture(api, qty="4")
    row = fixture["row"]
    row.verb = IV_CANCEL_BALANCE
    row.note = note
    world.db.flush()

    ProjectOrderInquiryService(world.db)._record_handover(
        row, kind="raised", actor_user_id=world.actor
    )
    world.db.commit()

    line = _handover_calls(calls)[-1]["context"]["handover"]["lines"][-1]
    assert line["qty"] == now_qty, line
    assert line["was"] == {"qty": was_qty}, line


def test_raised_cancel_balance_without_a_stamp_is_left_as_it_was(api, monkeypatch):
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fixture = _one_row_fixture(api, qty="4")
    row = fixture["row"]
    row.verb = IV_CANCEL_BALANCE
    row.note = "Confirmed by WhatsApp"
    world.db.flush()

    ProjectOrderInquiryService(world.db)._record_handover(
        row, kind="raised", actor_user_id=world.actor
    )
    world.db.commit()

    line = _handover_calls(calls)[-1]["context"]["handover"]["lines"][-1]
    assert line["qty"] == "4" and line["was"] is None, line


def test_redirected_line_with_a_product_swap_prints_item_code_and_new_item_code(api, monkeypatch):
    """Owner, 12 Oct: the item code can change too, and the email must show it - off the
    same OI before values (`previous_item_code`), including the redirected / fresh-row path
    that hid SRTWB246's date. Rendered through the no-REMARK document."""
    from app.models.procurement import PurchaseOrderLine

    from ..test_oi_email_no_remark import ORDER, _line_table, _render
    from ..test_oi_email_print import _ctx
    from ..test_oi_product_follow_apply import _apply, _swap_fixture

    _client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    fx = _swap_fixture(api, linked=True, acknowledged=True)
    po_line = world.db.get(PurchaseOrderLine, fx["link"].po_line_id)
    po_line.qty_received = po_line.qty_ordered
    world.db.commit()
    calls.clear()

    response = _apply(fx)
    assert response.status_code == 200, response.text
    world.db.commit()
    world.db.expire_all()

    old, new = fx["old"].product_code, fx["new"].product_code
    fresh = [
        r for r in _rows_of(world, fx["line"])
        if r.verb == IV_ORDER and r.state != INQUIRY_CANCELLED and not r.redirected_to_pool
    ]
    assert len(fresh) == 1 and fresh[0].previous_item_code == old, fresh

    entries = _line_entries(calls, new)
    assert len(entries) == 1, entries
    assert (entries[0]["was"] or {}).get("item_code") == old, entries[0]
    assert "qty" not in (entries[0]["was"] or {}), "same quantity: no NEW QTY"
    assert "delivery_date" not in (entries[0]["was"] or {}), "same date: no NEW DATE"

    body = _line_table(_render(_ctx(ORDER, entries)))
    assert body[0] == ["LOCATION", "ITEM CODE", "NEW ITEM CODE", "QTY", "DELIVERY"], body
    assert body[1][1:3] == [old, new], body
