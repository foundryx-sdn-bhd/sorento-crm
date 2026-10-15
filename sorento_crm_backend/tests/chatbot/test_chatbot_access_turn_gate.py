"""ACCESS-MODEL S2: the turn reads ONE tree (AC-AM-6, 10, 11).

Three seams, all filled from `effective_access`:
  * `head/access.py::check_access`   -> `allowed` / `decision` / `attributes`
  * `turn_runtime.load_profile`      -> `Profile.grants` (domain NAMES, sorted list)
  * `turn/apply.py::apply`           -> the domain gate (`_narrow_and_plan`, apply.py:2199) compares
                                        `row.name` to `profile.grants`, no longer `row.reveal_key`

The apply seam is exercised through the public `apply(state, verdict, policy)` the existing
`test_rearch_s2_plan_shape.py::test_denied_lists_a_domain_the_profile_grants_forbid` uses; no
private function is called.

Red-first: `app/models/chatbot_access.py` does not exist yet.
"""
from __future__ import annotations

from datetime import datetime, timedelta

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
from tests.chatbot._turn_helpers import (
    POLICY_KIND_ROWS,
    TIER_ORDER_FIXTURE,
    _domain_row,
    entity,
    verdict,
)


def _seed(db):
    wid = make_workspace(db)
    make_domain(db, "zzt_stock")
    make_domain(db, "zzt_cost", reveal_key="zzt.cost")
    make_field(db, "zzt_stock", "zzt.stock.sellable")
    return wid


def _check(db, rio, agent="general_enquiries"):
    from app.services.chatbot.head.access import check_access

    return check_access(db, agent_code=agent, contact_id=rio, space_id=SPACE_ID)


class TestCheckAccess:
    def test_a_role_with_no_agent_rows_is_allowed(self, session_factory):
        """AC-AM-11: the agent grant no longer gates a chat turn."""
        db = session_factory()
        wid = _seed(db)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock"], fields=["zzt.stock.sellable"]))
        access = _check(db, rio)
        assert access["allowed"] is True
        assert access["attributes"] == ["zzt.stock.sellable"]

    def test_attributes_equal_the_effective_access_attributes_as_a_list(self, session_factory):
        from app.services.chatbot.access_tree import effective_access

        db = session_factory()
        wid = _seed(db)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock", "zzt_cost"], fields=["zzt.stock.sellable"]))
        tree = effective_access(db, contact_respond_id=rio, space_id=SPACE_ID)
        access = _check(db, rio)
        assert access["attributes"] == list(tree.attributes) == ["zzt.cost", "zzt.stock.sellable"]

    def test_agent_rows_without_a_role_are_allowed_with_empty_attributes(self, session_factory):
        """A resolved contact is allowed; the tree, not the agent, decides what it reaches."""
        from sqlalchemy import text

        db = session_factory()
        wid = _seed(db)
        pk, rio = make_contact(db, workspace_id=wid)
        agent_id = db.execute(
            text(
                "INSERT INTO access_agents (id, code, name, is_active, assign_to_new_internal_contacts, "
                "synced_to_excel) VALUES (gen_random_uuid(), :c, 'A', true, false, false) RETURNING id"
            ),
            {"c": rid("agent")},
        ).scalar()
        db.execute(
            text(
                "INSERT INTO contact_agent_access (id, respond_contact_id, respond_contact_phone, agent_id, "
                "is_allowed, synced_to_excel) VALUES (gen_random_uuid(), :c, '+60000', :a, true, false)"
            ),
            {"c": pk, "a": agent_id},
        )
        db.commit()
        access = _check(db, rio)
        assert access["allowed"] is True
        assert access["attributes"] == []

    def test_an_unknown_agent_code_no_longer_denies(self, session_factory):
        db = session_factory()
        wid = _seed(db)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock"]))
        assert _check(db, rio, agent=rid("no-such-agent"))["allowed"] is True

    def test_an_expired_agent_grant_does_not_matter(self, session_factory):
        """AC-AM-10: no validity window (every legacy grant lapses 31 Dec 2026)."""
        from sqlalchemy import text

        db = session_factory()
        wid = _seed(db)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock"]))
        agent_id = db.execute(
            text(
                "INSERT INTO access_agents (id, code, name, is_active, assign_to_new_internal_contacts, "
                "synced_to_excel) VALUES (gen_random_uuid(), 'general_enquiries', 'G', true, false, false) "
                "RETURNING id"
            )
        ).scalar()
        db.execute(
            text(
                "INSERT INTO contact_agent_access (id, respond_contact_id, respond_contact_phone, agent_id, "
                "is_allowed, valid_to, synced_to_excel) VALUES (gen_random_uuid(), :c, '+60001', :a, true, :t, false)"
            ),
            {"c": pk, "a": agent_id, "t": datetime.utcnow() - timedelta(days=400)},
        )
        db.commit()
        assert _check(db, rio)["allowed"] is True

    def test_unknown_contact_is_denied_with_deny_unknown_contact(self, session_factory):
        db = session_factory()
        _seed(db)
        from app.services.chatbot.access_tree import effective_access

        assert effective_access(db, contact_respond_id=rid("nobody"), space_id=SPACE_ID).resolved is False
        access = _check(db, rid("nobody"))
        assert access["allowed"] is False
        assert access["decision"] == "deny_unknown_contact"
        assert access["attributes"] == []

    def test_the_return_shape_keeps_every_key(self, session_factory):
        db = session_factory()
        wid = _seed(db)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock"]))
        access = _check(db, rio)
        assert {
            "allowed", "decision", "agent_name", "attributes", "all_attributes_allowed", "hidden_spec_keys",
        } <= set(access)
        assert access["all_attributes_allowed"] is False

    def test_a_field_override_remove_reaches_the_attributes(self, session_factory):
        db = session_factory()
        wid = _seed(db)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock"], fields=["zzt.stock.sellable"]))
        override(db, pk, "zzt_stock", field_key="zzt.stock.sellable", granted=False)
        assert _check(db, rio)["attributes"] == []


