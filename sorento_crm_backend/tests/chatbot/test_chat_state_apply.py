"""CHAT-STATE S3 (red): `chat_state.apply_changes`, the pure application of the parser's
`state_changes` (UAC C1-C5, card rules R1-R12; plan test list S3 items 1-10).

The period on `new_subject` is deliberately NOT asserted (owner ruling Q3 pending, S3b).

Contract used here: `apply_changes(state, verdict, *, open_question, domains, kinds,
brands, positions_read_elsewhere) -> Applied(.valid, .state, .verdict, .log)`.
`domains` is the registry's domain names, `kinds` its entity kind names, `brands` its
brand codes. A list item is `{"position", "label", "code"}`. An op carries no value: it
is read from the parse's existing fields.
"""
from __future__ import annotations

import copy
import inspect

import pytest

from app.services.chatbot.turn import question as question_mod
from app.services.chatbot.turn.pending import ask
from app.services.chatbot.turn.state import Focus, State
from tests.chatbot._turn_helpers import entity, verdict

DOMAINS = ["inventory", "incoming", "order", "sales"]
KINDS = ["product", "customer", "brand"]
BRANDS = ["SORENTO", "MOCHA"]


def _held(code: str, **extra):
    return {"raw": code, "canonical_code": code, "hint": "product", "current_message": False, **extra}


def _customer(name: str = "Customer X", code: str = "C0001"):
    return {"raw": name, "canonical_code": code, "hint": "customer", "current_message": False}


def _op(op: str, axis=None, position=None):
    return {"op": op, "axis": axis, "position": position}


def _apply(state: State, v: dict, *, open_question=None, positions_read_elsewhere=False):
    from app.services.chatbot.turn import chat_state

    return chat_state.apply_changes(
        state,
        v,
        open_question=open_question,
        domains=DOMAINS,
        kinds=KINDS,
        brands=BRANDS,
        positions_read_elsewhere=positions_read_elsewhere,
    )


def _roster(codes_labels):
    pending = ask(
        "product_pick",
        [
            {"position": i, "label": lab, "code": code, "name": None, "entity_type": "product", "payload": {}}
            for i, (code, lab) in enumerate(codes_labels, start=1)
        ],
        asked_at_turn=2,
    )
    return pending, question_mod.of_pending(pending)


def test_keep_leaves_the_focus_unchanged_and_state_led_carry():
    focus = Focus(products=[_held("SRT5733", quantity=5)], customers=[_customer()], brands=["SORENTO"], sales_channel="dealer")
    before = copy.deepcopy(focus)
    out = _apply(State(focus=focus, turn_no=3), verdict(state_changes=[_op("keep")]))
    assert out.valid is True
    assert out.state.focus == before
    assert out.state.state_led == "carry"
    assert "state_changes" not in out.verdict


def test_new_subject_keeps_customer_tier_brands_and_drops_the_rest_and_closes_pending():
    pending, oq = _roster([("SRT5733", "SRT5733"), ("SRTWC286", "SRTWC286")])
    focus = Focus(
        products=[_held("SRT5733")],
        customers=[_customer()],
        brands=["SORENTO"],
        tier=["dealer"],
        status="open",
        sales_channel="dealer",
        sales_ranking_frame={"basis": "qty_sold"},
        low_stock={"category": "basin"},
        top_selling={"rank_by": "qty"},
    )
    v = verdict(state_changes=[_op("new_subject")], entities=[entity("MWB8051")])
    out = _apply(State(focus=focus, pending=pending, turn_no=4), v, open_question=oq)
    assert out.valid is True
    kept = out.state.focus
    assert [c["canonical_code"] for c in kept.customers] == ["C0001"]
    assert kept.tier == ["dealer"]
    assert kept.brands == ["SORENTO"]
    assert all(p.get("canonical_code") != "SRT5733" for p in kept.products)
    assert kept.sales_ranking_frame is None
    assert kept.low_stock is None
    assert kept.top_selling is None
    assert kept.status is None
    assert kept.sales_channel is None
    assert out.state.pending is None
    assert out.state.state_led == "new_ask"


