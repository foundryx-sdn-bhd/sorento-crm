"""DO-ASK-SIMPLIFY rule 2: five DO fields are per-contact field reveals, hidden by default.

`documentation/plans/chatbot/PLAN-do-ask-simplify-2oct.md`, rule 2. Status, Pickup Time,
Transporter, Driver and Lorry Plate on a delivery order list carry a `contact_field_reveals`
key under `delivery_orders.*`; a contact without the grant never reads them. Every other DO
field (Order Number, Customer, dates, Products, Company, Warehouse) is always shown.

The real MCP presenter renders the row, then the chatbot's `output_structurer` gates it, so
this pins the whole chain the WhatsApp reply goes through.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.services.chatbot.lanes.business import fetch
from app.services.contact_field_reveal_service import FIELD_REVEAL_KEYS



def _mcp():
    """The MCP catalogue and presenter, imported when a test runs, never at collection: the
    backend image CI validates imports in holds no `sorento_crm_mcp`, so a module-level
    import fails collection there (the pattern `test_field_reveal_keys_pinned_to_catalog.py`
    and `test_dealer_eta_stock_routing.py` use)."""
    root = Path(__file__).resolve().parents[3] / "sorento_crm_mcp"
    if str(root) not in sys.path:
        sys.path.append(str(root))
    from sorento_crm_mcp.catalog import CATALOG
    from sorento_crm_mcp.presenters import present_response

    return CATALOG, present_response

DO_KEYS = {
    "delivery_orders.status": "Status",
    "delivery_orders.pickup_time": "Pickup Time",
    "delivery_orders.transporter": "Transporter",
    "delivery_orders.driver": "Driver",
    "delivery_orders.lorry_plate": "Lorry Plate",
}

_ROW = {
    "order_number": "202609-0916",
    "debtor_name": "HANLIM TRADING SDN BHD [A/C I]",
    "order_date": "2026-09-07",
    "actual_delivery_date": "2026-09-07",
    "order_status": "Picked Up / In Transit",
    "pickup_time": "09:18:00",
    "transporter": "SORENTO",
    "driver_name": "DRIVER B",
    "lorry_plate": "PLATE-1",
    "warehouse": "BRW",
    "lines": [{"product_code": "SRT320-CR", "quantity": 200}],
}


def _reply(granted: list[str] | None) -> str:
    envelope = json.loads(_mcp()[1]("crm_order_management_orders_list", json.dumps({"data": [_ROW]})))
    ctx = {"semantic_input": {}, "access": {"allowed": True, "attributes": granted}}
    return fetch.output_structurer(envelope, ctx)["response"]


def test_a_contact_with_no_grant_reads_no_logistics_field() -> None:
    said = _reply(None)
    for label in DO_KEYS.values():
        assert f"*{label}:*" not in said, f"{label} must be hidden without its grant: {said!r}"
    for value in ("DRIVER B", "PLATE-1", "Picked Up / In Transit", "09:18:00"):
        assert value not in said, said


# --- owner hand test 3 Oct 2026: EVERY printed DO field is a toggle, Warehouse included ----- #

#: Every field the DO list prints, by reveal key, with the value `_ROW` prints for it.
ALL_DO_FIELDS = {
    "delivery_orders.order_number": ("Order Number", "202609-0916"),
    "delivery_orders.customer": ("Customer", "HANLIM TRADING SDN BHD [A/C I]"),
    "delivery_orders.order_date": ("Order Date", "2026-09-07"),
    "delivery_orders.delivery_date": ("Actual Delivery Date", "2026-09-07"),
    "delivery_orders.status": ("Status", "Picked Up / In Transit"),
    "delivery_orders.pickup_time": ("Pickup Time", "09:18:00"),
    "delivery_orders.transporter": ("Transporter", "SORENTO"),
    "delivery_orders.driver": ("Driver", "DRIVER B"),
    "delivery_orders.lorry_plate": ("Lorry Plate", "PLATE-1"),
    "delivery_orders.warehouse": ("Warehouse", "BRW"),
    "delivery_orders.products": ("Products", "SRT320-CR (200)"),
}


def test_a_contact_with_no_grant_reads_no_do_field_at_all() -> None:
    said = _reply(None)
    for label, _value in ALL_DO_FIELDS.values():
        assert f"*{label}:*" not in said, f"{label} must be hidden without its grant: {said!r}"
    assert "BRW" not in said and "SRT320-CR" not in said, said


def test_each_do_field_has_its_own_switch() -> None:
    for key, (label, value) in ALL_DO_FIELDS.items():
        said = _reply([key])
        assert f"*{label}:* {value}" in said, (key, said)
        others = [lbl for k, (lbl, _v) in ALL_DO_FIELDS.items() if k != key]
        assert not [lbl for lbl in others if f"*{lbl}:*" in said], (key, said)


def test_with_every_switch_on_the_reply_is_as_before() -> None:
    said = _reply(list(ALL_DO_FIELDS))
    for label, value in ALL_DO_FIELDS.values():
        assert f"*{label}:* {value}" in said, (label, said)


def test_every_do_switch_is_on_the_field_reveals_checklist() -> None:
    listed = dict(FIELD_REVEAL_KEYS)
    assert set(ALL_DO_FIELDS) <= set(listed), sorted(set(ALL_DO_FIELDS) - set(listed))


def test_the_by_product_do_list_hides_warehouse_too() -> None:
    """The by-product DO list used to print the warehouse inside Products ("X (1) @ BRW"),
    which would get round the Warehouse switch; it is its own switched field now."""
    body = {"data": [{
        "order_number": "202609-0916", "debtor_name": "HANLIM TRADING SDN BHD [A/C I]",
        "order_date": "2026-09-07", "actual_delivery_date": "2026-09-07",
        "matched_products": [{"product_code": "SRT320-CR", "quantity": 200, "warehouse_code": "BRW"}],
    }]}
    def said_with(keys: list[str]) -> str:
        # A fresh envelope per call: `output_structurer` drops withheld fields in place.
        envelope = json.loads(_mcp()[1]("crm_order_management_orders_by_product_list", json.dumps(body)))
        return fetch.output_structurer(envelope, {"semantic_input": {}, "access": {"attributes": keys}})["response"]

    said = said_with([k for k in ALL_DO_FIELDS if k != "delivery_orders.warehouse"])
    assert "SRT320-CR (200)" in said and "BRW" not in said, said
    said = said_with(list(ALL_DO_FIELDS))
    assert "*Warehouse:* BRW" in said, said


def test_a_contact_with_every_grant_reads_today_s_reply() -> None:
    said = _reply(list(DO_KEYS))
    for value in ("Picked Up / In Transit", "09:18:00", "SORENTO", "DRIVER B", "PLATE-1"):
        assert value in said, said


def test_each_key_unlocks_only_its_own_field() -> None:
    said = _reply(["delivery_orders.status"])
    assert "Picked Up / In Transit" in said, said
    for value in ("DRIVER B", "PLATE-1", "09:18:00"):
        assert value not in said, said


def test_the_five_keys_are_on_the_field_reveals_checklist() -> None:
    listed = dict(FIELD_REVEAL_KEYS)
    for key in DO_KEYS:
        assert key in listed, f"{key} missing from FIELD_REVEAL_KEYS"


def test_the_orders_list_tool_declares_the_five_keys() -> None:
    """Only the orders list shows the five fields; the by-product list shows none of them."""
    specs = {spec.name: spec for spec in _mcp()[0]}
    declared = {key for key, _label in specs["crm_order_management_orders_list"].restricted_fields}
    assert set(DO_KEYS) <= declared, declared


# --- security B1: the "not delivered yet" miss line names the status only with the grant --- #


def _delivered_miss(**kwargs) -> str:
    from app.services.chatbot.lanes.business.answer import not_found_error_message

    order_uuid = "33333333-3333-4333-9333-333333333333"
    match = {
        "entity_type": "order",
        "uuid": order_uuid,
        "canonical_code": "202609-0916",
        "display": {"customer_name": "HANLIM TRADING SDN BHD [A/C I]", "status": "Picked Up / In Transit"},
    }
    resolved = {
        "tokens": ["202609-0916"],
        "unresolved_tokens": [],
        "resolutions": [{"token": "202609-0916", "matches": [match]}],
        "intersection": [match],
        "by_entity_type": {"order": [match]},
    }
    parser = {
        "domain_hint": "order",
        "order_status": "delivered",
        "entities": [{"hint": "order", "raw": "202609-0916"}],
        "routing": {"suggested_team": "customer_service"},
        "access_levels": [],
    }
    gate = {
        "gate_passed": True,
        "compatible_entities": [{"uuid": order_uuid, "entity_type": "order", "code": "202609-0916"}],
    }
    out = not_found_error_message({}, parser=parser, resolved=resolved, gate=gate, **kwargs)
    return out.get("escalate_message") or ""


def test_the_not_delivered_line_hides_the_status_without_the_grant() -> None:
    message = _delivered_miss(granted_keys=[])
    assert "hasn't been delivered yet." in message, message
    assert "Picked Up" not in message and "current status" not in message, message


def test_the_not_delivered_line_hides_the_status_when_no_grants_are_passed() -> None:
    message = _delivered_miss()
    assert "Picked Up" not in message, message


def test_the_not_delivered_line_names_the_status_with_the_grant() -> None:
    message = _delivered_miss(granted_keys=["delivery_orders.status"])
    assert "current status: Picked Up / In Transit" in message, message


# --- tester pass 2: a value's own '*' never opens WhatsApp bold ---------------------------- #


def test_an_asterisk_in_a_value_does_not_leave_a_stray_bold_marker() -> None:
    """A product code like '*REPLACE' printed raw pairs its '*' with the next label's bold
    marker, and the reply shows a stray '*'. A value's own '*' prints as the look-alike
    U+2217, which WhatsApp does not read as formatting."""
    row = {**_ROW, "lines": [{"product_code": "*REPLACE", "quantity": 1}]}
    envelope = json.loads(_mcp()[1]("crm_order_management_orders_list", json.dumps({"data": [row]})))
    said = fetch.output_structurer(envelope, {"semantic_input": {}, "access": {"attributes": list(ALL_DO_FIELDS)}})["response"]
    assert "∗REPLACE (1)" in said, said
    stars = [line for line in said.split("\n") if line.count("*") % 2]
    assert not stars, f"a line with an unpaired '*': {stars!r}"
