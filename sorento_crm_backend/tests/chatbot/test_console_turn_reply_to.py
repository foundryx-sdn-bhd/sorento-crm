"""QUOTED-REPLY AC-QR-18: the console's optional `reply_to` rides the dry-run envelope as the
webhook's own `replyTo` quote block. Red until `ConsoleTurnRequest.reply_to` and
`console_service._build_envelope(reply_to=...)` exist.

Setup is copied from `test_console_turn_endpoint.py` (same fixtures, same engine seams).
"""
from __future__ import annotations

import pytest

from app.services.chatbot import console_service
from app.services.chatbot.head import parser as parser_mod
from app.models.chatbot_turn import ChatbotTurn  # noqa: F401  (kept for fixture parity)

from tests.chatbot.test_console_turn_endpoint import (  # noqa: F401  (fixtures + constants)
    CONSOLE_TURN_URL,
    NOT_SUPPORTED_OUTPUT,
    _permissions,
    client,
    seeded_contact_with_a_prior_turn,
    stub_console_seams,
    _run_id,
)
from tests.chatbot.test_engine import CONTACT_ID, _envelope

QUOTE = {"id": "bot-turn-1-0", "text": "Which product do you mean?"}
EXPECTED_REPLY_TO = {
    "id": "bot-turn-1-0",
    "sender": {"source": "n8n"},
    "message": {"text": "Which product do you mean?"},
}


@pytest.fixture()
def captured(monkeypatch):
    """Wraps the real `run_turn` and records the envelope the engine receives."""
    seen: list = []
    real = console_service.run_turn

    def spy(envelope, *args, **kwargs):
        seen.append(envelope.model_dump(mode="json", by_alias=True) if hasattr(envelope, "model_dump") else envelope)
        return real(envelope, *args, **kwargs)

    monkeypatch.setattr(console_service, "run_turn", spy)
    return seen


def _post(client, **extra):
    body = {"contact_respond_id": str(CONTACT_ID), "text": "1 unit", "run_id": _run_id(), **extra}
    return client.post(CONSOLE_TURN_URL, json=body)


def _inner(envelope: dict) -> dict:
    return envelope["message"]["message"]


class TestEndpointCarriesReplyTo:
    def test_reply_to_lands_on_the_envelope_in_the_webhook_shape(
        self, client, seeded_contact_with_a_prior_turn, system_settings_row, stub_console_seams, monkeypatch, captured,
    ):
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: NOT_SUPPORTED_OUTPUT)
        resp = _post(client, reply_to=QUOTE)
        assert resp.status_code == 200, resp.text
        assert len(captured) == 1
        assert _inner(captured[0])["replyTo"] == EXPECTED_REPLY_TO

    def test_without_reply_to_there_is_no_replyto_key(
        self, client, seeded_contact_with_a_prior_turn, system_settings_row, stub_console_seams, monkeypatch, captured,
    ):
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: NOT_SUPPORTED_OUTPUT)
        resp = _post(client)
        assert resp.status_code == 200, resp.text
        assert "replyTo" not in _inner(captured[0])

    def test_null_reply_to_has_no_replyto_key(
        self, client, seeded_contact_with_a_prior_turn, system_settings_row, stub_console_seams, monkeypatch, captured,
    ):
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: NOT_SUPPORTED_OUTPUT)
        resp = _post(client, reply_to=None)
        assert resp.status_code == 200, resp.text
        assert "replyTo" not in _inner(captured[0])


class TestBuildEnvelopeReplyTo:
    def _build(self, **kw):
        return console_service._build_envelope(
            json_base(),
            contact_respond_id=str(CONTACT_ID),
            message_text="1 unit",
            run_id="ZZT-run",
            session_vars=None,
            prompt_version_id=None,
            **kw,
        )

    def test_reply_to_kwarg_sets_replyto(self):
        env = self._build(reply_to=QUOTE)
        assert _inner(env)["replyTo"] == EXPECTED_REPLY_TO

    def test_no_reply_to_kwarg_sets_no_replyto(self):
        assert "replyTo" not in _inner(self._build())

    def test_none_reply_to_sets_no_replyto(self):
        assert "replyTo" not in _inner(self._build(reply_to=None))

    def test_a_stale_replyto_on_the_borrowed_envelope_is_not_carried(self):
        base = json_base()
        base["message"]["message"]["replyTo"] = EXPECTED_REPLY_TO
        env = console_service._build_envelope(
            base, contact_respond_id=str(CONTACT_ID), message_text="x", run_id="ZZT-run",
            session_vars=None, prompt_version_id=None,
        )
        assert "replyTo" not in _inner(env)


def json_base() -> dict:
    import json

    return json.loads(_envelope().model_dump_json())
