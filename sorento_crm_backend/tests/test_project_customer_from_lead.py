"""The project's customer, read through its lead (owner hand-test, 13 Oct, points 18-19).

A project has no customer column: the buyer is recorded on the lead it came from. The
project payload names that customer, with the phone and the main contact the quotation
header defaults to, so neither the Overview nor the quotation form has to make a second
trip. A project registered with no lead, or whose lead has no buyer yet, says so with nulls.
"""
from __future__ import annotations

import uuid

from app.models.order import CustomerContact

from .test_project_form_lead_link import (  # noqa: F401  (api is a fixture)
    PROJECTS_BASE,
    _create_lead,
    _register,
    api,
)


def test_a_project_names_its_leads_buyer_with_phone_and_main_contact(api):
    client, db, company_id, _user_id = api
    lead = _create_lead(
        client,
        new_customer={
            "customer_name": f"zzt Gamuda Builders {uuid.uuid4().hex[:8]}",
            "phone_number": "03-7777 1234",
        },
    ).json()
    assert lead["customer_id"], lead
    db.add(
        CustomerContact(
            company_id=company_id,
            customer_id=lead["customer_id"],
            full_name="Lim Wei Ming",
            contact_role="main",
        )
    )
    db.flush()

    project = _register(client, lead_id=lead["id"]).json()
    body = client.get(f"{PROJECTS_BASE}/{project['id']}").json()

    assert body["customer_id"] == lead["customer_id"]
    assert body["customer_name"].startswith("zzt Gamuda Builders")
    assert body["customer_phone"] == "03-7777 1234"
    assert body["customer_attn"] == "Lim Wei Ming"


def test_a_project_with_no_lead_has_no_customer(api):
    client, _db, _company_id, _user_id = api
    project = _register(client).json()
    body = client.get(f"{PROJECTS_BASE}/{project['id']}").json()

    assert body["customer_id"] is None
    assert body["customer_name"] is None
    assert body["customer_phone"] is None
    assert body["customer_attn"] is None


def test_the_list_carries_the_customer_too(api):
    client, _db, _company_id, _user_id = api
    lead = _create_lead(
        client,
        new_customer={"customer_name": f"zzt List Buyer {uuid.uuid4().hex[:8]}"},
    ).json()
    project = _register(client, lead_id=lead["id"]).json()

    rows = client.get(f"{PROJECTS_BASE}/", params={"limit": 200}).json()["data"]
    row = next(r for r in rows if r["id"] == project["id"])
    assert row["customer_name"].startswith("zzt List Buyer")
