"""Phase 2 RED tests - chat fix PR-B S5 (AC-B7), route half: `GET /order-management/sales-report`
accepts `brand_ids` and the total is narrowed to those brands' products."""
from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from app.models.product import Brand
from app.services.company_scope import DEFAULT_COMPANY_ID
from tests._mc_lookup_seed import product, warehouse
from tests._sales_report_do_seed import line, seed_customer, seed_do
from tests._pg_fixture import unique_code
from tests.test_sales_report import BASE, client, db  # noqa: F401  (fixtures)


def _seed(db):
    mocha = Brand(id=str(uuid.uuid4()), brand_code=unique_code("MB")[:50], brand_name="ZZT Mocha", company_id=DEFAULT_COMPANY_ID)
    sorento = Brand(id=str(uuid.uuid4()), brand_code=unique_code("SB")[:50], brand_name="ZZT Sorento", company_id=DEFAULT_COMPANY_ID)
    db.add_all([mocha, sorento])
    db.flush()
    cust = seed_customer(db, name=unique_code("Cust"))
    wh = warehouse(db, company_id=DEFAULT_COMPANY_ID, code=unique_code("WH")[:20])
    pm = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-BRM1")
    ps = product(db, company_id=DEFAULT_COMPANY_ID, code="ZZT-BRS1")
    pm.brand_id, ps.brand_id = mocha.id, sorento.id
    for prod, qty in ((pm, 10), (ps, 30)):
        seed_do(db, customer_id=cust.id, order_date=date(2026, 6, 1),
                lines=[line(prod.id, wh.id, qty, price=Decimal("10"), total=Decimal(qty * 10))])
    db.commit()
    return cust, mocha, sorento


def test_b7_a_brand_narrows_the_total(client, db):
    cust, mocha, _s = _seed(db)
    whole = client.get(BASE, params={"customer_ids": cust.id}).json()
    assert whole["total"]["qty"] == 40, whole
    body = client.get(BASE, params={"customer_ids": cust.id, "brand_ids": mocha.id})
    assert body.status_code == 200, body.text
    assert body.json()["total"]["qty"] == 10, body.json()


def test_b7_two_brands_sum(client, db):
    cust, mocha, sorento = _seed(db)
    body = client.get(BASE, params={"customer_ids": cust.id, "brand_ids": f"{mocha.id},{sorento.id}"}).json()
    assert body["total"]["qty"] == 40, body


def test_b7_near_miss_a_brand_of_no_sale_gives_an_empty_total_not_the_whole(client, db):
    cust, _m, _s = _seed(db)
    other = Brand(id=str(uuid.uuid4()), brand_code=unique_code("OB")[:50], brand_name="ZZT Other", company_id=DEFAULT_COMPANY_ID)
    db.add(other)
    db.commit()
    body = client.get(BASE, params={"customer_ids": cust.id, "brand_ids": other.id}).json()
    assert body["total"]["qty"] == 0, body


# S5b (AC-B7): the reply must name the brand filter, so an empty brand-filtered total does not
# read as "no sales at all". Pinned (tester's choice, mirrors the outstanding report's echo):
# the body carries `brand_name`, the brand name(s) applied, absent/None when no brand filter.


def test_b7b_the_body_echoes_the_applied_brand(client, db):
    cust, mocha, _s = _seed(db)
    body = client.get(BASE, params={"customer_ids": cust.id, "brand_ids": mocha.id}).json()
    assert "ZZT Mocha" in str(body.get("brand_name")), body


def test_b7b_near_miss_no_brand_filter_echoes_none(client, db):
    cust, _m, _s = _seed(db)
    body = client.get(BASE, params={"customer_ids": cust.id}).json()
    assert not body.get("brand_name"), body
