"""A3 - SO outstanding bucket, group_by, three-line summary
(AC-905, AC-905b, AC-906, AC-909).

`documentation/plans/chatbot/PLAN-chatbot-growth-r1.md` Slice A;
`documentation/plans/chatbot/chatbot-growth-r1-acceptance-criteria.md` section A.

Postgres only, blank schema, every row seeded here (CI's database has none).
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.base import set_company_scope
from app.models.order import SalesOrder, SalesOrderLine
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.order_service import (
    ORDER_GROUP_BY_AXES,
    group_rows,
    so_outstanding_rows,
    so_outstanding_summary,
)

from tests._mc_lookup_seed import customer, order, order_line, product, warehouse
from tests._orders_view_seed import orders_view_principal
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/order-management/orders"


@pytest.fixture
def db():
    with blank_session() as s:
        set_company_scope(s, frozenset({DEFAULT_COMPANY_ID}))
        yield s


def _so_line(db, *, customer_id, product_id, ordered, delivered, status="open", order_date=None, requested=None, so_number=None):
    so = SalesOrder(
        id=str(uuid.uuid4()),
        so_number=so_number or unique_code("SO"),
        customer_id=customer_id,
        order_date=order_date,
        requested_delivery_date=requested,
        company_id=DEFAULT_COMPANY_ID,
    )
    db.add(so)
    db.flush()
    db.add(
        SalesOrderLine(
            id=str(uuid.uuid4()),
            sales_order_id=so.id,
            product_id=product_id,
            qty_ordered=ordered,
            qty_delivered=delivered,
            line_status=status,
            company_id=DEFAULT_COMPANY_ID,
        )
    )
    return so


# --------------------------------------------------------------------- service


def test_so_outstanding_rows_only_open_positive_delta(db):
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ABC SDN BHD")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="SRTWC8517")
    _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=10, delivered=3,
             order_date=date(2026, 6, 1), requested=date(2026, 6, 15))
    _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=5, delivered=5)  # fully delivered
    _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=8, delivered=0, status="closed")
    db.commit()

    rows = so_outstanding_rows(db, customer_ids=[cust.id])
    assert len(rows) == 1
    row = rows[0]
    assert row["outstanding_qty"] == 7
    assert row["product_code"] == "SRTWC8517"
    assert row["customer"] == "ABC SDN BHD"
    assert row["order_date"] == "2026-06-01"
    assert row["requested_delivery_date"] == "2026-06-15"


def test_so_outstanding_summary_scoped_by_customer_and_product(db):
    cust1 = customer(db, company_id=DEFAULT_COMPANY_ID, name="ABC")
    cust2 = customer(db, company_id=DEFAULT_COMPANY_ID, name="XYZ")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="SRTWC8517")
    other_prod = product(db, company_id=DEFAULT_COMPANY_ID, code="OTHER")
    _so_line(db, customer_id=cust1.id, product_id=prod.id, ordered=10, delivered=3)  # +7
    _so_line(db, customer_id=cust2.id, product_id=prod.id, ordered=4, delivered=0)  # +4, other customer
    _so_line(db, customer_id=cust1.id, product_id=other_prod.id, ordered=6, delivered=0)  # +6, other product
    db.commit()

    summary = so_outstanding_summary(db, customer_ids=[cust1.id], product_ids=[prod.id])
    assert summary == {"so_outstanding_qty": 7, "so_outstanding_count": 1}


def test_group_rows_by_product_and_date():
    rows = [
        {"product_code": "A", "order_date": "2026-06-01"},
        {"product_code": "A", "order_date": "2026-06-02"},
        {"product_code": "B", "order_date": "2026-06-01"},
    ]
    by_product = group_rows(rows, group_by="product")
    assert [g["key"] for g in by_product] == ["A", "B"]
    assert len(by_product[0]["rows"]) == 2

    by_date = group_rows(rows, group_by="date")
    assert {g["key"] for g in by_date} == {"2026-06-01", "2026-06-02"}


def test_group_rows_missing_axis_value_buckets_as_not_specified():
    rows = [{"customer": None}, {"customer": "ABC"}]
    groups = group_rows(rows, group_by="customer")
    keys = {g["key"] for g in groups}
    assert keys == {"Not specified", "ABC"}


# --------------------------------------------------------------------- route


@pytest.fixture
def client(db, monkeypatch):
    def _override_db():
        yield db

    # A real user holding the grant: the DO read routes are gated on it (#1669).
    principal = orders_view_principal(db)
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


def test_route_so_outstanding_bucket_lists_open_lines(client, db):
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ABC SDN BHD")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="SRTWC8517")
    _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=10, delivered=3)
    db.commit()

    resp = client.get(BASE, params={"order_status": "so_outstanding", "customer_ids": cust.id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pagination"]["total"] == 1
    assert body["data"][0]["outstanding_qty"] == 7
    assert body["data"][0]["so_number"]


def test_route_so_outstanding_group_by_product(client, db):
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ABC")
    p1 = product(db, company_id=DEFAULT_COMPANY_ID, code="P1")
    p2 = product(db, company_id=DEFAULT_COMPANY_ID, code="P2")
    _so_line(db, customer_id=cust.id, product_id=p1.id, ordered=5, delivered=0)
    _so_line(db, customer_id=cust.id, product_id=p2.id, ordered=3, delivered=0)
    db.commit()

    resp = client.get(
        BASE,
        params={"order_status": "so_outstanding", "customer_ids": cust.id, "group_by": "product"},
    )
    assert resp.status_code == 200, resp.text
    groups = resp.json()["groups"]
    assert {g["key"] for g in groups} == {"P1", "P2"}


def test_route_group_by_unknown_axis_422(client, db):
    resp = client.get(BASE, params={"group_by": "not-a-real-axis"})
    assert resp.status_code == 422
    body = resp.json()
    for axis in ORDER_GROUP_BY_AXES:
        assert axis in body["detail"]


def test_route_do_bucket_group_by_customer(client, db):
    cust1 = customer(db, company_id=DEFAULT_COMPANY_ID, name="ABC")
    cust2 = customer(db, company_id=DEFAULT_COMPANY_ID, name="XYZ")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="P1")
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    o1 = order(db, company_id=DEFAULT_COMPANY_ID, customer_id=cust1.id)
    o1.debtor_name = "ABC"
    o2 = order(db, company_id=DEFAULT_COMPANY_ID, customer_id=cust2.id)
    o2.debtor_name = "XYZ"
    order_line(db, company_id=DEFAULT_COMPANY_ID, order_id=o1.id, product_id=prod.id, warehouse_id=wh.id)
    order_line(db, company_id=DEFAULT_COMPANY_ID, order_id=o2.id, product_id=prod.id, warehouse_id=wh.id)
    db.commit()

    resp = client.get(BASE, params={"group_by": "customer"})
    assert resp.status_code == 200, resp.text
    groups = resp.json()["groups"]
    assert {g["key"] for g in groups} == {"ABC", "XYZ"}


def test_route_include_summary_and_include_pipeline_adds_so_outstanding_pipeline_leg(client, db):
    """AC-905b: `include_summary=true` PLUS `include_pipeline=true` on the plain DO
    bucket carries the SO-outstanding leg of the three-line pipeline, scoped by the
    same filters. `include_pipeline` is opt-in and separate from `include_summary`
    (fix, 7 Sep 2026, see the next test) - the CRM's chatbot lane sends both together
    on a quantity ask; nothing else in this codebase sends `include_pipeline`."""
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ABC")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="P1")
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    o1 = order(db, company_id=DEFAULT_COMPANY_ID, customer_id=cust.id)
    o1.debtor_name = "ABC"
    order_line(db, company_id=DEFAULT_COMPANY_ID, order_id=o1.id, product_id=prod.id, warehouse_id=wh.id)
    _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=9, delivered=2)  # +7 outstanding
    db.commit()

    resp = client.get(
        BASE,
        params={
            "customer_ids": cust.id,
            "product_ids": prod.id,
            "include_summary": "true",
            "include_pipeline": "true",
        },
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()["summary"]
    assert summary["so_outstanding_qty"] == 7
    assert summary["so_outstanding_count"] == 1


def test_route_include_summary_alone_never_adds_the_pipeline_leg(client, db):
    """Fix, 7 Sep 2026: `include_summary=true` WITHOUT `include_pipeline=true` must
    stay exactly the pre-A3 shape - this is what an old caller (n8n's own
    quantity-ask workflow, which already sends `include_summary=true` and has never
    heard of `include_pipeline`) gets today, and it must not gain a new field it
    never asked for. `so_outstanding_qty`'s presence alone is what
    `sorento_crm_mcp/presenters.py::_pipeline_summary_items` renders on, so leaking
    it here would show every DO caller a pipeline it did not ask for."""
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ABC")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="P1")
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    o1 = order(db, company_id=DEFAULT_COMPANY_ID, customer_id=cust.id)
    o1.debtor_name = "ABC"
    order_line(db, company_id=DEFAULT_COMPANY_ID, order_id=o1.id, product_id=prod.id, warehouse_id=wh.id)
    _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=9, delivered=2)
    db.commit()

    resp = client.get(
        BASE,
        params={"customer_ids": cust.id, "product_ids": prod.id, "include_summary": "true"},
    )
    assert resp.status_code == 200, resp.text
    summary = resp.json()["summary"]
    assert "so_outstanding_qty" not in summary
    assert "so_outstanding_count" not in summary


def test_route_list_do_without_include_summary_has_no_pipeline(client, db):
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ABC")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="P1")
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    o1 = order(db, company_id=DEFAULT_COMPANY_ID, customer_id=cust.id)
    o1.estimated_delivery_date = date(2026, 9, 12)
    order_line(db, company_id=DEFAULT_COMPANY_ID, order_id=o1.id, product_id=prod.id, warehouse_id=wh.id)
    db.commit()

    resp = client.get(BASE, params={"customer_ids": cust.id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "summary" not in body or body.get("summary") is None
    assert "groups" not in body or body.get("groups") is None
    # AC4 (chatbot-eta-not-in-turn): the CRM orders API itself still returns the
    # date - only MCP and the chatbot turn output drop it (entity_resolver.py,
    # answer.py). The UI reads this field unchanged.
    assert body["data"][0]["estimated_delivery_date"] is not None


# ----------------------------- should-fix 7: the external cap applies to this bucket too


def test_route_so_outstanding_takes_the_external_cap(client, db, monkeypatch):
    """Every DO bucket hard-caps an external/AI caller at 20 rows; the new bucket returned
    up to 500. That cap is what stops one WhatsApp turn pulling a five-hundred-row page
    through the MCP and into a message, and a new bucket is exactly where it is forgotten.
    """
    from app.api.v1.order_management import orders as orders_mod

    monkeypatch.setattr(orders_mod.app_settings, "external_api_key", "zzt-console-key")
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Cap Customer")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="SRTWC8517-CAP")
    for _ in range(25):
        _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=5, delivered=0)
    db.commit()

    params = {"order_status": "so_outstanding", "customer_ids": cust.id, "limit": 500}
    external = client.get(BASE, params=params, headers={"X-API-Key": "zzt-console-key"})
    assert external.status_code == 200, external.text
    assert len(external.json()["data"]) == 20, "the external cap did not reach this bucket"

    # A staff caller (no API key) is unchanged: the cap is about the external surface.
    staff = client.get(BASE, params=params)
    assert staff.status_code == 200, staff.text
    assert len(staff.json()["data"]) == 25


def test_route_so_outstanding_cap_lifts_for_a_date_scoped_read(client, db, monkeypatch):
    """The same relaxation the DO buckets get: a date-narrowed question wants the whole
    window, not a truncated top-20.

    What is graded here is the CAP LIFTING, not date filtering: `so_outstanding_rows` takes
    no date window of its own today (it reads `sales_order_lines`, which the DO date
    columns do not describe), so every seeded row comes back either way. The assertion is
    that 25 come back rather than 20 - i.e. `date_scoped` reached `_external_orders_limit`
    for this bucket exactly as it does for the others."""
    from datetime import date

    from app.api.v1.order_management import orders as orders_mod

    monkeypatch.setattr(orders_mod.app_settings, "external_api_key", "zzt-console-key")
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ZZT Window Customer")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="SRTWC8517-WINDOW")
    for _ in range(25):
        _so_line(
            db,
            customer_id=cust.id,
            product_id=prod.id,
            ordered=5,
            delivered=0,
            order_date=date(2026, 6, 10),
        )
    db.commit()

    resp = client.get(
        BASE,
        params={
            "order_status": "so_outstanding",
            "customer_ids": cust.id,
            "limit": 500,
            "order_date_from": "2026-06-01",
            "order_date_to": "2026-06-30",
        },
        headers={"X-API-Key": "zzt-console-key"},
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["data"]) == 25


def test_d8_the_list_route_stamps_the_so_block_on_its_rows(client, db):
    """D8 (owner console pass, 8 Sep 2026): the orders LIST route carried the SO figures at
    the top level only, so its per-row summary showed the DO block alone while the
    by-product route showed both. Now the total row and each customer row carry the five
    SO figures under include_pipeline."""
    from datetime import date as _d

    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ABC")
    other = customer(db, company_id=DEFAULT_COMPANY_ID, name="XYZ")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="P1")
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    for c in (cust, other):
        o = order(db, company_id=DEFAULT_COMPANY_ID, customer_id=c.id)
        o.debtor_name = c.customer_name
        order_line(db, company_id=DEFAULT_COMPANY_ID, order_id=o.id, product_id=prod.id, warehouse_id=wh.id)
    so = _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=9, delivered=2)  # +7
    so.order_date = _d(2026, 5, 1)
    db.commit()

    resp = client.get(BASE, params={"product_ids": prod.id, "include_summary": "true", "include_pipeline": "true"})
    assert resp.status_code == 200, resp.text
    summary = resp.json()["summary"]
    total = summary["products"][0]
    assert (total["so_count"], total["so_ordered_qty"], total["so_transferred_qty"], total["so_outstanding_qty"]) == (1, 9, 2, 7)
    assert total["so_date_from"] == "2026-05-01"
    by_cust = {g["customer"]: g for g in summary["groups"]}
    assert (by_cust["ABC"]["so_count"], by_cust["ABC"]["so_outstanding_qty"]) == (1, 7)
    assert (by_cust["XYZ"]["so_count"], by_cust["XYZ"]["so_outstanding_qty"]) == (0, 0)  # known, nothing: zeros
    assert summary["so_outstanding_qty"] == 7  # the top-level leg stays for its readers

    plain = client.get(BASE, params={"product_ids": prod.id, "include_summary": "true"}).json()["summary"]
    assert not any(k.startswith("so_") for row in plain["products"] + plain["groups"] for k in row)
