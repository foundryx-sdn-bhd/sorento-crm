"""AC-1 .. AC-4, AC-18: the switch, the fallback, the harness bypass and the trace.

Drives `engine.run_turn` on a blank Postgres schema. Only the provider seam
(`llm_call.chat`) and the embedding seam (`prompt_diet.embed_text`) are faked.
"""
from __future__ import annotations

import pytest

from app.services.chatbot import engine as engine_mod
from tests.chatbot.prompt_diet._common import CORE_TXT, card_marker
from tests.chatbot.prompt_diet._engine import STATIC_PROMPT, STATIC_VERSION, DietHarness, emission
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures used by name
    _envelope,
    seeded,
    stub_access,
)

pytestmark = pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")


def test_ac1_default_is_off() -> None:
    from app.config import settings

    assert settings.chatbot_prompt_diet is False


def test_ac1_switch_unset_parser_gets_the_static_prompt_and_no_embedding(
    monkeypatch, session_factory, seeded, stub_access
) -> None:
    h = DietHarness(monkeypatch, [emission()])
    h.watch_embed_only()
    stub_access()

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

    assert result.status == "done"
    assert len(h.parser_calls) == 1
    call = h.parser_calls[0]
    assert h.system_of(call) == STATIC_PROMPT
    assert call["prompt_cache_key"] == f"chatbot_semantic_parser:v{STATIC_VERSION}"
    assert h.embed_calls == []


def test_ac2_on_the_prompt_starts_with_core_and_holds_only_selected_cards(
    monkeypatch, session_factory, seeded, stub_access
) -> None:
    h = DietHarness(monkeypatch, [emission(domain_hint="inventory")])
    h.diet_on(example_areas=["inventory"])
    stub_access()

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

    assert result.status == "done"
    assert len(h.parser_calls) == 1
    system = h.system_of(h.parser_calls[0])
    assert system != STATIC_PROMPT
    assert system.startswith(CORE_TXT.read_text(encoding="utf-8")[:3000])
    assert card_marker("inventory") in system
    for other in ("sales", "order", "account", "promotion", "master_products", "escalation", "open_question"):
        assert card_marker(other) not in system, f"unselected card {other!r} leaked into the prompt"
    assert len(h.embed_calls) == 1


@pytest.mark.parametrize("which", ["embed", "nearest"])
def test_ac3_retrieval_failure_falls_back_to_the_static_prompt_and_answers(
    monkeypatch, session_factory, seeded, stub_access, which: str
) -> None:
    h = DietHarness(monkeypatch, [emission()])
    boom = RuntimeError("embedding provider is down")
    h.diet_on(
        example_areas=["inventory"],
        embed_error=boom if which == "embed" else None,
        nearest_error=boom if which == "nearest" else None,
    )
    stub_access()

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

    assert result.status == "done", "never stuck: the turn must still answer"
    assert len(h.parser_calls) == 1
    assert h.system_of(h.parser_calls[0]) == STATIC_PROMPT
    facts = h.understood(session_factory, result.turn_id)["facts"]
    diet = facts["prompt_diet"]
    assert diet["on"] is True
    assert diet["fallback"], "the trace names why the static prompt was used"
    assert "embedding provider is down" in str(diet["fallback"])


def test_ac3_card_loading_failure_falls_back_too(monkeypatch, session_factory, seeded, stub_access) -> None:
    """A broken cards document is as survivable as a broken embedding call."""
    from app.services.chatbot.head import prompt_diet

    h = DietHarness(monkeypatch, [emission()])
    h.diet_on(example_areas=["inventory"])
    monkeypatch.setattr(
        prompt_diet, "parse_cards", lambda text: (_ for _ in ()).throw(ValueError("duplicate card"))
    )
    stub_access()

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

    assert result.status == "done"
    assert h.system_of(h.parser_calls[0]) == STATIC_PROMPT
    assert h.understood(session_factory, result.turn_id)["facts"]["prompt_diet"]["fallback"]


def test_ac4_harness_bypass_makes_no_parser_call_and_no_embedding(
    monkeypatch, session_factory, seeded, stub_access
) -> None:
    h = DietHarness(monkeypatch, [emission()])
    h.diet_on(example_areas=["inventory"])
    stub_access()

    result = engine_mod.run_turn(
        _envelope(
            is_test=True,
            mock_reformulator_output=emission(user_goal="supplied by the harness", asks=None, topic_reset=None),
        ),
        session_factory=session_factory,
    )

    assert result.status == "done"
    assert h.parser_calls == []
    assert h.embed_calls == []


def test_ac18_trace_carries_the_prompt_diet_facts(monkeypatch, session_factory, seeded, stub_access) -> None:
    h = DietHarness(monkeypatch, [emission(domain_hint="inventory")], usages=[(1200, 30, 400)])
    h.diet_on(example_areas=["inventory", "inventory"])
    stub_access()

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

    facts = h.understood(session_factory, result.turn_id)["facts"]
    diet = facts["prompt_diet"]
    assert set(diet) >= {"on", "cards", "pinned", "examples", "missing", "recalled", "fallback"}
    assert diet["on"] is True
    assert diet["cards"] == ["inventory"]
    assert list(diet["pinned"]) == []
    assert len(diet["examples"]) == 2
    assert list(diet["missing"]) == []
    assert diet["recalled"] is False
    assert diet["fallback"] is None
    assert facts["prompt_tokens"] == 1200
    assert facts["cached_tokens"] == 400


def test_ac18_off_turn_records_on_false(monkeypatch, session_factory, seeded, stub_access) -> None:
    h = DietHarness(monkeypatch, [emission()])
    h.watch_embed_only()
    stub_access()

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

    diet = h.understood(session_factory, result.turn_id)["facts"]["prompt_diet"]
    assert diet["on"] is False
    assert diet["fallback"] is None
