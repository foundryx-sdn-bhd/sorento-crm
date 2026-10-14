"""S1: flexible sales targets with live achievement (UAC S1-1 to S1-29 except S1-11; S6-4 to
S6-7, S6-10, S6-14).

Plan: documentation/plans/sales/PLAN-sales-targets-opportunities-26sep.md, sections 3.1, 3.2,
3.8, 3.9, 16.1 to 16.3. UAC: sales-targets-opportunities-26sep-acceptance-criteria.md.

No implementation exists yet: `app/api/v1/sales/targets.py` is not mounted, so every route
call below 404s until the coder wires it, and every direct import of `app.models.sales.SalesTarget`
/ `app.services.sales.period_service` / etc. is done INSIDE a test (never at module import) so
one missing name fails only that test.

"Today" is pinned to 20 Oct 2026 through `team_service._today` (16.2: "Business day =
team_service._today()"), the same seam and the same date `test_sales_teams_s6.py` pins. The
target/achievement services are documented to reuse it; the `world` fixture ALSO best-effort
patches a `target_service`/`achievement_service` module-level `_today` if either exists, so this
file's golden dates hold even if the coder gives them their own seam instead of importing
`team_service`'s.

Golden numbers are hand-computed in a comment beside each assertion. `sales_order_lines`
never needs a warehouse (`SalesOrderLine.warehouse_id` is nullable); the DO-seam tests
(S1-26) do, because `order_lines.warehouse_id` (core, public) is NOT NULL.
"""
from __future__ import annotations

import re
import itertools
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.models.base import company_scope

from ._pg_fixture import blank_session

BASE = "/api/v1/sales/targets"
TEAMS_BASE = "/api/v1/sales/teams"
TODAY = date(2026, 10, 20)

ALL_TARGET = ["sales.targets.view", "sales.targets.add", "sales.targets.edit", "sales.targets.delete"]
ALL_TEAM = ["sales.teams.view", "sales.teams.add", "sales.teams.edit", "sales.teams.delete"]
ALL = ALL_TARGET + ALL_TEAM


def _uid() -> str:
    return str(uuid.uuid4())


_RUN = uuid.uuid4().hex[:2].upper()
_SEQ = itertools.count(1)


def _code() -> str:
    """A unique seeded code: `ZZT` + a per-run tag + a counter (11 characters, like the
    uuid slices it replaces). Random 8-hex slices collided on
    uq_orders_company_order_number once a test seeded thousands of orders."""
    return f"ZZT{_RUN}{next(_SEQ):06d}"


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


# --------------------------------------------------------------------------------------- #
# seed helpers
# --------------------------------------------------------------------------------------- #


def _agent(db, code, name=None, *, company_id=None, active=True, person_label=None):
    from app.models.sales_agent import SalesAgent

    row = SalesAgent(
        id=_uid(), sales_agent=f"ZZT{code}-{_uid()[:6]}".upper(), description=name or code,
        is_active=active, company_id=company_id, person_label=person_label,
    )
    db.add(row)
    db.flush()
    return row


def _category(db, company_id, name="ZZT Cat", parent_id=None):
    from app.models.product import ProductCategory

    row = ProductCategory(
        id=_uid(), company_id=company_id, category_code=_code(), category_name=name,
        parent_category_id=parent_id,
    )
    db.add(row)
    db.flush()
    return row


def _uom(db):
    from app.models.product import UnitOfMeasure

    row = UnitOfMeasure(id=_uid(), company_id=None, uom_code=_code(), uom_name="ZZT UOM")
    db.add(row)
    db.flush()
    return row


def _product(db, company_id, category_id, uom_id=None):
    from app.models.product import Product

    uom_id = uom_id or _uom(db).id
    row = Product(
        id=_uid(), company_id=company_id, product_code=_code(), product_name="ZZT Product",
        category_id=category_id, base_uom_id=uom_id, list_price=Decimal("0"),
    )
    db.add(row)
    db.flush()
    return row


def _warehouse(db, company_id):
    from app.models.inventory import Warehouse

    row = Warehouse(id=_uid(), company_id=company_id, warehouse_code=_code(), is_active=True)
    db.add(row)
    db.flush()
    return row


def _so_line(
    db, company_id, *, agent_id=None, customer_id=None, order_date, line_total, product_id,
    qty_ordered=1, qty_delivered=1, status="open", line_status="open", demand_class=None,
):
    from app.models.order import SalesOrder, SalesOrderLine

    so = SalesOrder(
        id=_uid(), company_id=company_id, so_number=_code(), order_date=order_date,
        status=status, sales_agent_id=agent_id, customer_id=customer_id, demand_class=demand_class,
    )
    db.add(so)
    db.flush()
    line = SalesOrderLine(
        id=_uid(), company_id=company_id, sales_order_id=so.id, product_id=product_id,
        qty_ordered=qty_ordered, qty_delivered=qty_delivered, line_total=line_total,
        line_status=line_status,
    )
    db.add(line)
    db.flush()
    return so, line


def _do_line(db, company_id, product_id, warehouse_id, sales_order_line_id, *, quantity, order_date, is_cancelled=False):
    from app.models.order import Order, OrderLine

    order = Order(
        id=_uid(), company_id=company_id, order_number=_code(), order_date=order_date,
        is_cancelled=is_cancelled,
    )
    db.add(order)
    db.flush()
    line = OrderLine(
        id=_uid(), company_id=company_id, line_sequence=1, order_id=order.id, product_id=product_id,
        warehouse_id=warehouse_id, quantity=quantity, sales_order_line_id=sales_order_line_id,
    )
    db.add(line)
    db.flush()
    return order.id, line.id


# --------------------------------------------------------------------------------------- #
# client / permission fixtures (copied from tests/test_sales_teams_s6.py)
# --------------------------------------------------------------------------------------- #


def _client(db, permissions):
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.main import app
    from app.services.company_scope_resolver import apply_company_scope
    from app.services.user_service import UserPermissionService

    user_id = _uid()
    actor = {"id": user_id, "email": f"{user_id}@zzt.test", "role": "user"}
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: dict(actor)
    app.dependency_overrides[get_current_user_or_api_key] = lambda: dict(actor)
    app.dependency_overrides[apply_company_scope] = lambda: None

    originals = (
        UserPermissionService.check_user_has_permission,
        UserPermissionService.get_user_permission_slugs,
    )
    granted = list(permissions)
    UserPermissionService.check_user_has_permission = lambda self, uid, slug: slug in granted
    UserPermissionService.get_user_permission_slugs = lambda self, uid: list(granted)
    return TestClient(app), originals


def _restore(originals) -> None:
    from app.main import app
    from app.services.user_service import UserPermissionService

    UserPermissionService.check_user_has_permission = originals[0]
    UserPermissionService.get_user_permission_slugs = originals[1]
    app.dependency_overrides.clear()


@pytest.fixture
def world(monkeypatch):
    from app.services.sales import team_service

    monkeypatch.setattr(team_service, "_today", lambda: TODAY)
    for mod in ("target_service", "achievement_service"):
        try:
            module = __import__(f"app.services.sales.{mod}", fromlist=["_today"])
        except ModuleNotFoundError:
            continue
        if hasattr(module, "_today"):
            monkeypatch.setattr(module, "_today", lambda: TODAY)
    with blank_session() as db:
        company_id = _sorento(db)
        with company_scope(db, frozenset({company_id})):
            yield db, company_id


@pytest.fixture
def api(world):
    db, company_id = world
    client, originals = _client(db, ALL)
    try:
        yield client, db, company_id
    finally:
        _restore(originals)


# --------------------------------------------------------------------------------------- #
# period_service (S1-19)
# --------------------------------------------------------------------------------------- #


def test_generate_periods_golden(world):
    """The 16.2 golden table, verbatim."""
    from app.services.sales import period_service

    # No split: one period for the whole range.
    assert period_service.generate_periods(date(2026, 10, 1), date(2026, 12, 15), None, None) == [
        (date(2026, 10, 1), date(2026, 12, 15)),
    ]

    # Monthly: 1-31 Oct, 1-30 Nov, 1-15 Dec.
    assert period_service.generate_periods(date(2026, 10, 1), date(2026, 12, 15), 1, "month") == [
        (date(2026, 10, 1), date(2026, 10, 31)),
        (date(2026, 11, 1), date(2026, 11, 30)),
        (date(2026, 12, 1), date(2026, 12, 15)),
    ]

    # Every 2 weeks: five 14-day periods, then a 6-day tail (1-14, 15-28 Oct, 29 Oct-11 Nov,
    # 12-25 Nov, 26 Nov-9 Dec, 10-15 Dec).
    assert period_service.generate_periods(date(2026, 10, 1), date(2026, 12, 15), 2, "week") == [
        (date(2026, 10, 1), date(2026, 10, 14)),
        (date(2026, 10, 15), date(2026, 10, 28)),
        (date(2026, 10, 29), date(2026, 11, 11)),
        (date(2026, 11, 12), date(2026, 11, 25)),
        (date(2026, 11, 26), date(2026, 12, 9)),
        (date(2026, 12, 10), date(2026, 12, 15)),
    ]

    # A start on the 31st, monthly: steps to the shorter month's last day (28 Feb 2027).
    assert period_service.generate_periods(date(2027, 1, 31), date(2027, 3, 30), 1, "month") == [
        (date(2027, 1, 31), date(2027, 2, 27)),
        (date(2027, 2, 28), date(2027, 3, 30)),
    ]

    # Every 2 weeks landing exactly on the end date: five periods, no short tail.
    assert period_service.generate_periods(date(2026, 10, 1), date(2026, 12, 9), 2, "week") == [
        (date(2026, 10, 1), date(2026, 10, 14)),
        (date(2026, 10, 15), date(2026, 10, 28)),
        (date(2026, 10, 29), date(2026, 11, 11)),
        (date(2026, 11, 12), date(2026, 11, 25)),
        (date(2026, 11, 26), date(2026, 12, 9)),
    ]

    # A one-day range is valid.
    assert period_service.generate_periods(date(2026, 10, 1), date(2026, 10, 1), None, None) == [
        (date(2026, 10, 1), date(2026, 10, 1)),
    ]


