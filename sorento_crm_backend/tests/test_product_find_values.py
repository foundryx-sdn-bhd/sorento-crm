"""CHAT-FIND round 3: typed filter VALUES in the find registry (crew audit, registry note).

A filter may take products (`product_ref`, as `fits` does), a value, or both. A value is one of
three kinds, declared on the row and checked by the route, never by the lane:

* `code`   a code-shaped string (stripped, 1 to 64 characters);
* `number` a finite number with a unit from the row's `units` (the first unit when none is said);
* `enum`   one of the row's `values` (a tuple, or a callable reading them from the database).

The runner receives one `FilterArg(product_ids, value, unit)`, so #1582's spec / promotion
filters move in as rows 2 and 3 with no code branch in the route, the lane or the MCP tool.
Plan: documentation/plans/chatbot/PLAN-chat-generic-find-12oct.md section 2.7.
"""
from __future__ import annotations

import json

import pytest

from app.services import product_find as pf
from app.services.error_handler import AppException

PID = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _row(key, *, products=False, value=None, ask="Which one?"):
    seen = {}

    def run(db, arg):
        seen["arg"] = arg
        return [pf.Group(None, None, None, f"{key} head", [])]

    return pf.FindFilter(
        key=key,
        vocabulary=f"{key}: test row",
        labels={},
        run=run,
        products=products,
        value=value,
        ask=ask,
    ), seen


@pytest.fixture()
def rows(monkeypatch):
    made = {
        "code_of": _row("code_of", value=pf.ValueSpec("code")),
        "size_of": _row("size_of", value=pf.ValueSpec("number", units=("mm", "cm"))),
        "finish_of": _row("finish_of", value=pf.ValueSpec("enum", values=("matt_black", "chrome"))),
        "db_enum": _row("db_enum", value=pf.ValueSpec("enum", values=lambda db: {"a", "b"})),
        "both": _row("both", products=True, value=pf.ValueSpec("code")),
    }
    for key, (row, _seen) in made.items():
        monkeypatch.setitem(pf.FILTERS, key, row)
    return {key: seen for key, (_row_, seen) in made.items()}


def _parse(items, db=None):
    return pf.parse_filters(json.dumps(items), db=db)


def _code(fn):
    with pytest.raises(AppException) as raised:
        fn()
    assert raised.value.status_code == 422
    return raised.value.code


# --------------------------------------------------------------------------- shapes


def test_v1_the_fits_row_takes_products_and_no_value():
    fits = pf.FILTERS["fits"]
    assert fits.products is True and fits.value is None
    assert fits.ask == pf.ASK_PRODUCT_LINE
    assert fits.type == pf.TYPE_PRODUCT_REF


def test_v2_code_value_is_stripped(rows):
    assert _parse([{"key": "code_of", "value": "  SRT-123 "}]) == [
        {"key": "code_of", "value": "SRT-123", "unit": None}
    ]


@pytest.mark.parametrize("bad", [None, "", "   ", 12, True, "x" * 65, ["a"]])
def test_v3_a_bad_or_missing_code_is_422(rows, bad):
    code = _code(lambda: _parse([{"key": "code_of", "value": bad}]))
    assert code in ("bad_value", "value_required")


def test_v4_number_with_its_unit_and_the_default_unit(rows):
    assert _parse([{"key": "size_of", "value": 250, "unit": "mm"}]) == [
        {"key": "size_of", "value": 250, "unit": "mm"}
    ]
    assert _parse([{"key": "size_of", "value": 2.5}]) == [{"key": "size_of", "value": 2.5, "unit": "mm"}]


@pytest.mark.parametrize(
    "item",
    [
        {"value": "250"},
        {"value": True},
        {"value": float("inf")},
        {"value": 250, "unit": "inch"},
        {},
    ],
)
def test_v5_a_bad_number_or_unit_is_422(rows, item):
    raw = json.dumps([{"key": "size_of", **item}]) if item.get("value") != float("inf") else (
        '[{"key": "size_of", "value": Infinity}]'
    )
    with pytest.raises(AppException) as raised:
        pf.parse_filters(raw)
    assert raised.value.status_code == 422
    assert raised.value.code in ("bad_value", "value_required")


def test_v6_enum_value_from_a_tuple_and_from_the_database(rows):
    assert _parse([{"key": "finish_of", "value": "chrome"}]) == [
        {"key": "finish_of", "value": "chrome", "unit": None}
    ]
    assert _parse([{"key": "db_enum", "value": "b"}], db=object()) == [
        {"key": "db_enum", "value": "b", "unit": None}
    ]
    assert _code(lambda: _parse([{"key": "finish_of", "value": "gold"}])) == "bad_value"


def test_v7_a_row_with_products_and_a_value_needs_both(rows):
    assert _code(lambda: _parse([{"key": "both", "value": "X1"}])) == "product_ids_required"
    assert _code(lambda: _parse([{"key": "both", "product_ids": [PID]}])) == "value_required"
    assert _parse([{"key": "both", "product_ids": [PID], "value": "X1"}]) == [
        {"key": "both", "product_ids": [PID], "value": "X1", "unit": None}
    ]


def test_v8_a_value_given_to_a_products_only_row_is_ignored():
    assert _parse([{"key": "fits", "product_ids": [PID], "value": "anything"}]) == [
        {"key": "fits", "product_ids": [PID]}
    ]


def test_v9_a_repeated_value_key_with_another_value_is_refused(rows):
    items = [{"key": "code_of", "value": "A"}, {"key": "code_of", "value": "B"}]
    assert _code(lambda: _parse(items)) == "bad_value"
    assert _parse([{"key": "code_of", "value": "A"}] * 2) == [{"key": "code_of", "value": "A", "unit": None}]


def test_v10_the_runner_gets_one_filter_arg(rows):
    pf.run_find(None, [{"key": "size_of", "value": 250, "unit": "mm"}], with_stock=False)
    arg = rows["size_of"]["arg"]
    assert (arg.product_ids, arg.value, arg.unit) == ([], 250, "mm")


def test_v11_a_group_with_no_anchor_renders_its_head(rows):
    body = pf.run_find(None, [{"key": "code_of", "value": "A", "unit": None}], with_stock=False)
    assert body["groups"] == [
        {"filter": "code_of", "anchor": None, "relation": None, "head": "code_of head", "rows": [], "more": 0}
    ]


def test_v12_the_vocabulary_names_every_row(rows):
    text = pf.parser_vocabulary()
    for key in ("fits", "code_of", "size_of", "finish_of", "both"):
        assert f"{key}:" in text
