"""SO-CANCEL-HANDOVER: Transfer to Cancel SO hands over to purchasing like cancel balance
(backend, Phase 2 red tests).

Contract: `documentation/plans/scm/so-cancel-handover-acceptance-criteria.md` (AC-H1 to AC-H14).

TEST-FIRST. Behaviour is driven through the HTTP confirm routes of SO-CANCEL-LINE; the
handover is observed by monkeypatching `AutomationService.dispatch_event` (the seam
`_fire_pending_handover` / `_fire_pending_changed_with_links` call after the root commit),
the same way `tests/test_order_inquiry_handover_automation.py` does.

Today `cancel_lines` only flips an acknowledged row to `ack_state = changed`; it never
cancels the row, never unlinks it and never queues a handover line. Status today:
  RED  : H1 H2 H3 H4 H5 H9 H10 H11
  GREEN today (guards): H6 H7 H8 H12 H13
Fixture shape = `tests/test_so_cancel_line.py::_cb_world` (fake ids only).
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.models.procurement import SPOAllocation
from app.models.project_so import (
    ACK_ACKNOWLEDGED,
    ACK_AWAITING,
    ACK_CHANGED,
    INQUIRY_ACTIONED,
    INQUIRY_CANCELLED,
    INQUIRY_PARTLY_LINKED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_CANCEL_BALANCE,
    OrderInquiryLink,
    OrderInquirySuggestedLink,
)
from app.services.project_order_inquiry_service import (
    _HANDOVER_VERB_LABEL,
    flag_rows_for_cancelled_lines,
)

from .test_oi_cancelled_line_used_confirm import _inquiry, _oi_row, _uid
from .test_order_inquiry_handover_automation import (
    TRIGGER,
    _captured_dispatches,
    _handover_calls,
    _register,
)
from .test_so_cancel_line import (
    REASON,
    _cancel_body,
    _cb_world,
    _confirm_all,
    _status,
)
from .test_so_supply_confirmation import (  # noqa: F401 - `api` is a fixture
    BASE,
    _core_line,
    _line_payload,
    _project_line,
    _product,
    api,
)

CHANGED_WITH_LINKS = "order_inquiry_changed_with_links"
DELIVERY = date(2026, 11, 3)
LOCATION = "ZZT-LOC-BRW"


def _cancel_press(client, w, *mirrors, reason=REASON, lines=None):
    return client.post(
        f"{BASE}/sales-orders/{w['order'].id}/confirm",
        json=_cancel_body(*mirrors, reason=reason, lines=lines),
    )


def _world_with_row(api, monkeypatch, **row_kwargs):
    """A cancel world plus one live ORDER row of 36 on line A, dispatch capture armed."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    db = world.db
    # The handover prints the project SO's own `autocount_doc_no`, as in prod.
    w["order"].autocount_doc_no = w["core_so"].so_number
    inquiry = _inquiry(db, world.company_id, w["order"].id)
    row = _oi_row(
        db, world.company_id, inquiry.id, mirror_a.id, product_a.product_code, "36",
        delivery_date=DELIVERY, **row_kwargs,
    )
    row.stock_location = LOCATION
    row.note = "ZZT earlier note"
    db.commit()
    calls.clear()
    return client, world, w, row, calls


def _reload(db, *rows):
    db.expire_all()
    for row in rows:
        db.refresh(row)


def _spo_link(world, row, qty="20"):
    db = world.db
    product = world.product
    allocation = SPOAllocation(
        id=_uid(), company_id=world.company_id, spo_number=f"ZZT-SPO-{_uid()[:8]}",
        spo_line_number=1, product_id=product.id, warehouse_id=world.own_wh.id,
        allocated_quantity=Decimal("40"),
    )
    db.add(allocation)
    db.flush()
    link = OrderInquiryLink(
        id=_uid(), company_id=world.company_id, row_id=row.id,
        spo_allocation_id=allocation.id, document=allocation.spo_number, qty=Decimal(qty),
    )
    db.add(link)
    db.flush()
    db.commit()
    return link


def _link_count(db, row):
    db.expire_all()
    return db.query(OrderInquiryLink).filter(OrderInquiryLink.row_id == row.id).count()



def _cancel_only(calls):
    """Exactly one handover dispatch, every line of it a plain CANCEL."""
    matches = _handover_calls(calls)
    return len(matches) == 1 and all(
        line["remark"] == "CANCEL" for line in matches[0]["context"]["handover"]["lines"]
    )

def _assert_cancel_balance_line(line, row):
    # ORDERBACK-DELTA (owner, 13 Oct 2026 22:40): "cancel -> CANCEL", one line per line.
    assert line["remark"] == "CANCEL"
    assert line["qty"] == "0"  # QTY CHANGE TO 0, same as the cancel balance route
    assert line["was"]["qty"] == "36"
    assert line["item_code"] == row.item_code
    # The line describes the SO LINE (its own date and location, owner 13 Oct 2026), not
    # the row's: `tests/test_oi_saved_line_email.py` pins those.


