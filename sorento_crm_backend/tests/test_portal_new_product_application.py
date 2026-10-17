"""NEW-PRODUCT-APP portal surface, red-first. Owner 18 Oct: "make sure we have equivalent
portal views"; crew PQ1 (b): no Supplier and no Cost price on the portal; PQ2 (a): the
portal applicant hears through its linked staff user.

Through TestClient because mounting order matters (the generic `/submissions/{kind}`
handlers must not shadow these literal paths). Pattern: tests/test_portal_sample_request.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.main import app  # noqa: F401  MUST be first app import (circular import)

from ._pg_fixture import blank_session
from ._sample_request_seed import MARKER, make_product, make_user, second_company, sorento, uid

PORTAL = "/api/v1/public/portal"
BASE = f"{PORTAL}/submissions/new_product_application"
NPA = f"{PORTAL}/new-product-application"
KIND = "new_product_application"


def _contact(db, *, granted: bool) -> str:
    from app.models.access import RespondContact
    from tests._portal_grant import grant_portal_forms, link_contact_company

    contact = RespondContact(
        id=uid(), phone_number=f"+60{uid().replace('-', '')[:9]}", name=f"{MARKER} contact"
    )
    db.add(contact)
    db.flush()
    link_contact_company(db, contact.id)
    if granted:
        grant_portal_forms(db, contact.id, [KIND])
    return contact.id


def _inquiry(db, contact_id, **over):
    from app.models.procurement import StockInquiry

    row = StockInquiry(
        id=uid(),
        inquiry_number=f"SI26-Z{uid()[:4]}",
        product_code="XY-1234-MB & XY-1234-BG",
        item_description="Kitchen sink, nanograin finish",
        project_customer=f"{MARKER} EXAMPLE DEVELOPER SDN BHD",
        project_name=f"{MARKER} EXAMPLE RESIDENCE, TOWER B",
        quantity="55",
        delivery_date="2028-01-17",
        purchasing_response="Supplier can make it, please apply for a new code.",
        status="responded",
        contact_id=contact_id,
        space_id="zzt-space",
    )
    for k, v in over.items():
        setattr(row, k, v)
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def portal(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.main import app as the_app
    from app.models.base import company_scope
    from app.models.portal import PortalToken
    from app.models.user import User
    from app.services.notification_service import NotificationService

    sent = []
    monkeypatch.setattr(
        NotificationService,
        "create_with_channel_preferences",
        lambda self, **kw: sent.append((kw.get("user_id"), kw.get("type"))),
    )

    with blank_session() as db:
        company_id = sorento(db)
        other_company = second_company(db)
        granted = _contact(db, granted=True)
        granted_b = _contact(db, granted=True)
        ungranted = _contact(db, granted=False)
        linked_user = make_user(db, f"{MARKER} linked salesman")
        db.get(User, linked_user).respond_contact_id = granted
        with company_scope(db, frozenset({company_id})):
            product = make_product(db)
        with company_scope(db, frozenset({other_company})):
            foreign = make_product(db)
        mine = _inquiry(db, granted)
        mine_open = _inquiry(db, granted, status="pending_purchasing")
        theirs = _inquiry(db, granted_b)
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
                    c=c, db=db, state=state, company_id=company_id, other_company=other_company,
                    granted=granted, granted_b=granted_b, ungranted=ungranted,
                    linked_user=linked_user, product=product, foreign=foreign, mine=mine,
                    mine_open=mine_open, theirs=theirs, sent=sent,
                )
        finally:
            the_app.dependency_overrides.clear()


def _as(p, contact_id):
    p.state["contact"] = contact_id
    return p.c


def _line(p, **over):
    body = {
        "proposed_code": "XY-1234-MB",
        "description": "Kitchen sink, nanograin finish",
        "category_id": p.product.category_id,
        "base_uom_id": p.product.base_uom_id,
        "expected_qty": "55",
    }
    body.update(over)
    return body


def _create(p, contact=None, **over):
    body = {"stock_inquiry_id": p.mine.id, "customer_name": "typed", "lines": [_line(p)]}
    body.update(over)
    res = _as(p, contact or p.granted).post(BASE, json=body)
    assert res.status_code == 201, res.text
    return res.json()


# ------------------------------------------------------------------ registration


def test_kind_is_grantable_not_default_and_labelled():
    from app.services import portal_form_visibility_service as vis
    from app.services.portal_service import GRANTABLE_PORTAL_FORM_TYPES, SUPPORTED_TYPES

    assert KIND in GRANTABLE_PORTAL_FORM_TYPES
    assert KIND not in SUPPORTED_TYPES
    assert vis._KIND_LABELS[KIND] == "New Product Application"


def test_kind_has_no_revision_settings_row():
    """It has no portal revision adapter (Revise is its own move), so the revision settings
    list must not offer it (the #1763 lesson for sample_request)."""
    from app.api.v1.forms.revision_configs import REVISABLE_PORTAL_FORM_TYPES

    assert KIND not in REVISABLE_PORTAL_FORM_TYPES


ROUTES = [
    ("get", BASE, None),
    ("post", BASE, {}),
    ("get", f"{BASE}/{{id}}", None),
    ("put", f"{BASE}/{{id}}", {}),
    ("post", f"{BASE}/{{id}}/submit", None),
    ("post", f"{BASE}/{{id}}/revise", {"reason": "x"}),
    ("post", f"{BASE}/{{id}}/cancel", None),
    ("get", f"{NPA}/inquiries", None),
    ("get", f"{NPA}/prefill?stock_inquiry_id={{id}}", None),
    ("get", f"{NPA}/lookups/item-groups", None),
    ("get", f"{NPA}/customers?q=zzt", None),
]


@pytest.mark.parametrize("method, url, body", ROUTES)
def test_no_grant_is_403_on_every_route(portal, method, url, body):
    c = _as(portal, portal.ungranted)
    url = url.replace("{id}", uid())
    res = getattr(c, method)(url, **({"json": body} if body is not None else {}))
    assert res.status_code == 403, (url, res.status_code, res.text)
    assert res.json().get("code") == "FORM_TYPE_NOT_VISIBLE"


# ------------------------------------------------------------------ create from my responded inquiry


def test_inquiries_lists_only_my_responded_ones(portal):
    res = _as(portal, portal.granted).get(f"{NPA}/inquiries")
    assert res.status_code == 200, res.text
    ids = {r["id"] for r in res.json()}
    assert portal.mine.id in ids
    assert portal.mine_open.id not in ids
    assert portal.theirs.id not in ids


def test_prefill_from_my_responded_inquiry_copies_text(portal):
    res = _as(portal, portal.granted).get(f"{NPA}/prefill", params={"stock_inquiry_id": portal.mine.id})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["customer_id"] is None
    assert body["lines"][0]["proposed_code"] == "XY-1234-MB & XY-1234-BG"


@pytest.mark.parametrize("which", ["theirs", "mine_open"])
def test_prefill_refuses_someone_elses_or_an_unanswered_inquiry(portal, which):
    target = getattr(portal, which)
    res = _as(portal, portal.granted).get(f"{NPA}/prefill", params={"stock_inquiry_id": target.id})
    assert res.status_code == 404


def test_create_links_only_my_own_inquiry(portal):
    res = _as(portal, portal.granted).post(
        BASE, json={"stock_inquiry_id": portal.theirs.id, "lines": [_line(portal)]}
    )
    assert res.status_code == 422, res.text


def test_create_is_owned_by_the_contact_and_its_linked_user(portal):
    d = _create(portal)
    assert d["source"] == "portal"
    assert d["status"] == "draft"
    assert d["application_no"].startswith("NPA-")
    assert d["applicant_id"] == portal.linked_user


# ------------------------------------------------------------------ PQ1 (b): no supplier, no cost


@pytest.mark.parametrize("field, value", [("supplier_id", "SUPPLIER"), ("cost_price", "12.50")])
def test_portal_cannot_set_supplier_or_cost(portal, field, value):
    from app.models.procurement import Supplier
    from app.models.base import company_scope

    if value == "SUPPLIER":
        with company_scope(portal.db, frozenset({portal.company_id})):
            s = Supplier(id=uid(), supplier_code=f"ZZT{uid()[:6]}", supplier_name="x")
            portal.db.add(s)
            portal.db.flush()
        value = s.id
    res = _as(portal, portal.granted).post(BASE, json={"lines": [_line(portal, **{field: value})]})
    assert res.status_code == 422, res.text


def test_portal_record_carries_no_supplier_or_cost_keys(portal):
    d = _create(portal)
    line = d["lines"][0]
    for key in ("supplier_id", "supplier_name", "cost_price"):
        assert key not in line


def test_lookups_list_the_company_item_groups_and_no_suppliers(portal):
    groups = _as(portal, portal.granted).get(f"{NPA}/lookups/item-groups")
    assert groups.status_code == 200, groups.text
    ids = {r["id"] for r in groups.json()}
    assert portal.product.category_id in ids
    assert portal.foreign.category_id not in ids
    for kind in ("uoms", "brands", "item-types"):
        assert _as(portal, portal.granted).get(f"{NPA}/lookups/{kind}").status_code == 200
    assert _as(portal, portal.granted).get(f"{NPA}/lookups/suppliers").status_code == 404


def test_another_companys_item_group_is_refused(portal):
    res = _as(portal, portal.granted).post(
        BASE, json={"lines": [_line(portal, category_id=portal.foreign.category_id)]}
    )
    assert res.status_code == 422, res.text


# ------------------------------------------------------------------ same rules as staff


def test_someone_elses_application_is_404(portal):
    d = _create(portal)
    c = _as(portal, portal.granted_b)
    assert c.get(f"{BASE}/{d['id']}").status_code == 404
    assert c.put(f"{BASE}/{d['id']}", json={"remark": "x"}).status_code == 404
    assert c.post(f"{BASE}/{d['id']}/submit").status_code == 404
    listed = c.get(BASE).json()
    assert d["id"] not in {r["id"] for r in listed["data"]}


def test_submit_lock_revise_cycle(portal):
    d = _create(portal)
    c = _as(portal, portal.granted)
    sub = c.post(f"{BASE}/{d['id']}/submit")
    assert sub.status_code == 200, sub.text
    assert sub.json()["status"] == "submitted"
    locked = c.put(f"{BASE}/{d['id']}", json={"remark": "x"})
    assert locked.status_code == 422
    assert "new_product_application_locked" in locked.text
    assert c.post(f"{BASE}/{d['id']}/revise", json={"reason": ""}).status_code == 422
    rev = c.post(f"{BASE}/{d['id']}/revise", json={"reason": "wrong UOM"})
    assert rev.status_code == 200, rev.text
    assert rev.json()["status"] == "draft"


def test_submit_names_the_missing_field(portal):
    d = _create(portal, lines=[_line(portal, base_uom_id=None)])
    res = _as(portal, portal.granted).post(f"{BASE}/{d['id']}/submit")
    assert res.status_code == 422
    assert "Line 1" in res.text


def test_portal_cannot_approve_or_work_lines(portal):
    d = _create(portal)
    c = _as(portal, portal.granted)
    assert c.post(f"{BASE}/{d['id']}/approve").status_code in (404, 405)


def test_cancel_by_the_contact(portal):
    d = _create(portal)
    res = _as(portal, portal.granted).post(f"{BASE}/{d['id']}/cancel")
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "cancelled"


def test_staff_rejection_reaches_the_linked_user(portal):
    """PQ2 (a): the portal applicant hears through its linked staff user."""
    from app.models.new_product_application import NewProductApplication
    from app.services import new_product_application_service as svc

    d = _create(portal)
    assert _as(portal, portal.granted).post(f"{BASE}/{d['id']}/submit").status_code == 200
    app_row = portal.db.get(NewProductApplication, d["id"])
    approver = make_user(portal.db, f"{MARKER} approver")
    svc.reject(portal.db, app_row, actor=svc.Actor(user_id=approver, can_approve=True), reason="no")
    portal.db.commit()
    svc.notify(portal.db, app_row, "rejected")
    assert (portal.linked_user, "new_product_application_rejected") in portal.sent


def test_portal_application_is_stamped_with_the_contacts_company(portal):
    from app.models.new_product_application import NewProductApplication

    d = _create(portal)
    row = portal.db.get(NewProductApplication, d["id"])
    assert str(row.company_id) == str(portal.company_id)
    assert row.contact_id == portal.granted


def test_review_portal_cannot_set_or_read_the_supplier_model_no(portal):
    res = _as(portal, portal.granted).post(BASE, json={"lines": [_line(portal, supplier_item_code="XY1234")]})
    assert res.status_code == 422, res.text
    d = _create(portal)
    assert "supplier_item_code" not in d["lines"][0]
