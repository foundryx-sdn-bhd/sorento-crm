"""`scripts/chatbot_console_check.py` borrows a real contact's latest envelope and swaps the
text in. The borrowed envelope's QUOTED message (`tf-message.message.replyTo`) must not ride
along: `engine._message_text` appends it as a `reply to: ...` line the parser reads
(CHATBOT-TRIO, 7 Oct 2026: a dev contact's last turn was a quoted-reply test, and its nine
quoted product codes came back as entities on every console turn of that contact)."""
from __future__ import annotations

import importlib.util
import pathlib

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "chatbot_console_check.py"
_spec = importlib.util.spec_from_file_location("chatbot_console_check", _SCRIPT)
console_check = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(console_check)


def _borrowed() -> dict:
    return {
        "contact": {"id": 487555417, "phone": "+60100000000"},
        "message": {
            "contact": {"id": 487555417},
            "event_type": "message.received",
            "message": {
                "contactId": 487555417,
                "messageId": "wamid.original",
                "message": {"type": "text", "text": "another 10"},
                "replyTo": {
                    "id": "console-quote-32caacf6b8fc",
                    "message": {"text": "5. BRBC22137W-1 - \n6. BRBC22144W - "},
                },
            },
        },
    }


def test_the_borrowed_quote_does_not_ride_into_the_console_turn():
    out = console_check._envelope_for(
        _borrowed(), contact="900000072", message="they got pic/", run_id="console-check-test"
    )
    inner = out["message"]["message"]
    assert inner["message"] == {"type": "text", "text": "they got pic/"}
    assert "replyTo" not in inner, inner


def test_the_base_envelope_is_never_mutated():
    base = _borrowed()
    console_check._envelope_for(base, contact="900000072", message="pic", run_id="r")
    assert base["message"]["message"]["replyTo"]["id"] == "console-quote-32caacf6b8fc"
