"""PV2-FIX r8b (reviewer on r8a): an exact code that is also a family code is never expanded, family
expansion is capped at v1's 20, and the ms/zh did-you-mean keeps its full stop before the offer."""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2 import clarify_copy, turn
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile
from app.services.chatbot.pv2.resolve import DbResolver, Entity
from app.services.chatbot.pv2.schema import Ask
from app.services.chatbot.pv2.state import Lookup, ResolvedRef, dump_state
from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import NOW, ask as _base_ask, mk_state, out
from tests.chatbot.pv2.test_fix_round2 import _Fake, _e

FAMILY_LIMIT = 20  # v1's PREFIX_LIMIT


def _ask(**kw) -> Ask:
    base = _base_ask().model_dump()
    base.update(kw)
    return Ask.model_validate(base)


def _incoming(db, phrase):
    resolver = DbResolver(db, pinned={})
    parsed = out(_ask(domain="incoming", entities=[_e(phrase=phrase)]))
    return apply_turn(mk_state(), parsed, resolver=resolver)


def _ent(phrase):
    return Entity.model_construct(kind="product", key=None, phrase=phrase, op="set", refers_to_held=False,
                                  doc_number=None, quantity=None)


def _seed_family(db, family, codes, name_is_code=False):
    cat = S.category(db)
    for code in codes:
        row = S.product(db, code, cat=cat)
        row.family_code = family
    db.flush()


# ------------------------------------------------------------------ 1 an exact code that is a family code


def test_the_pinned_family_flag_exists_and_defaults_false():
    assert ResolvedRef(kind="product", key="A", label="A").family is False


def test_an_exact_code_that_is_also_a_family_code_stays_one_product():
    with S.resolve_session() as db:
        _seed_family(db, "SRTWF12C", ["SRTWF12C", "SRTWF12C-BL-NEW", "SRTWF12C-GM-NEW"])
        state, actions = _incoming(db, "SRTWF12C")
    assert isinstance(actions[0], Lookup)
    assert [r.key for r in state.held.refs["product"]] == ["SRTWF12C"], state.held.refs


def test_a_typed_family_with_no_exact_code_is_marked_family_and_expanded():
    with S.resolve_session() as db:
        _seed_family(db, "SRTWC286", ["SRTWC286-SH", "SRTWC286-SH-NEW", "SRTWC286-SH-P"])
        resolver = DbResolver(db, pinned={})
        res = resolver.resolve_entities([_ent("SRTWC286")], held=mk_state().held)[0]
        assert res.status == "exact" and res.refs[0].family is True
        state, actions = _incoming(db, "SRTWC286")
    assert sorted(r.key for r in state.held.refs["product"]) == ["SRTWC286-SH", "SRTWC286-SH-NEW", "SRTWC286-SH-P"]


def test_an_exact_code_is_not_marked_family():
    with S.resolve_session() as db:
        _seed_family(db, "SRTWF12C", ["SRTWF12C", "SRTWF12C-BL-NEW"])
        resolver = DbResolver(db, pinned={})
        res = resolver.resolve_entities([_ent("SRTWF12C")], held=mk_state().held)[0]
    assert res.status == "exact" and res.refs[0].key == "SRTWF12C" and res.refs[0].family is False


# ------------------------------------------------------------------ 2 the cap


def test_a_family_of_25_expands_to_at_most_20_members():
    codes = [f"SRTBIG1-V{n:02d}" for n in range(1, 26)]
    with S.resolve_session() as db:
        _seed_family(db, "SRTBIG1", codes)
        state, actions = _incoming(db, "SRTBIG1")
    refs = state.held.refs["product"]
    assert isinstance(actions[0], Lookup)
    assert 1 < len(refs) <= FAMILY_LIMIT, len(refs)


# ------------------------------------------------------------------ 3 punctuation in ms / zh


def _did_you_mean(language):
    table = {"product|SRTXYZ999": ("ambiguous", [("product", "SRTWB999")], True)}
    parsed = out(_ask(domain="inventory", entities=[_e(phrase="SRTXYZ999")]), language=language)
    ctx = turn.TurnV2Context(
        text="x", now=NOW, raw_state=dump_state(mk_state()), profile=MemoryProfile(audience="dealer"),
        parse=lambda m, u: parsed, resolver=_Fake(table), load_exchanges=lambda s: [],
        domain_teams={"inventory": "warehouse"}, offer_mode="offer")
    return turn._run(ctx).direct_reply or ""


@pytest.mark.parametrize("language", ["ms", "zh"])
def test_the_did_you_mean_keeps_a_full_stop_before_the_translated_offer(language):
    offer = clarify_copy.escalate_offer("warehouse", language)
    reply = _did_you_mean(language)
    assert f"Reply with a code to continue. {offer}" in reply, reply
