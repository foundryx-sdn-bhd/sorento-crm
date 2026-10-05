"""ACCESS-MODEL S1: default roles + legacy mapping + loss check (AC-AM-16, 19, 20).

Red-first: `app/services/chatbot/access_seed.py` and `app/models/chatbot_access.py` do not exist
yet.

Legacy shapes seeded here mirror the nine mapping shapes of `access-model-2oct-mapping.sql`
part A (agents in `contact_agent_access`, keys in `contact_field_reveals`, incoming overrides
in `agent_field_access`). Contacts are named differently on purpose: the mapping is computed
from the diff between role ticks and legacy keys, never by name.

Registry rows (`chatbot_domains`, `chatbot_domain_fields`) are seeded by `_registry` below
because the blank schema has no migration seed. Placement used (PLAN "Schema" + CARD section 2):
  * `inventory`      fields `inventory.sellable`, `purchase_orders.placed` (on order),
                     `scm.low_stock_report` (kind report)
  * `order`          field `sales_orders.outstanding` (kind report)
  * `purchase_order` fields `purchase_orders.supplier`, `purchase_orders.po_number`
  * `incoming`       fields `incoming_stock.<gated field>` x23
  * domain reveal_key (granted with the domain): `purchase_cost` -> purchase_orders.cost,
    `sales` -> sales_orders.sales_report, `purchase_order` -> purchase_orders.placed
  * NO `outstanding` and NO `low_stock` domain rows (PLAN N1): both are `report` fields (owner N1: a Reports section).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import text

from tests.chatbot._access_seed import (
    SPACE_ID,
    make_contact,
    make_domain,
    make_field,
    make_workspace,
    rid,
)

INCOMING_FIELDS = (
    "estimated_arrival_date", "eta_delay_date", "inspection_date", "approval_date",
    "gatepass_date", "warehouse_arrival_date", "informed_collection_date", "collection_date",
    "loading_date", "etc_date", "etd_date", "liner_code", "china_forwarder",
    "malaysia_forwarder", "consignee", "delivery_warehouse", "free_days_available", "loc",
    "stacked", "coa_permit_no", "source_sheet", "shipping_container_number",
    "remaining_incoming_quantity",
)
DEFAULT_ALLOWED = {"estimated_arrival_date", "shipping_container_number", "remaining_incoming_quantity"}
FIVE_AGENTS = (
    "general_enquiries", "incoming_stock_enquiries", "lead_time_enquiries", "marketing_form", "order_enquiries",
)
ALL_AGENTS = FIVE_AGENTS + ("conversation_analysis", "ideation")
K_SELLABLE, K_PLACED = "inventory.sellable", "purchase_orders.placed"
K_SUPPLIER, K_COST = "purchase_orders.supplier", "purchase_orders.cost"
K_OUTSTANDING, K_SALES, K_LOW = "sales_orders.outstanding", "sales_orders.sales_report", "scm.low_stock_report"
K_STAMP_INCOMING, K_STAMP_ATTACHMENT = "stamp.incoming", "stamp.product_attachment"
#: Owner ruling 2 Oct: stamps are ticked on EVERY seeded preset, Dealer included. A pending
#: owner follow-up may untick them on Dealer; flip this ONE constant if that lands.
DEALER_HAS_STAMPS = True
ALL_SEVEN = (K_SELLABLE, K_PLACED, K_SUPPLIER, K_COST, K_OUTSTANDING, K_SALES, K_LOW)

DEALER_DOMAINS = {
    "master_products", "product_attachment", "resource_attachment", "promotion", "forms",
    "portal_link", "inventory", "order", "incoming",
}
SALES_OFFICE_DOMAINS = DEALER_DOMAINS | {"spo_allocation", "purchase_order"}
PURCHASING_DOMAINS = SALES_OFFICE_DOMAINS | {"purchase_cost"}
WAREHOUSE_DOMAINS = {"inventory", "incoming", "spo_allocation", "master_products"}
SUPPORTED_DOMAINS = PURCHASING_DOMAINS | {"sales", "ideate"}


def _registry(db) -> None:
    for name, key in (
        ("master_products", None), ("product_attachment", None), ("resource_attachment", None),
        ("promotion", None), ("forms", None), ("portal_link", None), ("inventory", None),
        ("order", None), ("incoming", None), ("spo_allocation", None), ("purchase_order", K_PLACED),
        ("ideate", None), ("purchase_cost", K_COST), ("sales", K_SALES),
    ):
        make_domain(db, name, reveal_key=key)
    make_domain(db, "goods_receive", supported=False)
    db.execute(text("UPDATE chatbot_domains SET access_section = 'reports' WHERE name = 'sales'"))
    db.commit()
    make_field(db, "inventory", K_SELLABLE)
    make_field(db, "inventory", K_PLACED)
    make_field(db, "inventory", K_LOW, kind="report")
    make_field(db, "order", K_OUTSTANDING, kind="report")
    make_field(db, "purchase_order", K_SUPPLIER)
    make_field(db, "purchase_order", "purchase_orders.po_number")
    for f in INCOMING_FIELDS:
        make_field(db, "incoming", f"incoming_stock.{f}")
    make_field(db, "incoming", K_STAMP_INCOMING)
    make_field(db, "product_attachment", K_STAMP_ATTACHMENT)


def _agents(db) -> dict[str, str]:
    ids = {}
    for code in ALL_AGENTS:
        ids[code] = db.execute(
            text(
                "INSERT INTO access_agents (id, code, name, is_active, assign_to_new_internal_contacts, "
                "synced_to_excel) VALUES (gen_random_uuid(), :c, :c, true, false, false) RETURNING id"
            ),
            {"c": code},
        ).scalar()
    db.commit()
    return ids


def _legacy_contact(db, wid, agent_ids, *, agents, keys, incoming_overrides=(), name=None) -> str:
    pk, _rio = make_contact(db, workspace_id=wid, name=name)
    for code in agents:
        db.execute(
            text(
                "INSERT INTO contact_agent_access (id, respond_contact_id, respond_contact_phone, agent_id, "
                "is_allowed, valid_to, synced_to_excel) "
                "VALUES (gen_random_uuid(), :c, '+60', :a, true, :t, false)"
            ),
            {"c": pk, "a": agent_ids[code], "t": datetime(2026, 12, 31)},
        )
    for key in keys:
        db.execute(
            text(
                "INSERT INTO contact_field_reveals (id, respond_contact_id, field_key, granted) "
                "VALUES (gen_random_uuid(), :c, :k, true)"
            ),
            {"c": pk, "k": key},
        )
    for field in incoming_overrides:
        db.execute(
            text(
                "INSERT INTO agent_field_access (id, agent_code, resource, field_key, contact_id, is_allowed) "
                "VALUES (gen_random_uuid(), 'incoming_stock_enquiries', 'incoming_stock', :f, :c, true)"
            ),
            {"f": field, "c": pk},
        )
    db.commit()
    return pk


def _rio(db, pk) -> str:
    return db.execute(text("SELECT respond_io_id FROM respond_contacts WHERE id = :i"), {"i": pk}).scalar()


def _role_codes(db, pk) -> list[str]:
    from app.models.chatbot_access import ChatbotRole, ContactChatbotRole

    return sorted(
        code
        for (code,) in db.query(ChatbotRole.code)
        .join(ContactChatbotRole, ContactChatbotRole.role_id == ChatbotRole.id)
        .filter(ContactChatbotRole.contact_id == pk)
        .all()
    )


def _override_count(db, pk) -> int:
    from app.models.chatbot_access import ContactAccessOverride

    return db.query(ContactAccessOverride).filter(ContactAccessOverride.contact_id == pk).count()


def _access(db, pk):
    from app.services.chatbot.access_tree import effective_access

    return effective_access(db, contact_respond_id=_rio(db, pk), space_id=SPACE_ID)


def _role(db, code):
    from app.models.chatbot_access import ChatbotRole

    return db.query(ChatbotRole).filter(ChatbotRole.code == code).one()


def _role_domains(db, code) -> set[str]:
    from app.models.chatbot_access import ChatbotRoleDomain

    role = _role(db, code)
    return {d for (d,) in db.query(ChatbotRoleDomain.domain_name).filter(ChatbotRoleDomain.role_id == role.id)}


def _role_fields(db, code) -> set[str]:
    from app.models.chatbot_access import ChatbotRoleField

    role = _role(db, code)
    return {f for (f,) in db.query(ChatbotRoleField.field_key).filter(ChatbotRoleField.role_id == role.id)}


def _seeded(session_factory):
    from app.services.chatbot.access_seed import seed_default_roles

    db = session_factory()
    wid = make_workspace(db)
    _registry(db)
    seed_default_roles(db)
    db.commit()
    return db, wid


class TestSeedDefaultRoles:
    """AC-AM-1 (presets exist only as seed rows), AC-AM-19 (card section 2 ticks)."""

    def test_creates_the_five_presets_with_the_sees_all_flag(self, session_factory):
        from app.models.chatbot_access import ChatbotRole

        db, _wid = _seeded(session_factory)
        rows = {r.code: r for r in db.query(ChatbotRole).all()}
        assert {"dealer", "sales_office", "purchasing", "warehouse", "management"} <= set(rows)
        assert rows["dealer"].name == "Dealer"
        assert rows["sales_office"].name == "Sales office"
        assert rows["dealer"].sees_all_customers is False
        for code in ("sales_office", "purchasing", "warehouse", "management"):
            assert rows[code].sees_all_customers is True, code

    def test_domain_ticks_follow_card_section_two(self, session_factory):
        db, _wid = _seeded(session_factory)
        assert _role_domains(db, "dealer") == DEALER_DOMAINS
        assert _role_domains(db, "sales_office") == SALES_OFFICE_DOMAINS
        assert _role_domains(db, "purchasing") == PURCHASING_DOMAINS
        assert _role_domains(db, "warehouse") == WAREHOUSE_DOMAINS
        assert _role_domains(db, "management") == SUPPORTED_DOMAINS
        assert "goods_receive" not in _role_domains(db, "management"), "unsupported stays unticked"

    def test_field_ticks_follow_card_section_two(self, session_factory):
        db, _wid = _seeded(session_factory)
        eta, qty, container = (
            "incoming_stock.estimated_arrival_date",
            "incoming_stock.remaining_incoming_quantity",
            "incoming_stock.shipping_container_number",
        )
        dealer = _role_fields(db, "dealer")
        assert {f for f in dealer if f.startswith("incoming_stock.")} == {eta, qty}
        assert not dealer & {K_SELLABLE, K_PLACED, K_SUPPLIER}, "a dealer sees no sensitive stock/PO field"
        office = _role_fields(db, "sales_office")
        assert {f for f in office if f.startswith("incoming_stock.")} == {eta, qty, container}
        assert {K_SELLABLE, K_PLACED} <= office
        assert K_SUPPLIER not in office
        assert not office & {K_OUTSTANDING, K_LOW}, "the two reports are Warehouse (low stock) and Management (both) only"
        assert K_LOW in _role_fields(db, "warehouse")
        assert K_SUPPLIER in _role_fields(db, "purchasing")
        assert {f for f in _role_fields(db, "warehouse") if f.startswith("incoming_stock.")} == {
            f"incoming_stock.{f}" for f in INCOMING_FIELDS
        }
        from app.models.chatbot_access import ChatbotDomainField

        every_key = {k for (k,) in db.query(ChatbotDomainField.key).all()}
        assert _role_fields(db, "management") == every_key

    def test_reports_section_ticks(self, session_factory):
        """Owner N1: Reports are `report` fields (plus the `sales` domain, access_section reports)."""
        from app.models.chatbot_access import ChatbotDomainField
        from app.models.chatbot_policy import ChatbotDomain

        db, _wid = _seeded(session_factory)
        kinds = {k: kind for k, kind in db.query(ChatbotDomainField.key, ChatbotDomainField.kind)}
        assert kinds[K_LOW] == kinds[K_OUTSTANDING] == "report"
        assert db.query(ChatbotDomain).filter(ChatbotDomain.name == "sales").one().access_section == "reports"
        assert "sales" in _role_domains(db, "management")
        assert {K_LOW, K_OUTSTANDING} <= _role_fields(db, "management")
        assert K_LOW in _role_fields(db, "warehouse")
        assert K_OUTSTANDING not in _role_fields(db, "warehouse")
        for code in ("dealer", "sales_office", "purchasing"):
            assert not _role_fields(db, code) & {K_LOW, K_OUTSTANDING}, code
            assert "sales" not in _role_domains(db, code), code

    def test_seeding_twice_changes_nothing(self, session_factory):
        from app.models.chatbot_access import ChatbotRole, ChatbotRoleDomain, ChatbotRoleField
        from app.services.chatbot.access_seed import seed_default_roles

        db, _wid = _seeded(session_factory)
        before = (
            db.query(ChatbotRole).count(),
            db.query(ChatbotRoleDomain).count(),
            db.query(ChatbotRoleField).count(),
        )
        seed_default_roles(db)
        db.commit()
        after = (
            db.query(ChatbotRole).count(),
            db.query(ChatbotRoleDomain).count(),
            db.query(ChatbotRoleField).count(),
        )
        assert after == before


class TestMapLegacyContacts:
    """AC-AM-19: role + overrides per legacy shape, computed from the diff."""

    def test_plain_five_agents_with_sellable_and_placed_is_sales_office_with_no_overrides(self, session_factory):
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        pk = _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(K_SELLABLE, K_PLACED), name="Plain A")
        map_legacy_contacts(db)
        db.commit()
        assert _role_codes(db, pk) == ["sales_office"]
        assert _override_count(db, pk) == 0
        access = _access(db, pk)
        assert {K_SELLABLE, K_PLACED} <= set(access.attributes)
        assert {"order", "incoming", "inventory", "purchase_order"} <= access.domains
        assert "purchase_cost" not in access.domains
        assert access.sees_all_customers is True

    def test_only_general_enquiries_with_sellable_and_placed_is_sales_office(self, session_factory):
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        pk = _legacy_contact(db, wid, agents, agents=("general_enquiries",), keys=(K_SELLABLE, K_PLACED), name="CONTACT K")
        map_legacy_contacts(db)
        db.commit()
        assert _role_codes(db, pk) == ["sales_office"]
        assert _override_count(db, pk) == 0

    def test_five_agents_with_no_keys_is_sales_office_minus_the_sensitive_stock_and_po_rows(self, session_factory):
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        pk = _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(), name="CONTACT Q")
        map_legacy_contacts(db)
        db.commit()
        assert _role_codes(db, pk) == ["sales_office"]
        assert _override_count(db, pk) > 0
        access = _access(db, pk)
        assert K_SELLABLE not in access.attributes
        assert K_PLACED not in access.attributes
        assert "purchase_order" not in access.domains
        assert {"inventory", "order", "incoming"} <= access.domains, "still reaches stock, orders, incoming"

    def test_a_namesake_with_the_same_shape_maps_identically(self, session_factory):
        """Never by name: two differently named contacts of one shape get one result."""
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        a = _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(), name="CONTACT A")
        b = _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(), name=rid("someone-else"))
        map_legacy_contacts(db)
        db.commit()
        assert _role_codes(db, a) == _role_codes(db, b)
        assert _override_count(db, a) == _override_count(db, b) > 0
        assert _access(db, a).attributes == _access(db, b).attributes
        assert _access(db, a).domains == _access(db, b).domains

    def test_outstanding_key_on_top_of_sales_office_is_an_override(self, session_factory):
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        pk = _legacy_contact(
            db, wid, agents, agents=FIVE_AGENTS, keys=(K_SELLABLE, K_PLACED, K_OUTSTANDING), name="CONTACT F"
        )
        map_legacy_contacts(db)
        db.commit()
        assert _role_codes(db, pk) == ["sales_office"]
        assert _override_count(db, pk) > 0
        assert K_OUTSTANDING in _access(db, pk).attributes
        from app.models.chatbot_access import ContactAccessOverride

        rows = db.query(ContactAccessOverride).filter(ContactAccessOverride.contact_id == pk).all()
        assert [(o.domain_name, o.field_key, o.granted) for o in rows] == [("order", K_OUTSTANDING, True)]

    def test_cost_and_supplier_keys_make_a_purchasing_contact(self, session_factory):
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        pk = _legacy_contact(
            db, wid, agents, agents=FIVE_AGENTS + ("conversation_analysis",),
            keys=(K_SELLABLE, K_COST, K_PLACED, K_SUPPLIER), name="CONTACT P",
        )
        map_legacy_contacts(db)
        db.commit()
        assert _role_codes(db, pk) == ["purchasing"]
        assert _override_count(db, pk) == 0
        access = _access(db, pk)
        assert {K_SELLABLE, K_COST, K_PLACED, K_SUPPLIER} <= set(access.attributes)
        assert "purchase_cost" in access.domains

    def test_incoming_field_overrides_on_a_purchasing_contact_are_kept_as_field_overrides(self, session_factory):
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        extra = [f for f in INCOMING_FIELDS if f not in DEFAULT_ALLOWED]
        assert len(extra) == 20
        pk = _legacy_contact(
            db, wid, agents, agents=FIVE_AGENTS,
            keys=(K_SELLABLE, K_COST, K_PLACED, K_SUPPLIER), incoming_overrides=extra, name="CONTACT O",
        )
        map_legacy_contacts(db)
        db.commit()
        assert _role_codes(db, pk) == ["purchasing"]
        attributes = set(_access(db, pk).attributes)
        assert {f"incoming_stock.{f}" for f in extra} <= attributes
        assert _override_count(db, pk) >= 20

    def test_all_seven_keys_with_the_ideation_agent_is_management(self, session_factory):
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        pk = _legacy_contact(db, wid, agents, agents=ALL_AGENTS, keys=ALL_SEVEN, name="CONTACT M")
        map_legacy_contacts(db)
        db.commit()
        assert _role_codes(db, pk) == ["management"]
        assert _override_count(db, pk) == 0
        access = _access(db, pk)
        assert set(ALL_SEVEN) <= set(access.attributes)
        assert {"ideate", "sales", "purchase_cost"} <= access.domains
        assert not access.domains & {"low_stock", "outstanding"}, "reports are fields, never domains"

    def test_reveal_keys_but_no_agent_gets_no_role(self, session_factory):
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        pk = _legacy_contact(db, wid, agents, agents=(), keys=(K_SELLABLE, K_PLACED), name="CONTACT N")
        map_legacy_contacts(db)
        db.commit()
        assert _role_codes(db, pk) == []
        access = _access(db, pk)
        assert access.domains == frozenset()
        assert access.attributes == ()

    def test_a_contact_with_nothing_gets_no_role(self, session_factory):
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        _agents(db)
        pk, _ = make_contact(db, workspace_id=wid)
        map_legacy_contacts(db)
        db.commit()
        assert _role_codes(db, pk) == []
        assert _override_count(db, pk) == 0

    def test_mapping_twice_is_idempotent(self, session_factory):
        from app.models.chatbot_access import ContactAccessOverride, ContactChatbotRole
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(), name="CONTACT Q")
        _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(K_SELLABLE, K_PLACED, K_OUTSTANDING))
        map_legacy_contacts(db)
        db.commit()
        before = (db.query(ContactChatbotRole).count(), db.query(ContactAccessOverride).count())
        map_legacy_contacts(db)
        db.commit()
        assert (db.query(ContactChatbotRole).count(), db.query(ContactAccessOverride).count()) == before

    def test_a_domain_added_after_the_mapping_is_granted_to_nobody(self, session_factory):
        """AC-AM-16."""
        from app.models.chatbot_access import ChatbotRoleDomain
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        pks = [
            _legacy_contact(db, wid, agents, agents=ALL_AGENTS, keys=ALL_SEVEN),
            _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(K_SELLABLE, K_PLACED)),
        ]
        map_legacy_contacts(db)
        db.commit()
        make_domain(db, "zzt_brand_new", reveal_key="zzt.brand_new")
        make_field(db, "zzt_brand_new", "zzt.brand_new.field")
        assert db.query(ChatbotRoleDomain).filter(ChatbotRoleDomain.domain_name == "zzt_brand_new").count() == 0
        for pk in pks:
            access = _access(db, pk)
            assert "zzt_brand_new" not in access.domains
            assert "zzt.brand_new" not in access.attributes
            assert "zzt.brand_new.field" not in access.attributes


class TestAccessLossReport:
    """AC-AM-20."""

    def _mapped(self, session_factory):
        from app.services.chatbot.access_seed import map_legacy_contacts

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        contacts = {
            "plain": _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(K_SELLABLE, K_PLACED)),
            "CONTACT Q": _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=()),
            "CONTACT F": _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=(K_SELLABLE, K_PLACED, K_OUTSTANDING)),
            "purchasing": _legacy_contact(
                db, wid, agents, agents=FIVE_AGENTS, keys=(K_SELLABLE, K_COST, K_PLACED, K_SUPPLIER),
                incoming_overrides=[f for f in INCOMING_FIELDS if f not in DEFAULT_ALLOWED],
            ),
            "management": _legacy_contact(db, wid, agents, agents=ALL_AGENTS, keys=ALL_SEVEN),
            "no_agent": _legacy_contact(db, wid, agents, agents=(), keys=(K_SELLABLE, K_PLACED)),
            "nothing": make_contact(db, workspace_id=wid)[0],
        }
        map_legacy_contacts(db)
        db.commit()
        return db, contacts

    def test_report_is_empty_after_mapping(self, session_factory):
        from app.services.chatbot.access_seed import access_loss_report

        db, contacts = self._mapped(session_factory)
        report = access_loss_report(db)
        mine = [row for row in report if set(map(str, row.values())) & set(contacts.values())]
        assert mine == [], mine

    def test_report_names_the_contact_and_key_when_a_grant_is_lost(self, session_factory):
        """Negative control: the report is not trivially empty."""
        from app.models.chatbot_access import ContactChatbotRole
        from app.services.chatbot.access_seed import access_loss_report

        db, contacts = self._mapped(session_factory)
        db.query(ContactChatbotRole).filter(ContactChatbotRole.contact_id == contacts["plain"]).delete()
        db.commit()
        report = access_loss_report(db)
        rows = [row for row in report if contacts["plain"] in set(map(str, row.values()))]
        assert rows, "losing the role must be reported"
        reported_keys = {v for row in rows for v in row.values() if isinstance(v, str)}
        assert {K_SELLABLE, K_PLACED} <= reported_keys
        assert not [r for r in report if contacts["CONTACT Q"] in set(map(str, r.values()))]

    def test_a_contact_that_was_refused_before_is_not_reported(self, session_factory):
        """Reveal keys but no agent: refused every turn today, no role after, nothing lost."""
        from app.services.chatbot.access_seed import access_loss_report

        db, contacts = self._mapped(session_factory)
        report = access_loss_report(db)
        assert not [r for r in report if contacts["no_agent"] in set(map(str, r.values()))]


def _office_type(db, pk, name="Sorento Office") -> None:
    code = rid("at")[:50]
    db.execute(
        text("INSERT INTO contact_access_types (code, name, is_active, keywords) VALUES (:c, :n, true, '[]'::jsonb)"),
        {"c": code, "n": name},
    )
    db.execute(
        text("INSERT INTO respond_contact_access_types (contact_id, access_type_code) VALUES (:c, :t)"),
        {"c": pk, "t": code},
    )
    db.commit()


class TestLinkedNonOfficeContactsMapToDealer:
    """Owner answer Q1 (AC-AM-19/20): customer scope is never widened by the mapping."""

    def _world(self, session_factory):
        from app.services.chatbot.access_seed import map_legacy_contacts
        from tests.chatbot._access_seed import make_customer_link

        db, wid = _seeded(session_factory)
        agents = _agents(db)
        keys = (K_SELLABLE, K_PLACED)
        dealer = _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=keys, name="Linked A")
        make_customer_link(db, dealer)
        office = _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=keys, name="Linked Office")
        make_customer_link(db, office)
        _office_type(db, office)
        unlinked = _legacy_contact(db, wid, agents, agents=FIVE_AGENTS, keys=keys, name="Unlinked A")
        map_legacy_contacts(db)
        db.commit()
        return db, dealer, office, unlinked

    def test_linked_non_office_contact_gets_dealer_plus_add_overrides(self, session_factory):
        db, dealer, _office, _unlinked = self._world(session_factory)
        assert _role_codes(db, dealer) == ["dealer"]
        access = _access(db, dealer)
        assert access.sees_all_customers is False
        assert {K_SELLABLE, K_PLACED} <= set(access.attributes)
        assert _override_count(db, dealer) > 0

    def test_a_linked_office_contact_keeps_the_internal_role(self, session_factory):
        db, _dealer, office, _unlinked = self._world(session_factory)
        assert _role_codes(db, office) == ["sales_office"]

    def test_an_unlinked_non_office_contact_keeps_todays_mapping(self, session_factory):
        db, _dealer, _office, unlinked = self._world(session_factory)
        assert _role_codes(db, unlinked) == ["sales_office"]

    def test_the_gain_report_is_empty_after_mapping(self, session_factory):
        from app.services.chatbot.access_seed import access_gain_report

        db, *_ = self._world(session_factory)
        assert access_gain_report(db) == []

    def test_the_gain_report_names_a_contact_that_became_wider_on_customers(self, session_factory):
        from app.services.chatbot.access_seed import access_gain_report
        from tests.chatbot._access_seed import give_role as _give

        db, dealer, _office, _unlinked = self._world(session_factory)
        _give(db, dealer, _role(db, "sales_office").id)
        report = access_gain_report(db)
        assert [r["contact_id"] for r in report] == [dealer]
        assert report[0]["kind"] == "customers"
