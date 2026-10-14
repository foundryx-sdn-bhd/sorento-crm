"""PV2 redesign R-GAPS-a fix round 1 red tests (RG-7f, RG-7g). Real `apply_turn` paths with the pure fakes, no model call.

F1 a replayed remove never re-misses   F2 every option question keeps the others   F3 count after ETA, no attachment kind
F4 sorento + mocha on a brand-filtered report (green guard)   F5 the raiser is matched on identity
"""
from __future__ import annotations

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.history import MemoryProfile, render_memory
from app.services.chatbot.pv2.schema import Filter, Period, Quantity, ReportSpec
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, out
from tests.chatbot.pv2.test_rgaps_a import _amb, _exact, _keys, _pick, _q, _qty

OFFER = pytest.mark.parametrize("can_offer", [False, True], ids=["staff", "non-staff"])


def _pick_key(act, key):
    """The option key whose payload (or label) names `key`."""
    for o in act.pending.options:
        if o.payload.get("key") == key or o.label == key:
            return o.key
    raise AssertionError(f"no option for {key}: {[(o.key, o.label) for o in act.pending.options]}")


# ------------------------------------------------------------------ F1: a replayed remove never re-misses (RG-7f)

CUSTOMERS = FakeResolver({"entities": {f"customer|{c}": _exact("customer", c) for c in ("Z", "Y", "W")}})


def _rm(name):
    return entity("customer", phrase=name, op="remove")


THIS_SO = entity("order", refers_to_held=True)


def _asked(held_customers, ents, act="refines_held", can_offer=False):
    st = mk_state(mk_held("order", {"customer": held_customers, "product": ["A1"]}))
    st, acts = apply_turn(st, out(ask(act=act, domain="order", entities=ents)), resolver=CUSTOMERS, now=NOW,
                          can_offer_handover=can_offer)
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_which_held", acts[0]
    return st, acts[0]


@OFFER
def test_rg7f_a_pick_after_remove_customer_z_and_this_so_reaches_a_lookup_with_the_picked_customer(can_offer):
    st, q = _asked(["C1", "Z"], [_rm("Z"), THIS_SO], can_offer=can_offer)
    st, acts = apply_turn(st, _pick(_pick_key(q, "C1")), resolver=CUSTOMERS, now=NOW, can_offer_handover=can_offer)
    assert isinstance(acts[0], Lookup), f"{type(acts[0]).__name__}: a pick must never re-ask or offer a hand-over"
    assert _keys(st, "customer") == ["C1"]


@OFFER
@pytest.mark.parametrize("picked", ["Z", "Y"])
def test_rg7f_two_removes_and_this_so_reach_a_lookup_on_either_pick(can_offer, picked):
    st, q = _asked(["Z", "Y"], [_rm("Z"), _rm("Y"), THIS_SO], can_offer=can_offer)
    st, acts = apply_turn(st, _pick(_pick_key(q, picked)), resolver=CUSTOMERS, now=NOW, can_offer_handover=can_offer)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__


@OFFER
def test_rg7f_a_remove_that_raised_the_question_beside_a_second_remove_reaches_a_lookup(can_offer):
    st, q = _asked(["C1", "Z"], [_rm("W"), _rm("Z")], can_offer=can_offer)
    st, acts = apply_turn(st, _pick(_pick_key(q, "C1")), resolver=CUSTOMERS, now=NOW, can_offer_handover=can_offer)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert _keys(st, "customer") == ["C1"]


@OFFER
def test_rg7f_guard_the_new_topic_raise_site_reaches_a_lookup_on_the_first_pick(can_offer):
    st, q = _asked(["C1", "Z"], [_rm("Z")], act="new_topic", can_offer=can_offer)
    st, acts = apply_turn(st, _pick(_pick_key(q, "C1")), resolver=CUSTOMERS, now=NOW, can_offer_handover=can_offer)
    assert isinstance(acts[0], Lookup)


# ------------------------------------------------------------------ F5: the raiser is matched on identity, not a whole dump


@pytest.mark.parametrize("edit", ["none", "extra_key", "missing_optional"])
def test_rg7f_a_stored_raiser_dump_from_another_entity_shape_still_matches_its_entity(edit):
    st, q = _asked(["C1", "Z"], [_rm("W")])
    raised = dict(st.pending.raised_by)
    assert raised, "the question records the entity that raised it"
    if edit == "none":
        pass  # green guard: the dump as written today matches
    elif edit == "extra_key":
        raised["a_field_added_later"] = None
    else:
        raised.pop("doc_number", None)
    st = st.model_copy(update={"pending": st.pending.model_copy(update={"raised_by": raised})})
    st, acts = apply_turn(st, _pick(_pick_key(q, "C1")), resolver=CUSTOMERS, now=NOW)
    assert isinstance(acts[0], Lookup), "an older or newer raiser dump must not make the pick loop"


# ------------------------------------------------------------------ F2: every option question keeps the other items (RG-7g)


class _Resolver(FakeResolver):
    """FakeResolver plus the did_you_mean flag a config entry may carry."""

    def resolve_entities(self, ents, *, held):
        res = super().resolve_entities(ents, held=held)
        table = self.config.get("entities", {})
        for e, r in zip(ents, res):
            r.did_you_mean = bool((table.get(f"{e.kind}|{e.key if e.key is not None else e.phrase}") or {}).get("did_you_mean"))
        return res


