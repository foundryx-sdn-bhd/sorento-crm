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
