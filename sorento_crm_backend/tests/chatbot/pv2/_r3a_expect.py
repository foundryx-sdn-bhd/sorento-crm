"""PV2 redesign R3a: the values the retired code tables held at HEAD 2fb828b40 (crew/pv2 082cc73be), hard-coded.

Every table below is the value of a constant R3a retires into `chatbot_entity_kinds` / `chatbot_domains` columns; the
source file:line is where it lives today. The parity tests compare the registry (seed, migration data, live Policy) with
THESE literals, so a typo in the move is a red test and not a silent behaviour change. Not collected as a test module.
"""
from __future__ import annotations

from typing import Any

#: The 18 seeded kinds in policy_rows.DEFAULT_KIND_ROWS order (turn/policy_rows.py:447).
KINDS = (
    "product", "promotion", "customer", "transporter", "inbound_shipment", "warehouse", "attachment", "form",
    "order", "category", "brand", "attachment_type", "specification", "delivery_order", "sales_order",
    "customer_po_ref", "purchase_order", "purchase_request",
)
#: The 16 seeded domains in policy_rows.DEFAULT_DOMAIN_ROWS order (turn/policy_rows.py:55).
DOMAINS = (
    "master_products", "product_attachment", "promotion", "forms", "inventory", "order", "incoming", "portal_link",
    "resource_attachment", "goods_receive", "spo_allocation", "ideate", "purchase_order", "purchase_cost", "sales",
    "chat_analytics",
)

#: The column keys a stale schema (before pv2_0004) carries: a `SELECT *` has no others.
OLD_KIND_KEYS = (
    "kind", "label", "resolver_source", "did_you_mean", "default_narrowing", "family_grouping",
    "base_property_words", "roster_cap",
)
OLD_DOMAIN_KEYS = (
    "name", "label", "intents", "tools", "primary_tool", "escalation_team_code", "switch_words", "narrowing",
    "reveal_key", "supported", "ladder", "takes_date_filter", "sort_order",
)
NEW_KIND_FIELDS = (
    "labels", "plural_labels", "default_domain", "v1_not_found", "child_kinds", "printed_fields", "pv2_resolver",
)
NEW_DOMAIN_FIELDS = (
    "reads_quantity", "subject_required", "expands_sets", "expands_family", "single_subject", "carry_scope_kinds",
    "ask_labels",
)

# --------------------------------------------------------------------------- kinds

#: pv2/clarify_copy.py:18-39 `_KIND_WORD`, the entries whose kind HAS a row. A row kind with no entry here
#: (transporter, category, attachment_type, specification) has no word today: kind_word falls back to
#: kind.replace("_", " "), so its `labels` is {}.
LABELS: dict[str, dict[str, str]] = {
    "product": {"en": "product", "zh": "产品", "ms": "produk"},
    "customer": {"en": "customer", "zh": "客户", "ms": "pelanggan"},
    "brand": {"en": "brand", "zh": "品牌", "ms": "jenama"},
    "warehouse": {"en": "warehouse", "zh": "仓库", "ms": "gudang"},
    "order": {"en": "order", "zh": "订单", "ms": "pesanan"},
    "delivery_order": {"en": "delivery order", "zh": "送货单", "ms": "pesanan penghantaran"},
    "sales_order": {"en": "sales order", "zh": "销售订单", "ms": "pesanan jualan"},
    "customer_po_ref": {"en": "customer PO reference", "zh": "客户采购单号", "ms": "rujukan PO pelanggan"},
    "purchase_order": {"en": "purchase order", "zh": "采购单", "ms": "pesanan belian"},
    "purchase_request": {"en": "purchase request", "zh": "采购申请", "ms": "permohonan belian"},
    "inbound_shipment": {"en": "inbound shipment", "zh": "到货批次", "ms": "penghantaran stok masuk"},
    "attachment": {"en": "attachment", "zh": "附件", "ms": "lampiran"},
    "form": {"en": "form", "zh": "表格", "ms": "borang"},
    "promotion": {"en": "promotion", "zh": "促销", "ms": "promosi"},
}
#: The `_KIND_WORD` entries of kinds with NO row. They stay in code (R4 adds their rows).
CODE_FALLBACK_KIND_WORDS = frozenset(
    {"product_type", "order_number", "product_class", "customer_order", "supplier", "spec", "report_filter"}
)

#: pv2/clarify_copy.py:148-151 `_KIND_PLURAL`.
PLURAL_LABELS: dict[str, dict[str, str]] = {
    "product": {"en": "products", "zh": "产品", "ms": "produk"},
    "customer": {"en": "customers", "zh": "客户", "ms": "pelanggan"},
}