def test_period_cap_104(world):
    from app.services.error_handler import AppException
    from app.services.sales import period_service

    # 1 Jan 2026 to 29 Dec 2027 weekly: 728 days / 7 = exactly 104 periods, still fine.
    ok = period_service.generate_periods(date(2026, 1, 1), date(2027, 12, 29), 1, "week")
    assert len(ok) == 104

    # Two days later: 105 periods, 422 TOO_MANY_PERIODS.
    with pytest.raises(AppException) as exc:
        period_service.generate_periods(date(2026, 1, 1), date(2027, 12, 31), 1, "week")
    assert exc.value.status_code == 422
    assert exc.value.detail.get("code") == "TOO_MANY_PERIODS"


# --------------------------------------------------------------------------------------- #
# create / validate (S1-1, S1-3, S1-19)
# --------------------------------------------------------------------------------------- #


def test_create_rejects_bad_dates_and_half_split(api):
    client, db, _ = api
    agent = _agent(db, "A")
    base = {
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT Target",
        "metric": "amount", "basis": "ordered", "product_scope": "all", "target_value": 1000,
    }
    assert client.post(BASE, json={**base, "start_date": "2026-12-15", "end_date": "2026-10-01"}).status_code == 422
    assert client.post(BASE, json={**base, "start_date": "2026-10-01", "end_date": "2026-12-31", "split_every": 1}).status_code == 422
    assert client.post(BASE, json={**base, "start_date": "2026-10-01", "end_date": "2026-12-31", "split_unit": "month"}).status_code == 422
    res = client.post(BASE, json={
        **base, "start_date": "2026-01-01", "end_date": "2027-12-31", "split_every": 1, "split_unit": "week",
    })
    assert res.status_code == 422, res.text


def test_create_agent_target(api):
    client, db, _ = api
    agent = _agent(db, "A", "Agent A")
    payload = {
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT Ali FY26 H2",
        "metric": "amount", "basis": "ordered", "product_scope": "all",
        "start_date": "2026-10-01", "end_date": "2027-03-31",
        "split_every": 1, "split_unit": "month", "target_value": 120000,
    }
    res = client.post(BASE, json=payload)
    assert res.status_code == 201, res.text
    body = res.json()
    assert re.match(r"^TGT-\d{6}$", body["target_no"])
    assert body["subject_kind"] == "agent"
    assert body["sales_agent_id"] == agent.id
    periods = body["periods"]
    assert len(periods) == 6
    assert [p["target_value"] for p in periods] == [120000] * 6
    assert periods[0]["period_start"] == "2026-10-01"
    assert periods[-1]["period_end"] == "2027-03-31"


def test_target_no_per_company_and_concurrent_unique(api):
    client, db, _ = api
    agent = _agent(db, "A")

    def make(name):
        return client.post(BASE, json={
            "subject_kind": "agent", "sales_agent_id": agent.id, "name": name,
            "metric": "amount", "basis": "ordered", "product_scope": "all",
            "start_date": "2026-10-01", "end_date": "2026-10-31", "target_value": 1000,
        }).json()

    first = make("ZZT One")
    second = make("ZZT Two")
    assert first["target_no"] != second["target_no"]
    n1 = int(first["target_no"].split("-")[1])
    n2 = int(second["target_no"].split("-")[1])
    assert n2 == n1 + 1


def test_legacy_payload_fields_422(api):
    client, db, _ = api
    agent = _agent(db, "A")
    res = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_month": "2026-10-01", "months": 6,
        "periodicity": "month", "target_value": 120000,
    })
    assert res.status_code == 422, res.text


def test_scope_validation(api):
    client, db, company_id = api
    agent = _agent(db, "A")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    base = {
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "start_date": "2026-10-01", "end_date": "2026-10-31", "target_value": 1000,
    }
    assert client.post(BASE, json={**base, "product_scope": "categories", "category_ids": []}).status_code == 422
    assert client.post(BASE, json={
        **base, "product_scope": "categories", "category_ids": [category.id], "product_ids": [product.id],
    }).status_code == 422
    assert client.post(BASE, json={**base, "product_scope": "products", "product_ids": []}).status_code == 422
    assert client.post(BASE, json={**base, "product_scope": "all", "category_ids": [category.id]}).status_code == 422
    ok = client.post(BASE, json={**base, "product_scope": "categories", "category_ids": [category.id]})
    assert ok.status_code == 201, ok.text


# --------------------------------------------------------------------------------------- #
# period patch / header edit keep rule (S1-4, S1-5)
# --------------------------------------------------------------------------------------- #


def test_patch_period_one_only(api):
    client, db, _ = api
    agent = _agent(db, "A")
    created = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-12-31",
        "split_every": 1, "split_unit": "month", "target_value": 1000,
    }).json()
    periods = created["periods"]
    res = client.patch(f"{BASE}/{created['id']}/periods/{periods[1]['id']}", json={"target_value": 250})
    assert res.status_code == 200, res.text
    values = {p["id"]: p["target_value"] for p in res.json()["periods"]}
    assert values[periods[0]["id"]] == 1000
    assert values[periods[1]["id"]] == 250
    assert values[periods[2]["id"]] == 1000


def test_header_change_keep_rule(api):
    client, db, _ = api
    agent = _agent(db, "A")
    created = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-12-31",
        "split_every": 1, "split_unit": "month", "target_value": 100,
    }).json()
    periods = {p["period_start"]: p["id"] for p in created["periods"]}
    client.patch(f"{BASE}/{created['id']}/periods/{periods['2026-10-01']}", json={"target_value": 100})
    client.patch(f"{BASE}/{created['id']}/periods/{periods['2026-11-01']}", json={"target_value": 200})
    client.patch(f"{BASE}/{created['id']}/periods/{periods['2026-12-01']}", json={"target_value": 300})

    extended = client.patch(f"{BASE}/{created['id']}", json={"end_date": "2027-01-31"})
    assert extended.status_code == 200, extended.text
    by_start = {p["period_start"]: p["target_value"] for p in extended.json()["periods"]}
    assert by_start["2026-10-01"] == 100
    assert by_start["2026-11-01"] == 200
    assert by_start["2026-12-01"] == 300
    # Jan is a brand new period: no old period contains its start, so it takes the nearest
    # old period in time (December's).
    assert by_start["2027-01-01"] == 300


# --------------------------------------------------------------------------------------- #
# list (S1-6, S1-20)
# --------------------------------------------------------------------------------------- #


def test_list_on_date_rows_and_no_target(api):
    client, db, _ = api
    with_target = _agent(db, "A")
    no_target = _agent(db, "B")
    client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": with_target.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 500,
    })
    body = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()
    assert body["on"] == "2026-10-15"
    by_agent = {r["sales_agent_id"]: r for r in body["rows"]}
    assert by_agent[with_target.id]["target_value"] == 500
    assert by_agent[no_target.id]["target_id"] is None
    assert by_agent[no_target.id]["achieved_value"] is None


def test_period_end_inclusive(api):
    client, db, company_id = api
    agent = _agent(db, "A")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    target = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    _so_line(db, company_id, agent_id=agent.id, order_date=date(2026, 10, 31), line_total=Decimal("100"), product_id=product.id)
    _so_line(db, company_id, agent_id=agent.id, order_date=date(2026, 11, 1), line_total=Decimal("999"), product_id=product.id)

    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    row = next(r for r in rows if r["sales_agent_id"] == agent.id)
    assert row["achieved_value"] == 100  # the day after period_end never counts


def test_ended_target_not_listed(api):
    client, db, _ = api
    agent = _agent(db, "A")
    client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT Ended", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-09-01", "end_date": "2026-10-14",
        "target_value": 100,
    })
    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    row = next(r for r in rows if r["sales_agent_id"] == agent.id)
    assert row["target_id"] is None  # the "No target" row, not the ended one


# --------------------------------------------------------------------------------------- #
# achievement goldens (S1-7, S1-8, S1-9, S1-10, 3.2)
# --------------------------------------------------------------------------------------- #


def test_amount_golden(api):
    client, db, company_id = api
    a = _agent(db, "A")
    b = _agent(db, "B")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)

    def line(order_date, line_total, qty_ordered=1, qty_delivered=1, status="open", line_status="open", agent=a):
        return _so_line(
            db, company_id, agent_id=agent.id, order_date=order_date, line_total=Decimal(str(line_total)),
            qty_ordered=qty_ordered, qty_delivered=qty_delivered, status=status, line_status=line_status,
            product_id=product.id,
        )

    line(date(2026, 10, 5), 1000, qty_ordered=10, qty_delivered=10)                    # counts: open
    line(date(2026, 10, 10), 500, qty_ordered=5, qty_delivered=2, status="closed")     # counts: not cancelled
    line(date(2026, 10, 12), 9999, qty_ordered=1, qty_delivered=1, status="cancelled") # excluded: cancelled order
    line(date(2026, 10, 15), 300, qty_ordered=3, qty_delivered=3)                      # counts: open line
    line(date(2026, 10, 15), 700, qty_ordered=1, qty_delivered=1, line_status="cancelled")  # excluded: cancelled line
    line(date(2026, 9, 30), 400, qty_ordered=4, qty_delivered=4)                       # excluded: wrong month
    _so_line(db, company_id, agent_id=a.id, order_date=None, line_total=Decimal("600"),
              qty_ordered=6, qty_delivered=6, product_id=product.id)                    # excluded: no order_date
    line(date(2026, 10, 20), 800, qty_ordered=8, qty_delivered=8, agent=b)             # excluded: agent B
    line(date(2026, 10, 8), 250, qty_ordered=0, qty_delivered=0)                       # ordered 250, delivered 0

    ordered = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": a.id, "name": "ZZT Ordered", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    delivered = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": a.id, "name": "ZZT Delivered", "metric": "amount",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()

    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    ordered_row = next(r for r in rows if r["target_id"] == ordered["id"])
    delivered_row = next(r for r in rows if r["target_id"] == delivered["id"])
    # ordered = 1000 + 500 + 300 + 250 (qty_ordered=0 line still counts its line_total) = 2050
    assert ordered_row["achieved_value"] == 2050
    # delivered = 1000 (10/10) + 200 (500*2/5) + 300 (3/3) + 0 (qty_ordered=0) = 1500
    assert delivered_row["achieved_value"] == 1500


