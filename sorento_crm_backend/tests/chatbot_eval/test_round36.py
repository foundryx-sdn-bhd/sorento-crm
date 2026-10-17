"""EVAL-BENCH round 36 G4: a qualified "no <qualifier> products matched" is a dead end in a spec reply.

`_spec` sets `shown` from a code in the reply and no COULD_NOT_FIND_RE hit; the regex needs "no" directly before
products/results/items, so "But no master products matched these." after a listed code still reads as shown.
Synthetic data only.
"""
from __future__ import annotations

import pytest

from scripts.chatbot_eval.targets import grade_turn
from tests.chatbot_eval.test_targets import item
from tests.chatbot_eval.test_targets_v2 import SPEC_ITEM, SPEC_V, row2


def _spec(reply):
    return grade_turn(row2(verdict=SPEC_V, reply=reply), item("spec", **SPEC_ITEM))


@pytest.mark.parametrize("tail", [
    "But no master products matched these.",          # the gate-off dead end (crew case)
    "No matching products found.",                    # qualifier before "products", "found" form
])
def test_red_a_qualified_no_products_matched_is_a_dead_end(tail):
    res = _spec(f"SRTA100\n\n{tail}")
    assert res["checks"]["shown"] is False, res["checks"]


def test_guard_codes_with_no_dead_end_phrase_keep_shown():
    assert _spec("Here's what you want: white basins (2)\n1. SRTA100\n2. SRTA200")["checks"]["shown"] is True


def test_guard_no_stock_and_no_incoming_is_not_a_could_not_find():
    assert _spec("SRTA100: no stock and no incoming.")["checks"]["shown"] is True


def test_guard_but_no_incoming_matched_after_a_listed_code_keeps_shown():
    # "no incoming matched" qualifies a fact, not the product list: the plain forms below must stay as today
    assert _spec("Here's what you want: white basins (2)\n1. SRTA100\n2. SRTA200\n\nBut no incoming matched these.")["checks"]["shown"] is True


@pytest.mark.parametrize("tail", ["Couldn't find SRTZ999.", "No products found."])
def test_guard_plain_dead_end_forms_still_fail(tail):
    assert _spec(f"SRTA100\n\n{tail}")["checks"]["shown"] is False


def test_red_b_underscore_qualifier_is_a_dead_end():
    # the bot writes the internal domain name: "But no master_products matched these."
    res = _spec("SRTA100\n\nBut no master_products matched these.")
    assert res["checks"]["shown"] is False, res["checks"]


def test_red_c_no_inside_a_word_is_not_a_dead_end():
    # "Milano" ends in "no": without a word boundary "no basin products matched" would read as a dead end
    res = _spec("Milano basin products matched: SRTA100")
    assert res["checks"]["shown"] is True, res["checks"]
