"""Order inquiry: Request CS to reserve stock (`PLAN-oi-request-cs-reserve.md`, slices 2/3).

Contract: `documentation/plans/scm/oi-request-cs-reserve-acceptance-criteria.md`,
AC-RS-1 to AC-RS-21 (backend). TEST-FIRST (Phase 2): written before
`app/services/order_inquiry_reserve_service.py`, the two new models, the new permission,
the widened link CHECK and the two seeded automations exist - there is NO implementation
to look at. A red here must be a missing module/attribute, a 404, or an assertion on
behaviour the plan says does not exist yet - never an import typo or a fixture bug.

Postgres only (`tests/_pg_fixture.py`); every FK chain is seeded here or borrowed from an
existing seeding helper (`test_order_inquiry_worklist.py`, `test_planning_changes.py`,
`test_order_inquiry_handshake.py`) - never `LIMIT 1` off a shared table. Most of this file
runs on `blank_session()` (a real, empty Postgres schema built from `Base.metadata`) -
fast and isolated, and sufficient for everything the reserve service itself touches
(rows, links, requests). Two tests need the REAL migrated database instead, because
`scm.committed_v` is a raw-SQL VIEW a migration installs and is not part of
`Base.metadata`: RS-13 borrows `test_order_inquiry_handshake`'s `world`/`api` (rolled
back), the same reason that suite gives for doing the same.

NAMED ASSUMPTIONS (per the tester's brief - "name any gap so the captain can pin it for
the coder"), because neither the plan nor the UAC pins these internal names and the
project's own convention (`test_order_inquiry_handover_automation.py`'s own docstring) is
that a test-first suite IS what pins them:

1. `OrderInquiryReserveService(db).create_request(inquiry_id, rows, note, actor_user_id)`
   returns an object exposing `.id`, `.ordinal`, `.state` (an ORM row or an equivalent
   namespace) - never a bare dict - matching every other service method in this codebase
   that hands back the row it just wrote (`ProjectSupplyService.confirm`, `place_on_po`).
2. Post-commit dispatch needs an explicit registration call the same shape as the
   existing `register_order_inquiry_post_commit_dispatch` (`project_order_inquiry_
   service.py:9355`, drains `order_inquiry_changed_with_links`) and `_fire_pending_
   handover` (`:9494`, drains the handover) - but NEITHER of those two drains this
   feature's own events, so this suite expects its OWN function,
   `register_order_inquiry_reserve_post_commit_dispatch`, exported from the new
   `order_inquiry_reserve_service` module. If the coder instead attaches the listener
   unconditionally at import time (no caller-visible register function needed, the way
   `_stamp_inquiry_no` does), RS-1/RS-6/RS-19/RS-21's `_register()` calls need deleting
   rather than renaming - flagged here rather than guessed twice.
3. The route paths are exactly the plan's own words: `POST {LIST}/{inquiry_id}/
   reserve-requests`, `POST {LIST}/reserve-requests/{id}/cancel`,
   `POST {LIST}/reserve-requests/{id}/reserve`.
4. AC-RS-20 names TWO readers ("header lines + worklist"); only the worklist reader
   (`GET {LIST}`, already proven by `test_order_inquiry_worklist.py`) is exercised here
   for time - the header-lines route lives in a part of `order_inquiries.py` this file
   does not otherwise touch, and the two readers share one `_serialize`-shaped
   contract per the plan (3.5), so one red on the shared field is a fair proxy. Narrowed
   deliberately, same as AC-H6/AC-H15 are narrowed in the handover suite's own docstring.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.models.base import company_scope
from app.models.project_so import (
    INQUIRY_ACTIONED,
    INQUIRY_PARTLY_LINKED,
    INQUIRY_PLACED,
    INQUIRY_RAISED,
    IV_DELAY,
    IV_ORDER,
    OrderInquiry,
    OrderInquiryLink,
    OrderInquiryRow,
    ProjectSalesOrder,
)
from app.services.error_handler import AppException

from ._pg_fixture import blank_session
from .test_order_inquiry_worklist import (
    LIST,
    MARKER as WL_MARKER,
    _client as _wl_client,
    _inquiry_for,
    _line_on_authored_order,
    _purchase_order,
    _restore as _wl_restore,
    _row,
    _sorento,
    _uid,
    _user,
    api as worklist_api,  # noqa: F401  (re-exported fixture; used by RS-12/RS-20)
)
from .test_planning_changes import _warehouse
from .test_order_inquiry_handshake import world as handshake_world  # noqa: F401  (fixture)

MARKER = "zzt-oi-reserve"
BASE = "/api/v1/project-sales"

RESERVE_PERMISSION = "projects.order_inquiries.reserve"
REQUESTER_PERMISSIONS = [
    "projects.projects.view",
    "projects.order_inquiry.action",
    "projects.order_inquiries.acknowledge",
]
RESERVER_PERMISSIONS = REQUESTER_PERMISSIONS + [RESERVE_PERMISSION]

REQUEST_URL = lambda inquiry_id: f"{LIST}/{inquiry_id}/reserve-requests"  # noqa: E731
CANCEL_URL = lambda request_id: f"{LIST}/reserve-requests/{request_id}/cancel"  # noqa: E731
#: The old ALL-ROWS reserve route (3.3), retired by section 6c F2 - kept defined here,
#: not called by any test below, ONLY so `test_order_inquiry_reserve_round2.py` can
#: import it (re-exported as `OLD_ALL_ROWS_RESERVE_URL`) to prove AC-RS-57 (404/405).
RESERVE_URL = lambda request_id: f"{LIST}/reserve-requests/{request_id}/reserve"  # noqa: E731
#: Section 6c F2/F5/F3: the per-row routes every test below now drives.
ROW_RESERVE_URL = lambda request_id, row_id: (  # noqa: E731
    f"{LIST}/reserve-requests/{request_id}/rows/{row_id}/reserve"
)
ROW_UNRESERVE_URL = lambda request_id, row_id: (  # noqa: E731
    f"{LIST}/reserve-requests/{request_id}/rows/{row_id}/unreserve"
)
ROW_HISTORY_URL = lambda request_id, row_id: (  # noqa: E731
    f"{LIST}/reserve-requests/{request_id}/rows/{row_id}/history"
)
#: Round 4 (`PLAN-oi-request-cs-reserve.md` 6e.1): the per-row `/reserve` route above
#: is retired (`test_order_inquiry_reserve_commit.py::test_AC_RS_79_...` pins the 404).
#: Every test below that used to answer a row through it now goes through the commit
#: route instead - the same underlying validation, batched.
COMMIT_URL = lambda inquiry_id: f"{LIST}/{inquiry_id}/reserve-commit"  # noqa: E731


# --------------------------------------------------------------------------------- #
# harness                                                                            #
# --------------------------------------------------------------------------------- #


def _pso(db, company_id: str) -> ProjectSalesOrder:
    """The smallest legal `ProjectSalesOrder` - `provisional_ref` is its only NOT NULL
    column beyond id/company_id (measured, `app/models/project_so.py` ~:454-478). An
    `OrderInquiry` header needs one to point at; nothing here reads through it."""
    pso = ProjectSalesOrder(
        id=_uid(), company_id=company_id, provisional_ref=f"{MARKER}-{_uid()[:8]}"
    )
    db.add(pso)
    db.flush()
    return pso


class _World:
    def __init__(self, db, company_id, requester, reserver, pool, site, inquiry):
        self.db = db
        self.company_id = company_id
        self.requester = requester
        self.reserver = reserver
        self.pool = pool
        self.site = site
        self.inquiry = inquiry


def _seeded_world(db):
    company_id = _sorento(db)
    requester = _user(db, f"{MARKER} Joey")
    reserver = _user(db, f"{MARKER} Eling")
    pool = _warehouse(db, f"ZZT-{_uid()[:6]}".upper())
    site = _warehouse(db, f"{pool.warehouse_code}-BB", pool_warehouse_id=pool.id)
    inquiry = _inquiry_for(db, company_id, _pso(db, company_id))
    db.commit()
    return _World(db, company_id, requester, reserver, pool, site, inquiry)


def _api(permissions):
    with blank_session() as db:
        world = _seeded_world(db)
        client, originals = _wl_client(db, world.requester, permissions)
        try:
            with company_scope(db, frozenset({world.company_id})):
                yield client, world
        finally:
            _wl_restore(originals)


@pytest.fixture()
def api():
    """The default actor holds BOTH permissions (request + reserve) - most tests are
    about the reserve SERVICE's own rules, not about who may press which button (that is
    RS-10's own job, which builds its own narrower client)."""
    yield from _api(RESERVER_PERMISSIONS)


def _as(db, user_id: str, permissions):
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        client, originals = _wl_client(db, user_id, permissions)
        try:
            yield client
        finally:
            _wl_restore(originals)

    return _ctx()


def _register():
    """See module docstring, assumption 2."""
    from app.services.order_inquiry_reserve_service import (
        register_order_inquiry_reserve_post_commit_dispatch,
    )

    register_order_inquiry_reserve_post_commit_dispatch()


