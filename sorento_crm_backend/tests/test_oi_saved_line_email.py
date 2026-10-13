"""ORDERBACK-DELTA extension (owner, 13 Oct 2026 22:40): the purchasing email of a board
Confirm has exactly ONE line per SAVED line whose decision is new or changed, its REMARK
saying what was decided (`saved_line_remark`). Carried lines never appear; a saved line
re-approved with no change sends nothing (Q3); a Confirm with no such line sends no email.

Read off the email actually dispatched (post-commit drain, `AutomationService.dispatch_event`
captured), through the board's own Confirm route. Every row is seeded here.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from .test_planning_change_apply_on_board import (  # noqa: F401 - fixtures reused
    _confirm,
    api,
    world,
)
from .test_planning_changes import _core_line, _core_so, _line_payload, _project_line, _project_so
from .test_so_supply_confirmation import _stock

TRIGGER = "order_inquiry_handover"
D_A = date(2026, 11, 2)
D_B = date(2026, 11, 9)


@pytest.fixture()
def emails(monkeypatch):
    from app.services.project_order_inquiry_service import (
        register_order_inquiry_post_commit_dispatch,
    )

    register_order_inquiry_post_commit_dispatch()
    sent: list = []

    def _fake_dispatch(self, trigger_type, *, context, source_kind, source_id):
        if trigger_type == TRIGGER:
            sent.append(context["handover"])
        return {"trigger_type": trigger_type, "fired": 0, "results": []}

    monkeypatch.setattr(
        "app.services.automation_service.AutomationService.dispatch_event", _fake_dispatch
    )
    return sent


def _order(api, qty_a="10", qty_b="6"):
    _client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    core_a = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=qty_a,
                        required_date=D_A)
    core_b = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=qty_b,
                        required_date=D_B)
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line_a = _project_line(db, order, line_no=10, product=world.product, core_line=core_a)
    line_b = _project_line(db, order, line_no=20, product=world.product, core_line=core_b)
    db.commit()
    return order, line_a, line_b


def _lines(email):
    return [(l["item_code"], l["qty"], l["location"], l["remark"]) for l in email["lines"]]


def _post(api, order, payload):
    client, world = api
    response = _confirm(client, order.id, payload)
    assert response.status_code == 200, response.text
    world.db.commit()


def test_a_first_confirm_sends_one_line_per_saved_line_with_its_decision(api, emails):
    _client, world = api
    _stock(world.db, world.product, world.pool_wh, on_hand=50)
    world.db.commit()
    order, line_a, line_b = _order(api)
    pool = world.pool_wh.warehouse_code
    own = world.own_wh.warehouse_code

    _post(api, order, [
        _line_payload(line_a.id, buy_qty="10"),
        _line_payload(line_b.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}]),
    ])

    assert len(emails) == 1, emails
    code = world.product.product_code
    assert sorted(_lines(emails[0])) == sorted([
        (code, "10", own, "ORDER"),
        (code, "6", pool, f"USE {pool}"),
    ]), _lines(emails[0])


def test_a_saved_line_whose_decision_moved_is_the_only_line(api, emails):
    """Line A Buy 10, line B Use BRW 6. Re-Confirm saves only line B, now a Buy: one line,
    ORDER 6; line A (carried) is absent."""
    _client, world = api
    _stock(world.db, world.product, world.pool_wh, on_hand=50)
    world.db.commit()
    order, line_a, line_b = _order(api)
    _post(api, order, [
        _line_payload(line_a.id, buy_qty="10"),
        _line_payload(line_b.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}]),
    ])
    emails.clear()

    _post(api, order, [_line_payload(line_b.id, buy_qty="6", amend_reason="Keep BRW free.")])

    assert len(emails) == 1, emails
    assert _lines(emails[0]) == [
        (world.product.product_code, "6", world.own_wh.warehouse_code, "ORDER"),
    ]


def test_a_re_approved_unchanged_saved_line_sends_nothing(api, emails):
    _client, world = api
    _stock(world.db, world.product, world.pool_wh, on_hand=50)
    world.db.commit()
    order, line_a, line_b = _order(api)
    _post(api, order, [
        _line_payload(line_a.id, buy_qty="10"),
        _line_payload(line_b.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}]),
    ])
    emails.clear()

    _post(api, order, [
        _line_payload(line_b.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}]),
    ])

    assert emails == [], [_lines(e) for e in emails]


def test_one_saved_line_is_one_email_line_however_many_rows_it_raised(api, emails):
    """Use BRW 6 + Buy 4 on one line: ONE email line naming both parts, not an ORDER row
    line beside it."""
    _client, world = api
    _stock(world.db, world.product, world.pool_wh, on_hand=50)
    world.db.commit()
    order, line_a, _line_b = _order(api)
    pool = world.pool_wh.warehouse_code

    _post(api, order, [
        _line_payload(line_a.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}],
                      buy_qty="4", amend_reason="Part from BRW, buy the rest."),
    ])

    assert len(emails) == 1, emails
    assert _lines(emails[0]) == [
        (world.product.product_code, "10", pool, f"USE {pool} 6 & ORDER 4"),
    ]
