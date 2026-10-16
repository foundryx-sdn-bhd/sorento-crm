"""S2: opportunities logged by salespeople, portal side (UAC S2-3 to S2-6, S2-9, S2-15).

Plan: documentation/plans/sales/PLAN-sales-targets-opportunities-26sep.md, sections 3.5, 3.7,
section 16 (the S2 build contract). UAC: sales-targets-opportunities-26sep-acceptance-criteria.md,
S2-3 to S2-6, S2-9, S2-15.

Same TestClient-through-the-app shape as tests/test_portal_price_tag_routes.py (D49): mounting
order between `portal_sales_opportunity.router` and the generic `portal.router` is part of the
contract, not something a service call can prove.

Nothing here exists yet on this branch (no `app.api.v1.public.portal_sales_opportunity`, no
`app.services.sales.portal_agent`, no `app.models.sales.SalesOpportunity`): every test is
expected to fail on collection, at import, or with a 404 for a route that is not mounted.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

# MUST be first app import - resolves the circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from tests._pg_fixture import blank_session

BASE = "/api/v1/public/portal/sales-opportunities"


def _uid() -> str:
    return str(uuid.uuid4())


def _sorento(db) -> str:
    return db.execute(text("select id from companies where code = 'SRT'")).scalar()


def _contact(db: Session, *, name: str = "ZZT Salesperson"):
    from app.models.access import RespondContact

    contact = RespondContact(id=_uid(), phone_number=f"+60{_uid().replace('-', '')[:9]}", name=name)
    db.add(contact)
    db.flush()
    return contact


def _grant_kind(db: Session, contact_id: str, kind: str = "sales_opportunity") -> None:
    from app.models.price_tag import ContactPortalFormOverride

    db.add(
        ContactPortalFormOverride(
            id=_uid(), contact_id=contact_id, form_type=kind, is_enabled=True
        )
    )
    db.flush()


def _agent(db: Session, *, contact_id=None, active: bool = True, company_id=None, code=None):
    from app.models.sales_agent import SalesAgent

    agent = SalesAgent(
        id=_uid(),
        sales_agent=code or f"ZZO-{_uid()[:8]}".upper(),
        description="ZZT Agent",
        is_active=active,
        contact_id=contact_id,
        company_id=company_id,
    )
    db.add(agent)
    db.flush()
    return agent


def _customer(db, company_id, *, name: str = "ZZT Customer", agent_id=None):
    from app.models.order import Customer

    customer = Customer(
        id=_uid(),
        company_id=company_id,
        customer_code=f"ZZT-{_uid()[:6]}",
        customer_name=name,
        sales_agent_id=agent_id,
    )
    db.add(customer)
    db.flush()
    return customer


def _product(db, *, name: str = "ZZT Portal Product"):
    from app.models.product import Brand, Product, ProductCategory, UnitOfMeasure

    category = ProductCategory(
        id=_uid(), category_code=f"ZZT-{_uid()[:6]}", category_name="ZZT Portal Cat"
    )
    brand = Brand(id=_uid(), brand_code=f"ZZT-{_uid()[:6]}", brand_name="ZZT Portal Brand")
    uom = UnitOfMeasure(id=_uid(), uom_code=f"ZZT-{_uid()[:6]}", uom_name="Each")
    db.add_all([category, brand, uom])
    db.flush()
    product = Product(
        id=_uid(),
        product_code=f"ZZT-{_uid()[:6]}",
        product_name=name,
        category_id=category.id,
        brand_id=brand.id,
        base_uom_id=uom.id,
        list_price=Decimal("50.00"),
    )
    db.add(product)
    db.flush()
    return product


def _seed(db) -> None:
    from app.services.sales import sales_seed_service

    sales_seed_service.run(db)
    db.flush()


@contextmanager
def _portal_client(db, contact_id: str):
    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.models.portal import PortalToken

    def _override_get_db():
        yield db

    def _override_portal_token():
        # Never added to the session: the routes read contact_id off it, nothing persists it.
        return PortalToken(id=_uid(), contact_id=contact_id, space_id="zzt-space")

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_portal_token] = _override_portal_token
    try:
        with TestClient(app, headers={"X-Portal-Token": "zzt-token"}) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


def _status_id(db, key: str) -> str:
    from app.models.status import Status

    return (
        db.query(Status)
        .filter(Status.entity_type == "sales_opportunity", Status.key == key)
        .one()
        .id
    )


# --------------------------------------------------------------------------- #
# S2-3: a granted, linked contact creates at the initial stage
# --------------------------------------------------------------------------- #


def test_s2_3_granted_agent_creates_at_the_initial_stage_ignoring_the_body_agent():
    with blank_session() as db:
        _seed(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        mine = _agent(db, contact_id=contact.id)
        someone_else = _agent(db)

        with _portal_client(db, contact.id) as c:
            res = c.post(
                BASE,
                json={
                    "title": "ZZT Portal Opp",
                    "expected_amount": "500",
                    "expected_close_date": "2026-11-01",
                    "prospect_name": "ZZT Some Prospect",
                    "sales_agent_id": someone_else.id,
                },
            )
            assert res.status_code == 201, res.text
            body = res.json()
            assert body["sales_agent_id"] == mine.id
            assert body["source"] == "portal"
            assert body["created_by_contact_id"] == contact.id
            assert body["stage_key"] == "new"


# --------------------------------------------------------------------------- #
# S2-4: visibility and agent-linkage gates
# --------------------------------------------------------------------------- #


def test_s2_4_no_grant_is_403_form_type_not_visible():
    with blank_session() as db:
        _seed(db)
        contact = _contact(db)
        with _portal_client(db, contact.id) as c:
            res = c.get(BASE)
            assert res.status_code == 403, res.text
            assert res.json().get("code") == "FORM_TYPE_NOT_VISIBLE"


def test_s2_4_granted_but_not_linked_to_an_agent_is_403_not_a_sales_agent():
    with blank_session() as db:
        _seed(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        with _portal_client(db, contact.id) as c:
            res = c.get(BASE)
            assert res.status_code == 403, res.text
            assert res.json().get("code") == "NOT_A_SALES_AGENT"


def test_s2_4_granted_but_the_linked_agent_is_inactive_is_403_not_a_sales_agent():
    with blank_session() as db:
        _seed(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        _agent(db, contact_id=contact.id, active=False)
        with _portal_client(db, contact.id) as c:
            res = c.get(BASE)
            assert res.status_code == 403, res.text
            assert res.json().get("code") == "NOT_A_SALES_AGENT"


def test_s2_4_kind_is_grantable_not_a_base_kind_and_not_auto_visible():
    from app.services.portal_service import GRANTABLE_PORTAL_FORM_TYPES, SUPPORTED_TYPES

    assert "sales_opportunity" in GRANTABLE_PORTAL_FORM_TYPES
    assert "sales_opportunity" not in SUPPORTED_TYPES

    with blank_session() as db:
        from app.services.portal_form_visibility_service import resolve_visible_form_types

        contact = _contact(db)
        visible = resolve_visible_form_types(db, contact.id)
        assert "sales_opportunity" not in visible


# --------------------------------------------------------------------------- #
# S2-5 / S2-15 (portal): scoped customer lookup, prospect and blocked options
# --------------------------------------------------------------------------- #


def test_s2_15_portal_customer_options_are_scoped_to_the_agents_own_customers():
    with blank_session() as db:
        _seed(db)
        company_id = _sorento(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        mine_agent = _agent(db, contact_id=contact.id)
        other_agent = _agent(db)
        mine = _customer(db, company_id, name="Kedai Mine", agent_id=mine_agent.id)
        theirs = _customer(db, company_id, name="Kedai Theirs", agent_id=other_agent.id)

        with _portal_client(db, contact.id) as c:
            res = c.get(f"{BASE}/customer-options", params={"q": "kedai"})
            assert res.status_code == 200, res.text
            ids = {i["customer_id"] for i in res.json()["items"]}
            assert mine.id in ids
            assert theirs.id not in ids


def test_s2_15_portal_exact_match_of_another_agents_customer_is_blocked_not_offered_as_prospect():
    with blank_session() as db:
        _seed(db)
        company_id = _sorento(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        _agent(db, contact_id=contact.id)
        other_agent = _agent(db)
        _customer(db, company_id, name="Seri Indah", agent_id=other_agent.id)

        with _portal_client(db, contact.id) as c:
            res = c.get(f"{BASE}/customer-options", params={"q": "Seri Indah"})
            assert res.status_code == 200, res.text
            body = res.json()
            assert body["prospect"] is None
            assert body["blocked"] == {
                "name": "Seri Indah",
                "message": "Seri Indah is another agent's customer",
            }


def test_s2_15_portal_no_matching_customer_offers_the_typed_name_as_a_prospect():
    with blank_session() as db:
        _seed(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        _agent(db, contact_id=contact.id)

        with _portal_client(db, contact.id) as c:
            res = c.get(f"{BASE}/customer-options", params={"q": "ZZT Nobody Has This Name"})
            assert res.status_code == 200, res.text
            assert res.json()["prospect"] == {"name": "ZZT Nobody Has This Name"}
            assert res.json()["blocked"] is None


def test_s2_15_portal_create_with_another_agents_customer_is_422_customer_not_yours():
    with blank_session() as db:
        _seed(db)
        company_id = _sorento(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        _agent(db, contact_id=contact.id)
        other_agent = _agent(db)
        theirs = _customer(db, company_id, name="ZZT Not Mine", agent_id=other_agent.id)

        with _portal_client(db, contact.id) as c:
            res = c.post(
                BASE,
                json={
                    "customer_id": theirs.id,
                    "title": "ZZT",
                    "expected_amount": "1",
                    "expected_close_date": "2026-11-01",
                },
            )
            assert res.status_code == 422, res.text
            assert res.json().get("code") == "CUSTOMER_NOT_YOURS"


def test_s2_15_portal_create_with_neither_customer_nor_prospect_is_422():
    with blank_session() as db:
        _seed(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        _agent(db, contact_id=contact.id)

        with _portal_client(db, contact.id) as c:
            res = c.post(
                BASE,
                json={
                    "title": "ZZT",
                    "expected_amount": "1",
                    "expected_close_date": "2026-11-01",
                },
            )
            assert res.status_code == 422, res.text
            assert res.json().get("code") == "CUSTOMER_OR_PROSPECT_REQUIRED"


def test_s2_15_portal_create_with_prospect_matching_another_agents_customer_is_422():
    with blank_session() as db:
        _seed(db)
        company_id = _sorento(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        _agent(db, contact_id=contact.id)
        other_agent = _agent(db)
        _customer(db, company_id, name="Seri Indah", agent_id=other_agent.id)

        with _portal_client(db, contact.id) as c:
            res = c.post(
                BASE,
                json={
                    "prospect_name": "Seri Indah",
                    "title": "ZZT",
                    "expected_amount": "1",
                    "expected_close_date": "2026-11-01",
                },
            )
            assert res.status_code == 422, res.text
            assert res.json().get("code") == "PROSPECT_IS_A_CUSTOMER"


# --------------------------------------------------------------------------- #
# S2-6: own-only visibility, allowed edges, Lost reason
# --------------------------------------------------------------------------- #


def test_s2_6_agent_sees_and_opens_only_their_own_opportunities():
    with blank_session() as db:
        _seed(db)
        contact_a = _contact(db, name="ZZT Agent A Contact")
        contact_b = _contact(db, name="ZZT Agent B Contact")
        _grant_kind(db, contact_a.id)
        _grant_kind(db, contact_b.id)
        _agent(db, contact_id=contact_a.id)
        _agent(db, contact_id=contact_b.id)

        with _portal_client(db, contact_a.id) as c:
            mine = c.post(
                BASE,
                json={
                    "title": "ZZT Mine",
                    "expected_amount": "1",
                    "expected_close_date": "2026-11-01",
                    "prospect_name": "ZZT Mine Prospect",
                },
            ).json()

        with _portal_client(db, contact_b.id) as c:
            theirs = c.post(
                BASE,
                json={
                    "title": "ZZT Theirs",
                    "expected_amount": "1",
                    "expected_close_date": "2026-11-01",
                    "prospect_name": "ZZT Theirs Prospect",
                },
            ).json()

        with _portal_client(db, contact_a.id) as c:
            listed = c.get(BASE).json()["items"]
            assert {o["id"] for o in listed} == {mine["id"]}
            assert c.get(f"{BASE}/{theirs['id']}").status_code == 404


def test_s2_6_stage_move_not_along_an_edge_is_422_and_lost_needs_a_reason():
    with blank_session() as db:
        _seed(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        _agent(db, contact_id=contact.id)

        with _portal_client(db, contact.id) as c:
            created = c.post(
                BASE,
                json={
                    "title": "ZZT",
                    "expected_amount": "1",
                    "expected_close_date": "2026-11-01",
                    "prospect_name": "ZZT Prospect",
                },
            ).json()

            negotiation_id = _status_id(db, "negotiation")
            res = c.patch(f"{BASE}/{created['id']}", json={"status_id": negotiation_id})
            assert res.status_code == 422, res.text

            lost_id = _status_id(db, "lost")
            no_reason = c.patch(f"{BASE}/{created['id']}", json={"status_id": lost_id})
            assert no_reason.status_code == 422, no_reason.text

            with_reason = c.patch(
                f"{BASE}/{created['id']}", json={"status_id": lost_id, "lost_reason": "no_response"}
            )
            assert with_reason.status_code == 200, with_reason.text
            assert with_reason.json()["outcome"] == "lost"


def test_s2_6_detail_available_transitions_from_new_exclude_the_inactive_proposal():
    with blank_session() as db:
        _seed(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        _agent(db, contact_id=contact.id)

        with _portal_client(db, contact.id) as c:
            created = c.post(
                BASE,
                json={
                    "title": "ZZT",
                    "expected_amount": "1",
                    "expected_close_date": "2026-11-01",
                    "prospect_name": "ZZT Prospect",
                },
            ).json()
            detail = c.get(f"{BASE}/{created['id']}").json()
            keys = {t["key"] for t in detail["available_transitions"]}
            assert keys == {"qualified", "won", "lost"}
            assert "proposal" not in keys


# --------------------------------------------------------------------------- #
# S2-9 (portal half): a stage move is audited to the acting contact
# --------------------------------------------------------------------------- #


def test_s2_9_portal_stage_move_is_audited_to_the_contact():
    from app.models.audit import AuditLog

    with blank_session() as db:
        _seed(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        _agent(db, contact_id=contact.id)

        with _portal_client(db, contact.id) as c:
            created = c.post(
                BASE,
                json={
                    "title": "ZZT",
                    "expected_amount": "1",
                    "expected_close_date": "2026-11-01",
                    "prospect_name": "ZZT Prospect",
                },
            ).json()
            qualified_id = _status_id(db, "qualified")
            res = c.patch(f"{BASE}/{created['id']}", json={"status_id": qualified_id})
            assert res.status_code == 200, res.text

        rows = (
            db.query(AuditLog)
            .filter(
                AuditLog.entity_type == "sales_opportunities",
                AuditLog.entity_id == created["id"],
            )
            .all()
        )
        assert rows, "expected an audit row for the portal stage move"
        assert any(r.contact_id == contact.id for r in rows)


# --------------------------------------------------------------------------- #
# Module gate: `sales` off under strict mode is 403 on every portal route
# --------------------------------------------------------------------------- #


def test_module_gate_403s_every_route_when_sales_is_not_enabled(monkeypatch):
    from app.config import settings
    from app.models.app_modules import AppModuleCatalog, TenantModule
    from app.modules.runtime.installer import DEFAULT_TENANT_ID

    monkeypatch.setattr(settings, "module_guard_strict", True)
    with blank_session() as db:
        _seed(db)
        contact = _contact(db)
        _grant_kind(db, contact.id)
        _agent(db, contact_id=contact.id)

        other_key = f"zzt_other_{_uid()[:6]}"
        db.add(
            AppModuleCatalog(
                id=_uid(), module_key=other_key, display_name="ZZT Other", dependencies=[]
            )
        )
        db.flush()
        db.add(
            TenantModule(
                id=_uid(), tenant_id=DEFAULT_TENANT_ID, module_key=other_key, enabled=True
            )
        )
        db.flush()

        with _portal_client(db, contact.id) as c:
            res = c.get(BASE)
            assert res.status_code == 403, res.text
            assert res.json().get("code") == "MODULE_NOT_ENABLED"

            res2 = c.post(
                BASE,
                json={
                    "title": "ZZT",
                    "expected_amount": "1",
                    "expected_close_date": "2026-11-01",
                    "prospect_name": "ZZT Prospect",
                },
            )
            assert res2.status_code == 403, res2.text
            assert res2.json().get("code") == "MODULE_NOT_ENABLED"


# --------------------------------------------------------------------------- #
# Price tag regression: the lifted resolver still serves the debtor lookup
# --------------------------------------------------------------------------- #


def test_price_tag_debtor_lookup_still_works_after_the_resolver_lift():
    """`PriceTagRequestService.lookup_debtors_for_agent` still serves the debtor list after
    the lift. `agent_for_contact` (the CRM to-do's one-agent answer) still picks the lowest
    code; the portal's debtor list covers both agents (PORTAL-MULTI-AGENT)."""
    from app.services.price_tag_request_service import PriceTagRequestService
    from app.services.sales.portal_agent import agent_for_contact

    with blank_session() as db:
        company_id = _sorento(db)
        contact = _contact(db)
        low = _agent(db, contact_id=contact.id, code=f"AAA-{_uid()[:6]}".upper())
        high = _agent(db, contact_id=contact.id, code=f"ZZZ-{_uid()[:6]}".upper())
        customer = _customer(db, company_id, name="ZZT Debtor", agent_id=low.id)

        resolved = agent_for_contact(db, contact.id)
        assert resolved.id == low.id

        debtors = PriceTagRequestService.lookup_debtors_for_agent(db, contact.id)
        assert any(d["customer_id"] == customer.id for d in debtors)
        assert high.id  # keeps the second row from being an unused fixture
