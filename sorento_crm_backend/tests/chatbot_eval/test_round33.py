"""EVAL-BENCH round 33 (owner ruling 17 Oct): a sibling picker after a full answer is not needless.

After a full answer to an exact code the bot lists the code's sibling variants; the grader must not fail it.
A did-you-mean INSTEAD of the answer still fails. Synthetic data only.
"""
from __future__ import annotations

import pytest

from scripts.chatbot_eval.targets import grade_turn
from tests.chatbot_eval.test_targets import row
from tests.chatbot_eval.test_targets_v2 import GOLD_CL, TOOL_A, UA, _with_oq, item, prod, row2


def _pick(*codes):
    return {"kind": "product_pick", "expects": "choice", "payload": {}, "asked_at_turn": 2,
            "options": [{"label": c, "code": c} for c in codes]}


SIBLINGS = _pick("SRTA100-BL", "SRTA100-GM")
SIBLINGS_WITH_SELF = _pick("SRTA100", "SRTA100-GM")
FOREIGN = _pick("SRTB200", "SRTC300")
DID_YOU_MEAN = _pick("SRTA100")


def _lookup(q, reply="SRTA100 has 40 pcs.", tools=(TOOL_A,)):
    r = row2(state_products=[prod("SRTA100", UA)], mem_after=[prod("SRTA100", UA)], tools=list(tools), reply=reply)
    return _with_oq(r, q)


def _and_x(q):
    r = row(decision="new_ask", domains=("incoming",), focus_codes=["SRTA100"])
    r["response"] = {"reply": {"text": "SRTA100 arrives on 3 Nov, 200 pcs."}}
    return _with_oq(r, q)


AND_X = dict(variant="new_fact", fact="incoming", subject_after=["SRTA100"])


@pytest.mark.parametrize("q", [SIBLINGS, SIBLINGS_WITH_SELF], ids=["siblings", "self_and_sibling"])
def test_red_a_sibling_picker_after_a_full_code_lookup_answer_passes(q):
    res = grade_turn(_lookup(q), item("code_lookup", **GOLD_CL))
    assert res["checks"]["no_picker"] is not False, res["checks"]
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_red_a_sibling_picker_after_a_full_and_x_answer_passes():
    res = grade_turn(_and_x(SIBLINGS), item("followup", "and_x", **AND_X))
    assert res["checks"]["no_picker"] is not False, res["checks"]
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_guard_a_did_you_mean_instead_of_the_answer_fails():
    r = _lookup(DID_YOU_MEAN, reply="Did you mean SRTA100?", tools=())
    res = grade_turn(r, item("code_lookup", **GOLD_CL))
    assert res["passed"] is False, res["checks"]


def test_guard_a_non_sibling_picker_after_a_full_answer_still_fails():
    res = grade_turn(_lookup(FOREIGN), item("code_lookup", **GOLD_CL))
    assert res["checks"]["no_picker"] is False, res["checks"]
    assert res["passed"] is False


def test_guard_a_sibling_picker_with_a_failing_fact_check_still_fails():
    res = grade_turn(_lookup(SIBLINGS), item("code_lookup", code="srta100", fact="promotion"))
    assert res["checks"]["fact"] is False, res["checks"]
    assert res["checks"]["no_picker"] is False, res["checks"]
    assert res["passed"] is False


def test_guard_an_ambiguous_gold_with_a_picker_passes():
    res = grade_turn(_lookup(SIBLINGS), item("code_lookup", ambiguous=True, **GOLD_CL))
    assert res["checks"].get("no_picker", True) is not False, res["checks"]


def test_guard_pin_an_unresolved_gold_without_a_code_and_a_picker():
    r = _lookup(SIBLINGS)
    res = grade_turn(r, item("code_lookup", code_unresolved=True, fact="inventory"))
    # pinned today: with no gold code the picker still counts as needless
    assert res["checks"]["no_picker"] is False, res["checks"]