def test_quantity_golden_capped(api):
    client, db, company_id = api
    a = _agent(db, "A")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    # Over-delivered by AutoCount: ordered 10, delivered 12.
    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 5), line_total=Decimal("1000"),
              qty_ordered=10, qty_delivered=12, product_id=product.id)
    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 6), line_total=Decimal("500"),
              qty_ordered=4, qty_delivered=4, product_id=product.id)

    ordered = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": a.id, "name": "ZZT Qty Ordered", "metric": "quantity",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    delivered = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": a.id, "name": "ZZT Qty Delivered", "metric": "quantity",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    assert next(r for r in rows if r["target_id"] == ordered["id"])["achieved_value"] == 14   # 10 + 4
    assert next(r for r in rows if r["target_id"] == delivered["id"])["achieved_value"] == 14  # least(12,10)+4, never 16


def test_quantity_leaves_an_autocount_cancel_out(api):
    """AC-CANCEL-CRM: 10 ordered, TransferedQty 10 of which 4 is AutoCount Cancel SO. Neither
    basis credits the agent with the cancelled 4."""
    client, db, company_id = api
    a = _agent(db, "A")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    _so, line = _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 5),
                         line_total=Decimal("1000"), qty_ordered=10, qty_delivered=10,
                         product_id=product.id)
    line.qty_cancelled = 4
    db.flush()
    ids = {}
    for basis in ("ordered", "delivered"):
        ids[basis] = client.post(BASE, json={
            "subject_kind": "agent", "sales_agent_id": a.id, "name": f"ZZT Qty {basis}",
            "metric": "quantity", "basis": basis, "product_scope": "all",
            "start_date": "2026-10-01", "end_date": "2026-10-31", "target_value": 0,
        }).json()["id"]
    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    got = {b: next(r for r in rows if r["target_id"] == i)["achieved_value"] for b, i in ids.items()}
    assert got == {"ordered": 6, "delivered": 6}


def test_scope_subcategories_products_all(api):
    client, db, company_id = api
    a = _agent(db, "A")
    basins = _category(db, company_id, "ZZT Basins")
    countertop = _category(db, company_id, "ZZT Basins Countertop", parent_id=basins.id)
    taps = _category(db, company_id, "ZZT Taps")
    p_basin = _product(db, company_id, basins.id)
    p_countertop = _product(db, company_id, countertop.id)
    p_tap = _product(db, company_id, taps.id)

    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 5), line_total=Decimal("100"), product_id=p_basin.id)
    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 6), line_total=Decimal("50"), product_id=p_countertop.id)
    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 7), line_total=Decimal("999"), product_id=p_tap.id)

    def target(name, product_scope, **extra):
        return client.post(BASE, json={
            "subject_kind": "agent", "sales_agent_id": a.id, "name": name, "metric": "amount",
            "basis": "ordered", "product_scope": product_scope, "start_date": "2026-10-01",
            "end_date": "2026-10-31", "target_value": 0, **extra,
        }).json()

    cat_target = target("ZZT Basins", "categories", category_ids=[basins.id])
    prod_target = target("ZZT Countertop only", "products", product_ids=[p_countertop.id])
    all_target = target("ZZT All", "all")

    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    by_id = {r["target_id"]: r for r in rows}
    assert by_id[cat_target["id"]]["achieved_value"] == 150   # basin + its sub-category, not taps
    assert by_id[prod_target["id"]]["achieved_value"] == 50   # countertop product only
    assert by_id[all_target["id"]]["achieved_value"] == 1149  # everything


def test_project_orders_count(api):
    client, db, company_id = api
    a = _agent(db, "A")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 5), line_total=Decimal("100"),
              product_id=product.id, demand_class="retail")
    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 6), line_total=Decimal("200"),
              product_id=product.id, demand_class="project")

    target = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": a.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    row = next(r for r in rows if r["target_id"] == target["id"])
    assert row["achieved_value"] == 300  # project order counts exactly as retail does


def test_value_expr_pinned_to_sales_report(api):
    """3.2: `achievement_value_expr` must not drift from `sales_report_service`'s own
    `confirmed_value` - proven by seeding a company-isolated line set and comparing both."""
    from app.services import sales_report_service

    client, db, company_id = api
    a = _agent(db, "A")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 5), line_total=Decimal("1000"),
              qty_ordered=10, qty_delivered=7, product_id=product.id)
    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 20), line_total=Decimal("450"),
              qty_ordered=3, qty_delivered=3, product_id=product.id)

    target = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": a.id, "name": "ZZT", "metric": "amount",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    achieved = next(r for r in rows if r["target_id"] == target["id"])["achieved_value"]

    report = sales_report_service.sales_report(db, date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))
    october = next(m for m in report["months"] if m["month"] == "2026-10")
    assert achieved == october["confirmed_value"]


# --------------------------------------------------------------------------------------- #
# duplicate (S1-12)
# --------------------------------------------------------------------------------------- #


def test_duplicate_month_aligned(api):
    client, db, _ = api
    agent = _agent(db, "A")
    source = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT Source", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-12-31",
        "split_every": 1, "split_unit": "month", "target_value": 100,
    }).json()
    dup = client.post(f"{BASE}/{source['id']}/duplicate")
    assert dup.status_code == 201, dup.text
    body = dup.json()
    assert body["id"] != source["id"]
    assert body["target_no"] != source["target_no"]
    assert body["name"] == "ZZT Source (copy)"
    assert body["start_date"] == "2027-01-01"
    assert body["end_date"] == "2027-03-31"  # same 3 whole months, starting the day after


def test_duplicate_day_count(api):
    client, db, _ = api
    agent = _agent(db, "A")
    source = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT Source", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-05", "end_date": "2026-10-14",
        "target_value": 100,
    }).json()  # 10 days, not month aligned
    dup = client.post(f"{BASE}/{source['id']}/duplicate").json()
    assert dup["start_date"] == "2026-10-15"
    assert dup["end_date"] == "2026-10-24"  # same 10-day span


def test_duplicate_figures_scope_new_no(api):
    client, db, company_id = api
    agent = _agent(db, "A")
    category = _category(db, company_id)
    source = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT Source", "metric": "amount",
        "basis": "ordered", "product_scope": "categories", "category_ids": [category.id],
        "start_date": "2026-10-01", "end_date": "2026-11-30", "split_every": 1, "split_unit": "month",
        "target_value": 0,
    }).json()
    periods = {p["period_start"]: p["id"] for p in source["periods"]}
    client.patch(f"{BASE}/{source['id']}/periods/{periods['2026-10-01']}", json={"target_value": 111})
    client.patch(f"{BASE}/{source['id']}/periods/{periods['2026-11-01']}", json={"target_value": 222})

    dup = client.post(f"{BASE}/{source['id']}/duplicate").json()
    assert dup["target_no"] != source["target_no"]
    assert dup["product_scope"] == "categories"
    assert [s["id"] for s in dup["scope"]] == [category.id]
    ordered_periods = sorted(dup["periods"], key=lambda p: p["period_start"])
    assert [p["target_value"] for p in ordered_periods] == [111, 222]


def test_duplicate_team_and_child(api):
    client, db, _ = api
    a = _agent(db, "A")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id]}).json()
    source = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team Source", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 500}],
    }).json()
    dup = client.post(f"{BASE}/{source['id']}/duplicate")
    assert dup.status_code == 201, dup.text
    body = dup.json()
    assert body["subject_kind"] == "team"
    assert body["child_count"] == 1
    assert body["children"][0]["periods"][0]["target_value"] == 500
    assert body["periods"][0]["target_value"] == 500


# --------------------------------------------------------------------------------------- #
# permissions, company scope, delete (S1-13)
# --------------------------------------------------------------------------------------- #


def test_routes_403_per_slug(world):
    db, company_id = world
    agent = _agent(db, "A")
    target_id = _uid()
    routes = [
        ("get", BASE, None, "sales.targets.view"),
        ("get", f"{BASE}/options", None, "sales.targets.view"),
        ("get", f"{BASE}/{target_id}", None, "sales.targets.view"),
        ("post", BASE, {
            "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
            "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01",
            "end_date": "2026-10-31", "target_value": 1,
        }, "sales.targets.add"),
        ("patch", f"{BASE}/{target_id}", {"name": "ZZT Renamed"}, "sales.targets.edit"),
        ("patch", f"{BASE}/{target_id}/periods/{_uid()}", {"target_value": 1}, "sales.targets.edit"),
        ("post", f"{BASE}/{target_id}/duplicate", None, "sales.targets.edit"),
        ("post", f"{BASE}/{target_id}/children", {"sales_agent_id": agent.id, "target_value": 1}, "sales.targets.edit"),
        ("delete", f"{BASE}/{target_id}", None, "sales.targets.delete"),
    ]
    for method, url, body, slug in routes:
        others = [s for s in ALL_TARGET if s != slug]
        client, originals = _client(db, others)
        try:
            kwargs = {"json": body} if body is not None else {}
            res = getattr(client, method)(url, **kwargs)
        finally:
            _restore(originals)
        assert res.status_code == 403, f"{method.upper()} {url} without {slug}: {res.status_code}"


