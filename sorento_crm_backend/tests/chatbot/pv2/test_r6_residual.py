"""PV2 R6 residual (contract test b1498993b9, invariant I2): an entity question asked with no options of its own
(a not-found product for a contact with no hand-over offer, the which-product question) offers what the reference
can mean (`_referents` of that kind: the rows the last answer printed, held, saved topics) whenever there is any.

Hand-written live-shape parses, fresh paraphrases ('the grey ones as well', 'any in the warehouse?'); the bot's own
printed lines are read back, never the user's message.
"""
from __future__ import annotations

import re

import pytest

from app.services.chatbot.pv2 import answer
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, Option
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, out

NUMBERED = re.compile(r"^(\d+)\. (.+)$", re.M)


def _rows(*codes):
    return [Option(key=str(n), label=c, payload={"kind": "row", "row_kind": "product", "of": len(codes)})
            for n, c in enumerate(codes, start=1)]


def _printed(acts, state):
    return [(int(n), label.strip()) for n, label in NUMBERED.findall(answer.replay_reply(state, acts))]


def _stored(st):
    return [(n, o.label) for n, o in enumerate(st.pending.options, start=1)] if st.pending else []


def test_a_product_the_resolver_cannot_find_offers_the_products_the_last_answer_printed():
    """RED: 'the grey ones as well' after a stock list of two codes, read as a product phrase the data does not hold.
    A staff contact (no hand-over offer) gets 'Couldn't find ...' with zero options today."""
    st0 = mk_state(mk_held("inventory", {}), last_rows=_rows("ZQTB4410-GR", "ZQTB4410-WH"))
    parsed = out(ask(act="refines_held", entities=[entity("product", phrase="the grey ones as well")]))
    st, acts = apply_turn(st0, parsed, resolver=FakeResolver({}), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], ClarifyAct) and acts[0].not_found, acts
    assert [o.label for o in st.pending.options] == ["ZQTB4410-GR", "ZQTB4410-WH"], st.pending
    assert _printed(acts, st) == _stored(st)


def test_picking_one_of_those_settles_the_missing_product_and_looks_it_up():
    st0 = mk_state(mk_held("inventory", {}), last_rows=_rows("ZQTB4410-GR", "ZQTB4410-WH"))
    parsed = out(ask(act="refines_held", entities=[entity("product", phrase="the grey ones as well")]))
    r = FakeResolver({})
    st1, _ = apply_turn(st0, parsed, resolver=r, now=NOW, can_offer_handover=False)
    st2, acts = apply_turn(st1, out(ask(act="answers_bot_question", answer={"option_key": "1", "free_value": None})),
                           resolver=r, now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], Lookup), acts
    assert [x.label for x in st2.held.refs.get("product", [])] == ["ZQTB4410-GR"]


def test_the_which_product_question_offers_the_products_the_last_answer_printed():
    """RED: 'any in the warehouse?' with no product held, after an answer that printed two products."""
    st0 = mk_state(mk_held("master_products", {}), last_rows=_rows("ZQTB4410-GR", "ZQTB4410-WH"))
    st, acts = apply_turn(st0, out(ask(act="refines_held", domain="inventory")), resolver=FakeResolver({}), now=NOW)
    assert isinstance(acts[0], ClarifyAct), acts
    assert [o.label for o in st.pending.options] == ["ZQTB4410-GR", "ZQTB4410-WH"], st.pending
    assert _printed(acts, st) == _stored(st)


def test_guard_with_nothing_to_offer_the_not_found_line_stays_option_less():  # guard: passes today
    st, acts = apply_turn(mk_state(mk_held("inventory", {})),
                          out(ask(act="refines_held", entities=[entity("product", phrase="ZQTB9999")])),
                          resolver=FakeResolver({}), now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], ClarifyAct) and acts[0].not_found and not st.pending.options


def test_guard_a_contact_offered_the_hand_over_keeps_its_yes_no_offer():  # guard: passes today
    st0 = mk_state(mk_held("inventory", {}), last_rows=_rows("ZQTB4410-GR", "ZQTB4410-WH"))
    parsed = out(ask(act="refines_held", entities=[entity("product", phrase="the grey ones as well")]))
    st, acts = apply_turn(st0, parsed, resolver=FakeResolver({}), now=NOW, can_offer_handover=True)
    assert st.pending.kind == "offer_doc_handover" and [o.key for o in st.pending.options] == ["yes", "no"]


# ------------------------------------------------------------------ a number on a confirm is its stored option (lead ruling)

GROUP = "ZEBRA HOMEWARE SDN BHD"
Z1, Z2 = "CUST-ZEB-1", "CUST-ZEB-2"


def _zebra_confirm():
    """'zebra deliveries this week?': the group named partly, so the bot asked 'Did you mean ZEBRA HOMEWARE SDN BHD?'
    (the group name only; its yes covers both ledger accounts)."""
    from tests.chatbot.pv2.carry_fakes import option, pending

    yes = option("yes", GROUP, {"kind": "customer", "key": Z1, "keys": [Z1, Z2], "label": GROUP, "op": "set",
                                "labels": [f"{GROUP} [A/C I]", f"{GROUP} [A/C II]"]})
    no = option("no", "No", {"value": False})
    return mk_state(mk_held("order", status="outstanding"),
                    pending_=pending("clarify_confirm", [yes, no], asked=f"Did you mean {GROUP}?"))


@pytest.mark.parametrize("answer_kw", [
    {"row_pick": 2},
    {"answer": {"option_key": None, "free_value": "2"}},
    {"answer": {"option_key": "2", "free_value": None}},
], ids=["row-pick", "free-value", "option-key"])
def test_a_2_on_a_confirm_is_its_stored_second_option_no_never_the_second_account(answer_kw):
    """RED: '2' to the group-name confirm. MEMORY numbers its stored options 1=yes 2=no, so 2 is the no (asks for the
    full name), never account II, which HEAD picks."""
    st, acts = apply_turn(_zebra_confirm(), out(ask(act="answers_bot_question", **answer_kw)), resolver=FakeResolver({}),
                          now=NOW)
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_entity", acts
    assert not st.held.refs.get("customer"), st.held.refs


def test_a_1_on_a_confirm_is_its_yes_the_whole_group():
    st, acts = apply_turn(_zebra_confirm(), out(ask(act="answers_bot_question", row_pick=1)), resolver=FakeResolver({}),
                          now=NOW)
    assert isinstance(acts[0], Lookup) and [r.key for r in st.held.refs["customer"]] == [Z1, Z2]


def test_guard_naming_one_account_on_the_confirm_still_picks_that_account():  # guard: passes today
    r = FakeResolver({"entities": {"customer|A/C II": {"status": "exact", "refs": [
        {"kind": "customer", "key": Z2, "label": f"{GROUP} [A/C II]"}]}}})
    parsed = out(ask(act="answers_bot_question", entities=[entity("customer", phrase="A/C II")]))
    st, acts = apply_turn(_zebra_confirm(), parsed, resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup) and [x.key for x in st.held.refs["customer"]] == [Z2]
