"""Owner hand test 14 Oct 2026 (SO424764 on main): line 416 CB2514SS was already decided Buy
16 with its ORDER row raised. A reconfirm that changed only lines 144 and 336 simulated as
"Would write: line 416 CB2514SS: ORDER 16 at BRW-BB" while "Would confirm" named only 144 and
336. Owner rule: purchasing is told only what it has to act on, so an unchanged, already
raised line is neither written again (the simulation) nor sent again (the email).

Same shape here, through the board's own Confirm and Simulate routes.
"""
from __future__ import annotations

from app.models.project_so import INQUIRY_RAISED, IV_ORDER, OrderInquiryRow

from .test_oi_saved_line_email import D_A, D_B, TRIGGER, _lines, _post, emails  # noqa: F401
from .test_planning_change_apply_on_board import (  # noqa: F401 - fixtures reused
    BASE,
    api,
    world,
)
from .test_planning_changes import _core_line, _core_so, _line_payload, _project_line, _project_so
from .test_so_supply_confirmation import _stock

SIMULATE = f"{BASE}/fulfilment-planning/simulate-confirm"


def _so424764(api):
    _client, world = api
    db = world.db
    core_so = _core_so(db, world.company_id)
    lines = []
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    for line_no, qty, when in ((144, "10", D_A), (336, "6", D_B), (416, "16", D_A)):
        core = _core_line(db, core_so, world.product, world.own_wh, qty_ordered=qty,
                          required_date=when)
        lines.append(_project_line(db, order, line_no=line_no, product=world.product,
                                   core_line=core))
    db.commit()
    return order, lines


def _reconfirm_body(world, line_144, line_336):
    return [
        _line_payload(line_144.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "10"}],
                      amend_reason="BRW has it."),
        _line_payload(line_336.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": "6"}],
                      amend_reason="BRW has it."),
    ]


def _raised_order_rows(world, line):
    return (
        world.db.query(OrderInquiryRow)
        .filter(
            OrderInquiryRow.so_line_id == str(line.id),
            OrderInquiryRow.verb == IV_ORDER,
            OrderInquiryRow.state == INQUIRY_RAISED,
        )
        .all()
    )


def test_an_unchanged_raised_buy_line_is_neither_simulated_as_a_write_nor_sent(
    api, emails, monkeypatch
):
    from app.services.user_service import UserPermissionService

    client, world = api
    monkeypatch.setattr(
        UserPermissionService, "get_user_role_slugs", lambda self, user_id: {"admin"}
    )
    _stock(world.db, world.product, world.pool_wh, on_hand=50)
    world.db.commit()
    order, (line_144, line_336, line_416) = _so424764(api)
    _post(api, order, [
        _line_payload(line_144.id, buy_qty="10"),
        _line_payload(line_336.id, buy_qty="6"),
        _line_payload(line_416.id, buy_qty="16"),
    ])
    (row_416,) = _raised_order_rows(world, line_416)
    emails.clear()

    simulated = client.post(SIMULATE, json={"orders": [
        {"pso_id": order.id, "lines": _reconfirm_body(world, line_144, line_336)},
    ]})
    assert simulated.status_code == 200, simulated.text
    (result,) = simulated.json()["results"]
    assert result["ok"], result
    written = [(row["line_no"], row["verb"], row["qty"]) for row in result["inquiry_rows"]]
    assert all(line_no != 416 for line_no, _verb, _qty in written), written
    assert emails == [], "a simulation sends nothing"

    _post(api, order, _reconfirm_body(world, line_144, line_336))

    (still,) = _raised_order_rows(world, line_416)
    assert still.id == row_416.id, "line 416's raised row is kept, not cancelled and re-raised"
    assert len(emails) == 1, emails
    told = [item_code_qty for item_code_qty in _lines(emails[0])]
    assert all(qty != "16" for _code, qty, _loc, _remark in told), told
    assert len(told) == 2, told
