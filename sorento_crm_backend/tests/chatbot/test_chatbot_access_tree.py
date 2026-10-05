"""ACCESS-MODEL S2: `effective_access` (AC-AM-5, 6, 8, 10, 16).

Red-first: `app/services/chatbot/access_tree.py` and `app/models/chatbot_access.py` do not exist
yet, so every test fails with ModuleNotFoundError until the coder adds them.

Each test seeds its own workspace, contact, domain, field and role rows in the blank schema.
Domain names here are the test's own (`zzt_*`) so the assertions do not depend on the real
registry seed.
"""
from __future__ import annotations

import dataclasses

import pytest

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


def _ea(db, rio: str, space_id: str | None = SPACE_ID):
    from app.services.chatbot.access_tree import effective_access

    return effective_access(db, contact_respond_id=rio, space_id=space_id)


def _world(session_factory):
    """One workspace, three domains (one carries a reveal_key), two fields.

    A plain function called from each test body, not a fixture, so a missing new model
    module shows as a test FAILURE (ImportError) rather than a fixture ERROR."""
    db = session_factory()
    wid = make_workspace(db)
    make_domain(db, "zzt_stock")
    make_domain(db, "zzt_cost", reveal_key="zzt.cost")
    make_domain(db, "zzt_orders")
    make_field(db, "zzt_stock", "zzt.stock.sellable")
    make_field(db, "zzt_orders", "zzt.orders.outstanding", kind="report")
    return db, wid


class TestShape:
    def test_effective_access_is_a_frozen_dataclass_with_the_contract_fields(self, session_factory):
        """AC-AM-11: the one reader returns one value object."""
        from app.services.chatbot.access_tree import EffectiveAccess

        assert dataclasses.is_dataclass(EffectiveAccess)
        names = {f.name for f in dataclasses.fields(EffectiveAccess)}
        assert names == {
            "resolved", "domains", "attributes", "sees_all_customers", "roles", "tiers",
        }
        db, wid = _world(session_factory)
        _pk, rio = make_contact(db, workspace_id=wid)
        access = _ea(db, rio)
        with pytest.raises(dataclasses.FrozenInstanceError):
            access.resolved = False  # type: ignore[misc]
        assert isinstance(access.domains, frozenset)
        assert isinstance(access.attributes, tuple)
        assert isinstance(access.roles, tuple)


class TestDenyByDefault:
    def test_contact_with_no_role_reaches_nothing(self, session_factory):
        """AC-AM-6."""
        db, wid = _world(session_factory)
        _pk, rio = make_contact(db, workspace_id=wid)
        access = _ea(db, rio)
        assert access.resolved is True
        assert access.domains == frozenset()
        assert access.attributes == ()
        assert access.sees_all_customers is False
        assert access.roles == ()

    def test_unknown_contact_is_unresolved_and_empty(self, session_factory):
        """AC-AM-6, edge 3."""
        db, _wid = _world(session_factory)
        access = _ea(db, rid("nobody"))
        assert access.resolved is False
        assert access.domains == frozenset()
        assert access.attributes == ()
        assert access.sees_all_customers is False
        assert access.roles == ()

    def test_contact_in_another_workspace_is_not_resolved(self, session_factory):
        db, wid = _world(session_factory)
        other = make_workspace(db, "acm-space-other")
        _pk, rio = make_contact(db, workspace_id=other)
        assert _ea(db, rio, SPACE_ID).resolved is False

    def test_null_workspace_contact_resolves_through_the_fallback(self, session_factory):
        """Same NULL-workspace fallback the reveal path uses today."""
        db, _wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=None)
        rid_ = make_role(db, rid("r"), domains=["zzt_stock"])
        give_role(db, pk, rid_)
        access = _ea(db, rio)
        assert access.resolved is True
        assert access.domains == frozenset({"zzt_stock"})

    def test_a_new_registry_row_is_ticked_on_no_role(self, session_factory):
        """AC-AM-16: inserting a domain + field grants it to nobody."""
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock"], fields=["zzt.stock.sellable"]))
        before = _ea(db, rio)
        make_domain(db, "zzt_brand_new", reveal_key="zzt.brand_new")
        make_field(db, "zzt_brand_new", "zzt.brand_new.field")
        after = _ea(db, rio)
        assert after == before
        assert "zzt_brand_new" not in after.domains
        assert "zzt.brand_new" not in after.attributes


