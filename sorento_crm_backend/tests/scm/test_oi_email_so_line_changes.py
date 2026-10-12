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
and `_write` records that fresh row as a plain `raised` line with `was=None`. The date move
reached the row and never reached the email.

Same harness as `tests/scm/test_oi_handover_line_total.py`.
"""
from __future__ import annotations

from decimal import Decimal

from app.models.project_so import INQUIRY_CANCELLED, IV_ORDER, OrderInquiryLink

from ..test_order_inquiry_handover_automation import _captured_dispatches, _register
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
