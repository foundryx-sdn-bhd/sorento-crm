"""The SO outstanding number reaches the BY-PRODUCT order answer, per row.

Measured on the owner's turn 98912914 ("how many did heng seng hardware take of
srtwc286"): the CRM lane picked `crm_order_management_orders_by_product_list` and sent
`include_summary=true` AND `include_pipeline=true`, but the answer carried per
customer x product DO rows and NO SO outstanding line. Three drops in a row: the
by-product route had no `include_pipeline` param (FastAPI ignores unknown query
params), `list_orders_by_product` only ever called `stamp_order_summary`, and the MCP
ToolSpec did not list the param either.

Owner ruling on the shape: `so_outstanding_qty` goes on EVERY entry of
`summary.products` (per product code) and EVERY entry of `summary.groups` (per
customer x product) - and NEVER at the top level of the by-product summary, because
the MCP presenter's `_pipeline_summary_items` keys on that top-level field and would
then print DO COUNTS as "DO open" / "Delivered" whenever more than one product
variant sits in the row (the owner's case has 3).

Same substrate as `tests/test_order_list_summary.py`: Postgres only, blank scratch
schema, every row seeded here (CI's database is empty; never LIMIT 1 off a table).
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
from app.models.order import OrderStatus, SalesOrder, SalesOrderLine
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.order_service import OrderService

from tests._mc_lookup_seed import (
    MOCHA_ID,
    customer,
    order,
    order_line,
    product,
    seed_mocha,
    warehouse,
)
from tests._orders_view_seed import orders_view_principal
from tests._pg_fixture import blank_session, unique_code

BASE = "/api/v1/order-management/orders/by-product"


@pytest.fixture
def db():
    with blank_session() as session:
        seed_mocha(session)
        set_company_scope(session, frozenset({DEFAULT_COMPANY_ID, MOCHA_ID}))
        for code, name, final in (
            ("NEW", "New Order", False),
            ("DELIVERED", "Picked Up / In Transit", True),
        ):
            session.add(OrderStatus(id=str(uuid.uuid4()), status_code=code, status_name=name,
                                    sequence=0, is_final_status=final))
        session.flush()
        yield session


def _status_id(db, code: str) -> str:
    return db.query(OrderStatus.id).filter(OrderStatus.status_code == code).scalar()


def _do(db, *, cust, prod, wh, qty, delivered_on: date | None = date(2026, 3, 2),
        company_id: str = DEFAULT_COMPANY_ID, debtor_name: str | None = None):
    """One DO with one line of `qty` for `prod`; delivered unless `delivered_on=None`."""
    o = order(db, company_id=company_id, customer_id=cust.id)
    o.debtor_name = debtor_name if debtor_name is not None else cust.customer_name
    o.order_status_id = _status_id(db, "DELIVERED" if delivered_on else "NEW")
    o.actual_delivery_date = delivered_on
    o.order_date = date(2026, 1, 1)
    order_line(db, company_id=company_id, order_id=o.id, product_id=prod.id,
               warehouse_id=wh.id, quantity=qty)
    db.flush()
    return o


def _so_line(db, *, customer_id, product_id, ordered, delivered=0, status="open",
             company_id: str = DEFAULT_COMPANY_ID):
    so = SalesOrder(id=str(uuid.uuid4()), so_number=unique_code("SO"), customer_id=customer_id,
                    company_id=company_id)
    db.add(so)
    db.flush()
    db.add(SalesOrderLine(id=str(uuid.uuid4()), sales_order_id=so.id, product_id=product_id,
                          qty_ordered=ordered, qty_delivered=delivered, line_status=status,
                          company_id=company_id))
    db.flush()
    return so


def _walk_keys(node, out: set[str]) -> set[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            out.add(k)
            _walk_keys(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_keys(v, out)
    return out


@pytest.fixture
def scenario(db):
    """Customer HENG SENG, product SRTWC286: one delivered DO of 10, one open SO
    line with 9 ordered / 2 delivered (+7 outstanding)."""
    cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="HENG SENG HARDWARE SDN BHD")
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code="SRTWC286")
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID)
    _do(db, cust=cust, prod=prod, wh=wh, qty=10)
    _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=9, delivered=2)
    db.commit()
    return cust, prod, wh


# ------------------------------------------------------------------ service

def test_so_outstanding_lands_on_every_product_and_group_row_never_top_level(db, scenario):
    cust, prod, _ = scenario
    r = OrderService(db).list_orders_by_product(
        product_ids=[prod.id], customer_ids=[cust.id], include_summary=True, include_pipeline=True
    )
    s = r["summary"]
    assert s["products"][0]["product_code"] == "SRTWC286"
    assert s["products"][0]["so_outstanding_qty"] == 7
    assert s["products"][0]["delivered_quantity"] == 10  # the DO measures are untouched
    assert s["groups"][0]["customer"] == "HENG SENG HARDWARE SDN BHD"
    assert s["groups"][0]["so_outstanding_qty"] == 7
    # per row ONLY - a top-level key would make the MCP presenter print DO counts as
    # quantities on a multi-variant row
    assert "so_outstanding_qty" not in s
    assert "so_outstanding_count" not in s
    assert isinstance(s["products"][0]["so_outstanding_qty"], int)


def test_without_include_pipeline_the_payload_is_byte_identical(db, scenario):
    """A caller that never asked (every pre-existing caller, n8n included) gets exactly
    today's payload - no new key anywhere in the tree."""
    cust, prod, _ = scenario
    svc = OrderService(db)
    plain = svc.list_orders_by_product(product_ids=[prod.id], customer_ids=[cust.id], include_summary=True)
    assert "so_outstanding_qty" not in _walk_keys(plain, set())
    assert "so_outstanding_count" not in _walk_keys(plain, set())
    # and the pipeline flag alone (no summary asked) adds nothing either
    no_summary = svc.list_orders_by_product(product_ids=[prod.id], customer_ids=[cust.id], include_pipeline=True)
    assert "summary" not in no_summary


