"""PV2 redesign R-GAPS-b nits round red tests: N1 (en cap line grammar), N2 / N3 (natural ms cap lines), N4 (an explicit
add survives a did-you-mean pick). No model call."""
from __future__ import annotations

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2 import clarify_copy
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state
from tests.chatbot.pv2.carry_fakes import NOW, ask, entity, out
from tests.chatbot.pv2.test_fix_round2 import _Fake
from tests.chatbot.pv2.test_rgaps_b_fix1 import SEVENTH, _pick, _p, _t
from tests.chatbot.pv2.test_rgaps_b_fix5 import _cap_line, _doc_ask, _docs, _resolver, ReportSpec, reply_for

REPORT = ReportSpec(dataset="sales", measures=[], group_by=["customer"], top_n_per_group=None, filters=[], period=None,
                    compare_to=None, compare_period=None, grain=None, threshold=None, sort=None, top_n=5,
                    basis="delivered")


def _junk_picks(lang, n):
    """A report measure question, then `n` unreadable answers: the last reply."""
    r = _resolver(_docs("product"))
    st, acts = apply_turn(new_state(NOW), out(ask(act="new_topic", domain="sales", report=REPORT), language=lang),
                          resolver=r, now=NOW, can_offer_handover=False)
    assert st.pending is not None and st.pending.kind == "clarify_report_measure", st.pending
    for _ in range(n):
        st, acts = apply_turn(st, out(ask(act="answers_bot_question", answer={"option_key": "99", "free_value": None}),
                                      language=lang), resolver=r, now=NOW, can_offer_handover=False)
    return reply_for(acts[0], lang)


def test_n1_the_en_what_to_check_cap_line_reads_i_still_need_to_know_what_to_check():
    reply = _junk_picks("en", 4)
    assert reply.startswith("I still need to know what to check"), reply


def test_n2_the_ms_kind_line_has_no_yang_doubling():
    reply = _cap_line(_resolver(_docs("product")), _t(_p("mou")), "ms")
    assert reply == "Saya masih perlu tahu produk mana satu yang anda maksudkan untuk HANLIM.", reply


def test_n2_the_ms_generic_line_has_no_yang_doubling():
    reply = _cap_line(_resolver(_docs("delivery_order", "sales_order")), _doc_ask(), "ms")
    assert reply == "Saya masih perlu tahu mana satu yang anda maksudkan.", reply


def test_n3_the_ms_measure_cap_line_does_not_say_ukuran_kedudukan():
    reply = _junk_picks("ms", 2)
    assert "Saya masih perlu" in reply, f"the cap line was not reached: {reply}"
    assert "ukuran kedudukan" not in reply, reply


def test_n3_the_ms_measure_line_with_a_subject_reads_angka_mana_untuk_menyusun_kedudukan():
    assert clarify_copy.still_need("measure", "HANLIM", "ms") == \
        "Saya masih perlu tahu angka mana untuk menyusun kedudukan bagi HANLIM."


# ------------------------------------------------------------------ N4: an explicit add survives a did-you-mean pick

TABLE = {
    "product|SRT391-GM": ("exact", [("product", "SRT391-GM")], False),
    "product|srtwc28": ("ambiguous", [("product", "SRTWC286")], True),
}


def _typo_then_pick(op):
    r = _Fake(TABLE)
    st = new_state(NOW)
    typo = entity("product", phrase="srtwc28").model_copy(update={"op": op})
    for parsed in (out(ask(act="new_topic", domain="inventory", entities=[_p("SRT391-GM")])),
                   out(ask(act="refines_held", domain="inventory", entities=[typo]))):
        st, acts = apply_turn(st, parsed, resolver=r, now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_did_you_mean", type(acts[0]).__name__
    st, acts = apply_turn(st, _pick("1"), resolver=r, now=NOW, can_offer_handover=False)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    return sorted(x.key for x in acts[0].held.refs.get("product", []))


def test_n4_an_explicit_add_whose_typo_becomes_a_did_you_mean_keeps_the_held_product_after_the_pick():
    assert _typo_then_pick("add") == ["SRT391-GM", "SRTWC286"]


def test_n4_guard_a_plain_did_you_mean_still_replaces():
    assert _typo_then_pick("set") == ["SRTWC286"]