class TestRoleUnion:
    def test_domains_are_the_union_of_every_role(self, session_factory):
        """AC-AM-5."""
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, "zzt_b_role", domains=["zzt_stock"]))
        give_role(db, pk, make_role(db, "zzt_a_role", domains=["zzt_orders"]))
        access = _ea(db, rio)
        assert access.domains == frozenset({"zzt_stock", "zzt_orders"})
        assert access.roles == ("zzt_a_role", "zzt_b_role"), "role codes, sorted"

    def test_attributes_are_sorted_and_deduped(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(
            db,
            pk,
            make_role(
                db, rid("r1"), domains=["zzt_stock", "zzt_orders"],
                fields=["zzt.stock.sellable", "zzt.orders.outstanding"],
            ),
        )
        give_role(db, pk, make_role(db, rid("r2"), domains=["zzt_stock"], fields=["zzt.stock.sellable"]))
        access = _ea(db, rio)
        assert access.attributes == ("zzt.orders.outstanding", "zzt.stock.sellable")

    def test_a_field_counts_only_when_its_own_domain_is_granted(self, session_factory):
        """AC-AM-2 / contract: a tick on a field whose domain is not in the final domains is inert."""
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_orders"], fields=["zzt.stock.sellable"]))
        access = _ea(db, rio)
        assert access.domains == frozenset({"zzt_orders"})
        assert "zzt.stock.sellable" not in access.attributes

    def test_an_unticked_field_inside_a_granted_domain_is_absent(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock"]))
        access = _ea(db, rio)
        assert access.domains == frozenset({"zzt_stock"})
        assert access.attributes == ()

    def test_a_granted_domain_with_a_reveal_key_adds_that_key_to_attributes(self, session_factory):
        """Contract: attributes = granted field keys PLUS reveal_key of every granted domain."""
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_cost"]))
        assert _ea(db, rio).attributes == ("zzt.cost",)

    def test_sees_all_customers_when_any_held_role_has_it(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r1"), domains=["zzt_stock"], sees_all=False))
        assert _ea(db, rio).sees_all_customers is False
        give_role(db, pk, make_role(db, rid("r2"), domains=[], sees_all=True))
        assert _ea(db, rio).sees_all_customers is True


class TestReports:
    """Owner N1 / AC-AM-4b: a report needs its owning domain; it never grants it."""

    def test_a_report_tick_without_its_domain_grants_neither(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock"], fields=["zzt.orders.outstanding"]))
        access = _ea(db, rio)
        assert "zzt.orders.outstanding" not in access.attributes
        assert "zzt_orders" not in access.domains

    def test_a_report_tick_with_its_domain_is_an_attribute(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_orders"], fields=["zzt.orders.outstanding"]))
        access = _ea(db, rio)
        assert access.attributes == ("zzt.orders.outstanding",)
        assert access.domains == frozenset({"zzt_orders"})

    def test_removing_the_owning_domain_drops_the_report(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_orders"], fields=["zzt.orders.outstanding"]))
        override(db, pk, "zzt_orders", granted=False)
        assert _ea(db, rio).attributes == ()

    def test_a_report_add_override_never_grants_the_domain(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        override(db, pk, "zzt_orders", field_key="zzt.orders.outstanding", granted=True)
        access = _ea(db, rio)
        assert access.attributes == ()
        assert access.domains == frozenset()


class TestOverrides:
    def test_domain_add_override_grants_without_any_role(self, session_factory):
        """AC-AM-5 / AC-AM-6: 'no role and no add override' is the empty case; an add override opens it."""
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        override(db, pk, "zzt_orders", granted=True)
        access = _ea(db, rio)
        assert access.resolved is True
        assert access.domains == frozenset({"zzt_orders"})
        assert access.roles == ()

    def test_domain_remove_override_beats_the_role_add(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock", "zzt_orders"]))
        override(db, pk, "zzt_stock", granted=False)
        assert _ea(db, rio).domains == frozenset({"zzt_orders"})

    def test_domain_remove_override_beats_even_a_second_role_add(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r1"), domains=["zzt_stock"]))
        give_role(db, pk, make_role(db, rid("r2"), domains=["zzt_stock"]))
        override(db, pk, "zzt_stock", granted=False)
        assert _ea(db, rio).domains == frozenset()

    def test_removing_a_domain_also_drops_its_field_ticks_and_reveal_key(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(
            db, pk,
            make_role(db, rid("r"), domains=["zzt_stock", "zzt_cost"], fields=["zzt.stock.sellable"]),
        )
        assert _ea(db, rio).attributes == ("zzt.cost", "zzt.stock.sellable")
        override(db, pk, "zzt_stock", granted=False)
        override(db, pk, "zzt_cost", granted=False)
        access = _ea(db, rio)
        assert access.domains == frozenset()
        assert access.attributes == ()

    def test_field_remove_override_beats_the_role_tick(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_stock"], fields=["zzt.stock.sellable"]))
        override(db, pk, "zzt_stock", field_key="zzt.stock.sellable", granted=False)
        access = _ea(db, rio)
        assert access.domains == frozenset({"zzt_stock"})
        assert access.attributes == ()

    def test_field_add_override_grants_a_field_the_role_did_not_tick(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_orders"]))
        override(db, pk, "zzt_orders", field_key="zzt.orders.outstanding", granted=True)
        assert _ea(db, rio).attributes == ("zzt.orders.outstanding",)

    def test_field_add_override_is_inert_when_its_domain_is_not_granted(self, session_factory):
        db, wid = _world(session_factory)
        pk, rio = make_contact(db, workspace_id=wid)
        give_role(db, pk, make_role(db, rid("r"), domains=["zzt_orders"]))
        override(db, pk, "zzt_stock", field_key="zzt.stock.sellable", granted=True)
        access = _ea(db, rio)
        assert "zzt.stock.sellable" not in access.attributes
        assert "zzt_stock" not in access.domains


class TestDuplicateContactRows:
    def test_two_rows_with_one_respond_id_get_the_intersection(self, session_factory):
        """AC-AM-8: fail closed."""
        db, wid = _world(session_factory)
        pk1, rio = make_contact(db, workspace_id=wid)
        pk2, _ = make_contact(db, respond_io_id=rio, workspace_id=wid)
        give_role(
            db, pk1,
            make_role(db, rid("r1"), domains=["zzt_stock", "zzt_orders"], fields=["zzt.stock.sellable"], sees_all=True),
        )
        give_role(
            db, pk2,
            make_role(db, rid("r2"), domains=["zzt_stock", "zzt_cost"], fields=["zzt.stock.sellable"], sees_all=False),
        )
        access = _ea(db, rio)
        assert access.domains == frozenset({"zzt_stock"})
        assert access.attributes == ("zzt.stock.sellable",)
        assert access.sees_all_customers is False, "only if both rows have it"

    def test_one_duplicate_with_no_role_empties_the_result(self, session_factory):
        db, wid = _world(session_factory)
        pk1, rio = make_contact(db, workspace_id=wid)
        _pk2, _ = make_contact(db, respond_io_id=rio, workspace_id=wid)
        give_role(db, pk1, make_role(db, rid("r"), domains=["zzt_stock"], sees_all=True))
        access = _ea(db, rio)
        assert access.domains == frozenset()
        assert access.attributes == ()
        assert access.sees_all_customers is False

    def test_sees_all_survives_only_when_both_rows_have_it(self, session_factory):
        db, wid = _world(session_factory)
        pk1, rio = make_contact(db, workspace_id=wid)
        pk2, _ = make_contact(db, respond_io_id=rio, workspace_id=wid)
        give_role(db, pk1, make_role(db, rid("r1"), domains=["zzt_stock"], sees_all=True))
        give_role(db, pk2, make_role(db, rid("r2"), domains=["zzt_stock"], sees_all=True))
        assert _ea(db, rio).sees_all_customers is True
