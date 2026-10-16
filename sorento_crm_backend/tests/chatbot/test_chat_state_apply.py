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


# --------------------------------------------------------------------------- #
# S4b: held-slot ops (owner 16 Oct ~22:45). Contract: keep / skip / clear / unclear may carry
# axis customer or period. The focus is changed only by `clear`; the stance per slot is
# reported in `applied.log["slots"]` as {axis: op} (skip = held but not applied to this ask).
# --------------------------------------------------------------------------- #

WINDOW = {"start": "2026-10-01", "end": "2026-10-07"}


def _held_state() -> State:
    return State(focus=Focus(customers=[_customer()], date_window=dict(WINDOW), domains=["order"]), turn_no=3)


@pytest.mark.parametrize("axis", ["customer", "period"])
@pytest.mark.parametrize("op", ["keep", "skip", "unclear"])
def test_s4b_keep_skip_unclear_leave_the_stored_focus_and_report_the_stance(op, axis):
    state = _held_state()
    out = _apply(state, verdict(state_changes=[_op(op, axis)]))
    assert out.valid is True, out.log.get("rejected")
    assert out.state.focus.customers == state.focus.customers
    assert out.state.focus.date_window == state.focus.date_window
    assert out.log["slots"] == {axis: op}
    assert "state_changes" not in out.verdict


@pytest.mark.parametrize(
    "axis,field,empty", [("customer", "customers", []), ("period", "date_window", None)]
)
def test_s4b_clear_drops_the_slot_from_the_stored_focus(axis, field, empty):
    out = _apply(_held_state(), verdict(state_changes=[_op("clear", axis)]))
    assert out.valid is True, out.log.get("rejected")
    assert getattr(out.state.focus, field) == empty
    assert out.log["slots"] == {axis: "clear"}


def test_s4b_two_slots_can_be_stated_in_one_emission():
    out = _apply(_held_state(), verdict(state_changes=[_op("skip", "customer"), _op("keep", "period")]))
    assert out.valid is True, out.log.get("rejected")
    assert out.log["slots"] == {"customer": "skip", "period": "keep"}


def test_s4b_no_statement_reports_no_slots():
    out = _apply(_held_state(), verdict(state_changes=[_op("keep")]))
    assert out.valid is True
    assert out.log.get("slots", {}) == {}


@pytest.mark.parametrize("op", ["keep", "skip", "clear", "unclear"])
def test_s4b_an_axis_other_than_customer_or_period_is_invalid_for_these_ops(op):
    state = _held_state()
    for axis in ("product", "gizmo"):
        out = _apply(state, verdict(state_changes=[_op(op, axis)]))
        assert out.valid is False, (op, axis)
        assert out.state.focus == state.focus
        assert out.log["rejected"]


def test_s4b_the_same_slot_stated_twice_is_invalid():
    out = _apply(_held_state(), verdict(state_changes=[_op("skip", "customer"), _op("keep", "customer")]))
    assert out.valid is False
    assert out.log["rejected"]


# --------------------------------------------------------------------------- #
# Reviewer findings (round 4)
# --------------------------------------------------------------------------- #


def _answered_task_question():
    from app.services.chatbot.turn import task as task_mod

    task = task_mod.Task(
        kind="stock_qty", domain="inventory", status=task_mod.ANSWERED,
        slots=(task_mod.Slot("11111111-1111-4111-8111-111111111111", "SRT1", 5),),
    )
    return task, task_mod.open_question((task,))


def _owed_task_question():
    from app.services.chatbot.turn import task as task_mod

    task = task_mod.Task(
        kind="stock_qty", domain="inventory", status=task_mod.OPEN,
        slots=(task_mod.Slot("11111111-1111-4111-8111-111111111111", "SRT1", None),),
    )
    return task, task_mod.open_question((task,))


def _mode(out):
    return (out.verdict.get("open_question_answer") or {}).get("mode")


def test_b1_close_question_is_a_no_op_on_an_answered_stock_question():
    task, oq = _answered_task_question()
    state = State(focus=Focus(products=[_held("SRT1")], domains=["inventory"], tasks=(task,)), turn_no=3)
    v = verdict(domain_hint="incoming", state_changes=[_op("switch_domain"), _op("close_question")])
    out = _apply(state, v, open_question=oq)
    assert out.valid is True, out.log.get("rejected")
    assert _mode(out) != "cancel"
    assert [p["canonical_code"] for p in out.state.focus.products] == ["SRT1"]


