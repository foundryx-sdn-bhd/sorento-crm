"""Phase 2 RED tests - REPORTS-STEP2 S5a review: `/report` accepts every legacy arg the lane builds and its
body equals the old route's body for the same args (reviewer B1, B2, B3), plus error parity and the S2
ranking path against `/report-ask`.

The legacy args are exactly the ones `fetch.entity_ids_transformer` builds for `crm_outstanding_report` /
`crm_sales_report` (`product_codes`, `location_token`, `order_date_from/to`, `detail`, `so_refused`,
`channel`, no date window). The `view` arg is the MCP's own and never reaches the route.
Postgres only, every row seeded here.
"""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._pg_fixture import unique_code
from tests._report_ask_so_seed import seed_so_line
from tests._sales_report_do_seed import (  # noqa: F401  (client / db are fixtures)
    _as_contact,
    _contact,
    client,
    db,
    line,
    product,
    seed_agent,
    seed_customer,
    seed_do,
    warehouse,
)
from tests.reports_step2.test_report_route import ASK, PERIOD, ROUTE as REPORT_ROUTE
from tests.reports_step2.test_s4_measures import (
    OUT_GRANT,
    OUTSTANDING,
    SALES_GRANT,
    SALES_REPORT,
    _get,
    _staff,
    _without_kind,
)

D = Decimal
REPORT = "/api/v1/order-management/report"


def _outstanding_world(db):
    cust = seed_customer(db, name=unique_code("Cust"))
    p1 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    p2 = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh1 = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-IB1")
    wh2 = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-IB2")
    seed_so_line(db, cust=cust, prod=p1, qty=10, total="100.00", wh=wh1, delivered=3, order_date=date(2026, 8, 5))
    seed_so_line(db, cust=cust, prod=p2, qty=6, total="60.00", wh=wh2, delivered=0, order_date=date(2026, 9, 5))
    db.commit()
    return cust, p1, p2


OUT_CASES = {
    "several_product_codes": lambda c, p1, p2: dict(product_codes=[p1.product_code, p2.product_code]),
    "location_token": lambda c, p1, p2: dict(customer_ids=c.id, warehouse_codes=["ZZT-IB1", "ZZT-IB2"],
                                             location_token="IB"),
    "order_date_window": lambda c, p1, p2: dict(customer_ids=c.id, order_date_from="2026-09-01",
                                                order_date_to="2026-09-30"),
    "detail_so": lambda c, p1, p2: dict(customer_ids=c.id, scope="both", detail="so"),
    "detail_do": lambda c, p1, p2: dict(customer_ids=c.id, scope="both", detail="do"),
    "so_refused_do_only": lambda c, p1, p2: dict(customer_ids=c.id, scope="do", so_refused="true"),
}


@pytest.mark.parametrize("case", sorted(OUT_CASES))
def test_review_order_outstanding_accepts_the_legacy_args_and_equals_the_old_route(client, db, case):
    cust, p1, p2 = _outstanding_world(db)
    contact = _staff(db, SALES_GRANT, OUT_GRANT)
    legacy = OUT_CASES[case](cust, p1, p2)
    old = _get(client, OUTSTANDING, contact, **legacy)
    new = _get(client, REPORT, contact, measure="order_outstanding", **legacy)
    assert old.status_code == 200, old.text
    assert new.status_code == 200, new.text
    assert _without_kind(new.json()) == old.json()


def _sales_world(db):
    cust = seed_customer(db, name=unique_code("Cust"))
    prod = product(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("SKU"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-IB1")
    seed_agent(db, code=unique_code("AG"))
    seed_do(db, customer_id=cust.id, order_date=date(2026, 9, 10), number=unique_code("DO"),
            lines=[line(prod.id, wh.id, 5, price=D("10"), total=D("50.00"))])
    db.commit()
    return cust


SALES_CASES = {
    "no_date_window": lambda c: dict(customer_ids=c.id),
    "channel": lambda c: dict(customer_ids=c.id, channel="project"),
    "location_token": lambda c: dict(customer_ids=c.id, warehouse_codes=["ZZT-IB1"], location_token="IB"),
    "pick_group_by_product": lambda c: dict(customer_ids=c.id, group_by="product"),
}


@pytest.mark.parametrize("case", sorted(SALES_CASES))
def test_review_delivered_sales_accepts_the_legacy_args_and_equals_the_old_route(client, db, case):
    cust = _sales_world(db)
    contact = _staff(db, SALES_GRANT)
    legacy = SALES_CASES[case](cust)
    old = _get(client, SALES_REPORT, contact, **legacy)
    new = _get(client, REPORT, contact, measure="delivered_sales", **legacy)
    assert old.status_code == 200, old.text
    assert new.status_code == 200, new.text
    assert _without_kind(new.json()) == old.json()


# ------------------------------------------------------------------ error parity


@pytest.mark.parametrize(
    "measure, path, grants, bad",
    [
        ("order_outstanding", OUTSTANDING, (SALES_GRANT, OUT_GRANT), dict(customer_ids=str(uuid.uuid4()), scope="bogus")),
        ("order_outstanding", OUTSTANDING, (SALES_GRANT, OUT_GRANT), dict(customer_ids="not-a-uuid")),
        ("delivered_sales", SALES_REPORT, (SALES_GRANT,), dict(product_code="ab")),
        ("delivered_sales", SALES_REPORT, (SALES_GRANT,), dict(customer_ids=str(uuid.uuid4()), group_by="zzz")),
    ],
    ids=["outstanding_scope", "outstanding_customer_ids", "sales_product_code", "sales_group_by"],
)
def test_review_a_4xx_of_the_old_route_is_the_same_4xx_through_report(client, db, measure, path, grants, bad):
    contact = _staff(db, *grants)
    old = _get(client, path, contact, **bad)
    new = _get(client, REPORT, contact, measure=measure, **bad)
    assert 400 <= old.status_code < 500, old.text
    assert new.status_code == old.status_code, (new.text, old.text)
    assert new.json() == old.json(), (new.json(), old.json())


# ------------------------------------------------------------------ the S2 ranking path on /report


def test_review_a_ranking_with_filters_carried_and_a_link_param_equals_report_ask_and_is_accepted(client, db):
    from tests.test_report_ask_route import _full, _ranking_world

    w = _ranking_world(db)
    contact = _full(db)
    shared = dict(group_by="customer", top_n=5, brand_ids=[w.brand_a.id], category_ids=[w.cat_x.id],
                  customer_agent_ids=[w.a1.id], sales_agent_ids=[w.a1.id, w.a2.id])
    old = _get(client, ASK, contact, basis="delivered", measure="amount", **PERIOD, **shared)
    new = _get(client, REPORT, contact, measure="delivered_amount", carried=["brand_ids", "date_from", "date_to"],
               **PERIOD, **shared)
    assert old.status_code == 200, old.text
    assert new.status_code == 200, new.text
    assert new.json()["rows"] == old.json()["rows"] and new.json()["total"] == old.json()["total"]
    assert new.json()["carried"] == ["brand_ids", "date_from", "date_to"], new.json()