OPTION_ITEMS = {"entities": {
    "product|mou": _amb("product", ("SRTWC286", "SRTWC287")),
    "product|srtwc28": {**_amb("product", ("SRTWC288", "SRTWC289")), "did_you_mean": True},
    "product|zzz": {"status": "none", "options": [
        {"key": "ZZ1", "label": "ZZ1", "payload": {"kind": "product", "key": "ZZ1", "label": "ZZ1"}},
        {"key": "ZZ2", "label": "ZZ2", "payload": {"kind": "product", "key": "ZZ2", "label": "ZZ2"}}]},
}}


def _play_picks(domain, ents, wanted):
    """Ask, then answer every question with the option for the next wanted code; returns (state, last action)."""
    r = _Resolver(OPTION_ITEMS)
    st, acts = apply_turn(new_state(NOW), out(ask(act="new_topic", domain=domain, entities=ents)), resolver=r, now=NOW)
    for code in wanted:
        assert isinstance(acts[0], ClarifyAct), f"expected a question for {code}, got {type(acts[0]).__name__}; held {_keys(st)}"
        st, acts = apply_turn(st, _pick(_pick_key(acts[0], code)), resolver=r, now=NOW)
    return st, acts[0]


@pytest.mark.parametrize("domain", ["incoming", "order"])
@pytest.mark.parametrize("name,ents,wanted,expect", [
    ("did-you-mean", [("mou", 3), ("srtwc28", 2)], ["SRTWC286", "SRTWC288"], {"SRTWC286": 3, "SRTWC288": 2}),
    ("closest after ambiguous", [("mou", 3), ("zzz", 2)], ["SRTWC286", "ZZ1"], {"SRTWC286": 3, "ZZ1": 2}),
    ("closest before ambiguous", [("zzz", 2), ("mou", 3)], ["ZZ1", "SRTWC286"], {"ZZ1": 2, "SRTWC286": 3}),
])
def test_rg7g_a_pick_on_a_did_you_mean_or_closest_question_keeps_the_other_items(domain, name, ents, wanted, expect):
    st, last = _play_picks(domain, [_q(entity("product", phrase=p), n) for p, n in ents], wanted)
    assert isinstance(last, Lookup), f"{name}: {type(last).__name__}"
    assert _qty(st) == expect


# ------------------------------------------------------------------ F3: a count after an ETA answer; no stray attachment kind


def _memory(st):
    return render_memory(st, MemoryProfile(audience="staff"), NOW)


@pytest.mark.parametrize("shape", ["B", "E"])
def test_rg7g_guard_a_count_after_an_eta_answer_is_a_stock_ask_with_that_count(shape):
    r = FakeResolver({"entities": {"product|SRTWC286": _exact("product", "SRTWC286")}})
    eta = out(ask(act="new_topic", domain="incoming", entities=[_q(entity("product", phrase="SRTWC286"), 50)]))
    count = _q(entity("product", refers_to_held=(shape == "B")), 10)
    st, acts = apply_turn(new_state(NOW), eta, resolver=r, now=NOW)
    st, acts = apply_turn(st, out(ask(act="refines_held", domain=None, entities=[count])), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup) and acts[0].domain == "inventory"
    assert _qty(st) == {"SRTWC286": 10}


@pytest.mark.parametrize("shape", ["B", "E"])
def test_rg7g_a_count_after_a_photo_reshapes_to_inventory_and_drops_the_attachment_kind_from_held_and_memory(shape):
    r = FakeResolver({"entities": {"product|SRTWT5880-CR": _exact("product", "SRTWT5880-CR")},
                      "attachment_types": {"picture": _exact("attachment_type", "picture")}})
    photo = out(ask(act="new_topic", domain="product_attachment", attachment_type="picture",
                    entities=[entity("product", phrase="SRTWT5880-CR")]))
    count = _q(entity("product", refers_to_held=(shape == "B")), 10)
    st, acts = apply_turn(new_state(NOW), photo, resolver=r, now=NOW)
    st, acts = apply_turn(st, out(ask(act="refines_held", domain=None, entities=[count])), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup) and st.held.domain == "inventory"
    assert not st.held.attachment_type and not st.held.attachment_types
    assert "attachment_type" not in _memory(st).split("PENDING QUESTION")[0]


# ------------------------------------------------------------------ F4: sorento and mocha on a held brand-filtered report (green guard)

BR = {"entities": {"brand|sorento": _exact("brand", "sorento", "Sorento"), "brand|mocha": _exact("brand", "mocha", "Mocha")}}


def _brand_report():
    return ReportSpec(dataset=None, measures=["revenue"], group_by=["product"], top_n_per_group=None,
                      filters=[Filter(dimension="brand", op="in", keys=["toto"], values=[])],
                      period=Period(preset="last_month", n=None, date_from=None, date_to=None), compare_to=None,
                      compare_period=None, grain=None, threshold=None, sort=None, top_n=5, basis=None)


def test_rg7g_guard_sorento_and_mocha_refine_a_brand_filtered_report_keeps_both_brands():
    h = mk_held("sales")
    h.report = _brand_report()
    parsed = out(ask(act="refines_held", domain="sales",
                     entities=[entity("brand", phrase="sorento"), entity("brand", phrase="mocha")]))
    st, acts = apply_turn(mk_state(h), parsed, resolver=FakeResolver(BR), now=NOW)
    keys = [k for f in st.held.report.filters if f.dimension == "brand" for k in f.keys]
    assert keys == ["sorento", "mocha"]
