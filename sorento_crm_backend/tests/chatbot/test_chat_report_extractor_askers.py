"""CA-3 owner hand test round, RED: the extractor schema gains asker / category / brand
(UAC-27, UAC-30). No network: the provider is a fake, as in `test_chat_report_extractor.py`.

Pinned where the plan is silent (the coder follows, or asks the captain to change the test):
* the strict schema now requires NINE keys: the old six plus `askers`, `category_words`,
  `brand_words` (the old six-key assertions in `test_chat_report_extractor.py` change with it);
* `group_by` enum adds `asker`, `salesperson`, `category`, `brand` (ten old words kept);
* `askers`: array whose items are enum {dealer, salesperson, staff, unclassified}; `[]` means
  "no asker named" (the default applies). A value outside the four, or a non-list, is invalid;
* `category_words`, `brand_words`: arrays of strings (`[]` when none); anything else is invalid;
* `_checked(data, domains)` keeps its signature and returns the nine keys or None.
"""
from __future__ import annotations

import json

import pytest

from tests.chatbot.test_chat_report_extractor import (  # noqa: F401  (fixtures and helpers by name)
    GROUP_BY,
    _Provider,
    _run,
    configured,
    db,
)

NEW_GROUP_BY = {"asker", "salesperson", "category", "brand"}
ASKERS = {"dealer", "salesperson", "staff", "unclassified"}
NINE = {"group_by", "measure", "subject", "top_n", "date_from", "date_to", "askers", "category_words", "brand_words",
        "customer_words", "product_words"}  # lead sign-off 10 Oct (owner live round 3: customer_words / product_words): nine keys became eleven
GOOD9 = {
    "group_by": "category", "measure": "asks", "subject": None, "top_n": 5,
    "date_from": "2026-01-01", "date_to": "2026-12-31",
    "askers": ["salesperson"], "category_words": ["water closet"], "brand_words": ["sorento"],
    "customer_words": [], "product_words": [],
}


def test_a_valid_reply_returns_the_nine_keys(configured):
    out = _run(configured, _Provider(json.dumps(GOOD9)))
    assert out == GOOD9 and set(out) == NINE


def test_empty_lists_are_a_valid_reading(configured):
    plain = {**GOOD9, "group_by": "topic", "askers": [], "category_words": [], "brand_words": [],
             "customer_words": [], "product_words": []}
    assert _run(configured, _Provider(json.dumps(plain))) == plain


@pytest.mark.parametrize("group_by", sorted(NEW_GROUP_BY))
def test_each_new_group_by_word_is_accepted(configured, group_by):
    out = _run(configured, _Provider(json.dumps({**GOOD9, "group_by": group_by})))
    assert out is not None and out["group_by"] == group_by


def test_the_schema_sent_declares_the_new_fields(configured):
    provider = _Provider(json.dumps(GOOD9))
    _run(configured, provider)
    schema = provider.calls[0]["json_schema"]
    schema = schema.get("schema", schema)
    assert set(schema["required"]) == NINE and schema["additionalProperties"] is False, schema
    props = schema["properties"]
    assert {v for v in props["group_by"]["enum"] if v} == GROUP_BY | NEW_GROUP_BY
    assert props["askers"]["type"] == "array" and set(props["askers"]["items"]["enum"]) == ASKERS, props["askers"]
    for key in ("category_words", "brand_words"):
        assert props[key]["type"] == "array" and props[key]["items"]["type"] == "string", props[key]


@pytest.mark.parametrize(
    "bad",
    [
        {"askers": ["manager"]},
        {"askers": "dealer"},
        {"askers": [None]},
        {"category_words": "water closet"},
        {"category_words": [1]},
        {"brand_words": None},
        {"brand_words": [["sorento"]]},
    ],
    ids=["asker_out_of_enum", "asker_not_list", "asker_null_item", "category_not_list", "category_not_str",
         "brand_null", "brand_nested"],
)
def test_output_outside_the_new_contract_is_none(configured, bad):
    assert _run(configured, _Provider(json.dumps({**GOOD9, **bad}))) is None


@pytest.mark.parametrize("missing", ["askers", "category_words", "brand_words"])
def test_a_missing_new_key_is_none(configured, missing):
    reply = {k: v for k, v in GOOD9.items() if k != missing}
    assert _run(configured, _Provider(json.dumps(reply))) is None


def test_checked_validates_the_new_fields_directly():
    from app.services.chatbot.analytics import chat_report_extractor as mod

    assert mod._checked(dict(GOOD9), ["inventory"]) == GOOD9
    assert mod._checked({**GOOD9, "askers": ["nobody"]}, ["inventory"]) is None
    assert mod._checked({**GOOD9, "brand_words": [3]}, ["inventory"]) is None
