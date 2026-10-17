"""Save-time validation tests for app/rule_engine/schemas.validate_tree.

Runs against the ``promotion`` fact source (the only registered source). Verifies
the empty-problem-list happy path plus every rejection: unknown fact, operator
not allowed for the fact type, between-arity, list-operator emptiness, cross-fact
scalar-only + type match, empty group, and depth exceeded.
"""
from __future__ import annotations

from app.rule_engine.schemas import validate_tree


def _cond(fact, operator, value, value_kind="literal"):
    return {
        "kind": "condition",
        "fact": fact,
        "operator": operator,
        "valueKind": value_kind,
        "value": value,
    }


def _group(combinator, *rules):
    return {"kind": "group", "combinator": combinator, "rules": list(rules)}


SOURCES = ["promotion"]


def test_valid_tree_no_problems():
    tree = _group(
        "or",
        _cond("promotion.accessLevels", "contains_any", ["sorento_dealer"]),
        _cond("promotion.name", "contains", "Sorento"),
    )
    assert validate_tree(tree, SOURCES) == []


def test_non_group_root_rejected():
    problems = validate_tree({"kind": "condition"}, SOURCES)
    assert problems == ["Conditions must be a group."]


def test_unknown_fact_rejected():
    tree = _group("and", _cond("promotion.unknownField", "eq", "x"))
    problems = validate_tree(tree, SOURCES)
    assert any("Unknown field" in p for p in problems)


def test_operator_not_allowed_for_fact_type():
    # promotion.name is a string; contains_any is a list operator -> invalid.
    tree = _group("and", _cond("promotion.name", "contains_any", ["x"]))
    problems = validate_tree(tree, SOURCES)
    assert any("not valid for" in p for p in problems)


def test_between_needs_exactly_two_nonblank_values():
    number_fact = "promotion.endDate.daysUntil"
    too_few = _group("and", _cond(number_fact, "between", [1]))
    assert any("exactly two values" in p for p in validate_tree(too_few, SOURCES))

    blank_bound = _group("and", _cond(number_fact, "between", [1, ""]))
    assert any("exactly two values" in p for p in validate_tree(blank_bound, SOURCES))

    ok = _group("and", _cond(number_fact, "between", [1, 10]))
    assert validate_tree(ok, SOURCES) == []


def test_list_operator_needs_non_empty_list():
    tree = _group("and", _cond("promotion.accessLevels", "contains_any", []))
    problems = validate_tree(tree, SOURCES)
    assert any("at least one value" in p for p in problems)


def test_cross_fact_requires_scalar_operator():
    # contains_any is not a cross-fact operator -> reject a fact-vs-fact compare.
    tree = _group(
        "and",
        _cond(
            "promotion.accessLevels",
            "contains_any",
            "promotion.name",
            value_kind="fact",
        ),
    )
    problems = validate_tree(tree, SOURCES)
    assert any("cannot compare against another field" in p for p in problems)


def test_cross_fact_requires_matching_types():
    # startDate (date) compared with name (string) via 'before' -> type mismatch.
    tree = _group(
        "and",
        _cond("promotion.startDate", "before", "promotion.name", value_kind="fact"),
    )
    problems = validate_tree(tree, SOURCES)
    assert any("Cannot compare" in p for p in problems)


def test_cross_fact_same_type_ok():
    tree = _group(
        "and",
        _cond("promotion.startDate", "before", "promotion.endDate", value_kind="fact"),
    )
    assert validate_tree(tree, SOURCES) == []


def test_empty_group_rejected():
    tree = _group("and")  # no rules
    problems = validate_tree(tree, SOURCES)
    assert any("Empty group" in p for p in problems)


def test_depth_exceeded_rejected():
    inner = _cond("promotion.name", "contains", "x")
    node = _group("and", inner)
    for _ in range(6):
        node = _group("and", node)
    problems = validate_tree(node, SOURCES)
    assert any("nesting depth" in p for p in problems)


import pytest  # noqa: E402


@pytest.mark.parametrize("operator", ["eq", "neq"])
@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_number_eq_neq_refuses_a_non_finite_value(operator, bad):
    tree = _group("and", _cond("promotion.endDate.daysUntil", operator, bad))
    assert validate_tree(tree, SOURCES) != []
