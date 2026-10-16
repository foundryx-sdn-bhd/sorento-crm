"""OI-BULK-RESERVE part 2 (`PLAN-oi-bulk-reserve-15oct.md`, mock v4): the cross-OI Lines page
filters LINES by reserve status. `GET /order-inquiries?reserve=<status>` keeps a row when one
of its own request rows is in that status, with the Documents view's four statuses and the
same predicate (`reserve_status_predicates()`, AC-SN-30). TEST-FIRST.

Reuses the staged-notify suite's world: one OI per status plus a mixed OI whose first row's
reserve notice is parked and whose second row still needs a reserve.
"""
from __future__ import annotations

import pytest

import sqlalchemy as sa

from app.models.project_so import OrderInquiryReserveRequestRow, OrderInquiryRow

from .test_oi_staged_notify import (  # noqa: F401  (`sn` is a pytest fixture)
    _ok,
    _status_world,
    sn,
)
from .test_order_inquiry_reserve import _captured_dispatches, _register
from .test_order_inquiry_worklist import LIST

SUMMARY = f"{LIST}/summary"


def _rows_of(world, inquiry) -> list:
    """The OI's rows, the ANSWERED one first: the mixed OI's two rows share a `created_at`,
    so ordering by it left "first" to the random id."""
    answered = (
        sa.exists()
        .where(
            OrderInquiryReserveRequestRow.row_id == OrderInquiryRow.id,
            OrderInquiryReserveRequestRow.qty_reserved.isnot(None),
        )
        .correlate(OrderInquiryRow)
    )
    return [
        row.id
        for row in world.db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.order_inquiry_id == inquiry.id)
        .order_by(answered.desc(), OrderInquiryRow.created_at, OrderInquiryRow.id)
        .all()
    ]


def _listed(client, ours: set, **params) -> set:
    body = _ok(client.get(LIST, params={"limit": 100, **params}))
    return {row["id"] for row in body["data"]} & ours


def test_reserve_filter_keeps_rows_whose_own_request_row_is_in_that_status(sn, monkeypatch):
    client, world = sn
    _register()
    _captured_dispatches(monkeypatch)
    w = _status_world(client, world)
    rows = {key: _rows_of(world, inquiry) for key, inquiry in w.items()}
    ours = {row_id for ids in rows.values() for row_id in ids}
    mixed_first, mixed_second = rows["mixed"]

    expect = {
        "parked": set(rows["parked"]),
        "needs_reserve": set(rows["needs"]) | {mixed_second},
        "notice_parked": set(rows["notice_parked"]) | {mixed_first},
        "reserved": set(rows["reserved"]),
    }
    for status, want in expect.items():
        assert _listed(client, ours, reserve=status) == want, status
    # No filter: every one of ours, the row with no request included.
    assert _listed(client, ours) == ours


def test_reserve_filter_composes_with_inquiry_and_summary(sn, monkeypatch):
    client, world = sn
    _register()
    _captured_dispatches(monkeypatch)
    w = _status_world(client, world)
    mixed_first, mixed_second = _rows_of(world, w["mixed"])

    body = _ok(
        client.get(LIST, params={"reserve": "needs_reserve", "inquiry_id": w["mixed"].id})
    )
    assert [row["id"] for row in body["data"]] == [mixed_second]

    # The page's counts read the same rows the list does.
    listed = _ok(client.get(LIST, params={"reserve": "notice_parked", "limit": 100}))
    summary = _ok(client.get(SUMMARY, params={"reserve": "notice_parked"}))
    assert listed["pagination"]["total"] >= 2
    assert summary["total_rows"] == listed["pagination"]["total"]


def test_unknown_reserve_status_is_422(sn):
    client, _world = sn
    assert client.get(LIST, params={"reserve": "bogus"}).status_code == 422


