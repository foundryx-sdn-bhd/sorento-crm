"""PV2 redesign R-GAPS-b fix round 5 red tests: L1 (the staff cap line never names a raw or wrong kind and reads
naturally in en / zh / ms), L2 (an explicit add survives a pick). No model call."""
from __future__ import annotations

import re

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2.answer import reply_for
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import ReportSpec
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, out
from tests.chatbot.pv2.test_rgaps_b_fix1 import SEVENTH, _amb, _c, _ex, _p, _pick, _t

LANGS = ["en", "zh", "ms"]


def _opt(kind, key):
    return {"key": key, "label": key, "payload": {"kind": kind, "key": key, "label": key}}


def _docs(*kinds):
    return {"status": "ambiguous", "options": [_opt(k, f"{k}-123") for k in kinds]}


def _resolver(doc):
    return FakeResolver({"entities": {
        "customer|hanlim": _ex("customer", "c-hanlim", "HANLIM"),
        "sales_order|123": doc,
        "product|mou": _amb("product", ["MOU-1", "MOU-2"]),
    }})


def _cap_line(resolver, repeat, lang):
    """HANLIM held, then the same ambiguous ask three times: the staff cap line, in `lang`."""
    first = _t(_c("hanlim"), period=SEVENTH)
    st = new_state(NOW)
    st, acts = apply_turn(st, first, resolver=resolver, now=NOW, can_offer_handover=False)
    for _ in range(3):
        st, acts = apply_turn(st, repeat.model_copy(update={"language": lang}), resolver=resolver, now=NOW,
                              can_offer_handover=False)
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.options == [], type(acts[0]).__name__
    return reply_for(acts[0], lang)


def _doc_ask():
    return _t(entity("sales_order", phrase="123"))


def _without_subject(reply):
    return reply.replace("HANLIM", "")


# ------------------------------------------------------------------ L1: mixed kinds and kinds with no localized word

RAW_KINDS = ["delivery_order", "customer_po_ref", "inbound_shipment", "supplier", "attachment", "purchase_order"]


def test_l1a_options_of_more_than_one_kind_give_a_generic_which_one_line_not_the_first_kind_en():
    reply = _cap_line(_resolver(_docs("delivery_order", "sales_order")), _doc_ask(), "en")
    assert "delivery order" not in reply.lower() and "sales order" not in reply.lower(), reply
    assert "which one" in reply.lower(), reply


@pytest.mark.parametrize("lang", ["zh", "ms"])
def test_l1a_options_of_more_than_one_kind_name_no_kind_in_zh_ms(lang):
    reply = _cap_line(_resolver(_docs("delivery_order", "sales_order")), _doc_ask(), lang)
    stripped = _without_subject(reply)
    assert not re.search(r"delivery order|sales order|[a-z]_[a-z]", stripped.lower()), reply
    if lang == "zh":
        assert not re.search(r"[A-Za-z]", stripped), f"ASCII in the zh line: {reply}"


@pytest.mark.parametrize("lang", ["zh", "ms"])
@pytest.mark.parametrize("kind", RAW_KINDS)
def test_l1b_a_kind_the_line_can_name_never_shows_its_raw_name_or_an_underscore_in_zh_ms(kind, lang):
    reply = _cap_line(_resolver(_docs(kind, kind)), _doc_ask(), lang)
    stripped = _without_subject(reply)
    assert "_" not in reply and kind not in reply, reply
    assert kind.replace("_", " ") not in stripped.lower(), reply
    if lang == "zh":
        assert not re.search(r"[A-Za-z]", stripped), f"ASCII in the zh line: {reply}"


def test_l1c_the_en_line_reads_i_still_need_to_know_which_kind_you_mean_for_subject():
    reply = _cap_line(_resolver(_docs("product")), _t(_p("mou")), "en")
    assert reply == "I still need to know which product you mean for HANLIM.", reply


def test_l1d_the_zh_line_puts_the_subject_first():
    reply = _cap_line(_resolver(_docs("product")), _t(_p("mou")), "zh")
    assert reply == "关于 HANLIM，我仍需要知道您指的是哪个产品。", reply


def test_l1e_the_ms_measure_line_does_not_say_angka_untuk_disusun():
    rep = ReportSpec(dataset="sales", measures=[], group_by=["customer"], top_n_per_group=None, filters=[],
                     period=None, compare_to=None, compare_period=None, grain=None, threshold=None, sort=None,
                     top_n=5, basis="delivered")
    r = _resolver(_docs("product"))
    st, acts = apply_turn(new_state(NOW), out(ask(act="new_topic", domain="sales", report=rep), language="ms"),
                          resolver=r, now=NOW, can_offer_handover=False)
    assert st.pending is not None and st.pending.kind == "clarify_report_measure", st.pending
    for _ in range(2):
        st, acts = apply_turn(st, out(ask(act="answers_bot_question", answer={"option_key": "99", "free_value": None}),
                                      language="ms"), resolver=r, now=NOW, can_offer_handover=False)
    reply = reply_for(acts[0], "ms")
    assert "Saya masih perlu tahu angka mana untuk menyusun kedudukan" in reply, f"the cap line was not reached: {reply}"
    assert "angka untuk disusun" not in reply, reply


# ------------------------------------------------------------------ L2: an explicit add survives a pick

R2 = FakeResolver({"entities": {
    "product|SRT391-GM": _ex("product", "SRT391-GM"),
    "product|mou": _amb("product", ["MOU-1", "MOU-2"]),
}})


def _refine_then(op, pick_key):
    st = new_state(NOW)
    held = out(ask(act="new_topic", domain="order", entities=[_p("SRT391-GM")], period=SEVENTH))
    mou = entity("product", phrase="mou").model_copy(update={"op": op})
    for parsed in (held, out(ask(act="refines_held", domain="order", entities=[mou])), _pick(pick_key)):
        st, acts = apply_turn(st, parsed, resolver=R2, now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    return sorted(r.key for r in acts[0].held.refs.get("product", []))


def test_l2_an_explicit_add_then_a_single_pick_keeps_the_held_product_and_adds_the_pick():
    assert _refine_then("add", "1") == ["MOU-1", "SRT391-GM"]


def test_l2_an_explicit_add_then_all_keeps_the_held_product_and_adds_every_option():
    assert _refine_then("add", "all") == ["MOU-1", "MOU-2", "SRT391-GM"]


def test_l2_guard_a_plain_refine_still_replaces_for_a_single_pick_and_for_all():
    assert _refine_then("set", "1") == ["MOU-1"]
    assert _refine_then("set", "all") == ["MOU-1", "MOU-2"]
