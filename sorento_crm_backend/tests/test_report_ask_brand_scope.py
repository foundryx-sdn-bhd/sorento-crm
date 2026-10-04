"""CONTACT-BRAND-SCOPE x REPORT-ENGINE: `crm_report_ask` honours the calling contact's brands.

Brand scope arrived from main after the report engine: `BRAND_SCOPE_TREATMENT` must name
`crm_report_ask` (`filtered`: it returns product rows when `group_by=product`), the route must
count only in-scope lines for a brand-scoped X-API-Key contact, and the fetch guard must drop a
`group_by=product` row naming another brand's product.

Own file because `tests/test_report_ask_route.py` and the brand-scope seed each define a `db`
fixture. Same harness as `tests/test_brand_scope_paths.py` (real `apply_company_scope`, stubbed
authentication). Every row is seeded here.

Seed (Sep 2026, fully delivered, one line per SO):
    AGENT1: MOCHA product 2 x 100.00, SORENTO product 3 x 1000.00
    AGENT2: SORENTO product 5 x 7.00, unbranded product 4 x 9.00
A MOCHA-only contact therefore sees AGENT1 with 2 / 100.00 and nothing else.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from tests._brand_scope_seed import api, db, leaks, world  # noqa: F401  (fixtures by name)
from tests._report_ask_so_seed import seed_so_line
from tests._sales_report_do_seed import money, seed_agent

D = Decimal
ROUTE = "/api/v1/order-management/report-ask"
PERIOD = {"date_from": "2026-09-01", "date_to": "2026-09-30"}


@pytest.fixture
def sales(db, world):
    from app.services.company_scope import DEFAULT_COMPANY_ID  # noqa: F401

    ag1, ag2 = seed_agent(db, code="ZZT-BS-AG1"), seed_agent(db, code="ZZT-BS-AG2")
    kw = dict(cust=world.cust)
    seed_so_line(db, prod=world.p_mocha, agent=ag1, qty=2, total="100.00", **kw)
    seed_so_line(db, prod=world.p_sorento, agent=ag1, qty=3, total="1000.00", **kw)
    seed_so_line(db, prod=world.p_sorento, agent=ag2, qty=5, total="7.00", **kw)
    seed_so_line(db, prod=world.p_null, agent=ag2, qty=4, total="9.00", **kw)
    db.commit()
    return world


def _ask(api, who, **params):
    resp = api.get(ROUTE, who, **PERIOD, basis="delivered", measure="amount", **params)
    assert resp.status_code == 200, resp.text
    return resp


def _rows(resp):
    return [(r["name"], r["qty"], money(r["amount"])) for r in resp.json()["rows"]]


def test_the_tool_is_marked_filtered_in_the_contract() -> None:
    from app.services.chatbot.contracts import BRAND_SCOPE_TREATMENT

    assert BRAND_SCOPE_TREATMENT.get("crm_report_ask") == "filtered"


def test_a_scoped_contact_ranking_by_product_sees_only_its_brands_products(api, sales) -> None:
    resp = _ask(api, sales.scoped, group_by="product", top_n=10)
    assert leaks(resp, sales) == {"mocha": True, "sorento": False, "null": False}, resp.text
    body = resp.json()
    assert body["total_count"] == 1, body
    assert body["total"]["qty"] == 2 and money(body["total"]["amount"]) == D("100.00"), body["total"]


def test_a_scoped_contact_ranking_by_sales_agent_counts_only_in_scope_lines(api, sales) -> None:
    resp = _ask(api, sales.scoped, group_by="sales_agent", top_n=10)
    assert _rows(resp) == [("ZZT-BS-AG1", 2, D("100.00"))], resp.json()["rows"]
    body = resp.json()
    assert body["total"]["qty"] == 2 and money(body["total"]["amount"]) == D("100.00"), body["total"]


def test_a_scoped_contact_ranking_by_brand_sees_only_its_own_brand(api, sales) -> None:
    resp = _ask(api, sales.scoped, group_by="brand", top_n=10)
    assert sales.sorento.brand_name not in resp.text, resp.text
    assert [r["name"] for r in resp.json()["rows"]] == [sales.mocha.brand_name], resp.json()["rows"]


def test_a_scoped_contacts_plain_total_is_the_in_scope_lines_only(api, sales) -> None:
    body = _ask(api, sales.scoped).json()
    assert body["total"]["qty"] == 2 and money(body["total"]["amount"]) == D("100.00"), body["total"]


def test_an_unscoped_contact_sees_everything_as_today(api, sales) -> None:
    by_agent = _ask(api, sales.unscoped, group_by="sales_agent", top_n=10)
    assert _rows(by_agent) == [("ZZT-BS-AG1", 5, D("1100.00")), ("ZZT-BS-AG2", 9, D("16.00"))], by_agent.json()["rows"]
    by_product = _ask(api, sales.unscoped, group_by="product", top_n=10)
    assert leaks(by_product, sales) == {"mocha": True, "sorento": True, "null": True}, by_product.text


def test_the_fetch_guard_drops_a_product_row_of_another_brand(db, world) -> None:
    from app.services.chatbot.lanes.business import brand_guard

    result = {"group_by": "product", "rows": [
        {"rank": 1, "name": world.codes["sorento"], "qty": 3, "amount": 1000.0},
        {"rank": 2, "name": world.codes["mocha"], "qty": 2, "amount": 100.0},
        {"rank": 3, "name": world.codes["null"], "qty": 4, "amount": 9.0},
    ]}
    out = brand_guard.guard_result("crm_report_ask", result, [world.mocha.id], db)
    assert [r["name"] for r in out["rows"]] == [world.codes["mocha"]], out


def test_the_fetch_guard_leaves_a_sales_agent_ranking_alone(db, world) -> None:
    from app.services.chatbot.lanes.business import brand_guard

    result = {"group_by": "sales_agent", "rows": [{"rank": 1, "name": "ZZT-BS-AG1", "qty": 2, "amount": 100.0}]}
    assert brand_guard.guard_result("crm_report_ask", result, [world.mocha.id], db) == result


# --------------------------------------------------------------------------- #
# L3: the same scope on the ordered and outstanding bases
# --------------------------------------------------------------------------- #


@pytest.fixture
def sales_open(db, sales):
    """`sales` plus two lines nothing was delivered on: MOCHA 6 x 60.00 (AG1), SORENTO 8 x 80.00 (AG2)."""
    from sqlalchemy import select

    from app.models.sales_agent import SalesAgent

    ag1 = db.execute(select(SalesAgent).where(SalesAgent.sales_agent == "ZZT-BS-AG1")).scalar_one()
    ag2 = db.execute(select(SalesAgent).where(SalesAgent.sales_agent == "ZZT-BS-AG2")).scalar_one()
    seed_so_line(db, prod=sales.p_mocha, agent=ag1, qty=6, delivered=0, total="60.00", cust=sales.cust)
    seed_so_line(db, prod=sales.p_sorento, agent=ag2, qty=8, delivered=0, total="80.00", cust=sales.cust)
    db.commit()
    return sales


def _ask_basis(api, who, basis, **params):
    resp = api.get(ROUTE, who, **PERIOD, basis=basis, measure="amount", **params)
    assert resp.status_code == 200, resp.text
    return resp


@pytest.mark.parametrize("basis, qty, amount", [("ordered", 8, D("160.00")), ("outstanding", 6, D("60.00"))])
def test_l3_a_scoped_contact_sees_only_its_brands_figures_on_every_basis(api, sales_open, basis, qty, amount) -> None:
    by_product = _ask_basis(api, sales_open.scoped, basis, group_by="product", top_n=10)
    assert leaks(by_product, sales_open) == {"mocha": True, "sorento": False, "null": False}, by_product.text
    by_agent = _ask_basis(api, sales_open.scoped, basis, group_by="sales_agent", top_n=10)
    assert _rows(by_agent) == [("ZZT-BS-AG1", qty, amount)], by_agent.json()["rows"]
    by_brand = _ask_basis(api, sales_open.scoped, basis, group_by="brand", top_n=10)
    assert [r["name"] for r in by_brand.json()["rows"]] == [sales_open.mocha.brand_name], by_brand.json()["rows"]
    total = _ask_basis(api, sales_open.scoped, basis).json()["total"]
    assert total["qty"] == qty and money(total["amount"]) == amount, total


# --------------------------------------------------------------------------- #
# Security H1: the brand scope must not depend on the legacy shared key
# --------------------------------------------------------------------------- #

LEGACY_KEY = "zzt-legacy-shared-key-not-sent"


class _IntegrationApi:
    """A caller whose X-API-Key is an `integration_api_keys` key, NOT `settings.external_api_key`.
    Authentication is the REAL dependency (only `get_db` is overridden)."""

    def __init__(self, client, db, key: str) -> None:
        self.client, self.db, self.key = client, db, key

    def get(self, path, who, **params):
        from app.models.base import set_company_scope
        from app.services.company_scope import DEFAULT_COMPANY_ID
        from tests._brand_scope_seed import SPACE

        self.db.info.pop("brand_scope", None)
        self.db.commit()
        query = {k: v for k, v in params.items() if v is not None}
        query.update({"contact_id": who.id, "space_id": SPACE})
        try:
            return self.client.get(path, params=query, headers={"X-API-Key": self.key})
        finally:
            self.db.info.pop("brand_scope", None)
            set_company_scope(self.db, frozenset({DEFAULT_COMPANY_ID}))


@pytest.fixture
def integration_api(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.dependencies import get_db
    from app.main import app
    from app.models.integration import Integration
    from app.services.integration_key_service import IntegrationKeyService
    from tests._brand_scope_seed import _superadmin
    from tests._pg_fixture import unique_code

    monkeypatch.setattr(settings, "external_api_key", LEGACY_KEY)
    principal = _superadmin(db)
    integration = Integration(
        name=unique_code("ZZT-INT"), type="autocount_esb", act_as_user_id=principal["id"], is_active=True
    )
    db.add(integration)
    db.flush()
    key = IntegrationKeyService(db).issue_key(integration)
    db.commit()
    assert key != LEGACY_KEY

    def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    try:
        yield _IntegrationApi(TestClient(app), db, key)
    finally:
        app.dependency_overrides.clear()


def test_h1_the_integration_key_alone_is_a_valid_caller(integration_api, sales) -> None:
    """Guard on the harness: this key authenticates and the unscoped contact sees every brand."""
    resp = _ask(integration_api, sales.unscoped, group_by="product", top_n=10)
    assert leaks(resp, sales) == {"mocha": True, "sorento": True, "null": True}, resp.text


def test_h1_a_scoped_contact_by_brand_sees_only_its_own_brand(integration_api, sales) -> None:
    resp = _ask(integration_api, sales.scoped, group_by="brand", top_n=10)
    assert sales.sorento.brand_name not in resp.text and "(blank)" not in resp.text, resp.text
    assert [r["name"] for r in resp.json()["rows"]] == [sales.mocha.brand_name], resp.json()["rows"]


def test_h1_a_scoped_contact_by_sales_agent_counts_only_in_scope_lines(integration_api, sales) -> None:
    resp = _ask(integration_api, sales.scoped, group_by="sales_agent", top_n=10)
    assert _rows(resp) == [("ZZT-BS-AG1", 2, D("100.00"))], resp.json()["rows"]


def test_h1_a_scoped_contacts_total_is_the_in_scope_lines_only(integration_api, sales) -> None:
    body = _ask(integration_api, sales.scoped).json()
    assert body["total"]["qty"] == 2 and money(body["total"]["amount"]) == D("100.00"), body["total"]


def test_h1_a_scoped_contact_by_product_sees_only_its_brands_products(integration_api, sales) -> None:
    resp = _ask(integration_api, sales.scoped, group_by="product", top_n=10)
    assert leaks(resp, sales) == {"mocha": True, "sorento": False, "null": False}, resp.text
