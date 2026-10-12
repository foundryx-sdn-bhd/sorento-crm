"""REPORTS-STEP2 hand test 1 (h5): a brand name that exists in two companies is echoed once."""
from __future__ import annotations

import uuid

from app.models.company import RespondContactCompany
from app.models.product import Brand
from tests._mc_lookup_seed import seed_mocha
from tests._pg_fixture import unique_code
from decimal import Decimal as D

from tests._mc_lookup_seed import warehouse
from tests._sales_report_do_seed import money, seed_agent, seed_customer
from tests.test_report_ask_route import _brand, _full, _ok, _prod, _rows, _sale  # noqa: F401
from tests._sales_report_do_seed import client, db  # noqa: F401  (fixtures)


def test_h5_the_same_brand_name_in_two_companies_is_echoed_once(client, db) -> None:
    mocha = seed_mocha(db)
    first = _brand(db, "ZZT TWIN BRAND")
    second = Brand(id=str(uuid.uuid4()), brand_code=unique_code("BR")[:50], brand_name="ZZT TWIN BRAND",
                   company_id=mocha.id)
    db.add(second)
    db.flush()
    contact = _full(db)
    db.add(RespondContactCompany(id=str(uuid.uuid4()), respond_contact_id=contact.id, company_id=mocha.id))
    db.commit()
    body = _ok(client, contact, brand_ids=[first.id, second.id])
    brand = [f for f in body["filters"] if f["key"] == "brand"]
    assert brand and brand[0]["values"] == ["ZZT TWIN BRAND"], body["filters"]


# ----------------------------------------------------------------------------- the (no agent) bucket


def _agents_world(db):
    """A1 10 pcs RM 100 and A2 20 pcs RM 50 sold; two lines with no agent (3 pcs RM 30 and 1 pc RM 0)."""
    from app.services.company_scope import DEFAULT_COMPANY_ID

    cust = seed_customer(db, name=unique_code("Cust"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH"))
    prod = _prod(db, unique_code("ZZTNA", alpha=True))
    a1, a2 = seed_agent(db, code="ZZT-NA-1"), seed_agent(db, code="ZZT-NA-2")
    kw = dict(cust=cust, wh=wh, prod=prod)
    _sale(db, agent=a1, qty=10, total="100.00", **kw)
    _sale(db, agent=a2, qty=20, total="50.00", **kw)
    _sale(db, agent=None, qty=3, total="30.00", **kw)
    _sale(db, agent=None, qty=1, total="0.00", **kw)
    db.commit()
    return a1, a2


def test_h9_a_sales_agent_ranking_leaves_out_the_no_agent_bucket_and_its_total(client, db) -> None:
    _agents_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10)
    assert [r["name"] for r in body["rows"]] == ["ZZT-NA-1", "ZZT-NA-2"], body["rows"]
    assert money(body["total"]["amount"]) == D("150.00") and body["total"]["qty"] == 30, body["total"]


def test_h10_the_bottom_direction_leaves_it_out_too(client, db) -> None:
    _agents_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, sort="asc")
    assert [r["name"] for r in body["rows"]] == ["ZZT-NA-2", "ZZT-NA-1"], body["rows"]
    assert money(body["total"]["amount"]) == D("150.00"), body["total"]


def test_h11_guard_a_filter_naming_no_agent_keeps_the_bucket(client, db) -> None:
    a1, _a2 = _agents_world(db)
    body = _ok(client, _full(db), group_by="sales_agent", top_n=10, sales_agent_ids=[a1.id, "none"])
    names = [r["name"] for r in body["rows"]]
    assert "(no agent)" in names and "ZZT-NA-1" in names, body["rows"]


def test_h12_guard_a_customer_ranking_still_counts_lines_with_no_agent(client, db) -> None:
    _agents_world(db)
    body = _ok(client, _full(db), group_by="customer", top_n=10)
    assert money(body["total"]["amount"]) == D("180.00") and body["total"]["qty"] == 34, body["total"]
