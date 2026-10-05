"""ACCESS-MODEL interim review fixes (red). Keys chosen here: supplier field `purchase_cost.supplier`.

1 accepted hand-off keeps the offer's agent; 2 on-order key single path; 3 lapsed legacy grants;
5 deferred unlink checks the contact like the route; 6 4xx not 500; 7 tier-1 names fail closed;
8 supplier field under purchase_cost; Q4 either view slug reads.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text

from tests.chatbot._access_seed import give_role, make_contact, make_domain, make_field, make_role, make_workspace, rid
from tests.chatbot.test_chatbot_access_migration import (
    FIVE_AGENTS,
    _access,
    _agents,
    _registry,
    _role_codes,
    _role_fields,
    _seeded,
)
from tests.chatbot.test_chatbot_registry_labels_api import _agent, _agent_team, _company, _team
from tests.chatbot.test_chatbot_roles_api import (  # noqa: F401 - fixtures and constants
    _GRANTS,
    ACCESS_VIEW,
    BASE,
    CONTACT_EDIT,
    CONTACT_VIEW,
    _create,
    _permissions,
    client,
    db,
)

K_PLACED = "purchase_orders.placed"


# 1 ------------------------------------------------------------------------------------------
def test_an_accepted_offer_keeps_the_offers_agent_over_the_domain_rows():
    from app.services.chatbot import turn_runtime
    from tests.chatbot.test_chatbot_escalation_domain_agent import _policy
    from tests.chatbot._turn_helpers import verdict

    policy = _policy(inventory="incoming_stock_enquiries")
    v = verdict(domain_hint="inventory")
    v["routing"] = {"suggested_team": None, "suggested_agent": "order_enquiries"}
    out = turn_runtime.lane_parse_output(v, domain="inventory", policy=policy, accepted_team="customer_service")
    assert out["routing"]["suggested_team"] == "customer_service"
    assert out["routing"]["suggested_agent"] == "order_enquiries"


# 2 ------------------------------------------------------------------------------------------
def test_seeding_clears_the_purchase_order_reveal_key(session_factory):
    from app.services.chatbot.access_seed import seed_field_registry

    db = session_factory()
    make_domain(db, "purchase_order", reveal_key=K_PLACED)
    db.commit()
    seed_field_registry(db)
    db.commit()
    key = db.execute(text("SELECT reveal_key FROM chatbot_domains WHERE name = 'purchase_order'")).scalar()
    assert key is None


def test_on_order_key_comes_only_from_the_inventory_field_tick(session_factory):
    db, wid = _seeded(session_factory)
    db.execute(text("UPDATE chatbot_domains SET reveal_key = NULL WHERE name = 'purchase_order'"))
    db.commit()
    pk, _ = make_contact(db, workspace_id=wid)
    role = make_role(db, rid("r"), domains=["inventory", "purchase_order"], fields=["inventory.sellable"])
    give_role(db, pk, role)
    assert K_PLACED not in _access(db, pk).attributes


# 3 ------------------------------------------------------------------------------------------
def _grant(db, pk, agent_id, *, valid_from=None, valid_to=None):
    db.execute(
        text(
            "INSERT INTO contact_agent_access (id, respond_contact_id, respond_contact_phone, agent_id, "
            "is_allowed, valid_from, valid_to, synced_to_excel) "
            "VALUES (gen_random_uuid(), :c, '+60', :a, true, :f, :t, false)"
        ),
        {"c": pk, "a": agent_id, "f": valid_from, "t": valid_to},
    )
    db.commit()


def test_legacy_mapping_ignores_lapsed_and_not_yet_valid_grants(session_factory):
    from app.services.chatbot.access_seed import map_legacy_contacts

    db, wid = _seeded(session_factory)
    agents = _agents(db)
    now = datetime.utcnow()
    expired, future, live = (make_contact(db, workspace_id=wid)[0] for _ in range(3))
    _grant(db, expired, agents["general_enquiries"], valid_to=now - timedelta(days=1))
    _grant(db, future, agents["general_enquiries"], valid_from=now + timedelta(days=1))
    _grant(db, live, agents["general_enquiries"], valid_to=now + timedelta(days=30))
    map_legacy_contacts(db)
    db.commit()
    assert _role_codes(db, expired) == []
    assert _role_codes(db, future) == []
    assert _role_codes(db, live) != []


# 5 ------------------------------------------------------------------------------------------
def test_deferred_unlink_refuses_an_unknown_contact_or_role_like_the_route(db):
    from app.services.error_handler import AppException
    from app.services.form_action_registry import get_action
    from app.services import record_actions  # noqa: F401

    role_id = make_role(db, rid("r"))
    pk, _ = make_contact(db)
    give_role(db, pk, role_id)
    action = get_action("chatbot_role_contact.unlink")
    for entity in (f"{role_id}:{uuid.uuid4()}", f"{uuid.uuid4()}:{pk}"):
        with pytest.raises(AppException) as exc:
            action.execute(db, {"entity_id": entity})
        assert exc.value.status_code in (403, 404)
    from app.models.chatbot_access import ContactChatbotRole

    assert db.query(ContactChatbotRole).filter(ContactChatbotRole.role_id == role_id).count() == 1


# 6 ------------------------------------------------------------------------------------------
class TestErrorsAre4xx:
    def test_a_malformed_role_id_in_a_contact_put_is_422(self, client, db):
        pk, _ = make_contact(db)
        resp = client.put(f"{BASE}/contacts/{pk}/access", json={"role_ids": ["not-a-uuid"], "overrides": []})
        assert resp.status_code == 422, resp.text

    def test_a_malformed_role_id_in_the_holders_route_is_4xx(self, client, db):
        pk, _ = make_contact(db)
        resp = client.post(f"{BASE}/roles/not-a-uuid/contacts", json={"contact_ids": [pk]})
        assert resp.status_code in (404, 422), resp.text

    def test_two_overrides_on_one_field_in_a_put_is_422(self, client, db):
        make_domain(db, "zzt_dup")
        make_field(db, "zzt_dup", "zzt.dup.f")
        pk, _ = make_contact(db)
        db.commit()
        one = {"domain_name": "zzt_dup", "field_key": "zzt.dup.f", "granted": True}
        resp = client.put(
            f"{BASE}/contacts/{pk}/access", json={"role_ids": [], "overrides": [one, {**one, "granted": False}]}
        )
        assert resp.status_code == 422, resp.text

    def test_a_duplicate_role_name_is_409(self, client, db):
        _create(client, "ZZT Same Name")
        resp = client.post(
            f"{BASE}/roles", json={"name": "ZZT Same Name", "description": "", "sees_all_customers": False}
        )
        assert resp.status_code == 409, resp.text

    def test_granting_an_unsupported_domain_is_422(self, client, db):
        make_domain(db, "zzt_unsupported", supported=False)
        db.commit()
        role = _create(client, "ZZT Grants")["id"]
        resp = client.put(f"{BASE}/roles/{role}/grants", json={"domains": ["zzt_unsupported"], "fields": []})
        assert resp.status_code == 422, resp.text


# 7 ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("scope_kind", ["unset", "empty"])
def test_tier1_names_fail_closed_without_a_company_scope(client, db, scope_kind):
    from app.main import app
    from app.models.base import UNSET, set_company_scope
    from app.services.company_scope_resolver import apply_company_scope

    make_domain(db, "zzt_fc", escalation_team_code="zzt_fc_set")
    agent = _agent(db, "zzt_fc_agent")
    db.execute(text("UPDATE chatbot_domains SET escalation_agent_code = 'zzt_fc_agent' WHERE name = 'zzt_fc'"))
    other = _company(db)
    _agent_team(db, agent, "zzt_fc_set", _team(db, "Zzt Fc One"), 1)
    _agent_team(db, agent, "zzt_fc_set", _team(db, "Zzt Fc Two", other), 1, other)
    db.commit()
    app.dependency_overrides[apply_company_scope] = lambda: None
    set_company_scope(db, UNSET if scope_kind == "unset" else frozenset())
    domains = {d["name"]: d for d in client.get(f"{BASE}/access/registry").json()["domains"]}
    assert domains["zzt_fc"]["escalation_tier1_team"] is None


# 8 ------------------------------------------------------------------------------------------
def test_purchase_cost_carries_a_supplier_field_that_purchasing_and_management_tick(session_factory):
    from app.services.chatbot.access_seed import seed_field_registry

    db, _ = _seeded(session_factory)
    seed_field_registry(db)
    db.commit()
    domain = db.execute(
        text("SELECT domain_name FROM chatbot_domain_fields WHERE key = 'purchase_cost.supplier'")
    ).scalar()
    assert domain == "purchase_cost"
    # roles were seeded before the field existed; seed on a fresh role set
    from app.services.chatbot.access_seed import seed_default_roles

    db.execute(text("DELETE FROM chatbot_role_fields"))
    db.execute(text("DELETE FROM chatbot_role_domains"))
    db.execute(text("DELETE FROM chatbot_roles"))
    db.commit()
    seed_default_roles(db)
    db.commit()
    assert "purchase_cost.supplier" in _role_fields(db, "purchasing")
    assert "purchase_cost.supplier" in _role_fields(db, "management")
    assert "purchase_cost.supplier" not in _role_fields(db, "sales_office")


# Q4 -----------------------------------------------------------------------------------------
class TestEitherViewSlugReads:
    def _urls(self, client, db):
        role = _create(client, "ZZT Q4")["id"]
        pk, _ = make_contact(db)
        db.commit()
        return [
            f"{BASE}/access/registry",
            f"{BASE}/roles",
            f"{BASE}/roles/{role}",
            f"{BASE}/roles/{role}/contacts",
            f"{BASE}/contacts/{pk}/access",
        ]

    @pytest.mark.parametrize("keep", [ACCESS_VIEW, CONTACT_VIEW])
    def test_one_view_slug_is_enough(self, client, db, keep):
        urls = self._urls(client, db)
        _GRANTS.clear()
        _GRANTS.add(keep)
        for url in urls:
            assert client.get(url).status_code == 200, url

    def test_neither_slug_is_403(self, client, db):
        urls = self._urls(client, db)
        _GRANTS.clear()
        _GRANTS.add(CONTACT_EDIT)
        for url in urls:
            assert client.get(url).status_code == 403, url


# Q3 -----------------------------------------------------------------------------------------
class TestGrantingNeedsManageEditOnlyRemoves:
    """Owner answer Q3: granting needs reference_data.manage; contacts.edit may only remove."""

    def _only_edit(self):
        from tests.chatbot.test_chatbot_roles_api import ROLE_MANAGE  # noqa: F401

        _GRANTS.clear()
        _GRANTS.update({CONTACT_EDIT, CONTACT_VIEW})

    def _held(self, db, pk) -> set[str]:
        from app.models.chatbot_access import ContactChatbotRole

        db.expire_all()
        return {r.role_id for r in db.query(ContactChatbotRole).filter(ContactChatbotRole.contact_id == pk)}

    def _overrides(self, db, pk) -> set[tuple]:
        from app.models.chatbot_access import ContactAccessOverride

        db.expire_all()
        return {
            (o.domain_name, o.field_key, bool(o.granted))
            for o in db.query(ContactAccessOverride).filter(ContactAccessOverride.contact_id == pk)
        }

    def test_adding_holders_needs_manage(self, client, db):
        from tests.chatbot.test_chatbot_roles_api import ROLE_MANAGE

        role = make_role(db, rid("r"))
        pk, _ = make_contact(db)
        db.commit()
        url = f"{BASE}/roles/{role}/contacts"
        self._only_edit()
        assert client.post(url, json={"contact_ids": [pk]}).status_code == 403
        assert self._held(db, pk) == set()
        _GRANTS.add(ROLE_MANAGE)
        assert client.post(url, json={"contact_ids": [pk]}).status_code in (200, 201, 204)
        assert self._held(db, pk) == {role}

    def test_removing_a_holder_stays_on_edit(self, client, db):
        role = make_role(db, rid("r"))
        pk, _ = make_contact(db)
        give_role(db, pk, role)
        self._only_edit()
        assert client.delete(f"{BASE}/roles/{role}/contacts/{pk}").status_code in (200, 204)
        assert self._held(db, pk) == set()

    def test_the_deferred_unlink_stays_on_edit(self):
        from app.services import record_actions  # noqa: F401
        from app.services.form_action_registry import get_action

        assert get_action("chatbot_role_contact.unlink").permission == CONTACT_EDIT

    def test_put_with_only_edit_cannot_add_a_role_or_a_plus_override(self, client, db):
        make_domain(db, "zzt_q3")
        role = make_role(db, rid("r"))
        pk, _ = make_contact(db)
        db.commit()
        self._only_edit()
        url = f"{BASE}/contacts/{pk}/access"
        assert client.put(url, json={"role_ids": [role], "overrides": []}).status_code == 403
        assert client.put(
            url, json={"role_ids": [], "overrides": [{"domain_name": "zzt_q3", "field_key": None, "granted": True}]}
        ).status_code == 403
        assert self._held(db, pk) == set()
        assert self._overrides(db, pk) == set()

    def test_put_with_only_edit_may_remove_roles_and_overrides_and_add_minus(self, client, db):
        from tests.chatbot._access_seed import override

        make_domain(db, "zzt_q3b")
        make_domain(db, "zzt_q3c")
        role = make_role(db, rid("r"))
        pk, _ = make_contact(db)
        give_role(db, pk, role)
        override(db, pk, "zzt_q3b", granted=True)
        self._only_edit()
        url = f"{BASE}/contacts/{pk}/access"
        resp = client.put(
            url,
            json={"role_ids": [], "overrides": [{"domain_name": "zzt_q3c", "field_key": None, "granted": False}]},
        )
        assert resp.status_code == 200, resp.text
        assert self._held(db, pk) == set()
        assert self._overrides(db, pk) == {("zzt_q3c", None, False)}

    def test_put_with_manage_may_add_everything(self, client, db):
        from tests.chatbot.test_chatbot_roles_api import ROLE_MANAGE

        make_domain(db, "zzt_q3d")
        role = make_role(db, rid("r"))
        pk, _ = make_contact(db)
        db.commit()
        self._only_edit()
        _GRANTS.add(ROLE_MANAGE)
        resp = client.put(
            f"{BASE}/contacts/{pk}/access",
            json={"role_ids": [role], "overrides": [{"domain_name": "zzt_q3d", "field_key": None, "granted": True}]},
        )
        assert resp.status_code == 200, resp.text
        assert self._held(db, pk) == {role}
