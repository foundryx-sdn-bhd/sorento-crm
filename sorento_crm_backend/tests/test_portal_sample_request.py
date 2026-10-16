"""SR-CORE portal surface, red-first (Phase 2). Every test cites its AC id.

Through TestClient, because mounting order is the lesson of D49: the generic
``/submissions/{kind}`` handler must not shadow the literal sample_request paths.
Pattern: tests/test_portal_price_tag_routes.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.main import app  # noqa: F401  MUST be first app import (circular import)

from ._pg_fixture import blank_session
from ._sample_request_seed import (
    MARKER,
    full_payload,
    make_customer,
    make_product,
    make_project,
    make_user,
    second_company,
    seed_catalogue,
    sorento,
    uid,
)

PORTAL = "/api/v1/public/portal"
BASE = f"{PORTAL}/submissions/sample_request"


def _contact(db, *, granted: bool, company: bool = True) -> str:
    from app.models.access import RespondContact
    from tests._portal_grant import grant_portal_forms, link_contact_company

    contact = RespondContact(
        id=uid(), phone_number=f"+60{uid().replace('-', '')[:9]}", name=f"{MARKER} contact"
    )
    db.add(contact)
    db.flush()
    if company:
        link_contact_company(db, contact.id)
    if granted:
        grant_portal_forms(db, contact.id, ["sample_request"])
    return contact.id


@pytest.fixture
def portal():
    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.models.portal import PortalToken
    from fastapi.testclient import TestClient

    from app.main import app as the_app

    with blank_session() as db:
        company_id = sorento(db)
        from app.services import project_seed_service

        other_company = second_company(db)
        project_seed_service.run(db, company_id=company_id)
        project_seed_service.run(db, company_id=other_company)
        owner = make_user(db, f"{MARKER} owner")
        cat = seed_catalogue(db)
        inside = make_project(db, company_id, owner, f"{MARKER} inside tower")
        outside = make_project(db, other_company, owner, f"{MARKER} outside tower")
        granted = _contact(db, granted=True)
        granted_b = _contact(db, granted=True)
        ungranted = _contact(db, granted=False)
        # A real row: the company-scope resolver reads portal_tokens by the header value, and
        # without one it falls back to the default company, which hides a missing scope wrap.
        db.add(
            PortalToken(
                id=uid(),
                token="zzt-token",
                contact_id=granted,
                space_id="zzt-space",
                expires_at=datetime.utcnow() + timedelta(days=30),
                verified_at=datetime.utcnow() - timedelta(minutes=5),
            )
        )
        db.commit()

        state = {"contact": granted}

        def _token():
            return PortalToken(id=uid(), contact_id=state["contact"], space_id="zzt-space")

        the_app.dependency_overrides[get_db] = lambda: db
        the_app.dependency_overrides[get_portal_token] = _token
        try:
            with TestClient(the_app, headers={"X-Portal-Token": "zzt-token"}) as c:
                yield SimpleNamespace(
                    c=c,
                    db=db,
                    state=state,
                    cat=cat,
                    inside=inside,
                    outside=outside,
                    company_id=company_id,
                    other_company=other_company,
                    granted=granted,
                    granted_b=granted_b,
                    ungranted=ungranted,
                )
        finally:
            the_app.dependency_overrides.clear()


def _as(p, contact_id):
    p.state["contact"] = contact_id
    return p.c


def _create(p, contact=None, **over):
    body = full_payload(product_id=p.cat.plain.id, **over)
    res = _as(p, contact or p.granted).post(BASE, json=body)
    assert res.status_code == 201, res.text
    return res.json()


# ------------------------------------------------------------------ AC-SR-40


def test_ac_sr_40_sample_request_is_grantable_not_default():
    """AC-SR-40: in GRANTABLE_PORTAL_FORM_TYPES, NOT in SUPPORTED_TYPES."""
    from app.services.portal_service import GRANTABLE_PORTAL_FORM_TYPES, SUPPORTED_TYPES

    assert "sample_request" in GRANTABLE_PORTAL_FORM_TYPES
    assert "sample_request" not in SUPPORTED_TYPES


def test_ac_sr_40_kind_label():
    """AC-SR-40: the visibility service knows the label (plan S4)."""
    from app.services import portal_form_visibility_service as svc

    assert svc._KIND_LABELS["sample_request"] == "Sample Request"


ROUTES = [
    ("get", BASE, None),
    ("post", BASE, {}),
    ("get", f"{BASE}/{{id}}", None),
    ("put", f"{BASE}/{{id}}", {}),
    ("post", f"{BASE}/{{id}}/submit", None),
    ("post", f"{BASE}/{{id}}/cancel", None),
    ("get", f"{BASE}/{{id}}/pdf", None),
    ("get", f"{PORTAL}/sample-request/products?q=zzt", None),
    ("get", f"{PORTAL}/sample-request/customers?q=zzt", None),
]


@pytest.mark.parametrize("method, url, body", ROUTES)
def test_ac_sr_40_no_grant_is_403_on_every_route(portal, method, url, body):
    """AC-SR-40: without the grant: 403 FORM_TYPE_NOT_VISIBLE, before any record lookup."""
    c = _as(portal, portal.ungranted)
    url = url.replace("{id}", uid())
    res = getattr(c, method)(url, **({"json": body} if body is not None else {}))
    assert res.status_code == 403, (url, res.status_code, res.text)
    assert res.json().get("code") == "FORM_TYPE_NOT_VISIBLE"


# ------------------------------------------------------------------ happy path


def test_ac_sr_41_create_list_get_submit_cancel(portal):
    """AC-SR-40/41/43: a granted contact creates, lists, opens, submits and cancels."""
    d = _create(portal)
    assert d["source"] == "portal"
    assert d["status"] == "draft"
    assert d["request_no"].startswith("SR-")

    c = _as(portal, portal.granted)
    listed = c.get(BASE)
    assert listed.status_code == 200, listed.text
    assert d["id"] in [r["id"] for r in listed.json()["data"]]

    got = c.get(f"{BASE}/{d['id']}")
    assert got.status_code == 200, got.text
    assert got.json()["id"] == d["id"]

    sub = c.post(f"{BASE}/{d['id']}/submit")
    assert sub.status_code in (200, 201), sub.text
    assert c.get(f"{BASE}/{d['id']}").json()["status"] == "submitted"

    cancelled = c.post(f"{BASE}/{d['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert c.get(f"{BASE}/{d['id']}").json()["status"] == "cancelled"


def test_ac_sr_43_cancel_is_not_offered_after_cancelled(portal):
    """AC-SR-43: cancel only while draft/submitted -> a second cancel is 409."""
    d = _create(portal)
    c = _as(portal, portal.granted)
    assert c.post(f"{BASE}/{d['id']}/cancel").status_code == 200
    assert c.post(f"{BASE}/{d['id']}/cancel").status_code == 409


def test_ac_sr_43_submitted_request_cannot_be_edited(portal):
    """AC-SR-43 / 32: read-only after submit."""
    d = _create(portal)
    c = _as(portal, portal.granted)
    assert c.post(f"{BASE}/{d['id']}/submit").status_code in (200, 201)
    res = c.put(f"{BASE}/{d['id']}", json={"scope_note": "late"})
    assert res.status_code == 409, res.text


def test_ac_sr_02_portal_submit_refusal_carries_code_and_field(portal):
    """AC-SR-02: the same SAMPLE_REQUEST_INCOMPLETE refusal on the portal."""
    d = _create(portal, delivery_mode=None)
    res = _as(portal, portal.granted).post(f"{BASE}/{d['id']}/submit")
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["code"] == "SAMPLE_REQUEST_INCOMPLETE"
    assert body["details"]["field"] == "delivery_mode"


def test_ac_sr_10_portal_inactive_product_is_422(portal):
    """AC-SR-10: product check applies to the portal write too."""
    body = full_payload(
        product_id=portal.cat.plain.id, lines=[{"product_id": portal.cat.inactive.id, "qty": 1}]
    )
    res = _as(portal, portal.granted).post(BASE, json=body)
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "SAMPLE_REQUEST_PRODUCT_INVALID"


def test_ac_sr_11_portal_folded_search(portal):
    """AC-SR-11: portal product search is dash, space and case blind, active only."""
    c = _as(portal, portal.granted)
    res = c.get(f"{PORTAL}/sample-request/products", params={"q": "zzt9101"})
    assert res.status_code == 200, res.text
    assert "ZZT-9101" in [r["product_code"] for r in res.json()]
    every = c.get(f"{PORTAL}/sample-request/products", params={"q": "ZZT"}).json()
    assert portal.cat.inactive.product_code not in [r["product_code"] for r in every]


# ------------------------------------------------------------------ AC-SR-41


def test_ac_sr_41_other_contacts_request_is_404_on_every_by_id_route(portal):
    """AC-SR-41 + contract: records load by (id, contact_id); someone else's id is not found."""
    d = _create(portal, contact=portal.granted)
    c = _as(portal, portal.granted_b)
    rid = d["id"]
    assert c.get(f"{BASE}/{rid}").status_code == 404
    assert c.put(f"{BASE}/{rid}", json={"scope_note": "x"}).status_code == 404
    assert c.post(f"{BASE}/{rid}/submit").status_code == 404
    assert c.post(f"{BASE}/{rid}/cancel").status_code == 404
    assert c.get(f"{BASE}/{rid}/pdf").status_code == 404


