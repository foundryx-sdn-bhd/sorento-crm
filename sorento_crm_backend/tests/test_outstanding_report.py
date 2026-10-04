"""Phase 2 RED tests - `GET /api/v1/order-management/outstanding-report`.

`documentation/plans/chatbot/PLAN-chatbot-outstanding-report.md` (Backend contract, Slice
S2); `documentation/plans/chatbot/chatbot-outstanding-report-acceptance-criteria.md`
AC-1110 to AC-1119.

Written BEFORE the route/service/schema exist (Phase 2, test-first). Every test hits the
route over HTTP and must fail with 404 (route not found) until the coder wires it up - not
an import error, not a fixture bug. Postgres only (`tests/_pg_fixture.py`), every row seeded
here; CI's database has none.

AC-1115, REWRITTEN THREE TIMES:
- R1 (owner testing round 1, 13 Sep 2026): "most of the DO are delivered right so what's
  outstanding? I thought outstanding means still got some pending quantity." Dropped
  `do_qty`/`delivered_qty` from the block and both breakdowns; every figure became pending-only.
- R3 (owner testing round 2, 13 Sep 2026): "need to show delivered also... if it is 0 then we
  show 0, don't hide." Brought `do_qty`/`delivered_qty` back on `do_rows[]` ONLY.
- R6 (owner testing round 3, 13 Sep 2026): "need to show the delivered also, so the by
  location and by customer needs to be the DO qty (O/S: {pending})." Brings `do_qty` /
  `delivered_qty` back on the BLOCK too, and `do_qty` back on BOTH breakdowns, summed over
  EVERY DO in scope (pending AND delivered) - R1's population rule now survives only for
  `do_count` / `do_date_min` / `do_date_max` / `do_rows[]`, which stay pending-DOs-only.
  `pending_qty` = SUM over pending DOs; `do_qty` = SUM over every DO, pending and delivered;
  `delivered_qty` = the rest of it. `do_qty == delivered_qty + pending_qty` holds on the
  block and on every breakdown row.
- R11 (owner testing round 3, 13 Sep 2026, SAME sitting as R6/R10, supersedes R6):
  "the breakdown list should tally with whatever reported at the summary at the top" -
  choosing option 1 (breakdowns list only names with outstanding above 0). R6's TWO
  populations collapse back to ONE: `_outstanding_clause` alone, the same population
  `do_count`/dates/`do_rows[]` already used. `do_qty` now sums OUTSTANDING DOs only (not
  every DO), so `do_qty == pending_qty` and `delivered_qty` is always `0` given today's
  schema (`order_lines.quantity` carries no delivered/outstanding split below the DO
  header) - the field stays for shape parity, in case that ever changes. A name with
  ONLY a delivered DO is ABSENT from both breakdowns, not printed with `pending_qty` 0.

R10 (owner testing round 3, 13 Sep 2026, same sitting): "be it DO outstanding or SO
outstanding, we need to rank by highest quantity at the top." Every `*_by_location`/
`*_by_customer` array is sorted by the outstanding figure descending, ties by the total
descending, then name ascending, in the ROUTE (the presenter is a dumb pass-through -
pinned separately in `sorento_crm_mcp/tests/test_presenters_outstanding.py`).
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import RespondContact
from app.models.base import set_company_scope
from app.models.company import RespondContactCompany
from app.models.integration import Integration, IntegrationApiKey
from app.models.order import Order, OrderLine, OrderStatus, SalesOrder, SalesOrderLine
from app.models.user import (
    User,
    UserPermission,
    UserRole,
    UserRoleAssignment,
    UserRolePermission,
)
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.integration_key_service import IntegrationKeyService

from tests._mc_lookup_seed import customer, order, order_line, product, seed_mocha, warehouse
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/order-management/outstanding-report"
PERMISSION_SLUG = "order_management.orders.view"


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


def _seed_superadmin(db) -> dict:
    """A principal that clears `require_permission_with_api_key` via role bypass,
    so every test EXCEPT the permission-focused AC-1118 can ignore RBAC plumbing."""
    user = User(id=str(uuid.uuid4()), email=f"{unique_code('SA')}@test.com", name="ZZT Superadmin", status="ACTIVE")
    role = UserRole(id=str(uuid.uuid4()), slug="superadmin", name="Superadmin")
    db.add_all([user, role])
    db.flush()
    db.add(UserRoleAssignment(user_id=user.id, role_id=role.id))
    db.flush()
    return {"id": user.id, "email": user.email}


@pytest.fixture
def client(db):
    principal = _seed_superadmin(db)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    app.dependency_overrides[apply_company_scope] = _override_scope
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------- seed helpers


def _so_line(
    db,
    *,
    product_id,
    ordered,
    delivered,
    customer_id=None,
    warehouse_id=None,
    line_status="open",
    header_status="open",
    source_system=None,
    order_date=None,
    so_number=None,
):
    so = SalesOrder(
        id=str(uuid.uuid4()),
        so_number=so_number or unique_code("SO"),
        customer_id=customer_id,
        order_date=order_date,
        status=header_status,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(so)
    db.flush()
    db.add(
        SalesOrderLine(
            id=str(uuid.uuid4()),
            sales_order_id=so.id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            qty_ordered=ordered,
            qty_delivered=delivered,
            line_status=line_status,
            source_system=source_system,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    return so


def _delivered_status(db) -> OrderStatus:
    status = OrderStatus(id=str(uuid.uuid4()), status_code="delivered", status_name="Delivered")
    db.add(status)
    db.flush()
    return status


def _do(
    db,
    *,
    product_id,
    warehouse_id,
    qty,
    customer_id=None,
    order_date=None,
    actual_delivery_date=None,
    order_status_id=None,
    number=None,
):
    o = Order(
        id=str(uuid.uuid4()),
        order_number=number or unique_code("DO"),
        customer_id=customer_id,
        order_date=order_date,
        actual_delivery_date=actual_delivery_date,
        order_status_id=order_status_id,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(o)
    db.flush()
    db.add(
        OrderLine(
            id=str(uuid.uuid4()),
            order_id=o.id,
            product_id=product_id,
            warehouse_id=warehouse_id,
            quantity=qty,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    return o


# --------------------------------------------------------------------- AC-1110


def test_so_figures_share_one_population(client, db):
    """One product, four SO lines: only the live (open header + open line) line
    counts. Cancelled and retired-provisional (line_status='closed', regardless of
    source_system or qty_delivered) contribute to none of the three figures."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    _so_line(db, product_id=prod.id, ordered=10, delivered=3, header_status="open", line_status="open")
    _so_line(db, product_id=prod.id, ordered=10, delivered=10, header_status="open", line_status="closed")
    _so_line(db, product_id=prod.id, ordered=8, delivered=0, header_status="open", line_status="cancelled")
    _so_line(
        db,
        product_id=prod.id,
        ordered=5,
        delivered=0,
        header_status="open",
        line_status="closed",
        source_system="scm_order_inquiry",
    )
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "so"})
    assert resp.status_code == 200, resp.text
    so = resp.json()["so"]
    assert (so["ordered_qty"], so["transferred_qty"], so["outstanding_qty"]) == (10, 3, 7)
    assert so["ordered_qty"] == so["transferred_qty"] + so["outstanding_qty"]