def _captured_dispatches(monkeypatch) -> list[dict]:
    calls: list[dict] = []

    def _fake_dispatch(self, trigger_type, *, context, source_kind, source_id):
        calls.append(
            {
                "trigger_type": trigger_type,
                "context": context,
                "source_kind": source_kind,
                "source_id": source_id,
            }
        )
        return {"trigger_type": trigger_type, "fired": 0, "results": []}

    monkeypatch.setattr(
        "app.services.automation_service.AutomationService.dispatch_event",
        _fake_dispatch,
    )
    return calls


def _requested_calls(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["trigger_type"] == "order_inquiry_reserve_requested"]


def _reserved_calls(calls: list[dict]) -> list[dict]:
    return [c for c in calls if c["trigger_type"] == "order_inquiry_reserved"]


def _open_row(world, *, qty="139", verb=IV_ORDER, state=INQUIRY_RAISED, **fields):
    fields.setdefault("item_code", f"{MARKER}-ITEM")
    fields.setdefault("delivery_date", date(2026, 10, 1))
    fields.setdefault("stock_location", world.site.warehouse_code)
    row = _row(
        world.db, world.company_id, world.inquiry, verb=verb, state=state, qty=qty, **fields
    )
    world.db.commit()
    return row


def _service(db):
    from app.services.order_inquiry_reserve_service import OrderInquiryReserveService

    return OrderInquiryReserveService(db)


# --------------------------------------------------------------------------------- #
# RS-1                                                                               #
# --------------------------------------------------------------------------------- #


def test_request_creates_request_and_rows_one_dispatch(api, monkeypatch):
    client, world = api
    _register()
    calls = _captured_dispatches(monkeypatch)

    rows = [_open_row(world, qty=q) for q in ("50", "60", "70")]

    response = client.post(
        REQUEST_URL(world.inquiry.id),
        json={
            "rows": [
                {"row_id": row.id, "qty_requested": str(row.qty), "warehouse_id": world.site.id}
                for row in rows
            ],
            "note": None,
        },
    )
    assert response.status_code == 201, response.text
    world.db.commit()

    from app.models.project_so import OrderInquiryReserveRequest, OrderInquiryReserveRequestRow

    request = (
        world.db.query(OrderInquiryReserveRequest)
        .filter(OrderInquiryReserveRequest.order_inquiry_id == world.inquiry.id)
        .one()
    )
    assert request.ordinal == 1
    assert request.state == "requested"
    assert request.requested_by == world.requester
    assert request.requested_at is not None

    request_rows = (
        world.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.request_id == request.id)
        .all()
    )
    assert len(request_rows) == 3

    matches = _requested_calls(calls)
    assert len(matches) == 1, f"expected exactly one dispatch (R9), got {len(matches)}"
    assert matches[0]["context"]["reserve"]["row_count"] == 3


# --------------------------------------------------------------------------------- #
# RS-2                                                                               #
# --------------------------------------------------------------------------------- #


def test_request_default_location_is_pool(api):
    client, world = api
    row_at_site = _open_row(world, stock_location=world.site.warehouse_code)
    row_at_pool = _open_row(world, stock_location=world.pool.warehouse_code)

    resp_site = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": row_at_site.id, "qty_requested": "139"}]},
    )
    assert resp_site.status_code == 201, resp_site.text
    world.db.commit()

    resp_pool = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": row_at_pool.id, "qty_requested": "139"}]},
    )
    assert resp_pool.status_code == 201, resp_pool.text
    world.db.commit()

    from app.models.project_so import OrderInquiryReserveRequestRow

    site_request_row = (
        world.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.row_id == row_at_site.id)
        .one()
    )
    assert site_request_row.warehouse_id == world.pool.id, (
        "a row at BRW-BB (pool_warehouse_id set) must default to BRW itself (R3)"
    )

    pool_request_row = (
        world.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.row_id == row_at_pool.id)
        .one()
    )
    assert pool_request_row.warehouse_id == world.pool.id, (
        "a row whose location IS ALREADY a pool must default to itself"
    )


# --------------------------------------------------------------------------------- #
# RS-3                                                                               #
# --------------------------------------------------------------------------------- #


def test_request_qty_capped_at_remaining(api):
    client, world = api
    po_line = _purchase_order(world.db, world.company_id)["line"]
    row = _open_row(world, qty="100")
    world.db.add(
        OrderInquiryLink(
            id=_uid(), company_id=world.company_id, row_id=row.id,
            po_line_id=po_line.id, document="ZZT-PO-LINKED", qty=Decimal("40"),
        )
    )
    world.db.commit()
    # remaining = 100 - 40 = 60

    over = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": row.id, "qty_requested": "61"}]},
    )
    assert over.status_code == 422, over.text

    zero = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": row.id, "qty_requested": "0"}]},
    )
    assert zero.status_code == 422, zero.text

    ok = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": row.id, "qty_requested": "60"}]},
    )
    assert ok.status_code == 201, ok.text


# --------------------------------------------------------------------------------- #
# RS-4                                                                               #
# --------------------------------------------------------------------------------- #


def test_request_rejects_open_duplicate_and_wrong_state(api):
    client, world = api

    duplicate_row = _open_row(world)
    first = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": duplicate_row.id, "qty_requested": "139"}]},
    )
    assert first.status_code == 201, first.text
    world.db.commit()

    second = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": duplicate_row.id, "qty_requested": "1"}]},
    )
    assert second.status_code == 409, second.text

    wrong_verb_row = _open_row(world, verb=IV_DELAY)
    wrong_verb = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": wrong_verb_row.id, "qty_requested": "1"}]},
    )
    assert wrong_verb.status_code == 409, wrong_verb.text

    actioned_row = _open_row(world, state=INQUIRY_ACTIONED)
    wrong_state = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": actioned_row.id, "qty_requested": "1"}]},
    )
    assert wrong_state.status_code == 409, wrong_state.text

    other_inquiry = _inquiry_for(world.db, world.company_id, _pso(world.db, world.company_id))
    world.db.commit()
    foreign_row = _row(
        world.db, world.company_id, other_inquiry, verb=IV_ORDER, state=INQUIRY_RAISED, qty="10",
    )
    world.db.commit()
    wrong_inquiry = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": foreign_row.id, "qty_requested": "1"}]},
    )
    assert wrong_inquiry.status_code == 409, wrong_inquiry.text


# --------------------------------------------------------------------------------- #
# RS-5                                                                               #
# --------------------------------------------------------------------------------- #


def test_second_request_after_reserved_allowed(api):
    client, world = api
    row = _open_row(world, qty="139")

    first = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": row.id, "qty_requested": "139"}]},
    )
    assert first.status_code == 201, first.text
    world.db.commit()
    request_id = first.json()["id"]

    from app.models.project_so import OrderInquiryReserveRequestRow

    request_row = (
        world.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.row_id == row.id)
        .one()
    )
    reserve_resp = client.post(
        COMMIT_URL(world.inquiry.id),
        json={
            "reserves": [
                {
                    "row_id": row.id,
                    "warehouse_id": world.site.id,
                    "qty_reserved": "50",
                    "reason": "BRW only has 50 in stock",
                }
            ]
        },
    )
    assert reserve_resp.status_code == 200, reserve_resp.text
    world.db.commit()

    second = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": row.id, "qty_requested": "89"}]},
    )
    assert second.status_code == 201, second.text
    assert second.json()["ordinal"] == 2


# --------------------------------------------------------------------------------- #
# RS-6 to RS-11 - RETIRED. Each pinned the per-row `.../rows/{row_id}/reserve` route's  #
# OWN request/response contract directly (link write + `refresh_link_state` + one       #
# dispatch on completion; 0/short/full + reason; over-requested; permission; answering  #
# the same row twice). `PLAN-oi-request-cs-reserve.md` section 6e.1 (owner round 4, 24  #
# Sep) retires that route outright - AC-RS-79 pins its own 404 - in favour of the       #
# batched `POST .../reserve-requests/{id}/commit`, which                                #
# `test_order_inquiry_reserve_commit.py` covers end to end: link write + refresh_link_  #
# state + single dispatch (AC-RS-76), completion/conflict/404/empty/atomicity (AC-RS-   #
# 77), permission (AC-RS-79). RS-9 was already retired the same way, one round earlier  #
# (F2's per-row answer superseding the even older all-rows 3.3).                        #
# --------------------------------------------------------------------------------- #


# --------------------------------------------------------------------------------- #
# RS-12 (real worklist reader, borrowed `api` fixture from test_order_inquiry_worklist) #
# --------------------------------------------------------------------------------- #


