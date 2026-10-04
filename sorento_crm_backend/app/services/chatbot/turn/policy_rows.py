# The seeded policy rows for `chatbot_domains` and `chatbot_entity_kinds` (AC-1501,
# AC-1502). ONE copy: `alembic/versions/chatbot_rearch_s0.py` seeds a real database from
# these lists, and `policy.load_policy` falls back to them when the tables are absent or
# empty - which is every blank-schema test fixture, since neither table is built by
# `Base.metadata.create_all` alone.
#
# Data only. Nothing here reads a message, calls a database or imports a lane.
#
# FROZEN SEED DATA (captain ruling, 16 Sep 2026). Any change to the policy goes
# through a NEW migration against the live tables, never by editing this file: these
# rows are what a fresh database starts from, and an edit here would silently
# disagree with every database that has already been seeded.
from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------- #
# RECORD_KEY_KIND - hand pass 12 round 2, Group C (owner ruling, 21 Sep 2026). Not a
# `chatbot_domains` column: checked `DomainPolicy` (turn/policy.py) and the seeded rows
# below, neither carries anything named a "record key" today, so this is the smallest
# addition - one plain dict, three entries, read by `turn_runtime.make_tool_runner`'s
# own rerun-on-miss check. NOT database-seeded (unlike DEFAULT_DOMAIN_ROWS below) and
# so not under the "frozen seed data" rule - a plain Python constant, edited directly.
#
# The entity KIND (the same string `Focus`'s own extra-bucket keys and `_spec_row`'s
# `entity_type` use, folded through `turn.state.EXTRA_KIND_ALIASES` before comparison)
# that IS this domain's own record - the thing a customer names when they mean ONE
# specific row, not a filter over many: a shipment/container number for incoming, an
# order number for order (the same "order"/"customer_order" axis EXTRA_KIND_ALIASES
# already folds to one bucket), a PO number for purchase_order.
RECORD_KEY_KIND: dict[str, str] = {
    "incoming": "inbound_shipment",
    "order": "order",
    # Not exercised by any test this round - no live PO-number resolver path exists
    # yet (`lanes/business/gate.py::ALLOWED["purchase_order"]` since 29 Sep 2026 lists
    # product/warehouse/category/brand, none of them a PO number's own hint). Named for the day one
    # lands, after the tool's own field key (`crm_procurement_po_placed_list`'s
    # envelope already keys its own number "po_number").
    "purchase_order": "po_number",
}

