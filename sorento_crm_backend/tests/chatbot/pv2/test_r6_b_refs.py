"""PV2 R6 slice B (plan behaviour 3): follow-up references read a document's LINES, not its number. Red tests, no LLM.

The live parser, for 'what about this product?' / 'and the others?' / 'the second one', often emits NO entity at all: act
unclear or refines_held, `clarify {needed: true, about: "which_held"}`. Every test here starts from that shape. Fresh
paraphrases: 'is that item in stock?', 'how about both of them?', 'the 150 one please'.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.pv2 import register
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, RegisterEntry
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, held as mk_held, mk_state, option, out, pending

SO = "SO419122"


def _which_held(act="unclear", **kw):
    return out(ask(act=act, **kw), clarify={"needed": True, "about": "which_held", "phrase": None})


def _labels(st):
    return [o.label for o in st.pending.options] if st.pending else []


def _so_answer(lines, via):
    """The last answer printed one sales order; its product lines are known to the register."""
    st = mk_state(mk_held("order", {"sales_order": [SO]}), turn_no=3)
    entries = [RegisterEntry(id="R1", kind="sales_order", key=SO, label=SO, parent=None, subject=None, turn=3)]
    if via == "children":
        entries += [RegisterEntry(id=f"R{n}", kind="product", key=c, label=c, parent="R1", subject=None, turn=3)
                    for n, c in enumerate(lines, start=2)]
    else:
        st.doc_lines = {SO: list(lines)}
    st.register = entries
    st.last_rows = register.shown_rows(st.register, 3)
    return st


# ------------------------------------------------------------------ "this product" with no entity in the parse


@pytest.mark.parametrize("via", ["children", "doc_lines"])
@pytest.mark.parametrize("act", ["unclear", "refines_held"])
def test_this_product_with_no_entity_after_a_multi_line_so_offers_the_line_products_never_the_document(via, act):
    """RED: HEAD offers the held ref (the SO number) as the choice for a product reference."""
    st0 = _so_answer(["SRTWB1096", "SRTWT7203"], via)
    kw = {"domain": "inventory"} if act == "refines_held" else {}
    st, acts = apply_turn(st0, _which_held(act, **kw), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    assert sorted(_labels(st)) == ["SRTWB1096", "SRTWT7203"], _labels(st)
    assert SO not in _labels(st)


@pytest.mark.parametrize("via", ["children", "doc_lines"])
def test_that_one_with_no_entity_after_a_one_line_so_is_that_lines_product_not_the_document(via):
    """RED: one line is that product (a Lookup on it) or at worst a question listing it; never the SO number."""
    st0 = _so_answer(["SRTW5000"], via)
    r = FakeResolver({"entities": {"product|SRTW5000": {"status": "exact", "refs": [
        {"kind": "product", "key": "SRTW5000", "label": "SRTW5000"}]}}})
    st, acts = apply_turn(st0, _which_held("unclear"), resolver=r, now=NOW)
    if isinstance(acts[0], Lookup):
        assert [x.key for x in st.held.refs.get("product", [])] == ["SRTW5000"]
    else:
        assert _labels(st) == ["SRTW5000"], _labels(st)
    assert SO not in _labels(st)


# ------------------------------------------------------------------ "both" / "the others" read the earlier products


def _two_products_discussed():
    """SRTWC286 was asked about first (a saved topic); SRTWT7203 is held now."""
    st = mk_state(mk_held("inventory", {"product": ["SRTWT7203"]}), topics=[mk_held("inventory", {"product": ["SRTWC286"]})])
    return st


@pytest.mark.parametrize("act", ["unclear", "refines_held"])
def test_both_with_no_entity_offers_the_earlier_product_from_topics_beside_the_held_one(act):
    """RED: HEAD reads only held's refs, so 'both of them' never reaches the product in the saved topic."""
    kw = {"domain": "inventory"} if act == "refines_held" else {}
    st, acts = apply_turn(_two_products_discussed(), _which_held(act, **kw), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct), type(acts[0]).__name__
    assert {"SRTWC286", "SRTWT7203"} <= set(_labels(st)), _labels(st)


def test_the_others_with_no_entity_offers_the_products_of_the_saved_topics():
    """RED: 'show me the others' - the held product is not the other one, the saved topic's is."""
    st, acts = apply_turn(_two_products_discussed(), _which_held("unclear"), resolver=FakeResolver({}), now=NOW)
    assert "SRTWC286" in _labels(st), _labels(st)


# ------------------------------------------------------------------ "the X one" picks from the menu just answered


def test_the_150_one_after_a_menu_was_answered_offers_that_menus_other_options():
    """RED: turn 1 answers a three-product menu with option 1; turn 2 ('the 150 one') parses with no entity and a
    which_held flag. HEAD offers only the held (picked) product; the menu just answered is not read."""
    codes = ["SRTWC286-SH", "SRTWC286-SH-150", "SRTWC286-SH-200"]
    opts = [option(str(n), c, {"kind": "product", "key": c, "label": c, "op": "set"}) for n, c in enumerate(codes, 1)]
    st0 = mk_state(mk_held("inventory"), pending_=pending("clarify_entity", opts, asked="Which product?"))
    st1, acts1 = apply_turn(st0, out(ask(act="answers_bot_question", answer={"option_key": "1", "free_value": None})),
                            resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts1[0], Lookup) and [x.key for x in st1.held.refs["product"]] == [codes[0]]
    st2, acts2 = apply_turn(st1, _which_held("unclear"), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts2[0], ClarifyAct)
    assert set(codes[1:]) <= set(_labels(st2)), _labels(st2)
