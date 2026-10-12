"""REPORTS-STEP2 hand test 1 (h5): a brand name that exists in two companies is echoed once."""
from __future__ import annotations

import uuid

from app.models.company import RespondContactCompany
from app.models.product import Brand
from tests._mc_lookup_seed import seed_mocha
from tests._pg_fixture import unique_code
from tests.test_report_ask_route import _brand, _full, _ok  # noqa: F401
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
