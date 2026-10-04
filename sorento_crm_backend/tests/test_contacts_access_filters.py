"""Lane CONTACT-BULK-ACCESS, slice S2 (A2): contacts list access columns and filters.

UAC ``documentation/plans/contacts/contact-bulk-access-3oct-acceptance-criteria.md`` A2.1, A2.3-A2.6.
Red tests written before the implementation. Postgres only (``blank_session``); every row is
seeded here and assertions are scoped to the seeded names through the ``query`` param.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

# MUST be first app import - resolves a circular import in app.modules.runtime.guards
from app.main import app  # noqa: E402

from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.models.access import (
    AccessAgent,
    ContactAccessType,
    ContactAgentAccess,
    ContactFieldReveal,
    RespondContact,
    RespondContactCustomer,
)
from app.models.base import set_company_scope
from app.models.order import Customer
from app.services.company_scope import DEFAULT_COMPANY_ID
from app.services.company_scope_resolver import apply_company_scope
from app.services.user_service import UserPermissionService

from tests._mc_lookup_seed import MOCHA_ID, seed_mocha
from tests._pg_fixture import blank_session, unique_code

SORENTO = DEFAULT_COMPANY_ID
CONTACTS = "/api/v1/user-management/contacts/"
VIEW = "user_management.contacts.view"
COST = "purchase_orders.cost"
SELLABLE = "inventory.sellable"


@pytest.fixture
def db():
    with blank_session() as session:
        seed_mocha(session)
        set_company_scope(session, frozenset({SORENTO, MOCHA_ID}))
        yield session


@pytest.fixture
def state():
    return {"scope": frozenset({SORENTO})}


@pytest.fixture
def client(db, state, monkeypatch):
    from app.models.user import User

    actor = User(id=str(uuid.uuid4()), email=f"zzt-{uuid.uuid4().hex[:8]}@test.com", name="ZZT Actor")
    db.add(actor)
    db.flush()
    principal = {"id": actor.id, "email": actor.email}

    def _override_db():
        yield db

    async def _override_scope():
        set_company_scope(db, state["scope"])
        return state["scope"]

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_current_user_or_api_key] = lambda: principal
    app.dependency_overrides[apply_company_scope] = _override_scope
    monkeypatch.setattr(
        UserPermissionService,
        "check_user_has_permission",
        lambda self, uid, slug: slug == VIEW,
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _type(db, tag):
    code = unique_code(f"zat{tag}")
    row = ContactAccessType(code=code, name=f"ZZT type {code}", is_active=True, sort_order=1)
    db.add(row)
    db.flush()
    return row


def _agent(db, tag):
    code = unique_code(f"zag{tag}")
    row = AccessAgent(code=code, name=f"ZZT agent {code}")
    db.add(row)
    db.flush()
    return row


def _contact(
    db,
    tag,
    label,
    *,
    types=(),
    tier=None,
    reveals=(),
    revoked=(),
    stock=True,
    notify=False,
    packing=False,
    eta=True,
    escalation=True,
    agents=(),
    memory=None,
):
    profile = {"language": "en"}
    if tier is not None:
        profile["tier"] = tier
    row = RespondContact(
        id=str(uuid.uuid4()),
        phone_number=f"60{uuid.uuid4().int % 10**8 + 10**8}",
        name=f"ZZT {tag}-{label} {unique_code('c')}",
        chatbot_profile=profile,
        chatbot_memory_level=memory,
        chatbot_stock_allowed=stock,
        notify_salesman=notify,
        packing_list_allowed=packing,
        chatbot_eta_offset_applied=eta,
        escalation_allowed=escalation,
    )
    db.add(row)
    db.flush()
    row.access_types = list(types)
    db.flush()
    for key in reveals:
        db.add(ContactFieldReveal(respond_contact_id=row.id, field_key=key, granted=True))
    for key in revoked:
        db.add(ContactFieldReveal(respond_contact_id=row.id, field_key=key, granted=False))
    for agent, allowed, vfrom, vto in agents:
        db.add(
            ContactAgentAccess(
                respond_contact_id=row.id,
                respond_contact_phone=row.phone_number,
                agent_id=agent.id,
                is_allowed=allowed,
                valid_from=vfrom,
                valid_to=vto,
            )
        )
    db.flush()
    return row


def _customer(db, company_id=SORENTO):
    row = Customer(customer_code=unique_code("ZZT-C"), customer_name="ZZT cust", company_id=company_id)
    db.add(row)
    db.flush()
    return row


def _link(db, contact, customer):
    db.add(
        RespondContactCustomer(
            contact_id=contact.id, customer_id=customer.id, company_id=customer.company_id, source="manual"
        )
    )
    db.flush()


def _list(client, **params):
    return client.get(CONTACTS, params={"limit": 1000, **params})


def _ids(resp):
    assert resp.status_code == 200, resp.text
    return {row["id"] for row in resp.json()["data"]}


@pytest.fixture
def seeded(db):
    tag = unique_code("F", alpha=True)
    ta, tb = _type(db, "a"), _type(db, "b")
    cust1, cust2 = _customer(db), _customer(db)
    c1 = _contact(db, tag, "one", types=[ta], tier="dealer", reveals=[COST], escalation=True, packing=True, stock=True)
    c2 = _contact(db, tag, "two", types=[tb], tier="office", escalation=False, packing=False, stock=True)
    c3 = _contact(db, tag, "three", types=[ta, tb], tier="end_user", revoked=[COST], escalation=True, packing=False, stock=False)
    c4 = _contact(db, tag, "four", escalation=True, packing=False, stock=True)
    _link(db, c1, cust1)
    _link(db, c2, cust1)
    _link(db, c2, cust2)
    return dict(tag=tag, ta=ta, tb=tb, c1=c1, c2=c2, c3=c3, c4=c4, cust1=cust1, cust2=cust2)


# ---------------------------------------------------------------- A2.1


def test_a2_1_rows_carry_chatbot_tier_and_cost_visible(client, seeded):
    body = _list(client, query=seeded["tag"]).json()
    by_id = {r["id"]: r for r in body["data"]}
    assert set(by_id) == {seeded[k].id for k in ("c1", "c2", "c3", "c4")}
    assert by_id[seeded["c1"].id]["chatbot_tier"] == "dealer"
    assert by_id[seeded["c2"].id]["chatbot_tier"] == "office"
    assert by_id[seeded["c3"].id]["chatbot_tier"] == "end_user"
    assert by_id[seeded["c4"].id]["chatbot_tier"] is None
    assert by_id[seeded["c1"].id]["cost_visible"] is True
    assert by_id[seeded["c2"].id]["cost_visible"] is False  # no reveal row at all
    assert by_id[seeded["c3"].id]["cost_visible"] is False  # row exists but revoked
    assert by_id[seeded["c4"].id]["cost_visible"] is False


# ---------------------------------------------------------------- A2.3 / A2.4


def _case(params, *names):
    return pytest.param(params, set(names), id="+".join(f"{k}={v}" for k, v in params.items()))


FILTER_CASES = [
    _case({"tier": "dealer"}, "c1"),
    _case({"tier": "office"}, "c2"),
    _case({"tier": "end_user"}, "c3"),
    _case({"tier": "none"}, "c4"),
    _case({"cost": "yes"}, "c1"),
    _case({"cost": "no"}, "c2", "c3", "c4"),
    _case({"escalation": "yes"}, "c1", "c3", "c4"),
    _case({"escalation": "no"}, "c2"),
    _case({"packing_list": "yes"}, "c1"),
    _case({"packing_list": "no"}, "c2", "c3", "c4"),
    _case({"stock": "yes"}, "c1", "c2", "c4"),
    _case({"stock": "no"}, "c3"),
    # two filters AND together
    _case({"tier": "dealer", "cost": "yes"}, "c1"),
    _case({"stock": "yes", "escalation": "yes"}, "c1", "c4"),
    _case({"cost": "no", "packing_list": "no", "escalation": "yes"}, "c3", "c4"),
    _case({"tier": "office", "cost": "yes"}),
]


@pytest.mark.parametrize("params,expected", FILTER_CASES)
def test_a2_4_filters_return_exactly_the_matching_contacts(client, seeded, params, expected):
    resp = _list(client, query=seeded["tag"], **params)
    assert _ids(resp) == {seeded[name].id for name in expected}
    assert resp.json()["pagination"]["total"] == len(expected)


def test_a2_3_access_type_filter_alone_and_combined(client, seeded):
    tag = seeded["tag"]
    assert _ids(_list(client, query=tag, access_type=seeded["ta"].code)) == {seeded["c1"].id, seeded["c3"].id}
    assert _ids(_list(client, query=tag, access_type=seeded["tb"].code)) == {seeded["c2"].id, seeded["c3"].id}
    assert _ids(_list(client, query=tag, access_type=seeded["ta"].code, packing_list="no")) == {seeded["c3"].id}


def test_a2_3_customer_id_filter_alone_and_combined(client, seeded):
    tag = seeded["tag"]
    assert _ids(_list(client, query=tag, customer_id=seeded["cust1"].id)) == {seeded["c1"].id, seeded["c2"].id}
    assert _ids(_list(client, query=tag, customer_id=seeded["cust2"].id)) == {seeded["c2"].id}
    assert _ids(_list(client, query=tag, customer_id=seeded["cust1"].id, tier="dealer")) == {seeded["c1"].id}


def test_a2_4_filtered_total_and_paging_match(client, seeded):
    body = _list(client, query=seeded["tag"], escalation="yes", limit=1, page=3).json()
    assert body["pagination"]["total"] == 3
    assert len(body["data"]) == 1


# ---------------------------------------------------------------- A2.5


def test_a2_5_access_differs_from_lists_exactly_contacts_a_copy_would_change(client, db, seeded):
    ta, tb = seeded["ta"], seeded["tb"]
    ag_a, ag_b = _agent(db, "a"), _agent(db, "b")
    d1, d2 = datetime(2026, 1, 1), datetime(2026, 12, 31)
    tag = unique_code("D", alpha=True)

    def base(label, **over):
        kw = dict(types=[ta, tb], tier="dealer", reveals=[COST], stock=False, notify=True, packing=True,
                  eta=False, escalation=False, agents=[(ag_a, True, d1, d2)])
        kw.update(over)
        return _contact(db, tag, label, **kw)

    x = base("X")
    same = base("same", memory="full")  # memory is not part of the access set
    _link(db, same, seeded["cust1"])  # nor are customers
    differs = {
        "types": base("types", types=[ta]),
        "tier": base("tier", tier="office"),
        "stock": base("stock", stock=True),
        "notify": base("notify", notify=False),
        "packing": base("packing", packing=False),
        "eta": base("eta", eta=True),
        "escalation": base("escalation", escalation=True),
        "reveals": base("reveals", reveals=[COST, SELLABLE]),
        "agent_flag": base("agent_flag", agents=[(ag_a, False, d1, d2)]),
        "agent_dates": base("agent_dates", agents=[(ag_a, True, None, None)]),
        "agent_extra": base("agent_extra", agents=[(ag_a, True, d1, d2), (ag_b, True, None, None)]),
    }
    resp = _list(client, query=tag, access_differs_from=x.id)
    ids = _ids(resp)
    assert ids == {c.id for c in differs.values()}
    assert x.id not in ids
    assert same.id not in ids
    assert resp.json()["pagination"]["total"] == len(differs)


def test_a2_5_access_differs_from_combines_with_other_filters(client, db, seeded):
    tag = unique_code("E", alpha=True)
    x = _contact(db, tag, "X", tier="dealer")
    a = _contact(db, tag, "a", tier="office")
    b = _contact(db, tag, "b", tier="end_user")
    assert _ids(_list(client, query=tag, access_differs_from=x.id)) == {a.id, b.id}
    assert _ids(_list(client, query=tag, access_differs_from=x.id, tier="office")) == {a.id}


def test_do_default_on_access_differs_from_compares_the_effective_do_reveals(client, db, seeded):
    """DO field reveals default ON (owner, 4 Oct 2026): a contact with no DO rows reads the same
    as one with every DO key explicitly granted, and differs from one with a DO key turned off."""
    from app.services.contact_field_reveal_service import FIELD_REVEAL_KEYS

    do_keys = [k for k, _label in FIELD_REVEAL_KEYS if k.startswith("delivery_orders.")]
    tag = unique_code("G", alpha=True)
    x = _contact(db, tag, "X")
    fresh = _contact(db, tag, "fresh")
    explicit = _contact(db, tag, "explicit", reveals=do_keys)
    hidden = _contact(db, tag, "hidden", revoked=["delivery_orders.warehouse"])
    assert _ids(_list(client, query=tag, access_differs_from=x.id)) == {hidden.id}
    assert fresh.id not in _ids(_list(client, query=tag, access_differs_from=x.id))
    assert explicit.id not in _ids(_list(client, query=tag, access_differs_from=x.id))


# ---------------------------------------------------------------- A2.6


def test_a2_6_customer_id_matches_only_links_inside_the_callers_company_scope(client, db, state):
    tag = unique_code("S", alpha=True)
    contact = _contact(db, tag, "x")
    other_company_customer = _customer(db, company_id=MOCHA_ID)
    _link(db, contact, other_company_customer)

    state["scope"] = frozenset({SORENTO})
    assert contact.id not in _ids(_list(client, query=tag, customer_id=other_company_customer.id))

    state["scope"] = frozenset({SORENTO, MOCHA_ID})
    assert _ids(_list(client, query=tag, customer_id=other_company_customer.id)) == {contact.id}


def test_n1_malformed_customer_id_is_422_not_500(client):
    assert _list(client, customer_id="not-a-uuid").status_code == 422