def test_b1_a_sole_close_question_cancels_only_an_owed_quantities_question():
    task, oq = _owed_task_question()
    state = State(focus=Focus(products=[_held("SRT1")], domains=["inventory"], tasks=(task,)), turn_no=3)
    out = _apply(state, verdict(state_changes=[_op("close_question")]), open_question=oq)
    assert out.valid is True and _mode(out) == "cancel"
    task2, oq2 = _answered_task_question()
    state2 = State(focus=Focus(products=[_held("SRT1")], domains=["inventory"], tasks=(task2,)), turn_no=3)
    out2 = _apply(state2, verdict(state_changes=[_op("close_question")]), open_question=oq2)
    assert _mode(out2) != "cancel"


@pytest.mark.parametrize(
    "focus",
    [
        Focus(set_clarify={"kind": "brand", "options": ["Sorento", "Mocha"]}),
        Focus(set_page={"other_brands": ["Mocha"]}),
        Focus(top_selling={"asked": {"slot": "basis"}}),
    ],
    ids=["set_clarify", "other_brands", "top_selling_asked"],
)
def test_s2_answer_to_a_focus_held_question_is_accepted_and_the_answer_is_kept(focus):
    answer = {"mode": "pick", "picked": [], "items": [], "qty_for_all": None}
    brand = entity("sorento", "brand", canonical_code="SORENTO")
    out = _apply(
        State(focus=focus, turn_no=3),
        verdict(state_changes=[_op("answer")], open_question_answer=answer, entities=[brand]),
        open_question=None,
    )
    assert out.valid is True, out.log.get("rejected")
    assert out.verdict["open_question_answer"]["mode"] == "pick"
    assert [e["canonical_code"] for e in out.verdict["entities"]] == ["SORENTO"]


def test_s4_two_picks_on_a_roster_answer_both_positions():
    pending, oq = _roster([("SRT1", "SRT1"), ("SRT2", "SRT2"), ("SRT3", "SRT3")])
    state = State(focus=Focus(), pending=pending, turn_no=3)
    out = _apply(state, verdict(state_changes=[_op("pick", position=1), _op("pick", position=3)]), open_question=oq)
    assert out.valid is True, out.log.get("rejected")
    assert out.verdict["reference_positions"] == [1, 3]
    assert out.verdict["open_question_answer"]["picked"] == [1, 3]


def _list_rows(entity_type="product"):
    return [
        {"position": i, "label": c, "code": c, "entity_type": entity_type}
        for i, c in enumerate(["SRT1", "SRT2", "SRT3"], 1)
    ]


def test_s4_two_picks_on_a_data_list_give_two_entities():
    state = State(focus=Focus(last_list=_list_rows()), turn_no=3)
    out = _apply(state, verdict(state_changes=[_op("pick", position=1), _op("pick", position=3)]))
    assert out.valid is True, out.log.get("rejected")
    assert sorted(e["canonical_code"] for e in out.verdict["entities"]) == ["SRT1", "SRT3"]


def test_s5_only_product_rows_of_the_last_list_are_pickable():
    ok = _apply(State(focus=Focus(last_list=_list_rows()), turn_no=3), verdict(state_changes=[_op("pick", position=2)]))
    assert ok.valid is True
    for kind in ("spec_bucket", None):
        out = _apply(
            State(focus=Focus(last_list=_list_rows(kind)), turn_no=3),
            verdict(state_changes=[_op("pick", position=2)]),
        )
        assert out.valid is False, kind
        assert out.log["rejected"]


def test_s6_new_subject_on_an_empty_shown_state_changes_nothing():
    state = State(focus=Focus(), turn_no=1)
    out = _apply(state, verdict(domain_hint="sales", state_changes=[_op("new_subject")]))
    assert out.valid is True
    assert out.state.state_led is None
    assert out.state.focus == state.focus


def test_s7_a_brand_set_by_name_or_by_code_is_valid_when_the_registry_carries_both():
    from app.services.chatbot.turn import chat_state

    registry = [{"brand_name": "Sorento Home", "brand_code": "SRH"}]
    state = State(focus=Focus(products=[_held("SRT5733")]), turn_no=3)
    for raw, code in (("Sorento Home", None), ("srh", "SRH"), ("Sorento Home", "SRH")):
        out = chat_state.apply_changes(
            state,
            verdict(state_changes=[_op("set", "brand")], entities=[entity(raw, "brand", canonical_code=code)]),
            open_question=None, domains=DOMAINS, kinds=KINDS, brands=registry, positions_read_elsewhere=False,
        )
        assert out.valid is True, (raw, code, out.log.get("rejected"))
    bad = chat_state.apply_changes(
        state,
        verdict(state_changes=[_op("set", "brand")], entities=[entity("zzz", "brand", canonical_code="ZZZ")]),
        open_question=None, domains=DOMAINS, kinds=KINDS, brands=registry, positions_read_elsewhere=False,
    )
    assert bad.valid is False


