"""PORTAL-MULTI-AGENT (PLAN-portal-multi-agent-16oct.md, UAC AC-PMA1 to AC-PMA7).

Owner rule (16 Oct 2026): a portal login (contact) linked to more than one sales agent sees the
customers of ALL those agents together, everywhere in the portal. Inactive agents' customers are
left out; a contact with no active linked agent is 403 `NOT_A_SALES_AGENT`.

Run:
    DATABASE_URL=<throwaway db> venv/bin/pytest tests/test_portal_multi_agent.py -q
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.models.access import RespondContactCustomer
from app.models.base import set_company_scope
from app.models.chat_history import ChatHistory
from app.models.order import Customer, Order
from app.models.price_tag import ContactPortalFormOverride
from app.models.sales_agent import SalesAgent
from app.models.stock_ask import StockAsk

from ._pg_fixture import blank_session

SORENTO = "00000000-0000-0000-0000-000000000001"
PORTAL = "/api/v1/public/portal"
ASKS = f"{PORTAL}/customer-asks"
CONVERSATIONS = f"{PORTAL}/conversations"
OPPORTUNITIES = f"{PORTAL}/sales-opportunities"
BASE_TIME = datetime(2026, 10, 1, 3, 0, 0)


def _uid() -> str:
    return str(uuid.uuid4())


class _DeadRespondClient:
    def list_messages(self, *a, **k):
        raise RuntimeError("Respond.io unreachable in tests")


@pytest.fixture(autouse=True)
def _local_lane_only():
    with patch("app.services.integration_service.RespondClient") as client_cls:
        client_cls.return_value = _DeadRespondClient()
        client_cls.for_identifier.return_value = _DeadRespondClient()
        client_cls.for_contact_id.return_value = _DeadRespondClient()
        yield client_cls


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _contact(db, name: str) -> str:
    cid = _uid()
    db.execute(
        text(
            "INSERT INTO respond_contacts (id, respond_io_id, phone_number, name, session_vars) "
            "VALUES (:id, :rid, :phone, :name, CAST('{}' AS jsonb))"
        ),
        {"id": cid, "rid": f"ZZT-{_uid()[:8]}", "phone": f"+6005{uuid.uuid4().int % 10**7:07d}", "name": name},
    )
    return cid


def _agent(db, contact_id: str | None, code: str, *, active: bool = True) -> SalesAgent:
    row = SalesAgent(
        id=_uid(),
        sales_agent=f"ZZT{code}{_uid()[:4]}".upper(),
        contact_id=contact_id,
        company_id=SORENTO,
        is_active=active,
    )
    db.add(row)
    db.flush()
    return row


def _switch(db, contact_id: str, form_type: str) -> None:
    db.add(ContactPortalFormOverride(contact_id=contact_id, form_type=form_type, is_enabled=True))
    db.flush()


def _customer(db, name: str, agent: SalesAgent) -> Customer:
    row = Customer(
        id=_uid(),
        customer_code=f"ZZT-C-{_uid()[:6]}",
        customer_name=name,
        company_id=SORENTO,
        sales_agent_id=agent.id,
    )
    db.add(row)
    db.flush()
    return row


def _ask(db, customer: Customer, contact_id: str, code: str) -> StockAsk:
    row = StockAsk(
        id=_uid(),
        company_id=SORENTO,
        customer_id=customer.id,
        contact_id=contact_id,
        product_code=code,
        quantity=10,
        branch="no_incoming",
        answer_summary=f"{code} x 10: no stock.",
        notified_agent=True,
        created_at=datetime.utcnow() - timedelta(minutes=5),
    )
    db.add(row)
    db.flush()
    return row


def _chat(db, dealer_contact: str, customer: Customer, body: str, minutes: int) -> None:
    """The dealer contact is linked to the customer and has one message."""
    db.add(
        RespondContactCustomer(
            id=_uid(), contact_id=dealer_contact, customer_id=customer.id, is_primary=True, company_id=SORENTO
        )
    )
    rid, phone = db.execute(
        text("SELECT respond_io_id, phone_number FROM respond_contacts WHERE id = :id"), {"id": dealer_contact}
    ).one()
    db.add(
        ChatHistory(
            channel="whatsapp",
            contact_id=rid,
            phone_number=phone,
            message=body,
            sent_at=BASE_TIME + timedelta(minutes=minutes),
            type="incoming",
            message_id=str(1_700_000_000_000 + minutes),
        )
    )
    db.flush()


@pytest.fixture
def world():
    """Contact "Agent Jayson" linked to two agents (JAYDEN, WT), the dev case in miniature,
    plus an unrelated agent X on another contact."""
    with blank_session() as db:
        set_company_scope(db, frozenset({SORENTO}))
        me = _contact(db, "Agent Jayson")
        other = _contact(db, "Agent Other")
        stranger = _contact(db, "Not An Agent")
        jayden = _agent(db, me, "AJAYDEN")  # sorts first by code
        wt = _agent(db, me, "BWT")
        x = _agent(db, other, "XOTHER")
        for kind in ("customer_asks", "conversation"):
            _switch(db, me, kind)
            _switch(db, stranger, kind)
        ca = _customer(db, "Four Smart Plus", jayden)
        cb = _customer(db, "I Bath Studio", wt)
        cx = _customer(db, "Somebody Elses Trading", x)
        dealer_a, dealer_b, dealer_x = (_contact(db, n) for n in ("Dealer A", "Dealer B", "Dealer X"))
        _chat(db, dealer_a, ca, "hi from A", 1)
        _chat(db, dealer_b, cb, "hi from B", 2)
        _chat(db, dealer_x, cx, "hi from X", 3)
        w = {
            "db": db,
            "me": me,
            "stranger": stranger,
            "jayden": jayden,
            "wt": wt,
            "x": x,
            "ca": ca,
            "cb": cb,
            "cx": cx,
            "dealer_a": dealer_a,
            "dealer_b": dealer_b,
            "dealer_x": dealer_x,
            "ask_a": _ask(db, ca, dealer_a, "SRT-A"),
            "ask_b": _ask(db, cb, dealer_b, "SRT-B"),
            "ask_x": _ask(db, cx, dealer_x, "SRT-X"),
        }
        db.commit()
        yield w


def _client(w, contact_id: str) -> TestClient:
    from app.api.v1.public.portal import get_portal_token
    from app.database import get_db
    from app.models.portal import PortalToken
    from app.services.company_scope_resolver import apply_company_scope

    db = w["db"]

    def _override_get_db():
        yield db

    async def _override_scope():
        set_company_scope(db, frozenset({SORENTO}))
        return frozenset({SORENTO})

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[apply_company_scope] = _override_scope
    app.dependency_overrides[get_portal_token] = lambda: PortalToken(
        id=_uid(), contact_id=contact_id, space_id="zzt-space"
    )
    return TestClient(app, headers={"X-Portal-Token": "zzt-token"}, raise_server_exceptions=False)


def _deactivate(w, *agents: SalesAgent) -> None:
    for agent in agents:
        agent.is_active = False
    w["db"].flush()


# --------------------------------------------------------------------------- #
# AC-PMA1: the resolver answers every active linked agent
# --------------------------------------------------------------------------- #


def test_pma1_agents_for_contact_returns_every_active_linked_agent_by_code(world):
    from app.services.sales.portal_agent import agents_for_contact

    db = world["db"]
    assert [a.id for a in agents_for_contact(db, world["me"])] == [world["jayden"].id, world["wt"].id]
    _deactivate(world, world["wt"])
    assert [a.id for a in agents_for_contact(db, world["me"])] == [world["jayden"].id]
    _deactivate(world, world["jayden"])
    assert agents_for_contact(db, world["me"]) == []
    assert agents_for_contact(db, world["stranger"]) == []


def test_pma7_crm_agent_for_user_still_answers_one_agent(world):
    """No CRM screen change: the staff to-do's resolver keeps its one-agent answer."""
    from app.services.sales.portal_agent import agent_for_contact

    assert agent_for_contact(world["db"], world["me"]).id == world["jayden"].id


