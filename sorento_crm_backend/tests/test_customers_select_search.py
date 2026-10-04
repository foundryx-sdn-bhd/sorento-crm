"""`/order-management/customers/select` can be SEARCHED and PAGED on the server.

It returned every active customer - 6,397 of them on the client's database - as whole ORM
rows, on every open of every customer dropdown in the product. The sales-order detail page
is where that finally showed: the select took seconds to open because the browser was being
handed the entire debtor master to filter locally.

Two properties, and the second is the one that makes this change safe to ship:

* `query` / `limit` / `offset` narrow and page it, the same shape
  `master_data/products_select.py` already uses;
* **omitting them still returns the whole list.** Two callers - the order-management
  customer select and the SCM filter bar - hold the full array and filter it in the browser,
  so a default limit would silently make customer 51 unreachable in both. Products could
  default to 100 because that is what THAT endpoint already returned unconditionally; this
  one already returned everything, so "everything" is what no arguments has to keep meaning.

Postgres only, on a blank scratch schema, every row marked so nothing here is confused with
the real debtor master.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.models.order import Customer
from tests._pg_fixture import blank_session

URL = "/api/v1/order-management/customers/select"
_USER = {"id": str(uuid.uuid4()), "email": "captain@example.test", "role": "admin"}

#: The incumbent company every test schema is seeded with (tests/conftest.py). `Customer` is
#: company-scoped and the filter is FAIL-CLOSED, so a session with no scope reads an empty
#: master however many rows were seeded.
SORENTO_COMPANY_ID = "00000000-0000-0000-0000-000000000001"

#: What a customer dropdown actually shows. Listed explicitly rather than dumped off the ORM
#: row - the raw row carries credit limits and terms, and a dropdown is not the place to
#: decide who may see a customer's credit. The agent fields are there for the contact and
#: agent customer pickers (PLAN-contact-customers-29sep D2).
SELECT_KEYS = {
    "id",
    "customer_code",
    "customer_name",
    "market_segment_code",
    "sales_agent_id",
    "sales_agent_code",
    "sales_agent_name",
    # CUSTOMER-GROUP: the group's Add ledgers picker shows which group a ledger is in now.
    "customer_group_id",
    "customer_group_name",
}


@pytest.fixture
def db():
    from app.models.base import company_scope

    with blank_session() as s:
        with company_scope(s, frozenset({SORENTO_COMPANY_ID})):
            yield s


@pytest.fixture
def client(db):
    from app.main import app
    from app.database import get_db
    from app.dependencies import get_current_user, get_current_user_or_api_key
    from app.services.company_scope_resolver import apply_company_scope

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: _USER
    # The `order` module guard on the router resolves its principal through the
    # api-key-aware dependency, so overriding only `get_current_user` leaves every request
    # answering 401 before it reaches the endpoint under test.
    app.dependency_overrides[get_current_user_or_api_key] = lambda: _USER
    app.dependency_overrides[apply_company_scope] = (
        lambda: frozenset({SORENTO_COMPANY_ID})
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _seed(db, code: str, name: str, *, is_active: bool = True) -> Customer:
    row = Customer(
        id=str(uuid.uuid4()), customer_code=code, customer_name=name,
        is_active=is_active, company_id=SORENTO_COMPANY_ID,
    )
    db.add(row)
    db.commit()
    return row


def _codes(res) -> list[str]:
    return [r["customer_code"] for r in res.json()["data"]]


def test_no_arguments_still_returns_every_active_customer(client, db):
    for i in range(3):
        _seed(db, f"ZZT-CUS-{i}", f"ZZT Kitchens {i}")

    res = client.get(URL)

    assert res.status_code == 200, res.text
    assert sorted(_codes(res)) == ["ZZT-CUS-0", "ZZT-CUS-1", "ZZT-CUS-2"]


def test_a_row_carries_only_what_a_dropdown_shows(client, db):
    _seed(db, "ZZT-CUS-KEYS", "ZZT Keys Sdn Bhd")

    row = client.get(URL).json()["data"][0]

    assert set(row) == SELECT_KEYS


def test_the_query_matches_the_code_and_the_name(client, db):
    _seed(db, "ZZT-CUS-ROW", "ZZT Rowenda Kitchen")
    _seed(db, "ZZT-CUS-ARIA", "ZZT Aria Verde")

    assert _codes(client.get(URL, params={"query": "rowenda"})) == ["ZZT-CUS-ROW"]
    assert _codes(client.get(URL, params={"query": "CUS-ARIA"})) == ["ZZT-CUS-ARIA"]


def test_limit_and_offset_page_it_stably(client, db):
    for i in range(4):
        _seed(db, f"ZZT-CUS-P{i}", f"ZZT Paged {i}")

    first = _codes(client.get(URL, params={"query": "ZZT-CUS-P", "limit": 2}))
    second = _codes(client.get(URL, params={"query": "ZZT-CUS-P", "limit": 2, "offset": 2}))

    # Ordered by code, so the two pages neither repeat nor skip a row.
    assert first == ["ZZT-CUS-P0", "ZZT-CUS-P1"]
    assert second == ["ZZT-CUS-P2", "ZZT-CUS-P3"]


def test_an_inactive_customer_is_not_offered(client, db):
    _seed(db, "ZZT-CUS-GONE", "ZZT Closed Account", is_active=False)
    _seed(db, "ZZT-CUS-LIVE", "ZZT Live Account")

    assert _codes(client.get(URL, params={"query": "ZZT-CUS-"})) == ["ZZT-CUS-LIVE"]
