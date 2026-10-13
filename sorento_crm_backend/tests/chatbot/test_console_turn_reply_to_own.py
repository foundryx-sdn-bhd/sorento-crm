"""QUOTED-REPLY AC-QR-23 (console backend): `reply_to.source` ("n8n" | "contact") rides the dry-run
envelope as `replyTo.sender.source`, and `ConsoleTurnResponse.message_id` is the respond message id
of the customer message the engine received. Setup is copied from `test_console_turn_reply_to.py`.
"""
from __future__ import annotations

import pytest

from app.services.chatbot import console_service
from app.services.chatbot.head import parser as parser_mod

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


@pytest.fixture()
def captured(monkeypatch):
    seen: list = []
    real = console_service.run_turn

    def spy(envelope, *args, **kwargs):
        seen.append(envelope.model_dump(mode="json", by_alias=True) if hasattr(envelope, "model_dump") else envelope)
        return real(envelope, *args, **kwargs)

    monkeypatch.setattr(console_service, "run_turn", spy)
    return seen


def _post(client, **extra):
    body = {"contact_respond_id": str(CONTACT_ID), "text": "and eta?", "run_id": _run_id(), **extra}
    return client.post(CONSOLE_TURN_URL, json=body)


def _inner(envelope: dict) -> dict:
    return envelope["message"]["message"]


def _json_base() -> dict:
    import json

    return json.loads(_envelope().model_dump_json())


class TestSourceOnTheEnvelope:
    def test_contact_source_lands_on_the_envelope(
        self, client, seeded_contact_with_a_prior_turn, system_settings_row, stub_console_seams, monkeypatch, captured,
    ):
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: NOT_SUPPORTED_OUTPUT)
        resp = _post(client, reply_to={"id": "m-1", "text": "SRTBF11841 have stock?", "source": "contact"})
        assert resp.status_code == 200, resp.text
        assert _inner(captured[0])["replyTo"] == {
            "id": "m-1",
            "sender": {"source": "contact"},
            "message": {"text": "SRTBF11841 have stock?"},
        }

    def test_absent_source_defaults_to_n8n(
        self, client, seeded_contact_with_a_prior_turn, system_settings_row, stub_console_seams, monkeypatch, captured,
    ):
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: NOT_SUPPORTED_OUTPUT)
        resp = _post(client, reply_to={"id": "bot-1-0", "text": "Which product?"})
        assert resp.status_code == 200, resp.text
        assert _inner(captured[0])["replyTo"]["sender"] == {"source": "n8n"}

    def test_unknown_source_is_422(
        self, client, seeded_contact_with_a_prior_turn, system_settings_row, stub_console_seams, monkeypatch, captured,
    ):
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: NOT_SUPPORTED_OUTPUT)
        resp = _post(client, reply_to={"id": "m-1", "text": "x", "source": "x"})
        assert resp.status_code == 422, resp.text
        assert captured == []

    def test_build_envelope_carries_a_contact_source(self):
        env = console_service._build_envelope(
            _json_base(),
            contact_respond_id=str(CONTACT_ID),
            message_text="and eta?",
            run_id="ZZT-run",
            session_vars=None,
            prompt_version_id=None,
            reply_to={"id": "m-1", "text": "own text", "source": "contact"},
        )
        assert _inner(env)["replyTo"]["sender"] == {"source": "contact"}


class TestResponseMessageId:
    def test_message_id_is_the_envelope_message_id(
        self, client, seeded_contact_with_a_prior_turn, system_settings_row, stub_console_seams, monkeypatch, captured,
    ):
        monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: NOT_SUPPORTED_OUTPUT)
        resp = _post(client)
        assert resp.status_code == 200, resp.text
        sent = _inner(captured[0])["messageId"]
        assert sent
        assert resp.json()["message_id"] == sent
