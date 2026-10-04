"""Phase 2 RED tests - AC-17: the fetch output guard (last layer, beside the customer guard).

`run_fetch` given a brand scope drops every list item (any depth) that names an out-of-scope
product by `product_code` / `item_code` / `product_id` before the answer is built; an
unscoped contact's result is untouched. The scope reaches fetch as
`semantic_input["scope_brand_ids"]`; at `run_fetch` it is derived from the turn ctx. Tester's
choice (the plan names the carrier, not the ctx key): `ctx["brand_scope"] = {"ids": [...]}`,
mirroring `ctx["customer_scope"]`, AND `ctx["contact"]["id"]` is the real scoped contact, so
an implementation that reads the one reader (`contact_brand_scope`) from the db passes too.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from tests._brand_scope_seed import db, world  # noqa: F401  (fixtures by name)


def _run(db, world, result: dict, *, scoped: bool, domain: str = "inventory", db_arg=True):
    from app.services.chatbot.lanes.business import run_fetch
    from app.services.chatbot.lanes.business.services import FetchServices

    ctx: dict[str, Any] = {
        "contact": {"id": (world.scoped if scoped else world.unscoped).id},
        "access": {"attributes": []},
        "parse": {"output": {"domain_hint": domain}},
    }
    if scoped and db_arg:
        # the scope is the one the engine stamped on the turn session, not a ctx claim
        from app.models.base import set_brand_scope

        set_brand_scope(db, frozenset({world.mocha.id}))
    elif scoped:
        ctx["brand_scope"] = {"ids": [str(world.mocha.id)]}  # no session to stamp: the ctx narrows
    payload = {
        "_exit_kind": "continue",
        "gate": {"compatible_entities": [
            {"uuid": str(world.p_mocha.id), "entity_type": "product", "code": world.codes["mocha"]}
        ]},
        "ctx": ctx,
    }
    mcp = FetchServices(mcp_call=lambda name, args: json.dumps(result))
    return run_fetch(payload, services=mcp, db=db if db_arg else None)


def _blob(fragment) -> str:
    return json.dumps(fragment, default=str)


def _rows(world) -> dict:
    return {"items": [
        {"product_code": world.codes["mocha"], "qty": 5},
        {"product_code": world.codes["sorento"], "qty": 6},
        {"product_code": world.codes["null"], "qty": 7},
    ], "has_result": True}


def test_scoped_result_keeps_only_in_scope_rows(db, world) -> None:
    blob = _blob(_run(db, world, _rows(world), scoped=True))
    assert world.codes["mocha"] in blob
    assert world.codes["sorento"] not in blob
    assert world.codes["null"] not in blob


def test_unscoped_result_is_untouched(db, world) -> None:
    """AC-6."""
    blob = _blob(_run(db, world, _rows(world), scoped=False))
    for code in world.codes.values():
        assert code in blob


def test_rows_named_by_item_code_are_dropped(db, world) -> None:
    result = {"items": [{"item_code": world.codes["mocha"]}, {"item_code": world.codes["sorento"]}]}
    blob = _blob(_run(db, world, result, scoped=True))
    assert world.codes["mocha"] in blob and world.codes["sorento"] not in blob


def test_rows_named_by_product_id_are_dropped(db, world) -> None:
    result = {"items": [{"product_id": str(world.p_mocha.id)}, {"product_id": str(world.p_sorento.id)},
                       {"product_id": str(world.p_null.id)}]}
    blob = _blob(_run(db, world, result, scoped=True))
    assert str(world.p_mocha.id) in blob
    assert str(world.p_sorento.id) not in blob and str(world.p_null.id) not in blob


def test_nested_lists_are_filtered_and_the_parent_row_stays(db, world) -> None:
    """A shipment row with `lines`: the out-of-scope lines go, the shipment stays."""
    result = {"items": [{
        "shipment_number": "ZZT-SHP-NEST",
        "lines": [
            {"product_code": world.codes["mocha"], "remaining_incoming_quantity": 11},
            {"product_code": world.codes["sorento"], "remaining_incoming_quantity": 11},
            {"product": {"product_code": world.codes["null"]}},
        ],
    }]}
    blob = _blob(_run(db, world, result, scoped=True, domain="incoming"))
    assert "ZZT-SHP-NEST" in blob
    assert world.codes["mocha"] in blob
    assert world.codes["sorento"] not in blob and world.codes["null"] not in blob


def test_a_tool_marked_no_products_is_not_stripped(db, world) -> None:
    """Only `filtered` tools are guarded: the portal link has no product rows to drop."""
    result = {"portal_url": "https://example.test/portal/c/zzt"}
    blob = _blob(_run(db, world, result, scoped=True, domain="portal_link"))
    assert "https://example.test/portal/c/zzt" in blob


def test_guard_fails_closed_when_it_cannot_look_the_codes_up(world) -> None:
    """No session to resolve brands with -> the product rows are dropped, never passed on."""
    blob = _blob(_run(None, world, _rows(world), scoped=True, db_arg=False))
    assert world.codes["sorento"] not in blob and world.codes["null"] not in blob
