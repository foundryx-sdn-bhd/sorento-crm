"""QUOTED-REPLY slice 4: `scripts/chatbot_console_check.py` case key `quote: <text>`.

UAC: AC-QR-16. The pure envelope builder only; the YAML replay of AC-QR-8 and AC-QR-9
against the live parser is a manual console pass (it needs the live OpenAI parser) and has
no pytest form.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chatbot_console_check.py"


def _module():
    spec = importlib.util.spec_from_file_location("chatbot_console_check_qr", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _borrowed() -> dict:
    return {
        "contact": {"id": 900000041, "firstName": "ZZT"},
        "message": {
            "event_type": "message.received",
            "contact": {"id": 900000041},
            "message": {
                "messageId": "ZZT-borrowed-1",
                "contactId": 900000041,
                "channelId": "whatsapp",
                "traffic": "incoming",
                "message": {"type": "text", "text": "borrowed text"},
            },
        },
    }


def _build(**kwargs):
    return _module()._envelope_for(_borrowed(), contact="900000041", run_id="zzt-qr-run", **kwargs)


def _inner(envelope) -> dict:
    return envelope["message"]["message"] if isinstance(envelope, dict) else envelope.message["message"]


def test_ac_qr_16_envelope_for_accepts_a_quote_kwarg_and_sets_a_bot_sourced_reply_to():
    envelope = _build(message="1 unit", quote="How many units of ELP3754?")
    reply_to = _inner(envelope)["replyTo"]
    assert reply_to["message"]["text"] == "How many units of ELP3754?"
    assert reply_to["sender"]["source"] != "contact"
    assert reply_to["id"], "a synthetic id is stamped"


def test_ac_qr_16_no_quote_leaves_the_envelope_without_a_reply_to():
    envelope = _build(message="1 unit")
    assert "replyTo" not in _inner(envelope)