def test_ac_sr_41_list_only_shows_own_requests(portal):
    """AC-SR-41: a contact lists only own requests."""
    mine = _create(portal, contact=portal.granted)["id"]
    theirs = _create(portal, contact=portal.granted_b)["id"]
    ids = [r["id"] for r in _as(portal, portal.granted).get(BASE).json()["data"]]
    assert mine in ids and theirs not in ids


def test_ac_sr_54_portal_list_rows_carry_no_phone_or_address(portal):
    """AC-SR-54: portal list rows never carry the personal-data keys; detail does."""
    d = _create(portal, site_contact_phone="+60100000001", ship_to_address="9 Jalan Zzt")
    c = _as(portal, portal.granted)
    res = c.get(BASE)
    assert "+60100000001" not in res.text and "Jalan Zzt" not in res.text
    for row in res.json()["data"]:
        assert "site_contact_phone" not in row and "ship_to_address" not in row
    assert c.get(f"{BASE}/{d['id']}").json()["site_contact_phone"] == "+60100000001"


# ------------------------------------------------------------------ AC-SR-42


def test_ac_sr_42_project_outside_the_contacts_lookup_is_422(portal):
    """AC-SR-42: a project the contact's lookup does not return -> SAMPLE_REQUEST_PROJECT_NOT_ALLOWED."""
    body = full_payload(
        product_id=portal.cat.plain.id, project_id=portal.outside.id, project_title=None
    )
    res = _as(portal, portal.granted).post(BASE, json=body)
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "SAMPLE_REQUEST_PROJECT_NOT_ALLOWED"