class TestLoadProfileGrants:
    def _profile(self, db, rio):
        from app.services.chatbot import turn_runtime

        profile, _recall = turn_runtime.load_profile(db, rio, space_id=SPACE_ID)
        return profile

    def test_grants_are_the_sorted_domain_names(self, session_factory):
        db = session_factory()
        wid = _seed(db)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock", "zzt_cost"]))
        grants = self._profile(db, rio).grants
        assert grants == ["zzt_cost", "zzt_stock"]
        assert isinstance(grants, list)

    def test_a_contact_with_no_role_gets_an_empty_list_not_none(self, session_factory):
        """AC-AM-6: deny by default. `None` means unrestricted; it must be gone for a resolved contact."""
        db = session_factory()
        wid = _seed(db)
        _pk, rio = make_contact(db, workspace_id=wid)
        assert self._profile(db, rio).grants == []

    def test_a_remove_override_drops_the_domain_from_grants(self, session_factory):
        db = session_factory()
        wid = _seed(db)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock", "zzt_cost"]))
        override(db, pk, "zzt_cost", granted=False)
        assert self._profile(db, rio).grants == ["zzt_stock"]


def _gate_policy():
    from app.services.chatbot.turn.policy import Policy

    rows = [
        _domain_row("purchase_cost", narrowing={"product": "narrow_to_code"}, reveal_key="purchase_orders.cost"),
        _domain_row("inventory", narrowing={"product": "list_all"}, reveal_key="inventory.sellable"),
    ]
    return Policy.from_rows(domains=rows, kinds=POLICY_KIND_ROWS, tier_order=TIER_ORDER_FIXTURE)


def _denied(grants, domain_hint):
    from app.services.chatbot.turn.apply import apply
    from app.services.chatbot.turn.state import Focus, Profile, State

    state = State(focus=Focus(), pending=None, profile=Profile(grants=grants))
    v = verdict(domain_hint=domain_hint, entities=[entity("SRTWC8517")])
    _state2, plan = apply(state, v, _gate_policy())
    return plan.denied


class TestDomainGateComparesNames:
    def test_the_gate_reads_the_domain_name_not_the_reveal_key(self):
        """AC-AM-11: `Profile.grants` holds domain names; a reveal_key in grants grants nothing."""
        # Named in grants -> allowed, even though the row carries a different reveal_key.
        assert "purchase_cost" not in _denied(["purchase_cost"], "purchase_cost")
        # Only the reveal key in grants -> the domain NAME is absent -> denied.
        assert "purchase_cost" in _denied(["purchase_orders.cost"], "purchase_cost")
        # Empty grants -> denied.
        assert "purchase_cost" in _denied([], "purchase_cost")

    def test_inventory_is_gated_by_its_name_not_its_sellable_key(self):
        assert "inventory" not in _denied(["inventory"], "inventory")
        assert "inventory" in _denied(["purchase_cost"], "inventory")
        assert "inventory" in _denied(["inventory.sellable"], "inventory")
