"""FULFIL-CONFIRM-SCOPE slice 1: the confirm answers name the lines they wrote.

Plan `documentation/plans/scm/PLAN-fulfil-confirm-scope-30sep.md` S1, UAC
`fulfil-confirm-scope-30sep-acceptance-criteria.md` AC-S1..AC-S4.

`POST .../sales-orders/{pso_id}/confirm` and `POST .../fulfilment-planning/confirm-all`
both answer with `lines_confirmed: [{project_line_id, line_no, item_code}]` (payload order)
and `lines_carried: int`. Asserted on the HTTP body, never the service dict, because
`response_model` silently drops an undeclared field.

RED today for the right reason: the two fields are not declared or returned, so the body
has no `lines_confirmed` / `lines_carried` key.

Postgres via `tests/_pg_fixture.py`, fixture chain reused from
`tests/test_so_supply_confirmation.py`.
"""
from __future__ import annotations

from decimal import Decimal

from .test_fulfilment_line_draft_route import (  # noqa: F401 - `api` is a fixture
    _board,
    _covered_two_line_world,
    _stage_reject,
)
from .test_so_supply_confirmation import (  # noqa: F401
    BASE,
    _core_line,
    _core_so,
    _line_payload,
    _project_line,
    _project_so,
    _stock,
    api,
)


def _four_line_order(world):
    """One order, four lines (10..40) of the world's product on the own warehouse."""
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=1000)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    lines = []
    for line_no in (10, 20, 30, 40):
        core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="5")
        lines.append(
            _project_line(
                db, order, line_no=line_no, product=world.product, core_line=core_line
            )
        )
    db.commit()
    return order, lines


def _reserve(world, line, qty="5"):
    return _line_payload(line.id, reserve=[{"warehouse_id": world.pool_wh.id, "qty": qty}])


def _entry(line, world):
    return {
        "project_line_id": line.id,
        "line_no": line.line_no,
        "item_code": world.product.product_code,
    }


def _first_press_covers_10_and_20(client, world, order, lines):
    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_reserve(world, lines[0]), _reserve(world, lines[1])]},
    )
    assert response.status_code == 200, response.text


def test_single_confirm_echoes_exactly_the_named_lines_and_the_carried_count(api):
    """AC-S1 + AC-S4: second press names 30 and 40; 10 and 20 are carried forward."""
    client, world = api
    order, lines = _four_line_order(world)
    _first_press_covers_10_and_20(client, world, order, lines)

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_reserve(world, lines[2]), _reserve(world, lines[3])]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lines_confirmed"] == [_entry(lines[2], world), _entry(lines[3], world)]
    assert body["lines_carried"] == 2
    assert body["lines_decided"] == 4
    assert body["lines_decided"] == len(body["lines_confirmed"]) + body["lines_carried"]


def test_confirm_all_echoes_the_named_lines_per_order(api):
    """AC-S1 on the confirm-all route: the per-order entry carries the same two fields."""
    client, world = api
    order, lines = _four_line_order(world)
    _first_press_covers_10_and_20(client, world, order, lines)

    response = client.post(
        f"{BASE}/fulfilment-planning/confirm-all",
        json={
            "orders": [
                {
                    "pso_id": order.id,
                    "lines": [_reserve(world, lines[2]), _reserve(world, lines[3])],
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert result["lines_confirmed"] == [_entry(lines[2], world), _entry(lines[3], world)]
    assert result["lines_carried"] == 2
    assert result["lines_decided"] == 4


def test_a_held_back_line_is_in_lines_held_back_and_not_in_lines_confirmed(api):
    """AC-S2 (#1362 hold-back, `_write_holding_back`). Setup: the pool holds 50 and two lines
    of 50 each reserve all 50 from it. `_check_line` spends the pool as a running ledger
    across the lines of one press (see the shared-capacity test in
    test_so_supply_confirmation.py), so line B (20) is refused with a line-addressed 409;
    the hold-back drops it and confirms line A (10)."""
    client, world = api
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=50)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_a = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    core_b = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="50")
    line_a = _project_line(db, order, line_no=10, product=world.product, core_line=core_a)
    line_b = _project_line(db, order, line_no=20, product=world.product, core_line=core_b)
    db.commit()

    response = client.post(
        f"{BASE}/fulfilment-planning/confirm-all",
        json={
            "orders": [
                {
                    "pso_id": order.id,
                    "lines": [
                        _reserve(world, line_a, "50"),
                        _reserve(world, line_b, "50"),
                    ],
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    result = response.json()["results"][0]
    assert result["ok"] is True, result
    assert [row["line_no"] for row in result["lines_confirmed"]] == [10]
    assert result["lines_confirmed"][0]["project_line_id"] == line_a.id
    assert [row["line_no"] for row in result["lines_held_back"]] == [20]
    assert len(result["lines_confirmed"]) + len(result["lines_held_back"]) == 2


def test_a_withdrawal_only_press_echoes_no_confirmed_lines(api):
    """AC-S3: `lines: []` plus `rejected_line_ids` on an order whose active revision covers
    two lines confirms nothing new, and says so."""
    client, world, core_so, order, _core_1, line_1, _core_2, line_2 = (
        _covered_two_line_world(api)
    )
    key_1 = next(
        row["key"]
        for row in _board(client, core_so)["contributions"]
        if row["item_code"] == world.product.product_code
    )
    _stage_reject(client, key_1)

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [], "rejected_line_ids": [str(line_1.id)]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lines_confirmed"] == []
    assert body["lines_carried"] == 1


def _fulfilled_and_open_order(world):
    """One order: line 10 has nothing open (plan quantity 0, fulfilled), line 20 open."""
    db = world.db
    _stock(db, world.product, world.pool_wh, on_hand=1000)
    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    core_done = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="5")
    # The board plans a line for `coalesce(qty_required, qty_ordered)` (14 Sep ruling), so a
    # delivered quantity alone does not make a line fulfilled; a zero plan quantity does.
    core_done.qty_required = Decimal("0")
    core_open = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="5")
    done = _project_line(db, order, line_no=10, product=world.product, core_line=core_done)
    open_line = _project_line(
        db, order, line_no=20, product=world.product, core_line=core_open
    )
    db.commit()
    return order, done, open_line


def test_a_fulfilled_line_is_skipped_and_not_echoed_as_confirmed(api):
    """The echo is what was FROZEN, not what the payload named: a named line with nothing
    open on it is skipped (`lines_fulfilled_skipped`) and must not appear."""
    client, world = api
    order, done, open_line = _fulfilled_and_open_order(world)

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_reserve(world, done), _reserve(world, open_line)]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["lines_fulfilled_skipped"] == 1
    assert body["lines_confirmed"] == [_entry(open_line, world)]


def test_a_fulfilled_only_press_writes_nothing_and_echoes_no_lines(api):
    client, world = api
    order, done, _open_line = _fulfilled_and_open_order(world)

    response = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [_reserve(world, done)]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision_no"] is None
    assert body["lines_confirmed"] == []
