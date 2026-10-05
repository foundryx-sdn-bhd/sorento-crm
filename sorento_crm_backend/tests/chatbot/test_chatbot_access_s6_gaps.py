"""ACCESS-MODEL S6 gaps (red): deferred role delete, role holders add/remove, agent display name.

Pending-action keys pinned here (named in the round brief):
  * `chatbot_role.delete`          entity_type `chatbot_role`, entity_id = role id,
                                   permission user_management.reference_data.manage, 10 s window.
  * `chatbot_role_contact.unlink`  entity_type `chatbot_role_contact`, entity_id = "<role_id>:<contact_id>",
                                   permission user_management.contacts.edit, 5 s window.
Routes: POST /system/chatbot/roles/{id}/contacts {contact_ids}, DELETE .../contacts/{contact_id}.
Registry domain field: `escalation_agent_name` (access_agents.name).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import text

from tests.chatbot._access_seed import give_role, make_contact, make_domain, make_role, rid
from tests.chatbot.test_chatbot_roles_api import (  # noqa: F401 - fixtures and constants
    _GRANTS,
    BASE,
    ROLE_MANAGE,
    CONTACT_EDIT,
    _permissions,
    client,
    db,
)

PENDING = "/api/v1/pending-actions"


def _lapse(db, action_id: str) -> None:
    from app.models.sla import SlaFormAction

    db.query(SlaFormAction).filter(SlaFormAction.id == action_id).update(
        {"commit_at": datetime.utcnow() - timedelta(seconds=1)}, synchronize_session=False
    )
    db.commit()


def _park(client, key, entity_type, entity_id, payload=None):
    return client.post(
        PENDING,
        json={"action_key": key, "entity_type": entity_type, "entity_id": entity_id, "payload": payload or {}},
    )


def _commit(client, db, parked, entity_type, entity_id):
    _lapse(db, parked.json()["id"])
    return client.get(f"{PENDING}/current", params={"entity_type": entity_type, "entity_id": entity_id})


def _holders(db, role_id) -> set[str]:
    from app.models.chatbot_access import ContactChatbotRole

    db.expire_all()
    return {r.contact_id for r in db.query(ContactChatbotRole).filter(ContactChatbotRole.role_id == role_id)}


class TestRoleDeleteIsDeferred:
    def test_key_is_registered_with_the_destructive_window_and_manage_permission(self):
        from app.services import record_actions  # noqa: F401
        from app.services.form_action_registry import get_action

        action = get_action("chatbot_role.delete")
        assert action is not None
        assert action.entity_types == ("chatbot_role",)
        assert action.permission == ROLE_MANAGE

    def test_parking_then_lapsing_deletes_an_unheld_role(self, client, db):
        from app.models.chatbot_access import ChatbotRole

        role_id = make_role(db, "zzt_s6_del")
        db.commit()
        parked = _park(client, "chatbot_role.delete", "chatbot_role", role_id)
        assert parked.status_code == 202, parked.text
        assert parked.json()["window_seconds"] == 10
        db.expire_all()
        assert db.query(ChatbotRole).filter(ChatbotRole.id == role_id).first() is not None
        current = _commit(client, db, parked, "chatbot_role", role_id)
        assert current.json()["last_outcome"]["status"] == "committed", current.text
        db.expire_all()
        assert db.query(ChatbotRole).filter(ChatbotRole.id == role_id).first() is None

    def test_a_held_role_is_refused_with_409_and_stays(self, client, db):
        from app.models.chatbot_access import ChatbotRole
        from app.services.error_handler import AppException

        import pytest

        from app.services import record_actions  # noqa: F401
        from app.services.form_action_registry import get_action

        role_id = make_role(db, "zzt_s6_held")
        pk, _ = make_contact(db)
        give_role(db, pk, role_id)
        action = get_action("chatbot_role.delete")
        assert action is not None
        with pytest.raises(AppException) as exc:
            action.execute(db, {"entity_id": role_id})
        assert exc.value.status_code == 409
        db.rollback()
        assert db.query(ChatbotRole).filter(ChatbotRole.id == role_id).first() is not None

    def test_without_manage_permission_parking_is_403(self, client, db):
        role_id = make_role(db, "zzt_s6_noperm")
        db.commit()
        _GRANTS.discard(ROLE_MANAGE)
        assert _park(client, "chatbot_role.delete", "chatbot_role", role_id).status_code == 403


class TestRoleHoldersRoutes:
    def test_post_adds_holders_and_is_idempotent(self, client, db):
        role_id = make_role(db, "zzt_s6_add")
        a, _ = make_contact(db)
        b, _ = make_contact(db)
        db.commit()
        url = f"{BASE}/roles/{role_id}/contacts"
        first = client.post(url, json={"contact_ids": [a, b]})
        assert first.status_code in (200, 201, 204), first.text
        assert _holders(db, role_id) == {a, b}
        again = client.post(url, json={"contact_ids": [a]})
        assert again.status_code in (200, 201, 204), again.text
        assert _holders(db, role_id) == {a, b}

    def test_delete_removes_one_holder(self, client, db):
        role_id = make_role(db, "zzt_s6_rm")
        a, _ = make_contact(db)
        b, _ = make_contact(db)
        give_role(db, a, role_id)
        give_role(db, b, role_id)
        resp = client.delete(f"{BASE}/roles/{role_id}/contacts/{a}")
        assert resp.status_code in (200, 204), resp.text
        assert _holders(db, role_id) == {b}

    def test_unknown_role_or_contact_is_404(self, client, db):
        role_id = make_role(db, "zzt_s6_404")
        a, _ = make_contact(db)
        db.commit()
        missing = str(uuid.uuid4())
        assert client.post(f"{BASE}/roles/{missing}/contacts", json={"contact_ids": [a]}).status_code == 404
        assert client.post(f"{BASE}/roles/{role_id}/contacts", json={"contact_ids": [missing]}).status_code == 404
        assert client.delete(f"{BASE}/roles/{missing}/contacts/{a}").status_code == 404
        assert client.delete(f"{BASE}/roles/{role_id}/contacts/{missing}").status_code == 404

    def test_both_need_contacts_edit(self, client, db):
        role_id = make_role(db, "zzt_s6_perm")
        a, _ = make_contact(db)
        give_role(db, a, role_id)
        _GRANTS.discard(CONTACT_EDIT)
        assert client.post(f"{BASE}/roles/{role_id}/contacts", json={"contact_ids": [a]}).status_code == 403
        assert client.delete(f"{BASE}/roles/{role_id}/contacts/{a}").status_code == 403

    def test_post_with_a_malformed_body_is_422(self, client, db):
        role_id = make_role(db, "zzt_s6_422")
        db.commit()
        assert client.post(f"{BASE}/roles/{role_id}/contacts", json={"contact_ids": "x"}).status_code == 422


class TestRoleContactUnlinkIsDeferred:
    def test_key_is_registered(self):
        from app.services import record_actions  # noqa: F401
        from app.services.form_action_registry import get_action

        action = get_action("chatbot_role_contact.unlink")
        assert action is not None
        assert action.entity_types == ("chatbot_role_contact",)
        assert action.permission == CONTACT_EDIT

    def test_parking_then_lapsing_removes_only_that_holder(self, client, db):
        role_id = make_role(db, "zzt_s6_unl")
        a, _ = make_contact(db)
        b, _ = make_contact(db)
        give_role(db, a, role_id)
        give_role(db, b, role_id)
        entity_id = f"{role_id}:{a}"
        parked = _park(client, "chatbot_role_contact.unlink", "chatbot_role_contact", entity_id)
        assert parked.status_code == 202, parked.text
        assert parked.json()["window_seconds"] == 5
        assert _holders(db, role_id) == {a, b}
        current = _commit(client, db, parked, "chatbot_role_contact", entity_id)
        assert current.json()["last_outcome"]["status"] == "committed", current.text
        assert _holders(db, role_id) == {b}


class TestRegistryAgentName:
    def test_domain_carries_the_agent_display_name_next_to_the_code(self, client, db):
        make_domain(db, "zzt_s6_agent", escalation_team_code="zzt_s6_set")
        code = f"zzt_s6_{uuid.uuid4().hex[:6]}"
        db.execute(
            text(
                "INSERT INTO access_agents (id, code, name, is_active, assign_to_new_internal_contacts, "
                "synced_to_excel) VALUES (gen_random_uuid(), :c, 'Zzt Display Agent', true, false, false)"
            ),
            {"c": code},
        )
        db.execute(
            text("UPDATE chatbot_domains SET escalation_agent_code = :c WHERE name = 'zzt_s6_agent'"), {"c": code}
        )
        make_domain(db, "zzt_s6_noagent")
        db.commit()
        domains = {d["name"]: d for d in client.get(f"{BASE}/access/registry").json()["domains"]}
        assert domains["zzt_s6_agent"]["escalation_agent_code"] == code
        assert domains["zzt_s6_agent"]["escalation_agent_name"] == "Zzt Display Agent"
        assert "escalation_agent_name" in domains["zzt_s6_noagent"]
        assert domains["zzt_s6_noagent"]["escalation_agent_name"] is None
