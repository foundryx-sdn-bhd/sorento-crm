"""INTENT-FRAMES with the LIVE parser: the owner's sequences end to end.

The real provider reads each message with the prompt this branch publishes
(`SEMANTIC_PARSER_PROMPT` plus the committed policy-blocks seed, the shape
`chatbot_rearch_s4._body` publishes, `INTENT_FRAMES_ADDENDUM` included); the engine, the
resolver, the lanes and the presenters are real; only the tool reads are stubbed
(`_r9_engine_console.EngineConsole`).

Opt-in, never in CI: `INTENT_FRAMES_LIVE_PARSER=1 CHATBOT_LIVE_LLM=1` (model `LOW_STOCK_LIVE_MODEL`, default
`gpt-5.4-mini`, the production parser model; key `OPENAI_API_KEY`, or a placeholder where an
egress proxy injects it).
"""
from __future__ import annotations

import json
import os

import pytest

from app.services.chatbot import turn_runtime

from tests.chatbot._r9_engine_console import EngineConsole
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture

LIVE = os.environ.get("INTENT_FRAMES_LIVE_PARSER") == "1"
pytestmark = [
    pytest.mark.skipif(not LIVE, reason="live parser run: set INTENT_FRAMES_LIVE_PARSER=1"),
    # NO-LIVE-LLM-TESTS (#1456): a real provider call needs the marker and CHATBOT_LIVE_LLM=1 too.
    pytest.mark.live_external,
]

ASK = "How many units"
A, B = "SRTWC286-SH-150", "SRTWC286-SH-200"


class LiveConsole(EngineConsole):
    """`EngineConsole` with the parser put back: every turn is read by the real model."""

    def __init__(self, session_factory, monkeypatch, stub_access, *, phone: str) -> None:
        super().__init__(session_factory, monkeypatch, stub_access, phone=phone)
        from app.services.chatbot.head import parser as parser_mod
        from tests.chatbot.test_low_stock_live_parser import _config

        self.readings: list[dict] = []
        original = type(self)._original_parse

        def live_parse(config, user_block):
            out = dict(original(_config(), user_block))
            self.readings.append(out)
            return out

        monkeypatch.setattr(parser_mod, "parse", live_parse)
        monkeypatch.setattr(parser_mod, "resolve_config", lambda db, **k: _config())

    def tell(self, message: str) -> str:
        text = self.say(message, {"_live": True})
        r = self.readings[-1]
        print(json.dumps({
            "message": message, "intent": r.get("intent_hint"), "continuation": r.get("continuation"),
            "entity_op": r.get("entity_op"), "topic_reset": r.get("topic_reset"),
            "positions": r.get("reference_positions"), "answer": r.get("open_question_answer"),
            "dim": r.get("domain_in_message"), "domain": r.get("domain_hint"),
            "ents": [(e.get("raw"), e.get("hint"), e.get("current_message")) for e in r.get("entities") or []], "broaden": [r.get("scope_intent"), r.get("broaden_axis"), r.get("broaden_to"), r.get("match_mode")],
            "stored": (self.stored_question or {}).get("kind"),
            "rules": list(self.last_trace.rules_fired or []),
            "reply": text[:160],
        }, ensure_ascii=False))
        return text


def _original_parse():
    from app.services.chatbot.head import parser as parser_mod

    return parser_mod.parse


@pytest.fixture
def live(session_factory, monkeypatch, stub_access):
    LiveConsole._original_parse = staticmethod(_original_parse())
    return lambda phone: LiveConsole(session_factory, monkeypatch, stub_access, phone=phone)


def _focus(c) -> dict:
    return (c.state or {}).get("focus") or {}


def test_live_availability_then_low_stock_then_compact(live, monkeypatch, stub_access):
    c = live("+60000014741")
    stub_access(attributes=["scm.low_stock_report"])
    assert c.tell(f"check stock {A} and {B}").startswith("How many units for each?")
    since = len(c.tool_calls)
    text = c.tell("low stock report for sorento water tap")
    assert ASK not in text, text
    for _name, args in c.tool_calls[since:]:
        assert not args.get("product_ids"), args
    assert _focus(c).get("products") == []
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    c.stock_on_hand = 40
    assert ASK not in c.tell("stock?")


def test_live_incoming_pick_check_stock_then_7(live, monkeypatch):
    c = live("+60000014742")
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    c.stock_on_hand = 12
    assert c.tell("incoming srtwc286").startswith("Which product do you mean?")
    c.tell("4")
    since = len(c.tool_calls)
    text = c.tell("check stock")
    stock_calls = [a for n, a in c.tool_calls[since:] if n == "crm_inventory_stock_balance_list"]
    assert stock_calls and [c.codes[p] for p in stock_calls[-1]["product_ids"]] == ["SRTWC286-SH-NEW"], (text, stock_calls)
    # "7" can pick only while the roster is still the open question. In this harness the
    # incoming tool returns no rows, so "4" ends in a miss whose escalation offer replaces
    # the roster (the same on the base branch); the pick is then nothing to answer.
    if (c.stored_question or {}).get("kind") == "product_pick":
        since = len(c.tool_calls)
        c.tell("7")
        incoming = [a for n, a in c.tool_calls[since:] if n == "crm_incoming_stock_list"]
        assert incoming and [c.codes[p] for p in incoming[0]["product_ids"]] == ["SRTWC286-SH-NEW-P"], incoming


def test_live_clear_drops_the_frame(live):
    c = live("+60000014743")
    c.tell(f"check stock {A}")
    c.tell("clear")
    focus = _focus(c)
    assert focus.get("products") == [] and not focus.get("tasks"), focus


def test_live_eta_follow_up_carries_the_product(live, monkeypatch):
    c = live("+60000014744")
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    c.stock_on_hand = 12
    c.tell(f"stock {A}")
    text = c.tell("got eta?")
    assert f"No incoming for {A}" in text, text


def test_live_ignored_offer_is_not_accepted_later(live, monkeypatch):
    """Security review B1, live: a miss offers escalation, the contact asks something else,
    then "ok thanks" never hands the chat to a person."""
    c = live("+60000014745")
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    c.stock_on_hand = 12
    offer = c.tell("check stock SRTWX0000")
    c.tell(f"stock {A}")
    c.tell("ok thanks")
    assert c.last_trace.lane not in ("escalation",), (offer, c.last_trace.lane)
    assert (c.stored_question or {}).get("kind") not in ("team_pick", "member_offer", "company_pick")


def test_live_ok_thanks_after_an_ignored_offer_never_escalates(live, monkeypatch):
    """B1 without a new ask in between: the offer, an aside, then "ok thanks"."""
    c = live("+60000014746")
    monkeypatch.setattr(turn_runtime, "_stock_availability_only", lambda *a, **k: False)
    offer = c.tell("check stock SRTWX0000")
    assert "escalate" in offer.casefold(), offer
    c.tell("nice weather today")
    c.tell("ok thanks")
    assert c.last_trace.lane != "escalation", c.last_trace.lane
    assert (c.stored_question or {}).get("kind") not in ("team_pick", "member_offer", "company_pick")
