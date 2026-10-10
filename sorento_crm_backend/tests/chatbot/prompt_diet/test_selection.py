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


ALL = {
    "open_question", "escalation", "account", "master_products", "sales", "order", "promotion",
    "resource_attachment", "purchase_order", "spo_allocation", "purchase_cost", "inventory",
    "ideate", "product_attachment",
}


def test_ac8_constants_are_pinned() -> None:
    m = _m()
    assert m.COMPANIONS == {"order": ("account",), "sales": ("account", "order")}
    assert set(m.STATE_CARDS) == {"open_question", "escalation"}


def test_ac8_retrieved_plus_pinned_union() -> None:
    m = _m()
    got = m.select_cards(retrieved_areas={"inventory"}, pinned={"promotion"}, available=ALL)
    assert set(got) == {"inventory", "promotion"}


def test_ac8_order_brings_account() -> None:
    m = _m()
    got = m.select_cards(retrieved_areas={"order"}, pinned=set(), available=ALL)
    assert set(got) == {"order", "account"}


def test_ac8_sales_brings_account_and_order() -> None:
    m = _m()
    got = m.select_cards(retrieved_areas={"sales"}, pinned=set(), available=ALL)
    assert set(got) == {"sales", "account", "order"}


def test_ac8_a_pinned_area_also_brings_its_companions() -> None:
    m = _m()
    got = m.select_cards(retrieved_areas=set(), pinned={"sales"}, available=ALL)
    assert set(got) == {"sales", "account", "order"}


def test_ac8_names_not_available_are_dropped() -> None:
    m = _m()
    got = m.select_cards(retrieved_areas={"order", "no_such_card"}, pinned=set(), available={"order"})
    assert got == ["order"]


def test_ac9_open_question_pins_the_open_question_card() -> None:
    m = _m()
    got = m.pinned_areas(
        focus_domains=[], pending_kind=None, open_question={"question": "which one?"}, tasks=[]
    )
    assert got == {"open_question"}


@pytest.mark.parametrize("kind", ["product_pick", "customer_pick", "member_offer", "outstanding_scope"])
def test_ac9_a_pending_pick_or_offer_pins_open_question(kind: str) -> None:
    m = _m()
    got = m.pinned_areas(focus_domains=[], pending_kind=kind, open_question=None, tasks=[])
    assert "open_question" in got


def test_ac9_an_open_task_pins_open_question() -> None:
    m = _m()
    got = m.pinned_areas(focus_domains=[], pending_kind=None, open_question=None, tasks=[{"id": "t1"}])
    assert got == {"open_question"}


@pytest.mark.parametrize("kind", ["team_pick", "member_offer", "company_pick"])
def test_ac9_a_pending_escalation_offer_pins_escalation(kind: str) -> None:
    m = _m()
    got = m.pinned_areas(focus_domains=[], pending_kind=kind, open_question=None, tasks=[])
    assert "escalation" in got


def test_ac9_a_business_pick_does_not_pin_escalation() -> None:
    m = _m()
    got = m.pinned_areas(focus_domains=[], pending_kind="product_pick", open_question=None, tasks=[])
    assert "escalation" not in got


def test_ac9_nothing_open_pins_nothing() -> None:
    m = _m()
    got = m.pinned_areas(focus_domains=[], pending_kind=None, open_question=None, tasks=[])
    assert got == set()


def test_ac10_focus_domains_are_pinned() -> None:
    m = _m()
    got = m.pinned_areas(
        focus_domains=["master_products", "inventory"], pending_kind=None, open_question=None, tasks=[]
    )
    assert got == {"master_products", "inventory"}


def test_ac10_pinned_focus_survives_empty_retrieval() -> None:
    """A short reply ("2") retrieves nothing; the focus card is still there."""
    m = _m()
    got = m.select_cards(retrieved_areas=set(), pinned={"master_products"}, available=ALL)
    assert got == ["master_products"]


def test_ac11_card_order_puts_state_cards_first() -> None:
    m = _m()
    assert set(m.CARD_ORDER[:2]) == {"open_question", "escalation"}
    assert len(set(m.CARD_ORDER)) == len(m.CARD_ORDER)


def test_ac11_output_follows_card_order_whatever_the_input_order() -> None:
    m = _m()
    wanted = {"sales", "inventory", "open_question", "promotion"}
    expected = [c for c in m.CARD_ORDER if c in wanted | {"account", "order"}]
    results = set()
    for perm in itertools.permutations(sorted(wanted)):
        retrieved = set(perm[:2])
        pinned = set(perm[2:])
        results.add(tuple(m.select_cards(retrieved_areas=retrieved, pinned=pinned, available=ALL)))
    assert results == {tuple(expected)}


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
