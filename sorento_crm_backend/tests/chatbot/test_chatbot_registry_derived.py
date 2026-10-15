"""ACCESS-MODEL S5: registry derivations (AC-AM-15, PLAN S5).

(a) `contact_field_reveal_service.field_reveal_keys(db)` and `GET /system/chatbot/field-reveal-keys`
    read `chatbot_domain_fields` rows of kind field/report whose key is a legacy reveal key.
(b) The incoming REST gate (`field_access.decide` / `apply_field_access`, signatures unchanged)
    decides `incoming_stock` field visibility for a chat contact from `effective_access`
    attributes (`incoming_stock.<field>`), not from `agent_field_access`.
(c) `contracts.suggested_agents(db)` returns the active `access_agents.code` values, sorted.

Red-first: the new function names and the tree-based gate do not exist yet.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

import app.main  # noqa: F401  isort:skip
from app.main import app
from app.dependencies import get_current_user, get_current_user_or_api_key, get_db
from app.services.user_service import UserPermissionService

from tests.chatbot._access_seed import (
    SPACE_ID,
    give_role,
    make_contact,
    make_domain,
    make_field,
    make_role,
    make_workspace,
    override,
    rid,
)
from tests.chatbot.test_turns_admin_api import db  # noqa: F401

LEGACY_KEYS = {
    "inventory.sellable", "purchase_orders.placed", "purchase_orders.supplier", "purchase_orders.cost",
    "sales_orders.outstanding", "sales_orders.sales_report", "scm.low_stock_report",
}


def _seed_reveal_rows(db):
    make_domain(db, "inventory")
    make_domain(db, "order")
    make_domain(db, "incoming")
    make_field(db, "inventory", "inventory.sellable", label="Sellable stock")
    make_field(db, "order", "sales_orders.outstanding", kind="report", label="Outstanding SO")
    make_field(db, "incoming", "incoming_stock.consignee", label="Consignee")  # not a legacy key
    make_field(db, "order", "zzt.other", label="Other")  # not a legacy key


class TestFieldRevealKeysFromRows:
    def test_function_returns_only_legacy_keys_present_as_rows(self, session_factory):
        from app.services.contact_field_reveal_service import field_reveal_keys

        db_ = session_factory()
        _seed_reveal_rows(db_)
        result = field_reveal_keys(db_)
        assert isinstance(result, tuple)
        assert sorted(result) == [
            ("inventory.sellable", "Sellable stock"),
            ("sales_orders.outstanding", "Outstanding SO"),
        ], "a legacy key with no row (purchase_orders.cost ...) is absent: the DB is the source"

    def test_a_row_that_is_not_a_legacy_reveal_key_is_ignored(self, session_factory):
        from app.services.contact_field_reveal_service import field_reveal_keys

        db_ = session_factory()
        _seed_reveal_rows(db_)
        make_field(db_, "incoming", "stamp.incoming", kind="report", label="Stamp")
        keys = [k for k, _ in field_reveal_keys(db_)]
        for not_legacy in ("incoming_stock.consignee", "zzt.other", "stamp.incoming"):
            assert not_legacy not in keys


class TestFieldRevealKeysEndpoint:
    @pytest.fixture()
    def client(self, db, monkeypatch):  # noqa: F811
        monkeypatch.setattr(UserPermissionService, "check_user_has_permission", lambda s, u, slug: True)
        monkeypatch.setattr(UserPermissionService, "get_user_role_slugs", lambda s, u: set())

        def _override_db():
            yield db

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: {"id": str(uuid.uuid4())}
        app.dependency_overrides[get_current_user_or_api_key] = lambda: {"id": str(uuid.uuid4())}
        try:
            yield TestClient(app, raise_server_exceptions=False)
        finally:
            app.dependency_overrides.clear()

    def test_endpoint_serves_the_rows_not_the_literal(self, client, db):  # noqa: F811
        _seed_reveal_rows(db)
        resp = client.get("/api/v1/system/chatbot/field-reveal-keys")
        assert resp.status_code == 200, resp.text
        items = {i["key"]: i["label"] for i in resp.json()["items"]}
        assert items == {"inventory.sellable": "Sellable stock", "sales_orders.outstanding": "Outstanding SO"}


def _incoming_world(db, *, grant_agent: bool):
    wid = make_workspace(db)
    make_domain(db, "incoming")
    make_field(db, "incoming", "incoming_stock.consignee")
    pk, rio = make_contact(db, workspace_id=wid)
    if grant_agent:
        agent_id = db.execute(
            text(
                "INSERT INTO access_agents (id, code, name, is_active, assign_to_new_internal_contacts, "
                "synced_to_excel) VALUES (gen_random_uuid(), 'incoming_stock_enquiries', 'I', true, false, false) "
                "RETURNING id"
            )
        ).scalar()
        db.execute(
            text(
                "INSERT INTO contact_agent_access (id, respond_contact_id, respond_contact_phone, agent_id, "
                "is_allowed, synced_to_excel) VALUES (gen_random_uuid(), :c, '+60', :a, true, false)"
            ),
            {"c": pk, "a": agent_id},
        )
        db.execute(
            text(
                "INSERT INTO agent_field_access (id, agent_code, resource, field_key, contact_id, is_allowed) "
                "VALUES (gen_random_uuid(), 'incoming_stock_enquiries', 'incoming_stock', 'consignee', NULL, true)"
            )
        )
    db.commit()
    return pk, rio


def _payload():
    return {
        "data": [{"shipment_number": "S-1", "consignee": "ACME", "estimated_arrival_date": "2026-07-18"}],
        "pagination": {"total": 1, "page": 1, "limit": 10},
    }


class TestIncomingGateReadsTheTree:
    def _decide(self, db, rio):
        from app.services.field_access import decide

        (d,) = decide(db, resource="incoming_stock", fields=["consignee"], contact_id=rio, space_id=SPACE_ID)
        return d

    def test_agent_wide_default_rows_without_a_tree_grant_do_not_reveal(self, session_factory):
        db_ = session_factory()
        _pk, rio = _incoming_world(db_, grant_agent=True)
        assert self._decide(db_, rio).allowed is False

    def test_a_role_tick_on_the_incoming_field_reveals_it(self, session_factory):
        db_ = session_factory()
        pk, rio = _incoming_world(db_, grant_agent=True)
        give_role(db_, pk, make_role(db_, rid("r"), domains=["incoming"], fields=["incoming_stock.consignee"]))
        assert self._decide(db_, rio).allowed is True

    def test_the_tree_grant_works_without_any_agent_grant(self, session_factory):
        db_ = session_factory()
        pk, rio = _incoming_world(db_, grant_agent=False)
        give_role(db_, pk, make_role(db_, rid("r"), domains=["incoming"], fields=["incoming_stock.consignee"]))
        assert self._decide(db_, rio).allowed is True

    def test_a_field_remove_override_hides_it_again(self, session_factory):
        db_ = session_factory()
        pk, rio = _incoming_world(db_, grant_agent=True)
        give_role(db_, pk, make_role(db_, rid("r"), domains=["incoming"], fields=["incoming_stock.consignee"]))
        override(db_, pk, "incoming", field_key="incoming_stock.consignee", granted=False)
        assert self._decide(db_, rio).allowed is False

    def test_apply_field_access_strips_and_keeps_by_the_tree(self, session_factory):
        from app.services.field_access import apply_field_access

        db_ = session_factory()
        pk, rio = _incoming_world(db_, grant_agent=True)
        stripped = apply_field_access(
            db_, _payload(), resource="incoming_stock", contact_id=rio, space_id=SPACE_ID
        )
        row = stripped["data"][0]
        assert "consignee" not in row
        assert row["shipment_number"] == "S-1", "the answer itself is never gated"

        give_role(db_, pk, make_role(db_, rid("r"), domains=["incoming"], fields=["incoming_stock.consignee"]))
        kept = apply_field_access(
            db_, _payload(), resource="incoming_stock", contact_id=rio, space_id=SPACE_ID
        )
        assert kept["data"][0]["consignee"] == "ACME"


class TestSuggestedAgentsFromRows:
    def _agent(self, db_, code, active=True):
        db_.execute(
            text(
                "INSERT INTO access_agents (id, code, name, is_active, assign_to_new_internal_contacts, "
                "synced_to_excel) VALUES (gen_random_uuid(), :c, :c, :a, false, false)"
            ),
            {"c": code, "a": active},
        )

    def test_returns_active_codes_sorted_and_omits_inactive(self, session_factory):
        from app.services.chatbot.contracts import suggested_agents

        db_ = session_factory()
        self._agent(db_, "zzt_b_agent")
        self._agent(db_, "zzt_a_agent")
        self._agent(db_, "zzt_dead_agent", active=False)
        db_.commit()
        result = list(suggested_agents(db_))
        assert result == ["zzt_a_agent", "zzt_b_agent"]