def test_set_and_clear_touch_only_that_axis():
    focus = Focus(products=[_held("SRT5733")], customers=[_customer()], status="open", sales_channel="project")
    base = State(focus=focus, turn_no=3)

    out = _apply(base, verdict(state_changes=[_op("set", "channel")], sales_channel="dealer"))
    assert out.valid is True
    assert out.state.focus.sales_channel == "dealer"
    assert out.state.focus.status == "open"
    assert out.state.focus.products == focus.products
    assert out.state.focus.customers == focus.customers
    assert out.state.state_led == "refine"

    out = _apply(base, verdict(state_changes=[_op("clear", "channel")]))
    assert out.valid is True
    assert out.state.focus.sales_channel is None
    assert out.state.focus.status == "open"
    assert out.state.focus.products == focus.products

    out = _apply(base, verdict(state_changes=[_op("set", "sort")], sort_by="eta", sort_dir="asc"))
    assert out.valid is True
    assert out.state.focus.sort["by"] == "eta"
    assert out.state.focus.sales_channel == "project"


def test_set_with_no_field_for_that_axis_in_the_parse_is_invalid():
    focus = Focus(products=[_held("SRT5733")], sales_channel="project")
    before = copy.deepcopy(focus)
    out = _apply(State(focus=focus, turn_no=3), verdict(state_changes=[_op("set", "channel")]))
    assert out.valid is False
    assert out.state.focus == before
    assert out.log["rejected"]


def test_pick_on_the_open_roster_declares_a_pick_answer_with_positions():
    pending, oq = _roster([("SRT5733", "SRT5733"), ("SRTWC286", "SRTWC286"), ("MWB8051", "MWB8051")])
    state = State(focus=Focus(products=[_held("SRT5733")]), pending=pending, turn_no=3)
    out = _apply(state, verdict(state_changes=[_op("pick", position=2)]), open_question=oq)
    assert out.valid is True
    assert out.verdict["open_question_answer"]["mode"] == "pick"
    assert out.verdict["reference_positions"] == [2]


def test_pick_on_a_data_list_returns_that_rows_code_and_out_of_range_is_invalid():
    rows = [
        {"position": 1, "label": "SRT5733", "code": "SRT5733"},
        {"position": 2, "label": "SRTWC286", "code": "SRTWC286"},
        {"position": 3, "label": "MWB8051", "code": "MWB8051"},
    ]
    focus = Focus(products=[_held("SRT5733")], last_list=rows)
    out = _apply(State(focus=focus, turn_no=3), verdict(state_changes=[_op("pick", position=2)]))
    assert out.valid is True
    picked = [e for e in out.verdict["entities"] if "SRTWC286" in (e.get("canonical_code"), e.get("raw"))]
    assert len(picked) == 1
    assert picked[0]["current_message"] is True

    before = copy.deepcopy(focus)
    out = _apply(State(focus=focus, turn_no=3), verdict(state_changes=[_op("pick", position=14)]))
    assert out.valid is False
    assert out.state.focus == before
    assert out.log["rejected"]


def test_without_an_answer_op_the_declared_answer_is_dropped_and_with_one_it_is_untouched():
    pending, oq = _roster([("SRT5733", "SRT5733"), ("SRTWC286", "SRTWC286")])
    declared = {"mode": "pick", "picked": [1], "items": [], "qty_for_all": None}
    state = State(focus=Focus(products=[_held("SRT5733")]), pending=pending, turn_no=3)

    v = verdict(state_changes=[_op("keep")], open_question_answer=dict(declared), reference_positions=[1])
    out = _apply(state, v, open_question=oq)
    assert (out.verdict.get("open_question_answer") or {}).get("mode") is None
    assert out.verdict.get("reference_positions") in ([], None)

    v = verdict(state_changes=[_op("answer")], open_question_answer=dict(declared), reference_positions=[1])
    out = _apply(state, v, open_question=oq)
    assert out.valid is True
    assert out.verdict == {k: val for k, val in v.items() if k != "state_changes"}


def test_close_question_closes_the_pending():
    pending, oq = _roster([("SRT5733", "SRT5733"), ("SRTWC286", "SRTWC286")])
    state = State(focus=Focus(products=[_held("SRT5733")]), pending=pending, turn_no=3)
    out = _apply(state, verdict(state_changes=[_op("close_question")]), open_question=oq)
    assert out.valid is True
    assert out.state.pending is None
    assert out.state.focus.products == state.focus.products