def test_company_scope(api):
    from app.models.company import Company

    client, db, _ = api
    other = Company(id=_uid(), name="ZZT Other Co", code=f"Z{_uid()[:6]}")
    db.add(other)
    db.flush()
    with company_scope(db, None):
        foreign_agent = _agent(db, "X", company_id=other.id)
        from app.models.sales import SalesTarget

        foreign_target = SalesTarget(
            id=_uid(), company_id=other.id, target_no="TGT-999999", name="ZZT Foreign",
            subject_kind="agent", sales_agent_id=foreign_agent.id, metric="amount", basis="ordered",
            product_scope="all", start_date=date(2026, 10, 1), end_date=date(2026, 10, 31),
        )
        db.add(foreign_target)
        db.flush()

    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    assert foreign_target.id not in [r["target_id"] for r in rows]
    assert client.get(f"{BASE}/{foreign_target.id}").status_code == 404
    assert client.delete(f"{BASE}/{foreign_target.id}").status_code == 404


def test_delete_hard(api):
    client, db, _ = api
    agent = _agent(db, "A")
    target = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 1,
    }).json()
    res = client.delete(f"{BASE}/{target['id']}")
    assert res.status_code == 200, res.text
    assert client.get(f"{BASE}/{target['id']}").status_code == 404

    from app.models.sales import SalesTarget, SalesTargetPeriod

    db.expire_all()
    assert db.query(SalesTarget).filter(SalesTarget.id == target["id"]).count() == 0
    assert db.query(SalesTargetPeriod).filter(SalesTargetPeriod.target_id == target["id"]).count() == 0


# --------------------------------------------------------------------------------------- #
# unassigned (S1-14)
# --------------------------------------------------------------------------------------- #


def test_unassigned_calendar_month(api):
    client, db, company_id = api
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    _so_line(db, company_id, agent_id=None, order_date=date(2026, 10, 5), line_total=Decimal("300"), product_id=product.id)
    _so_line(db, company_id, agent_id=None, order_date=date(2026, 10, 20), line_total=Decimal("200"), product_id=product.id)
    _so_line(db, company_id, agent_id=None, order_date=date(2026, 11, 1), line_total=Decimal("999"), product_id=product.id)

    body = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()
    assert body["unassigned_amount"] == 500


# --------------------------------------------------------------------------------------- #
# delivered by DO date (S1-26)
# --------------------------------------------------------------------------------------- #


def test_do_golden_a_to_d(api):
    """Each case gets its OWN agent: (a), (b)/(c) and (d) otherwise share one September period
    and the same agent's September achievement sums every line in it, so (b)'s residual would
    read 13,000 (its own 3,000 plus (a)'s whole 10,000) instead of the UAC's 3,000."""
    client, db, company_id = api
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    warehouse = _warehouse(db, company_id)

    def new_line(agent):
        _, ln = _so_line(
            db, company_id, agent_id=agent.id, order_date=date(2026, 9, 20), line_total=Decimal("10000"),
            qty_ordered=100, qty_delivered=100, product_id=product.id,
        )
        return ln

    def target_for(agent, start, end):
        return client.post(BASE, json={
            "subject_kind": "agent", "sales_agent_id": agent.id, "name": f"ZZT {start}", "metric": "amount",
            "basis": "delivered", "product_scope": "all", "start_date": start, "end_date": end,
            "target_value": 0,
        }).json()

    def achieved(target_id, on):
        rows = client.get(BASE, params={"on": on, "subject": "agent"}).json()["rows"]
        return next(r for r in rows if r["target_id"] == target_id)["achieved_value"]

    # (a) no linked DO line: October counts 0, September keeps the whole 100 / RM 10,000.
    agent_a = _agent(db, "DOA")
    line_a = new_line(agent_a)
    sep_a = target_for(agent_a, "2026-09-01", "2026-09-30")
    oct_a = target_for(agent_a, "2026-10-01", "2026-10-31")
    assert achieved(sep_a["id"], "2026-09-25") == 10000
    assert achieved(oct_a["id"], "2026-10-15") == 0

    # (b) DO of 40 on 5 Oct and 30 on 3 Nov: Oct 4000, Nov 3000, September's residual 3000.
    agent_b = _agent(db, "DOB")
    line_b = new_line(agent_b)
    sep_b = target_for(agent_b, "2026-09-01", "2026-09-30")
    oct_b = target_for(agent_b, "2026-10-01", "2026-10-31")
    nov_b = target_for(agent_b, "2026-11-01", "2026-11-30")
    _do_line(db, company_id, product.id, warehouse.id, line_b.id, quantity=40, order_date=date(2026, 10, 5))
    do_nov_order, _do_nov_line = _do_line(db, company_id, product.id, warehouse.id, line_b.id, quantity=30, order_date=date(2026, 11, 3))
    assert achieved(oct_b["id"], "2026-10-15") == 4000
    assert achieved(nov_b["id"], "2026-11-15") == 3000
    assert achieved(sep_b["id"], "2026-09-25") == 3000

    # (c) the 3 Nov DO cancelled: November counts 0, September's residual becomes 6000.
    from app.models.order import Order

    db.query(Order).filter(Order.id == do_nov_order).update({"is_cancelled": True})
    db.flush()
    assert achieved(nov_b["id"], "2026-11-15") == 0
    assert achieved(sep_b["id"], "2026-09-25") == 6000

    # (d) a fresh line: DO of 70 on 5 Oct, 50 on 3 Nov, on a line of 100 - never more than 100.
    agent_d = _agent(db, "DOD")
    line_d = new_line(agent_d)
    sep_d = target_for(agent_d, "2026-09-01", "2026-09-30")
    oct_d = target_for(agent_d, "2026-10-01", "2026-10-31")
    nov_d = target_for(agent_d, "2026-11-01", "2026-11-30")
    _do_line(db, company_id, product.id, warehouse.id, line_d.id, quantity=70, order_date=date(2026, 10, 5))
    _do_line(db, company_id, product.id, warehouse.id, line_d.id, quantity=50, order_date=date(2026, 11, 3))
    assert achieved(oct_d["id"], "2026-10-15") == 7000
    assert achieved(nov_d["id"], "2026-11-15") == 3000  # only 30 of the 50 counts
    assert achieved(sep_d["id"], "2026-09-25") == 0


def test_do_salesman_text_ignored(api):
    client, db, company_id = api
    agent = _agent(db, "A")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    warehouse = _warehouse(db, company_id)
    _, line = _so_line(db, company_id, agent_id=agent.id, order_date=date(2026, 10, 5), line_total=Decimal("1000"),
                         qty_ordered=10, qty_delivered=10, product_id=product.id)
    from app.models.order import Order, OrderLine

    do_order = Order(
        id=_uid(), company_id=company_id, order_number=_code(), order_date=date(2026, 10, 6),
        is_cancelled=False, agent="Someone Else", salesman="Someone Else",
    )
    db.add(do_order)
    db.flush()
    db.add(OrderLine(
        id=_uid(), company_id=company_id, line_sequence=1, order_id=do_order.id, product_id=product.id,
        warehouse_id=warehouse.id, quantity=10, sales_order_line_id=line.id,
    ))
    db.flush()

    target = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    row = next(r for r in rows if r["target_id"] == target["id"])
    assert row["achieved_value"] == 1000  # counts for the SO's agent, never the DO's free text


def test_do_rule_team_and_quantity(api):
    client, db, company_id = api
    a = _agent(db, "A")
    team = client.post(TEAMS_BASE, json={"name": "ZZT DO Team", "sales_agent_ids": [a.id]}).json()
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    warehouse = _warehouse(db, company_id)
    _, line = _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 9, 20), line_total=Decimal("1000"),
                         qty_ordered=10, qty_delivered=10, product_id=product.id)
    _do_line(db, company_id, product.id, warehouse.id, line.id, quantity=6, order_date=date(2026, 10, 5))

    qty_target = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": a.id, "name": "ZZT Qty", "metric": "quantity",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    team_target = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team", "metric": "amount",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 0}],
    }).json()

    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    assert next(r for r in rows if r["target_id"] == qty_target["id"])["achieved_value"] == 6

    team_rows = client.get(BASE, params={"on": "2026-10-15", "subject": "team", "sales_team_id": team["id"]}).json()["rows"]
    assert next(r for r in team_rows if r["target_id"] == team_target["id"])["achieved_value"] == 600


# --------------------------------------------------------------------------------------- #
# team targets (S1-27, S1-28, S6-4, S6-5)
# --------------------------------------------------------------------------------------- #


def test_team_create_children_and_sum(api):
    client, db, _ = api
    a = _agent(db, "A")
    b = _agent(db, "B")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id, b.id]}).json()
    res = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team Target", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [
            {"sales_agent_id": a.id, "target_value": 600},
            {"sales_agent_id": b.id, "target_value": 400},
        ],
    })
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["child_count"] == 2
    assert body["periods"][0]["target_value"] == 1000
    children = {c["sales_agent_id"]: c for c in body["children"]}
    assert children[a.id]["periods"][0]["target_value"] == 600
    assert children[b.id]["periods"][0]["target_value"] == 400


