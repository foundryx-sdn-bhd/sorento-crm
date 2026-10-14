"""PV2-FIX r7e (security review): stock-denied contacts get an untagged did-you-mean, "all" under a v1
product pick goes to v1 as mode "all", availability-mode dealers keep v1's which-one for a partial."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.schema import Ask, ParserV2Output
from app.services.chatbot.pv2.state import Lookup, dump_state
from tests.chatbot.pv2 import replay
from tests.chatbot.pv2.carry_fakes import NOW, ask as _base_ask, held as mk_held, mk_state, option, out, pending
from tests.chatbot.pv2.test_fix_round2 import _Fake

TODAY = date(2026, 10, 14)
LOO = {json.loads(l)["case"]: json.loads(l) for l in
       (Path(__file__).parent / "fixtures" / "livecheck_loo_r7.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


# ------------------------------------------------------------------ 1 stock-denied contacts get no tags


def _stub_denial(monkeypatch, *, switch, stock_allowed):
    from app.services.chatbot import engine as engine_mod
    from app.services.chatbot import turn_runtime

    monkeypatch.setattr(engine_mod, "_stock_denial_enabled", lambda db, row=None: switch)
    monkeypatch.setattr(turn_runtime, "load_profile",
                        lambda db, contact: (SimpleNamespace(stock_allowed=stock_allowed), None))
    return lambda: SimpleNamespace(close=lambda: None)


def test_a_contact_v1_would_stock_deny_gets_no_did_you_mean_tags(monkeypatch):
    factory = _stub_denial(monkeypatch, switch=True, stock_allowed=False)
    assert turn.dym_tags_for(factory, "c1", "s1") is None


def test_a_stock_allowed_contact_still_gets_tags(monkeypatch):
    factory = _stub_denial(monkeypatch, switch=True, stock_allowed=True)
    assert callable(turn.dym_tags_for(factory, "c1", "s1"))


def test_with_the_denial_switch_off_every_contact_gets_tags(monkeypatch):
    factory = _stub_denial(monkeypatch, switch=False, stock_allowed=False)
    assert callable(turn.dym_tags_for(factory, "c1", "s1"))


# ------------------------------------------------------------------ 2 "all" under a product pick goes to v1 as all


def _pick_all_verdict(v1_kind):
    h = mk_held("inventory", {"product": ["SRTWC286"]})
    state = mk_state(h, pending_=pending("v1_open_question", [option(str(i), f"C{i}") for i in range(1, 6)]))
    state.pending.v1_kind = v1_kind
    state, actions = apply_turn(state, out(_ask(act="answers_bot_question",
                                                answer={"option_key": "all", "free_value": None})), resolver=_Fake({}))
    assert isinstance(actions[0], Lookup), actions[0]
    return bridge.to_v1_verdict(state, actions[0], today=TODAY)["open_question_answer"]


def test_all_under_a_product_pick_is_sent_to_v1_as_mode_all():
    answer = _pick_all_verdict("product_pick")
    assert answer["mode"] == "all", answer


def test_all_under_another_v1_pick_keeps_every_position():
    answer = _pick_all_verdict("customer_pick")
    assert answer["mode"] == "pick" and answer["picked"] == [1, 2, 3, 4, 5], answer


# ------------------------------------------------------------------ 3 availability dealers keep v1's which-one


def _wc286(**extra):
    row = LOO["loo-B3-WC286_stock"]
    parsed = ParserV2Output.model_validate(row["parser_out"])
    ctx = turn.TurnV2Context(
        text=row["text"], now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=replay.CannedResolver(row["resolve"]), load_exchanges=lambda s: [],
        **extra)
    return turn._run(ctx)


def test_staff_keep_the_list_all_settle_for_a_partial_stock_ask():
    res = _wc286()
    assert isinstance(res.actions[0], Lookup)
    assert len(res.state.held.refs["product"]) == 10


def test_an_availability_dealer_is_not_given_every_match_as_exact_codes():
    res = _wc286(stock_availability_only=True)
    assert isinstance(res.actions[0], Lookup)
    refs = res.state.held.refs["product"]
    assert len(refs) == 1, [r.key for r in refs]


def test_a_detailed_stock_dealer_keeps_the_list_all_settle():
    res = _wc286(stock_availability_only=False)
    assert len(res.state.held.refs["product"]) == 10


# ------------------------------------------------------------------ 4 "all" on PV2's own menu for availability dealers


def _own_menu_state(kind):
    opts = [option(str(i), f"SRTWC286-V{i}", {"kind": "product", "key": f"SRTWC286-V{i}", "label": f"SRTWC286-V{i}"})
            for i in range(1, 4)]
    return mk_state(mk_held("inventory"), pending_=pending(kind, opts, asked="Which one?"))


def _answer_all(kind, **extra):
    from tests.chatbot.pv2.carry_fakes import FakeResolver

    parsed = out(_ask(act="answers_bot_question", answer={"option_key": "all", "free_value": None}))
    ctx = turn.TurnV2Context(
        text="all", now=NOW, raw_state=dump_state(_own_menu_state(kind)), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=FakeResolver({}), load_exchanges=lambda s: [], **extra)
    return turn._run(ctx)


@pytest.mark.parametrize("kind", ["clarify_entity", "clarify_did_you_mean"])
def test_an_availability_dealer_cannot_pick_all_on_pv2s_own_menu(kind):
    from app.services.chatbot.turn.apply import STOCK_PICK_NOT_ALL

    res = _answer_all(kind, stock_availability_only=True)
    assert STOCK_PICK_NOT_ALL in (res.direct_reply or ""), res.direct_reply
    assert res.state.pending is not None and res.state.pending.kind == kind
    assert res.state.held.refs.get("product", []) == []


@pytest.mark.parametrize("kind", ["clarify_entity"])
def test_a_staff_or_detailed_contacts_all_still_picks_every_option(kind):
    res = _answer_all(kind)
    assert [r.key for r in res.state.held.refs["product"]] == ["SRTWC286-V1", "SRTWC286-V2", "SRTWC286-V3"]