# --------------------------------------------------------------------------- #
# chatbot_domains seed - one row per app.services.chatbot.contracts.DOMAIN_SPEC entry,
# in that dict's own declaration order (sort_order). Every fact below is read off that
# constant (or a sibling one named in the docstring), not invented: label from
# lanes.business.answer.DOMAIN_LABELS where named, escalation_team_code from
# DOMAIN_SPEC.escalation_team (one of contracts.SUGGESTED_TEAMS), takes_date_filter from
# whether any of the domain's tools appears in lanes.business.fetch.DATE_PARAMS,
# reveal_key from the three named grant constants (answer.DOMAIN_GRANT_REQUIRED,
# answer._CROSSDOMAIN_RUNG_GRANT) plus the inline "inventory.sellable" gate in fetch.py,
# ladder from answer.DEFAULT_CROSSDOMAIN_LADDER (== the system_settings
# chatbot_crossdomain_ladder default, migration 491), narrowing from the six triples
# AC-1526 names literally.
# --------------------------------------------------------------------------- #
DEFAULT_DOMAIN_ROWS: list[dict[str, Any]] = [
    dict(
        name="master_products",
        label="product information",
        intents=["check_product"],
        tools=[
            "crm_master_products_list",
            "crm_master_brands_list",
            "crm_master_product_categories_list",
            "crm_master_units_of_measure_list",
        ],
        escalation_team_code="purchasing",
        switch_words=[
            "catalogue", "catalog", "spec", "specs", "specification",
            "specifications", "dimension", "dimensions",
        ],
        narrowing={},
        reveal_key=None,
        supported=True,
        ladder=[],
    ),
    dict(
        name="product_attachment",
        label="product attachments",
        intents=["check_product_attachment"],
        tools=["crm_master_product_attachments_list", "crm_certificates_list"],
        escalation_team_code="marketing_product",
        switch_words=[],
        # S6b (owner ruling, hand-pass 1 finding 4): a document ask naming several
        # products asks which one, the same `must_narrow_one` the order domain applies
        # to its customer. Migration `chatbot_rearch_s6b` carries it onto a seeded DB.
        #
        # S12 (owner ruling 21 Sep 2026, hand pass 12): `narrow_by_tier`, not
        # `must_narrow_one` - a document ask over a family answers the tier the
        # customer is on instead of asking which variant. Migration
        # `chatbot_rearch_s12` carries it onto a seeded DB.
        narrowing={"attachment_type": "narrow_by_type", "product": "narrow_by_tier"},
        reveal_key=None,
        supported=True,
        ladder=[],
    ),
    dict(
        name="promotion",
        label="promotions",
        intents=["check_promotion"],
        tools=[
            "crm_marketing_promotions_list",
            "crm_marketing_promotion_attachments_list",
            "crm_marketing_promotion_products_list",
        ],
        escalation_team_code="marketing_promotion",
        switch_words=["promo", "promos", "promotion", "promotions", "promosi"],
        # S12 (owner ruling 21 Sep 2026, hand pass 12): the product token on a promotion
        # ask settles the same way the tier does. Migration `chatbot_rearch_s12` carries
        # it onto a seeded DB.
        narrowing={"tier": "narrow_by_tier", "product": "narrow_by_tier"},
        reveal_key=None,
        supported=True,
        ladder=[],
    ),
    dict(
        name="forms",
        label="forms",
        intents=["get_forms"],
        tools=["crm_forms_management_forms_list"],
        escalation_team_code="marketing_form",
        switch_words=[],
        # Item 1 (17 Sep 2026): a picked form settles through the SAME policy
        # `product_attachment.product` uses - a carry already holding a uuid (the
        # pick) passes straight through, several named forms ask, one bare name
        # defers to the resolver. Migration `chatbot_rearch_s7` carries it onto a
        # seeded DB.
        narrowing={"form": "must_narrow_one"},
        reveal_key=None,
        supported=True,
        ladder=[],
    ),
    dict(
        name="inventory",
        label="stock",
        intents=["check_stock", "low_stock_report"],
        tools=[
            "crm_inventory_stock_balance_list",
            "crm_inventory_warehouses_list",
            "crm_low_stock_report",
        ],
        escalation_team_code="warehouse",
        switch_words=[
            "stock", "stocks", "inventory", "stok", "qty", "quantity",
            "low stock", "reorder report", "below level",
        ],
        narrowing={"product": "list_all"},
        # The inline gate in lanes/business/fetch.py: a sellable-stock answer is
        # refused without this reveal.
        reveal_key="inventory.sellable",
        supported=True,
        ladder=["incoming", "purchase_order"],
    ),
    dict(
        name="order",
        label="orders",
        intents=["check_order"],
        tools=[
            "crm_order_management_orders_list",
            "crm_order_management_orders_by_product_list",
            "crm_master_customers_list",
            "crm_outstanding_report",
            # PLAN-chatbot-sales-report.md S4 wiring point 3: an allow-list member only
            # (`fetch.CHATBOT_READ_ONLY_TOOLS` is this seed's own union), NEVER
            # `tools[0]` - the pick is an override in
            # `lanes/business/__init__.py::run_fetch`, beside the outstanding one.
            "crm_sales_report",
            # PLAN-chatbot-top-x-hot-selling-24sep.md S3 (AC-1941): same rule, an
            # allow-list member only; migration `chatbot_top_selling_tool`.
            "crm_top_selling_report",
            # PLAN-retail-sales-reports-26sep S1: an allow-list member only, picked by
            # the `sales_analysis` override in `run_fetch`, never `tools[0]`. Migration
            # `sales_s1_reports_module` adds it to a seeded database.
            "crm_sales_analysis",
        ],
        escalation_team_code="customer_service",
        switch_words=[
            "order", "orders", "outstanding", "tempahan",
            "delivery", "deliveries", "deliver", "delivered",
            "penghantaran", "hantar", "dihantar",
        ],
        # S6d (owner hand pass 2, item 6): the product token on an order ask resolves
        # and FILTERS the answer. Without a policy of its own it contributed neither an
        # entity nor a filter, and the outstanding report ran over every product.
        #
        # S12 (owner ruling 21 Sep 2026, hand pass 12): `list_all`, not
        # `optional_filter` - an order ask naming a product family answers over every
        # variant rather than narrowing to one code, the same way `inventory` and
        # `purchase_cost` already do. Migration `chatbot_rearch_s12` carries it onto a
        # seeded DB.
        #
        # Hand pass 12, Group B: "order" narrows `narrow_to_code`, the same policy
        # value `incoming`/`purchase_order` already give their own `product` kind - a
        # did-you-mean pick over an order token is ALREADY SETTLED the moment it is
        # picked (it carries a uuid), so this is the `just_picked` shortcut
        # (`turn/narrow.py::decide`) most of the time in practice, falling through to
        # the ordinary `narrow_to_code` rules on any later turn that still carries it.
        # `EXTRA_KIND_ALIASES` (`turn/state.py`) folds "customer_order"/"order_number"
        # onto this SAME "order" bucket, so one row covers every entity_type the
        # resolver types an order token with.
        narrowing={
            "customer": "must_narrow_one",
            "product": "list_all",
            "order": "narrow_to_code",
        },
        # answer._OUTSTANDING_SO_GRANT ("sales_orders.outstanding"): the SO arm of an
        # outstanding-order answer is refused without it.
        reveal_key="sales_orders.outstanding",
        supported=True,
        ladder=[],
    ),
    dict(
        name="incoming",
        label="incoming stock",
        intents=["check_incoming"],
        tools=[
            "crm_incoming_stock_list",
            "crm_incoming_stock_by_product",
            "crm_incoming_stock_shipments",
        ],
        escalation_team_code="purchasing",
        switch_words=["incoming", "eta", "shipment", "shipments", "arriving", "container", "containers"],
        narrowing={"product": "narrow_to_code"},
        reveal_key=None,
        supported=True,
        ladder=["inventory", "purchase_order"],
    ),
    dict(
        name="portal_link",
        label="this request",
        intents=["get_portal_link"],
        tools=["crm_portal_link_get"],
        escalation_team_code=None,
        switch_words=[],
        narrowing={},
        reveal_key=None,
        supported=True,
        ladder=[],
    ),
    dict(
        name="resource_attachment",
        label="resource attachments",
        intents=["get_resource_attachment"],
        tools=[
            "crm_resource_attachments_list",
            "crm_resource_attachments_catalogue",
            "crm_resource_attachments_current_stock_list",
        ],
        escalation_team_code=None,
        switch_words=[],
        narrowing={},
        reveal_key=None,
        supported=True,
        ladder=[],
    ),
    dict(
        name="goods_receive",
        label="goods receive",
        intents=["check_goods_receive"],
        tools=[],
        escalation_team_code=None,
        switch_words=[],
        narrowing={},
        reveal_key=None,
        supported=False,
        ladder=[],
    ),
    dict(
        name="spo_allocation",
        label="last in",
        intents=["check_spo"],
        tools=["crm_procurement_spo_allocations_last_receipt_list"],
        escalation_team_code=None,
        switch_words=["spo"],
        narrowing={"product": "narrow_to_code"},
        reveal_key=None,
        supported=True,
        ladder=[],
    ),
    dict(
        name="ideate",
        label="ideas",
        intents=["submit_idea"],
        tools=[],
        escalation_team_code=None,
        switch_words=[],
        narrowing={},
        reveal_key=None,
        supported=True,
        ladder=[],
    ),
    dict(
        name="purchase_order",
        label="outstanding purchase orders",
        intents=["check_po"],
        tools=["crm_procurement_po_placed_list"],
        escalation_team_code="purchasing",
        switch_words=["po"],
        narrowing={"product": "narrow_to_code"},
        # answer._CROSSDOMAIN_RUNG_GRANT: the PO rung of the cross-domain climb is
        # refused without it.
        reveal_key="purchase_orders.placed",
        supported=True,
        ladder=[],
    ),
    dict(
        name="purchase_cost",
        label="last purchase cost",
        intents=["check_po_cost"],
        tools=["crm_procurement_po_last_cost_list"],
        escalation_team_code="purchasing",
        switch_words=[],
        # S6d (owner hand pass 2, item 3): the cost answer LISTS every variant, the way
        # inventory does - a family was a picker before the answer (turn 70be252c).
        # S8 (hand pass 6, item 6): "list_all" also ran with NO product at all - a
        # "purchase cost" with nothing carried and nothing named reached the tool with
        # no filter, and that tool refuses an unfiltered call. "narrow_by_type" keeps
        # the family-list behaviour once a product is in play (its non-empty branch is
        # the same pass-through `list_all` gives) and asks which product only when
        # there is truly nothing to list.
        narrowing={"product": "narrow_by_type"},
        # answer.DOMAIN_GRANT_REQUIRED["purchase_cost"]: the whole domain is refused
        # without it.
        reveal_key="purchase_orders.cost",
        supported=True,
        ladder=[],
    ),
]

