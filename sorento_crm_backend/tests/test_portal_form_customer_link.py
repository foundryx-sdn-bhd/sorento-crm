"""RED tests - FORM-ROUTING-CONDITIONS PC-2 / PC-3 (portal side).

* PC-2: GET /public/portal/lookups/debtors?q= reads the customer master and returns
  {customer_id, debtor_name, customer_code, debtor_type, sales_type}, active customers only.
* PC-3: portal PR / SF draft save + submit accept `customer_id`; an unknown id is a 422;
  `customer_id` without `customer_name` is allowed.

Harness pattern: tests/test_portal_product_lookup_id_s2.py (portal token override over a blank
Postgres schema), every row seeded here.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app  # noqa: E402

from app.models.base import set_company_scope
from app.models.order import Customer
from app.services.company_scope import DEFAULT_COMPANY_ID, register_company_scope_listeners
from tests._pg_fixture import blank_session

BASE = "/api/v1/public/portal"


def _uid() -> str:
    return str(uuid.uuid4())


def _sfx() -> str:
    return uuid.uuid4().hex[:6].upper()


def _contact(db):
    from app.models.access import RespondContact

    contact = RespondContact(id=_uid(), phone_number=f"+60{_uid().replace('-', '')[:9]}", name="SALESMAN A")
    db.add(contact)
    db.flush()
    return contact


def _customer(db, code, name, **kw) -> Customer:
    register_company_scope_listeners()
    set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
    row = Customer(customer_code=code, customer_name=name, company_id=DEFAULT_COMPANY_ID, **kw)
    db.add(row)
    db.flush()
    return row


@contextmanager
def _portal_client(db, contact_id: str):
    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.models.portal import PortalToken

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_portal_token] = lambda: PortalToken(
        id=_uid(), contact_id=contact_id, space_id="zzt-space"
    )
    try:
        with TestClient(app, headers={"X-Portal-Token": "zzt-token"}) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------ PC-2 lookup


def test_debtor_lookup_returns_customers_with_the_full_item_shape():
    with blank_session() as db:
        contact = _contact(db)
        tag = f"ZZQ{_sfx()}"
        pc = _customer(db, f"302-{tag}", f"{tag} PROJECT CASH SDN BHD")
        pj = _customer(db, f"300-{tag}", f"{tag} PROJECT SDN BHD")
        walkin = _customer(db, f"301-{tag}", f"CASH ({tag} PERSON A)")
        _customer(db, f"300-{tag}X", f"{tag} INACTIVE SDN BHD", is_active=False)

        with _portal_client(db, contact.id) as c:
            res = c.get(f"{BASE}/lookups/debtors", params={"q": tag})
            assert res.status_code == 200, res.text
            items = {i["customer_code"]: i for i in res.json()}

        assert set(items) == {pc.customer_code, pj.customer_code, walkin.customer_code}
        assert items[pc.customer_code] == {
            "customer_id": str(pc.id),
            "debtor_name": pc.customer_name,
            "customer_code": pc.customer_code,
            "debtor_type": "project_cash",
            "sales_type": "cash_sales",
        }
        assert items[pj.customer_code]["sales_type"] == "project"
        assert items[walkin.customer_code]["debtor_type"] is None
        assert items[walkin.customer_code]["sales_type"] is None


def test_debtor_lookup_matches_a_code_substring_case_insensitively():
    with blank_session() as db:
        contact = _contact(db)
        tag = f"ZZQ{_sfx()}"
        row = _customer(db, f"300-{tag}", "ZZT CODE MATCH SDN BHD")
        with _portal_client(db, contact.id) as c:
            res = c.get(f"{BASE}/lookups/debtors", params={"q": tag.lower()})
            assert res.status_code == 200, res.text
            assert [i["customer_id"] for i in res.json()] == [str(row.id)]


# ------------------------------------------------------------------ PC-3 draft save + submit


def _draft(c, kind, fields, products=None):
    body = {"fields": fields}
    if products is not None:
        body["products"] = products
    return c.post(f"{BASE}/submissions/{kind}", json=body)


def _stored_customer_id(db, submission_id):
    value = db.execute(
        text("SELECT customer_id FROM purchase_requests WHERE id = :i"), {"i": submission_id}
    ).scalar_one()
    return None if value is None else str(value)


@pytest.mark.parametrize("kind", ["purchase_request", "sponsorship_form"])
def test_draft_save_stores_customer_id(kind):
    with blank_session() as db:
        contact = _contact(db)
        cust = _customer(db, f"300-ZZT{_sfx()}", "ZZT DRAFT CUSTOMER SDN BHD")
        with _portal_client(db, contact.id) as c:
            res = _draft(c, kind, {"customer_name": cust.customer_name, "customer_id": str(cust.id)})
            assert res.status_code in (200, 201), res.text
            sid = res.json()["id"]
            assert _stored_customer_id(db, sid) == str(cust.id)
            assert res.json().get("customer_id") == str(cust.id)


def test_draft_update_can_set_and_clear_customer_id():
    with blank_session() as db:
        contact = _contact(db)
        cust = _customer(db, f"300-ZZT{_sfx()}", "ZZT UPDATE CUSTOMER SDN BHD")
        with _portal_client(db, contact.id) as c:
            sid = _draft(c, "purchase_request", {"customer_name": "FREE TEXT"}).json()["id"]
            put = c.put(
                f"{BASE}/submissions/purchase_request/{sid}",
                json={"fields": {"customer_name": cust.customer_name, "customer_id": str(cust.id)}},
            )
            assert put.status_code == 200, put.text
            assert _stored_customer_id(db, sid) == str(cust.id)
            cleared = c.put(
                f"{BASE}/submissions/purchase_request/{sid}",
                json={"fields": {"customer_name": "FREE TEXT", "customer_id": None}},
            )
            assert cleared.status_code == 200, cleared.text
            assert _stored_customer_id(db, sid) is None


def test_customer_id_without_customer_name_is_allowed():
    with blank_session() as db:
        contact = _contact(db)
        cust = _customer(db, f"300-ZZT{_sfx()}", "ZZT NAMELESS LINK SDN BHD")
        with _portal_client(db, contact.id) as c:
            res = _draft(c, "purchase_request", {"customer_id": str(cust.id)})
            assert res.status_code in (200, 201), res.text
            assert _stored_customer_id(db, res.json()["id"]) == str(cust.id)


def test_unknown_customer_id_is_422_on_save():
    with blank_session() as db:
        contact = _contact(db)
        with _portal_client(db, contact.id) as c:
            res = _draft(c, "purchase_request", {"customer_name": "X", "customer_id": _uid()})
            assert res.status_code == 422, res.text


def test_unknown_customer_id_is_422_on_submit():
    with blank_session() as db:
        contact = _contact(db)
        with _portal_client(db, contact.id) as c:
            sid = _draft(c, "purchase_request", {"customer_name": "FREE TEXT"}).json()["id"]
            res = c.post(
                f"{BASE}/submissions/purchase_request/{sid}/submit",
                json={"fields": {"customer_name": "X", "customer_id": _uid()}},
            )
            assert res.status_code == 422, res.text


@pytest.mark.parametrize("kind", ["purchase_request", "sponsorship_form"])
def test_customer_id_is_in_the_editable_whitelist(kind):
    from app.services.portal_service import PortalService

    with blank_session() as db:
        assert "customer_id" in PortalService(db)._editable_fields(kind)


@pytest.mark.parametrize("kind", ["complaint", "stock_inquiry"])
def test_customer_id_is_not_editable_on_other_kinds(kind):
    from app.services.portal_service import PortalService

    with blank_session() as db:
        assert "customer_id" not in PortalService(db)._editable_fields(kind)