def test_team_create_member_any_day_in_range(api):
    """An agent who joins mid-range still qualifies: member on any day in it (S1-27)."""
    client, db, _ = api
    a = _agent(db, "A")
    b = _agent(db, "B")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id]}).json()
    client.put(f"{TEAMS_BASE}/{team['id']}/members", json={"sales_agent_ids": [a.id, b.id], "moves_on": "2026-10-15"})
    res = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team Target", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [
            {"sales_agent_id": a.id, "target_value": 600},
            {"sales_agent_id": b.id, "target_value": 400},
        ],
    })
    assert res.status_code == 201, res.text


def test_team_create_rejects_non_member(api):
    client, db, _ = api
    a = _agent(db, "A")
    outsider = _agent(db, "OUT")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id]}).json()
    res = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team Target", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [
            {"sales_agent_id": a.id, "target_value": 600},
            {"sales_agent_id": outsider.id, "target_value": 400},
        ],
    })
    assert res.status_code == 422, res.text


def test_team_period_patch_is_sum(api):
    client, db, _ = api
    a = _agent(db, "A")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id]}).json()
    created = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team Target", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 500}],
    }).json()
    period_id = created["periods"][0]["id"]
    res = client.patch(f"{BASE}/{created['id']}/periods/{period_id}", json={"target_value": 999})
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "TEAM_TARGET_IS_SUM"


def test_child_edit_add_delete_resum(api):
    client, db, _ = api
    a = _agent(db, "A")
    b = _agent(db, "B")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id, b.id]}).json()
    created = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team Target", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 600}],
    }).json()
    a_child_id = created["children"][0]["target_id"]
    a_period_id = next(p["id"] for p in client.get(f"{BASE}/{a_child_id}").json()["periods"])

    edited = client.patch(f"{BASE}/{a_child_id}/periods/{a_period_id}", json={"target_value": 700})
    assert edited.status_code == 200, edited.text
    parent = client.get(f"{BASE}/{created['id']}").json()
    assert parent["periods"][0]["target_value"] == 700

    added = client.post(f"{BASE}/{created['id']}/children", json={"sales_agent_id": b.id, "target_value": 300})
    assert added.status_code == 201, added.text
    assert added.json()["periods"][0]["target_value"] == 1000

    b_child_id = next(c["target_id"] for c in added.json()["children"] if c["sales_agent_id"] == b.id)
    assert client.delete(f"{BASE}/{b_child_id}").status_code == 200
    parent = client.get(f"{BASE}/{created['id']}").json()
    assert parent["periods"][0]["target_value"] == 700


def test_team_edit_rewrites_children(api):
    client, db, _ = api
    a = _agent(db, "A")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id]}).json()
    created = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team Target", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 500}],
    }).json()
    res = client.patch(f"{BASE}/{created['id']}", json={"end_date": "2026-11-30"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["end_date"] == "2026-11-30"
    child_detail = client.get(f"{BASE}/{body['children'][0]['target_id']}").json()
    assert child_detail["end_date"] == "2026-11-30"


def test_child_follows_parent_422(api):
    client, db, _ = api
    a = _agent(db, "A")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id]}).json()
    created = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team Target", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 500}],
    }).json()
    child_id = created["children"][0]["target_id"]
    res = client.patch(f"{BASE}/{child_id}", json={"metric": "quantity"})
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "CHILD_FOLLOWS_PARENT"
    renamed = client.patch(f"{BASE}/{child_id}", json={"name": "ZZT A's own line"})
    assert renamed.status_code == 200, renamed.text


def test_team_delete_cascades_via_record_action(api):
    import app.services.record_actions  # noqa: F401
    from app.services.form_action_registry import get_action

    client, db, _ = api
    a = _agent(db, "A")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id]}).json()
    created = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team Target", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 500}],
    }).json()
    action = get_action("sales_target.delete")
    assert action is not None
    assert action.permission == "sales.targets.delete"
    action.execute(db, {"entity_id": created["id"]})
    db.flush()
    from app.models.sales import SalesTarget

    assert db.query(SalesTarget).filter(SalesTarget.id == created["id"]).count() == 0
    assert db.query(SalesTarget).filter(SalesTarget.parent_target_id == created["id"]).count() == 0


def test_child_keeps_figures_after_move(api):
    client, db, _ = api
    a = _agent(db, "A")
    north = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id]}).json()
    south = client.post(TEAMS_BASE, json={"name": "ZZT South", "sales_agent_ids": []}).json()
    created = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": north["id"], "name": "ZZT North Target", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 500}],
    }).json()
    client.put(f"{TEAMS_BASE}/{south['id']}/members", json={"sales_agent_ids": [a.id], "moves_on": "2026-10-15"})

    child_id = created["children"][0]["target_id"]
    child = client.get(f"{BASE}/{child_id}").json()
    assert child["periods"][0]["target_value"] == 500  # the figure moved with nobody


# --------------------------------------------------------------------------------------- #
# person label widening (S1-29, R2)
# --------------------------------------------------------------------------------------- #


def test_person_label_agent(api):
    client, db, company_id = api
    sean1 = _agent(db, "SEAN1", "Sean I", person_label="Sean")
    sean3 = _agent(db, "SEAN3", "Sean III", person_label="Sean")
    lone = _agent(db, "LONE", "Lone Wolf")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    _so_line(db, company_id, agent_id=sean1.id, order_date=date(2026, 10, 5), line_total=Decimal("100"), product_id=product.id)
    _so_line(db, company_id, agent_id=sean3.id, order_date=date(2026, 10, 6), line_total=Decimal("200"), product_id=product.id)
    _so_line(db, company_id, agent_id=lone.id, order_date=date(2026, 10, 7), line_total=Decimal("50"), product_id=product.id)

    target_sean1 = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": sean1.id, "name": "ZZT Sean", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    target_lone = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": lone.id, "name": "ZZT Lone", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()

    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    assert next(r for r in rows if r["target_id"] == target_sean1["id"])["achieved_value"] == 300  # SEAN I + SEAN III
    assert next(r for r in rows if r["target_id"] == target_lone["id"])["achieved_value"] == 50    # no label: own code only


def test_person_label_team_window(api):
    """A team with SEAN I as a member counts SEAN III's orders only inside Sean's membership
    window in that team."""
    client, db, company_id = api
    sean1 = _agent(db, "SEAN1", "Sean I", person_label="Sean")
    sean3 = _agent(db, "SEAN3", "Sean III", person_label="Sean")
    team = client.post(TEAMS_BASE, json={"name": "ZZT Sean Team", "sales_agent_ids": [sean1.id]}).json()
    client.put(f"{TEAMS_BASE}/{team['id']}/members", json={"sales_agent_ids": [], "moves_on": "2026-10-15"})

    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    _so_line(db, company_id, agent_id=sean3.id, order_date=date(2026, 10, 10), line_total=Decimal("100"), product_id=product.id)
    _so_line(db, company_id, agent_id=sean3.id, order_date=date(2026, 10, 20), line_total=Decimal("999"), product_id=product.id)

    target = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-14",
        "agent_figures": [{"sales_agent_id": sean1.id, "target_value": 0}],
    }).json()
    rows = client.get(BASE, params={"on": "2026-10-10", "subject": "team", "sales_team_id": team["id"]}).json()["rows"]
    achieved = next(r for r in rows if r["target_id"] == target["id"])["achieved_value"]
    assert achieved == 100  # only the order inside Sean I's membership window in this team


# --------------------------------------------------------------------------------------- #
# S6-4 to S6-7, S6-10, S6-14: team target validation, golden equality, move golden, listing
# --------------------------------------------------------------------------------------- #


def test_team_create_rejects_target_value_inactive_bad_subject(api):
    client, db, _ = api
    a = _agent(db, "A")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id]}).json()
    client.patch(f"{TEAMS_BASE}/{team['id']}", json={"is_active": False})

    res = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 100}],
    })
    assert res.status_code == 422, res.text  # inactive team

    active = client.post(TEAMS_BASE, json={"name": "ZZT Active", "sales_agent_ids": [a.id]}).json()
    res2 = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": active["id"], "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 999,
    })
    assert res2.status_code == 422, res2.text  # a team-level figure is rejected

    res3 = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": active["id"], "sales_agent_id": a.id, "name": "ZZT",
        "metric": "amount", "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01",
        "end_date": "2026-10-31", "agent_figures": [{"sales_agent_id": a.id, "target_value": 100}],
    })
    assert res3.status_code == 422, res3.text  # both team and agent set


def test_team_golden_and_equality(api):
    client, db, company_id = api
    a = _agent(db, "A")
    b = _agent(db, "B")
    c = _agent(db, "C")  # not a member of the team
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id, b.id]}).json()
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    for agent, total in ((a, 100), (b, 200), (c, 9999)):
        _so_line(db, company_id, agent_id=agent.id, order_date=date(2026, 10, 5), line_total=Decimal(str(total)),
                  product_id=product.id)

    team_target = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 0}, {"sales_agent_id": b.id, "target_value": 0}],
    }).json()
    team_rows = client.get(BASE, params={"on": "2026-10-15", "subject": "team", "sales_team_id": team["id"]}).json()["rows"]
    team_achieved = next(r for r in team_rows if r["target_id"] == team_target["id"])["achieved_value"]
    assert team_achieved == 300  # A + B, never C

    a_only = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": a.id, "name": "ZZT A alone", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    b_only = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": b.id, "name": "ZZT B alone", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    a_alone = next(r for r in rows if r["target_id"] == a_only["id"])["achieved_value"]
    b_alone = next(r for r in rows if r["target_id"] == b_only["id"])["achieved_value"]
    assert team_achieved == a_alone + b_alone


