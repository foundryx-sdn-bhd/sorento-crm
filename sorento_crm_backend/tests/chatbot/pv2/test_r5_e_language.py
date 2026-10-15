"""PV2 R5 slice E (UAC R5-6): the reply language of PV2's own copy. Red tests, no LLM, no DB.

Driven through the hook entry (`turn.run_turn_v2`) with a hand-written parse, like test_carry_review_r1.py::_hook.
The conversation language is the one the engine saved in `session_vars["reply_language"]`. A language-neutral turn
(a number, 'ok', a product code) keeps it even when the parse says "en" or "mixed"; a turn typed in a language
wins. Expected copy comes from `clarify_copy.text(key, lang)`, never from literals.
"""
from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from app.services.chatbot.pv2 import clarify_copy, turn
from app.services.chatbot.pv2.state import dump_state
from app.services.chatbot.turn.state import Profile
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held, mk_state, option, out, pending


def _drive(monkeypatch, *, text, parsed, carried, state=None):
    cfg = SimpleNamespace(keys=SimpleNamespace(entity_keys={}, attachment_types=[]), index_sha="sha",
                          prompt_version=1, version_id="v", cache_key="k")
    call = types.ModuleType("app.services.chatbot.pv2.call")
    call.resolve_v2_config = lambda db, override_version_id=None: cfg
    call.parse_v2 = lambda config, history, memory_block, text: SimpleNamespace(output=parsed, usage={}, dropped=[])
    res = types.ModuleType("app.services.chatbot.pv2.resolve")
    res.DbResolver = lambda db, pinned=None, **kw: FakeResolver()
    import app.services.chatbot.pv2 as pv2_pkg

    for name, mod in (("call", call), ("resolve", res)):
        monkeypatch.setitem(sys.modules, f"app.services.chatbot.pv2.{name}", mod)
        monkeypatch.setattr(pv2_pkg, name, mod, raising=False)
    monkeypatch.setattr("app.services.chatbot.head.access.default_space_id", lambda db: None)
    monkeypatch.setattr("app.services.chatbot.lanes.business.services._mcp_call", lambda db=None: (lambda *a, **k: {}))
    monkeypatch.setattr(turn, "load_exchanges", lambda db, **kw: [])
    sv = {"reply_language": carried}
    if state is not None:
        sv["pv2"] = dump_state(state)
    hctx = SimpleNamespace(
        session_factory=lambda: SimpleNamespace(close=lambda: None), override_version_id=None,
        contact_respond_id="r-1", envelope=SimpleNamespace(ingress="whatsapp", is_test=False), dry_run=False,
        session_block={"session_vars": sv}, profile=Profile(), text=text, now=NOW, memory_intake=None, state_in=None)
    return turn.run_turn_v2(hctx)


def _nothing_held_parse(language):
    return out(ask(act="new_topic", domain="inventory", entities=[entity("product", refers_to_held=True)]),
               language=language)


NEUTRAL = ["SRTWC9017", "ok", "3"]  # fresh wording: a code, a filler, a number


@pytest.mark.parametrize("carried", ["ms", "zh"])
@pytest.mark.parametrize("parsed_lang", ["en", "mixed"])
@pytest.mark.parametrize("typed", NEUTRAL)
def test_e1_a_neutral_turn_keeps_the_conversation_language_in_pv2s_own_clarify(monkeypatch, carried, parsed_lang, typed):
    res = _drive(monkeypatch, text=typed, parsed=_nothing_held_parse(parsed_lang), carried=carried)
    assert res.route == "clarify" and res.direct_reply, res.route
    assert clarify_copy.text("nothing_held", carried) in res.direct_reply, res.direct_reply
    assert res.language == carried


@pytest.mark.parametrize("carried", ["ms", "zh"])
def test_e2_a_neutral_turn_keeps_the_language_in_the_reask_line_and_hint(monkeypatch, carried):
    st = mk_state(held("order", {}), pending_=pending(
        "clarify_entity", [option("1", "SRTWC9017"), option("2", "SRTWC9018")],
        asked=clarify_copy.text("which", carried), asked_turn=3), turn_no=3)
    parsed = out(ask(act="answers_bot_question", answer={"option_key": "zz", "free_value": None}), language="en")
    res = _drive(monkeypatch, text="zz", parsed=parsed, carried=carried, state=st)
    assert res.route == "clarify", res.route
    assert clarify_copy.text("reask", carried) in res.direct_reply, res.direct_reply
    assert clarify_copy.text("reply_number", carried) in res.direct_reply, res.direct_reply


@pytest.mark.parametrize("carried", ["ms", "zh"])
@pytest.mark.parametrize("route_ask,route", [(ask(act="small_talk"), "small_talk")])
def test_e3_a_neutral_small_talk_turn_carries_the_conversation_language_to_the_composer(monkeypatch, carried, route_ask, route):
    res = _drive(monkeypatch, text="ok", parsed=out(route_ask, language="en"), carried=carried)
    assert res.route == route and res.language == carried, (res.route, res.language)


def test_e4_a_neutral_lookup_turn_carries_the_conversation_language(monkeypatch):
    st = mk_state(held("inventory", {"product": ["SRTWC9017"]}), turn_no=3)
    parsed = out(ask(act="refines_held", domain="inventory"), language="mixed")
    res = _drive(monkeypatch, text="3", parsed=parsed, carried="ms", state=st)
    assert res.route == "lookup" and res.language == "ms", (res.route, res.language)


def test_e5_english_typed_in_a_malay_conversation_replies_english(monkeypatch):  # guard: passes today
    res = _drive(monkeypatch, text="could you check the stock of this product for me please",
                 parsed=_nothing_held_parse("en"), carried="ms")
    assert clarify_copy.text("nothing_held", "en") in res.direct_reply, res.direct_reply
    assert res.language == "en"


def test_e6_chinese_typed_in_an_english_conversation_replies_chinese(monkeypatch):  # guard: passes today
    res = _drive(monkeypatch, text="请问这个产品还有库存吗", parsed=_nothing_held_parse("zh"), carried="en")
    assert clarify_copy.text("nothing_held", "zh") in res.direct_reply, res.direct_reply
    assert res.language == "zh"


def test_e7_malay_typed_in_a_chinese_conversation_replies_malay_even_when_parsed_en(monkeypatch):
    res = _drive(monkeypatch, text="tolong semak stok barang ni untuk saya", parsed=_nothing_held_parse("en"), carried="zh")
    assert clarify_copy.text("nothing_held", "ms") in res.direct_reply, res.direct_reply
    assert res.language == "ms"


def test_e8_guard_no_carried_language_follows_the_parse(monkeypatch):  # guard: passes today
    res = _drive(monkeypatch, text="SRTWC9017", parsed=_nothing_held_parse("ms"), carried=None)
    assert clarify_copy.text("nothing_held", "ms") in res.direct_reply, res.direct_reply
