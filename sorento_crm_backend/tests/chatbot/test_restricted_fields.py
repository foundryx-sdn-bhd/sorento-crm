"""A2/A5/A6 general rule - `output_structurer` drops a restricted field/summary
item unless the contact's access grants it (AC-903, AC-904, AC-907).

`documentation/plans/chatbot/PLAN-chatbot-growth-r1.md` Slice A;
`documentation/plans/chatbot/chatbot-growth-r1-acceptance-criteria.md` section A/E.

A presenter marks a field restricted by putting its key in the envelope's
top-level `restricted_fields` (field key -> permission key). `ctx["access"]
["attributes"]` is a list of granted permission keys; today `head/access.py::
check_access` always returns it as None, which this module reads as the empty
grant set - so a contact with no grant reads a byte-identical answer to one
with no `restricted_fields` at all (AC-903). Slice C is what wires
`check_access` to a real per-contact grant.
"""
from __future__ import annotations

from app.services.chatbot.lanes.business import fetch


def _stock_envelope(*, with_sellable: bool) -> dict:
    fields = [
        {"key": "product_code", "label": "Product Code", "value": "SRTWC8517"},
        {"key": "quantity_on_hand", "label": "Quantity On Hand", "value": 20},
    ]
    envelope: dict = {
        "result_type": "stock",
        "intro": "Stock details found for the requested products.",
        "items": [{"title": "SRTWC8517", "fields": list(fields)}],
        "has_result": True,
    }
    if with_sellable:
        envelope["items"][0]["fields"] += [
            {"key": "open_so_qty", "label": "Open SO", "value": 7},
            {"key": "sellable", "label": "Sellable (on hand minus open SO)", "value": 13},
        ]
        envelope["restricted_fields"] = {
            "open_so_qty": "inventory.sellable",
            "sellable": "inventory.sellable",
        }
    return envelope


def test_no_restricted_fields_at_all_is_unaffected():
    """Envelopes that never carry `restricted_fields` (every tool before A2) are untouched."""
    envelope = _stock_envelope(with_sellable=False)
    out = fetch.output_structurer(envelope, {"semantic_input": {}})
    keys = {f["key"] for f in out["answers"][0]["fields"]}
    assert keys == {"product_code", "quantity_on_hand"}


def test_restricted_field_dropped_with_no_grant():
    """AC-903: no `access` in ctx at all (the pre-A2 shape) drops the restricted pair -
    byte-identical to the no-sellable envelope above."""
    envelope = _stock_envelope(with_sellable=True)
    out = fetch.output_structurer(envelope, {"semantic_input": {}})
    keys = {f["key"] for f in out["answers"][0]["fields"]}
    assert keys == {"product_code", "quantity_on_hand"}
    assert "Sellable" not in out["response"]


def test_restricted_field_dropped_when_access_attributes_is_none():
    """`ctx.access.attributes` is None (today's real shape) - read as empty grants."""
    envelope = _stock_envelope(with_sellable=True)
    ctx = {"semantic_input": {}, "access": {"allowed": True, "attributes": None}}
    out = fetch.output_structurer(envelope, ctx)
    keys = {f["key"] for f in out["answers"][0]["fields"]}
    assert keys == {"product_code", "quantity_on_hand"}


def test_restricted_field_kept_when_granted():
    """AC-904: a contact whose `access.attributes` contains the permission key keeps it."""
    envelope = _stock_envelope(with_sellable=True)
    ctx = {"semantic_input": {}, "access": {"allowed": True, "attributes": ["inventory.sellable"]}}
    out = fetch.output_structurer(envelope, ctx)
    keys = {f["key"] for f in out["answers"][0]["fields"]}
    assert keys == {"product_code", "quantity_on_hand", "open_so_qty", "sellable"}
    assert "Sellable (on hand minus open SO)" in out["response"]


