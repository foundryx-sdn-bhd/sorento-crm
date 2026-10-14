"""PV2-FIX r7i (reasoning-low dev run, cand-0026 "97503 eta"): a clarify flag about an entity the ask itself
carries resolves that entity; it never becomes an option-less question."""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import ClarifyAct, Lookup
from tests.chatbot.pv2.carry_fakes import ask as _base_ask, mk_state, out
from tests.chatbot.pv2.test_fix_round2 import _Fake

CLARIFY = {"needed": True, "phrase": "97503", "about": "entity"}


def _parsed():
    base = _base_ask().model_dump()
    ent = {"kind": "order", "key": None, "phrase": "97503", "op": "set", "refers_to_held": False,
           "quantity": None, "doc_number": None}
    base.update(act="new_topic", domain="incoming", entities=[ent])
    return out(Ask.model_validate(base), clarify=CLARIFY)


def _run(table):
    return apply_turn(mk_state(), _parsed(), resolver=_Fake(table))


def test_an_ambiguous_entity_under_a_clarify_flag_gives_the_candidates_menu():
    table = {"order|97503": ("ambiguous", [("product", "MBF97503"), ("product", "MMC97503")], False)}
    state, actions = _run(table)
    act = actions[0]
    assert isinstance(act, ClarifyAct) and act.about == "entity"
    assert [o.payload.get("key") for o in act.pending.options] == ["MBF97503", "MMC97503"]


def test_a_unique_entity_under_a_clarify_flag_is_a_lookup():
    table = {"order|97503": ("exact", [("product", "MBF97503")], False)}
    state, actions = _run(table)
    assert isinstance(actions[0], Lookup), actions[0]
    assert [r.key for r in state.held.refs["product"]] == ["MBF97503"]


def test_an_unknown_entity_under_a_clarify_flag_gives_the_not_found_line():
    state, actions = _run({})
    act = actions[0]
    assert isinstance(act, ClarifyAct) and act.not_found is True
    assert "97503" in act.pending.asked


def _rendered(table):
    from app.services.chatbot.pv2 import turn
    from app.services.chatbot.pv2.history import MemoryProfile
    from app.services.chatbot.pv2.state import dump_state
    from tests.chatbot.pv2.carry_fakes import NOW

    parsed = _parsed()
    ctx = turn.TurnV2Context(
        text="97503 eta", now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience="staff"),
        parse=lambda m, u: parsed, resolver=_Fake(table), load_exchanges=lambda s: [], offer_mode="none")
    return turn._run(ctx).direct_reply or ""


@pytest.mark.parametrize("table", [
    {},
    {"order|97503": ("ambiguous", [("product", "A"), ("product", "B")], False)},
], ids=["unknown", "ambiguous"])
def test_the_rendered_reply_never_asks_which_phrase_without_numbered_options(table):
    reply = _rendered(table)
    asks_which = 'Which "97503"' in reply
    numbered = any(line.strip().startswith("1.") for line in reply.splitlines())
    assert not (asks_which and not numbered), reply