def test_taken_remaining_include_reserved(worklist_api):
    client, db, company_id, seeded = worklist_api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    line = _line_on_authored_order(db, company_id, seeded, qty="139", day=6)
    po_line = _purchase_order(db, company_id)["line"]

    row = _row(
        db, company_id, inquiry,
        so_line_id=line.id, item_code=f"{WL_MARKER}-RESERVE", qty="139",
        state=INQUIRY_PARTLY_LINKED, delivery_date=date(2026, 4, 6),
    )
    db.add(
        OrderInquiryLink(
            id=_uid(), company_id=company_id, row_id=row.id,
            po_line_id=po_line.id, document="ZZT-PO-LINKED", qty=Decimal("40"),
        )
    )
    db.commit()

    warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}".upper())
    from app.services.order_inquiry_reserve_service import OrderInquiryReserveService

    service = OrderInquiryReserveService(db)
    requester_id = _user(db, f"{WL_MARKER} requester")
    request = service.create_request(
        inquiry_id=inquiry.id,
        rows=[{"row_id": row.id, "qty_requested": Decimal("99"), "warehouse_id": warehouse.id}],
        note=None,
        actor_user_id=requester_id,
    )
    db.commit()
    from app.models.project_so import OrderInquiryReserveRequestRow

    request_row = (
        db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.request_id == request.id)
        .one()
    )
    reserver_id = _user(db, f"{WL_MARKER} reserver")
    service.commit_request(
        inquiry_id=inquiry.id,
        reserves=[
            {
                "row_id": row.id,
                "warehouse_id": warehouse.id,
                "qty_reserved": Decimal("50"),
                "reason": "BRW only has 50 in stock",
            }
        ],
        amendments=[],
        actor_user_id=reserver_id,
    )
    db.commit()

    body = client.get(LIST, params={"delivery_month": "2026-04"}).json()
    by_id = {entry["id"]: entry for entry in body["data"]}

    assert by_id[row.id]["taken_from_po"] == "90", "PO 40 + reserved 50 (unchanged sum-by-row_id)"
    assert by_id[row.id]["remaining_open"] == "49"
    assert by_id[row.id]["reserved_qty"] == "50"
    assert by_id[row.id]["po_number"] is not None, (
        "the row's own PO link must still derive po_number - the reserve link must not "
        "leak into po_ref/po_number derivation"
    )


# --------------------------------------------------------------------------------- #
# RS-13 (real DB - scm.committed_v)                                                  #
# --------------------------------------------------------------------------------- #


def test_committed_v_owed_nets_reserved_link(handshake_world):
    """Borrows `test_order_inquiry_handshake`'s `world` fixture directly (imported below,
    at module scope, so pytest resolves it by name exactly as `test_oi_one_header.py`
    already does for the same suite) - the rolled-back real database is the only thing
    that carries `scm.committed_v`."""
    from .test_order_inquiry_handshake import (
        _client as _hs_client,
        _confirm,
        _core_line,
        _core_so,
        _line_payload,
        _project_line,
        _project_so,
        _restore as _hs_restore,
    )

    world = handshake_world
    from app.services.order_inquiry_reserve_service import OrderInquiryReserveService
    from app.models.project_so import OrderInquiryReserveRequestRow

    db = world.db
    core_so = _core_so(db, world.company_id)
    core_line = _core_line(
        db, core_so, world.product, world.warehouse, qty_ordered="139",
        required_date=date(2026, 8, 25),
    )
    order = _project_so(db, world.project, so_id=core_so.id, autocount_doc_no=core_so.so_number)
    line = _project_line(db, order, line_no=1, product=world.product, core_line=core_line)
    db.commit()

    # Wiring fix (review round): `_hs_client` monkeypatches `UserPermissionService`
    # at the CLASS level - every other caller in this file (`test_order_inquiry_
    # handshake.py`'s own tests) restores it in a `finally`, and this one did not,
    # which leaked the lambda into every test after it in the same pytest process
    # (`test_rbac.py` reads a stubbed `check_user_has_permission`/`get_user_
    # permission_slugs` when this file runs ahead of it). Fixed here as mock
    # wiring, not an assertion - the test's own behaviour is unchanged.
    client, originals = _hs_client(
        db, world.cs_user, ["projects.projects.view", "projects.projects.edit", "projects.order_inquiry.action"]
    )
    try:
        response = _confirm(client, order.id, [_line_payload(line.id, buy_qty="139")])
    finally:
        _hs_restore(originals)
    assert response.status_code == 200, response.text
    db.commit()

    row = (
        db.query(OrderInquiryRow)
        .filter(OrderInquiryRow.so_line_id == line.id, OrderInquiryRow.state != "cancelled")
        .one()
    )

    def _owed() -> Decimal:
        return Decimal(
            str(
                db.execute(
                    sa.text(
                        "SELECT COALESCE(SUM(project_committed), 0) FROM scm.committed_v "
                        "WHERE product_id = :pid"
                    ),
                    {"pid": str(world.product.id)},
                ).scalar()
                or 0
            )
        )

    before = _owed()

    service = OrderInquiryReserveService(db)
    request = service.create_request(
        inquiry_id=row.order_inquiry_id,
        rows=[{"row_id": row.id, "qty_requested": Decimal("50"), "warehouse_id": world.warehouse.id}],
        note=None,
        actor_user_id=world.cs_user,
    )
    db.commit()
    request_row = (
        db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.request_id == request.id)
        .one()
    )
    service.commit_request(
        inquiry_id=row.order_inquiry_id,
        reserves=[
            {"row_id": row.id, "warehouse_id": world.warehouse.id, "qty_reserved": Decimal("50")}
        ],
        amendments=[],
        actor_user_id=world.cs_user,
    )
    db.commit()

    after = _owed()
    assert before - after == Decimal("50"), (
        f"committed_v owed must drop by the reserved qty (R8): before={before} after={after}"
    )


# --------------------------------------------------------------------------------- #
# RS-14 - RETIRED. AC-RS-14 pinned "reversal = the existing Unlink" (plan 3.3's own     #
# words). `PLAN-oi-request-cs-reserve.md` section 6c F5 supersedes it outright: "Unlink  #
# (bulk deferred action and per-row) SKIPS reserve links ... 3.3 'Reversal' is           #
# superseded" - Unlink now does the OPPOSITE of what this test asserted (it leaves a     #
# reserve link standing), and the replacement action is Unreserve, its own route.        #
# `test_order_inquiry_reserve_round2.py::test_unlink_never_touches_a_reserve_link`       #
# (AC-RS-59) pins the correct behaviour in this same lane.                               #
# --------------------------------------------------------------------------------- #


# --------------------------------------------------------------------------------- #
# RS-15                                                                              #
# --------------------------------------------------------------------------------- #


def test_request_context_shape_and_link(api, monkeypatch):
    client, world = api
    _register()
    calls = _captured_dispatches(monkeypatch)

    row = _open_row(world, qty="139", item_code=f"{MARKER}-CTX")
    response = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": row.id, "qty_requested": "61"}], "note": "urgent"},
    )
    assert response.status_code == 201, response.text
    world.db.commit()

    matches = _requested_calls(calls)
    assert matches, "the request must dispatch"
    context = matches[0]["context"]
    reserve = context["reserve"]

    for key in ("inquiry_no", "ordinal", "so_number", "customer", "project", "requested_by",
                "requested_at", "rows", "row_count", "state", "link"):
        assert key in reserve, f"context.reserve missing {key!r}: {reserve}"

    assert reserve["inquiry_no"] == world.inquiry.inquiry_no
    assert reserve["row_count"] == 1
    assert reserve["rows"][0]["item_code"] == f"{MARKER}-CTX"
    assert reserve["rows"][0]["qty_requested"] == "61"
    for key in ("name", "email"):
        assert key in reserve["requested_by"], reserve["requested_by"]

    for key in ("actor", "raiser", "requester", "today"):
        assert key in context, f"context missing {key!r}: {context}"

    request_id = response.json()["id"]
    assert reserve["link"].endswith(f"?reserve={request_id}"), reserve["link"]
    assert str(world.inquiry.id) in reserve["link"], reserve["link"]


# --------------------------------------------------------------------------------- #
# RS-16                                                                              #
# --------------------------------------------------------------------------------- #


def test_recipient_order_request_and_reserved():
    from app.services.automation_recipients import resolve_recipients

    with blank_session() as db:
        eling_id = _user(db, f"{MARKER} Eling")
        db.commit()

        request_config = {
            "user_ids": [eling_id],
            "include_actor": True,
            "include_raiser": True,
            "one_email": True,
        }
        request_context = {
            "actor": {"email": "joey@zzt.test", "name": "Joey"},
            "raiser": {"email": "raiser@zzt.test", "name": "Raiser"},
        }
        recipients = resolve_recipients(db, request_config, request_context)
        emails = [r["email"] for r in recipients]
        assert emails[0].lower().startswith("eling") or "eling" in (recipients[0].get("name") or "").lower(), (
            f"user_ids resolves FIRST (Eling To): {recipients}"
        )
        assert "joey@zzt.test" in emails
        assert "raiser@zzt.test" in emails
        assert emails.index("joey@zzt.test") < emails.index("raiser@zzt.test"), (
            "requester (include_actor) before raiser: " + str(emails)
        )

        reserved_config = {
            "include_requester": True,
            "include_raiser": True,
            "one_email": True,
        }
        reserved_context = {
            "requester": {"email": "joey@zzt.test", "name": "Joey"},
            "raiser": {"email": "raiser@zzt.test", "name": "Raiser"},
        }
        reserved_recipients = resolve_recipients(db, reserved_config, reserved_context)
        reserved_emails = [r["email"] for r in reserved_recipients]
        assert reserved_emails[0] == "joey@zzt.test", (
            f"requester must resolve FIRST (To) on the reserved mail: {reserved_recipients}"
        )
        assert "raiser@zzt.test" in reserved_emails

        # Dedup: same person as both requester and raiser -> one entry only.
        same_person_context = {
            "requester": {"email": "one@zzt.test", "name": "One"},
            "raiser": {"email": "one@zzt.test", "name": "One"},
        }
        deduped = resolve_recipients(db, reserved_config, same_person_context)
        assert len(deduped) == 1, deduped


