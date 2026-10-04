"""Owner hand test 26 Sep 2026 (PR #1247), slice 2: a family becomes a which-one pick,
and an exact code wins.

Owner ruling 26 Sep (R1 re-ruled, "for R1 - okay can"): #1118's quantity collection is
changed in this PR. Every test below is one owner turn from the scout's comment, with the
focus state quoted there as its fixture:

* T1 "check stock srtwc286": ten SRTWC286-SH* products, none coded SRTWC286. Was a
  ten-slot quantity task; is a pick with no task.
* T2 "88" under that question: was a re-ask of all ten; is the pick again, carrying 88.
* T3 "check stock srtwc286 88 pcs": the 88 joined to nothing; rides on the pick, and the
  code the dealer then types is answered with it.
* T4 "check stock SRTWC286-SH": an exact code among its nine siblings keeps only itself.
* T5 fresh "check stock SRTWC286-SH-UF": one product, one named question.
* T6 "10" after that question: answered.

Drives the same seams `engine.py` does, in the same order: `turn/task.py::after_reply`
(what the stock tool's reply leaves), `engine._stock_ask_reply` (what the turn then says
and stores) and `turn/apply.py::apply` (what the answering turn fetches).
"""
from __future__ import annotations

from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.state import Focus

from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import build_policy, verdict

FAMILY = ht.SRTWC286_FAMILY


def _family_rows():
    return [ht.row(code) for code in FAMILY]


# --------------------------------------------------------------------------- #
# after_reply: the pure rule
# --------------------------------------------------------------------------- #


def test_t1_family_with_no_quantity_is_a_pick_and_opens_no_task():
    reply = task_mod.after_reply(
        (),
        ht.envelopes(*_family_rows()),
        turn_no=1,
        named_products=True,
        asked=[ht.asked("srtwc286")],
    )

    assert reply.tasks == (), "a family is a pick, never a ten-slot quantity task"
    assert reply.text == "\n".join(
        ["SRTWC286 matches 10 products. Which one?", *task_mod.numbered(FAMILY)]
    )
    assert reply.pick is not None
    assert [o["label"] for o in reply.pick["options"]] == FAMILY
    assert all(o["uuid"] == ht.uuid_of(o["label"]) for o in reply.pick["options"])
    assert reply.pick["payload"]["stock_pick"] is True
    assert reply.pick["payload"]["stock_qty"] is None
    assert reply.pick["payload"]["domains"] == ["inventory"]


def test_t3_family_with_a_quantity_carries_it_on_the_pick():
    reply = task_mod.after_reply(
        (),
        ht.envelopes(*_family_rows()),
        turn_no=3,
        asked=[ht.asked("srtwc286", quantity=88)],
    )

    assert reply.tasks == ()
    assert reply.text.splitlines()[0] == "SRTWC286 x 88: which one?"
    assert reply.text.splitlines()[1:] == task_mod.numbered(FAMILY)
    assert reply.pick["payload"]["stock_qty"] == 88


def test_t3_top_level_demand_qty_is_carried_too():
    reply = task_mod.after_reply(
        (),
        ht.envelopes(*_family_rows()),
        asked=[ht.asked("srtwc286")],
        demand_qty=88,
    )
    assert reply.pick["payload"]["stock_qty"] == 88


def test_more_than_ten_matches_lists_every_one():
    """PICKER-NO-CAP (owner, 2 Oct 2026): no cut at ten, every match is numbered."""
    codes = [f"SRTX1-{i:02d}" for i in range(12)]
    reply = task_mod.after_reply(
        (), ht.envelopes(*[ht.row(c) for c in codes]), asked=[ht.asked("srtx1")]
    )
    lines = reply.text.splitlines()
    assert lines[0] == "SRTX1 matches 12 products. Which one?"
    assert lines[1:] == task_mod.numbered(codes)


def test_t4_exact_code_wins_over_its_siblings():
    reply = task_mod.after_reply(
        (),
        ht.envelopes(*_family_rows()),
        turn_no=4,
        asked=[ht.asked("SRTWC286-SH")],
    )

    assert reply.pick is None
    assert len(reply.tasks) == 1
    assert [s.label for s in reply.tasks[0].slots] == ["SRTWC286-SH"]
    assert reply.text == "How many units of SRTWC286-SH?"


def test_t5_fresh_exact_code_asks_for_that_one_product():
    reply = task_mod.after_reply(
        (),
        ht.envelopes(ht.row("SRTWC286-SH-UF")),
        turn_no=5,
        asked=[ht.asked("SRTWC286-SH-UF")],
    )

    assert reply.pick is None
    assert [s.label for s in reply.tasks[0].slots] == ["SRTWC286-SH-UF"]
    assert reply.text == "How many units of SRTWC286-SH-UF?"


def test_a_multi_product_ask_is_not_a_family():
    reply = task_mod.after_reply(
        (),
        ht.envelopes(ht.row("ELP3754"), ht.row("SRTKT1631SS")),
        asked=[ht.asked("ELP3754"), ht.asked("SRTKT1631SS")],
    )
    assert reply.pick is None
    assert [s.label for s in reply.tasks[0].slots] == ["ELP3754", "SRTKT1631SS"]
    # Round 6, ruling 2: point form, one numbered line per product.
    assert reply.text == "How many units for each?\n1. ELP3754 - \n2. SRTKT1631SS - "