# lanes/business/fetch.DATE_PARAMS's own keys - which tools take a date window. A
# domain's takes_date_filter is true when ANY of its tools appears here (AC-1501).
DATE_PARAM_TOOLS: set[str] = {
    "crm_order_management_orders_list",
    "crm_order_management_orders_by_product_list",
    "crm_incoming_stock_list",
    "crm_incoming_stock_by_product",
    "crm_incoming_stock_shipments",
    "crm_marketing_promotions_list",
    "crm_resource_attachments_list",
    "crm_resource_attachments_catalogue",
    "crm_sla_conversation_event_logs_list",
    "crm_procurement_po_placed_list",
    "crm_outstanding_report",
    "crm_sales_report",
    "crm_low_stock_report",
    "crm_top_selling_report",
    "crm_sales_analysis",
}

# --------------------------------------------------------------------------- #
# chatbot_entity_kinds seed - one row per contracts.ENTITY_HINTS kind, in that tuple's
# own order. base_property_words is populated for "product" only, from
# lanes/business/fetch._BASE_PROPERTY_WORDS plus "discontinued" (-> is_discontinued,
# AC-1502's own literal) and "brand" (-> some column, non-empty).
# --------------------------------------------------------------------------- #
PRODUCT_BASE_PROPERTY_WORDS: dict[str, str] = {
    "price": "list_price",
    "list price": "list_price",
    "harga": "list_price",
    "cost": "cost_price",
    "dimension": "dimensions",
    "dimensions": "dimensions",
    "size": "dimensions",
    "ukuran": "dimensions",
    "saiz": "dimensions",
    "description": "description",
    "name": "product_name",
    "discontinued": "is_discontinued",
    "brand": "brand_id",
}