# --------------------------------------------------------------------------------- #
# RS-17                                                                              #
# --------------------------------------------------------------------------------- #


def _find_reserve_seed_migration_path() -> Path | None:
    """Content-sniffed by the SEEDED TEMPLATE CODE, not the table name (fix round,
    section 6c): the plan's own oirs_0002 follow-up migration necessarily mentions
    `order_inquiry_reserve_requests` too, in its `order_inquiry_reserve_events` FK
    (`REFERENCES order_inquiry_reserve_request_rows`, which CONTAINS the table name as
    a substring) - the table-name sniff this helper used before round 2 existed is no
    longer unambiguous. `order_inquiry_reserve_requested_default` is the template CODE
    3.6 seeds and is unique to the ONE migration that seeds both automations."""
    versions_dir = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    matches = [
        path
        for path in versions_dir.glob("*.py")
        if "order_inquiry_reserve_requested_default" in path.read_text(errors="ignore")
    ]
    assert len(matches) <= 1, (
        "more than one alembic migration mentions "
        f"order_inquiry_reserve_requested_default: {[p.name for p in matches]}"
    )
    return matches[0] if matches else None


def _load_reserve_seed_migration():
    """oirs_0001's own module, its `upgrade` WRAPPED to chain forward onto every later
    sibling in this migration family that exists on disk (`oirs_0002_reserve_round2`,
    `oirs_0003_reserve_commit_tmpl`, ...) - "upgrade the seed migration" reaching
    HEAD, the same thing a real `alembic upgrade head` against a fresh database does,
    and the same reasoning `_load_round2_migration`'s own docstring gives for chaining
    oirs_0002 onto oirs_0001 rather than amending oirs_0001 in place (round 4, AC-RS-80:
    a shipped migration's own SEED TEXT is never edited - the round-4 body fix is its
    own file, `oirs_0003_reserve_commit_tmpl.py`, an UPDATE chained on top). Every
    sibling's own `upgrade()` is idempotent (`IF NOT EXISTS` / `_set_template`'s own
    UPDATE), so `test_seed_migration_idempotent`'s own `module.upgrade()` twice, and its
    own explicit `_load_round2_migration().downgrade()` + `module.downgrade()` sequence
    (oirs_0001's own `.downgrade`, left untouched here), both stay exactly as they were.
    """
    path = _find_reserve_seed_migration_path()
    assert path is not None, (
        "no alembic migration under alembic/versions/ creates "
        "projects.order_inquiry_reserve_requests / seeds "
        "order_inquiry_reserve_requested_default + order_inquiry_reserved_default - the "
        "coder must add one (PLAN-oi-request-cs-reserve.md 3.1, AC-RS-17)."
    )
    import importlib.util

    spec = importlib.util.spec_from_file_location("zzt_reserve_seed_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    original_upgrade = module.upgrade
    versions_dir = path.parent

    def _upgrade_to_head():
        original_upgrade()
        for name in ("oirs_0002_reserve_round2", "oirs_0003_reserve_commit_tmpl"):
            sibling_path = versions_dir / f"{name}.py"
            if not sibling_path.exists():
                continue
            sibling_spec = importlib.util.spec_from_file_location(
                f"zzt_{name}", sibling_path
            )
            sibling_module = importlib.util.module_from_spec(sibling_spec)
            sibling_spec.loader.exec_module(sibling_module)
            sibling_module.upgrade()

    module.upgrade = _upgrade_to_head
    return module


def _load_round2_migration():
    """`oirs_0002_reserve_round2` chains onto this file's own oirs_0001 migration and
    creates `order_inquiry_reserve_events`, whose FK targets `order_inquiry_reserve_
    request_rows` - a table `order_inquiry_reserve_events` ALREADY carries in `blank_
    session()`'s own scratch schema, since that is built from the full `Base.metadata`,
    not from whichever alembic revision has run. So downgrading oirs_0001 alone (which
    drops `order_inquiry_reserve_request_rows`) hits `DependentObjectsStillExist` unless
    oirs_0002's own table is dropped FIRST - the same order a real downgrade chain would
    run in. Loaded by file name directly (not the content sniff `_find_reserve_seed_
    migration_path` uses) since this is the one specific sibling migration the fix
    needs, the same convention `test_order_inquiry_reserve_round2.py::_oirs_0001_path`
    already uses for its own sibling lookup."""
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "oirs_0002_reserve_round2.py"
    )
    assert path.exists(), "oirs_0002_reserve_round2.py is missing from this branch"
    import importlib.util

    spec = importlib.util.spec_from_file_location("zzt_reserve_round2_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_released_links_migration():
    """`oirr_0001_released_links` FKs `order_inquiry_reserve_request_rows`; dropped first so
    oirs_0001's downgrade needs no CASCADE."""
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "oirr_0001_released_links.py"
    )
    assert path.exists(), "oirr_0001_released_links.py is missing from this branch"
    import importlib.util

    spec = importlib.util.spec_from_file_location("zzt_released_links_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_migration_idempotent():
    module = _load_reserve_seed_migration()
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with blank_session() as db:
        ctx = MigrationContext.configure(db.connection())
        with Operations.context(ctx):
            module.upgrade()
            module.upgrade()  # idempotent re-run

        for code in ("order_inquiry_reserve_requested_default", "order_inquiry_reserved_default"):
            count = db.execute(
                sa.text("SELECT count(*) FROM email_templates WHERE code = :code"), {"code": code}
            ).scalar()
            assert count == 1, f"{code} must be seeded exactly once"

        for trigger, name in (
            ("order_inquiry_reserve_requested", "Order inquiry: request CS to reserve"),
            ("order_inquiry_reserved", "Order inquiry: reserved by CS"),
        ):
            rows = db.execute(
                sa.text(
                    "SELECT enabled, recipient_config FROM automations WHERE trigger_type = :t "
                    "AND name = :n"
                ),
                {"t": trigger, "n": name},
            ).fetchall()
            assert len(rows) == 1, f"{trigger} must be seeded exactly once: {rows}"
            enabled, cfg = rows[0]
            assert enabled is True
            import json

            cfg = cfg if isinstance(cfg, dict) else json.loads(cfg)
            assert cfg.get("one_email") is True

        with Operations.context(ctx):
            # oirs_0002's own table first (its FK is what blocks oirs_0001's downgrade -
            # see `_load_round2_migration`'s own docstring); running its downgrade needs
            # no prior upgrade() call since `DROP TABLE IF EXISTS` / `DROP COLUMN IF
            # EXISTS` are both safe against a table `blank_session()` already built.
            _load_released_links_migration().downgrade()
            _load_round2_migration().downgrade()
            module.downgrade()

        remaining_templates = db.execute(
            sa.text(
                "SELECT count(*) FROM email_templates WHERE code IN "
                "('order_inquiry_reserve_requested_default', 'order_inquiry_reserved_default')"
            )
        ).scalar()
        assert remaining_templates == 0
        remaining_automations = db.execute(
            sa.text(
                "SELECT count(*) FROM automations WHERE trigger_type IN "
                "('order_inquiry_reserve_requested', 'order_inquiry_reserved')"
            )
        ).scalar()
        assert remaining_automations == 0

        # The widened CHECK is restored on downgrade too. `conname` alone is not enough
        # to identify the right constraint: the REAL `projects.order_inquiry_links` (this
        # lane's own migrated DB, `oirs_0001_reserve_requests` already applied there)
        # carries a constraint of the SAME name - constraint names are per-TABLE, not
        # global - so an unscoped lookup can return the real (still three-target) row
        # instead of this test's own scratch-schema copy that `downgrade()` just
        # restored. Scoped to a table named `order_inquiry_links` sitting in one of the
        # schemas THIS session's own search path actually has open
        # (`current_schemas(false)`) - the scratch `{name}_projects` translation
        # `blank_session()` set up, never the literal `projects` schema, which is not on
        # that path.
        check_def = db.execute(
            sa.text(
                "SELECT pg_get_constraintdef(con.oid) "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_namespace nsp ON nsp.oid = rel.relnamespace "
                "WHERE con.conname = 'ck_order_inquiry_links_one_target' "
                "AND rel.relname = 'order_inquiry_links' "
                "AND nsp.nspname = ANY (current_schemas(false))"
            )
        ).scalar()
        assert check_def is not None
        assert "reserve_request_row_id" not in check_def, (
            f"downgrade must restore the two-target CHECK, got: {check_def}"
        )


# --------------------------------------------------------------------------------- #
# RS-18                                                                              #
# --------------------------------------------------------------------------------- #


def test_link_check_exactly_one_target(api):
    client, world = api
    row = _open_row(world, qty="50")
    po_line = _purchase_order(world.db, world.company_id)["line"]

    request = client.post(
        REQUEST_URL(world.inquiry.id), json={"rows": [{"row_id": row.id, "qty_requested": "50"}]}
    ).json()
    world.db.commit()
    from app.models.project_so import OrderInquiryReserveRequestRow

    request_row = (
        world.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.row_id == row.id)
        .one()
    )

    with pytest.raises(IntegrityError):
        world.db.add(
            OrderInquiryLink(
                id=_uid(), company_id=world.company_id, row_id=row.id,
                po_line_id=po_line.id, reserve_request_row_id=request_row.id,
                document="two-targets", qty=Decimal("1"),
            )
        )
        world.db.flush()
    world.db.rollback()

    with pytest.raises(IntegrityError):
        world.db.add(
            OrderInquiryLink(
                id=_uid(), company_id=world.company_id, row_id=row.id,
                document="no-target", qty=Decimal("1"),
            )
        )
        world.db.flush()
    world.db.rollback()

    world.db.add(
        OrderInquiryLink(
            id=_uid(), company_id=world.company_id, row_id=row.id,
            reserve_request_row_id=request_row.id, document="reserve-only", qty=Decimal("1"),
        )
    )
    world.db.flush()  # must not raise