def test_scoped_by_the_same_customer_and_product_filters_as_the_do_summary(db, scenario):
    cust, prod, wh = scenario
    other_cust = customer(db, company_id=DEFAULT_COMPANY_ID, name="ABC TRADING SDN BHD")
    other_prod = product(db, company_id=DEFAULT_COMPANY_ID, code="SRTWC999")
    _do(db, cust=other_cust, prod=prod, wh=wh, qty=4)                 # other customer, same product: a group row
    _so_line(db, customer_id=other_cust.id, product_id=prod.id, ordered=5)   # +5 for the other customer
    _so_line(db, customer_id=cust.id, product_id=other_prod.id, ordered=50)  # other product: outside the filter
    _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=3, status="closed")  # not open: ignored
    _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=2, delivered=2)      # delta 0: ignored
    db.commit()

    # product-only filter: both customers, per-customer share on the group, sum on the product
    s = OrderService(db).list_orders_by_product(
        product_ids=[prod.id], include_summary=True, include_pipeline=True
    )["summary"]
    assert [(g["customer"], g["so_outstanding_qty"]) for g in s["groups"]] == [
        ("ABC TRADING SDN BHD", 5), ("HENG SENG HARDWARE SDN BHD", 7),
    ]
    assert [(p["product_code"], p["so_outstanding_qty"]) for p in s["products"]] == [("SRTWC286", 12)]

    # customer narrower: the other customer's SO must not leak into the product total
    s2 = OrderService(db).list_orders_by_product(
        product_ids=[prod.id], customer_ids=[cust.id], include_summary=True, include_pipeline=True
    )["summary"]
    assert [(p["product_code"], p["so_outstanding_qty"]) for p in s2["products"]] == [("SRTWC286", 7)]
    assert [(g["customer"], g["so_outstanding_qty"]) for g in s2["groups"]] == [("HENG SENG HARDWARE SDN BHD", 7)]