def test_ac_sr_42_project_inside_the_lookup_is_accepted(portal):
    """AC-SR-42: the same write with an allowed project is created."""
    d = _create(portal, project_id=portal.inside.id, project_title=None)
    got = _as(portal, portal.granted).get(f"{BASE}/{d['id']}").json()
    assert got["project_id"] == portal.inside.id


def test_ac_sr_42_update_rechecks_the_project(portal):
    """AC-SR-42: the API re-checks on save, not only on create."""
    d = _create(portal)
    res = _as(portal, portal.granted).put(
        f"{BASE}/{d['id']}", json={"project_id": portal.outside.id}
    )
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "SAMPLE_REQUEST_PROJECT_NOT_ALLOWED"


# ------------------------------------------------------------------ print


def test_ac_sr_60_portal_pdf_for_own_request(portal):
    """AC-SR-60/61: the owner prints own request as a PDF."""
    d = _create(portal)
    res = _as(portal, portal.granted).get(f"{BASE}/{d['id']}/pdf")
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/pdf")
    assert res.content.startswith(b"%PDF")


def test_generic_submissions_handler_does_not_shadow_the_literal_route(portal):
    """D49: POST /submissions/sample_request must reach the sample request route, not the
    generic handler (which would answer with a body.fields complaint or an unsupported-kind 4xx)."""
    res = _as(portal, portal.granted).post(
        BASE, json=full_payload(product_id=portal.cat.plain.id)
    )
    assert res.status_code == 201, res.text
    assert "fields" not in res.text