def test_move_on_15_oct_golden(api):
    """Today pinned 20 Oct 2026: A moves N -> S on 15 Oct; A's 10 Oct order counts for N, not
    S; 15 Oct and later count for S, not N (S6-6, S6-14)."""
    client, db, company_id = api
    a = _agent(db, "A")
    north = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id]}).json()
    south = client.post(TEAMS_BASE, json={"name": "ZZT South", "sales_agent_ids": []}).json()

    north_target = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": north["id"], "name": "ZZT North Target", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 0}],
    }).json()
    client.put(f"{TEAMS_BASE}/{south['id']}/members", json={"sales_agent_ids": [a.id], "moves_on": "2026-10-15"})
    south_target = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": south["id"], "name": "ZZT South Target", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 0}],
    }).json()

    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 10), line_total=Decimal("100"), product_id=product.id)
    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 15), line_total=Decimal("200"), product_id=product.id)

    north_rows = client.get(BASE, params={"on": "2026-10-20", "subject": "team", "sales_team_id": north["id"]}).json()["rows"]
    south_rows = client.get(BASE, params={"on": "2026-10-20", "subject": "team", "sales_team_id": south["id"]}).json()["rows"]
    assert next(r for r in north_rows if r["target_id"] == north_target["id"])["achieved_value"] == 100
    assert next(r for r in south_rows if r["target_id"] == south_target["id"])["achieved_value"] == 200


def test_list_team_rows_active_only(api):
    client, db, _ = api
    a = _agent(db, "A")
    active_team = client.post(TEAMS_BASE, json={"name": "ZZT Active", "sales_agent_ids": [a.id]}).json()
    inactive_team = client.post(TEAMS_BASE, json={"name": "ZZT Inactive", "sales_agent_ids": []}).json()
    client.patch(f"{TEAMS_BASE}/{inactive_team['id']}", json={"is_active": False})

    rows = client.get(BASE, params={"on": "2026-10-20", "subject": "team"}).json()["rows"]
    by_team = {r["sales_team_id"]: r for r in rows}
    assert by_team[active_team["id"]]["target_id"] is None  # active, no target: "No target" row
    assert inactive_team["id"] not in by_team               # inactive: no row at all


def test_list_agents_by_team_and_no_team(api):
    client, db, _ = api
    a = _agent(db, "A")
    b = _agent(db, "B")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id]}).json()

    filtered = client.get(BASE, params={"on": "2026-10-20", "subject": "agent", "sales_team_id": team["id"]}).json()["rows"]
    assert {r["sales_agent_id"] for r in filtered} == {a.id}

    no_team = client.get(BASE, params={"on": "2026-10-20", "subject": "agent", "sales_team_id": "none"}).json()["rows"]
    assert {r["sales_agent_id"] for r in no_team} == {b.id}

    res = client.get(BASE, params={"on": "2026-10-20", "subject": "team", "sales_team_id": "none"})
    assert res.status_code == 422, res.text  # "none" is agent-only


def test_leaving_agent_row(api):
    """16.3, "Leaving agent": the row is the agent's OWN first target, not the team's sum, and
    carries `left_on`."""
    client, db, company_id = api
    a = _agent(db, "A")
    b = _agent(db, "B")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id, b.id]}).json()
    other_team = client.post(TEAMS_BASE, json={"name": "ZZT South", "sales_agent_ids": []}).json()
    client.put(f"{TEAMS_BASE}/{other_team['id']}/members", json={"sales_agent_ids": [a.id], "moves_on": "2026-10-15"})

    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 5), line_total=Decimal("500"), product_id=product.id)
    _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 20), line_total=Decimal("700"), product_id=product.id)

    a_own = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": a.id, "name": "ZZT A own", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()

    rows = client.get(BASE, params={"on": "2026-10-20", "subject": "agent", "sales_team_id": team["id"]}).json()["rows"]
    a_row = next(r for r in rows if r["sales_agent_id"] == a.id)
    assert a_row["left_on"] == "2026-10-14"
    assert a_row["target_id"] == a_own["id"]
    assert a_row["achieved_value"] == 1200  # both orders count: the agent's OWN target


def test_shared_label_counts_once_for_team(api):
    client, db, company_id = api
    sean1 = _agent(db, "SEAN1", "Sean I", person_label="Sean")
    sean3 = _agent(db, "SEAN3", "Sean III", person_label="Sean")
    team = client.post(TEAMS_BASE, json={"name": "ZZT Sean Team", "sales_agent_ids": [sean1.id]}).json()
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    _so_line(db, company_id, agent_id=sean1.id, order_date=date(2026, 10, 5), line_total=Decimal("100"), product_id=product.id)
    _so_line(db, company_id, agent_id=sean3.id, order_date=date(2026, 10, 6), line_total=Decimal("200"), product_id=product.id)

    team_target = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": sean1.id, "target_value": 0}],
    }).json()
    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "team", "sales_team_id": team["id"]}).json()["rows"]
    achieved = next(r for r in rows if r["target_id"] == team_target["id"])["achieved_value"]
    # Widened by the shared label, but counted once: EXISTS, never a join per member.
    assert achieved == 300


def test_teams_list_targets_now(api):
    client, db, _ = api
    a = _agent(db, "A")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [a.id]}).json()
    empty_team = client.post(TEAMS_BASE, json={"name": "ZZT Empty", "sales_agent_ids": []}).json()

    client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT Team Target", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": a.id, "target_value": 0}],
    })

    listed = {t["id"]: t for t in client.get(TEAMS_BASE).json()["data"]}
    assert listed[team["id"]]["targets_now"] == 1
    assert listed[empty_team["id"]]["targets_now"] == 0


def test_options_gated_view(api):
    client, db, company_id = api
    active = _agent(db, "A", active=True)
    inactive = _agent(db, "OLD", active=False)
    category = _category(db, company_id)

    res = client.get(f"{BASE}/options")
    assert res.status_code == 200, res.text
    body = res.json()
    agent_ids = {o["id"] for o in body["agents"]}
    assert active.id in agent_ids
    assert inactive.id not in agent_ids
    assert any(c["id"] == category.id for c in body["categories"])
    assert "products" not in body  # products come from master-data's own select, not here

    view_only, originals = _client(db, ["sales.targets.view"])
    try:
        assert view_only.get(f"{BASE}/options").status_code == 200
    finally:
        _restore(originals)
    denied, originals2 = _client(db, [])
    try:
        assert denied.get(f"{BASE}/options").status_code == 403
    finally:
        _restore(originals2)


def test_detail_shape_and_counts_label(api):
    client, db, company_id = api
    a = _agent(db, "A")
    ordered = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": a.id, "name": "ZZT Ordered", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    assert ordered["counts_label"] == "Ordered"

    delivered = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": a.id, "name": "ZZT Delivered", "metric": "amount",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()
    assert delivered["counts_label"] == "Delivered"

    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    warehouse = _warehouse(db, company_id)
    _, line = _so_line(db, company_id, agent_id=a.id, order_date=date(2026, 10, 5), line_total=Decimal("100"), product_id=product.id)
    _do_line(db, company_id, product.id, warehouse.id, line.id, quantity=1, order_date=date(2026, 10, 6))

    refreshed = client.get(f"{BASE}/{delivered['id']}").json()
    assert refreshed["counts_label"] == "Delivered (by DO date)"


# --------------------------------------------------------------------------------------- #
# Review round 2: null in PATCH, non-UUID ids, cross-company and soft-deleted DO, company
# scope hardening, performance guard.
# --------------------------------------------------------------------------------------- #


def _no_leaked_sql(body_text: str) -> None:
    lowered = body_text.lower()
    for needle in ("psycopg", "sql", "insert", "update"):
        assert needle not in lowered, f"{needle!r} leaked into the error body: {body_text}"


@pytest.mark.parametrize(
    "field", ["metric", "basis", "product_scope", "start_date", "end_date"],
)
def test_patch_null_field_is_422_not_500(api, field):
    """A PATCH sending an explicit JSON null for a required header field must be refused as a
    validation error, never let the null reach a NOT NULL column and surface as a raw DB 500
    with the statement and parameters in the body."""
    client, db, _ = api
    agent = _agent(db, "A")
    target = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 100,
    }).json()
    res = client.patch(f"{BASE}/{target['id']}", json={field: None})
    assert res.status_code == 422, res.text
    _no_leaked_sql(res.text)


@pytest.mark.parametrize(
    "payload_fn",
    [
        lambda agent, team: {
            "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
            "basis": "ordered", "product_scope": "categories", "category_ids": ["x"],
            "start_date": "2026-10-01", "end_date": "2026-10-31", "target_value": 1,
        },
        lambda agent, team: {
            "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
            "basis": "ordered", "product_scope": "products", "product_ids": ["x"],
            "start_date": "2026-10-01", "end_date": "2026-10-31", "target_value": 1,
        },
        lambda agent, team: {
            "subject_kind": "agent", "sales_agent_id": "x", "name": "ZZT", "metric": "amount",
            "basis": "ordered", "product_scope": "all",
            "start_date": "2026-10-01", "end_date": "2026-10-31", "target_value": 1,
        },
        lambda agent, team: {
            "subject_kind": "team", "sales_team_id": "x", "name": "ZZT", "metric": "amount",
            "basis": "ordered", "product_scope": "all",
            "start_date": "2026-10-01", "end_date": "2026-10-31",
            "agent_figures": [{"sales_agent_id": agent.id, "target_value": 1}],
        },
        lambda agent, team: {
            "subject_kind": "team", "sales_team_id": team["id"], "name": "ZZT", "metric": "amount",
            "basis": "ordered", "product_scope": "all",
            "start_date": "2026-10-01", "end_date": "2026-10-31",
            "agent_figures": [{"sales_agent_id": "x", "target_value": 1}],
        },
    ],
    ids=["category_ids", "product_ids", "sales_agent_id", "sales_team_id", "agent_figures_agent_id"],
)
def test_create_rejects_non_uuid_ids_as_422(api, payload_fn):
    client, db, _ = api
    agent = _agent(db, "A")
    team = client.post(TEAMS_BASE, json={"name": "ZZT North", "sales_agent_ids": [agent.id]}).json()
    res = client.post(BASE, json=payload_fn(agent, team))
    assert res.status_code == 422, res.text
    _no_leaked_sql(res.text)


