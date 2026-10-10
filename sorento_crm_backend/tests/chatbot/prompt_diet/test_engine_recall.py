"""AC-10 (engine half), AC-14 .. AC-17, AC-19: the missing-area re-call.

The parser's own output says which card it needed; one re-call adds it; never a third.
"""
from __future__ import annotations

import pytest

from app.services.chatbot import engine as engine_mod
from tests.chatbot.prompt_diet._common import card_marker
from tests.chatbot.prompt_diet._engine import DietHarness, emission
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures used by name
    _envelope,
    seeded,
    stub_access,
)

pytestmark = pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")


def _run(session_factory):
    return engine_mod.run_turn(_envelope(), session_factory=session_factory)


def test_ac10_empty_retrieval_still_parses_on_core_alone(monkeypatch, session_factory, seeded, stub_access) -> None:
    h = DietHarness(monkeypatch, [emission(domain_hint=None)])
    h.diet_on(example_areas=[])
    stub_access()

    result = _run(session_factory)

    assert result.status == "done"
    assert len(h.parser_calls) == 1
    diet = h.understood(session_factory, result.turn_id)["facts"]["prompt_diet"]
    assert diet["fallback"] is None
    assert diet["cards"] == []


def test_ac14_a_domain_hint_naming_an_unloaded_card_triggers_one_recall(
    monkeypatch, session_factory, seeded, stub_access
) -> None:
    first = emission(domain_hint="promotion", intent_hint="first_emission")
    second = emission(domain_hint="promotion", intent_hint="second_emission")
    h = DietHarness(monkeypatch, [first, second])
    h.diet_on(example_areas=["inventory"])
    stub_access()

    result = _run(session_factory)

    assert len(h.parser_calls) == 2
    one, two = (h.system_of(c) for c in h.parser_calls)
    assert card_marker("promotion") not in one
    assert card_marker("inventory") in one
    assert card_marker("promotion") in two
    assert card_marker("inventory") in two, "the re-call ADDS a card, it does not swap"
    facts = h.understood(session_factory, result.turn_id)["facts"]
    assert facts["intent"] == "second_emission", "the second emission is the one used"
    diet = facts["prompt_diet"]
    assert list(diet["missing"]) == ["promotion"]
    assert diet["recalled"] is True


def test_ac14_an_ask_domain_naming_an_unloaded_card_triggers_the_recall(
    monkeypatch, session_factory, seeded, stub_access
) -> None:
    first = emission(domain_hint="inventory", asks=[{"domain": "inventory", "intent": "a"}, {"domain": "promotion", "intent": "b"}])
    h = DietHarness(monkeypatch, [first, emission(domain_hint="inventory")])
    h.diet_on(example_areas=["inventory"])
    stub_access()

    result = _run(session_factory)

    assert len(h.parser_calls) == 2
    assert card_marker("promotion") in h.system_of(h.parser_calls[1])
    assert list(h.understood(session_factory, result.turn_id)["facts"]["prompt_diet"]["missing"]) == ["promotion"]


@pytest.mark.parametrize("status", ["sales_report", "sales_analysis", "top_selling", "sales_ranking"])
def test_ac15_sales_order_status_without_the_sales_card_recalls_with_sales_and_companions(
    monkeypatch, session_factory, seeded, stub_access, status: str
) -> None:
    first = emission(domain_hint="inventory", order_status=status)
    h = DietHarness(monkeypatch, [first, first])
    h.diet_on(example_areas=["inventory"])
    stub_access()

    _run(session_factory)

    assert len(h.parser_calls) == 2
    one, two = (h.system_of(c) for c in h.parser_calls)
    assert card_marker("sales") not in one
    for name in ("sales", "account", "order"):
        assert card_marker(name) in two, f"{name} missing from the re-call"


def test_ac16_never_more_than_two_parser_calls_and_usage_is_summed(
    monkeypatch, session_factory, seeded, stub_access
) -> None:
    """The second emission names yet another unloaded card: still no third call."""
    first = emission(domain_hint="sales")
    second = emission(domain_hint="promotion")
    h = DietHarness(monkeypatch, [first, second], usages=[(1000, 10, 500), (2000, 20, 700)])
    h.diet_on(example_areas=["inventory"])
    stub_access()

    result = _run(session_factory)

    assert len(h.parser_calls) == 2
    facts = h.understood(session_factory, result.turn_id)["facts"]
    assert facts["prompt_tokens"] == 3000
    assert facts["completion_tokens"] == 30
    assert facts["tokens"] == 3030
    assert facts["cached_tokens"] == 1200
    assert facts["prompt_diet"]["recalled"] is True


def test_ac17_no_recall_when_every_needed_card_was_loaded(monkeypatch, session_factory, seeded, stub_access) -> None:
    h = DietHarness(monkeypatch, [emission(domain_hint="inventory", asks=[{"domain": "inventory", "intent": "a"}])])
    h.diet_on(example_areas=["inventory"])
    stub_access()

    result = _run(session_factory)

    assert len(h.parser_calls) == 1
    diet = h.understood(session_factory, result.turn_id)["facts"]["prompt_diet"]
    assert list(diet["missing"]) == []
    assert diet["recalled"] is False


def test_ac17_no_recall_for_a_domain_that_has_no_card(monkeypatch, session_factory, seeded, stub_access) -> None:
    h = DietHarness(monkeypatch, [emission(domain_hint="a_domain_with_no_card")])
    h.diet_on(example_areas=["inventory"])
    stub_access()

    _run(session_factory)

    assert len(h.parser_calls) == 1


def test_ac19_cache_key_names_the_core_version_and_the_selected_cards(
    monkeypatch, session_factory, seeded, stub_access
) -> None:
    h = DietHarness(monkeypatch, [emission(domain_hint="inventory")])
    h.diet_on(example_areas=["inventory"])
    stub_access()

    result = _run(session_factory)

    key = h.parser_calls[0]["prompt_cache_key"]
    cards = h.understood(session_factory, result.turn_id)["facts"]["prompt_diet"]["cards"]
    assert key.startswith("chatbot_parser_core:v")
    assert key.endswith(":" + "+".join(cards))
    assert cards == ["inventory"]


def test_ac19_the_recall_has_a_different_cache_key(monkeypatch, session_factory, seeded, stub_access) -> None:
    h = DietHarness(monkeypatch, [emission(domain_hint="promotion"), emission(domain_hint="promotion")])
    h.diet_on(example_areas=["inventory"])
    stub_access()

    _run(session_factory)

    first, second = (c["prompt_cache_key"] for c in h.parser_calls)
    assert first != second
    assert second.endswith("promotion") or "promotion" in second.split(":")[-1].split("+")
