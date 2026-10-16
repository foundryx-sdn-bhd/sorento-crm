"""Phase 3 fix round 2, should-fix 4: the portal request's company scope must only ever
NARROW, never widen.

`apply_company_scope` (the router-level dependency, `app/main.py`) already resolves the
scope for the WHOLE request from the contact's own `respond_contact_companies` rows before
any route body runs. `_require_agent` used to overwrite that with
`frozenset({agent.company_id}) if agent.company_id else None` - for a SHARED agent
(`company_id` is `None`) that is a literal `None`, which `app.models.base` documents as "no
predicate - all companies". A contact scoped to company A by an admin then had every
company-scoped query for the rest of the request - the customer lookup inside
`_resolve_company`, `customer_options`, the opportunity list - run wide open.

These are request-level tests (TestClient over the real `/api/v1/public/portal` app, a real
`PortalToken` row and real `respond_contact_companies` rows) - a service-level test can prove
`_resolve_company` alone behaves; only a full request proves the DEPENDENCY CHAIN (
`apply_company_scope` -> `_require_agent`) does not clobber what the first one resolved.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves the circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from tests._pg_fixture import blank_session
from tests._portal_grant import grant_portal_forms

BASE = "/api/v1/public/portal/sales-opportunities"


def _uid() -> str:
    return str(uuid.uuid4())


def _company(db, *, name: str) -> str:
    from app.models.company import Company

    company = Company(id=_uid(), name=name, code=f"ZZT{_uid()[:6].upper()}")
    db.add(company)
    db.flush()
    return company.id


def _contact(db, *, name: str = "ZZT Fix2-4 Salesperson"):
    from app.models.access import RespondContact

    contact = RespondContact(id=_uid(), phone_number=f"+60{_uid().replace('-', '')[:9]}", name=name)
    db.add(contact)
    db.flush()
    return contact


def _scope_contact_to(db, contact_id: str, company_id: str) -> None:
    from app.models.company import RespondContactCompany

    db.add(RespondContactCompany(id=_uid(), respond_contact_id=contact_id, company_id=company_id))
    db.flush()


def _agent(db, *, contact_id: str, company_id, active: bool = True):
    from app.models.sales_agent import SalesAgent

    agent = SalesAgent(
        id=_uid(),
        sales_agent=f"ZZO-{_uid()[:8]}".upper(),
        description="ZZT Fix2-4 Agent",
        is_active=active,
        contact_id=contact_id,
        company_id=company_id,
    )
    db.add(agent)
    db.flush()
    return agent


def _customer(db, company_id, *, name: str = "ZZT Fix2-4 Customer", agent_id=None):
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


def _real_token(db, contact_id: str):
    from app.models.portal import PortalToken

    token = PortalToken(
        id=_uid(),
        token=f"ZZT-fix2-4-{uuid.uuid4().hex}",
        contact_id=contact_id,
        space_id="zzt-fix2-4-space",
        expires_at=datetime.utcnow() + timedelta(days=30),
        verified_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(token)
    db.flush()
    return token


@pytest.fixture
def api():
    from app.database import get_db
    from app.services.sales import sales_seed_service

    with blank_session() as db:
        sales_seed_service.run(db)
        db.flush()

        def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db
        try:
            yield db
        finally:
            app.dependency_overrides.clear()


def test_fix2_4a_customer_in_another_company_assigned_to_the_agent_is_still_422(api):
    """A's own agent, B's own customer, somehow already assigned to that agent (a row an
    admin should never create, but a request-level guard must not trust it): the contact is
    scoped to A only, so B is invisible to it regardless. Kept as a regression companion to
    4b below, which is the one the fix actually changes the outcome of."""
    db = api
    company_a = _company(db, name="ZZT Fix2-4a Company A")
    company_b = _company(db, name="ZZT Fix2-4a Company B")
    contact = _contact(db)
    _scope_contact_to(db, contact.id, company_a)
    grant_portal_forms(db, contact.id, ["sales_opportunity"])
    agent = _agent(db, contact_id=contact.id, company_id=company_a)
    customer_in_b = _customer(db, company_b, agent_id=agent.id)
    token = _real_token(db, contact.id)

    with TestClient(app) as c:
        res = c.post(
            BASE,
            json={
                "title": "ZZT Fix2-4a Opp",
                "expected_amount": "500",
                "expected_close_date": "2026-11-01",
                "customer_id": customer_in_b.id,
            },
            headers={"X-Portal-Token": token.token},
        )
    assert res.status_code == 422, res.text

    from app.models.sales import SalesOpportunity
    from app.models.base import company_scope as _unscoped

    with _unscoped(db, None):
        landed = (
            db.query(SalesOpportunity).filter(SalesOpportunity.company_id == company_b).count()
        )
    assert landed == 0, "nothing must land in company B"


def test_fix2_4b_shared_agent_customer_options_stay_within_the_contacts_companies(api):
    """The bug: a SHARED agent (no `company_id`) used to reset the whole request's scope to
    `None` - "no predicate", i.e. every company - clobbering the `A`-only scope
    `apply_company_scope` had already resolved for this contact. Seed a company-B customer
    assigned to this same shared agent (plausible - a shared agent serves every company) and
    prove `customer-options` never surfaces it to a contact scoped to A alone."""
    db = api
    company_a = _company(db, name="ZZT Fix2-4b Company A")
    company_b = _company(db, name="ZZT Fix2-4b Company B")
    contact = _contact(db)
    _scope_contact_to(db, contact.id, company_a)
    grant_portal_forms(db, contact.id, ["sales_opportunity"])
    shared_agent = _agent(db, contact_id=contact.id, company_id=None)
    customer_in_a = _customer(db, company_a, name="ZZT Fix2-4b In Scope", agent_id=shared_agent.id)
    customer_in_b = _customer(db, company_b, name="ZZT Fix2-4b Out Of Scope", agent_id=shared_agent.id)
    token = _real_token(db, contact.id)

    with TestClient(app) as c:
        res = c.get(f"{BASE}/customer-options", headers={"X-Portal-Token": token.token})
    assert res.status_code == 200, res.text
    names = {item["customer_name"] for item in res.json()["items"]}
    assert customer_in_a.customer_name in names
    assert customer_in_b.customer_name not in names, (
        "a shared agent must not widen the request past the contact's own company scope"
    )


def test_fix2_4c_shared_agent_removing_the_narrowing_guard_turns_4b_red(api):
    """Mutation check (coordinator's ask): monkeypatch `_require_agent` back to the old
    unconditional overwrite and confirm 4b's own assertion actually fails - proves 4b
    exercises the real guard, not a fixture accident."""
    db = api
    company_a = _company(db, name="ZZT Fix2-4c Company A")
    company_b = _company(db, name="ZZT Fix2-4c Company B")
    contact = _contact(db)
    _scope_contact_to(db, contact.id, company_a)
    grant_portal_forms(db, contact.id, ["sales_opportunity"])
    shared_agent = _agent(db, contact_id=contact.id, company_id=None)
    _customer(db, company_a, name="ZZT Fix2-4c In Scope", agent_id=shared_agent.id)
    customer_in_b = _customer(db, company_b, name="ZZT Fix2-4c Out Of Scope", agent_id=shared_agent.id)
    token = _real_token(db, contact.id)

    from app.api.v1.public import portal_sales_opportunity as mod
    from app.models.base import set_company_scope

    original = mod._require_agents

    def _old_broken_require_agent(db_, token_):
        from app.services.portal_form_visibility_service import require_form_visible
        from app.services.sales.portal_agent import agent_for_contact

        mod._require_module_enabled(db_)
        require_form_visible(db_, token_.contact_id, mod._FORM_TYPE)
        agent = agent_for_contact(db_, token_.contact_id)
        db_.info["actor_contact_id"] = str(token_.contact_id)
        # The pre-fix behaviour: unconditional overwrite, `None` for a shared agent.
        set_company_scope(db_, frozenset({agent.company_id}) if agent.company_id else None)
        return [agent]

    mod._require_agents = _old_broken_require_agent
    try:
        with TestClient(app) as c:
            res = c.get(f"{BASE}/customer-options", headers={"X-Portal-Token": token.token})
        assert res.status_code == 200, res.text
        names = {item["customer_name"] for item in res.json()["items"]}
        assert customer_in_b.customer_name in names, (
            "with the old unconditional overwrite restored, the out-of-scope customer "
            "leaks back in - proving the real fix in `_require_agents` is what fix2-4b pins"
        )
    finally:
        mod._require_agents = original
