"""Round 2 (code review): the fetch guard knows each tool's product keys, and does not widen.

* top selling names a product as `rows[].code`; order analytics as `groups[].group_key` and
  `group_label` (group_by=product); complaint analytics lower-cases its `group_key`. Those rows
  must be dropped for an out-of-scope product.
* a generic `code` is NOT a product on a tool where it means a customer code.
* `ctx["brand_scope"]` can never widen the scope the session / contact carries.
"""
from __future__ import annotations

import json

from tests._brand_scope_seed import db, world  # noqa: F401  (fixtures by name)


def _guard(db, tool, result, world):
    from app.services.chatbot.lanes.business import brand_guard

    return brand_guard.guard_result(tool, result, [world.mocha.id], db)


def test_top_selling_rows_named_by_code_are_dropped(db, world) -> None:
    result = {"rows": [
        {"rank": 1, "code": world.codes["sorento"], "quantity": 3},
        {"rank": 2, "code": world.codes["mocha"], "quantity": 2},
        {"rank": 3, "code": world.codes["null"], "quantity": 4},
    ]}
    out = _guard(db, "crm_top_selling_report", result, world)
    assert [r["code"] for r in out["rows"]] == [world.codes["mocha"]], out


def test_order_analytics_groups_named_by_group_key_and_label_are_dropped(db, world) -> None:
    result = {"group_by": "product", "groups": [
        {"group_key": world.codes["sorento"], "group_label": f"{world.codes['sorento']} - x", "value": 1007.0},
        {"group_key": world.codes["mocha"], "group_label": f"{world.codes['mocha']} - x", "value": 100.0},
        {"group_key": world.codes["null"], "group_label": f"{world.codes['null']} - x", "value": 9.0},
    ]}
    out = _guard(db, "crm_order_analytics", result, world)
    assert [g["group_key"] for g in out["groups"]] == [world.codes["mocha"]], out


def test_complaint_analytics_lower_cased_group_key_is_dropped(db, world) -> None:
    result = {"group_by": "product", "groups": [
        {"group_key": world.codes["sorento"].lower(), "group_label": world.codes["sorento"], "value": 2},
        {"group_key": world.codes["mocha"].lower(), "group_label": world.codes["mocha"], "value": 1},
    ]}
    out = _guard(db, "crm_complaint_analytics", result, world)
    assert [g["group_label"] for g in out["groups"]] == [world.codes["mocha"]], out


def test_a_customer_code_under_the_generic_code_key_survives_on_a_customer_tool(db, world) -> None:
    """`code` means a customer on the debtors list; it must not be looked up as a product."""
    result = {"data": [{"code": "ZZT-CUST-1", "name": "ZZT Customer"}, {"code": world.codes["sorento"], "name": "x"}]}
    out = _guard(db, "crm_master_customers_list", result, world)
    assert out == result


def test_a_non_product_code_value_on_top_selling_is_not_dropped_as_unknown(db, world) -> None:
    """Unknown codes are left alone (the guard drops only codes it can place out of scope)."""
    result = {"rows": [{"code": "ZZT-NO-SUCH", "quantity": 1}, {"code": world.codes["mocha"], "quantity": 2}]}
    out = _guard(db, "crm_top_selling_report", result, world)
    assert len(out["rows"]) == 2


def _run(db, world, result, *, ctx_extra=None):
    from app.models.base import set_brand_scope
    from app.services.chatbot.lanes.business import run_fetch
    from app.services.chatbot.lanes.business.services import FetchServices

    set_brand_scope(db, frozenset({world.mocha.id}))
    ctx = {
        "contact": {"id": world.scoped.id},
        "access": {"attributes": []},
        "parse": {"output": {"domain_hint": "inventory"}},
        **(ctx_extra or {}),
    }
    payload = {
        "_exit_kind": "continue",
        "gate": {"compatible_entities": [
            {"uuid": str(world.p_mocha.id), "entity_type": "product", "code": world.codes["mocha"]}
        ]},
        "ctx": ctx,
    }
    fragment = run_fetch(payload, services=FetchServices(mcp_call=lambda n, a: json.dumps(result)), db=db)
    return json.dumps(fragment, default=str)


def test_ctx_brand_scope_cannot_widen_the_stamped_scope(db, world) -> None:
    """Session stamped MOCHA, ctx claims MOCHA + SORENTO: SORENTO rows are still dropped."""
    result = {"items": [
        {"product_code": world.codes["mocha"]}, {"product_code": world.codes["sorento"]},
    ], "has_result": True}
    blob = _run(db, world, result,
                ctx_extra={"brand_scope": {"ids": [str(world.mocha.id), str(world.sorento.id)]}})
    assert world.codes["mocha"] in blob
    assert world.codes["sorento"] not in blob