# --------------------------------------------------------------------- AC-1111


def test_order_date_window_filters_so_and_do(client, db):
    """A 2026 window keeps only the 2026 SO/DO - in totals, by_location, by_customer
    and the row lists - and never filters on `actual_delivery_date`."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Window Customer")

    _so_line(
        db, product_id=prod.id, ordered=9, delivered=2, customer_id=cust.id, warehouse_id=wh.id,
        order_date=date(2025, 6, 1), so_number=unique_code("SO25"),
    )
    _so_line(
        db, product_id=prod.id, ordered=6, delivered=1, customer_id=cust.id, warehouse_id=wh.id,
        order_date=date(2026, 6, 1), so_number=unique_code("SO26"),
    )
    # A DO with no actual_delivery_date (never delivered) but IN the 2026 order_date window -
    # must still be counted, proving the window is on order_date, not actual_delivery_date.
    _do(
        db, product_id=prod.id, warehouse_id=wh.id, qty=40, customer_id=cust.id,
        order_date=date(2025, 7, 1), actual_delivery_date=None, number=unique_code("DO25"),
    )
    _do(
        db, product_id=prod.id, warehouse_id=wh.id, qty=25, customer_id=cust.id,
        order_date=date(2026, 7, 1), actual_delivery_date=None, number=unique_code("DO26"),
    )
    db.commit()

    resp = client.get(
        BASE,
        params={
            "product_code": prod.product_code,
            "scope": "both",
            "order_date_from": "2026-01-01",
            "order_date_to": "2026-12-31",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert (body["so"]["ordered_qty"], body["so"]["outstanding_qty"]) == (6, 5)
    assert body["so"]["so_count"] == 1
    assert {row["customer_name"] for row in body["so_by_customer"]} == {"ZZT Window Customer"}
    assert sum(row["ordered_qty"] for row in body["so_by_location"]) == 6
    assert [row["order_date"] for row in body["so_rows"]] == ["2026-06-01"]

    # R1 then R6 (13 Sep 2026): `pending_qty` covers outstanding DOs only, while
    # `do_qty` covers every DO in scope. Both seeded DOs here are pending
    # (`actual_delivery_date=None`), so the two figures coincide and `delivered_qty`
    # is 0 - the window assertion below is about the DATE filter either way.
    assert body["do"]["pending_qty"] == 25
    assert body["do"]["do_count"] == 1
    assert [row["pending_qty"] for row in body["do_rows"]] == [25]
    assert sum(row["pending_qty"] for row in body["do_by_location"]) == 25
    assert sum(row["pending_qty"] for row in body["do_by_customer"]) == 25
    assert body["do"]["do_qty"] == 25
    assert body["do"]["delivered_qty"] == 0


# --------------------------------------------------------------------- AC-1112


def test_warehouse_codes_filter_and_null_bucket(client, db):
    """`warehouse_codes` filters SO lines on `sales_order_lines.warehouse_id` and DO
    lines on `order_lines.warehouse_id`. A NULL-warehouse SO line is excluded once the
    filter is set, and buckets under `code: null` when it is not (order_lines.warehouse_id
    is NOT NULL at the DB, so only the SO side can show the null bucket)."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh_ib = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-BRW-IB")
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT WH Customer")

    _so_line(db, product_id=prod.id, ordered=10, delivered=0, warehouse_id=wh_ib.id, customer_id=cust.id)
    _so_line(db, product_id=prod.id, ordered=4, delivered=0, warehouse_id=None, customer_id=cust.id)
    _do(db, product_id=prod.id, warehouse_id=wh_ib.id, qty=7, customer_id=cust.id)
    db.commit()

    unfiltered = client.get(BASE, params={"product_code": prod.product_code, "scope": "both"})
    assert unfiltered.status_code == 200, unfiltered.text
    body = unfiltered.json()
    assert body["so"]["ordered_qty"] == 14
    codes = {row["code"] for row in body["so_by_location"]}
    assert codes == {"ZZT-BRW-IB", None}

    filtered = client.get(
        BASE,
        params={"product_code": prod.product_code, "scope": "both", "warehouse_codes": "ZZT-BRW-IB"},
    )
    assert filtered.status_code == 200, filtered.text
    fbody = filtered.json()
    assert fbody["so"]["ordered_qty"] == 10  # the NULL-warehouse line is excluded
    assert {row["code"] for row in fbody["so_by_location"]} == {"ZZT-BRW-IB"}
    assert fbody["do"]["pending_qty"] == 7  # the seeded DO is pending, so do_qty matches it
    assert fbody["do"]["do_qty"] == 7
    assert {row["code"] for row in fbody["do_by_location"]} == {"ZZT-BRW-IB"}


# --------------------------------------------------------------------- AC-1113


def test_customer_query_matches_name_not_debtor_code(client, db):
    """`customer_query=Dealer A` matches `customers.customer_name` ILIKE - a customer
    whose CODE contains the query string but whose NAME does not must be excluded."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    match = customer(db, company_id=DEFAULT_COMPANY_ID, name="Dealer A Sdn Bhd")
    match.customer_code = unique_code("CODE")
    no_match = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Totally Different Co")
    no_match.customer_code = "ZZT-DEALER-A-CODE"  # contains the query text, in the CODE only
    db.flush()

    _so_line(db, product_id=prod.id, ordered=10, delivered=0, customer_id=match.id)
    _so_line(db, product_id=prod.id, ordered=20, delivered=0, customer_id=no_match.id)
    db.commit()

    resp = client.get(
        BASE, params={"product_code": prod.product_code, "scope": "so", "customer_query": "Dealer A"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["so"]["ordered_qty"] == 10
    assert body["so"]["so_count"] == 1
    # R13: product AND customer named is the BOTH subject, whose only breakdown is by
    # location - the filter is graded on the rows, which carry the customer either way.
    assert {row["customer_name"] for row in body["so_rows"]} == {"Dealer A Sdn Bhd"}


# --------------------------------------------------------------------- AC-1114


def test_so_rows_roll_up_lines_per_so(client, db):
    """Two live lines on one SO (205 + 205) roll up to ONE `so_rows` entry with
    `ordered_qty=410` and the distinct warehouse codes joined by ', '. Rows are
    sorted by `order_date` desc then `so_number`."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh_a = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-WHA")
    wh_b = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-WHB")
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Rollup Customer")

    so_multi = SalesOrder(
        id=str(uuid.uuid4()), so_number="ZZT-SO-LATER", customer_id=cust.id,
        order_date=date(2026, 6, 2), status="open", company_id=DEFAULT_COMPANY_ID,
    )
    db.add(so_multi)
    db.flush()
    db.add_all([
        SalesOrderLine(
            id=str(uuid.uuid4()), sales_order_id=so_multi.id, product_id=prod.id,
            warehouse_id=wh_a.id, qty_ordered=205, qty_delivered=0, line_status="open",
            company_id=DEFAULT_COMPANY_ID,
        ),
        SalesOrderLine(
            id=str(uuid.uuid4()), sales_order_id=so_multi.id, product_id=prod.id,
            warehouse_id=wh_b.id, qty_ordered=205, qty_delivered=0, line_status="open",
            company_id=DEFAULT_COMPANY_ID,
        ),
    ])

    # An earlier single-line SO, to check the sort (order_date asc).
    _so_line(
        db, product_id=prod.id, ordered=50, delivered=0, customer_id=cust.id, warehouse_id=wh_a.id,
        order_date=date(2026, 6, 1), so_number="ZZT-SO-EARLIER",
    )
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "so"})
    assert resp.status_code == 200, resp.text
    rows = resp.json()["so_rows"]
    assert [r["so_number"] for r in rows] == ["ZZT-SO-LATER", "ZZT-SO-EARLIER"]
    rolled = rows[0]
    assert rolled["ordered_qty"] == 410
    assert set(rolled["location"].split(", ")) == {"ZZT-WHA", "ZZT-WHB"}