# ---------------------------------------------------------------- AC-H1, H2, H3


def test_h1_cancel_cancels_an_acknowledged_row_and_keeps_the_note(api, monkeypatch):
    """AC-H1 [RED]: row stays raised/changed today."""
    client, world, w, row, _calls = _world_with_row(api, monkeypatch)
    assert row.ack_state == ACK_ACKNOWLEDGED

    response = _cancel_press(client, w, w["a"][1])

    assert response.status_code == 200, response.text
    _reload(world.db, row)
    assert row.state == INQUIRY_CANCELLED
    assert row.note.startswith("ZZT earlier note")
    assert row.note.endswith(f"Transferred to Cancel SO: {REASON}")


def test_h2_one_handover_dispatch_with_a_cancel_balance_line(api, monkeypatch):
    """AC-H2 [RED]: no handover is queued today."""
    client, world, w, row, calls = _world_with_row(api, monkeypatch)

    response = _cancel_press(client, w, w["a"][1])

    assert response.status_code == 200, response.text
    matches = _handover_calls(calls)
    assert len(matches) == 1, f"expected one {TRIGGER} dispatch, got {len(matches)}"
    handover = matches[0]["context"]["handover"]
    assert handover["line_count"] == 1
    assert handover["verbs"] == [_HANDOVER_VERB_LABEL[IV_CANCEL_BALANCE]]
    assert handover["headline"] == "CANCEL BALANCE"
    assert [o["so_number"] for o in handover["orders"]] == [
        handover["lines"][0]["so_number"]
    ]
    assert handover["lines"][0]["so_number"] == w["core_so"].so_number
    _assert_cancel_balance_line(handover["lines"][0], row)
    assert REASON not in handover["lines"][0]["remark"]


def test_h3_an_unacknowledged_row_is_cancelled_and_handed_over(api, monkeypatch):
    """AC-H3 [RED]: an awaiting row is left alone today."""
    client, world, w, row, calls = _world_with_row(api, monkeypatch, ack_state=ACK_AWAITING)

    response = _cancel_press(client, w, w["a"][1])

    assert response.status_code == 200, response.text
    _reload(world.db, row)
    assert row.state == INQUIRY_CANCELLED
    matches = _handover_calls(calls)
    assert len(matches) == 1
    _assert_cancel_balance_line(matches[0]["context"]["handover"]["lines"][0], row)


# ---------------------------------------------------------------- AC-H4, H5


def test_h4_a_linked_row_is_unlinked_cancelled_and_handed_over(api, monkeypatch):
    """AC-H4 [RED]: links stay, row stays partly_linked, no dispatches today."""
    client, world, w, row, calls = _world_with_row(api, monkeypatch)
    row.state = INQUIRY_PARTLY_LINKED
    _spo_link(world, row)
    assert _link_count(world.db, row) == 1
    calls.clear()

    response = _cancel_press(client, w, w["a"][1])

    assert response.status_code == 200, response.text
    assert _link_count(world.db, row) == 0
    _reload(world.db, row)
    assert row.state == INQUIRY_CANCELLED
    with_links = [c for c in calls if c["trigger_type"] == CHANGED_WITH_LINKS]
    assert len(with_links) == 1, [c["trigger_type"] for c in calls]
    matches = _handover_calls(calls)
    assert len(matches) == 1
    _assert_cancel_balance_line(matches[0]["context"]["handover"]["lines"][0], row)


def test_h5_a_placed_row_with_no_link_is_cancelled_without_changed_with_links(
    api, monkeypatch
):
    """AC-H5 [RED]: row stays placed, no handover today."""
    client, world, w, row, calls = _world_with_row(api, monkeypatch)
    row.state = INQUIRY_PLACED
    world.db.commit()
    assert _link_count(world.db, row) == 0

    response = _cancel_press(client, w, w["a"][1])

    assert response.status_code == 200, response.text
    _reload(world.db, row)
    assert row.state == INQUIRY_CANCELLED
    assert not [c for c in calls if c["trigger_type"] == CHANGED_WITH_LINKS]
    matches = _handover_calls(calls)
    assert len(matches) == 1
    _assert_cancel_balance_line(matches[0]["context"]["handover"]["lines"][0], row)


# ---------------------------------------------------------------- AC-H6, H7, H8


