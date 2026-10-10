"""Fix round 1: the embedding call uses the key in `ai_assistant_configs`, never an env key.

A missing key is a recorded fallback to the static prompt, not an exception out of the turn.
"""
from __future__ import annotations

import pytest

from app.config import settings
from app.services import llm_provider
from app.services.chatbot import engine as engine_mod
from tests._pg_fixture import blank_session
from tests.chatbot.prompt_diet._engine import STATIC_PROMPT, DietHarness, emission
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures used by name
    _envelope,
    seeded,
    stub_access,
)


def _m():
    from app.services.chatbot.head import prompt_diet

    return prompt_diet


class _FakeOpenAI:
    instances: list = []

    def __init__(self, api_key, default_model=None):
        self.api_key = api_key
        _FakeOpenAI.instances.append(self)

    def embed(self, text):
        return [0.5] * 1536


def test_embed_text_sends_the_access_key_through_the_openai_provider(monkeypatch) -> None:
    m = _m()
    _FakeOpenAI.instances = []
    monkeypatch.setattr(llm_provider, "OpenAIProvider", _FakeOpenAI)
    monkeypatch.setattr(settings, "openai_api_key", "")
    vector = m.embed_text(m.EmbeddingAccess(api_key="sk-from-config", model="text-embedding-3-small"), "hello")
    assert len(vector) == 1536
    assert [i.api_key for i in _FakeOpenAI.instances] == ["sk-from-config"]


def test_embed_text_without_a_key_raises_no_embedding_key_and_calls_no_provider(monkeypatch) -> None:
    m = _m()
    _FakeOpenAI.instances = []
    monkeypatch.setattr(llm_provider, "OpenAIProvider", _FakeOpenAI)
    monkeypatch.setattr(settings, "openai_api_key", "sk-env-must-not-be-used")
    with pytest.raises(Exception, match="no embedding key"):
        m.embed_text(m.EmbeddingAccess(api_key="", model="text-embedding-3-small"), "hello")
    assert _FakeOpenAI.instances == []


def test_the_key_is_never_in_the_access_repr() -> None:
    m = _m()
    assert "sk-secret" not in repr(m.EmbeddingAccess(api_key="sk-secret", model="x"))


def test_resolve_embedding_access_reads_the_config_row(monkeypatch) -> None:
    from app.services.ai_assistant_service import AIAssistantConfigService

    m = _m()
    monkeypatch.setattr(settings, "openai_api_key", "")
    with blank_session() as db:
        row = AIAssistantConfigService(db).get()
        row.provider = "openai"
        row.api_key_ciphertext = "sk-config-row"
        db.flush()
        access = m.resolve_embedding_access(db)
    assert access.api_key == "sk-config-row"
    assert access.model == settings.embedding_model_name


@pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")
def test_engine_hands_the_config_key_to_embed_text(monkeypatch, session_factory, seeded, stub_access) -> None:
    # Resolution is tested above against a real config row; a configured key in this DB
    # would also switch on the pipeline's other provider calls, so it is stubbed here.
    monkeypatch.setattr(
        _m(), "resolve_embedding_access", lambda db: _m().EmbeddingAccess(api_key="sk-config-engine", model="m")
    )
    h = DietHarness(monkeypatch, [emission(domain_hint="inventory")])
    h.diet_on(example_areas=["inventory"])
    seen = []

    def spy(access, text):
        seen.append(access)
        return [0.0] * 1536

    monkeypatch.setattr(_m(), "embed_text", spy)
    stub_access()

    engine_mod.run_turn(_envelope(), session_factory=session_factory)

    assert len(seen) == 1 and seen[0].api_key == "sk-config-engine"


@pytest.mark.usefixtures("_no_real_mcp_calls", "_stub_casual_llm")
def test_engine_without_a_key_falls_back_with_the_reason(monkeypatch, session_factory, seeded, stub_access) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "")
    real_embed = _m().embed_text  # captured before the harness swaps in its fake
    h = DietHarness(monkeypatch, [emission()])
    h.diet_on(example_areas=["inventory"])
    monkeypatch.setattr(_m(), "embed_text", real_embed)
    stub_access()

    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)

    assert result.status == "done"
    assert h.system_of(h.parser_calls[0]) == STATIC_PROMPT
    diet = h.understood(session_factory, result.turn_id)["facts"]["prompt_diet"]
    assert "no embedding key" in str(diet["fallback"])