# --------------------------------------------------------------------- AC-1115


def test_do_block_is_one_population_tallying_with_the_breakdowns(client, db):
    """R11, REPLACES R6's two-population DO block (owner testing round 3, 13 Sep
    2026): "the breakdown list should tally with whatever reported at the summary at
    the top", choosing option 1 (breakdowns list only names with outstanding above
    0). ONE population now, `_outstanding_clause` - the SAME population the block,
    BOTH breakdowns, `do_count`, the dates AND `do_rows[]` all draw from. A delivered
    DO contributes to NOTHING anywhere on this route, not even `do_qty` (R6's "every
    DO in scope" is gone): `do_qty` = SUM `order_lines.quantity` over OUTSTANDING DOs
    only, so it equals `pending_qty` and `delivered_qty` is always `0` - there is no
    partial-delivery split below the DO header in this schema (`order_lines` carries
    one `quantity`, no delivered/outstanding split), so "the part-delivered portion"
    R11's wording allows for never actually occurs today; the field stays for shape
    parity with the SO block and because the schema could grow one later.

    One delivered DO (qty 5) and one outstanding DO (qty 7) for the SAME customer:
    `do_qty` 7 (the outstanding DO only, NOT 12), `delivered_qty` 0, `pending_qty` 7,
    `do_count` 1, `do_by_customer` = exactly one row for that customer (`do_qty` 7,
    `pending_qty` 7). A SECOND customer with ONLY a delivered DO (qty 9) is ABSENT
    from `do_by_customer` entirely - R6's "still printed with pending 0" is reversed:
    a name with nothing outstanding is not a name on this block's population at all."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT DO Customer")
    cust_delivered_only = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT DO Delivered Only")
    delivered_status = _delivered_status(db)

    _do(
        db, product_id=prod.id, warehouse_id=wh.id, qty=5, customer_id=cust.id,
        order_status_id=delivered_status.id, actual_delivery_date=date(2026, 3, 1),
        number="ZZT-DO-DELIVERED",
    )
    _do(
        db, product_id=prod.id, warehouse_id=wh.id, qty=7, customer_id=cust.id,
        order_status_id=None, actual_delivery_date=None, number="ZZT-DO-OUTSTANDING",
    )
    _do(
        db, product_id=prod.id, warehouse_id=wh.id, qty=9, customer_id=cust_delivered_only.id,
        order_status_id=delivered_status.id, actual_delivery_date=date(2026, 3, 2),
        number="ZZT-DO-DELIVERED-ONLY",
    )
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "do"})
    assert resp.status_code == 200, resp.text
    do = resp.json()["do"]
    assert do["do_qty"] == 7, f"do_qty must be the OUTSTANDING DO only, not 5+7+9: {do}"
    assert do["delivered_qty"] == 0, f"delivered_qty = do_qty - pending_qty = 0: {do}"
    assert do["pending_qty"] == 7
    assert do["do_count"] == 1

    rows = {r["do_number"]: r for r in resp.json()["do_rows"]}
    assert set(rows) == {"ZZT-DO-OUTSTANDING"}, rows
    outstanding_row = rows["ZZT-DO-OUTSTANDING"]
    assert outstanding_row["pending_qty"] == 7
    assert outstanding_row["do_qty"] == 7
    assert outstanding_row["delivered_qty"] == 0

    by_customer = {r["customer_name"]: r for r in resp.json()["do_by_customer"]}
    assert by_customer == {"ZZT DO Customer": {"customer_name": "ZZT DO Customer", "do_qty": 7, "pending_qty": 7}}, (
        f"a customer with only a delivered DO must be ABSENT, and the remaining "
        f"customer's do_qty must equal its pending_qty (one population): {by_customer}"
    )


def test_a_sales_order_with_no_customer_echoes_null_not_the_string_none(client, db):
    """Owner smoke test, 13 Sep 2026: the reply printed `None: 178 (O/S: 178)` under
    *_By customer_*. The rendering is the presenter's job, but the WIRE has to carry a
    real null for it to render anything sensible - a serialized "None" string would be
    indistinguishable from a customer actually called None."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    _so_line(db, product_id=prod.id, ordered=178, delivered=0, customer_id=None)
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "so"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [row["customer_name"] for row in body["so_by_customer"]] == [None], (
        f"a NULL customer must stay null on the wire: {body['so_by_customer']}"
    )
    assert body["so_rows"][0]["customer_name"] is None, body["so_rows"]
    assert "None" not in resp.text, (
        f"nothing in the body may carry the literal string 'None': {resp.text}"
    )


# --------------------------------------------------------------------- AC-1116