# --------------------------------------------------------------------------------- #
# RS-19                                                                              #
# --------------------------------------------------------------------------------- #


def test_cancel_request_only_while_requested(api, monkeypatch):
    client, world = api
    _register()
    calls = _captured_dispatches(monkeypatch)

    row = _open_row(world, qty="50")
    request = client.post(
        REQUEST_URL(world.inquiry.id), json={"rows": [{"row_id": row.id, "qty_requested": "50"}]}
    ).json()
    world.db.commit()
    calls.clear()

    cancel = client.post(CANCEL_URL(request["id"]))
    assert cancel.status_code == 200, cancel.text
    world.db.commit()

    from app.models.project_so import OrderInquiryReserveRequest

    reloaded = (
        world.db.query(OrderInquiryReserveRequest).filter(OrderInquiryReserveRequest.id == request["id"]).one()
    )
    assert reloaded.state == "cancelled"
    assert reloaded.cancelled_by is not None
    assert reloaded.cancelled_at is not None
    assert _requested_calls(calls) == [] and _reserved_calls(calls) == [], "cancel sends no email"

    # A reserved request cannot be cancelled.
    row_b = _open_row(world, qty="20")
    request_b = client.post(
        REQUEST_URL(world.inquiry.id), json={"rows": [{"row_id": row_b.id, "qty_requested": "20"}]}
    ).json()
    world.db.commit()
    from app.models.project_so import OrderInquiryReserveRequestRow

    request_row_b = (
        world.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.row_id == row_b.id)
        .one()
    )
    client.post(
        COMMIT_URL(world.inquiry.id),
        json={"reserves": [{"row_id": row_b.id, "warehouse_id": world.site.id, "qty_reserved": "20"}]},
    )
    world.db.commit()

    reserved_cancel = client.post(CANCEL_URL(request_b["id"]))
    assert reserved_cancel.status_code == 409, reserved_cancel.text

    from app.services.form_action_registry import get_action

    action = get_action("order_inquiry_reserve_request.cancel")
    assert action is not None, "the deferred action must be registered (record_actions.py)"


# --------------------------------------------------------------------------------- #
# RS-20 (real worklist reader, borrowed `api` fixture from test_order_inquiry_worklist) #
# --------------------------------------------------------------------------------- #


def test_reserve_state_derived(worklist_api):
    client, db, company_id, seeded = worklist_api
    inquiry = db.get(OrderInquiry, seeded["authored_row"].order_inquiry_id)
    line = _line_on_authored_order(db, company_id, seeded, qty="90", day=7)
    row = _row(
        db, company_id, inquiry, so_line_id=line.id, item_code=f"{WL_MARKER}-STATE",
        qty="90", state=INQUIRY_RAISED, delivery_date=date(2026, 4, 7),
    )
    db.commit()

    body_none = client.get(LIST, params={"delivery_month": "2026-04"}).json()
    entry_none = next(e for e in body_none["data"] if e["id"] == row.id)
    assert entry_none.get("reserve_state") is None

    warehouse = _warehouse(db, f"ZZT-{_uid()[:6]}".upper())
    from app.services.order_inquiry_reserve_service import OrderInquiryReserveService

    requester_id = _user(db, f"{WL_MARKER} requester2")
    service = OrderInquiryReserveService(db)
    request = service.create_request(
        inquiry_id=inquiry.id,
        rows=[{"row_id": row.id, "qty_requested": Decimal("90"), "warehouse_id": warehouse.id}],
        note=None,
        actor_user_id=requester_id,
    )
    db.commit()

    body_requested = client.get(LIST, params={"delivery_month": "2026-04"}).json()
    entry_requested = next(e for e in body_requested["data"] if e["id"] == row.id)
    assert entry_requested["reserve_state"] == "requested"

    from app.models.project_so import OrderInquiryReserveRequestRow

    request_row = (
        db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.request_id == request.id)
        .one()
    )
    reserver_id = _user(db, f"{WL_MARKER} reserver2")
    # Reserved SHORT (50 of 90, with a reason) rather than in full, so the row still has
    # 40 remaining - the fixture this test's own next step needs to prove "reserved then
    # requested again reads requested" ON THE SAME ROW, rather than reaching for a fresh
    # one with nothing to do with the transition being pinned.
    service.commit_request(
        inquiry_id=inquiry.id,
        reserves=[
            {
                "row_id": row.id,
                "warehouse_id": warehouse.id,
                "qty_reserved": Decimal("50"),
                "reason": "BRW only has 50",
            }
        ],
        amendments=[],
        actor_user_id=reserver_id,
    )
    db.commit()

    body_reserved = client.get(LIST, params={"delivery_month": "2026-04"}).json()
    entry_reserved = next(e for e in body_reserved["data"] if e["id"] == row.id)
    assert entry_reserved["reserve_state"] == "reserved"
    assert entry_reserved["reserved_qty"] == "50"

    # Reserved then requested again (on the balance, 90 - 50 = 40) -> reads "requested".
    second_request = service.create_request(
        inquiry_id=inquiry.id,
        rows=[{"row_id": row.id, "qty_requested": Decimal("40"), "warehouse_id": warehouse.id}],
        note=None,
        actor_user_id=requester_id,
    )
    db.commit()
    assert second_request.ordinal == 2

    body_requested_again = client.get(LIST, params={"delivery_month": "2026-04"}).json()
    entry_requested_again = next(e for e in body_requested_again["data"] if e["id"] == row.id)
    assert entry_requested_again["reserve_state"] == "requested", (
        "a row reserved then requested again must read requested, not reserved (AC-RS-20)"
    )
    # Nothing left to request again on row2 (fully reserved) - the "reserved then
    # requested again" half is exhaustively covered by RS-5's own HTTP-level test
    # (`test_second_request_after_reserved_allowed`), which already proves ordinal 2 is
    # accepted; asserting the CHIP specifically on that same shape here would duplicate
    # RS-5 rather than add new coverage, so this test stops at "reserved" above.


# --------------------------------------------------------------------------------- #
# RS-21                                                                              #
# --------------------------------------------------------------------------------- #


def test_rollback_discards_pending_dispatch(api, monkeypatch):
    client, world = api
    _register()
    calls = _captured_dispatches(monkeypatch)

    row = _open_row(world, qty="50")

    savepoint = world.db.begin_nested()
    service = _service(world.db)
    service.create_request(
        inquiry_id=world.inquiry.id,
        rows=[{"row_id": row.id, "qty_requested": Decimal("50"), "warehouse_id": world.site.id}],
        note=None,
        actor_user_id=world.requester,
    )
    savepoint.rollback()

    world.db.commit()  # an unrelated commit: nothing above may still fire

    assert _requested_calls(calls) == [], "a rolled-back request must dispatch nothing"

    from app.models.project_so import OrderInquiryReserveRequest

    assert (
        world.db.query(OrderInquiryReserveRequest)
        .filter(OrderInquiryReserveRequest.order_inquiry_id == world.inquiry.id)
        .count()
        == 0
    ), "the rolled-back request must not exist either"


