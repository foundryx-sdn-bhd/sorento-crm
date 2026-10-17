"""QTY-CARRIED (owner, 13 Oct 2026): a quantity the parser put on a CARRIED product.

After the parser prompt fix ("A QUANTITY IS A STOCK QUESTION", crew test version v99),
a bare count after a product turn ("HAVE 10??", "ada 10?", "有10个吗") parses as
domain_hint inventory, intent check_stock, with the quantity on the carried product
entity (`current_message: false`, quantity 10, `demand_qty` null). The engine still
replied "How many units of SRTWT5880-CR?": `turn_runtime.without_restated_rows` read the
carried entity as a bare echo of the settled focus row and dropped it, quantity and all,
before `apply` ran. "SRTWT5880-CR X10" (typed now) was never an echo and worked.

Only the parser and the stock tool are stubbed. The chain after the parser is the real
code in the order `engine.run_turn` calls it: `without_restated_rows`, `turn/apply.apply`,
`turn_runtime.lane_parse_output` + `_spec_quantities` (the `requested_quantities` the
stock tool is sent) and `engine._stock_ask_reply`.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.services.chatbot import turn_runtime
from app.services.chatbot.turn import compose as turn_compose
from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.state import Focus

from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import build_policy, verdict

CODE = "SRTWT5880-CR"


def _carried(quantity: Any = None) -> dict[str, Any]:
    """The parser's own carried product entity, the shape v99 emits for "HAVE 10??"."""
    return {
        "raw": CODE,
        "hint": "product",
        "canonical_code": CODE,
        "current_message": False,
        "confident": True,
        "quantity": quantity,
    }


def _bare_count(quantity: Any = None) -> dict[str, Any]:
    return verdict(
        domain_hint="inventory",
        intent="check_stock",
        demand_qty=None,
        entities=[_carried(quantity)],
    )


def _after_picture(*, quantity: Any = None):
    """The focus after "SRTWT5880-CR picture": the product settled (uuid placed)."""
    rows = ht.product_rows(CODE)
    if quantity is not None:
        rows[0]["quantity"] = quantity
    return ht.state(Focus(products=rows, domains=["product_attachment"]), availability_only=True, turn_no=1)


def _turn(state, v):
    """One turn through the real chain; returns (reply text, requested_quantities)."""
    from app.services.chatbot.engine import _stock_ask_reply

    v = turn_runtime.without_restated_rows(v, state.focus)
    applied, plan = apply(replace(state, turn_no=state.turn_no + 1), v, build_policy())
    if plan.trace.task_question:
        return plan.trace.task_question, None
    (spec,) = ht.inventory_specs(plan)
    lane_out = turn_runtime.lane_parse_output(v, focus=applied.focus, domain="inventory", policy=build_policy())
    entities = [turn_runtime._spec_row(e) for e in spec.entities]
    quantities = turn_runtime._spec_quantities(lane_out, spec, entities).get("requested_quantities") or {}
    rows = []
    for entity in entities:
        qty = quantities.get(entity.get("uuid"))
        rows.append(ht.row(entity["canonical_code"], needs_quantity=qty is None, requested_qty=qty))
    answered = [r for r in rows if not r["needs_quantity"]]
    text = (
        "\n".join(f"{r['product_code']} x {r['requested_qty']}: in stock" for r in answered)
        if answered
        else "How many units do you need?"
    )
    answer = _stock_ask_reply(
        turn_compose.Answer(text=text), applied, ht.envelopes(*rows), plan, v, turn_no=applied.turn_no
    )
    return answer.text, quantities


def test_a_quantity_on_the_carried_product_is_answered_not_asked():
    text, quantities = _turn(_after_picture(), _bare_count(10))
    assert quantities == {ht.uuid_of(CODE): 10}
    assert text == f"{CODE} x 10: in stock"
    assert "How many units" not in text


def test_the_carried_echo_keeps_the_settled_uuid_and_the_quantity():
    state = _after_picture()
    out = turn_runtime.without_restated_rows(_bare_count(10), state.focus)
    (entity,) = out["entities"]
    assert entity["quantity"] == 10
    assert entity["uuid"] == ht.uuid_of(CODE)
    assert entity["current_message"] is False


def test_no_quantity_anywhere_still_asks_how_many_units():
    text, quantities = _turn(_after_picture(), _bare_count(None))
    assert quantities == {}
    assert text == f"How many units of {CODE}?"


def test_a_bare_echo_with_no_quantity_is_still_dropped():
    state = _after_picture()
    out = turn_runtime.without_restated_rows(_bare_count(None), state.focus)
    assert out["entities"] == []


def test_the_number_in_this_message_replaces_an_earlier_quantity():
    text, quantities = _turn(_after_picture(quantity=5), _bare_count(10))
    assert quantities == {ht.uuid_of(CODE): 10}
    assert text == f"{CODE} x 10: in stock"


def test_a_typed_code_with_its_quantity_still_works():
    typed = verdict(
        domain_hint="inventory", intent="check_stock", entities=[ht.asked(CODE, quantity=10)]
    )
    state = _after_picture()
    v = turn_runtime.without_restated_rows(typed, state.focus)
    assert v["entities"][0]["quantity"] == 10
    applied, plan = apply(replace(state, turn_no=2), v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    lane_out = turn_runtime.lane_parse_output(v, focus=applied.focus, domain="inventory", policy=build_policy())
    resolved = [turn_runtime._spec_row(e) for e in ht.product_rows(CODE)]
    assert turn_runtime._spec_quantities(lane_out, spec, resolved)["requested_quantities"] == {
        ht.uuid_of(CODE): 10
    }


def test_a_code_settled_on_two_rows_keeps_both_with_the_quantity():
    """Reviewer should-fix: a code two companies' products share is two settled rows;
    the echo with a quantity keeps both, never only the first."""
    rows = ht.product_rows(CODE)
    second = {**rows[0], "uuid": ht.uuid_of(CODE + "-other-company")}
    state = ht.state(Focus(products=[rows[0], second]), availability_only=True, turn_no=1)
    out = turn_runtime.without_restated_rows(_bare_count(10), state.focus)
    assert [e["uuid"] for e in out["entities"]] == [rows[0]["uuid"], second["uuid"]]
    assert all(e["quantity"] == 10 for e in out["entities"])