SPECIFICATION_KIND_ROW: dict[str, Any] = dict(
    kind="specification",
    label="Specification",
    resolver_source="product_spec_registry",
    did_you_mean=False,
    default_narrowing="optional_filter",
    family_grouping=None,
    base_property_words={},
    roster_cap=50,
)

DEFAULT_KIND_ROWS: list[dict[str, Any]] = [
    dict(
        kind="product",
        label="Product",
        resolver_source="master_products",
        did_you_mean=True,
        default_narrowing="list_all",
        family_grouping="base_code",
        base_property_words=PRODUCT_BASE_PROPERTY_WORDS,
        roster_cap=50,
    ),
    dict(
        kind="promotion",
        label="Promotion",
        resolver_source="promotions",
        did_you_mean=True,
        default_narrowing="optional_filter",
        family_grouping=None,
        base_property_words={},
        roster_cap=50,
    ),
    dict(
        kind="customer",
        label="Customer",
        resolver_source="customers",
        did_you_mean=True,
        default_narrowing="must_narrow_one",
        family_grouping="ledger_family",
        base_property_words={},
        roster_cap=50,
    ),
    dict(
        kind="transporter",
        label="Transporter",
        resolver_source="transporters",
        did_you_mean=True,
        default_narrowing="optional_filter",
        family_grouping=None,
        base_property_words={},
        roster_cap=50,
    ),
    dict(
        kind="inbound_shipment",
        label="Inbound shipment",
        resolver_source="packing_lists",
        did_you_mean=False,
        default_narrowing="optional_filter",
        family_grouping=None,
        base_property_words={},
        roster_cap=50,
    ),
    dict(
        kind="warehouse",
        label="Warehouse",
        resolver_source="warehouses",
        did_you_mean=True,
        default_narrowing="optional_filter",
        family_grouping=None,
        base_property_words={},
        roster_cap=50,
    ),
    dict(
        kind="attachment",
        label="Attachment",
        resolver_source="attachments",
        did_you_mean=False,
        default_narrowing="not_applicable",
        family_grouping=None,
        base_property_words={},
        roster_cap=50,
    ),
    dict(
        kind="form",
        label="Form",
        resolver_source="forms",
        did_you_mean=False,
        default_narrowing="not_applicable",
        family_grouping=None,
        base_property_words={},
        roster_cap=50,
    ),
    dict(
        kind="order",
        label="Order",
        resolver_source="orders",
        did_you_mean=False,
        default_narrowing="not_applicable",
        family_grouping=None,
        base_property_words={},
        roster_cap=50,
    ),
    dict(
        kind="category",
        label="Category",
        resolver_source="product_categories",
        did_you_mean=True,
        default_narrowing="optional_filter",
        family_grouping=None,
        base_property_words={},
        roster_cap=50,
    ),
    dict(
        kind="brand",
        label="Brand",
        resolver_source="brands",
        did_you_mean=True,
        default_narrowing="optional_filter",
        family_grouping=None,
        base_property_words={},
        roster_cap=50,
    ),
    dict(
        kind="attachment_type",
        label="Attachment type",
        resolver_source="attachment_types",
        did_you_mean=True,
        default_narrowing="narrow_by_type",
        family_grouping=None,
        base_property_words={},
        roster_cap=50,
    ),    # Fix round 8 on PR #833 (migration `spk_0001_specification_kind`): a product
    # property, grounded against the specification registry after the parse.
    SPECIFICATION_KIND_ROW,
]