# ------------------------------------------------------------------ review round 1

def _link_agent(p, contact_id):
    from app.models.sales_agent import SalesAgent

    agent = SalesAgent(id=uid(), sales_agent=f"ZZT{uid()[:6]}".upper(), contact_id=contact_id)
    p.db.add(agent)
    p.db.flush()
    return agent


def test_review_s2_customer_must_be_in_the_contacts_debtors(portal):
    """S2: a customer id outside the contact's debtors-for-agent set is 422
    SAMPLE_REQUEST_CUSTOMER_INVALID, on create and on update; the contact's own debtor is
    accepted; a typed name without an id stays allowed."""
    agent = _link_agent(portal, portal.granted)
    mine = make_customer(portal.db, portal.company_id)
    mine.sales_agent_id = agent.id
    stranger = make_customer(portal.db, portal.company_id)
    portal.db.commit()  # the first refusal rolls the session back

    c = _as(portal, portal.granted)
    bad = c.post(
        BASE,
        json=full_payload(
            product_id=portal.cat.plain.id, customer_id=stranger.id, customer_name=None
        ),
    )
    assert bad.status_code == 422, bad.text
    assert bad.json().get("code") == "SAMPLE_REQUEST_CUSTOMER_INVALID"

    ok = _create(portal, customer_id=mine.id, customer_name=None)
    assert ok["customer_id"] == mine.id

    res = c.put(f"{BASE}/{ok['id']}", json={"customer_id": stranger.id})
    assert res.status_code == 422, res.text
    assert res.json().get("code") == "SAMPLE_REQUEST_CUSTOMER_INVALID"

    typed = _create(portal, customer_name=f"{MARKER} walk-in")
    assert typed["customer_id"] is None


def test_browser_pass_customer_picker_lists_only_what_submit_accepts(portal):
    """Browser pass on 85d42f75: the portal picker listed every debtor (the unscoped
    `/lookups/debtors`) while submit accepts only the contact's debtors-for-agent, so a picked
    debtor came back 422. The sample request's own lookup lists the accepted set only, each with
    the id submit needs, and filters by code or name."""
    agent = _link_agent(portal, portal.granted)
    mine = make_customer(portal.db, portal.company_id)
    mine.sales_agent_id = agent.id
    stranger = make_customer(portal.db, portal.company_id)
    portal.db.commit()

    c = _as(portal, portal.granted)
    res = c.get(f"{PORTAL}/sample-request/customers", params={"q": ""})
    assert res.status_code == 200, res.text
    ids = {row["customer_id"] for row in res.json()}
    assert mine.id in ids
    assert stranger.id not in ids
    row = next(r for r in res.json() if r["customer_id"] == mine.id)
    assert row["customer_code"] == mine.customer_code
    assert row["debtor_name"] == mine.customer_name

    by_code = c.get(f"{PORTAL}/sample-request/customers", params={"q": mine.customer_code.lower()})
    assert [r["customer_id"] for r in by_code.json()] == [mine.id]
    none = c.get(f"{PORTAL}/sample-request/customers", params={"q": stranger.customer_code})
    assert none.json() == []

    picked = _create(portal, customer_id=row["customer_id"], customer_name=None)
    assert picked["customer_id"] == mine.id