def test_a_row_with_no_open_so_says_zero(db, scenario):
    cust, prod, wh = scenario
    quiet = customer(db, company_id=DEFAULT_COMPANY_ID, name="QUIET SDN BHD")
    _do(db, cust=quiet, prod=prod, wh=wh, qty=1)
    db.commit()
    s = OrderService(db).list_orders_by_product(
        product_ids=[prod.id], include_summary=True, include_pipeline=True
    )["summary"]
    # Review round 2, S1: QUIET is a customer the master knows with no open SO, so its 0
    # is a fact and stays; only a name the master does not know (the debtor-name gap
    # test below) omits the key.
    by_cust = {g["customer"]: g.get("so_outstanding_qty", "absent") for g in s["groups"]}
    assert by_cust == {"HENG SENG HARDWARE SDN BHD": 7, "QUIET SDN BHD": 0}
    assert s["products"][0]["so_outstanding_qty"] == 7


def test_company_scope_applies_to_the_so_rows_too(db, scenario):
    """A Mocha SO line on the same product must not reach a Sorento-scoped answer -
    the same by-hand predicate discipline `stamp_order_summary` documents."""
    cust, prod, _ = scenario
    m_cust = customer(db, company_id=MOCHA_ID, name="MOCHA BUYER SDN BHD")
    _so_line(db, customer_id=m_cust.id, product_id=prod.id, ordered=99, company_id=MOCHA_ID)
    db.commit()

    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    s = OrderService(db).list_orders_by_product(
        product_ids=[prod.id], include_summary=True, include_pipeline=True
    )["summary"]
    assert s["products"][0]["so_outstanding_qty"] == 7  # not 7 + 99


def test_known_gap_a_do_debtor_name_that_differs_from_the_customer_name(db, scenario):
    """KNOWN GAP, documented not fixed: `stamp_order_summary` names a group by the DO's
    `debtor_name` (falling back to the customer name), while `SalesOrder` has only
    `customer_id`, so the SO side is keyed on `Customer.customer_name`. A DO whose
    debtor_name was typed differently from its customer's name lands in a group the SO
    lookup cannot match: the product total still carries the SO quantity, that group
    carries no key (S1: never a 0 printed as fact). Both importers write debtor_name from the same customer master in
    practice, so this is rare; if it shows up in a real answer, the fix is to key both
    sides on customer_id."""
    cust, prod, wh = scenario
    _do(db, cust=cust, prod=prod, wh=wh, qty=2, debtor_name="HENG SENG HARDWARE (KL)")
    db.commit()
    s = OrderService(db).list_orders_by_product(
        product_ids=[prod.id], customer_ids=[cust.id], include_summary=True, include_pipeline=True
    )["summary"]
    assert s["products"][0]["so_outstanding_qty"] == 7
    by_cust = {g["customer"]: g.get("so_outstanding_qty", "absent") for g in s["groups"]}
    # the unmatchable group carries NO key (review round 2, S1): the presenter prints
    # nothing for it rather than "SO outstanding: 0" as fact
    assert by_cust == {"HENG SENG HARDWARE SDN BHD": 7, "HENG SENG HARDWARE (KL)": "absent"}


# -------------------------------------------------------------------- route

@pytest.fixture
def client(db):
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


def test_route_declares_include_pipeline():
    """FastAPI drops an undeclared query param silently - this is the drop the owner's
    turn measured, and it is invisible at runtime."""
    from fastapi.routing import APIRoute
    route = next(r for r in app.routes if isinstance(r, APIRoute) and r.path.endswith("/orders/by-product"))
    assert "include_pipeline" in {p.name for p in route.dependant.query_params}


def test_route_include_pipeline_reaches_the_rows_through_response_model(client, db, scenario):
    cust, prod, _ = scenario
    resp = client.get(BASE, params={"product_ids": prod.id, "customer_ids": cust.id,
                                    "include_summary": "true", "include_pipeline": "true"})
    assert resp.status_code == 200, resp.text
    s = resp.json()["summary"]
    assert s["products"][0]["so_outstanding_qty"] == 7
    assert s["groups"][0]["so_outstanding_qty"] == 7
    assert "so_outstanding_qty" not in s