# --------------------------------------------------------------------------- #
# AC-PMA2: Customer asks
# --------------------------------------------------------------------------- #


def test_pma2_customer_asks_list_and_todo_hold_both_agents_customers(world):
    client = _client(world, world["me"])
    listed = client.get(ASKS)
    assert listed.status_code == 200, listed.text
    assert {r["product_code"] for r in listed.json()["data"]} == {"SRT-A", "SRT-B"}

    todo = client.get(f"{ASKS}/todo")
    assert todo.status_code == 200, todo.text
    assert {r["product_code"] for r in todo.json()["open"]} == {"SRT-A", "SRT-B"}


def test_pma2_customer_asks_second_agents_ask_opens_and_updates(world):
    client = _client(world, world["me"])
    ask_b = world["ask_b"].id
    assert client.get(f"{ASKS}/{ask_b}/conversation").status_code == 200
    patched = client.patch(f"{ASKS}/{ask_b}", json={"state": "done"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["state"] == "done"
    assert client.patch(f"{ASKS}/{world['ask_x'].id}", json={"state": "done"}).status_code == 404


def test_pma2_inactive_agents_customers_drop_out_of_customer_asks(world):
    _deactivate(world, world["jayden"])  # the lowest code: today the one agent answered
    client = _client(world, world["me"])
    listed = client.get(ASKS)
    assert listed.status_code == 200, listed.text
    assert {r["product_code"] for r in listed.json()["data"]} == {"SRT-B"}
    assert client.patch(f"{ASKS}/{world['ask_a'].id}", json={"state": "done"}).status_code == 404


# --------------------------------------------------------------------------- #
# AC-PMA3: Conversations
# --------------------------------------------------------------------------- #


def test_pma3_conversations_hold_both_agents_customers(world):
    client = _client(world, world["me"])
    listed = client.get(CONVERSATIONS)
    assert listed.status_code == 200, listed.text
    codes = {r["customer_code"] for r in listed.json()["data"]}
    assert codes == {world["ca"].customer_code, world["cb"].customer_code}

    assert client.get(f"{CONVERSATIONS}/{world['dealer_b']}/page").status_code == 200
    assert client.get(f"{CONVERSATIONS}/{world['dealer_x']}/page").status_code == 404


def test_pma3_inactive_agents_customers_drop_out_of_conversations(world):
    _deactivate(world, world["wt"])
    client = _client(world, world["me"])
    listed = client.get(CONVERSATIONS)
    assert listed.status_code == 200, listed.text
    assert {r["customer_code"] for r in listed.json()["data"]} == {world["ca"].customer_code}
    assert client.get(f"{CONVERSATIONS}/{world['dealer_b']}/page").status_code == 404


# --------------------------------------------------------------------------- #
# AC-PMA4: Price tag debtor lookup
# --------------------------------------------------------------------------- #


def test_pma4_price_tag_debtors_hold_both_agents_customers_and_order_debtors(world):
    from app.services.price_tag_request_service import PriceTagRequestService

    db = world["db"]
    recent = date.today() - timedelta(days=30)
    for agent, code in ((world["jayden"], "ZZT-ORD-A"), (world["wt"], "ZZT-ORD-B"), (world["x"], "ZZT-ORD-X")):
        db.add(
            Order(
                order_number=f"ZZT-{_uid()[:8]}",
                order_date=recent,
                debtor_code=code,
                debtor_name=f"{code} name",
                agent=agent.sales_agent,
                company_id=SORENTO,
            )
        )
    db.flush()

    debtors = PriceTagRequestService.lookup_debtors_for_agent(db, world["me"])
    codes = {d["debtor_code"] for d in debtors}
    assert {world["ca"].customer_code, world["cb"].customer_code, "ZZT-ORD-A", "ZZT-ORD-B"} <= codes
    assert world["cx"].customer_code not in codes
    assert "ZZT-ORD-X" not in codes


# --------------------------------------------------------------------------- #
# AC-PMA6: zero active agents
# --------------------------------------------------------------------------- #


def test_pma6_no_active_linked_agent_is_403_everywhere_and_hides_the_agent_kinds(world):
    from app.services.portal_form_visibility_service import resolve_visible_form_types

    _deactivate(world, world["jayden"], world["wt"])
    client = _client(world, world["me"])
    for url in (ASKS, f"{ASKS}/todo", CONVERSATIONS):
        res = client.get(url)
        assert res.status_code == 403, (url, res.text)
        assert "NOT_A_SALES_AGENT" in res.text, url
    visible = resolve_visible_form_types(world["db"], world["me"])
    assert "customer_asks" not in visible
    assert "conversation" not in visible


def test_pma6_one_active_agent_keeps_the_agent_kinds_visible(world):
    from app.services.portal_form_visibility_service import resolve_visible_form_types

    _deactivate(world, world["jayden"])
    visible = resolve_visible_form_types(world["db"], world["me"])
    assert {"customer_asks", "conversation"} <= visible


# --------------------------------------------------------------------------- #
# AC-PMA5: Sales opportunity
# --------------------------------------------------------------------------- #


def _opp_world(db):
    from tests.test_portal_sales_opportunities_s2 import _agent as opp_agent
    from tests.test_portal_sales_opportunities_s2 import _contact as opp_contact
    from tests.test_portal_sales_opportunities_s2 import _customer as opp_customer
    from tests.test_portal_sales_opportunities_s2 import _grant_kind, _seed, _sorento

    _seed(db)
    company_id = _sorento(db)
    me = opp_contact(db, name="ZZT Agent Jayson")
    _grant_kind(db, me.id)
    jayden = opp_agent(db, contact_id=me.id, code=f"AAA-{_uid()[:6]}".upper())
    wt = opp_agent(db, contact_id=me.id, code=f"BBB-{_uid()[:6]}".upper())
    x = opp_agent(db, code=f"XXX-{_uid()[:6]}".upper())
    return {
        "company_id": company_id,
        "me": me,
        "jayden": jayden,
        "wt": wt,
        "ca": opp_customer(db, company_id, name="ZZT Four Smart Plus", agent_id=jayden.id),
        "cb": opp_customer(db, company_id, name="ZZT I Bath Studio", agent_id=wt.id),
        "cx": opp_customer(db, company_id, name="ZZT Somebody Elses", agent_id=x.id),
    }


def _new_opp(c, **body):
    payload = {"title": "ZZT Opp", "expected_amount": "100", "expected_close_date": "2026-11-01", **body}
    return c.post(OPPORTUNITIES, json=payload)


def test_pma5_customer_options_hold_both_agents_customers():
    from tests.test_portal_sales_opportunities_s2 import _portal_client

    with blank_session() as db:
        w = _opp_world(db)
        with _portal_client(db, w["me"].id) as c:
            res = c.get(f"{OPPORTUNITIES}/customer-options", params={"q": "ZZT"})
            assert res.status_code == 200, res.text
            ids = {i["customer_id"] for i in res.json()["items"]}
            assert {w["ca"].id, w["cb"].id} <= ids
            assert w["cx"].id not in ids

            exact = c.get(f"{OPPORTUNITIES}/customer-options", params={"q": "ZZT I Bath Studio"}).json()
            assert exact["blocked"] is None
            assert [i["customer_id"] for i in exact["items"]] == [w["cb"].id]


def test_pma5_create_list_open_and_edit_across_both_agents():
    from tests.test_portal_sales_opportunities_s2 import _portal_client

    with blank_session() as db:
        w = _opp_world(db)
        with _portal_client(db, w["me"].id) as c:
            for_b = _new_opp(c, customer_id=w["cb"].id)
            assert for_b.status_code == 201, for_b.text
            assert for_b.json()["sales_agent_id"] == w["wt"].id  # the customer's own agent

            prospect = _new_opp(c, prospect_name="ZZT Brand New Prospect")
            assert prospect.status_code == 201, prospect.text
            assert prospect.json()["sales_agent_id"] == w["jayden"].id  # first by code

            foreign = _new_opp(c, customer_id=w["cx"].id)
            assert foreign.status_code == 422, foreign.text
            assert "CUSTOMER_NOT_YOURS" in foreign.text

            listed = c.get(OPPORTUNITIES)
            assert listed.status_code == 200, listed.text
            assert {o["id"] for o in listed.json()["items"]} == {for_b.json()["id"], prospect.json()["id"]}

            opp_b = for_b.json()["id"]
            assert c.get(f"{OPPORTUNITIES}/{opp_b}").status_code == 200
            moved = c.patch(f"{OPPORTUNITIES}/{opp_b}", json={"customer_id": w["ca"].id})
            assert moved.status_code == 200, moved.text
            assert moved.json()["customer_id"] == w["ca"].id
            assert moved.json()["sales_agent_id"] == w["jayden"].id  # follows the customer


def test_pma5_works_when_the_lowest_code_agent_is_inactive():
    from tests.test_portal_sales_opportunities_s2 import _portal_client

    with blank_session() as db:
        w = _opp_world(db)
        w["jayden"].is_active = False
        db.flush()
        with _portal_client(db, w["me"].id) as c:
            listed = c.get(OPPORTUNITIES)
            assert listed.status_code == 200, listed.text
            created = _new_opp(c, customer_id=w["cb"].id)
            assert created.status_code == 201, created.text
            assert created.json()["sales_agent_id"] == w["wt"].id
            assert _new_opp(c, customer_id=w["ca"].id).status_code == 422  # inactive agent's customer


def test_pma6_sales_opportunity_with_no_active_agent_is_403():
    from tests.test_portal_sales_opportunities_s2 import _portal_client

    with blank_session() as db:
        w = _opp_world(db)
        w["jayden"].is_active = False
        w["wt"].is_active = False
        db.flush()
        with _portal_client(db, w["me"].id) as c:
            res = c.get(OPPORTUNITIES)
            assert res.status_code == 403, res.text
            assert "NOT_A_SALES_AGENT" in res.text


def test_pma5_my_targets_hold_both_agents_targets(monkeypatch):
    from app.schemas.sales import SalesTargetCreate
    from app.services.sales import target_service
    from tests.test_portal_sales_opportunities_s2 import _portal_client

    monkeypatch.setattr(target_service, "_today", lambda: date(2026, 10, 15))
    with blank_session() as db:
        w = _opp_world(db)
        ids = []
        for agent, name in ((w["jayden"], "ZZT Jayden Q4"), (w["wt"], "ZZT WT Q4")):
            target = target_service.create_target(
                db,
                SalesTargetCreate(
                    subject_kind="agent", sales_agent_id=agent.id, name=name, metric="amount",
                    basis="ordered", product_scope="all", start_date=date(2026, 10, 1),
                    end_date=date(2026, 12, 31), target_value=1000,
                ),
            )
            db.flush()
            ids.append(target.id)
        with _portal_client(db, w["me"].id) as c:
            res = c.get(f"{OPPORTUNITIES}/my-targets")
            assert res.status_code == 200, res.text
            assert {t["target_id"] for t in res.json()["targets"]} == set(ids)


# --------------------------------------------------------------------------- #
# Security review (16 Oct 2026): the agents' companies never widen the contact's own scope
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "junk",
    [{"Authorization": "Bearer junk"}, {"X-API-Key": "junk"}],
    ids=["junk-bearer", "junk-api-key"],
)
def test_pma_scope_a_junk_auth_header_never_widens_to_another_agents_company(junk):
    """A junk Bearer / X-API-Key next to a valid portal token resolves the request scope to
    UNSET. The agents' companies (A and B) must then be bounded by the contact's OWN companies
    (`respond_contact_companies`: A only), never set as they are."""
    from app.database import get_db
    from app.services.sales import sales_seed_service
    from tests._portal_grant import grant_portal_forms
    from tests.test_fix2_4_portal_company_scope_narrowing import (
        BASE,
        _agent,
        _company,
        _contact,
        _customer,
        _real_token,
        _scope_contact_to,
    )

    with blank_session() as db:
        sales_seed_service.run(db)
        db.flush()
        company_a = _company(db, name="ZZT PMA Company A")
        company_b = _company(db, name="ZZT PMA Company B")
        contact = _contact(db)
        _scope_contact_to(db, contact.id, company_a)
        grant_portal_forms(db, contact.id, ["sales_opportunity"])
        agent_a = _agent(db, contact_id=contact.id, company_id=company_a)
        agent_b = _agent(db, contact_id=contact.id, company_id=company_b)
        in_a = _customer(db, company_a, name="ZZT PMA In A", agent_id=agent_a.id)
        in_b = _customer(db, company_b, name="ZZT PMA In B", agent_id=agent_b.id)
        token = _real_token(db, contact.id)

        def _override_get_db():
            yield db

        app.dependency_overrides[get_db] = _override_get_db
        headers = {"X-Portal-Token": token.token, **junk}
        with TestClient(app) as c:
            options = c.get(f"{BASE}/customer-options", params={"q": "ZZT PMA"}, headers=headers)
            created = c.post(
                BASE,
                json={
                    "title": "ZZT PMA widen",
                    "expected_amount": "1",
                    "expected_close_date": "2026-11-01",
                    "customer_id": in_b.id,
                },
                headers=headers,
            )
        assert options.status_code == 200, options.text
        names = {i["customer_name"] for i in options.json()["items"]}
        assert in_b.customer_name not in names
        assert in_a.customer_name in names
        assert created.status_code == 422, created.text


def test_pma4_order_debtors_match_the_agent_code_in_the_agents_own_company():
    """Codes are unique per company only (`uq_sales_agents_company_sales_agent`): the order
    debtors of a same-code agent in ANOTHER company are not this contact's. A shared agent
    (no company) still matches its code in every company."""
    from app.models.company import Company
    from app.services.price_tag_request_service import PriceTagRequestService

    with blank_session() as db:
        company_a = Company(id=_uid(), name="ZZT PMA Debtor A", code=f"ZZT{_uid()[:6].upper()}")
        company_c = Company(id=_uid(), name="ZZT PMA Debtor C", code=f"ZZT{_uid()[:6].upper()}")
        db.add_all([company_a, company_c])
        db.flush()
        set_company_scope(db, None)
        me = _contact(db, "Agent Alan")
        code = f"ZZTALAN{_uid()[:4]}".upper()
        shared_code = f"ZZTSHARED{_uid()[:4]}".upper()
        db.add_all(
            [
                SalesAgent(id=_uid(), sales_agent=code, contact_id=me, company_id=company_c.id),
                SalesAgent(id=_uid(), sales_agent=code, contact_id=None, company_id=company_a.id),
                SalesAgent(id=_uid(), sales_agent=shared_code, contact_id=me, company_id=None),
            ]
        )
        recent = date.today() - timedelta(days=30)
        for company, agent_code, debtor in (
            (company_c, code, "ZZT-DEB-MINE-C"),
            (company_a, code, "ZZT-DEB-OTHER-ALAN-A"),
            (company_a, shared_code, "ZZT-DEB-SHARED-A"),
            (company_c, shared_code, "ZZT-DEB-SHARED-C"),
        ):
            db.add(
                Order(
                    order_number=f"ZZT-{_uid()[:8]}",
                    order_date=recent,
                    debtor_code=debtor,
                    debtor_name=debtor,
                    agent=agent_code,
                    company_id=company.id,
                )
            )
        db.flush()

        codes = {d["debtor_code"] for d in PriceTagRequestService.lookup_debtors_for_agent(db, me)}
        assert {"ZZT-DEB-MINE-C", "ZZT-DEB-SHARED-A", "ZZT-DEB-SHARED-C"} <= codes
        assert "ZZT-DEB-OTHER-ALAN-A" not in codes
