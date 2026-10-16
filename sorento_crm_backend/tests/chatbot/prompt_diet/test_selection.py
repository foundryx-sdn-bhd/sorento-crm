"""AC-8 .. AC-13: card selection is pure state and vectors, never the user's words.

Red now: `app.services.chatbot.head.prompt_diet` does not exist.
"""
from __future__ import annotations

import ast
import inspect
import itertools
import re

import pytest

from tests.chatbot.prompt_diet._common import MODULE_PY


def _m():
    from app.services.chatbot.head import prompt_diet

    return prompt_diet


ALL = {"product", "stock", "orders"}


def test_ac8_constants_are_pinned() -> None:
    m = _m()
    assert m.CARD_ORDER == ("product", "stock", "orders")
    assert m.AREA_BUNDLE == {
        "master_products": "product", "product_attachment": "product", "promotion": "product",
        "resource_attachment": "product", "ideate": "product",
        "inventory": "stock", "purchase_order": "stock", "spo_allocation": "stock", "purchase_cost": "stock",
        "order": "orders", "sales": "orders", "account": "orders",
    }
    assert set(m.STATE_CARDS) == set()


def test_ac8_retrieved_plus_pinned_union_of_bundles() -> None:
    m = _m()
    got = m.select_cards(retrieved_areas={"inventory"}, pinned={"promotion"}, available=ALL)
    assert got == ["product", "stock"]


@pytest.mark.parametrize("area", ["order", "sales", "account"])
def test_ac8_order_sales_account_are_the_orders_bundle(area: str) -> None:
    m = _m()
    assert m.select_cards(retrieved_areas={area}, pinned=set(), available=ALL) == ["orders"]
    assert m.select_cards(retrieved_areas=set(), pinned={area}, available=ALL) == ["orders"]


def test_ac8_areas_without_a_bundle_select_nothing() -> None:
    m = _m()
    got = m.select_cards(retrieved_areas={"forms", "incoming", "portal_link", "goods_receive"}, pinned=set(), available=ALL)
    assert got == []


def test_ac8_bundles_not_available_are_dropped() -> None:
    m = _m()
    got = m.select_cards(retrieved_areas={"order", "inventory", "no_such_card"}, pinned=set(), available={"orders"})
    assert got == ["orders"]


def test_ac9_only_the_focus_domains_pin() -> None:
    """Round 5: the open question and escalation text is always in core, so state pins nothing."""
    m = _m()
    assert m.pinned_areas(focus_domains=[], pending_kind=None, open_question=None, tasks=[]) == set()
    for kind in ("product_pick", "team_pick", "member_offer", "company_pick", "outstanding_scope"):
        assert m.pinned_areas(focus_domains=[], pending_kind=kind, open_question=None, tasks=[]) == set()
    assert m.pinned_areas(focus_domains=[], pending_kind=None, open_question={"question": "q"}, tasks=[{"id": "t"}]) == set()


def test_ac10_focus_domains_are_pinned() -> None:
    m = _m()
    got = m.pinned_areas(
        focus_domains=["master_products", "inventory"], pending_kind=None, open_question=None, tasks=[]
    )
    assert got == {"master_products", "inventory"}


def test_ac10_pinned_focus_survives_empty_retrieval() -> None:
    """A short reply ("2") retrieves nothing; the focus bundle is still there."""
    m = _m()
    got = m.select_cards(retrieved_areas=set(), pinned={"master_products"}, available=ALL)
    assert got == ["product"]


def test_ac11_output_follows_card_order_whatever_the_input_order() -> None:
    m = _m()
    wanted = {"sales", "inventory", "promotion"}
    results = set()
    for perm in itertools.permutations(sorted(wanted)):
        results.add(tuple(m.select_cards(retrieved_areas=set(perm[:2]), pinned=set(perm[2:]), available=ALL)))
    assert results == {("product", "stock", "orders")}


def test_ac11_same_card_set_builds_identical_prompts() -> None:
    m = _m()
    cards = {"a": "== A ==\nxa", "b": "== B ==\nxb"}
    one = m.build_system_prompt("CORE", cards, ["a", "b"], [])
    two = m.build_system_prompt("CORE", dict(reversed(list(cards.items()))), ["a", "b"], [])
    assert one == two


def test_ac12_prompt_is_core_then_cards_in_order_then_examples() -> None:
    m = _m()
    cards = {"a": "== CARD A ==\ntext-a", "b": "== CARD B ==\ntext-b", "c": "== CARD C ==\ntext-c"}
    ex = m.Example(
        area="a",
        message="Test message for SRT-TEST-1",
        context=None,
        expected_output={"user_goal": "marker-goal-xyz"},
    )
    prompt = m.build_system_prompt("CORE-TEXT", cards, ["b", "a"], [ex])
    assert prompt.startswith("CORE-TEXT")
    assert "text-c" not in prompt, "an unselected card must not appear"
    assert prompt.index("text-b") < prompt.index("text-a"), "cards in the order selected"
    assert prompt.index("Test message for SRT-TEST-1") > prompt.index("text-a"), "examples after all cards"
    assert "marker-goal-xyz" in prompt


def test_ac12_with_no_examples_the_prompt_is_core_plus_cards() -> None:
    m = _m()
    prompt = m.build_system_prompt("CORE-TEXT", {"a": "== A ==\nx"}, ["a"], [])
    assert prompt.startswith("CORE-TEXT") and "== A ==" in prompt


def test_ac13_selection_takes_state_and_vectors_not_text() -> None:
    m = _m()
    assert set(inspect.signature(m.select_cards).parameters) == {"retrieved_areas", "pinned", "available"}
    assert set(inspect.signature(m.pinned_areas).parameters) == {
        "focus_domains", "pending_kind", "open_question", "tasks",
    }
    assert list(inspect.signature(m.nearest_examples).parameters)[:2] == ["db", "vector"]


def test_ac13_module_source_has_no_regex_or_keyword_rules() -> None:
    source = MODULE_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned_modules = {"re", "regex", "difflib", "fuzzywuzzy", "rapidfuzz", "fnmatch"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not {a.name.split(".")[0] for a in node.names} & banned_modules, ast.dump(node)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in banned_modules, ast.dump(node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {"lower", "casefold", "findall", "fullmatch"}, ast.dump(node)
        if isinstance(node, ast.Compare):
            for op, right in zip(node.ops, node.comparators):
                if isinstance(op, (ast.In, ast.NotIn)) and isinstance(right, ast.Name):
                    assert right.id not in {"message", "msg", "user_message", "latest_user_message"}, right.id
    assert not re.search(r"\bre\.", source)