def test_breakdowns_sum_to_totals(client, db):
    """so_by_location/so_by_customer and do_by_location/do_by_customer each sum to
    their block's totals - R11's own tally identity ("the breakdown list should
    tally with whatever reported at the summary at the top"): a delivered DO (qty 4)
    is seeded alongside the outstanding ones at the SAME location/customer and must
    contribute NOTHING to either sum - `do_qty` sums to the block's `do_qty` exactly
    like `pending_qty` sums to `pending_qty` (one population, so the two identities
    coincide numerically, which is itself the point)."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh1 = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH1"))
    wh2 = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH2"))
    cust1 = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Cust One")
    cust2 = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Cust Two")
    delivered_status = _delivered_status(db)

    _so_line(db, product_id=prod.id, ordered=10, delivered=2, customer_id=cust1.id, warehouse_id=wh1.id)
    _so_line(db, product_id=prod.id, ordered=6, delivered=1, customer_id=cust2.id, warehouse_id=wh2.id)
    _do(db, product_id=prod.id, warehouse_id=wh1.id, qty=15, customer_id=cust1.id)
    _do(db, product_id=prod.id, warehouse_id=wh2.id, qty=9, customer_id=cust2.id)
    _do(
        db, product_id=prod.id, warehouse_id=wh1.id, qty=4, customer_id=cust1.id,
        order_status_id=delivered_status.id, actual_delivery_date=date(2026, 3, 1),
        number="ZZT-DO-BREAKDOWN-DELIVERED",
    )
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "both"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert sum(r["ordered_qty"] for r in body["so_by_location"]) == body["so"]["ordered_qty"]
    assert sum(r["outstanding_qty"] for r in body["so_by_location"]) == body["so"]["outstanding_qty"]
    assert sum(r["ordered_qty"] for r in body["so_by_customer"]) == body["so"]["ordered_qty"]
    assert sum(r["outstanding_qty"] for r in body["so_by_customer"]) == body["so"]["outstanding_qty"]

    assert body["do"]["do_qty"] == 24, body["do"]  # 15 + 9, the delivered 4 excluded entirely
    assert body["do"]["pending_qty"] == 24, body["do"]
    assert body["do"]["delivered_qty"] == 0, body["do"]
    assert sum(r["do_qty"] for r in body["do_by_location"]) == body["do"]["do_qty"], body["do_by_location"]
    assert sum(r["pending_qty"] for r in body["do_by_location"]) == body["do"]["pending_qty"], body["do_by_location"]
    assert sum(r["do_qty"] for r in body["do_by_customer"]) == body["do"]["do_qty"], body["do_by_customer"]
    assert sum(r["pending_qty"] for r in body["do_by_customer"]) == body["do"]["pending_qty"], body["do_by_customer"]


# --------------------------------------------------------------------- R10 (owner
# testing round 3, 13 Sep 2026): "be it DO outstanding or SO outstanding, we need to
# rank by highest quantity at the top." Every `*_By location_*` / `*_By customer_*`
# group, in both blocks, sorts by the bracketed outstanding quantity DESCENDING; ties
# by the total (the first number) DESCENDING; further ties by name ASCENDING.
# `Unassigned` (a NULL warehouse/customer) takes its place by its own numbers, never
# pinned first or last. The route returns the arrays already sorted - the presenter
# is a dumb pass-through (`sorento_crm_mcp/tests/test_presenters_outstanding.py`
# pins that separately).


def test_so_breakdowns_ranked_by_outstanding_desc_ties_by_total_then_name(client, db):
    """Five warehouses and five customers, paired one-per-SO-line so the SAME rank
    order is expected in BOTH breakdowns: one row clearly highest, two rows tied on
    `outstanding_qty` at 10 but differing `ordered_qty` (15 vs 10 - the higher total
    wins the tie), two more rows fully tied at (10, 10) broken only by name."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    rows = [
        # (warehouse code, customer name, ordered, delivered) -> outstanding = ordered - delivered
        ("ZZT-WH-HIGH", "ZZT Cust High", 20, 0),      # outstanding 20, total 20
        ("ZZT-WH-TIEHI", "ZZT Cust TieHi", 15, 5),    # outstanding 10, total 15
        ("ZZT-AAA-TIE", "ZZT AAA Tie", 10, 0),        # outstanding 10, total 10
        ("ZZT-ZZZ-TIE", "ZZT ZZZ Tie", 10, 0),        # outstanding 10, total 10
        ("ZZT-WH-LOW", "ZZT Cust Low", 5, 0),         # outstanding 5, total 5
    ]
    for wh_code, cust_name, ordered, delivered in rows:
        wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=wh_code)
        cust = customer(db, company_id=DEFAULT_COMPANY_ID, name=cust_name)
        _so_line(db, product_id=prod.id, ordered=ordered, delivered=delivered, customer_id=cust.id, warehouse_id=wh.id)
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "so"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    expected_locations = ["ZZT-WH-HIGH", "ZZT-WH-TIEHI", "ZZT-AAA-TIE", "ZZT-ZZZ-TIE", "ZZT-WH-LOW"]
    assert [r["code"] for r in body["so_by_location"]] == expected_locations, (
        f"so_by_location must rank by outstanding_qty desc, tie by ordered_qty desc, "
        f"then code asc: {body['so_by_location']}"
    )
    expected_customers = ["ZZT Cust High", "ZZT Cust TieHi", "ZZT AAA Tie", "ZZT ZZZ Tie", "ZZT Cust Low"]
    assert [r["customer_name"] for r in body["so_by_customer"]] == expected_customers, (
        f"so_by_customer must rank the same way: {body['so_by_customer']}"
    )


def test_do_breakdowns_ranked_by_outstanding_desc_then_name(client, db):
    """The DO mirror, seeded with OUTSTANDING DOs only (no delivered ones) so the
    ranking assertion holds regardless of how the DO block's population is defined
    elsewhere (R11 makes `do_qty == pending_qty` for every outstanding-only row,
    which this test deliberately does not disturb - the `do_qty` desc tie-break is
    exercised by the SO test above instead, over the identical sort function)."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    rows = [
        ("ZZT-DO-WH-HIGH", "ZZT DO Cust High", 20),
        ("ZZT-DO-AAA-TIE", "ZZT DO AAA Tie", 10),
        ("ZZT-DO-ZZZ-TIE", "ZZT DO ZZZ Tie", 10),
        ("ZZT-DO-WH-LOW", "ZZT DO Cust Low", 5),
    ]
    for wh_code, cust_name, qty in rows:
        wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=wh_code)
        cust = customer(db, company_id=DEFAULT_COMPANY_ID, name=cust_name)
        _do(db, product_id=prod.id, warehouse_id=wh.id, qty=qty, customer_id=cust.id)
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "do"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    expected_locations = ["ZZT-DO-WH-HIGH", "ZZT-DO-AAA-TIE", "ZZT-DO-ZZZ-TIE", "ZZT-DO-WH-LOW"]
    assert [r["code"] for r in body["do_by_location"]] == expected_locations, (
        f"do_by_location must rank by pending_qty desc, tie by name asc: {body['do_by_location']}"
    )
    expected_customers = ["ZZT DO Cust High", "ZZT DO AAA Tie", "ZZT DO ZZZ Tie", "ZZT DO Cust Low"]
    assert [r["customer_name"] for r in body["do_by_customer"]] == expected_customers, (
        f"do_by_customer must rank the same way: {body['do_by_customer']}"
    )


def test_unassigned_ranks_by_its_own_outstanding_qty_not_pinned(client, db):
    """R10: "Unassigned takes its place by its numbers like any other name" - a NULL
    warehouse and a NULL customer must sort into the MIDDLE of the ranking when
    their own quantity says so, never forced to the top or the bottom."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh_a = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-WH-A")
    wh_c = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-WH-C")
    cust_a = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Cust A")
    cust_b = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Cust B")

    # Row A: named warehouse + named customer, outstanding 30 (highest).
    _so_line(db, product_id=prod.id, ordered=30, delivered=0, customer_id=cust_a.id, warehouse_id=wh_a.id)
    # Row B: NULL warehouse, named customer, outstanding 20 (middle) - tests Unassigned
    # landing in the MIDDLE of so_by_location.
    _so_line(db, product_id=prod.id, ordered=20, delivered=0, customer_id=cust_b.id, warehouse_id=None)
    # Row C: named warehouse, NULL customer, outstanding 10 (lowest) - tests Unassigned
    # landing at the BOTTOM of so_by_customer.
    _so_line(db, product_id=prod.id, ordered=10, delivered=0, customer_id=None, warehouse_id=wh_c.id)
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "so"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert [r["code"] for r in body["so_by_location"]] == ["ZZT-WH-A", None, "ZZT-WH-C"], (
        f"the NULL warehouse (outstanding 20) must rank BETWEEN the 30 and the 10, "
        f"by its own number: {body['so_by_location']}"
    )
    assert [r["customer_name"] for r in body["so_by_customer"]] == ["ZZT Cust A", "ZZT Cust B", None], (
        f"the NULL customer (outstanding 10) must rank LAST here, by its own number, "
        f"not forced there structurally: {body['so_by_customer']}"
    )