# --------------------------------------------------------------------------------- #
# Security fix round (SF-1..SF-5, N-2, N-4)                                         #
# --------------------------------------------------------------------------------- #
#
# Written against the CONTRACT the captain's test list names, not against
# `order_inquiry_reserve_service.py` / `order_inquiries.py` as they stand today - both
# already exist (this round is a FIX round on an implemented slice, not slice 2/3's own
# test-first pass above), so "red" here means "the current code does the wrong thing",
# never "the module is missing". Each test names, in its own docstring, exactly what the
# current code does and why that is the gap.


def test_cancel_by_third_party_forbidden(api):
    """SF-1: `cancel_order_inquiry_reserve_request` gates on
    `require_any_permission([ACKNOWLEDGE, RESERVE])` alone - there is no ownership check
    at all, so ANY user holding ACKNOWLEDGE (purchasing at large, not just the one who
    raised THIS request) can cancel a colleague's open ask today. The requester
    cancelling their own request, and a RESERVE holder (Eling) cancelling anyone's, must
    both keep working."""
    client, world = api
    row_a = _open_row(world, qty="50")
    with _as(world.db, world.requester, REQUESTER_PERMISSIONS) as client_a:
        created = client_a.post(
            REQUEST_URL(world.inquiry.id),
            json={"rows": [{"row_id": row_a.id, "qty_requested": "50"}]},
        )
    assert created.status_code == 201, created.text
    world.db.commit()
    request_id = created.json()["id"]

    stranger = _user(world.db, f"{MARKER} Stranger")
    world.db.commit()
    with _as(world.db, stranger, REQUESTER_PERMISSIONS) as client_stranger:
        forbidden = client_stranger.post(CANCEL_URL(request_id))
    assert forbidden.status_code == 403, forbidden.text
    world.db.commit()

    with _as(world.db, world.requester, REQUESTER_PERMISSIONS) as client_a2:
        own_cancel = client_a2.post(CANCEL_URL(request_id))
    assert own_cancel.status_code == 200, own_cancel.text
    world.db.commit()

    row_b = _open_row(world, qty="30")
    with _as(world.db, world.requester, REQUESTER_PERMISSIONS) as client_a3:
        created2 = client_a3.post(
            REQUEST_URL(world.inquiry.id),
            json={"rows": [{"row_id": row_b.id, "qty_requested": "30"}]},
        )
    assert created2.status_code == 201, created2.text
    world.db.commit()
    request_id2 = created2.json()["id"]

    with _as(world.db, world.reserver, RESERVER_PERMISSIONS) as client_reserver:
        reserver_cancel = client_reserver.post(CANCEL_URL(request_id2))
    assert reserver_cancel.status_code == 200, reserver_cancel.text


# RETIRED (SF-2's own guard). Its whole premise was the OLD all-rows payload naming the
# SAME `request_row_id` twice in one `rows: [...]` array - `PLAN-oi-request-cs-reserve.md`
# section 6c F2 supersedes the shape itself: the per-row route names exactly one row (on
# the PATH, never in the body), so there is no "named twice in one call" to guard against
# any more - answering the same row a second time is `test_reserve_only_once`'s own 409,
# already ported above.


def test_request_and_reserve_reject_bad_warehouse(api):
    """SF-3: neither `create_request` nor `reserve` validates `warehouse_id` at all today
    - not that it is active, not that it belongs to this company - so a caller who passes
    a bare id gets it written straight onto the request row / the link's own document,
    inactive or foreign-tenant or not."""
    client, world = api
    from app.models.inventory import Warehouse

    inactive = _warehouse(world.db, f"ZZT-{_uid()[:6]}".upper())
    inactive.is_active = False
    world.db.commit()

    row_inactive = _open_row(world, qty="50")
    bad_request = client.post(
        REQUEST_URL(world.inquiry.id),
        json={
            "rows": [
                {"row_id": row_inactive.id, "qty_requested": "50", "warehouse_id": inactive.id}
            ]
        },
    )
    assert bad_request.status_code == 422, bad_request.text
    assert row_inactive.item_code in bad_request.text, bad_request.text

    from app.models.company import Company

    other_company_id = _uid()
    with company_scope(world.db, None):
        world.db.add(
            Company(id=other_company_id, name=f"{MARKER} Other Co", code=f"ZZ{_uid()[:6]}")
        )
        world.db.flush()
        foreign_warehouse = Warehouse(
            id=_uid(),
            company_id=other_company_id,
            warehouse_code=f"ZZT{_uid()[:6]}",
            warehouse_name=f"{MARKER} foreign",
            is_active=True,
        )
        world.db.add(foreign_warehouse)
        world.db.flush()
    world.db.commit()

    row_foreign = _open_row(world, qty="50")
    foreign_request = client.post(
        REQUEST_URL(world.inquiry.id),
        json={
            "rows": [
                {"row_id": row_foreign.id, "qty_requested": "50", "warehouse_id": foreign_warehouse.id}
            ]
        },
    )
    assert foreign_request.status_code == 422, foreign_request.text

    row_ok = _open_row(world, qty="50")
    ok_request = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": row_ok.id, "qty_requested": "50", "warehouse_id": world.site.id}]},
    )
    assert ok_request.status_code == 201, ok_request.text
    world.db.commit()

    row_reserve = _open_row(world, qty="50")
    req = client.post(
        REQUEST_URL(world.inquiry.id), json={"rows": [{"row_id": row_reserve.id, "qty_requested": "50"}]}
    ).json()
    world.db.commit()

    bad_reserve = client.post(
        COMMIT_URL(world.inquiry.id),
        json={
            "reserves": [
                {"row_id": row_reserve.id, "warehouse_id": inactive.id, "qty_reserved": "50"}
            ]
        },
    )
    assert bad_reserve.status_code == 422, bad_reserve.text


def test_reserve_holder_can_list_requests(api):
    """SF-4: `GET .../reserve-requests` gates on ACKNOWLEDGE alone - a user holding ONLY
    the RESERVE grant (Eling, who never raises a request, only answers one) currently
    gets 403 reading her own worklist's Lines tab. `record_actions.py` names
    `projects.order_inquiries.acknowledge` as the ONLY slug for the deferred cancel
    action too, so a reserve-only holder cannot even start that countdown."""
    client, world = api
    row = _open_row(world, qty="50")
    created = client.post(
        REQUEST_URL(world.inquiry.id), json={"rows": [{"row_id": row.id, "qty_requested": "50"}]}
    )
    assert created.status_code == 201, created.text
    world.db.commit()
    request_id = created.json()["id"]

    reserve_only = ["projects.projects.view", RESERVE_PERMISSION]
    with _as(world.db, world.reserver, reserve_only) as reserve_client:
        listed = reserve_client.get(REQUEST_URL(world.inquiry.id))
    assert listed.status_code == 200, listed.text
    assert any(entry["id"] == request_id for entry in listed.json()), listed.text

    with _as(world.db, world.reserver, []) as nobody_client:
        denied = nobody_client.get(REQUEST_URL(world.inquiry.id))
    assert denied.status_code == 403, denied.text

    from app.services.form_action_registry import get_action

    action = get_action("order_inquiry_reserve_request.cancel")
    assert action is not None, "the deferred action must still be registered"

    with _as(world.db, world.reserver, reserve_only) as reserve_client2:
        parked = reserve_client2.post(
            "/api/v1/pending-actions",
            json={
                "action_key": "order_inquiry_reserve_request.cancel",
                "entity_type": "order_inquiry_reserve_request",
                "entity_id": request_id,
                "payload": {},
            },
        )
    assert parked.status_code == 202, (
        "a reserve-permission holder must be able to start the deferred cancel: "
        f"{parked.status_code} {parked.text}"
    )


def test_reserve_recheck_remaining_after_new_po_link(api):
    """SF-5: `reserve()` checks `qty_reserved` against `rr.qty_requested`, the number
    frozen at REQUEST time - never against the row's CURRENT remaining. A PO link placed
    on the row after the request is raised, and before Eling confirms, silently opens the
    door to reserving more than the row actually has left, and the two links together
    then exceed the row's own qty."""
    client, world = api
    row = _open_row(world, qty="100")
    request = client.post(
        REQUEST_URL(world.inquiry.id), json={"rows": [{"row_id": row.id, "qty_requested": "100"}]}
    ).json()
    world.db.commit()

    po_line = _purchase_order(world.db, world.company_id)["line"]
    world.db.add(
        OrderInquiryLink(
            id=_uid(),
            company_id=world.company_id,
            row_id=row.id,
            po_line_id=po_line.id,
            document="ZZT-PO-LATE",
            qty=Decimal("60"),
        )
    )
    world.db.commit()
    # remaining is now 100 - 60 = 40, but the request row still carries qty_requested=100.

    from app.models.project_so import OrderInquiryReserveRequestRow

    request_row = (
        world.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.row_id == row.id)
        .one()
    )

    over = client.post(
        COMMIT_URL(world.inquiry.id),
        json={"reserves": [{"row_id": row.id, "warehouse_id": world.site.id, "qty_reserved": "100"}]},
    )
    assert over.status_code == 422, over.text
    assert "40" in over.text, over.text
    world.db.commit()

    ok = client.post(
        COMMIT_URL(world.inquiry.id),
        json={"reserves": [{"row_id": row.id, "warehouse_id": world.site.id, "qty_reserved": "40"}]},
    )
    assert ok.status_code == 200, ok.text
    world.db.commit()

    total_linked = (
        world.db.query(sa.func.coalesce(sa.func.sum(OrderInquiryLink.qty), 0))
        .filter(OrderInquiryLink.row_id == row.id)
        .scalar()
    )
    assert Decimal(str(total_linked)) <= Decimal(str(row.qty)), (
        f"links must never exceed the row's own qty: {total_linked} > {row.qty}"
    )