#: pv2/apply.py:634-639 `DOMAIN_BY_FOUND_KIND`.
DEFAULT_DOMAIN: dict[str, str] = {
    "delivery_order": "order", "sales_order": "order", "customer_po_ref": "order",
    "inbound_shipment": "incoming",
    "purchase_order": "purchase_order", "purchase_request": "purchase_order",
}

#: pv2/apply.py:1252 `DOCUMENT_KINDS`.
V1_NOT_FOUND = frozenset({"order", "delivery_order", "sales_order", "purchase_order", "inbound_shipment", "product"})

#: pv2/register.py:22-29 `_FIELD_KIND` (field -> kind, inverted): the printed fields that name the kind, and the
#: kind its containment source yields (register.py:58-70 stays code).
PRINTED_FIELDS: dict[str, frozenset[str]] = {
    "delivery_order": frozenset({"order_number", "order number"}),
    "purchase_order": frozenset({"po_number", "po number"}),
    "product": frozenset({"product_code", "product code"}),
}
#: Child kinds a document row lists (the order card's products): the map-R3 design, register.py `_PRODUCT_FIELDS`.
CHILD_KINDS: dict[str, tuple[str, ...]] = {
    "delivery_order": ("product",),
    "purchase_order": ("product",),
}

#: pv2/resolve.py:225-260 `_resolve_entity` dispatch (+ `_document_types`, REFERENCE_NARROW) and :149 `INDEX_KINDS`
#: and apply.py:252 `_FILTER_KINDS` (brand, attachment_type have rows; product_class / product_type do not and keep
#: the code fallback 'index').
PV2_RESOLVER: dict[str, str] = {
    "brand": "index", "attachment_type": "index",
    "product": "product", "customer": "customer", "warehouse": "warehouse",
    "inbound_shipment": "document", "form": "document", "order": "document", "delivery_order": "document",
    "sales_order": "document", "customer_po_ref": "document", "purchase_order": "document",
    "purchase_request": "document",
    # promotion, transporter, attachment, category, specification: NULL (the resolver does not hold them)
}

# --------------------------------------------------------------------------- domains

#: pv2/apply.py:302 `QUANTITY_DOMAINS`.
READS_QUANTITY = frozenset({"inventory"})
#: pv2/apply.py:425 `SUBJECT_REQUIRED_DOMAINS`.
SUBJECT_REQUIRED = frozenset({"inventory", "master_products"})
#: pv2/apply.py:1547 `SET_DOMAINS`.
EXPANDS_SETS = frozenset({"inventory"})
#: pv2/apply.py:1519 `FAMILY_DOMAINS`.
EXPANDS_FAMILY = frozenset({"incoming", "product_attachment"})
#: pv2/apply.py:58 `SINGLE_SUBJECT_DOMAINS`.
SINGLE_SUBJECT = frozenset({"product_attachment"})
#: pv2/apply.py:330 `CARRY_SCOPE_KINDS` (ordered).
CARRY_SCOPE_KINDS: dict[str, tuple[str, ...]] = {"order": ("product", "customer"), "sales": ("product", "customer")}
#: pv2/clarify_copy.py:144-147 `_CARRY_LABEL`.
ASK_LABELS: dict[str, dict[str, str]] = {
    "order": {"en": "Delivery", "zh": "送货", "ms": "Penghantaran"},
    "sales": {"en": "Sales", "zh": "销售", "ms": "Jualan"},
}


def expected_kind(kind: str) -> dict[str, Any]:
    """The seven new fields of one kind, normalised (tuples, dicts, None)."""
    return {
        "labels": LABELS.get(kind, {}),
        "plural_labels": PLURAL_LABELS.get(kind, {}),
        "default_domain": DEFAULT_DOMAIN.get(kind),
        "v1_not_found": kind in V1_NOT_FOUND,
        "child_kinds": CHILD_KINDS.get(kind, ()),
        "printed_fields": PRINTED_FIELDS.get(kind, frozenset()),
        "pv2_resolver": PV2_RESOLVER.get(kind),
    }


def expected_domain(name: str) -> dict[str, Any]:
    return {
        "reads_quantity": name in READS_QUANTITY,
        "subject_required": name in SUBJECT_REQUIRED,
        "expands_sets": name in EXPANDS_SETS,
        "expands_family": name in EXPANDS_FAMILY,
        "single_subject": name in SINGLE_SUBJECT,
        "carry_scope_kinds": CARRY_SCOPE_KINDS.get(name, ()),
        "ask_labels": ASK_LABELS.get(name, {}),
    }


def norm(field: str, value: Any) -> Any:
    """Compare shapes, not container types: a list or tuple is a tuple (a set for printed_fields), a mapping a dict."""
    if field == "printed_fields":
        return frozenset(value or ())
    if field in ("child_kinds", "carry_scope_kinds"):
        return tuple(value or ())
    if field in ("labels", "plural_labels", "ask_labels"):
        return dict(value or {})
    return value