# --------------------------------------------------------------------- AC-1117


def test_scope_omits_block_and_response_model_keeps_every_field(client, db):
    """A scope not asked for is ABSENT from the body (not an empty dict); `scope=both`
    (default) keeps every field the plan's contract declares - a `response_model`
    silently drops any undeclared one. R6, REWRITTEN AGAIN (owner testing round 3, 13
    Sep 2026): `do.do_qty` / `do.delivered_qty` are BACK on the BLOCK's contract, and
    BOTH breakdowns (`do_by_location[]` / `do_by_customer[]`) carry `do_qty` alongside
    `pending_qty` too - R1's "gone from the block and both breakdowns" is reversed for
    these two fields. `do_rows[]` already carried both since R3 and is unaffected."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Field Customer")
    _so_line(db, product_id=prod.id, ordered=10, delivered=2, customer_id=cust.id, warehouse_id=wh.id)
    _do(db, product_id=prod.id, warehouse_id=wh.id, qty=5, customer_id=cust.id)
    db.commit()

    so_only = client.get(BASE, params={"product_code": prod.product_code, "scope": "so"})
    assert so_only.status_code == 200, so_only.text
    assert "do" not in so_only.json()
    assert "so" in so_only.json()

    do_only = client.get(BASE, params={"product_code": prod.product_code, "scope": "do"})
    assert do_only.status_code == 200, do_only.text
    assert "so" not in do_only.json()
    assert "do" in do_only.json()

    both = client.get(BASE, params={"product_code": prod.product_code})  # default scope=both
    assert both.status_code == 200, both.text
    body = both.json()
    for key in (
        "product_code", "customer_name", "warehouse_codes", "order_date_from", "order_date_to",
        "so", "do", "so_by_location", "so_by_customer", "do_by_location", "do_by_customer",
        "so_rows", "do_rows",
    ):
        assert key in body, f"missing field: {key}"
    for key in ("ordered_qty", "transferred_qty", "outstanding_qty", "so_count", "order_date_min", "order_date_max"):
        assert key in body["so"], f"missing so.{key}"
    for key in ("do_qty", "delivered_qty", "pending_qty", "do_count", "do_date_min", "do_date_max"):
        assert key in body["do"], f"R6: do.{key} must be on the wire again"
    for row in body["do_by_location"] + body["do_by_customer"]:
        assert "do_qty" in row and "pending_qty" in row, (
            f"R6 brought do_qty BACK onto both breakdowns, alongside pending_qty: {row}"
        )
    for row in body["do_rows"]:
        assert "do_qty" in row and "delivered_qty" in row, (
            f"R3 brought do_qty/delivered_qty onto do_rows[] specifically: {row}"
        )
        assert row["delivered_qty"] == 0, (
            f"R3: delivered_qty must be present as 0, never omitted, for a pending DO: {row}"
        )


# --------------------------------------------------------------------- AC-1118


def test_route_permission_and_api_key_act_as(db):
    """403 without `order_management.orders.view`; an X-API-Key principal with an
    act-as user who HOLDS the grant reaches the route."""
    from app.config import settings

    def _override_db():
        yield db

    async def _override_scope():
        scope = frozenset({DEFAULT_COMPANY_ID})
        set_company_scope(db, scope)
        return scope

    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    _so_line(db, product_id=prod.id, ordered=1, delivered=0)

    no_grant_user = User(id=str(uuid.uuid4()), email=f"{unique_code('NG')}@test.com", name="ZZT No Grant", status="ACTIVE")
    db.add(no_grant_user)
    db.flush()

    permission = UserPermission(id=str(uuid.uuid4()), slug=PERMISSION_SLUG, name=PERMISSION_SLUG)
    role = UserRole(id=str(uuid.uuid4()), slug="zzt_orders_view_role", name="ZZT Orders View")
    granted_user = User(id=str(uuid.uuid4()), email=f"{unique_code('OK')}@test.com", name="ZZT Granted", status="ACTIVE")
    db.add_all([permission, role, granted_user])
    db.flush()
    db.add(UserRoleAssignment(user_id=granted_user.id, role_id=role.id))
    db.add(UserRolePermission(role_id=role.id, permission_id=permission.id))
    db.flush()

    integration = Integration(
        id=str(uuid.uuid4()), name="zzt-outstanding-report-key", type="automation",
        act_as_user_id=granted_user.id, is_active=True,
    )
    db.add(integration)
    db.flush()
    plaintext_key = IntegrationKeyService(db).issue_key(integration)
    db.commit()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[apply_company_scope] = _override_scope
    try:
        # No grant: JWT-style principal without the permission -> 403.
        app.dependency_overrides[get_current_user] = lambda: {"id": no_grant_user.id, "email": no_grant_user.email}
        app.dependency_overrides[get_current_user_or_api_key] = lambda: {"id": no_grant_user.id, "email": no_grant_user.email}
        client = TestClient(app)
        denied = client.get(BASE, params={"product_code": prod.product_code})
        assert denied.status_code == 403, denied.text

        # X-API-Key + act-as: remove the override so the real key resolves.
        del app.dependency_overrides[get_current_user_or_api_key]
        del app.dependency_overrides[get_current_user]
        client = TestClient(app)
        allowed = client.get(
            BASE, params={"product_code": prod.product_code}, headers={"X-API-Key": plaintext_key}
        )
        assert allowed.status_code == 200, allowed.text
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- AC-1113b


def test_customer_ids_filters_report(client, db):
    """`customer_ids` (csv of `customers.id`) filters the same way `customer_query` does -
    the chatbot lane sends the resolved customer entity's id, never the debtor code.
    `customer_query` and `customer_ids` together intersect (S4 point 9 / AC-1113b)."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    match = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Customer Ids Match")
    other = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Customer Ids Other")

    _so_line(db, product_id=prod.id, ordered=10, delivered=0, customer_id=match.id)
    _so_line(db, product_id=prod.id, ordered=25, delivered=0, customer_id=other.id)
    db.commit()

    resp = client.get(
        BASE, params={"product_code": prod.product_code, "scope": "so", "customer_ids": match.id}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["so"]["ordered_qty"] == 10, (
        f"customer_ids must filter the report the same way customer_query does: {body}"
    )
    # R13: the BOTH subject carries by_location only, so the rows are where the filtered
    # customer is read back (they always carry `customer_name`).
    assert {row["customer_name"] for row in body["so_rows"]} == {"ZZT Customer Ids Match"}


def test_customer_ids_echo_the_resolved_names_in_the_header(client, db):
    """AC-1136 (review round, 13 Sep 2026): the header prints the response's
    `customer_name`, and the chatbot sends `customer_ids` (a resolved UUID), never
    `customer_query`. The echo was set from `customer_query` alone, so a customer ask
    from WhatsApp printed `Customer: all` over figures that WERE filtered to one
    customer - the reply contradicted the numbers under it. Several ids join by ", "."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    first = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Echo Customer A")
    second = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Echo Customer B")
    _so_line(db, product_id=prod.id, ordered=10, delivered=0, customer_id=first.id)
    _so_line(db, product_id=prod.id, ordered=4, delivered=0, customer_id=second.id)
    db.commit()

    one = client.get(
        BASE, params={"product_code": prod.product_code, "scope": "so", "customer_ids": first.id}
    )
    assert one.status_code == 200, one.text
    assert one.json()["customer_name"] == "ZZT Echo Customer A", one.json()

    both = client.get(
        BASE,
        params={
            "product_code": prod.product_code,
            "scope": "so",
            "customer_ids": f"{first.id},{second.id}",
        },
    )
    assert both.status_code == 200, both.text
    assert both.json()["customer_name"] == "ZZT Echo Customer A, ZZT Echo Customer B", both.json()


def test_customer_header_dedupes_ledger_names(client, db):
    """AC-1163 (R19, 13 Sep 2026): DISTINCT ledger names, first-seen order. Briefly reversed
    on 2 Oct 2026 for CUSTOMER-GROUP, then restored by the owner's ruling (b) the same day:
    "we shouldn't do automated process like this, very dangerous". Only an explicit customer
    group joins ledgers; an ungrouped ledger prints its own name, identical names once."""
    plain_1 = customer(db, company_id=DEFAULT_COMPANY_ID, name="FULLSHUN SANITARYWARE SDN BHD")
    plain_2 = customer(db, company_id=DEFAULT_COMPANY_ID, name="FULLSHUN SANITARYWARE SDN BHD")
    plain_3 = customer(db, company_id=DEFAULT_COMPANY_ID, name="FULLSHUN SANITARYWARE SDN BHD")
    showcase = customer(
        db, company_id=DEFAULT_COMPANY_ID, name="*FULLSHUN SANITARYWARE SDN BHD (SHOWCASE)"
    )
    ac3_1 = customer(
        db, company_id=DEFAULT_COMPANY_ID, name="FULLSHUN SANITARYWARE SDN BHD [A/C III]"
    )
    ac3_2 = customer(
        db, company_id=DEFAULT_COMPANY_ID, name="FULLSHUN SANITARYWARE SDN BHD [A/C III]"
    )
    db.commit()

    ids = ",".join([plain_1.id, plain_2.id, plain_3.id, showcase.id, ac3_1.id, ac3_2.id])
    resp = client.get(BASE, params={"customer_ids": ids, "scope": "so"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["customer_name"] == (
        "FULLSHUN SANITARYWARE SDN BHD, "
        "*FULLSHUN SANITARYWARE SDN BHD (SHOWCASE), "
        "FULLSHUN SANITARYWARE SDN BHD [A/C III]"
    ), resp.json()


def test_customer_header_names_the_customer_group_on_the_route(client, db):
    """CUSTOMER-GROUP: six HANLIM ledgers in one group -> the group name, once, no count."""
    from sqlalchemy import text

    gid = str(uuid.uuid4())
    db.execute(
        text(
            "INSERT INTO customer_groups (id, company_id, name, created_at, updated_at) "
            "VALUES (:i, :c, 'HANLIM TRADING SDN BHD', now(), now())"
        ),
        {"i": gid, "c": DEFAULT_COMPANY_ID},
    )
    ids = []
    for name in (
        "HANLIM TRADING SDN BHD [A/C I]",
        "HANLIM TRADING SDN BHD [A/C II]",
        "HANLIM TRADING SDN BHD [A/C III]",
        "HANLIM TRADING SDN BHD [A/C IV]",
        "HANLIM TRADING SDN BHD",
        "HANLIM TRADING SDN BHD (CERAMIC & ELLECI)",
    ):
        row = customer(db, company_id=DEFAULT_COMPANY_ID, name=name)
        db.execute(text("UPDATE customers SET customer_group_id = :g WHERE id = :c"), {"g": gid, "c": row.id})
        ids.append(row.id)
    db.commit()

    resp = client.get(BASE, params={"customer_ids": ",".join(ids), "scope": "so"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["customer_name"] == "HANLIM TRADING SDN BHD", resp.json()


def test_fractional_quantities_round_to_whole_units(client, db):
    """`sales_order_lines.qty_ordered` is `Numeric(15,4)`, so a fraction is storable;
    every quantity on this report is declared `int` (the reply prints whole units with
    a thousands separator). 2.5 rounds HALF UP to 3 - never Python's bankers' rounding,
    which would print 2 for 2.5 and 4 for 3.5 in the same reply."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    _so_line(db, product_id=prod.id, ordered=Decimal("2.5000"), delivered=Decimal("0"))
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "so"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["so"]["ordered_qty"] == 3, body["so"]
    assert body["so"]["outstanding_qty"] == 3, body["so"]
    assert body["so_rows"][0]["ordered_qty"] == 3, body["so_rows"]


# --------------------------------------------------------------------------- AC-1119


def test_product_code_exact_no_siblings(client, db):
    """`product_code` resolves exactly (case-insensitive); a sibling code
    (`SRTWT7445-A`) never contributes to `SRTWT7445`'s figures."""
    target = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SRTWT7445")
    sibling = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-SRTWT7445-A")
    _so_line(db, product_id=target.id, ordered=10, delivered=0)
    _so_line(db, product_id=sibling.id, ordered=99, delivered=0)
    db.commit()

    resp = client.get(BASE, params={"product_code": "zzt-srtwt7445", "scope": "so"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["so"]["ordered_qty"] == 10
    assert body["product_code"] == "ZZT-SRTWT7445"


# --------------------------------------------------------------------------- B1 (security review, 13 Sep 2026)


def test_report_is_company_scoped_for_api_key_contact(db, monkeypatch):
    """B1 BLOCKER: the `client` fixture above overrides `apply_company_scope` with a
    single hard-coded company, so no other test in this file exercises the REAL
    resolver. Without `contact_id`/`space_id` reaching `crm_outstanding_report`'s
    query_params (the catalog fix this test pins), an X-API-Key call scopes to
    `None` (every company) and a Sorento contact's report would sum in Mocha's SO
    lines too - this test seeds the SAME product code live in both companies and
    asserts only the contact's own company's figures come back.

    `apply_company_scope` is NOT overridden here (that is the whole point): the
    route's own dependency runs for real, off the request's own `X-API-Key` +
    `contact_id` + `space_id`. Two different checks read the same key for two
    different reasons - `_api_key_valid` (company-scope resolution) compares it
    literally against `settings.external_api_key`, while `resolve_integration_
    principal` (route permission) looks it up by hash in `integration_api_keys` -
    so the ONE issued key is used for both, and `settings.external_api_key` is
    pinned to it, mirroring `tests/test_mcp_scope_resolver.py`'s own note on why
    that pin is necessary in a test process that never sets the env var.
    """
    from app.config import settings

    mocha = seed_mocha(db)
    code = unique_code("SKU")

    prod_a = product(db, company_id=DEFAULT_COMPANY_ID, code=code)
    prod_b = product(db, company_id=mocha.id, code=code)

    _so_line(db, product_id=prod_a.id, ordered=10, delivered=3)  # Sorento: outstanding 7
    so_b = SalesOrder(
        id=str(uuid.uuid4()), so_number=unique_code("SO"), status="open", company_id=mocha.id,
    )
    db.add(so_b)
    db.flush()
    db.add(
        SalesOrderLine(
            id=str(uuid.uuid4()), sales_order_id=so_b.id, product_id=prod_b.id,
            qty_ordered=500, qty_delivered=0, line_status="open", company_id=mocha.id,
        )
    )

    contact = RespondContact(id=str(uuid.uuid4()), phone_number=f"+6{unique_code('PH')[:10]}")
    db.add(contact)
    db.flush()
    db.add(
        RespondContactCompany(
            id=str(uuid.uuid4()), respond_contact_id=contact.id, company_id=DEFAULT_COMPANY_ID,
        )
    )

    superadmin = _seed_superadmin(db)
    integration = Integration(
        id=str(uuid.uuid4()), name="zzt-scope-key", type="automation",
        act_as_user_id=superadmin["id"], is_active=True,
    )
    db.add(integration)
    db.flush()
    plaintext_key = IntegrationKeyService(db).issue_key(integration)
    db.commit()
    monkeypatch.setattr(settings, "external_api_key", plaintext_key)

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    try:
        client = TestClient(app)
        resp = client.get(
            BASE,
            params={"product_code": code, "scope": "so", "contact_id": contact.id, "space_id": "zzt-space"},
            headers={"X-API-Key": plaintext_key},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["so"]["ordered_qty"] == 10, (
            f"a Sorento-only contact must never see Mocha's SO lines: {body}"
        )
        assert body["so"]["outstanding_qty"] == 7
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- S3 (security review, 13 Sep 2026)


def test_customer_ids_rejects_a_non_uuid_value(client, db):
    """S3: `customer_ids` is a UUID param like every other `<entity>_ids` filter on
    this route file - a non-UUID value is a 400, the same `parse_uuid_list` gives
    `order_ids`/`transporter_ids` elsewhere, never a silent no-match."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "customer_ids": "not-a-uuid"})
    assert resp.status_code == 400, resp.text


def test_customer_ids_over_fifty_is_422(client, db):
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    db.commit()

    too_many = ",".join(str(uuid.uuid4()) for _ in range(51))
    resp = client.get(BASE, params={"product_code": prod.product_code, "customer_ids": too_many})
    assert resp.status_code == 422, resp.text


def test_warehouse_codes_over_fifty_is_422(client, db):
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    db.commit()

    too_many = ",".join(f"ZZT-WH-{i}" for i in range(51))
    resp = client.get(BASE, params={"product_code": prod.product_code, "warehouse_codes": too_many})
    assert resp.status_code == 422, resp.text


# --------------------------------------------------------------------------- R13
# (owner ruling, 13 Sep 2026): "when we generate the outstanding summary for
# customer and for product it is different, they should be the same ... when we
# ask for customer, the by customer section becomes by product section." The
# report's subject is a product, a customer, or both. `product_code` becomes
# optional; at least one of `product_code` / `customer_ids` / `customer_query`
# is required. Which breakdown groups the response carries depends on the
# subject: product subject -> by_location + by_customer (today's shape);
# customer subject -> by_location + by_product (NEW); both -> by_location ONLY
# (by_customer AND by_product both absent, not empty). so_rows[]/do_rows[] now
# ALWAYS carry both customer_name and product_code, whatever the subject.


def test_product_code_is_optional_when_a_customer_is_given(client, db):
    """A customer-only ask (no product_code at all) must reach the route and
    succeed - today `product_code` is still a required query param, so this
    request 422s before the service ever runs."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT R13 Customer")
    _so_line(db, product_id=prod.id, ordered=10, delivered=2, customer_id=cust.id, warehouse_id=wh.id)
    db.commit()

    resp = client.get(BASE, params={"customer_ids": cust.id, "scope": "so"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("product_code") is None, (
        f"no product was given, so product_code must echo null, not a required string: {body}"
    )


def test_422_when_neither_product_nor_customer_is_given(client, db):
    """R13: at least one of product_code/customer_ids/customer_query is required.
    Today this already 422s, but for the WRONG reason (product_code is still a
    required field at the FastAPI layer) - the assertion on the message content
    is what actually pins the NEW business rule, not the coincidental status code."""
    resp = client.get(BASE, params={"scope": "so"})
    assert resp.status_code == 422, resp.text
    assert "customer_query" in resp.text and "product_code" in resp.text, (
        f"the 422 must name the business rule (at least one of product_code / "
        f"customer_ids / customer_query), not FastAPI's generic 'field required': {resp.text}"
    )


def test_customer_subject_report_has_by_product_not_by_customer(client, db):
    """R13's own words: "when we ask for customer, the by customer section
    becomes by product section." A customer-only ask (no product_code) gets
    `so_by_product[]` / `do_by_product[]` instead of `so_by_customer[]` /
    `do_by_customer[]`, which must be ABSENT from the body entirely."""
    prod1 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU1"))
    prod2 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU2"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT R13 Multi Product")
    _so_line(db, product_id=prod1.id, ordered=10, delivered=0, customer_id=cust.id, warehouse_id=wh.id)
    _so_line(db, product_id=prod2.id, ordered=6, delivered=0, customer_id=cust.id, warehouse_id=wh.id)
    _do(db, product_id=prod1.id, warehouse_id=wh.id, qty=7, customer_id=cust.id)
    _do(db, product_id=prod2.id, warehouse_id=wh.id, qty=3, customer_id=cust.id)
    db.commit()

    resp = client.get(BASE, params={"customer_ids": cust.id, "scope": "both"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "so_by_product" in body, f"a customer subject must carry so_by_product: {body}"
    assert "do_by_product" in body, f"a customer subject must carry do_by_product: {body}"
    assert "so_by_customer" not in body, (
        f"so_by_customer must be ABSENT for a customer subject, not just empty: {body}"
    )
    assert "do_by_customer" not in body, (
        f"do_by_customer must be ABSENT for a customer subject, not just empty: {body}"
    )
    assert "so_by_location" in body and "do_by_location" in body, (
        "by_location stays present for every subject"
    )
    so_products = {r["product_code"] for r in body["so_by_product"]}
    assert so_products == {prod1.product_code, prod2.product_code}, body["so_by_product"]
    do_products = {r["product_code"] for r in body["do_by_product"]}
    assert do_products == {prod1.product_code, prod2.product_code}, body["do_by_product"]


def test_product_subject_report_still_has_by_customer_not_by_product(client, db):
    """The mirror: a product-only ask (today's existing shape) must NOT carry
    so_by_product/do_by_product at all - only a customer subject introduces them."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT R13 Product Subject")
    _so_line(db, product_id=prod.id, ordered=10, delivered=0, customer_id=cust.id, warehouse_id=wh.id)
    _do(db, product_id=prod.id, warehouse_id=wh.id, qty=7, customer_id=cust.id)
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "both"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "so_by_customer" in body and "do_by_customer" in body
    assert "so_by_product" not in body, (
        f"so_by_product must be ABSENT for a product subject: {body}"
    )
    assert "do_by_product" not in body, (
        f"do_by_product must be ABSENT for a product subject: {body}"
    )


def test_both_subject_report_has_only_by_location(client, db):
    """R13: product AND customer given together -> by_location ONLY. Neither
    by_customer nor by_product may appear, on either side."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT R13 Both Subject")
    _so_line(db, product_id=prod.id, ordered=10, delivered=0, customer_id=cust.id, warehouse_id=wh.id)
    _do(db, product_id=prod.id, warehouse_id=wh.id, qty=7, customer_id=cust.id)
    db.commit()

    resp = client.get(
        BASE, params={"product_code": prod.product_code, "customer_ids": cust.id, "scope": "both"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "so_by_location" in body and "do_by_location" in body
    for absent in ("so_by_customer", "do_by_customer", "so_by_product", "do_by_product"):
        assert absent not in body, f"{absent} must be ABSENT when both a product and a customer are given: {body}"


def test_rows_carry_both_customer_name_and_product_code(client, db):
    """R13: so_rows[]/do_rows[] always carry BOTH customer_name and product_code,
    whatever the subject - the row fields are SO Number/Customer/Product/Location/
    Ordered/Transferred to DO/Outstanding/Order Date (SO) and DO Number/Customer/
    Product/Location/DO Qty/Delivered/Outstanding/DO Date (DO)."""
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT R13 Row Customer")
    _so_line(db, product_id=prod.id, ordered=10, delivered=0, customer_id=cust.id, warehouse_id=wh.id)
    _do(db, product_id=prod.id, warehouse_id=wh.id, qty=7, customer_id=cust.id)
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "both"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["so_rows"][0]["product_code"] == prod.product_code, body["so_rows"]
    assert body["so_rows"][0]["customer_name"] == "ZZT R13 Row Customer", body["so_rows"]
    assert body["do_rows"][0]["product_code"] == prod.product_code, body["do_rows"]
    assert body["do_rows"][0]["customer_name"] == "ZZT R13 Row Customer", body["do_rows"]


def test_by_product_ranked_and_tallies_with_the_block(client, db):
    """R10 (ranking) and R11 (tally) both apply to the NEW by_product group exactly
    as they do to by_location/by_customer: ranked by outstanding desc, and the
    breakdown sums equal the block's totals."""
    prod_high = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKUHIGH"))
    prod_low = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKULOW"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT R13 Rank Customer")
    _so_line(db, product_id=prod_low.id, ordered=5, delivered=0, customer_id=cust.id, warehouse_id=wh.id)
    _so_line(db, product_id=prod_high.id, ordered=20, delivered=0, customer_id=cust.id, warehouse_id=wh.id)
    db.commit()

    resp = client.get(BASE, params={"customer_ids": cust.id, "scope": "so"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [r["product_code"] for r in body["so_by_product"]] == [
        prod_high.product_code, prod_low.product_code,
    ], f"so_by_product must rank by outstanding_qty desc: {body['so_by_product']}"
    assert sum(r["ordered_qty"] for r in body["so_by_product"]) == body["so"]["ordered_qty"]
    assert sum(r["outstanding_qty"] for r in body["so_by_product"]) == body["so"]["outstanding_qty"]


# --------------------------------------------------------------------------- #
# AC-1169 (R23, owner ruling, 13 Sep 2026, live): the owner's own SO detail list
# came out 14/12/2022, 01/08/2024, 09/08/2024, 20/08/2024 - ASCENDING. "it need
# to be the recent [order] not the old one, since if it is a long list, I want
# to see the recent one." R10 already assigns sorting to the ROUTE (the
# presenter prints in the order given, never re-sorting) - so the fix is here,
# not in `sorento_crm_mcp`. `so_rows[]` / `do_rows[]` sort by their own date
# DESCENDING, ties by the document number DESCENDING (a later/higher number is
# the later document).
# --------------------------------------------------------------------------- #


def test_so_rows_print_latest_order_date_first(client, db):
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    _so_line(
        db, product_id=prod.id, ordered=1, delivered=0,
        order_date=date(2022, 12, 14), so_number="ZZT-R23-SO-2022",
    )
    _so_line(
        db, product_id=prod.id, ordered=1, delivered=0,
        order_date=date(2024, 8, 1), so_number="ZZT-R23-SO-2024A",
    )
    # A tie pair on the SAME (latest) date, different numbers - the higher number
    # (the later document) must print first.
    _so_line(
        db, product_id=prod.id, ordered=1, delivered=0,
        order_date=date(2024, 8, 9), so_number="ZZT-R23-SO-2024B-1",
    )
    _so_line(
        db, product_id=prod.id, ordered=1, delivered=0,
        order_date=date(2024, 8, 9), so_number="ZZT-R23-SO-2024B-2",
    )
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "so"})
    assert resp.status_code == 200, resp.text
    numbers = [row["so_number"] for row in resp.json()["so_rows"]]
    assert numbers == [
        "ZZT-R23-SO-2024B-2",
        "ZZT-R23-SO-2024B-1",
        "ZZT-R23-SO-2024A",
        "ZZT-R23-SO-2022",
    ], f"so_rows must print latest order_date first, ties by so_number descending: {numbers}"


def test_do_rows_print_latest_do_date_first(client, db):
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    _do(
        db, product_id=prod.id, warehouse_id=wh.id, qty=1,
        order_date=date(2022, 12, 14), number="ZZT-R23-DO-2022",
    )
    _do(
        db, product_id=prod.id, warehouse_id=wh.id, qty=1,
        order_date=date(2024, 8, 1), number="ZZT-R23-DO-2024A",
    )
    _do(
        db, product_id=prod.id, warehouse_id=wh.id, qty=1,
        order_date=date(2024, 8, 9), number="ZZT-R23-DO-2024B-1",
    )
    _do(
        db, product_id=prod.id, warehouse_id=wh.id, qty=1,
        order_date=date(2024, 8, 9), number="ZZT-R23-DO-2024B-2",
    )
    db.commit()

    resp = client.get(BASE, params={"product_code": prod.product_code, "scope": "do"})
    assert resp.status_code == 200, resp.text
    numbers = [row["do_number"] for row in resp.json()["do_rows"]]
    assert numbers == [
        "ZZT-R23-DO-2024B-2",
        "ZZT-R23-DO-2024B-1",
        "ZZT-R23-DO-2024A",
        "ZZT-R23-DO-2022",
    ], f"do_rows must print latest do_date first, ties by do_number descending: {numbers}"