def test_h6_an_actioned_row_is_not_cancelled_unlinked_or_handed_over(api, monkeypatch):
    """AC-H6 [GUARD, passes today]."""
    client, world, w, row, calls = _world_with_row(api, monkeypatch, state=INQUIRY_ACTIONED)
    link = _spo_link(world, row)
    calls.clear()

    response = _cancel_press(client, w, w["a"][1])

    assert response.status_code == 200, response.text
    _reload(world.db, row)
    assert row.state == INQUIRY_ACTIONED
    assert _link_count(world.db, row) == 1
    # ORDERBACK-DELTA (owner, 13 Oct 2026 22:40): every saved decision is one line, so the
    # Cancel SO press still tells purchasing CANCEL even with no live row left to cancel.
    assert _cancel_only(calls)
    assert not [c for c in calls if c["trigger_type"] == CHANGED_WITH_LINKS]
    assert link.row_id == row.id


def test_h7_cancelled_and_cancel_balance_exception_rows_are_left_alone(api, monkeypatch):
    """AC-H7 [GUARD, passes today]."""
    client, world, w, row, calls = _world_with_row(api, monkeypatch, state=INQUIRY_CANCELLED)
    db = world.db
    product_a = w["a"][2]
    exception = _oi_row(
        db, world.company_id, row.order_inquiry_id, w["a"][1].id, product_a.product_code, "9",
        ack_state=ACK_AWAITING,
    )
    exception.verb = IV_CANCEL_BALANCE
    db.commit()
    calls.clear()

    response = _cancel_press(client, w, w["a"][1])

    assert response.status_code == 200, response.text
    _reload(db, row, exception)
    assert row.state == INQUIRY_CANCELLED
    assert exception.state == INQUIRY_RAISED
    assert exception.ack_state == ACK_AWAITING
    # ORDERBACK-DELTA (owner, 13 Oct 2026 22:40): every saved decision is one line, so the
    # Cancel SO press still tells purchasing CANCEL even with no live row left to cancel.
    assert _cancel_only(calls)


