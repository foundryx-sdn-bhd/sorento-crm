"""EVAL-BENCH round 32a: an attribute line in WhatsApp bold or italics still answers.

The engine writes `*weight:* not recorded for SRTA100`; the `*` after the attribute word broke the
`\\s*:?\\s*` of the attribute check. Synthetic data only.
"""
from __future__ import annotations

import pytest

from scripts.chatbot_eval.targets import grade_turn
from tests.chatbot_eval.test_targets_v2 import UA, _r28_result, item, prod, row2

CODE = "SRTA100"
GOLD = dict(code=CODE, fact="master_products", fact_label="weight")
WEIGHT = [{"key": "spec:weight", "label": "Weight", "value": "12 kg"}]


def _row(reply, extra_fields=()):
    tool = {"name": "crm_master_products_list", "args": {"product_ids": [UA]}}
    r = row2(message=f"{CODE} check weight ?", reply=reply, mem_after=[prod(CODE, UA)], tools=[tool])
    r["tool_results"] = [{"name": "crm_master_products_list", "args": {"product_ids": [UA]},
                          "result": _r28_result(extra_fields, code=CODE)}]
    return r


def _grade(reply, extra_fields=()):
    return grade_turn(_row(reply, extra_fields), item("code_lookup", **GOLD))


def test_red_a_bold_honest_none_answers_the_attribute():
    res = _grade(f"*weight:* not recorded for {CODE}")
    assert res["checks"]["attribute_value"] is True, res["checks"]
    assert res["passed"] is True, (res["reason"], res["checks"])


def test_guard_a_bold_value_answers_the_attribute():
    res = _grade(f"{CODE}\n*weight:* 12 kg", WEIGHT)
    assert res["checks"]["attribute_value"] is True, res["checks"]


def test_red_underscore_italics_none_answers_the_attribute():
    res = _grade(f"_weight:_ not recorded for {CODE}")
    assert res["checks"]["attribute_value"] is True, res["checks"]


@pytest.mark.parametrize("reply,fields", [
    (f"weight: not recorded for {CODE}", ()),
    (f"{CODE}\nweight: 12 kg", WEIGHT),
])
def test_guard_the_plain_forms_still_pass(reply, fields):
    res = _grade(reply, fields)
    assert res["checks"]["attribute_value"] is True, res["checks"]


def test_guard_a_bold_none_while_the_record_lists_a_weight_still_fails():
    res = _grade(f"*weight:* not recorded for {CODE}", WEIGHT)
    assert res["checks"]["attribute_value"] is False, res["checks"]
    assert res["passed"] is False


@pytest.mark.parametrize("reply", [
    f"{CODE}\n*weight:*",
    f"{CODE}\n*weight:* see the brochure for details",
])
def test_guard_a_bold_attribute_word_with_no_value_and_no_none_still_fails(reply):
    res = _grade(reply)
    assert res["checks"]["attribute_value"] is False, res["checks"]
    assert res["passed"] is False


def test_guard_a_value_and_a_none_statement_in_one_bold_reply_counts_as_the_value():
    # the value branch (`between`) and the none statement both read the stripped reply; the record lists a weight
    res = _grade(f"*weight* 12 kg colour not recorded for {CODE}", WEIGHT)
    assert res["checks"]["attribute_value"] is True, res["checks"]


def test_guard_the_digits_of_an_underscored_code_are_not_a_value():
    res = _grade("weight: see SRTA_100 sheet")
    assert res["checks"]["attribute_value"] is False, res["checks"]


def test_guard_a_struck_through_value_after_the_word_counts_as_a_value_today():
    res = _grade(f"{CODE}\nweight: ~12 kg~", WEIGHT)
    assert res["checks"]["attribute_value"] is True, res["checks"]


def test_guard_a_struck_through_attribute_word_with_an_honest_none_counts_today():
    res = _grade(f"~weight:~ not recorded for {CODE}")
    assert res["checks"]["attribute_value"] is True, res["checks"]