def test_switch_domain_drops_status_period_sort_report_frame_and_keeps_products_and_customers():
    focus = Focus(
        products=[_held("SRT5733")],
        customers=[_customer()],
        domains=["inventory"],
        status="open",
        date_window={"start": "2026-09-01", "end": "2026-09-30"},
        sort={"by": "eta", "dir": "asc"},
        sales_ranking_frame={"basis": "qty_sold"},
    )
    out = _apply(
        State(focus=focus, turn_no=3),
        verdict(state_changes=[_op("switch_domain", "incoming")], domain_hint="incoming"),
    )
    assert out.valid is True
    kept = out.state.focus
    assert kept.products == focus.products
    assert kept.customers == focus.customers
    assert kept.status is None
    assert kept.date_window is None
    assert kept.sort is None
    assert kept.sales_ranking_frame is None


def test_replacing_one_held_product_keeps_that_rows_quantity_when_the_message_gave_none():
    focus = Focus(products=[_held("SRTWC286", quantity=5)])
    state = State(focus=focus, turn_no=3)

    out = _apply(state, verdict(state_changes=[_op("set", "product")], entities=[entity("SRT5733")]))
    assert out.valid is True
    rows = [e for e in out.verdict["entities"] if e.get("canonical_code") == "SRT5733"]
    assert len(rows) == 1 and rows[0]["quantity"] == 5

    out = _apply(state, verdict(state_changes=[_op("set", "product")], entities=[entity("SRT5733", quantity=10)]))
    rows = [e for e in out.verdict["entities"] if e.get("canonical_code") == "SRT5733"]
    assert len(rows) == 1 and rows[0]["quantity"] == 10


@pytest.mark.parametrize("op", [_op("keep"), _op("set", "product")])
def test_entities_not_named_by_this_message_are_dropped(op):
    state = State(focus=Focus(products=[_held("SRT5733")]), turn_no=3)
    v = verdict(
        state_changes=[op],
        entities=[entity("SRT5733", current_message=False), entity("MWB8051", current_message=True)],
    )
    out = _apply(state, v)
    codes = [e.get("canonical_code") for e in out.verdict["entities"]]
    assert "SRT5733" not in codes
    assert "MWB8051" in codes


def test_apply_changes_is_pure_and_takes_no_message_text():
    from app.services.chatbot.turn import chat_state

    params = list(inspect.signature(chat_state.apply_changes).parameters)
    assert params == ["state", "verdict", "open_question", "domains", "kinds", "brands", "positions_read_elsewhere"]

    state = State(focus=Focus(products=[_held("SRT5733")], customers=[_customer()], sales_channel="project"), turn_no=3)
    v = verdict(state_changes=[_op("set", "channel")], sales_channel="dealer")
    state_in, v_in = copy.deepcopy(state), copy.deepcopy(v)
    first = _apply(state, v)
    second = _apply(state, v)
    assert state == state_in and v == v_in
    assert first.state == second.state
    assert first.verdict == second.verdict
    assert first.log == second.log
    assert set(first.log) >= {"shown", "before", "changes", "rejected"}


def test_registry_valid_brand_is_kept_as_given_and_unknown_values_are_invalid_with_a_reason():
    state = State(focus=Focus(products=[_held("SRT5733")]), turn_no=3)
    brand = entity("sorento", "brand", canonical_code="SORENTO")
    out = _apply(state, verdict(state_changes=[_op("set", "brand")], entities=[brand]))
    assert out.valid is True
    kept = [e for e in out.verdict["entities"] if e.get("hint") == "brand"]
    assert [e["canonical_code"] for e in kept] == ["SORENTO"]

    unknown_brand = entity("zzz", "brand", canonical_code="ZZZ")
    cases = [
        verdict(state_changes=[_op("set", "brand")], entities=[unknown_brand]),
        verdict(state_changes=[_op("set", "colour")], entities=[entity("MWB8051")]),
        verdict(state_changes=[_op("set", "gizmo")], entities=[entity("G1", "gizmo")]),
        verdict(state_changes=[_op("teleport")]),
    ]
    for v in cases:
        out = _apply(state, v)
        assert out.valid is False, v["state_changes"]
        assert out.state.focus == state.focus
        assert out.log["rejected"], v["state_changes"]
