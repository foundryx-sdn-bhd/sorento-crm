"""PV2 redesign R1-7b, v1 side: `fetch.output_structurer` emits an additive envelope key `printed_rows`, the indices
into `answers` of the items the reply text PRINTED, in printed order. Summary mode and needs-quantity stock mode keep
their rows in `answers` (a positional pick resolves against them) but hide them from the text; a status-only product
list prints a duplicate once. The reply text is unchanged by the key."""
from __future__ import annotations

import copy

from tests.chatbot.pv2 import _register_envelopes as E
from app.services.chatbot.lanes.business import fetch


def _item(title, **flags):
    return {"title": title, "fields": [{"key": "product_code", "label": "Product Code", "value": title}],
            "flags": {"discontinued": False, "needs_quantity": False, **flags}}


def run(envelope, semantic_input=None):
    return fetch.output_structurer(envelope, {"semantic_input": semantic_input or {}, "access": {"attributes": E.ALL_REVEALS}})


def test_a_plain_list_prints_every_row():
    out = run({"result_type": "products", "intro": "", "items": [_item("A"), _item("B"), _item("C")], "has_result": True})
    assert out["printed_rows"] == [0, 1, 2]


def test_the_key_is_additive_the_reply_text_is_unchanged():
    env = {"result_type": "products", "intro": "", "items": [_item("A"), _item("B")], "has_result": True}
    with_key = run(copy.deepcopy(env))
    assert "A" in with_key["response"] and "B" in with_key["response"]
    assert with_key["answers"] and "printed_rows" in with_key


def test_summary_mode_hides_the_rows_from_the_text_so_none_are_printed():
    env = {"result_type": "orders", "intro": "", "items": [_item("A"), _item("B")], "has_result": True,
           "summary_items": [{"title": None, "fields": [{"label": "Total", "value": "12"}]}]}
    out = run(env)
    assert len(out["answers"]) == 2, "the rows stay in `answers` for a positional pick"
    assert out["printed_rows"] == []


def test_needs_quantity_stock_mode_prints_no_rows():
    env = {"result_type": "stock_availability", "intro": "How many units do you need?",
           "items": [_item("A", needs_quantity=True), _item("B", needs_quantity=True)], "has_result": True}
    out = run(env)
    assert len(out["answers"]) == 2
    assert out["printed_rows"] == []


def test_groups_print_the_group_items_in_group_order():
    a, b, c = _item("A"), _item("B"), _item("C")
    env = {"result_type": "orders", "intro": "", "items": [c, a, b], "has_result": True,
           "groups": [{"key": "g1", "label": "G1", "items": [a]}, {"key": "g2", "label": "G2", "items": [b, c]}]}
    out = run(env)
    assert [x["title"] for x in out["answers"]] == ["A", "B", "C"]
    assert out["printed_rows"] == [0, 1, 2]


def test_status_only_prints_the_first_of_each_duplicate_only():
    dup = lambda: _item("SRTWC8517", discontinued=True)  # noqa: E731
    env = {"result_type": "products", "intro": "", "items": [dup(), dup(), _item("SRTWC8518", discontinued=True)],
           "has_result": True}
    out = run(env, {"requested_attributes": ["discontinued"]})
    assert out["printed_rows"] == [0, 2]