def test_body_ids_malformed_are_422(api):
    """N-2: neither `row_id` (CREATE) nor `warehouse_id` (per-row reserve) is
    UUID-typed at the schema level, so a malformed value reaches a raw
    `.id.in_([...])`/dict-key comparison downstream. `create_request` hits Postgres
    with an invalid uuid literal and 500s instead of 422; a `row_id` duplicated inside
    one CREATE payload is not caught at all (two `OrderInquiryReserveRequestRow`s for
    the SAME row, together requesting more than the row's own remaining). Section 6c
    F2 moved the per-row route's own row id onto the PATH - `validate_uuid_path` is
    what guards a malformed one there now, same family of defect, same assertion."""
    client, world = api

    bad_create = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": "not-a-uuid", "qty_requested": "50"}]},
    )
    assert bad_create.status_code == 422, bad_create.text

    row = _open_row(world, qty="50")
    created = client.post(
        REQUEST_URL(world.inquiry.id), json={"rows": [{"row_id": row.id, "qty_requested": "50"}]}
    )
    assert created.status_code == 201, created.text
    world.db.commit()

    # Round 4 (6e.1): `row_id` moved back into the BODY (the commit route answers many
    # rows in one call), so a malformed one is `CommitReserveRowIn.row_id`'s own
    # `pattern=UUID_PATTERN` field validator, 422, never a 500.
    bad_reserve = client.post(
        COMMIT_URL(world.inquiry.id),
        json={
            "reserves": [
                {"row_id": "not-a-uuid", "warehouse_id": world.site.id, "qty_reserved": "50"}
            ]
        },
    )
    assert bad_reserve.status_code == 422, bad_reserve.text

    row_dup = _open_row(world, qty="30")
    dup_row_create = client.post(
        REQUEST_URL(world.inquiry.id),
        json={
            "rows": [
                {"row_id": row_dup.id, "qty_requested": "30"},
                {"row_id": row_dup.id, "qty_requested": "30"},
            ]
        },
    )
    assert dup_row_create.status_code == 422, dup_row_create.text


def test_note_and_reason_length_capped(api):
    """N-4: neither `note` (`CreateReserveRequestIn`) nor `reason` (`ReserveRowIn`, the
    per-row route's own schema - S7, review round 2: the old all-rows `ReserveAnswerRowIn`
    this docstring used to name is retired) carries a `max_length` today, so an
    arbitrarily long value is accepted and lands verbatim in an outgoing email body / on
    the worklist chip. Caps per the captain's list: note 5000, reason 2000."""
    client, world = api

    row = _open_row(world, qty="50")
    too_long_note = "x" * 5001
    resp_note = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": row.id, "qty_requested": "50"}], "note": too_long_note},
    )
    assert resp_note.status_code == 422, resp_note.text

    row_ok = _open_row(world, qty="20")
    ok_note = "x" * 5000
    resp_note_ok = client.post(
        REQUEST_URL(world.inquiry.id),
        json={"rows": [{"row_id": row_ok.id, "qty_requested": "20"}], "note": ok_note},
    )
    assert resp_note_ok.status_code == 201, resp_note_ok.text
    world.db.commit()

    row_reason = _open_row(world, qty="10")
    request = client.post(
        REQUEST_URL(world.inquiry.id), json={"rows": [{"row_id": row_reason.id, "qty_requested": "10"}]}
    ).json()
    world.db.commit()

    from app.models.project_so import OrderInquiryReserveRequestRow

    request_row = (
        world.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.row_id == row_reason.id)
        .one()
    )

    too_long_reason = "y" * 2001
    resp_reason = client.post(
        COMMIT_URL(world.inquiry.id),
        json={
            "reserves": [
                {
                    "row_id": row_reason.id,
                    "warehouse_id": world.site.id,
                    "qty_reserved": "5",
                    "reason": too_long_reason,
                }
            ]
        },
    )
    assert resp_reason.status_code == 422, resp_reason.text

    ok_reason = "y" * 2000
    resp_reason_ok = client.post(
        COMMIT_URL(world.inquiry.id),
        json={
            "reserves": [
                {
                    "row_id": row_reason.id,
                    "warehouse_id": world.site.id,
                    "qty_reserved": "5",
                    "reason": ok_reason,
                }
            ]
        },
    )
    assert resp_reason_ok.status_code == 200, resp_reason_ok.text


# --------------------------------------------------------------------------------- #
# Reviewer round (fix round, same lane) - test files only                           #
# --------------------------------------------------------------------------------- #


def test_po_ref_skips_reserve_link(api):
    """Reviewer SF-1: `refresh_link_state` derives `po_ref`/`po_line_id`/`spo_ref` from
    `_links_of(row.id)[0]` - "the first link, oldest first" - with no regard for WHAT
    kind of link that is. A reserve link is written with `document=f"Reserved @ {code}"`
    (`order_inquiry_reserve_service.reserve`), so a row reserved before it is ever placed
    on a PO reads `po_ref == "Reserved @ ..."` - a fake PO reference on every reader that
    prints `row.po_ref` directly, though NOT the worklist's own `po_number` column, which
    is a separate, already-correct mechanism (`_LINKED_PO_ID` INNER-joins
    `PurchaseOrderLine` on `OrderInquiryLink.po_line_id`, which a reserve link never
    carries) - asserted here too so a fix does not accidentally break the one reader that
    already gets this right."""
    client, world = api
    row = _open_row(world, qty="100", delivery_date=date(2026, 10, 1))
    request = client.post(
        REQUEST_URL(world.inquiry.id), json={"rows": [{"row_id": row.id, "qty_requested": "50"}]}
    ).json()
    world.db.commit()

    from app.models.project_so import OrderInquiryReserveRequestRow

    request_row = (
        world.db.query(OrderInquiryReserveRequestRow)
        .filter(OrderInquiryReserveRequestRow.row_id == row.id)
        .one()
    )
    reserve_resp = client.post(
        COMMIT_URL(world.inquiry.id),
        json={"reserves": [{"row_id": row.id, "warehouse_id": world.site.id, "qty_reserved": "50"}]},
    )
    assert reserve_resp.status_code == 200, reserve_resp.text
    world.db.commit()

    world.db.expire_all()
    refreshed = world.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row.id).one()
    assert refreshed.po_ref is None, refreshed.po_ref
    assert refreshed.po_line_id is None, refreshed.po_line_id
    assert refreshed.spo_ref is None, refreshed.spo_ref

    po_line = _purchase_order(world.db, world.company_id)["line"]
    from app.services.project_order_inquiry_service import ProjectOrderInquiryService

    world.db.add(
        OrderInquiryLink(
            id=_uid(), company_id=world.company_id, row_id=row.id,
            po_line_id=po_line.id, document="ZZT-PO-AFTER", qty=Decimal("20"),
        )
    )
    world.db.flush()
    ProjectOrderInquiryService(world.db).refresh_link_state([refreshed])
    world.db.commit()

    world.db.expire_all()
    refreshed2 = world.db.query(OrderInquiryRow).filter(OrderInquiryRow.id == row.id).one()
    assert refreshed2.po_ref == "ZZT-PO-AFTER", refreshed2.po_ref

    body = client.get(LIST, params={"delivery_month": "2026-10"}).json()
    entry = next((e for e in body["data"] if e["id"] == row.id), None)
    assert entry is not None, body
    assert not str(entry["po_number"] or "").startswith("Reserved @ "), entry["po_number"]