def test_no_availability_block_leaves_everything_alone():
    """R10: a detailed / compact contact's reply carries no `stock_availability` block,
    so none of this reaches staff."""
    task = ht.stock_task([("A", None)])
    reply = task_mod.after_reply(
        (task,), [{"items": []}], asked=[ht.asked("srtwc286")]
    )
    assert reply.tasks == (task,)
    assert reply.text is None and reply.pick is None


# --------------------------------------------------------------------------- #
# engine: what the turn says and stores
# --------------------------------------------------------------------------- #


class _Spec:
    def __init__(self, domain, entities=None):
        self.domain = domain
        self.entities = entities if entities is not None else [{"uuid": "x"}]
        self.filters = {}


class _Plan:
    def __init__(self, *domains):
        self.fetch = [_Spec(d) for d in domains]


def test_t1_engine_says_the_pick_and_stores_it_as_the_open_question():
    from app.services.chatbot.engine import _stock_ask_reply
    from app.services.chatbot.turn import compose as turn_compose

    state_out = ht.state(Focus(products=ht.product_rows(*FAMILY)))
    composed = turn_compose.Answer(text="How many units do you need?")
    out = _stock_ask_reply(
        composed,
        state_out,
        ht.envelopes(*_family_rows()),
        _Plan("inventory"),
        verdict(domain_hint="inventory", entities=[ht.asked("srtwc286")]),
        turn_no=1,
    )

    assert out.text.startswith("SRTWC286 matches 10 products. Which one?")
    assert "How many units" not in out.text
    assert "Data last updated" not in out.text
    assert out.question is not None and out.question.kind == "product_pick"
    assert [o["label"] for o in out.question.options] == FAMILY
    assert state_out.focus.tasks == ()


def test_engine_leaves_a_multi_domain_reply_alone():
    from app.services.chatbot.engine import _stock_ask_reply
    from app.services.chatbot.turn import compose as turn_compose

    composed = turn_compose.Answer(text="composed")
    out = _stock_ask_reply(
        composed,
        ht.state(),
        ht.envelopes(*_family_rows()),
        _Plan("inventory", "promotion"),
        verdict(entities=[ht.asked("srtwc286")]),
        turn_no=1,
    )
    assert out is composed


# --------------------------------------------------------------------------- #
# apply: the turn that answers the pick
# --------------------------------------------------------------------------- #


def test_t2_bare_number_under_the_pick_re_asks_it_carrying_the_number():
    focus = Focus(products=ht.product_rows(*FAMILY), domains=["inventory"])
    state2, plan = apply(
        ht.state(focus, pending=ht.family_pick()),
        verdict(demand_qty=88, entities=[]),
        build_policy(),
    )

    assert plan.fetch == []
    assert plan.trace.task_question.splitlines()[0] == "SRTWC286 x 88: which one?"
    assert state2.pending is not None
    assert state2.pending.payload["stock_qty"] == 88
    assert state2.focus.tasks == ()


def test_t3_then_the_code_is_answered_with_the_carried_quantity():
    focus = Focus(products=ht.product_rows(*FAMILY), domains=["inventory"])
    state2, plan = apply(
        ht.state(focus, pending=ht.family_pick(quantity=88)),
        verdict(entities=[ht.asked("SRTWC286-SH-UF")]),
        build_policy(),
    )

    specs = ht.inventory_specs(plan)
    assert len(specs) == 1
    assert [e.get("uuid") for e in specs[0].entities] == [ht.uuid_of("SRTWC286-SH-UF")]
    assert specs[0].filters.get("requested_quantities") == {ht.uuid_of("SRTWC286-SH-UF"): 88}
    assert state2.pending is None, "the pick is spent once stock is fetched"


def test_t3_then_a_position_is_answered_with_the_carried_quantity_too():
    focus = Focus(products=ht.product_rows(*FAMILY), domains=["inventory"])
    state2, plan = apply(
        ht.state(focus, pending=ht.family_pick(quantity=88)),
        verdict(reference_positions=[2], entities=[]),
        build_policy(),
    )
    specs = ht.inventory_specs(plan)
    assert specs[0].filters.get("requested_quantities") == {ht.uuid_of("SRTWC286-SH-UF"): 88}
    assert state2.pending is None


def test_a_quantity_typed_with_the_code_wins_over_the_carried_one():
    focus = Focus(products=ht.product_rows(*FAMILY), domains=["inventory"])
    _state2, plan = apply(
        ht.state(focus, pending=ht.family_pick(quantity=88)),
        verdict(entities=[ht.asked("SRTWC286-SH-UF", quantity=100)]),
        build_policy(),
    )
    specs = ht.inventory_specs(plan)
    assert specs[0].filters.get("requested_quantities") is None, (
        "this message stated its own quantity; the turn runtime joins it by code"
    )


def test_t6_ten_after_a_one_product_question_is_answered():
    task = ht.stock_task([("SRTWC286-SH-UF", None)])
    state2, plan = apply(
        ht.state(Focus(tasks=(task,), domains=["inventory"])),
        verdict(demand_qty=10, entities=[]),
        build_policy(),
    )
    specs = ht.inventory_specs(plan)
    assert len(specs) == 1
    assert specs[0].filters.get("requested_quantities") == {ht.uuid_of("SRTWC286-SH-UF"): 10}
