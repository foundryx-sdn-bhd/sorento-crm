"""ACCESS-MODEL (mock v8 gaps): registry labels/groups, tier-1 team names, role contacts.

1. `chatbot_domains.access_group / access_label / access_description` (nullable); the registry API
   returns `group`, `access_label` (falls back to `label`), `access_description` and
   `escalation_tier1_team` (tier-1 team name(s) of the domain's (agent, team set), ", " joined).
2. Labels for the 14 seeded domains come from `access_seed.seed_domain_labels(db)` (idempotent,
   updates existing domain rows; the migration calls the same data). `seed_default_roles` does
   not need to call it.
3. `GET /system/chatbot/roles/{id}/contacts` -> [{id, name}] sorted by name.

Red-first: columns, `seed_domain_labels` and the route do not exist yet.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text

from app.services.company_scope import DEFAULT_COMPANY_ID

from tests.chatbot._access_seed import give_role, make_contact, make_domain, make_role, rid
from tests.chatbot.test_chatbot_access_migration import _registry
from tests.chatbot.test_chatbot_roles_api import (  # noqa: F401 - fixtures and constants
    ACCESS_VIEW,
    BASE,
    _GRANTS,
    _create,
    _permissions,
    client,
    db,
)


def _domain_json(client, name):
    resp = client.get(f"{BASE}/access/registry")
    assert resp.status_code == 200, resp.text
    return {d["name"]: d for d in resp.json()["domains"]}[name]


def _team(db, name, company_id=DEFAULT_COMPANY_ID):
    from app.models.access import Team

    team = Team(name=name, company_id=company_id)
    db.add(team)
    db.flush()
    return team.id


def _agent(db, code):
    return db.execute(
        text(
            "INSERT INTO access_agents (id, code, name, is_active, assign_to_new_internal_contacts, "
            "synced_to_excel) VALUES (gen_random_uuid(), :c, :c, true, false, false) RETURNING id"
        ),
        {"c": code},
    ).scalar()


def _company(db):
    cid = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO companies (id, name, code) VALUES (:i, 'ZZT Other Co', :c)"),
        {"i": cid, "c": f"ZZT{uuid.uuid4().hex[:6]}"},
    )
    return cid


def _agent_team(db, agent_id, code, team_id, tier, company_id=DEFAULT_COMPANY_ID):
    from app.models.access import AgentTeam

    db.add(AgentTeam(agent_id=agent_id, code=code, team_id=team_id, tier=tier, company_id=company_id))
    db.flush()


class TestRegistryLabels:
    def test_columns_are_returned_and_fall_back_when_null(self, client, db):
        make_domain(db, "zzt_plain")
        d = _domain_json(client, "zzt_plain")
        assert {"group", "access_label", "access_description", "escalation_tier1_team"} <= set(d)
        assert d["group"] is None
        assert d["access_label"] == d["label"]

    def test_set_columns_are_returned(self, client, db):
        from app.models.chatbot_policy import ChatbotDomain

        make_domain(db, "zzt_set")
        row = db.query(ChatbotDomain).filter(ChatbotDomain.name == "zzt_set").one()
        row.access_group, row.access_label, row.access_description = "Stock and incoming", "Stock available", "What is on hand"
        db.commit()
        d = _domain_json(client, "zzt_set")
        assert (d["group"], d["access_label"], d["access_description"]) == (
            "Stock and incoming", "Stock available", "What is on hand",
        )
        assert d["label"] != "Stock available", "the parser label stays untouched"

    def _two_company_tier1(self, db):
        make_domain(db, "zzt_esc", escalation_team_code="zzt_set")
        agent = _agent(db, "zzt_esc_agent")
        db.execute(
            text("UPDATE chatbot_domains SET escalation_agent_code = 'zzt_esc_agent' WHERE name = 'zzt_esc'")
        )
        other = _company(db)
        _agent_team(db, agent, "zzt_set", _team(db, "Zzt Tier One B"), 1)
        _agent_team(db, agent, "zzt_set", _team(db, "Zzt Tier One A", other), 1, other)
        _agent_team(db, agent, "zzt_set", _team(db, "Zzt Tier Two"), 2)
        db.commit()
        return other

    def test_tier1_team_names_only_joined_with_comma_space(self, client, db):
        self._two_company_tier1(db)
        assert _domain_json(client, "zzt_esc")["escalation_tier1_team"] == "Zzt Tier One A, Zzt Tier One B"

    def test_company_scoped_session_sees_only_its_own_tier1_team(self, client, db):
        from app.main import app
        from app.models.base import set_company_scope
        from app.services.company_scope_resolver import apply_company_scope

        self._two_company_tier1(db)
        # The request-entry resolver would overwrite the scope from the actor's grants; pin ours.
        app.dependency_overrides[apply_company_scope] = lambda: None
        set_company_scope(db, frozenset({DEFAULT_COMPANY_ID}))
        assert _domain_json(client, "zzt_esc")["escalation_tier1_team"] == "Zzt Tier One B"

    def test_a_single_tier1_team_is_a_bare_name(self, client, db):
        make_domain(db, "zzt_one", escalation_team_code="zzt_one_set")
        agent = _agent(db, "zzt_one_agent")
        db.execute(text("UPDATE chatbot_domains SET escalation_agent_code = 'zzt_one_agent' WHERE name = 'zzt_one'"))
        _agent_team(db, agent, "zzt_one_set", _team(db, "Zzt Only Tier One"), 1)
        _agent_team(db, agent, "zzt_one_set", _team(db, "Zzt Escalation"), 2)
        db.commit()
        assert _domain_json(client, "zzt_one")["escalation_tier1_team"] == "Zzt Only Tier One"

    def test_no_team_or_no_agent_is_null(self, client, db):
        make_domain(db, "zzt_noteam")
        make_domain(db, "zzt_noagent", escalation_team_code="zzt_x_set")
        db.commit()
        assert _domain_json(client, "zzt_noteam")["escalation_tier1_team"] is None
        assert _domain_json(client, "zzt_noagent")["escalation_tier1_team"] is None


class TestSeededLabels:
    def test_seed_domain_labels_applies_mock_v8_wording(self, client, db):
        from app.services.chatbot.access_seed import seed_domain_labels

        _registry(db)
        seed_domain_labels(db)
        db.commit()
        expected = {
            "master_products": ("Products and marketing", "Product details"),
            "inventory": ("Stock and incoming", "Stock available"),
            "order": ("Orders", "Orders and deliveries"),
            "purchase_order": ("Purchasing", "Open purchase orders"),
            "sales": ("Reports", "Sales report"),
            "ideate": ("Other", "Share ideas"),
        }
        for name, (group, label) in expected.items():
            d = _domain_json(client, name)
            assert (d["group"], d["access_label"]) == (group, label), name
        labels = {n: _domain_json(client, n)["access_label"] for n in (
            "product_attachment", "resource_attachment", "promotion", "forms", "portal_link",
            "incoming", "spo_allocation", "purchase_cost",
        )}
        assert labels == {
            "product_attachment": "Product photos and documents",
            "resource_attachment": "Catalogues and other documents",
            "promotion": "Promotions",
            "forms": "Forms",
            "portal_link": "Their request portal link",
            "incoming": "Incoming stock",
            "spo_allocation": "When a product last came in",
            "purchase_cost": "What we last paid",
        }
        assert _domain_json(client, "inventory")["access_description"], "a one-line description is seeded"

    def test_seed_domain_labels_is_idempotent_and_adds_no_domain_rows(self, db):
        from app.models.chatbot_policy import ChatbotDomain
        from app.services.chatbot.access_seed import seed_domain_labels

        _registry(db)
        seed_domain_labels(db)
        db.commit()
        before = db.query(ChatbotDomain).count()
        seed_domain_labels(db)
        db.commit()
        assert db.query(ChatbotDomain).count() == before


class TestRoleContacts:
    def test_lists_holders_sorted_by_name(self, client, db):
        role = _create(client)["id"]
        other = make_role(db, rid("other"))
        for n in ("Zed Contact", "CONTACT B Contact", "Mid Contact"):
            pk, _ = make_contact(db, name=n)
            give_role(db, pk, role)
        stray, _ = make_contact(db, name="Stray Holder")
        give_role(db, stray, other)
        resp = client.get(f"{BASE}/roles/{role}/contacts")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert [c["name"] for c in body] == ["CONTACT B Contact", "Mid Contact", "Zed Contact"]
        assert all(set(c) >= {"id", "name"} for c in body)

    def test_empty_role_is_an_empty_list(self, client, db):
        role = _create(client)["id"]
        resp = client.get(f"{BASE}/roles/{role}/contacts")
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    def test_requires_view_permission(self, client, db):
        role = _create(client)["id"]
        _GRANTS.discard(ACCESS_VIEW)
        assert client.get(f"{BASE}/roles/{role}/contacts").status_code == 403

    def test_unknown_role_is_404(self, client, db):
        _create(client)  # route family exists
        assert client.get(f"{BASE}/roles/{uuid.uuid4()}/contacts").status_code == 404
