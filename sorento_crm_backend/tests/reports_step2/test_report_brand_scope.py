"""Phase 2 RED test - `/report` stamps the contact's brand scope like `/report-ask` (REPORTS-STEP2 AC-10).

Own file: the brand-scope seed defines its own `db` fixture (see `tests/test_report_ask_brand_scope.py`).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests._brand_scope_seed import api, db, leaks, world  # noqa: F401  (fixtures by name)
from tests._report_ask_so_seed import seed_so_line
from tests._pg_fixture import unique_code
from tests._sales_report_do_seed import money, seed_agent

ROUTE = "/api/v1/order-management/report"
PERIOD = {"date_from": "2026-09-01", "date_to": "2026-09-30"}


@pytest.fixture
def sales(db, world):
    ag1, ag2 = seed_agent(db, code=unique_code("ZZT-S2")), seed_agent(db, code=unique_code("ZZT-S2"))
    kw = dict(cust=world.cust)
    seed_so_line(db, prod=world.p_mocha, agent=ag1, qty=2, total="100.00", **kw)
    seed_so_line(db, prod=world.p_sorento, agent=ag1, qty=3, total="1000.00", **kw)
    seed_so_line(db, prod=world.p_null, agent=ag2, qty=4, total="9.00", **kw)
    db.commit()
    return world


def test_ac10_a_scoped_contact_ranking_by_product_sees_only_its_brands(api, sales):
    resp = api.get(ROUTE, sales.scoped, **PERIOD, measure="delivered_amount", group_by="product", top_n=10)
    assert resp.status_code == 200, resp.text
    assert leaks(resp, sales) == {"mocha": True, "sorento": False, "null": False}, resp.text
    body = resp.json()
    assert body["total"]["qty"] == 2 and money(body["total"]["amount"]) == Decimal("100.00"), body["total"]


def test_ac10_a_scoped_contacts_plain_total_is_the_in_scope_lines_only(api, sales):
    resp = api.get(ROUTE, sales.scoped, **PERIOD, measure="ordered_amount")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"]["qty"] == 2 and money(body["total"]["amount"]) == Decimal("100.00"), body