def test_review_portal_put_rechecks_the_customer_only_when_it_changes(portal):
    """A draft whose debtor later left the agent's book still saves with the same customer;
    changing to an outside debtor is still 422."""
    agent = _link_agent(portal, portal.granted)
    mine = make_customer(portal.db, portal.company_id)
    mine.sales_agent_id = agent.id
    stranger = make_customer(portal.db, portal.company_id)
    portal.db.commit()
    ok = _create(portal, customer_id=mine.id, customer_name=None)

    mine.sales_agent_id = None  # the debtor leaves the agent's book
    portal.db.commit()

    c = _as(portal, portal.granted)
    same = c.put(f"{BASE}/{ok['id']}", json={"customer_id": mine.id, "remarks": f"{MARKER} edit"})
    assert same.status_code == 200, same.text
    assert same.json()["customer_id"] == mine.id

    moved = c.put(f"{BASE}/{ok['id']}", json={"customer_id": stranger.id})
    assert moved.status_code == 422, moved.text
    assert moved.json().get("code") == "SAMPLE_REQUEST_CUSTOMER_INVALID"


def _other_company_product(p):
    # ORM, not raw SQL: text() would bypass the scratch schema's translate map.
    row = make_product(p.db, code="ZZT-OTHERCO-77", name=f"{MARKER} other company item")
    row.company_id = p.other_company
    p.db.commit()
    return row


def test_review_s3_other_company_product_is_not_listed_and_refused(portal):
    """S3 / AC-SR-10: the portal product search is scoped to the contact's company, and a
    write naming another company's product is a 422."""
    from tests._portal_grant import link_contact_company

    # The contact belongs to BOTH companies: the token scope alone would list both
    # catalogues, the route must narrow to the one company it stamps requests with.
    link_contact_company(portal.db, portal.granted, portal.other_company)
    foreign = _other_company_product(portal)
    c = _as(portal, portal.granted)
    res = c.get(f"{PORTAL}/sample-request/products", params={"q": "OTHERCO"})
    assert res.status_code == 200, res.text
    assert res.json() == []

    body = full_payload(
        product_id=portal.cat.plain.id, lines=[{"product_id": foreign.id, "qty": 1}]
    )
    refused = c.post(BASE, json=body)
    assert refused.status_code == 422, refused.text
    assert refused.json().get("code") == "SAMPLE_REQUEST_PRODUCT_INVALID"


def test_security_review_customer_picker_is_scoped_to_the_stamped_company(portal):
    """Security review on 0ec1299e: a contact in two companies must not be offered the other
    company's debtor, because submit checks inside the one company the request is stamped
    with; listed and accepted stay the same set."""
    from tests._portal_grant import link_contact_company

    link_contact_company(portal.db, portal.granted, portal.other_company)
    agent = _link_agent(portal, portal.granted)
    mine = make_customer(portal.db, portal.company_id)
    mine.sales_agent_id = agent.id
    foreign = make_customer(portal.db, portal.other_company)
    foreign.sales_agent_id = agent.id
    portal.db.commit()

    c = _as(portal, portal.granted)
    ids = {r["customer_id"] for r in c.get(f"{PORTAL}/sample-request/customers").json()}
    assert mine.id in ids
    assert foreign.id not in ids


def test_review_customer_picker_leaves_out_an_order_only_debtor(portal):
    """Reviewer on 75261484: the agent's book also holds debtors seen only on orders (no
    customer row, so no id submit can accept). The picker lists only pickable debtors."""
    from datetime import date

    from app.models.order import Order

    agent = _link_agent(portal, portal.granted)
    mine = make_customer(portal.db, portal.company_id)
    mine.sales_agent_id = agent.id
    portal.db.add(
        Order(
            order_number=f"ZZT-SO-{uid()[:6]}",
            order_date=date.today(),
            debtor_code="ZZT-ORDONLY",
            debtor_name=f"{MARKER} order only debtor",
            agent=agent.sales_agent,
            company_id=portal.company_id,
        )
    )
    portal.db.commit()

    rows = _as(portal, portal.granted).get(f"{PORTAL}/sample-request/customers").json()
    assert [r["customer_id"] for r in rows] == [mine.id]
    assert all(r["customer_id"] for r in rows)