def test_s8a_set_quantity_with_no_product_named_keeps_the_held_product_and_sets_the_quantity():
    state = State(focus=Focus(products=[_held("SRT5733")], domains=["inventory"]), turn_no=3)
    out = _apply(state, verdict(demand_qty=5, state_changes=[_op("set", "quantity")]))
    assert out.valid is True, out.log.get("rejected")
    assert [p["canonical_code"] for p in out.state.focus.products] == ["SRT5733"]
    assert out.verdict["demand_qty"] == 5 or any(e.get("quantity") == 5 for e in out.verdict["entities"])


def test_s8b_a_new_subject_closes_the_question_but_the_last_list_stays_pickable():
    pending, oq = _roster([("SRT1", "SRT1"), ("SRT2", "SRT2")])
    state = State(focus=Focus(last_list=_list_rows()), pending=pending, turn_no=3)
    first = _apply(state, verdict(domain_hint="inventory", state_changes=[_op("new_subject")]), open_question=oq)
    assert first.valid is True and first.state.pending is None and first.state.focus.last_list
    second = _apply(first.state, verdict(state_changes=[_op("pick", position=2)]))
    assert second.valid is True, second.log.get("rejected")
    assert [e["canonical_code"] for e in second.verdict["entities"]] == ["SRT2"]


def test_s8c_an_implied_domain_switch_under_keep_drops_the_domain_bound_axes_including_the_channel():
    focus = Focus(products=[_held("SRT5733")], domains=["sales"], sales_channel="dealer", status="top_selling")
    out = _apply(State(focus=focus, turn_no=3), verdict(domain_hint="inventory", state_changes=[_op("keep")]))
    assert out.valid is True
    assert out.state.focus.sales_channel is None
    assert out.state.focus.status is None
    assert [p["canonical_code"] for p in out.state.focus.products] == ["SRT5733"]


def test_s8d_a_stated_period_survives_only_with_a_set_period():
    focus = Focus(products=[_held("SRT5733")], domains=["order"])
    period = dict(date_mode="range", date_filter_start="2026-10-01", date_filter_end="2026-10-07", domain_hint="inventory")
    dropped = _apply(State(focus=focus, turn_no=3), verdict(state_changes=[_op("switch_domain")], **period))
    assert dropped.verdict["date_filter_start"] is None and dropped.verdict["date_mode"] is None
    kept = _apply(
        State(focus=focus, turn_no=3), verdict(state_changes=[_op("switch_domain"), _op("set", "period")], **period)
    )
    assert kept.valid is True and kept.verdict["date_filter_start"] == "2026-10-01"


def test_s8e_an_empty_change_list_falls_back_to_todays_path_with_the_reason_logged():
    state = State(focus=Focus(products=[_held("SRT5733")]), turn_no=3)
    v = verdict(domain_hint="inventory", state_changes=[])
    out = _apply(state, v)
    assert out.valid is False
    assert out.state is state or out.state.focus == state.focus
    assert out.log["rejected"] and out.log["rejected"][0]["reason"]
    assert "state_changes" not in out.verdict


def test_nit_the_focus_wire_omits_last_list_when_none_and_keeps_it_when_set():
    from app.services.chatbot.turn.state import focus_from_wire, focus_to_wire

    assert "last_list" not in focus_to_wire(Focus())
    rows = [{"position": 1, "label": "SRT1", "code": "SRT1"}]
    wire = focus_to_wire(Focus(last_list=rows))
    assert focus_from_wire(wire).last_list == rows


def test_nit_close_question_also_closes_the_top_selling_ask_and_other_brands_offer():
    focus = Focus(top_selling={"asked": {"slot": "basis"}, "basis": "qty"}, set_page={"other_brands": ["Mocha"], "page": 1})
    out = _apply(State(focus=focus, turn_no=3), verdict(state_changes=[_op("close_question")]), open_question=None)
    assert out.valid is True
    assert not (out.state.focus.top_selling or {}).get("asked")
    assert not (out.state.focus.set_page or {}).get("other_brands")


# --------------------------------------------------------------------------- #
# Owner 16 Oct ~23:00: the held period survives a new subject unless the parser says otherwise
# --------------------------------------------------------------------------- #


def _period_state() -> State:
    return State(focus=Focus(customers=[_customer()], date_window=dict(WINDOW), domains=["order"]), turn_no=3)