def test_do_cross_company_link_counts_nothing(api):
    """A DO seeded under a DIFFERENT company (`orders.company_id` and `order_lines.company_id`
    = B) but linked (`sales_order_line_id`) to company A's sales order line must not count for
    company A's target: the residual behaves exactly as if no DO were linked at all (the case
    (a) figure in `test_do_golden_a_to_d`), and `counts_label` agrees - it never sees a linked
    DO line, because that row belongs to another company."""
    from app.models.company import Company
    from app.models.inventory import Warehouse
    from app.models.order import Order, OrderLine

    client, db, company_id = api
    agent = _agent(db, "X")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    _, line = _so_line(
        db, company_id, agent_id=agent.id, order_date=date(2026, 9, 20), line_total=Decimal("10000"),
        qty_ordered=100, qty_delivered=100, product_id=product.id,
    )

    other = Company(id=_uid(), name="ZZT Other Co", code=f"Z{_uid()[:6]}")
    db.add(other)
    db.flush()
    with company_scope(db, None):
        foreign_warehouse = Warehouse(
            id=_uid(), company_id=other.id, warehouse_code=_code(), is_active=True
        )
        db.add(foreign_warehouse)
        db.flush()
        foreign_order = Order(
            id=_uid(), company_id=other.id, order_number=_code(), order_date=date(2026, 10, 5),
            is_cancelled=False,
        )
        db.add(foreign_order)
        db.flush()
        db.add(OrderLine(
            id=_uid(), company_id=other.id, line_sequence=1, order_id=foreign_order.id,
            product_id=product.id, warehouse_id=foreign_warehouse.id, quantity=40,
            sales_order_line_id=line.id,
        ))
        db.flush()

    target = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT", "metric": "amount",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-09-01", "end_date": "2026-09-30",
        "target_value": 0,
    }).json()
    rows = client.get(BASE, params={"on": "2026-09-25", "subject": "agent"}).json()["rows"]
    row = next(r for r in rows if r["target_id"] == target["id"])
    assert row["achieved_value"] == 10000  # as if no DO were linked at all
    assert client.get(f"{BASE}/{target['id']}").json()["counts_label"] == "Delivered"


def test_do_soft_deleted_counts_nothing_like_cancelled(api):
    """A linked DO with `orders.deleted_at` set counts nothing, exactly like a cancelled one
    (the case-(c) numbers in `test_do_golden_a_to_d`)."""
    from datetime import datetime

    from app.models.order import Order

    client, db, company_id = api
    agent = _agent(db, "DEL")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    warehouse = _warehouse(db, company_id)
    _, line = _so_line(
        db, company_id, agent_id=agent.id, order_date=date(2026, 9, 20), line_total=Decimal("10000"),
        qty_ordered=100, qty_delivered=100, product_id=product.id,
    )
    sep = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT Sep", "metric": "amount",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-09-01", "end_date": "2026-09-30",
        "target_value": 0,
    }).json()
    nov = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT Nov", "metric": "amount",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-11-01", "end_date": "2026-11-30",
        "target_value": 0,
    }).json()
    _do_line(db, company_id, product.id, warehouse.id, line.id, quantity=40, order_date=date(2026, 10, 5))
    do_nov_order, _do_nov_line = _do_line(
        db, company_id, product.id, warehouse.id, line.id, quantity=30, order_date=date(2026, 11, 3)
    )

    def achieved(target_id, on):
        rows = client.get(BASE, params={"on": on, "subject": "agent"}).json()["rows"]
        return next(r for r in rows if r["target_id"] == target_id)["achieved_value"]

    assert achieved(nov["id"], "2026-11-15") == 3000
    assert achieved(sep["id"], "2026-09-25") == 3000

    db.query(Order).filter(Order.id == do_nov_order).update({"deleted_at": datetime(2026, 11, 4)})
    db.flush()
    assert achieved(nov["id"], "2026-11-15") == 0     # soft-deleted: counts nothing, like cancelled
    assert achieved(sep["id"], "2026-09-25") == 6000  # the residual absorbs it back, like case (c)


def test_company_scope_hardened(api):
    """Security nit 5: the foreign target carries a period (not a bare header), a foreign
    agent/team/category/product named in a CREATE payload is refused, and a PATCH cannot reach
    a foreign target by pairing a foreign target id with the caller's OWN period id."""
    from app.models.company import Company
    from app.models.inventory import Warehouse
    from app.models.product import Product, ProductCategory
    from app.models.sales import SalesTarget, SalesTargetPeriod
    from app.models.sales_agent import SalesAgent

    client, db, company_id = api
    other = Company(id=_uid(), name="ZZT Other Co", code=f"Z{_uid()[:6]}")
    db.add(other)
    db.flush()
    with company_scope(db, None):
        foreign_agent = SalesAgent(id=_uid(), sales_agent=_code(), company_id=other.id)
        db.add(foreign_agent)
        db.flush()
        foreign_category = ProductCategory(
            id=_uid(), company_id=other.id, category_code=_code(), category_name="ZZT Foreign Cat",
        )
        db.add(foreign_category)
        db.flush()
        foreign_uom = _uom(db)
        foreign_product = Product(
            id=_uid(), company_id=other.id, product_code=_code(), product_name="ZZT Foreign Product",
            category_id=foreign_category.id, base_uom_id=foreign_uom.id, list_price=Decimal("0"),
        )
        db.add(foreign_product)
        db.flush()

        foreign_target = SalesTarget(
            id=_uid(), company_id=other.id, target_no="TGT-999999", name="ZZT Foreign",
            subject_kind="agent", sales_agent_id=foreign_agent.id, metric="amount", basis="ordered",
            product_scope="all", start_date=date(2026, 10, 1), end_date=date(2026, 10, 31),
        )
        db.add(foreign_target)
        db.flush()
        foreign_period = SalesTargetPeriod(
            id=_uid(), company_id=other.id, target_id=foreign_target.id,
            period_start=date(2026, 10, 1), period_end=date(2026, 10, 31), target_value=Decimal("100"),
        )
        db.add(foreign_period)
        db.flush()
        from app.models.sales import SalesTeam

        foreign_team = SalesTeam(id=_uid(), company_id=other.id, name="ZZT Foreign Team")
        db.add(foreign_team)
        db.flush()

    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    assert foreign_target.id not in [r["target_id"] for r in rows]
    assert client.get(f"{BASE}/{foreign_target.id}").status_code == 404
    assert client.delete(f"{BASE}/{foreign_target.id}").status_code == 404

    own_agent = _agent(db, "OWN")
    # A foreign agent named in a create payload.
    res = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": foreign_agent.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 1,
    })
    assert res.status_code in (404, 422), res.text

    # A foreign team named in a create payload.
    res = client.post(BASE, json={
        "subject_kind": "team", "sales_team_id": foreign_team.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "agent_figures": [{"sales_agent_id": own_agent.id, "target_value": 1}],
    })
    assert res.status_code in (404, 422), res.text

    # A foreign category named in a create payload.
    res = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": own_agent.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "categories", "category_ids": [foreign_category.id],
        "start_date": "2026-10-01", "end_date": "2026-10-31", "target_value": 1,
    })
    assert res.status_code in (404, 422), res.text

    # A foreign product named in a create payload.
    res = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": own_agent.id, "name": "ZZT", "metric": "amount",
        "basis": "ordered", "product_scope": "products", "product_ids": [foreign_product.id],
        "start_date": "2026-10-01", "end_date": "2026-10-31", "target_value": 1,
    })
    assert res.status_code in (404, 422), res.text

    # A PATCH on the foreign target's period, using MY OWN target's period id: still 404,
    # because the target lookup itself is company-scoped before the period is ever consulted.
    mine = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": own_agent.id, "name": "ZZT Mine", "metric": "amount",
        "basis": "ordered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 1,
    }).json()
    my_period_id = mine["periods"][0]["id"]
    res = client.patch(f"{BASE}/{foreign_target.id}/periods/{my_period_id}", json={"target_value": 5})
    assert res.status_code == 404, res.text