def test_route_without_include_pipeline_is_unchanged(client, db, scenario):
    cust, prod, _ = scenario
    resp = client.get(BASE, params={"product_ids": prod.id, "customer_ids": cust.id, "include_summary": "true"})
    assert resp.status_code == 200, resp.text
    assert "so_outstanding_qty" not in _walk_keys(resp.json(), set())


# ------------------------------------------------------------------ D3 five SO figures

def test_d3_the_so_block_is_five_figures_per_group_and_per_product(db, scenario):
    """D3 (owner console pass, 8 Sep 2026): distinct SO count over every line of any
    status, the SO date span, ordered and transferred-to-DO sums, and the outstanding
    remainder - per customer x product and summed per product."""
    from datetime import date as _d

    cust, prod, wh = scenario  # already: one open SO 9 ordered / 2 delivered, no date
    so_b = _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=5, delivered=5, status="closed")
    so_b.order_date = _d(2026, 3, 1)
    so_c = _so_line(db, customer_id=cust.id, product_id=prod.id, ordered=4, delivered=1)
    so_c.order_date = _d(2026, 6, 9)
    other = customer(db, company_id=DEFAULT_COMPANY_ID, name="ABC TRADING SDN BHD")
    _do(db, cust=other, prod=prod, wh=wh, qty=1)
    _so_line(db, customer_id=other.id, product_id=prod.id, ordered=8, delivered=0)
    db.commit()

    s = OrderService(db).list_orders_by_product(
        product_ids=[prod.id], include_summary=True, include_pipeline=True
    )["summary"]
    by_cust = {g["customer"]: g for g in s["groups"]}
    heng = by_cust["HENG SENG HARDWARE SDN BHD"]
    assert heng["so_count"] == 3
    assert heng["so_date_from"] == "2026-03-01" and heng["so_date_to"] == "2026-06-09"
    assert heng["so_ordered_qty"] == 18 and heng["so_transferred_qty"] == 8
    assert heng["so_outstanding_qty"] == 10  # 7 + 3; the closed line is not outstanding
    abc = by_cust["ABC TRADING SDN BHD"]
    assert abc["so_count"] == 1 and abc["so_ordered_qty"] == 8 and abc["so_transferred_qty"] == 0
    assert abc["so_outstanding_qty"] == 8 and "so_date_from" not in abc
    p = s["products"][0]
    assert p["so_count"] == 4 and p["so_ordered_qty"] == 26 and p["so_transferred_qty"] == 8
    assert p["so_outstanding_qty"] == 18
    assert p["so_date_from"] == "2026-03-01" and p["so_date_to"] == "2026-06-09"


def test_d3_three_states_cover_all_five_figures(db, scenario):
    cust, prod, wh = scenario
    quiet = customer(db, company_id=DEFAULT_COMPANY_ID, name="QUIET SDN BHD")
    _do(db, cust=quiet, prod=prod, wh=wh, qty=1)
    _do(db, cust=cust, prod=prod, wh=wh, qty=2, debtor_name="HENG SENG HARDWARE (KL)")
    db.commit()
    s = OrderService(db).list_orders_by_product(
        product_ids=[prod.id], include_summary=True, include_pipeline=True
    )["summary"]
    by_cust = {g["customer"]: g for g in s["groups"]}
    keys = ("so_count", "so_ordered_qty", "so_transferred_qty", "so_outstanding_qty")
    assert [by_cust["QUIET SDN BHD"][k] for k in keys] == [0, 0, 0, 0]          # known, nothing: zeros
    assert not any(k in by_cust["HENG SENG HARDWARE (KL)"] for k in keys)       # unknown name: absent
    assert by_cust["HENG SENG HARDWARE SDN BHD"]["so_count"] == 1              # matched: facts


def test_d3_without_include_pipeline_no_so_figure_exists(db, scenario):
    cust, prod, _ = scenario
    plain = OrderService(db).list_orders_by_product(product_ids=[prod.id], customer_ids=[cust.id], include_summary=True)
    keys = _walk_keys(plain, set())
    assert not {"so_count", "so_date_from", "so_ordered_qty", "so_transferred_qty", "so_outstanding_qty"} & keys
