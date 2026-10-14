"""Ladder v2 group borrow (section E.4) surviving a carry-forward reconfirm.

Gap found by the ladder v2 coder (`documentation/plans/scm/PLAN-demo-followups-19aug-ladder-v2.md`
section E): `_borrow_shortfalls` raises a group borrow's order-back off a component's donor
fields, but a carried-forward line (`_CarriedLine` / `_FrozenComponent`, `confirm`'s "the union
is the server's" rule) only carried 4 fields - `warehouse_id`, `qty`, `source`,
`donor_project_id` - so a group-borrow component riding along on a reconfirm that named some
OTHER line lost its donor identity and the order-back silently stopped being raised.

HTTP-level, same fixtures and helpers as `test_so_supply_confirmation.py` (the `api` fixture,
`_line_payload`), because the defect is in the write path a route exercises end to end, not in
one function in isolation.

Postgres via `tests/_pg_fixture.py::blank_session` (through the shared `api` fixture), per
PRINCIPLES.md and this repo's CI-is-empty lesson.
"""
from __future__ import annotations

from app.models.project_so import (
    IV_BORROW_SHORTFALL,
    OrderInquiryRow,
    SOSupplyDecision,
)

from ._borrow_mode import borrow_from_order_on
from .test_so_supply_confirmation import (  # noqa: F401  (fixtures/helpers, not tests)
    BASE,
    _core_line,
    _core_so,
    _line_payload,
    _project_line,
    _project_so,
    api,
)


def _find_component(snapshot, *, kind: str, source_location=None):
    for component in snapshot.get("components") or []:
        if component.get("kind") != kind:
            continue
        if source_location is not None and component.get("source_location") != source_location:
            continue
        return component
    raise AssertionError(f"no {kind} component in {snapshot!r}")


def test_a_carried_group_borrow_line_keeps_its_donor_and_regenerates_one_order_back(api):
    client, world = api
    db = world.db
    borrow_from_order_on(db)

    # The donor: another sales order's own line at the same location, lent to this one.
    donor_cso = _core_so(db, world.company_id)
    donor_cso.so_number = "SO371334"
    db.flush()
    donor_cline = _core_line(db, donor_cso, world.product, world.own_wh, qty_ordered="90")
    db.commit()

    order = _project_so(db, world.project)
    core_so = _core_so(db, world.company_id)
    borrow_core_line = _core_line(
        db, core_so, world.product, world.own_wh, qty_ordered="90"
    )
    borrow_line = _project_line(
        db, order, line_no=1, product=world.product, core_line=borrow_core_line
    )
    buy_core_line = _core_line(db, core_so, world.product, world.own_wh, qty_ordered="5")
    buy_line = _project_line(
        db, order, line_no=2, product=world.product, core_line=buy_core_line
    )
    db.commit()

    borrow_payload = _line_payload(
        borrow_line.id,
        borrow=[
            {
                "source": "other_location",
                "warehouse_id": world.own_wh.id,
                "qty": "90",
                "reason": "Group borrow, auto-proposed.",
                "donor_core_line_id": donor_cline.id,
                "donor_so_number": "SO371334",
                "donor_line_no": 4,
                "donor_agent_code": "JEREMY",
            }
        ],
    )
    buy_payload = _line_payload(buy_line.id, buy_qty="5")

    resp = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [borrow_payload, buy_payload]},
    )
    assert resp.status_code == 200, resp.text

    raised = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
        .all()
    )
    assert [row.state for row in raised].count("raised") == 1

    # Reconfirm naming ONLY the buy line - the borrow line is carried forward verbatim.
    resp = client.post(
        f"{BASE}/sales-orders/{order.id}/confirm",
        json={"lines": [buy_payload]},
    )
    assert resp.status_code == 200, resp.text

    active = (
        db.query(SOSupplyDecision)
        .filter(
            SOSupplyDecision.project_sales_order_id == order.id,
            SOSupplyDecision.state == "active",
        )
        .one()
    )
    assert active.revision_no == 2
    snapshots = {s["project_line_id"]: s for s in active.line_snapshots}
    borrow_snapshot = snapshots[str(borrow_line.id)]
    component = _find_component(borrow_snapshot, kind="borrow")
    assert component["donor_so_number"] == "SO371334"
    assert component["donor_line_no"] == 4
    assert component["donor_core_line_id"] == str(donor_cline.id)
    assert component["order_back_qty"] == "90"

    rows = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.verb == IV_BORROW_SHORTFALL)
        .all()
    )
    # The reconfirm restates the same order-back - not zero (the bug) and not a second
    # live copy stacked beside the first (a duplicate). Unchanged, it is the SAME row
    # (ORDERBACK-DELTA, owner 13 Oct 2026), not cancelled and raised again.
    assert len(rows) == 1, [(r.state, r.note) for r in rows]
    still_raised = [row for row in rows if row.state == "raised"]
    assert len(still_raised) == 1, "the order-back must survive the carry-forward reconfirm"
    assert "SO371334" in (still_raised[0].note or "")
