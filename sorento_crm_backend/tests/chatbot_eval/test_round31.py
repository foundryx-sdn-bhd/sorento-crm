"""EVAL-BENCH round 31a: a held order is the follow-up subject after.

The memory event can record the subject after the turn as an ORDER, not a product:
focus.after = {"products": [], "extra": {"order": [{"raw": ..., "hint": "customer_order"}]}}.
Synthetic data only.
"""
from __future__ import annotations

from scripts.chatbot_eval.targets import grade_turn
from tests.chatbot_eval.test_targets_v2 import UA, check, item, prod, row2

ORD = "PS209901-0001"
OTHER = "PS209901-0002"
HELD = {"raw": ORD, "hint": "customer_order", "uuid": None, "canonical_code": ORD}
GOLD = dict(variant="new_fact", fact="order", fact_label="order_status", subject_after=[ORD])


def _order_row(extra, **kw):
    return row2(message="and the status?", reply=f"{ORD} is confirmed.", domains=("order",),
                mem_before=[prod("SRTA100", UA)], mem_after=[], mem_extra=extra,
                tools=[{"name": "crm_orders_list", "args": {"status": "open"}}], **kw)


def test_red_a_held_order_in_memory_after_extra_is_the_subject_after():
    res = grade_turn(_order_row({"order": [HELD]}), item("followup", "and_x", **GOLD))
    check(res, True)


def test_guard_an_empty_after_focus_still_means_the_subject_was_dropped():
    check(grade_turn(_order_row({"order": []}), item("followup", "and_x", **GOLD)), False, "subject")


def test_guard_an_order_held_only_before_does_not_satisfy_the_gold_subject():
    r = _order_row({"order": []}, state_products=[])
    for ev in r["trace"]:
        if ev.get("kind") == "state":
            ev["focus"] = {"products": [], "extra": {"order": [HELD]}}
        if ev.get("kind") == "memory":
            ev["focus"]["before"] = {"products": [], "extra": {"order": [HELD]}}
    check(grade_turn(r, item("followup", "and_x", **GOLD)), False, "subject")


def test_guard_a_product_follow_up_with_an_unrelated_order_grades_as_before():
    unrelated = {"raw": OTHER, "hint": "customer_order", "uuid": None, "canonical_code": OTHER}
    r = row2(message="and srta100?", mem_before=[prod("SRTA200", UA)], mem_after=[prod("SRTA100", UA)],
             mem_extra={"order": [unrelated]}, tools=[{"name": "crm_inventory_list", "args": {"product_ids": [UA]}}])
    g = item("followup", "and_x", variant="new_fact", fact="inventory", subject_after=["SRTA100"])
    check(grade_turn(r, g), True)


# ---------------------------------------------------------------- precedence pins (kill-proof)
def _check_names(res):
    return {k for k, v in res["checks"].items() if v is False}


def test_pin_products_win_over_a_held_order_never_a_union():
    r = row2(message="and the status of the order?", reply=f"{ORD} is confirmed.", domains=("order",),
             mem_before=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], mem_extra={"order": [HELD]},
             tools=[{"name": "crm_orders_list", "args": {"status": "open"}}])
    res = grade_turn(r, item("followup", "switch", fact="order", subject_after=[ORD]))
    assert not res["passed"], res
    assert "subject" in _check_names(res) or "new_subject" in _check_names(res), res["checks"]


def test_pin_a_held_order_beats_the_lookups_own_strings():
    looked = [{"tool": "t", "entities": ["SRTB200"], "product_codes": ["SRTB200"]}]
    check(grade_turn(_order_row({"order": [HELD]}, looked=looked), item("followup", "and_x", **GOLD)), True)


def test_pin_a_malformed_order_entry_does_not_crash_and_is_no_subject():
    for bad in ({"raw": None}, {"raw": ""}, {"hint": "customer_order"}):
        res = grade_turn(_order_row({"order": [bad]}), item("followup", "and_x", **GOLD))
        assert res["checks"]["subject"] is False, res["checks"]


def test_pin_and_x_new_item_takes_a_held_order_as_the_new_subject():
    gold = dict(variant="new_item", fact="order", fact_label="order_status", subject_after=[ORD])
    res = grade_turn(_order_row({"order": [HELD]}), item("followup", "and_x", **gold))
    check(res, True)
    assert res["checks"]["new_subject"] is True, res["checks"]
