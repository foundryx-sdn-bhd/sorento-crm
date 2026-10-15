"""OI-BULK-RESERVE part 2 (`PLAN-oi-bulk-reserve-15oct.md`, mock v4): the cross-OI Lines page
filters LINES by reserve status. `GET /order-inquiries?reserve=<status>` keeps a row when one
of its own request rows is in that status, with the Documents view's four statuses and the
same predicate (`reserve_status_predicates()`, AC-SN-30). TEST-FIRST.

Reuses the staged-notify suite's world: one OI per status plus a mixed OI whose first row's
reserve notice is parked and whose second row still needs a reserve.
"""
from __future__ import annotations

from app.models.project_so import OrderInquiryRow

from .test_oi_staged_notify import (  # noqa: F401  (`sn` is a pytest fixture)
    _ok,
    _status_world,
    sn,
)
from .test_order_inquiry_reserve import _captured_dispatches, _register
from .test_order_inquiry_worklist import LIST

SUMMARY = f"{LIST}/summary"


def _rows_of(world, inquiry) -> list:
    return [
        row.id
        for row in world.db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.order_inquiry_id == inquiry.id)
        .order_by(OrderInquiryRow.created_at, OrderInquiryRow.id)
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
