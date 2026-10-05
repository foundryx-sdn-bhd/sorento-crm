"""Seed and legacy mapping for the chatbot access model (lane ACCESS-MODEL, slice S1).

Everything here is plain SQL through `sqlalchemy.text()` on whatever `.execute` object it is
given (a Session or a migration Connection) and imports no ORM model: a migration that seeds
through the live ORM breaks from-scratch upgrades the day the model gains a column.

Mapping rules are generic, never by name (`access-model-2oct-mapping.sql`): a contact that held
at least one active agent today gets Management (all three of sales report, low stock, cost),
else Purchasing (cost and supplier), else Sales office; then every legacy reveal key and every
per-contact incoming field is compared with what that role gives, and only the difference
becomes an override. A contact with no agent was refused every turn, and stays without a role.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text

K_SELLABLE = "inventory.sellable"
K_PLACED = "purchase_orders.placed"
K_SUPPLIER = "purchase_orders.supplier"
K_COST = "purchase_orders.cost"
K_OUTSTANDING = "sales_orders.outstanding"
K_SALES = "sales_orders.sales_report"
K_LOW = "scm.low_stock_report"
K_PO_NUMBER = "purchase_orders.po_number"
K_SPO_NUMBER = "spo_allocation.spo_number"
K_COST_SUPPLIER = "purchase_cost.supplier"
K_STAMP_INCOMING = "stamp.incoming"
K_STAMP_ATTACHMENT = "stamp.product_attachment"
LEGACY_KEYS = (K_SELLABLE, K_PLACED, K_SUPPLIER, K_COST, K_OUTSTANDING, K_SALES, K_LOW)

#: The 23 gated incoming-stock fields (`field_access.GATED_FIELDS`) with the label the Access
#: screens show; copied, not imported, so a migration never depends on live code.
INCOMING_FIELDS: tuple[tuple[str, str], ...] = (
    ("estimated_arrival_date", "ETA"),
    ("eta_delay_date", "ETA delay date"),
    ("inspection_date", "Inspection date"),
    ("approval_date", "Approval date"),
    ("gatepass_date", "Gatepass date"),
    ("warehouse_arrival_date", "Warehouse arrival date"),
    ("informed_collection_date", "Informed collection date"),
    ("collection_date", "Collection date"),
    ("loading_date", "Loading date"),
    ("etc_date", "ETC date"),
    ("etd_date", "ETD date"),
    ("liner_code", "Liner code"),
    ("china_forwarder", "China forwarder"),
    ("malaysia_forwarder", "Malaysia forwarder"),
    ("consignee", "Consignee"),
    ("delivery_warehouse", "Delivery warehouse"),
    ("free_days_available", "Free days available"),
    ("loc", "LOC"),
    ("stacked", "Stacked"),
    ("coa_permit_no", "COA permit no."),
    ("source_sheet", "Source sheet"),
    ("shipping_container_number", "Container no."),
    ("remaining_incoming_quantity", "Remaining quantity"),
)
_ETA = "incoming_stock.estimated_arrival_date"
_CONTAINER = "incoming_stock.shipping_container_number"
_QTY = "incoming_stock.remaining_incoming_quantity"

#: key -> (domain, label, kind, sort_order)
FIELD_REGISTRY: tuple[tuple[str, str, str, str], ...] = (
    (K_SELLABLE, "inventory", "Outstanding sales orders on stock", "field"),
    (K_PLACED, "inventory", "Quantity on order from suppliers", "field"),
    (K_LOW, "inventory", "Low stock report", "report"),
    (K_OUTSTANDING, "order", "Outstanding sales order report", "report"),
    (K_SUPPLIER, "purchase_order", "Supplier", "field"),
    (K_PO_NUMBER, "purchase_order", "PO number", "field"),
    (K_SPO_NUMBER, "spo_allocation", "SPO number", "field"),
    (K_COST_SUPPLIER, "purchase_cost", "Supplier", "field"),
    *((f"incoming_stock.{k}", "incoming", label, "field") for k, label in INCOMING_FIELDS),
    (K_STAMP_INCOMING, "incoming", "Show has / no incoming in pick lists", "field"),
    (K_STAMP_ATTACHMENT, "product_attachment", "Show has / no photos in pick lists", "field"),
)

_DEALER_DOMAINS = (
    "master_products", "product_attachment", "resource_attachment", "promotion", "forms",
    "portal_link", "inventory", "order", "incoming",
)
_SALES_OFFICE_DOMAINS = _DEALER_DOMAINS + ("spo_allocation", "purchase_order")
_PURCHASING_DOMAINS = _SALES_OFFICE_DOMAINS + ("purchase_cost",)
_WAREHOUSE_DOMAINS = ("inventory", "incoming", "spo_allocation", "master_products")
_STAMPS = (K_STAMP_INCOMING, K_STAMP_ATTACHMENT)
_SALES_OFFICE_FIELDS = (
    K_SELLABLE, K_PLACED, K_PO_NUMBER, K_SPO_NUMBER, _ETA, _CONTAINER, _QTY, *_STAMPS,
)

#: code -> (name, description, sees_all_customers, audience_tier, domains|None=every supported,
#:          fields|None=every field)
_ROLES: tuple[tuple[str, str, str, bool, str, Any, Any], ...] = (
    ("dealer", "Dealer", "Linked customers only", False, "dealer", _DEALER_DOMAINS,
     (_ETA, _QTY, *_STAMPS)),
    ("sales_office", "Sales office", "Sales office staff", True, "office", _SALES_OFFICE_DOMAINS,
     _SALES_OFFICE_FIELDS),
    ("purchasing", "Purchasing", "Purchasing staff", True, "office", _PURCHASING_DOMAINS,
     _SALES_OFFICE_FIELDS + (K_SUPPLIER, K_COST_SUPPLIER)),
    ("warehouse", "Warehouse", "Warehouse staff", True, "office", _WAREHOUSE_DOMAINS,
     (K_LOW, K_SPO_NUMBER, *(f"incoming_stock.{k}" for k, _ in INCOMING_FIELDS), *_STAMPS)),
    ("management", "Management", "Every supported topic", True, "office", None, None),
)

#: name -> (group, label, description)
DOMAIN_LABELS: dict[str, tuple[str, str, str]] = {
    "master_products": ("Products and marketing", "Product details", "Size, brand, category of a product"),
    "product_attachment": ("Products and marketing", "Product photos and documents", "Photos, spec sheets, certificates"),
    "resource_attachment": ("Products and marketing", "Catalogues and other documents", "Resource attachments"),
    "promotion": ("Products and marketing", "Promotions", "Current promotions for their tier"),
    "forms": ("Products and marketing", "Forms", "Forms they can fill in"),
    "portal_link": ("Products and marketing", "Their request portal link", "Portal link"),
    "inventory": ("Stock and incoming", "Stock available", "How much is in stock"),
    "incoming": ("Stock and incoming", "Incoming stock", "When stock arrives"),
    "spo_allocation": ("Stock and incoming", "When a product last came in", "Last in (SPO)"),
    "order": ("Orders", "Orders and deliveries", "Order status, DO"),
    "purchase_order": ("Purchasing", "Open purchase orders", "POs placed with suppliers"),
    "purchase_cost": ("Purchasing", "What we last paid", "Last purchase cost"),
    "sales": ("Reports", "Sales report", "Sales report, sales analysis, top selling"),
    "ideate": ("Other", "Share ideas", "Ideas sent to the team"),
}

#: domain -> escalation agent (CARD section 3b); a domain absent here offers no hand-off.
ESCALATION_AGENTS: dict[str, str] = {
    "master_products": "general_enquiries",
    "product_attachment": "general_enquiries",
    "promotion": "general_enquiries",
    "forms": "marketing_form",
    "inventory": "general_enquiries",
    "order": "order_enquiries",
    "incoming": "incoming_stock_enquiries",
    "purchase_order": "general_enquiries",
    "purchase_cost": "general_enquiries",
    "sales": "order_enquiries",
}


def _rows(db: Any, sql: str, **params: Any) -> list[Any]:
    return list(db.execute(text(sql), params).fetchall())


def seed_field_registry(db: Any) -> None:
    """The field and report rows, idempotent. Skips a row whose domain row does not exist.

    Also clears the `purchase_order` reveal_key: the on-order key has ONE path, the inventory
    field (review 6 Oct)."""
    db.execute(text("UPDATE chatbot_domains SET reveal_key = NULL WHERE name = 'purchase_order'"))
    for order, (key, domain, label, kind) in enumerate(FIELD_REGISTRY):
        db.execute(
            text(
                "INSERT INTO chatbot_domain_fields (id, domain_name, key, label, kind, sort_order) "
                "SELECT gen_random_uuid(), :d, :k, :l, :kind, :o "
                "WHERE EXISTS (SELECT 1 FROM chatbot_domains WHERE name = :d) "
                "ON CONFLICT (key) DO NOTHING"
            ),
            {"d": domain, "k": key, "l": label, "kind": kind, "o": order},
        )


def seed_domain_labels(db: Any) -> None:
    """Group, switch label and description of the seeded domains, and the `sales` row listing
    under Reports. Updates existing rows only; never adds a domain."""
    for name, (group, label, description) in DOMAIN_LABELS.items():
        db.execute(
            text(
                "UPDATE chatbot_domains SET access_group = :g, access_label = :l, "
                "access_description = :d WHERE name = :n"
            ),
            {"g": group, "l": label, "d": description, "n": name},
        )
    db.execute(text("UPDATE chatbot_domains SET access_section = 'reports' WHERE name = 'sales'"))


def seed_escalation_agents(db: Any) -> None:
    """Point each domain at its escalation agent, only where that agent exists and the domain
    has none yet."""
    for domain, agent in ESCALATION_AGENTS.items():
        db.execute(
            text(
                "UPDATE chatbot_domains SET escalation_agent_code = :a "
                "WHERE name = :d AND escalation_agent_code IS NULL "
                "AND EXISTS (SELECT 1 FROM access_agents WHERE code = :a)"
            ),
            {"a": agent, "d": domain},
        )


def seed_default_roles(db: Any) -> None:
    """The five presets and their ticks. Idempotent: an existing role keeps what the owner
    changed (no tick is added back or removed), only missing roles are created and ticked."""
    for order, (code, name, desc, sees_all, tier, domains, fields) in enumerate(_ROLES):
        created = db.execute(
            text(
                "INSERT INTO chatbot_roles (id, code, name, description, sees_all_customers, "
                "audience_tier, sort_order) VALUES (gen_random_uuid(), :c, :n, :d, :s, :t, :o) "
                "ON CONFLICT (code) DO NOTHING RETURNING id"
            ),
            {"c": code, "n": name, "d": desc, "s": sees_all, "t": tier, "o": order},
        ).first()
        if created is None:
            continue
        role_id = str(created[0])
        if domains is None:
            db.execute(
                text(
                    "INSERT INTO chatbot_role_domains (role_id, domain_name) "
                    "SELECT CAST(:r AS uuid), name FROM chatbot_domains WHERE supported"
                ),
                {"r": role_id},
            )
        else:
            db.execute(
                text(
                    "INSERT INTO chatbot_role_domains (role_id, domain_name) "
                    "SELECT CAST(:r AS uuid), name FROM chatbot_domains WHERE name = ANY(:d)"
                ),
                {"r": role_id, "d": list(domains)},
            )
        if fields is None:
            db.execute(
                text(
                    "INSERT INTO chatbot_role_fields (role_id, field_key) "
                    "SELECT CAST(:r AS uuid), key FROM chatbot_domain_fields"
                ),
                {"r": role_id},
            )
        else:
            db.execute(
                text(
                    "INSERT INTO chatbot_role_fields (role_id, field_key) "
                    "SELECT CAST(:r AS uuid), key FROM chatbot_domain_fields WHERE key = ANY(:f)"
                ),
                {"r": role_id, "f": list(fields)},
            )


# --- legacy mapping ---------------------------------------------------------------------------


def _role_grants(db: Any, code: str) -> tuple[str, set[str], set[str]] | None:
    row = db.execute(text("SELECT id FROM chatbot_roles WHERE code = :c"), {"c": code}).first()
    if row is None:
        return None
    rid = str(row[0])
    domains = {r[0] for r in _rows(db, "SELECT domain_name FROM chatbot_role_domains WHERE role_id = CAST(:r AS uuid)", r=rid)}
    fields = {r[0] for r in _rows(db, "SELECT field_key FROM chatbot_role_fields WHERE role_id = CAST(:r AS uuid)", r=rid)}
    return rid, domains, fields


def _key_paths(db: Any) -> dict[str, dict[str, Any]]:
    """For each legacy key, the ways the new tree can deliver it: a field row (domain, key)
    and/or the `reveal_key` of one or more domains."""
    paths: dict[str, dict[str, Any]] = {k: {"field": None, "domains": []} for k in LEGACY_KEYS}
    for key, domain in _rows(db, "SELECT key, domain_name FROM chatbot_domain_fields WHERE key = ANY(:k)", k=list(LEGACY_KEYS)):
        paths[key]["field"] = domain
    for name, key in _rows(db, "SELECT name, reveal_key FROM chatbot_domains WHERE reveal_key = ANY(:k)", k=list(LEGACY_KEYS)):
        paths[key]["domains"].append(name)
    return paths


def _legacy_contacts(db: Any) -> dict[str, dict[str, Any]]:
    """Per contact: active agent codes, granted reveal keys, allowed per-contact incoming
    fields and the incoming fields explicitly denied for the contact."""
    out: dict[str, dict[str, Any]] = {}

    def entry(cid: str) -> dict[str, Any]:
        return out.setdefault(
            cid, {"agents": set(), "keys": set(), "inc_on": set(), "inc_off": set(), "linked": False, "office": False}
        )

    for cid, code in _rows(
        db,
        "SELECT c.respond_contact_id, a.code FROM contact_agent_access c "
        "JOIN access_agents a ON a.id = c.agent_id WHERE c.is_allowed AND a.is_active "
        "AND (c.valid_to IS NULL OR c.valid_to > (now() AT TIME ZONE 'utc')) "
        "AND (c.valid_from IS NULL OR c.valid_from <= (now() AT TIME ZONE 'utc'))",
    ):
        entry(cid)["agents"].add(code)
    for cid, key in _rows(db, "SELECT respond_contact_id, field_key FROM contact_field_reveals WHERE granted"):
        entry(cid)["keys"].add(key)
    for cid, field, allowed in _rows(
        db,
        "SELECT contact_id, field_key, is_allowed FROM agent_field_access "
        "WHERE contact_id IS NOT NULL AND resource = 'incoming_stock'",
    ):
        entry(cid)["inc_on" if allowed else "inc_off"].add(f"incoming_stock.{field}")
    for (cid,) in _rows(db, "SELECT DISTINCT contact_id FROM respond_contact_customers"):
        if cid in out:
            out[cid]["linked"] = True
    for (cid,) in _rows(
        db,
        "SELECT DISTINCT rt.contact_id FROM respond_contact_access_types rt "
        "JOIN contact_access_types t ON t.code = rt.access_type_code "
        "WHERE t.name ILIKE '%office%' OR t.code ILIKE '%office%'",
    ):
        if cid in out:
            out[cid]["office"] = True
    return out


def _role_for(keys: set[str], *, linked_non_office: bool = False) -> str:
    # Ruling Q1: a linked contact outside the office keeps linked-only customers (Dealer);
    # whatever else it held comes back as add-overrides below.
    if linked_non_office:
        return "dealer"
    if {K_SALES, K_LOW, K_COST} <= keys:
        return "management"
    if {K_COST, K_SUPPLIER} <= keys:
        return "purchasing"
    return "sales_office"


def _insert_override(db: Any, contact: str, domain: str, field: str | None, granted: bool) -> None:
    db.execute(
        text(
            "INSERT INTO contact_access_overrides (id, contact_id, domain_name, field_key, granted) "
            "VALUES (gen_random_uuid(), :c, :d, :f, :g)"
        ),
        {"c": contact, "d": domain, "f": field, "g": granted},
    )


def map_legacy_contacts(db: Any) -> None:
    """Give every contact that held an agent a role plus the overrides that reproduce its
    legacy reveal keys and incoming-field overrides. Idempotent: a contact that already holds
    a role is left alone."""
    grants = {code: _role_grants(db, code) for code in ("dealer", "sales_office", "purchasing", "management")}
    if any(g is None for g in grants.values()):
        return
    paths = _key_paths(db)
    already = {r[0] for r in _rows(db, "SELECT DISTINCT contact_id FROM contact_chatbot_roles")}
    # Contacts that exist today; a legacy row of a deleted contact cannot be mapped.
    existing = {r[0] for r in _rows(db, "SELECT id FROM respond_contacts")}
    for cid, legacy in _legacy_contacts(db).items():
        if cid in already or cid not in existing or not legacy["agents"]:
            continue
        code = _role_for(legacy["keys"], linked_non_office=legacy["linked"] and not legacy["office"])
        role_id, role_domains, role_fields = grants[code]  # type: ignore[misc]
        db.execute(
            text("INSERT INTO contact_chatbot_roles (contact_id, role_id) VALUES (:c, CAST(:r AS uuid))"),
            {"c": cid, "r": role_id},
        )
        domains, fields = set(role_domains), set(role_fields)

        for key in LEGACY_KEYS:
            path = paths[key]
            field_domain = path["field"]
            via_field = field_domain is not None and field_domain in role_domains and key in role_fields
            via_domains = [d for d in path["domains"] if d in role_domains]
            wants = key in legacy["keys"]
            if not wants:
                if via_field:
                    _insert_override(db, cid, field_domain, key, False)
                    fields.discard(key)
                for d in via_domains:
                    _insert_override(db, cid, d, None, False)
                    domains.discard(d)
            elif not (via_field or via_domains):
                if field_domain is not None:
                    if field_domain not in domains:
                        _insert_override(db, cid, field_domain, None, True)
                        domains.add(field_domain)
                    _insert_override(db, cid, field_domain, key, True)
                    fields.add(key)
                else:
                    for d in path["domains"]:
                        _insert_override(db, cid, d, None, True)
                        domains.add(d)
        if "ideation" in legacy["agents"] and "ideate" not in role_domains and _domain_exists(db, "ideate"):
            _insert_override(db, cid, "ideate", None, True)

        registry = {r[0]: r[1] for r in _rows(db, "SELECT key, domain_name FROM chatbot_domain_fields WHERE key LIKE 'incoming_stock.%'")}
        for key in sorted(legacy["inc_on"]):
            if key in registry and key not in role_fields:
                _insert_override(db, cid, registry[key], key, True)
        for key in sorted(legacy["inc_off"]):
            if key in registry and key in role_fields:
                _insert_override(db, cid, registry[key], key, False)


def _domain_exists(db: Any, name: str) -> bool:
    return db.execute(text("SELECT 1 FROM chatbot_domains WHERE name = :n"), {"n": name}).first() is not None


def access_loss_report(db: Any) -> list[dict[str, str]]:
    """Grants a contact reached through an agent today that the new tree no longer gives.

    One row per lost thing: `{"contact_id", "name", "kind", "key"}`. `kind` is `role` (the
    contact holds no role at all), `reveal` (a legacy reveal key) or `incoming` (a per-contact
    incoming field). A contact that held no agent was refused every turn, so it is never
    reported. Empty list = nothing lost."""
    from app.services.chatbot.access_tree import tree_for_contact

    names = {r[0]: r[1] for r in _rows(db, "SELECT id, name FROM respond_contacts")}
    report: list[dict[str, str]] = []
    for cid, legacy in sorted(_legacy_contacts(db).items()):
        if cid not in names or not legacy["agents"]:
            continue
        access = tree_for_contact(db, cid)
        have = set(access.attributes)

        def lost(kind: str, key: str) -> None:
            report.append({"contact_id": cid, "name": names[cid] or "", "kind": kind, "key": key})

        if not access.roles and not access.domains:
            lost("role", "role")
        for key in sorted(legacy["keys"] & set(LEGACY_KEYS)):
            if key not in have:
                lost("reveal", key)
        for key in sorted(legacy["inc_on"]):
            if key not in have:
                lost("incoming", key)
    return report


def access_gain_report(db: Any) -> list[dict[str, str]]:
    """Contacts that now see MORE customers than they did: linked-only customers before (a
    linked contact outside the office), all customers now. One row per contact
    `{"contact_id", "name", "kind": "customers", "key"}`. Empty list = nobody gained (ruling Q1)."""
    from app.services.chatbot.access_tree import tree_for_contact

    names = {r[0]: r[1] for r in _rows(db, "SELECT id, name FROM respond_contacts")}
    report: list[dict[str, str]] = []
    for cid, legacy in sorted(_legacy_contacts(db).items()):
        if cid not in names or not legacy["agents"] or not legacy["linked"] or legacy["office"]:
            continue
        if tree_for_contact(db, cid).sees_all_customers:
            report.append({"contact_id": cid, "name": names[cid] or "", "kind": "customers", "key": "customers"})
    return report