# 33b: on real rows the engine records replied.facts asking=product_pick and sections=0 for BOTH pickers
def _real_shape(r):
    r = _with_oq(r, r["trace"][-1].get("open_question", {}).get("after") or SIBLINGS_WITH_SELF, asking="product_pick")
    for rec in r["trace"]:
        if rec.get("stage") == "replied":
            rec["facts"]["sections"] = 0
    return r


def test_red_real_shape_sibling_picker_after_a_full_code_lookup_answer_passes():
    reply = "SRTA100: 40 pcs in stock.\nRelated products:\n1. SRTA100\n2. SRTA100-GM"
    r = _real_shape(_lookup(SIBLINGS_WITH_SELF, reply=reply))
    assert any(x.get("stage") == "replied" and x["facts"]["asking"] == "product_pick" for x in r["trace"])
    res = grade_turn(r, item("code_lookup", **GOLD_CL))
    assert res["checks"]["no_picker"] is not False, res["checks"]
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_red_real_shape_sibling_picker_after_a_full_and_x_answer_passes():
    r = _and_x(SIBLINGS_WITH_SELF)
    r["response"] = {"reply": {"text": "SRTA100: no incoming.\nStock: 12 pcs.\nRelated products:\n1. SRTA100\n2. SRTA100-GM"}}
    r = _real_shape(r)
    assert any(x.get("stage") == "replied" and x["facts"]["asking"] == "product_pick" for x in r["trace"])
    res = grade_turn(r, item("followup", "and_x", **AND_X))
    assert res["checks"]["no_picker"] is not False, res["checks"]
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_guard_real_shape_did_you_mean_with_sibling_options_and_no_answer_still_fails():
    r = _real_shape(_lookup(SIBLINGS, reply="Did you mean:\n1. SRTA100-BL\n2. SRTA100-GM"))
    res = grade_turn(r, item("code_lookup", **GOLD_CL))
    assert res["checks"]["no_picker"] is False and res["passed"] is False, res["checks"]


# 33c: siblings share the EXACT family head (code before the first "-", which must hold a digit)
def _full(gold_code, q):
    r = _with_oq(row2(state_products=[prod(gold_code, UA)], mem_after=[prod(gold_code, UA)], tools=[TOOL_A],
                      reply=f"{gold_code} has 40 pcs."), q)
    return grade_turn(r, item("code_lookup", code=gold_code, fact="inventory"))


def test_red_options_that_only_extend_the_gold_head_are_another_family():
    res = _full("SRTA100", _pick("SRTA1000", "SRTA1001"))
    assert res["checks"]["no_picker"] is False, res["checks"]
    assert res["passed"] is False


def test_red_a_gold_head_without_a_digit_has_no_family():
    res = _full("SRT-A100", _pick("SRTB200", "SRTC300"))
    assert res["checks"]["no_picker"] is False, res["checks"]
    assert res["passed"] is False


@pytest.mark.parametrize("gold,opts", [
    ("SRTA100", ("SRTA100", "SRTA100-GM", "SRTA100-BL")),
    ("SRTWC286-SH", ("SRTWC286-SH-P", "SRTWC286-NEW")),
])
def test_guard_options_sharing_the_exact_head_are_siblings(gold, opts):
    res = _full(gold, _pick(*opts))
    assert res["checks"]["no_picker"] is not False, res["checks"]


def test_red_a_did_you_mean_on_a_typo_is_no_answer_even_when_the_lookup_ran():
    reply = 'Couldn\'t find "srta10o" (product). Did you mean:\n1. SRTA100\n2. SRTA100-GM'
    r = _real_shape(_lookup(SIBLINGS_WITH_SELF, reply=reply))
    res = grade_turn(r, item("code_lookup", **GOLD_CL))
    assert res["checks"]["no_picker"] is False, res["checks"]
    assert res["passed"] is False


def test_guard_a_gold_head_without_a_digit_has_no_family_even_when_the_option_heads_match():
    res = _full("SRT-A100", _pick("SRT-B200", "SRT-C300"))
    assert res["checks"]["no_picker"] is False, res["checks"]
    assert res["passed"] is False
