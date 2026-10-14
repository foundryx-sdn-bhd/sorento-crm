"""PV2 redesign R-GAPS-a red tests (RG-3, RG-4, RG-4b, RG-7 part 2, RG-7d, RG-7b, RG-7c, RG-8). No model call.

Every parse below is handwritten from a recorded shape. The tests drive the real `apply_turn` (and the bridge) with the
pure fakes; they assert behaviour (actions, held, the verdict), never a private helper.
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.chatbot.pv2 import _register_envelopes as E  # noqa: F401 - loads the worktree MCP first

from app.services.chatbot.pv2 import bridge
from app.services.chatbot.pv2.apply import apply_turn
from app.services.chatbot.pv2.schema import Filter, Period, Quantity, ReportSpec
from app.services.chatbot.pv2.state import ClarifyAct, Lookup, new_state
from tests.chatbot.pv2.carry_fakes import NOW, FakeResolver, ask, entity, held as mk_held, mk_state, out

TODAY = date(2026, 10, 14)


def _exact(kind, key, label=None):
    return {"status": "exact", "refs": [{"kind": kind, "key": key, "label": label or key}]}


def _amb(kind, keys):
    return {"status": "ambiguous", "options": [
        {"key": k, "label": k, "payload": {"kind": kind, "key": k, "label": k}} for k in keys]}


def _pick(key="1"):
    return out(ask(act="answers_bot_question", answer={"option_key": key, "free_value": None}))


def _q(e, n):
    return e.model_copy(update={"quantity": Quantity(number=n, unit=None)})


def _keys(st, kind="product"):
    return [r.key for r in st.held.refs.get(kind, [])]


def _qty(st):
    return {r.key: (r.quantity.number if r.quantity else None) for r in st.held.refs.get("product", [])}


def _play(resolver, *parsed, state=None):
    st, acts = state or new_state(NOW), []
    for p in parsed:
        st, acts = apply_turn(st, p, resolver=resolver, now=NOW)
    return st, acts


# ------------------------------------------------------------------ RG-4 / RG-4b: a bare count after a photo (#1617)

PHOTO = {"entities": {"product|SRTWT5880-CR": _exact("product", "SRTWT5880-CR")},
         "attachment_types": {"picture": _exact("attachment_type", "picture")}}


def _photo_turn():
    return out(ask(act="new_topic", domain="product_attachment", attachment_type="picture",
                   entities=[entity("product", phrase="SRTWT5880-CR")]))


def _count(refers=False):
    return _q(entity("product", refers_to_held=refers), 10)


COUNT_SHAPES = {
    "A": ask(act="refines_held", domain="inventory", entities=[_count(True)]),
    "B": ask(act="refines_held", domain=None, entities=[_count(True)]),
    "C": ask(act="new_topic", domain="inventory", entities=[_count(True)]),
    "D": ask(act="refines_held", domain="inventory", entities=[_count(False)]),
    "E": ask(act="refines_held", domain=None, entities=[_count(False)]),
    "F": ask(act="new_topic", domain=None, entities=[_count(True)]),
    "G": ask(act="answers_bot_question", domain=None, answer={"option_key": None, "free_value": None},
             entities=[_count(True)]),
    "H": ask(act="new_topic", domain="inventory", entities=[_count(False)]),
}


def _bridged_kinds(st, act):
    v = bridge.to_v1_verdict(st, act, today=TODAY)
    return v, [e.get("hint") or e.get("kind") for e in v["entities"]]


@pytest.mark.parametrize("shape", list("ACD"))
def test_rg4_guard_the_shapes_already_right_reach_an_inventory_lookup_with_the_count(shape):
    st, acts = _play(FakeResolver(PHOTO), _photo_turn(), out(COUNT_SHAPES[shape]))
    assert len(acts) == 1 and isinstance(acts[0], Lookup)
    assert acts[0].domain == "inventory" and _qty(st) == {"SRTWT5880-CR": 10}


@pytest.mark.parametrize("shape", list("BEFGH"))
def test_rg4_a_bare_count_after_a_photo_is_a_stock_ask_for_the_held_product(shape):
    st, acts = _play(FakeResolver(PHOTO), _photo_turn(), out(COUNT_SHAPES[shape]))
    assert len(acts) == 1 and isinstance(acts[0], Lookup), f"shape {shape}: {type(acts[0]).__name__}, no question"
    assert acts[0].domain == "inventory" and st.held.domain == "inventory"
    assert _keys(st) == ["SRTWT5880-CR"] and _qty(st) == {"SRTWT5880-CR": 10}
    v, kinds = _bridged_kinds(st, acts[0])
    assert v["domain_hint"] == "inventory"
    assert "attachment_type" not in kinds and "attachment" not in kinds, kinds


@pytest.mark.parametrize("shape", list("ABCDEFGH"))
def test_rg4_the_stock_verdict_for_a_count_after_a_photo_bridges_no_attachment_kind(shape):
    st, acts = _play(FakeResolver(PHOTO), _photo_turn(), out(COUNT_SHAPES[shape]))
    assert isinstance(acts[0], Lookup), f"shape {shape} asked a question"
    v, kinds = _bridged_kinds(st, acts[0])
    assert v["domain_hint"] == "inventory"
    assert "attachment_type" not in kinds and "attachment" not in kinds, kinds


def test_rg4_a_count_after_a_stock_answer_stays_a_stock_ask():
    r = FakeResolver({"entities": {"product|SRTWT5880-CR": _exact("product", "SRTWT5880-CR")}})
    first = out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="SRTWT5880-CR")]))
    for shape in "BE":
        st, acts = _play(r, first, out(COUNT_SHAPES[shape]))
        assert isinstance(acts[0], Lookup) and acts[0].domain == "inventory"
        assert _qty(st) == {"SRTWT5880-CR": 10}


# ------------------------------------------------------------------ RG-7 part 2 / RG-7d: a new report is a new subject (#1572)

BRANDS = {"entities": {"product|CBDR4003": _exact("product", "CBDR4003"),
                       "brand|sorento": _exact("brand", "sorento", "Sorento"),
                       "brand|mocha": _exact("brand", "mocha", "Mocha")}}


def _top_sales():
    return ReportSpec(dataset=None, measures=["revenue"], group_by=["product"], top_n_per_group=None, filters=[],
                      period=None, compare_to=None, compare_period=None, grain=None, threshold=None, sort=None,
                      top_n=10, basis=None)


def _stock_turn():
    return out(ask(act="new_topic", domain="inventory", entities=[entity("product", phrase="CBDR4003")]))


def _report_turn(act, *, refers=False):
    ents = [entity("brand", phrase="sorento"), entity("brand", phrase="mocha")]
    if refers:
        ents.insert(0, entity("product", phrase="CBDR4003", refers_to_held=True))
    return out(ask(act=act, domain="sales", report=_top_sales(), entities=ents))


def _brand_filter_keys(st):
    f = [f for f in (st.held.report.filters if st.held.report else []) if f.dimension == "brand"]
    return f[0].keys if f else []


def _topic_products(st):
    return [[r.key for r in t.refs.get("product", [])] for t in st.topics]


def test_rg7_a_refine_that_opens_a_report_leaves_the_held_product_on_a_topic():
    st, acts = _play(FakeResolver(BRANDS), _stock_turn(), _report_turn("refines_held"))
    assert st.held.domain == "sales" and st.held.report is not None
    assert _keys(st) == [], "CBDR4003 belongs to the stock topic, not to the new report"
    assert ["CBDR4003"] in _topic_products(st)


def test_rg7_a_refine_that_opens_a_report_filters_it_by_the_named_brands():
    st, acts = _play(FakeResolver(BRANDS), _stock_turn(), _report_turn("refines_held"))
    assert _brand_filter_keys(st) == ["sorento", "mocha"]


def test_rg7_guard_a_new_topic_report_does_not_hold_the_earlier_product():
    st, acts = _play(FakeResolver(BRANDS), _stock_turn(), _report_turn("new_topic"))
    assert st.held.domain == "sales" and _keys(st) == []
    assert ["CBDR4003"] in _topic_products(st)


def test_rg7_a_refine_that_names_the_held_product_still_carries_it():
    st, acts = _play(FakeResolver(BRANDS), _stock_turn(), _report_turn("refines_held", refers=True))
    assert _keys(st) == ["CBDR4003"]


# ------------------------------------------------------------------ RG-7c: a which_held pick keeps a remove of a held item


def _remove_z_and_this_so():
    r = FakeResolver({"entities": {"customer|Z": _exact("customer", "Z")}})
    st = mk_state(mk_held("order", {"customer": ["Z"], "product": ["A1"]}))
    parsed = out(ask(act="refines_held", domain="order", entities=[
        entity("customer", phrase="Z", op="remove"), entity("order", refers_to_held=True)]))
    return st, r, parsed


def test_rg7c_remove_customer_z_and_this_so_asks_which_held():
    st, r, parsed = _remove_z_and_this_so()
    st, acts = apply_turn(st, parsed, resolver=r, now=NOW)
    assert isinstance(acts[0], ClarifyAct) and acts[0].pending.kind == "clarify_which_held"
    assert len(acts[0].pending.options) >= 2


@pytest.mark.parametrize("pick", ["1", "2"])
def test_rg7c_either_pick_still_removes_customer_z(pick):
    st, r, parsed = _remove_z_and_this_so()
    st, acts = apply_turn(st, parsed, resolver=r, now=NOW)
    st, acts = apply_turn(st, _pick(pick), resolver=r, now=NOW)
    assert isinstance(acts[0], Lookup), type(acts[0]).__name__
    assert "Z" not in _keys(st, "customer"), "the remove of Z was part of the ask and must survive the pick"


# ------------------------------------------------------------------ RG-7b: a same-kind pick keeps the other items

MOU = _amb("product", ("SRTWC286", "SRTWC287"))
MOC = _amb("product", ("MOC-1", "MOC-2"))
ITEMS = {"entities": {"product|A1": _exact("product", "A1"), "product|mou": MOU, "product|moc": MOC}}


def _a1_and_mou(domain):
    return out(ask(act="new_topic", domain=domain,
                   entities=[_q(entity("product", phrase="A1"), 5), _q(entity("product", phrase="mou"), 3)]))


@pytest.mark.parametrize("domain", ["incoming", "order"])
def test_rg7b_a_pick_for_mou_keeps_a1_x5_and_adds_the_pick_x3(domain):
    r = FakeResolver(ITEMS)
    st, acts = _play(r, _a1_and_mou(domain))
    assert isinstance(acts[0], ClarifyAct)
    st, acts = _play(r, _pick("1"), state=st)
    assert isinstance(acts[0], Lookup)
    assert _qty(st) == {"A1": 5, "SRTWC286": 3}


def test_rg7b_two_ambiguous_items_ask_the_second_after_the_first_pick_and_keep_both():
    r = FakeResolver(ITEMS)
    both = out(ask(act="new_topic", domain="incoming", entities=[entity("product", phrase="mou"), entity("product", phrase="moc")]))
    st, acts = _play(r, both)
    assert isinstance(acts[0], ClarifyAct) and acts[0].phrase == "mou"
    st, acts = _play(r, _pick("1"), state=st)
    assert isinstance(acts[0], ClarifyAct) and acts[0].phrase == "moc", "moc is asked next, not dropped"
    st, acts = _play(r, _pick("1"), state=st)
    assert isinstance(acts[0], Lookup)
    assert sorted(_keys(st)) == ["MOC-1", "SRTWC286"]


@pytest.mark.parametrize("availability_only", [False, True], ids=["staff", "availability-only dealer"])
def test_rg7b_an_inventory_turn_with_an_exact_and_an_ambiguous_item_never_drops_the_exact_one(availability_only):
    r = FakeResolver(ITEMS)
    st, acts = apply_turn(new_state(NOW), _a1_and_mou("inventory"), resolver=r, now=NOW,
                          stock_availability_only=availability_only)
    assert "A1" in _keys(st), f"A1 was dropped on turn 1: held {_keys(st)}"
    assert _qty(st).get("A1") == 5


def test_rg7b_a_brand_pick_beside_an_exact_brand_keeps_both():
    r = FakeResolver({"entities": {"brand|sorento": _exact("brand", "sorento", "Sorento"), "brand|moc": _amb("brand", ("mocha", "mocca"))}})
    a = ask(act="new_topic", domain="inventory", entities=[entity("brand", key="sorento", phrase="sorento"), entity("brand", phrase="moc")])
    st, acts = _play(r, out(a))
    st, acts = _play(r, _pick("1"), state=st)
    assert isinstance(acts[0], Lookup)
    assert _keys(st, "brand") == ["sorento", "mocha"]


# ------------------------------------------------------------------ RG-3 (#1603) / RG-8: the v1 escalate offer answered yes

from tests.chatbot.pv2.test_core_engine_hook import FakeResult, _set_contact_mode, fake_turn  # noqa: E402,F401
from tests.chatbot.test_engine import _envelope, _parser_output, _turn_row, seeded, stub_access, stub_parser  # noqa: E402,F401
from app.services.chatbot import engine as engine_mod  # noqa: E402
from app.services.chatbot.lanes.escalation import ROUTED_TO_PIC_REPLY  # noqa: E402


def _routing_lane(monkeypatch):
    def _fake(ctx, item, *, dry_run, session_factory):
        return {"arm": "assign", "pending": None, "actions": [
            {"kind": "assign_conversation", "respond_user_id": "zzt-round-robin", "dry_run": dry_run},
            {"kind": "send_message", "text": ROUTED_TO_PIC_REPLY, "quick_replies": None, "result_set": [], "dry_run": dry_run}]}

    monkeypatch.setattr(engine_mod, "run_escalation_lane", _fake)


def _asks_for_a_person_verdict():
    return _parser_output(message_type="request_for_help", intent_hint=None, domain_hint=None, entities=[],
                          user_goal="talk to a person", asks=None, topic_reset=None)


def test_rg3_the_routed_text_is_on_the_reply_and_on_the_saved_row_for_a_v2_contact_whatever_the_route(
    session_factory, seeded, stub_parser, stub_access, fake_turn, monkeypatch
):
    # The flipped pin in test_fix_r2f.py covers the lookup route; this one covers a v2 turn whose trace names no route.
    _routing_lane(monkeypatch)
    fake_turn(FakeResult(verdict=_asks_for_a_person_verdict(), trace={"index_sha": "abc12345", "version": 3}))
    stub_parser(error=AssertionError("v1 parser must not run on a v2 turn"))
    stub_access()
    _set_contact_mode(session_factory, "v2")
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert (result.reply or {}).get("text") == ROUTED_TO_PIC_REPLY
    assert _turn_row(session_factory, result.turn_id).response["reply"]["text"] == ROUTED_TO_PIC_REPLY


def test_rg8_a_v1_contact_escalation_turn_is_unchanged(session_factory, seeded, stub_parser, stub_access, monkeypatch):
    _routing_lane(monkeypatch)
    stub_parser(_asks_for_a_person_verdict())
    stub_access()
    result = engine_mod.run_turn(_envelope(), session_factory=session_factory)
    assert not ((result.reply or {}).get("text") or "").strip(), "v1 keeps its own blank reply text (#1603 is v1's to fix)"
    assert any(a.get("kind") == "send_message" and a.get("text") == ROUTED_TO_PIC_REPLY for a in result.actions)