def test_ordinal_collision_does_not_500(api):
    """SF-8: `ordinal = coalesce(max(ordinal), 0) + 1` is a read-then-write with no
    locking, and the unique constraint `uq_order_inquiry_reserve_requests_ordinal` is the
    only thing standing between two concurrent requests and a raw `IntegrityError`. Two
    real overlapping transactions cannot be reproduced inside one rolled-back test
    session (`blank_session()`'s own savepoint-per-commit never reaches a second
    connection), so this simulates the LOSING side of that race directly: a one-shot
    `before_insert` listener sneaks a row bearing the SAME ordinal the service is about
    to write straight onto the table, moments before the service's own INSERT lands -
    exactly what "another session already committed this ordinal first" looks like from
    inside the same flush."""
    client, world = api
    from app.models.project_so import OrderInquiryReserveRequest
    from sqlalchemy import event

    row = _open_row(world, qty="50")
    collided = {"done": False}

    def _sneak_in_a_collision(mapper, connection, target):  # noqa: ANN001
        if collided["done"] or str(target.order_inquiry_id) != str(world.inquiry.id):
            return
        collided["done"] = True
        connection.execute(
            sa.text(
                "INSERT INTO order_inquiry_reserve_requests "
                "(id, company_id, order_inquiry_id, ordinal, state) "
                "VALUES (:id, :company_id, :order_inquiry_id, :ordinal, 'requested')"
            ),
            {
                "id": _uid(),
                "company_id": world.company_id,
                "order_inquiry_id": target.order_inquiry_id,
                "ordinal": target.ordinal,
            },
        )

    event.listen(OrderInquiryReserveRequest, "before_insert", _sneak_in_a_collision)
    try:
        response = client.post(
            REQUEST_URL(world.inquiry.id),
            json={"rows": [{"row_id": row.id, "qty_requested": "50"}]},
        )
    finally:
        event.remove(OrderInquiryReserveRequest, "before_insert", _sneak_in_a_collision)

    assert response.status_code in (201, 409), (
        "a real ordinal collision must land as a clean retry or a 409, never an "
        f"unhandled IntegrityError: {response.status_code} {response.text}"
    )
    if response.status_code == 201:
        assert response.json()["ordinal"] != 1, "the winning row already holds ordinal 1"


def test_request_dispatch_only_after_commit(api, monkeypatch):
    """Kill-test gap on AC-RS-1: RS-1 only proves ONE dispatch fires after commit; it
    never proves NONE fire before it. A dispatch fired inside `create_request` itself
    (rather than queued on `Session.info` for the `after_commit` listener to drain) would
    email out a reserve request whose write later rolls back - this asserts the queue
    empty mid-transaction and drained to exactly one only once the commit actually
    lands."""
    client, world = api
    _register()
    calls = _captured_dispatches(monkeypatch)

    row = _open_row(world, qty="50")
    service = _service(world.db)
    service.create_request(
        inquiry_id=world.inquiry.id,
        rows=[{"row_id": row.id, "qty_requested": Decimal("50"), "warehouse_id": world.site.id}],
        note=None,
        actor_user_id=world.requester,
    )
    assert _requested_calls(calls) == [], "nothing may dispatch before the commit lands"

    world.db.commit()
    assert len(_requested_calls(calls)) == 1, _requested_calls(calls)


def test_normalize_recipient_config_keeps_new_bools():
    """AC-RS-16 gap: `include_raiser`/`include_requester` are read by `_execute`'s own
    recipient resolution (RS-16 above already proves `resolve_recipients` reads them),
    but nothing pinned that `_normalize_recipient_config` - the ONE function every write
    path funnels a payload through - actually keeps them, coerced to bool, and defaults
    them to `False` rather than dropping them, the way the FALSY-config early return
    (`if not config: return {...}`) does today: that branch builds its own dict from
    scratch and never mentions either key, so a caller reading `include_raiser` off a
    freshly-created automation's default `recipient_config` gets `None`, not `False`."""
    from app.services.automation_service import AutomationService

    normalized = AutomationService._normalize_recipient_config(
        {"include_raiser": "true", "include_requester": 1}
    )
    assert normalized["include_raiser"] is True
    assert normalized["include_requester"] is True

    partial = AutomationService._normalize_recipient_config({"one_email": True})
    assert partial.get("include_raiser") is False
    assert partial.get("include_requester") is False

    empty = AutomationService._normalize_recipient_config({})
    assert empty.get("include_raiser") is False, empty
    assert empty.get("include_requester") is False, empty


def _reserve_fixture_context() -> dict:
    """One row short-reserved with a reason, one reserved in full with none - the exact
    pair AC-RS-30/31/32 describe. `remaining` is deliberately the SAME field the module
    docstring says both templates read at different moments (REMAINING before a reserve,
    BALANCE after one) - `_row_context`'s own shape, hand-built here rather than derived
    through the live service so this test stays a pure template-render check."""
    return {
        "reserve": {
            "inquiry_no": "OI-2609-0678",
            "ordinal": 1,
            "so_number": "SO402757",
            "customer": "BUIMACO",
            "project": "TUJU RESIDENCE",
            "requested_by": {"name": "Joey Lim", "email": "joey@sorento.test"},
            "requested_at": "22/09/2026",
            "note": None,
            "rows": [
                {
                    "item_code": "B2155-NL-BLUE",
                    "delivery_date": "21/07/2026",
                    "qty": "139",
                    "remaining": "89",
                    "qty_requested": "139",
                    "location": "BRW",
                    "qty_reserved": "50",
                    "reason": "BRW only has 50 in stock",
                },
                {
                    "item_code": "CKS1050",
                    "delivery_date": "24/07/2026",
                    "qty": "30",
                    "remaining": "0",
                    "qty_requested": "30",
                    "location": "MWH",
                    "qty_reserved": "30",
                    "reason": None,
                },
            ],
            "row_count": 2,
            "state": "reserved",
            "link": "https://crm.test/project-sales/order-inquiries?query=SO402757&reserve=req-1",
        },
        "actor": {"name": "Eling Wong", "email": "eling@sorento.test"},
        "raiser": {"name": "Raiser Person", "email": "raiser@sorento.test"},
        "requester": {"name": "Joey Lim", "email": "joey@sorento.test"},
        "today": "22/09/2026",
    }


def test_reserve_templates_render():
    """Reviewer 11 (AC-RS-30/31/32): the two seeded templates against a fixture context -
    the request mail's own column order (AC-RS-30: ITEM CODE, DELIVERY DATE, QTY,
    REMAINING, REQUESTED, LOCATION) and the reserved mail's (AC-RS-31: ITEM CODE, QTY,
    REQUESTED, RESERVED, BALANCE, LOCATION, REASON), a None reason printing blank never
    the word "None" (the exact prod-copy defect `test_order_inquiry_handover_automation
    .py`'s own AC-H14 extension already caught once for the handover template), inline
    border/padding on every cell, and the warehouse code Eling actually chose printing as
    LOCATION (AC-RS-32)."""
    import re

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    from app.models.email_template import EmailTemplate
    from app.services.email_template_service import EmailTemplateService

    module = _load_reserve_seed_migration()
    with blank_session() as db:
        ctx = MigrationContext.configure(db.connection())
        with Operations.context(ctx):
            module.upgrade()

        requested_template = (
            db.query(EmailTemplate)
            .filter(EmailTemplate.code == "order_inquiry_reserve_requested_default")
            .one()
        )
        reserved_template = (
            db.query(EmailTemplate)
            .filter(EmailTemplate.code == "order_inquiry_reserved_default")
            .one()
        )

        context = _reserve_fixture_context()

        requested_rendered = EmailTemplateService(db).render(requested_template, context)
        assert requested_rendered["subject"] == "Reserve request: OI-2609-0678 #1 - SO402757", (
            requested_rendered["subject"]
        )
        requested_html = requested_rendered["body_html"]
        for header in ("ITEM CODE", "DELIVERY DATE", "QTY", "REMAINING", "REQUESTED", "LOCATION"):
            assert header in requested_html, header
        # Two substrings either side of the query string's own `&`, never the raw link
        # whole: Jinja's autoescape turns `&` into `&amp;` in the rendered HTML, and a
        # test asserting the UNESCAPED link would be a fixture bug, not a real defect.
        assert "order-inquiries?query=SO402757" in requested_html, requested_html
        assert "reserve=req-1" in requested_html, requested_html

        reserved_rendered = EmailTemplateService(db).render(reserved_template, context)
        assert reserved_rendered["subject"] == "Reserved: OI-2609-0678 #1 - SO402757", (
            reserved_rendered["subject"]
        )
        reserved_html = reserved_rendered["body_html"]

        headers_in_order = [
            "ITEM CODE", "QTY", "REQUESTED", "RESERVED", "BALANCE", "LOCATION", "REASON",
        ]
        positions = [reserved_html.index(header) for header in headers_in_order]
        assert positions == sorted(positions), (
            f"the reserved table's columns must read {headers_in_order} in that order: "
            f"{reserved_html}"
        )

        # #1349: the rendered mail is a whole branded document; the border pin is about
        # the template's OWN table, so it reads the authored body, not the layout's cells.
        from tests._email_body import authored_html

        cells = re.findall(r"<(?:td|th)\b[^>]*>", authored_html(reserved_html))
        assert cells, "no table cells found in the reserved template's own body"
        for tag_html in cells:
            assert "border" in tag_html, tag_html
            assert "padding" in tag_html, tag_html

        assert "None" not in reserved_html, (
            "a null reason must print blank, never the literal word None: " + reserved_html
        )
        assert "BRW" in reserved_html, "the stored warehouse code must print as LOCATION"
        assert "order-inquiries?query=SO402757" in reserved_html, reserved_html
        assert "reserve=req-1" in reserved_html, reserved_html