def test_detail_query_stays_fast_at_scale(api):
    """Regression guard for quadratic growth in the delivered-by-DO-date achievement query
    (reviewer measured 0.78s at 500 lines on the old query, on this machine's DB). 12 monthly
    periods, 300 SO lines and 2 linked, non-residual DO lines each: GET /sales/targets/{id}
    must return well inside a generous budget, not scale like O(n^2) with the line count.

    The request is timed twice and the faster run is asserted, against 3.0 s. It is a stopwatch
    on a shared CI runner: under the xdist shard the 1.5 s budget failed on main twice at 1.60 s
    and passed once, with no product change between. Locally, under `-n auto --dist loadfile`
    over tests/test_sales_*.py, the two runs took 0.72 and 0.72 s, 0.74 and 0.92 s, 0.79 and
    0.83 s. The faster of two drops a one-off stall on a neighbouring worker, and 3.0 s leaves
    CI's 1.60 s about 2x margin. The guard still holds: the quadratic query measured 0.78 s at
    500 lines, so it would take tens of seconds here. It stays in the xdist pool rather than
    the serial step, which is kept for migration and DDL tests (`serial_ddl`)."""
    import time

    client, db, company_id = api
    agent = _agent(db, "PERF")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    warehouse = _warehouse(db, company_id)

    # 12 months x 300 SO lines, each delivered by two DOs (6 + 4: no residual). Seeded in
    # set-based SQL, the shape `_so_line` / `_do_line` write row by row: 18,000 rows through
    # the ORM took about a minute of this test's 74 s (CI-LEAN, 10 Oct 2026).
    db.flush()
    params = {"c": company_id, "a": agent.id, "p": product.id, "w": warehouse.id}
    db.execute(text(
        "insert into sales_orders (id, company_id, so_number, order_date, status, sales_agent_id) "
        "select gen_random_uuid(), :c, 'ZZTF' || m || '-' || g || '-' || substr(md5(random()::text), 1, 6), "
        "make_date(2026, m, 5), 'open', :a "
        "from generate_series(1, 12) m cross join generate_series(1, 300) g"
    ), params)
    db.execute(text(
        "insert into sales_order_lines (id, company_id, sales_order_id, product_id, qty_ordered, "
        "qty_delivered, line_total, line_status, purchasing_status) "
        "select gen_random_uuid(), :c, so.id, :p, 10, 10, 100, 'open', 'not_reviewed' "
        "from sales_orders so where so.company_id = :c and so.so_number like 'ZZTF%'"
    ), params)
    db.execute(text(
        "create temporary table zztf_do on commit drop as "
        "select row_number() over () as n, sol.id as sol_id, so.order_date as d, q.qty "
        "from sales_order_lines sol join sales_orders so on so.id = sol.sales_order_id "
        "cross join (values (6), (4)) as q(qty) "
        "where so.company_id = :c and so.so_number like 'ZZTF%'"
    ), params)
    db.execute(text(
        "insert into orders (id, company_id, order_number, order_date, is_cancelled, kpi_warning, "
        "subtotal_amount, discount_amount, tax_amount, total_amount, synced_to_excel) "
        "select md5('zztf' || n)::uuid, :c, 'ZZTF' || n || '-' || substr(md5(random()::text), 1, 6), "
        "d, false, false, 0, 0, 0, 0, false from zztf_do"
    ), params)
    db.execute(text(
        "insert into order_lines (id, company_id, line_sequence, order_id, product_id, warehouse_id, "
        "quantity, sales_order_line_id) "
        "select gen_random_uuid(), :c, 1, md5('zztf' || n)::uuid, :p, :w, qty, sol_id from zztf_do"
    ), params)
    seeded = db.execute(text(
        "select (select count(*) from sales_order_lines sol join sales_orders so on so.id = sol.sales_order_id "
        "where so.so_number like 'ZZTF%'), (select count(*) from zztf_do)"
    )).one()
    assert tuple(seeded) == (3600, 7200), seeded
    # Plan against the rows just seeded, not against whatever an earlier test on this xdist
    # worker left in the shared scratch schema's pg_class. Autovacuum on a worker whose earlier
    # files rolled back a few hundred rows records reltuples = 0 over non-empty pages, and the
    # planner then reads these 3,600 SO lines as about one row: a join filter removing 12,956,400
    # rows, 7 to 18 s against about 1 s on the same seed. ANALYZE runs inside this test's
    # transaction (it counts the transaction's own uncommitted rows), so the outer rollback
    # discards the statistics with the rows.
    db.execute(text("ANALYZE sales_orders, sales_order_lines, orders, order_lines, products"))

    target = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT Perf", "metric": "amount",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-01-01", "end_date": "2026-12-31",
        "split_every": 1, "split_unit": "month", "target_value": 0,
    }).json()

    from sqlalchemy import event

    statements = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "do_counted AS" in statement:
            statements.append((statement, parameters))

    engine = db.get_bind().engine
    event.listen(engine, "before_cursor_execute", _capture)
    runs = []
    try:
        for _ in range(2):
            started = time.monotonic()
            res = client.get(f"{BASE}/{target['id']}")
            runs.append(time.monotonic() - started)
            assert res.status_code == 200, res.text
    finally:
        event.remove(engine, "before_cursor_execute", _capture)
    took = ", ".join(f"{r:.2f}s" for r in runs)
    assert min(runs) < 3.0, f"detail took {took} for 3,600 SO lines / 7,200 DO lines"

    # The stopwatch above is noisy on a shared runner; the plan is not. No step may meet the
    # agent's sales order lines by a join FILTER (a nested loop comparing every pair): with
    # #1354 S2's wider `order_lines`, a statistics-less planner joined the delivered-in-span DO
    # lines to the agent's lines that way, "Rows Removed by Join Filter: 25912800", 1.8 s here
    # and 3.3 to 3.75 s on CI against about 0.8 s.
    assert statements, "the achievement statement was not captured"
    statement, parameters = statements[0]
    plan = "\n".join(
        row[0] for row in db.connection().exec_driver_sql("EXPLAIN " + statement, parameters)
    )
    quadratic = [ln.strip() for ln in plan.splitlines() if "Join Filter" in ln and "sales_order_lines" in ln]
    assert not quadratic, f"achievement plan filters a join on sales_order_lines: {quadratic}"


# --------------------------------------------------------------------------------------- #
# Final review: three kill-test gaps (K2, K4, K6).
# --------------------------------------------------------------------------------------- #


def test_cancelled_do_does_not_use_up_the_cap(api):
    """K2: a CANCELLED linked DO of 100 dated before a live linked DO of 40 must not count
    against the running-sum cap (`prior`) the live DO is capped by. October counts the live
    DO's 40 units (RM 4,000); the residual 60 units (RM 6,000) lands on the SO's own
    September order-date period - the cancelled DO neither counts itself nor blocks the live
    one from counting."""
    client, db, company_id = api
    agent = _agent(db, "K2")
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    warehouse = _warehouse(db, company_id)
    _, line = _so_line(
        db, company_id, agent_id=agent.id, order_date=date(2026, 9, 20), line_total=Decimal("10000"),
        qty_ordered=100, qty_delivered=100, product_id=product.id,
    )
    # The cancelled DO is dated FIRST, so an uncapped `prior` would see its 100 units as
    # already delivered and cap the live DO at 0.
    _do_line(
        db, company_id, product.id, warehouse.id, line.id, quantity=100, order_date=date(2026, 10, 1),
        is_cancelled=True,
    )
    _do_line(db, company_id, product.id, warehouse.id, line.id, quantity=40, order_date=date(2026, 10, 5))

    sep = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT Sep", "metric": "amount",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-09-01", "end_date": "2026-09-30",
        "target_value": 0,
    }).json()
    oct_ = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": agent.id, "name": "ZZT Oct", "metric": "amount",
        "basis": "delivered", "product_scope": "all", "start_date": "2026-10-01", "end_date": "2026-10-31",
        "target_value": 0,
    }).json()

    def achieved(target_id, on):
        rows = client.get(BASE, params={"on": on, "subject": "agent"}).json()["rows"]
        return next(r for r in rows if r["target_id"] == target_id)["achieved_value"]

    assert achieved(oct_["id"], "2026-10-15") == 4000  # the live DO's 40 units, uncapped by the cancelled one
    assert achieved(sep["id"], "2026-09-25") == 6000   # the residual: 100 - 40 delivered by DO


def test_unhandled_error_answers_a_generic_500_never_the_raw_text(api):
    """K4: a non-AppException error inside a targets route must never leak into the body -
    not the exception text, not SQL, not a database driver name."""
    from app.services.sales import target_service

    client, db, _ = api
    original = target_service.list_targets

    def _boom(*args, **kwargs):
        raise RuntimeError("secret SQL text")

    target_service.list_targets = _boom
    try:
        res = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"})
    finally:
        target_service.list_targets = original

    assert res.status_code == 500, res.text
    assert res.json()["message"] == "Something went wrong. Please try again."
    assert "secret" not in res.text


def test_shared_agent_target_excludes_other_companys_orders(api):
    """K6: a shared agent (`company_id` NULL) holds an agent target in company A and also
    appears on a sales order booked under company B, dated inside the period; A's achieved
    must never include B's order."""
    from app.models.company import Company

    client, db, company_id = api
    shared_agent = _agent(db, "SHARED")  # company_id None: a shared master row
    category = _category(db, company_id)
    product = _product(db, company_id, category.id)
    _so_line(
        db, company_id, agent_id=shared_agent.id, order_date=date(2026, 10, 5),
        line_total=Decimal("100"), product_id=product.id,
    )

    other = Company(id=_uid(), name="ZZT Other Co", code=f"Z{_uid()[:6]}")
    db.add(other)
    db.flush()
    with company_scope(db, None):
        other_category = _category(db, other.id)
        other_product = _product(db, other.id, other_category.id)
        _so_line(
            db, other.id, agent_id=shared_agent.id, order_date=date(2026, 10, 6),
            line_total=Decimal("999"), product_id=other_product.id,
        )

    target = client.post(BASE, json={
        "subject_kind": "agent", "sales_agent_id": shared_agent.id, "name": "ZZT Shared",
        "metric": "amount", "basis": "ordered", "product_scope": "all",
        "start_date": "2026-10-01", "end_date": "2026-10-31", "target_value": 0,
    }).json()
    rows = client.get(BASE, params={"on": "2026-10-15", "subject": "agent"}).json()["rows"]
    row = next(r for r in rows if r["target_id"] == target["id"])
    assert row["achieved_value"] == 100  # company A's own order only, never B's 999
