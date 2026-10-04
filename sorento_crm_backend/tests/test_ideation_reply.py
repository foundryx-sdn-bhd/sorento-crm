"""Ideation access-denied reply composer: LLM reply, canned-text fallback.

AC-1307: the access-denied reply for the ``ideation`` agent goes through the composer with
facts ``{denied: "ideation"}``, falling back to the existing ``access_denied`` template on any
failure or a reply that invents a URL or asks a question. The draft-status reply composer
this file used to cover was removed with the multi-turn draft flow (IDEATION-CAPTURE C2).

The LLM call is STUBBED; Postgres only (``tests/_pg_fixture.py``).
"""
from __future__ import annotations
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.services.ai_assistant_service import AIAssistantConfigService
from app.services.ideation_turn_service import compose_ideate_denial_reply
from app.services.llm_provider import ChatResult
from tests._pg_fixture import blank_session

@pytest.fixture
def db_session() -> Session:
    with blank_session() as session:
        yield session


@pytest.fixture
def configured(db_session: Session) -> Session:
    cfg = AIAssistantConfigService(db_session).get()
    cfg.api_key_ciphertext = "fake-key"
    cfg.provider = "openai"
    cfg.model = "gpt-4o-mini"
    cfg.is_enabled = True
    db_session.commit()
    return db_session


class _StubProvider:
    def __init__(self, text: str):
        self._text = text
        self.calls: list = []

    def chat(self, messages, *_a, **_k):
        self.calls.append(messages)
        return ChatResult(content=self._text, prompt_tokens=1, completion_tokens=1, total_tokens=2)


class _BoomProvider:
    def chat(self, *_a, **_k):
        raise RuntimeError("provider down")


def _patched(provider):
    return patch("app.services.ideation_turn_service.get_provider", return_value=provider)


# --------------------------------------------------------------------------- #
# AC-1301 / AC-1306 - facts + user message reach the model                    #
FALLBACK = "Sorry, you are not allowed to access ideation"


def test_user_message_reaches_the_model_for_language(configured):
    stub = _StubProvider("Maaf, idea tidak tersedia untuk anda sekarang.")
    with _patched(stub):
        compose_ideate_denial_reply(
            configured,
            user_message="saya ada idea, tanda harga patut tunjuk harga promo warna merah",
            fallback_text=FALLBACK,
        )
    user_block = stub.calls[0][1]["content"]
    assert "status: access_denied" in user_block
    assert "denied_agent: ideation" in user_block
    assert "saya ada idea" in user_block  # AC-1306: the user's own message rides along


def test_no_api_key_falls_back(db_session):
    """No AI assistant config seeded -> the composer never even calls the model."""
    out = compose_ideate_denial_reply(db_session, user_message="hi", fallback_text=FALLBACK)
    assert out == FALLBACK



# --------------------------------------------------------------------------- #
# Reviewer Should fix 3 - the denial reply rejects an invented URL/question    #
# --------------------------------------------------------------------------- #
def test_denial_reply_with_url_falls_back(configured):
    with _patched(_StubProvider("Not available. Try https://evil.test ?")):
        out = compose_ideate_denial_reply(
            configured, user_message="i have an idea", fallback_text="Sorry, you are not allowed to access ideation"
        )
    assert out == "Sorry, you are not allowed to access ideation"


def test_denial_reply_with_question_falls_back(configured):
    with _patched(_StubProvider("Sorry, would you like to try a different agent?")):
        out = compose_ideate_denial_reply(
            configured, user_message="i have an idea", fallback_text="Sorry, you are not allowed to access ideation"
        )
    assert out == "Sorry, you are not allowed to access ideation"


# --------------------------------------------------------------------------- #
# AC-1307 - access-denied reply for the ideation agent                        #
# --------------------------------------------------------------------------- #
def test_denial_reply_uses_llm_output(configured):
    with _patched(_StubProvider("Sorry, idea capture isn't available for you right now.")):
        out = compose_ideate_denial_reply(
            configured, user_message="i have an idea", fallback_text="Sorry, you are not allowed to access ideation"
        )
    assert out == "Sorry, idea capture isn't available for you right now."


def test_denial_reply_falls_back_on_provider_failure(configured):
    with _patched(_BoomProvider()):
        out = compose_ideate_denial_reply(
            configured, user_message="i have an idea", fallback_text="Sorry, you are not allowed to access ideation"
        )
    assert out == "Sorry, you are not allowed to access ideation"


def test_denial_reply_falls_back_on_empty_output(configured):
    with _patched(_StubProvider("")):
        out = compose_ideate_denial_reply(
            configured, user_message="i have an idea", fallback_text="Sorry, you are not allowed to access ideation"
        )
    assert out == "Sorry, you are not allowed to access ideation"
