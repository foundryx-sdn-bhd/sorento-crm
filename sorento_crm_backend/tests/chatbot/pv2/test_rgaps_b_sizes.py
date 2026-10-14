"""PV2 redesign R-GAPS-b, RG-2 (#1582): a size on any axis of the held family. No model call.

DB level on a blank Postgres schema (the real `DbResolver` + `apply_turn`), because the behaviour lives in how a bare
number meets the real spec rows. One parser flag test runs at the fake level.
"""
from __future__ import annotations

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2 import answer
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.resolve import DbResolver
from app.services.chatbot.pv2.schema import Clarify
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state
from tests.chatbot.pv2 import _resolve_seed as S
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, out, spec

PINNED = {
    "brand": set(), "product_type": {"Water Closet"}, "product_class": {"water_closet"}, "attachment_type": set(),
    "spec_attribute": {"dim_length", "dim_trap"}, "spec_value": set(),
}


@pytest.fixture()
def db():
    with S.resolve_session() as session:
        S.registry(session, "dim_length", "Length", "numeric", unit="mm")
        S.registry(session, "dim_trap", "Trap outlet length", "numeric", unit="mm")
        yield session


def _family(db, family, rows):
    """rows: [(suffix, length, trap)]; every variant is filed under the Water Closet class."""
    cat = S.category(db, class_label="Water Closet")
    for suffix, length, trap in rows:
        p = S.product(db, f"{family}-{suffix}", cat=cat)
        S.specs(db, p, klass="Water Closet", dim_length=length, dim_trap=trap)


def _ask_turn(db, parsed, *, state=None):
    r = DbResolver(db, pinned=PINNED)
    return apply_turn(state or new_state(NOW), parsed, resolver=r, now=NOW)


def _size(number, phrase=None):
    return spec(number=number, unit="mm", phrase=phrase or f"{number}mm")


def _codes(st):
    return sorted(r.key for r in st.held.refs.get("product", []))


# ------------------------------------------------------------------ a size the family holds on any axis


def test_rg2_a_size_held_on_any_axis_makes_only_those_variants_the_held_products_with_no_axis_question(db):
    _family(db, "SRTWC286", [("A", 250, 180), ("B", 600, 180), ("D", 700, 250)])
    parsed = out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="srtwc286")],
                     specs=[_size(250)]))
    st, acts = _ask_turn(db, parsed)
    assert len(acts) == 1 and isinstance(acts[0], Lookup), f"{type(acts[0]).__name__}: no Length / Trap outlet length question"
    assert _codes(st) == ["SRTWC286-A", "SRTWC286-D"], _codes(st)


def test_rg2_the_same_size_asked_as_a_refine_of_a_held_family_filters_its_variants(db):
    _family(db, "SRTWC286", [("A", 250, 180), ("B", 600, 180), ("D", 700, 250)])
    st, _ = _ask_turn(db, out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="srtwc286")])))
    st, acts = _ask_turn(db, out(ask(act="refines_held", domain="inventory", specs=[_size(250)])), state=st)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _codes(st) == ["SRTWC286-A", "SRTWC286-D"], _codes(st)


# ------------------------------------------------------------------ a size no variant holds: the sizes on file


def _miss(db, language="en", held_first=False):
    _family(db, "SRTWC290", [("A", 250, 180), ("B", 600, 180)])
    ent = [entity("product", phrase="srtwc290")]
    if held_first:
        st, _ = _ask_turn(db, out(ask(act="new_topic", domain="inventory", entities=ent), language=language))
        parsed = out(ask(act="refines_held", domain="inventory", specs=[_size(300)]), language=language)
        st, acts = _ask_turn(db, parsed, state=st)
    else:
        parsed = out(ask(act="new_topic", domain="inventory", entities=ent, specs=[_size(300)]), language=language)
        st, acts = _ask_turn(db, parsed)
    return st, acts


@pytest.mark.parametrize("held_first", [False, True], ids=["named in the ask", "family already held"])
def test_rg2_a_size_no_variant_holds_says_the_sizes_on_file_per_axis_with_no_options(db, held_first):
    st, acts = _miss(db, held_first=held_first)
    act = acts[0]
    assert isinstance(act, ClarifyAct), type(act).__name__
    assert act.pending.options == [], "no axis picks and no closest-product picks"
    reply = answer.replay_reply(st, acts)
    assert "No 300 mm for SRTWC290" in reply, reply
    assert "Sizes on file: Length 250, 600 mm; Trap outlet length 180 mm" in reply, reply


@pytest.mark.parametrize("language", ["zh", "ms"])
def test_rg2_the_sizes_on_file_line_is_localized_and_keeps_the_english_axis_names(db, language):
    st, acts = _miss(db, language=language)
    act = acts[0]
    assert isinstance(act, ClarifyAct) and act.pending.options == [], type(act).__name__
    reply = answer.replay_reply(st, acts)
    assert "SRTWC290" in reply and "300" in reply
    assert "Length 250, 600" in reply and "Trap outlet length 180" in reply, reply
    assert "No 300 mm for" not in reply and "Sizes on file" not in reply, reply


# ------------------------------------------------------------------ a class and a size, no code


def test_rg2_a_class_and_a_size_with_no_code_lists_the_class_products_holding_it_on_any_axis(db):
    _family(db, "SRTWC1", [("A", 250, 100)])
    _family(db, "SRTWC2", [("A", 400, 250)])
    _family(db, "SRTWC3", [("A", 600, 100)])
    parsed = out(ask(act="new_topic", domain="inventory", entities=[entity("product_class", key="water_closet",
                                                                               phrase="water closet")],
                     specs=[_size(250)]))
    st, acts = _ask_turn(db, parsed)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _codes(st) == ["SRTWC1-A", "SRTWC2-A"], _codes(st)


def test_rg2_a_class_and_a_size_is_capped_at_the_product_pool_limit(db):
    from app.services.chatbot.pv2.resolve import PRODUCT_POOL_LIMIT

    cat = S.category(db, class_label="Water Closet")
    for i in range(PRODUCT_POOL_LIMIT + 5):
        p = S.product(db, f"SRTWCZ{i:03d}-A", cat=cat)
        S.specs(db, p, klass="Water Closet", dim_length=250, dim_trap=100)
    parsed = out(ask(act="new_topic", domain="inventory", entities=[entity("product_class", key="water_closet",
                                                                               phrase="water closet")],
                     specs=[_size(250)]))
    st, acts = _ask_turn(db, parsed)
    assert isinstance(acts[0], Lookup) and 0 < len(_codes(st)) <= PRODUCT_POOL_LIMIT, len(_codes(st))


# ------------------------------------------------------------------ a parser flag about a phrase the ask already carries


def test_rg2_a_parser_flag_about_a_spec_phrase_the_ask_already_carries_asks_nothing():
    r = FakeResolver({
        "entities": {"product|SRTWC286": {"status": "exact", "refs": [{"kind": "product", "key": "SRTWC286-A", "label": "SRTWC286-A"}]}},
        "specs": {"|250": {"status": "exact", "specs": [{"attribute": "dim_length", "comparator": "eq", "value": 250.0, "unit": "mm"}]}},
    })
    parsed = out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="SRTWC286")],
                     specs=[_size(250, "250mm")]),
                 clarify=Clarify(needed=True, phrase="250mm", about="spec_attribute"))
    st, acts = apply_turn(new_state(NOW), parsed, resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup), f"{type(acts[0]).__name__}: the ask already carries the 250mm phrase"
