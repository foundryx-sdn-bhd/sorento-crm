"""PV2-FIX round 5d: PV2 writes v1's own did-you-mean text (lanes/business/answer.py build_suggest_offer,
~5340-5360 and `_cont`), keeps the PV2 pending; portal_form only for filling a form."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.chatbot.pv2 import turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import ClarifyAct, HandOver, Lookup, dump_state

from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask as _base_ask, mk_state, out
from tests.chatbot.pv2.test_fix_round2 import _Fake, _e

TABLE = {
    "product|SRTXYZ999": ("ambiguous", [("product", "SRTWB999")], True),
    "product|SRTXYZ998": ("ambiguous", [("product", "SRTWB998")], True),
}


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


def _ctx(parsed, state=None, **extra):
    return turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(state or mk_state()), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=_Fake(TABLE), load_exchanges=lambda s: [], **extra)


def _single():
    return out(_ask(domain="inventory", entities=[_e(phrase="SRTXYZ999")]))


V1_OFFER = ('Couldn\'t find "SRTXYZ999" (product). Did you mean:\n1. SRTWB999\n'
            "Reply with a code to continue, or would you like me to escalate to warehouse team?")
V1_STAFF = ('Couldn\'t find "SRTXYZ999" (product). Did you mean:\n1. SRTWB999\n'
            "Reply with a code to continue.")


def test_a_single_did_you_mean_is_written_in_v1s_words_with_the_offer():
    res = turn._run(_ctx(_single(), domain_teams={"inventory": "warehouse"}, offer_mode="offer"))
    assert isinstance(res.actions[0], ClarifyAct) and not any(isinstance(a, Lookup) for a in res.actions)
    assert V1_OFFER in res.direct_reply, res.direct_reply


def test_a_staff_did_you_mean_has_no_escalate_clause():
    res = turn._run(_ctx(_single(), offer_mode="none"))
    assert V1_STAFF in res.direct_reply, res.direct_reply
    assert "escalate" not in res.direct_reply.lower()


def test_a_two_part_ask_gets_one_did_you_mean_question_never_a_v1_miss():
    parsed = out(_ask(domain="inventory", entities=[_e(phrase="SRTXYZ999")]),
                 _ask(domain="incoming", entities=[_e(phrase="SRTXYZ998")]))
    res = turn._run(_ctx(parsed, offer_mode="offer"))
    assert res.direct_reply.count("Did you mean") == 1, res.direct_reply
    assert not any(isinstance(a, Lookup) and a.misses for a in res.actions)


def test_picking_the_suggestion_resolves_it_and_keeps_the_domain():
    first = turn._run(_ctx(_single(), domain_teams={"inventory": "warehouse"}, offer_mode="offer"))
    pick = out(_ask(act="answers_bot_question", answer={"option_key": "1", "free_value": None}))
    second = turn._run(_ctx(pick, state=first.state, domain_teams={"inventory": "warehouse"}, offer_mode="offer"))
    assert isinstance(second.actions[0], Lookup)
    assert [r.key for r in second.state.held.refs["product"]] == ["SRTWB999"]
    assert second.state.held.domain == "inventory"


def test_yes_to_the_did_you_mean_offer_hands_over_to_the_domains_team():
    first = turn._run(_ctx(_single(), domain_teams={"inventory": "warehouse"}, offer_mode="offer"))
    yes = out(_ask(act="answers_bot_question", answer={"option_key": "yes", "free_value": None}))
    second = turn._run(_ctx(yes, state=first.state, domain_teams={"inventory": "warehouse"}, offer_mode="offer"))
    assert any(isinstance(a, HandOver) and a.team_key == "warehouse" for a in second.actions)


def test_the_prompt_limits_portal_form_to_filling_in_a_form():
    prompt = (Path(turn.__file__).parent / "prompt_v2.txt").read_text(encoding="utf-8").lower()
    assert "fill in" in prompt