def test_a_different_grant_does_not_unlock_an_unrelated_restricted_key():
    envelope = _stock_envelope(with_sellable=True)
    ctx = {"semantic_input": {}, "access": {"allowed": True, "attributes": ["purchase_orders.supplier"]}}
    out = fetch.output_structurer(envelope, ctx)
    keys = {f["key"] for f in out["answers"][0]["fields"]}
    assert keys == {"product_code", "quantity_on_hand"}


def test_restricted_summary_item_field_also_dropped():
    """The same rule reaches `summary_items`, not just `items[].fields[]`."""
    envelope = {
        "result_type": "orders",
        "intro": "Summary over 3 DOs.",
        "items": [{"title": "202606-1", "fields": [{"key": "order_number", "label": "Order Number", "value": "202606-1"}]}],
        "summary_items": [
            {
                "title": "ABC",
                "fields": [
                    {"key": "customer", "label": "Customer", "value": "ABC"},
                    {"key": "supplier", "label": "Supplier", "value": "Acme Supplies"},
                ],
            }
        ],
        "restricted_fields": {"supplier": "purchase_orders.supplier"},
        "has_result": True,
    }
    out = fetch.output_structurer(envelope, {"semantic_input": {}})
    keys = {f["key"] for f in out["summary_items"][0]["fields"]}
    assert keys == {"customer"}


def test_a_field_with_no_key_is_never_touched_by_the_restricted_rule():
    """Unkeyed fields (e.g. the compact stock location pairs) are never candidates - the
    restricted rule matches on key, and a field with none has nothing to match."""
    envelope = {
        "result_type": "stock",
        "intro": "Stock summary for the requested products.",
        "items": [{"title": "SRTWC8517", "fields": [{"label": "BRW-BB", "value": 12}]}],
        "restricted_fields": {"sellable": "inventory.sellable"},
        "has_result": True,
    }
    out = fetch.output_structurer(envelope, {"semantic_input": {}})
    assert out["answers"][0]["fields"] == [{"label": "BRW-BB", "value": 12}]


def _compact_envelope() -> dict:
    return {
        "result_type": "stock_compact",
        "intro": "Stock summary.",
        "items": [{"title": "SRTWT107", "fields": [
            {"key": "product_code", "label": "Product Code", "value": "SRTWT107"},
            {"key": "total_on_hand", "label": "Total", "value": 51, "granted_value": "51 (O/S: 36)"},
            {"key": "location_on_hand", "label": "BRW", "value": 0, "granted_value": "0 (O/S: 12)"},
        ], "flags": {}}],
        "restricted_fields": {"total_on_hand": "inventory.sellable", "location_on_hand": "inventory.sellable"},
        "has_result": True,
    }


def test_a_granted_value_is_swapped_in_under_the_grant():
    """D1 (owner console pass, 8 Sep 2026): a restricted field that carries `granted_value`
    is a plain field with a granted suffix - under the grant the suffix IS the value."""
    ctx = {"semantic_input": {}, "access": {"allowed": True, "attributes": ["inventory.sellable"]}}
    out = fetch.output_structurer(_compact_envelope(), ctx)
    values = {f["label"]: f["value"] for f in out["answers"][0]["fields"]}
    assert values == {"Product Code": "SRTWT107", "Total": "51 (O/S: 36)", "BRW": "0 (O/S: 12)"}
    assert "granted_value" not in str(out["answers"])
    assert "*BRW:* 0 (O/S: 12)" in out["response"]  # one location: the Total is dropped (card v4)
    assert "*Total:*" not in out["response"]


def test_a_granted_value_is_stripped_and_the_plain_value_kept_without_the_grant():
    out = fetch.output_structurer(_compact_envelope(), {"semantic_input": {}})
    values = {f["label"]: f["value"] for f in out["answers"][0]["fields"]}
    assert values == {"Product Code": "SRTWT107", "Total": 51, "BRW": 0}
    assert "O/S" not in out["response"]
    assert "granted_value" not in str(out["answers"])
