"""PV2-FIX round 5 red tests: offer picks only on an explicit yes / option, an all-miss list names
every product, the PO phrasing is pinned in the prompt."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.services.chatbot.pv2 import bridge, turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import Lookup, dump_state

from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask as _base_ask, held as mk_held, mk_state, option, out
from tests.chatbot.pv2.test_fix_round2 import _Fake, _e

TODAY = date(2026, 10, 16)


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


# ------------------------------------------------------------------ 1 offer pick only on explicit yes / option


def _offer_pick(key, v1_kind="team_pick"):
    h = mk_held("inventory", {"product": ["SRTWC286"]})
    picked = option(key, "Option", {"kind": "choice", "key": key, "v1_kind": v1_kind})
    action = Lookup(ask_index=0, domain="inventory", held=h, picked=picked, pending_kind="v1_open_question")
    return bridge.to_v1_verdict(mk_state(h), action, today=TODAY)


def test_an_unrecognised_answer_never_confirms_an_escalation_offer():
    v = _offer_pick("maybe")
    assert v["open_question_answer"]["mode"] is None
    assert v["escalation"]["is_escalation_confirmation"] is not True
    assert v["is_affirmative"] is not True


def test_a_yes_and_an_option_still_confirm_and_a_no_still_declines():
    assert _offer_pick("yes")["escalation"]["is_escalation_confirmation"] is True
    assert _offer_pick("1")["escalation"]["is_escalation_confirmation"] is True
    no = _offer_pick("no")
    assert no["is_affirmative"] is False and no["escalation"]["is_escalation_confirmation"] is not True


# ------------------------------------------------------------------ 2 all-miss list names every product


def _all_missing(**extra):
    parsed = out(_ask(domain="inventory", entities=[_e(phrase="SRTAAA1"), _e(phrase="SRTBBB2"), _e(phrase="SRTCCC3")]))
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=FakeResolver({}), load_exchanges=lambda s: [], **extra)
    return turn._run(ctx)


V1_LIST = 'Couldn\'t find: "SRTAAA1" (product), "SRTBBB2" (product), "SRTCCC3" (product).'


def test_an_all_miss_list_names_every_product_in_v1s_words_for_staff():
    res = _all_missing(offer_mode="none")
    assert res.direct_reply.strip() == V1_LIST, res.direct_reply


def test_an_all_miss_list_keeps_the_offer_after_the_full_list():
    res = _all_missing(offer_mode="offer", domain_teams={"inventory": "warehouse"})
    assert f"{V1_LIST} Would you like me to escalate to warehouse team?" in res.direct_reply, res.direct_reply


# ------------------------------------------------------------------ 3 PO phrasing pinned in the prompt


def test_the_prompt_routes_a_products_po_question_to_purchase_order():
    prompt = (Path(turn.__file__).parent / "prompt_v2.txt").read_text(encoding="utf-8").lower()
    assert "got po" in prompt


def _prompt():
    return (Path(turn.__file__).parent / "prompt_v2.txt").read_text(encoding="utf-8").lower()


def test_the_prompt_says_delivery_words_decide_a_documents_kind():
    assert "done deliver" in _prompt()


def test_the_prompt_keeps_filler_and_question_words_out_of_a_document_phrase():
    p = _prompt()
    assert "bila sampai" in p and "boss" in p