def test_new_subject_with_no_period_statement_keeps_the_held_period():
    out = _apply(_period_state(), verdict(domain_hint="incoming", state_changes=[_op("new_subject")]))
    assert out.valid is True
    assert out.state.focus.date_window == WINDOW


def test_new_subject_with_set_period_replaces_the_held_period():
    v = verdict(
        domain_hint="incoming", date_mode="range", date_filter_start="2026-11-01", date_filter_end="2026-11-07",
        state_changes=[_op("new_subject"), _op("set", "period")],
    )
    out = _apply(_period_state(), v)
    assert out.valid is True, out.log.get("rejected")
    assert out.verdict["date_filter_start"] == "2026-11-01"
    assert out.state.focus.date_window in (None, {"start": "2026-11-01", "end": "2026-11-07"})
    assert out.state.focus.date_window != WINDOW


def test_new_subject_with_clear_period_drops_the_held_period():
    out = _apply(_period_state(), verdict(domain_hint="incoming", state_changes=[_op("new_subject"), _op("clear", "period")]))
    assert out.valid is True, out.log.get("rejected")
    assert out.state.focus.date_window is None


# --------------------------------------------------------------------------- #
# Round 5: lenient validation (a malformed part never voids the whole list)
# --------------------------------------------------------------------------- #


def test_r5_repeated_set_product_ops_are_merged_not_rejected():
    state = State(focus=Focus(products=[_held("SRT1")]), turn_no=3)
    ents = [entity("SRT2"), entity("SRT3"), entity("SRT4")]
    out = _apply(state, verdict(entities=ents, state_changes=[_op("set", "product")] * 3))
    assert out.valid is True, out.log.get("rejected")
    assert [e["canonical_code"] for e in out.verdict["entities"]] == ["SRT2", "SRT3", "SRT4"]


def test_r5_a_set_with_no_value_in_the_parse_is_a_logged_noop_and_the_rest_applies():
    state = State(focus=Focus(products=[_held("SRT1")]), turn_no=3)
    brand = entity("sorento", "brand", canonical_code="SORENTO")
    out = _apply(
        state, verdict(entities=[brand], state_changes=[_op("set", "channel"), _op("set", "brand")])
    )
    assert out.valid is True, out.log.get("rejected")
    assert [e["canonical_code"] for e in out.verdict["entities"]] == ["SORENTO"]
    ignored = out.log.get("ignored")
    assert ignored and ignored[0]["axis"] == "channel" and ignored[0]["reason"], out.log


def test_r5_axis_document_is_accepted_for_set_and_clear():
    state = State(focus=Focus(products=[_held("SRT1")], document=["PO1001"]), turn_no=3)
    out = _apply(state, verdict(document=["PO2002"], state_changes=[_op("set", "document")]))
    assert out.valid is True, out.log.get("rejected")
    assert out.state.focus.document == ["PO2002"] or out.verdict["document"] == ["PO2002"]
    cleared = _apply(state, verdict(state_changes=[_op("clear", "document")]))
    assert cleared.valid is True, cleared.log.get("rejected")
    assert cleared.state.focus.document == []


def test_r5_every_subject_key_the_state_block_prints_is_an_accepted_axis():
    from app.services.chatbot.turn import chat_state

    focus = Focus(
        products=[_held("SRT1")], customers=[_customer()], brands=["SORENTO"], tier=["dealer"],
        warehouse=[{"raw": "BRW", "canonical_code": "BRW", "hint": "warehouse"}],
        sales_channel="dealer", status="open", document=["PO1001"], sort={"by": "date", "dir": "asc"},
        date_window=dict(WINDOW), domains=["order"], relation="bigger",
        extra={"attachment_type": [{"raw": "photo", "canonical_code": "photo"}]},
    )
    state = State(focus=focus, turn_no=3)
    printed = list(chat_state.view(state, None)["subject"])
    assert "attachment_type" in printed and "document" in printed
    for axis in printed:
        out = _apply(state, verdict(state_changes=[_op("clear", axis)]))
        assert out.valid is True, (axis, out.log.get("rejected"))


def test_r5_an_unknown_op_or_a_truly_unknown_axis_still_invalidates_with_a_reason():
    state = State(focus=Focus(products=[_held("SRT1")]), turn_no=3)
    for change in (_op("teleport"), _op("clear", "gizmo"), _op("set", "gizmo")):
        out = _apply(state, verdict(entities=[entity("X1")], state_changes=[change]))
        assert out.valid is False, change
        assert out.log["rejected"]