# `DEFAULT_TIER_ORDER` used to live here (AC-1502). Gone (AC-1594, S6): the one literal is
# `app/modules/chatbot/lane_vocabulary.py::default_tier_order()` now - the core-safe
# doorway `SystemSetting.chatbot_tier_order`'s own Python default, the S0 migration's
# seed and `policy.load_policy`'s blank-schema fallback all read, so a fourth copy never
# has the chance to disagree with the other three.

# --------------------------------------------------------------------------- #
# Hand-curated per-TOOL exception lists (AC-1594, S6). Not domain or kind data - a tool's
# narrowing requirement is a property of the MCP argument shape, which has no row in
# either policy table - so these moved here rather than into `Policy`, matching the
# `DATE_PARAM_TOOLS` precedent just above: frozen, reasoned data that lives in the ONE
# seed-data module instead of as a third list on `lanes/business/fetch.py` itself.
# --------------------------------------------------------------------------- #

# SF6 (security review, PLAN-chatbot-last-purchase-cost.md, 12 Sep 2026): tools whose
# UNSCOPED branch answers every product (or every product at a warehouse) rather than
# refusing, so an entity filter is required rather than merely accepted.
# `crm_resource_attachments_list` - a warehouse/document-type filter is enough narrowing.
# `crm_procurement_po_last_cost_list` also joins for the same reason - see
# `PRODUCT_ID_REQUIRED_TOOLS` below, which narrows its own bar further.
ENTITY_FILTER_REQUIRED_TOOLS: frozenset[str] = frozenset(
    {"crm_resource_attachments_list", "crm_procurement_po_last_cost_list"}
)

# SF6: of `ENTITY_FILTER_REQUIRED_TOOLS`, the one tool for which `warehouse_ids` alone is
# NOT enough narrowing - its unscoped branch is a plain top_n cap over every product at
# that warehouse, so a warehouse named with no product is still an unnamed-product leak.
PRODUCT_ID_REQUIRED_TOOLS: frozenset[str] = frozenset({"crm_procurement_po_last_cost_list"})