def test_h8_a_cancelled_line_with_no_live_row_still_reads_cancel(api, monkeypatch):
    """AC-H8 [GUARD, passes today]."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    w = _cb_world(world)
    calls.clear()

    response = _cancel_press(client, w, w["a"][1])

    assert response.status_code == 200, response.text
    assert _status(world.db, w["a"][0].id) == "cancelled"
    # ORDERBACK-DELTA (owner, 13 Oct 2026 22:40): every saved decision is one line, so the
    # Cancel SO press still tells purchasing CANCEL even with no live row left to cancel.
    assert _cancel_only(calls)


# ---------------------------------------------------------------- AC-H9, H10, H11


def test_h9_three_cancelled_lines_make_one_dispatch_with_three_lines(api, monkeypatch):
    """AC-H9 [RED]."""
    client, world = api
    _register(world)
    calls = _captured_dispatches(monkeypatch)
    w = _cb_world(world)
    db = world.db
    core_so, order = w["core_so"], w["order"]
    third_product = _product(db)
    third_core = _core_line(
        db, core_so, third_product, world.own_wh, qty_ordered="36", qty_delivered="36"
    )
    third_core.line_status = "closed"
    third_mirror = _project_line(
        db, order, line_no=40, product=third_product, core_line=third_core
    )
    inquiry = _inquiry(db, world.company_id, order.id)
    mirrors = [w["a"], w["b"], (third_core, third_mirror, third_product)]
    for _core, mirror, product in mirrors:
        _oi_row(db, world.company_id, inquiry.id, mirror.id, product.product_code, "36")
    db.commit()
    calls.clear()

    response = _cancel_press(client, w, *[m for _c, m, _p in mirrors])

    assert response.status_code == 200, response.text
    matches = _handover_calls(calls)
    assert len(matches) == 1, f"expected one {TRIGGER} dispatch, got {len(matches)}"
    handover = matches[0]["context"]["handover"]
    assert handover["line_count"] == 3
    assert {line["remark"] for line in handover["lines"]} == {"CANCEL"}
    assert {line["item_code"] for line in handover["lines"]} == {
        p.product_code for _c, _m, p in mirrors
    }


def test_h10_a_mixed_press_hands_over_the_cancel_beside_the_confirm(api, monkeypatch):
    """AC-H10 [RED]: the cancel contributes no line today."""
    client, world, w, row, calls = _world_with_row(api, monkeypatch)
    _open_core, open_line = w["open"]

    response = _cancel_press(
        client, w, w["a"][1], lines=[_line_payload(open_line.id, buy_qty="5")]
    )

    assert response.status_code == 200, response.text
    matches = _handover_calls(calls)
    assert len(matches) == 1, f"expected one {TRIGGER} dispatch, got {len(matches)}"
    lines = matches[0]["context"]["handover"]["lines"]
    assert len(lines) >= 2, lines
    assert [ln["remark"] for ln in lines].count("CANCEL") == 1


def test_h11_confirm_all_behaves_the_same(api, monkeypatch):
    """AC-H11 [RED]."""
    client, world, w, row, calls = _world_with_row(api, monkeypatch)

    result = _confirm_all(client, w["order"], **_cancel_body(w["a"][1]))

    assert result["ok"] is True, result
    _reload(world.db, row)
    assert row.state == INQUIRY_CANCELLED
    assert row.note.endswith(f"Transferred to Cancel SO: {REASON}")
    matches = _handover_calls(calls)
    assert len(matches) == 1
    _assert_cancel_balance_line(matches[0]["context"]["handover"]["lines"][0], row)


# ---------------------------------------------------------------- AC-H12, H13


def test_h12_a_refused_press_cancels_no_row_and_dispatches_nothing(api, monkeypatch):
    """AC-H12 [GUARD, passes today]: blank reason (422) and a second press naming an
    already cancelled line beside a fresh one (409) both leave the rows untouched."""
    client, world, w, row, calls = _world_with_row(api, monkeypatch)
    core_b, mirror_b, product_b = w["b"]
    db = world.db
    inquiry_id = row.order_inquiry_id
    row_b = _oi_row(db, world.company_id, inquiry_id, mirror_b.id, product_b.product_code, "36")
    db.commit()
    calls.clear()

    blank = _cancel_press(client, w, w["a"][1], reason="  ")
    assert blank.status_code == 422, blank.text
    _reload(db, row, row_b)
    assert row.state == INQUIRY_RAISED
    assert not _handover_calls(calls)

    first = _cancel_press(client, w, w["a"][1])
    assert first.status_code == 200, first.text
    calls.clear()
    again = _cancel_press(client, w, w["a"][1], reason="a different reason")
    assert again.status_code == 409, again.text
    mixed = _cancel_press(client, w, w["a"][1], mirror_b)
    assert mixed.status_code == 409, mixed.text
    _reload(db, row_b)
    assert row_b.state == INQUIRY_RAISED
    assert not _handover_calls(calls)


def test_h13_the_ingest_flag_still_flips_an_acknowledged_row_to_changed(api):
    """AC-H13 [GUARD, passes today]: `flag_rows_for_cancelled_lines` (the AutoCount ingest
    callers) keeps its behaviour; nothing here pushes to AutoCount."""
    client, world = api
    w = _cb_world(world)
    core_a, mirror_a, product_a = w["a"]
    db = world.db
    inquiry = _inquiry(db, world.company_id, w["order"].id)
    row = _oi_row(db, world.company_id, inquiry.id, mirror_a.id, product_a.product_code, "36")
    db.commit()

    flagged = flag_rows_for_cancelled_lines(db, [core_a.id])
    db.commit()

    assert flagged == 1
    _reload(db, row)
    assert row.ack_state == ACK_CHANGED
    assert row.changed_at is not None
    assert row.state == INQUIRY_RAISED


# ---------------------------------------------------------------- reviewer guards


def test_h15_a_pool_redirected_row_is_left_as_it_is(api, monkeypatch):
    """Guards the `redirected_to_pool` filter [passes with the fix]."""
    client, world, w, row, calls = _world_with_row(api, monkeypatch, redirected=True)

    response = _cancel_press(client, w, w["a"][1])

    assert response.status_code == 200, response.text
    _reload(world.db, row)
    assert row.state == INQUIRY_RAISED
    assert row.note == "ZZT earlier note"
    # ORDERBACK-DELTA (owner, 13 Oct 2026 22:40): every saved decision is one line, so the
    # Cancel SO press still tells purchasing CANCEL even with no live row left to cancel.
    assert _cancel_only(calls)


def test_h16_a_cancelled_rows_suggested_links_are_dropped(api, monkeypatch):
    """Guards `_drop_suggested_links` [passes with the fix]."""
    client, world, w, row, calls = _world_with_row(api, monkeypatch)
    db = world.db
    allocation = SPOAllocation(
        id=_uid(), company_id=world.company_id, spo_number=f"ZZT-SPO-{_uid()[:8]}",
        spo_line_number=1, product_id=world.product.id, warehouse_id=world.own_wh.id,
        allocated_quantity=Decimal("40"),
    )
    db.add(allocation)
    db.flush()
    db.add(
        OrderInquirySuggestedLink(
            id=_uid(), company_id=world.company_id, row_id=row.id,
            spo_allocation_id=allocation.id, document=allocation.spo_number,
            qty=Decimal("20"), trigger="raise",
        )
    )
    db.commit()
    count = lambda: db.query(OrderInquirySuggestedLink).filter(  # noqa: E731
        OrderInquirySuggestedLink.row_id == row.id
    ).count()
    assert count() == 1

    response = _cancel_press(client, w, w["a"][1])

    assert response.status_code == 200, response.text
    db.expire_all()
    assert count() == 0