def test_list_rows_carry_the_open_request_rows_warehouse_and_code(sn, monkeypatch):
    """B2: `reserve_warehouse_id` / `reserve_location` = the OPEN request row's warehouse and
    its code; null with no request and on an answered row."""
    client, world = sn
    _register()
    _captured_dispatches(monkeypatch)
    w = _status_world(client, world)
    needs_row = _rows_of(world, w["needs"])[0]
    none_row = _rows_of(world, w["none"])[0]
    reserved_row = _rows_of(world, w["reserved"])[0]
    mixed_first, mixed_second = _rows_of(world, w["mixed"])

    body = _ok(client.get(LIST, params={"limit": 100}))
    by_id = {row["id"]: row for row in body["data"]}

    for oi_key, row_id in (("needs", needs_row), ("none", none_row), ("reserved", reserved_row)):
        assert by_id[row_id]["order_inquiry_id"] == w[oi_key].id
    assert by_id[mixed_first]["order_inquiry_id"] == w["mixed"].id
    assert by_id[mixed_second]["order_inquiry_id"] == w["mixed"].id

    for open_id in (needs_row, mixed_second):
        assert by_id[open_id]["reserve_warehouse_id"] == world.site.id
        assert by_id[open_id]["reserve_location"] == world.site.warehouse_code
    for closed_id in (none_row, reserved_row, mixed_first):
        assert "reserve_warehouse_id" in by_id[closed_id], "field must be declared on the schema"
        assert by_id[closed_id]["reserve_warehouse_id"] is None
        assert by_id[closed_id]["reserve_location"] is None


def test_request_warehouse_wins_over_the_lines_own_location(sn, monkeypatch):
    """S6: the line sits at the POOL, the request names the SITE: the row carries the
    request's warehouse id and code, not the line's own (guards the aliased subquery)."""
    from .test_oi_staged_notify import _new_oi, _oi_row, _request, _user, MARKER

    client, world = sn
    _register()
    _captured_dispatches(monkeypatch)
    who = _user(world.db, f"{MARKER} Raiser")
    inquiry = _new_oi(world, who)
    row = _oi_row(world, inquiry, stock_location=world.pool.warehouse_code)
    _request(client, world, inquiry, [(row, "50")], park=False)

    body = _ok(client.get(LIST, params={"limit": 100, "inquiry_id": inquiry.id}))
    (listed,) = [r for r in body["data"] if r["id"] == row.id]
    assert listed["reserve_warehouse_id"] == world.site.id
    assert listed["reserve_location"] == world.site.warehouse_code
    assert listed["reserve_location"] != world.pool.warehouse_code


def test_confirm_by_filter_with_reserve_confirms_only_the_narrowed_rows(sn, monkeypatch):
    """B1: "Select all matching" sends the page's filters, `reserve` included."""
    client, world = sn
    _register()
    _captured_dispatches(monkeypatch)
    w = _status_world(client, world)
    rows = {key: _rows_of(world, inquiry) for key, inquiry in w.items()}
    in_scope = set(rows["reserved"])
    out_of_scope = set(rows["parked"]) | set(rows["needs"]) | set(rows["none"])

    response = client.post(f"{LIST}/acknowledge", json={"filter": {"reserve": "reserved"}})
    assert response.status_code == 200, response.text
    world.db.expire_all()

    def acknowledged(ids):
        return {
            row.id
            for row in world.db.query(OrderInquiryRow).filter(OrderInquiryRow.id.in_(ids)).all()
            if row.acknowledged_at is not None
        }

    assert acknowledged(in_scope) == in_scope
    assert acknowledged(out_of_scope) == set()
    # A value outside the four statuses is refused, not ignored.
    bad = client.post(f"{LIST}/acknowledge", json={"filter": {"reserve": "bogus"}})
    assert bad.status_code == 422


def test_matrix_and_export_honour_the_reserve_filter(sn, monkeypatch):
    """S4: the Schedule matrix reads the same rows the list does."""
    from .test_oi_staged_notify import _new_oi, _oi_row, _request, _user, MARKER

    client, world = sn
    _register()
    _captured_dispatches(monkeypatch)
    who = _user(world.db, f"{MARKER} Raiser")
    asked = _new_oi(world, who)
    asked_row = _oi_row(world, asked)
    _request(client, world, asked, [(asked_row, "50")], park=False)
    plain = _new_oi(world, who)
    _oi_row(world, plain)

    ours = {str(asked.project_sales_order_id), str(plain.project_sales_order_id)}

    def matrix_rows(**params):
        body = _ok(
            client.get(f"{LIST}/matrix", params={"axis": "sales_order", "by": "month", **params})
        )
        return sum(cell["rows"] for cell in body["data"] if cell["axis_key"] in ours)

    assert matrix_rows() == 2
    assert matrix_rows(reserve="needs_reserve") == 1
    assert matrix_rows(reserve="reserved") == 0

    assert client.get(f"{LIST}/matrix", params={"axis": "sales_order", "reserve": "bogus"}).status_code == 422
    assert client.get(f"{LIST}/export", params={"reserve": "reserved"}).status_code == 200
    assert client.get(f"{LIST}/export", params={"reserve": "bogus"}).status_code == 422
