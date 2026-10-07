"""STOCK-QTY-PARTIAL-FILL (owner, 6 Oct 2026, "this is 80% of our problem"): a partial
answer to "How many units for each?" answers the lines it fills at once; the other lines
stay OPEN and the list is never printed again.

Prod, contact 550337739, 6 Oct 2026, prompt v47, main 9373f6c90:

    Any WC with s trap 300mm available?
    -> How many units for each?
       1. BRC21466UW-2-MY -
       2. SRTWC8152-SH-300 -
       3. SRTWC8152-SH-UF-300 -
       4. SRTWC8357-300-RL -
       5. SRTWC8366-300-RL-UF -
    I need 8152 6 unit
    -> the SAME five lines, line 2 filled                       <- the defect
    (again)
    -> the same five lines again
    SRTWC8152-SH-300 x 6 Units Have stock?

Owner ruling R1 (6 Oct 22:35): "when we ask how many units for each and the user answers
like 5 for the 2nd one, we answer that one immediately; the remaining 4 stay OPEN, they can
continue answering, or switch to other products, or other domains; the design must be
flexible like this." Crew rulings: Q1 (a) no "still open" line; Q2 (a) a code off the list
is a new ask; Q3 (a) one bare number still fills every open line (26 Sep F2).

Card: documentation/plans/chatbot/stock-qty-partial-fill-behaviour-card.md. UAC AC-1 to AC-8.
Only the parser is stubbed; `Console` is the round 4 replay harness over the real
`turn/apply.py::apply`, `turn/task.py::after_reply` and `engine._stock_ask_reply`.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.state import Focus

from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import build_policy, verdict
from tests.chatbot.test_stock_ask_ht26_r4_owner_replay import TOO_BIG, Console

#: The five lines the owner's console printed on 6 Oct, in its order.
PROD_LINES = [
    "BRC21466UW-2-MY",
    "SRTWC8152-SH-300",
    "SRTWC8152-SH-UF-300",
    "SRTWC8357-300-RL",
    "SRTWC8366-300-RL-UF",
]

LIST = "\n".join(["How many units for each?", *[f"{i}. {code} - " for i, code in enumerate(PROD_LINES, 1)]])


def _task(values: list[Any], *, status: str = task_mod.OPEN):
    return ht.stock_task(list(zip(PROD_LINES, values)), status=status)


def _state_with(task, *, turn_no: int = 3):
    focus = Focus(products=ht.product_rows(*PROD_LINES), domains=["inventory"], tasks=(task,))
    return ht.state(focus, turn_no=turn_no, availability_only=True)


def _fill(*items: tuple[int | None, str | None, int]) -> dict[str, Any]:
    """The parser's `open_question_answer` for a fill of the given (position, code, qty)."""
    return {
        "mode": "fill",
        "items": [{"position": p, "code": c, "qty": q} for p, c, q in items],
        "qty_for_all": None,
    }


def _stock_task(state) -> Any:
    (task,) = [t for t in state.focus.tasks if t.kind == "stock_qty"]
    return task


def _answered(quantities: dict[str, int]) -> str:
    return "\n".join(f"{code} x {qty}: {TOO_BIG}" for code, qty in quantities.items())


def _console_after_the_list() -> Console:
    """The owner's state after turn 1: the five-line task open, nothing noted."""
    console = Console()
    console.state = _state_with(_task([None] * 5), turn_no=1)
    console.transcript = ["Any WC with s trap 300mm available?", f"-> {LIST}"]
    return console


# --------------------------------------------------------------------------- #
# AC-1 / AC-2: apply - the filled line is fetched alone, the task stays open
# --------------------------------------------------------------------------- #


def test_a_partial_fill_fetches_the_filled_line_only_and_asks_nothing():
    """"I need 8152 6 unit": the parser resolved the fragment to line 2 (the Open question
    lists the codes) and declared a fill. Line 2 is fetched at 6; lines 1, 3, 4, 5 are
    neither fetched nor asked again."""
    state = _state_with(_task([None] * 5))
    v = verdict(entities=[], open_question_answer=_fill((None, "SRTWC8152-SH-300", 6)))
    new_state, plan = apply(state, v, build_policy())
    assert "open_question_answer_fill" in plan.trace.rules_fired
    assert "task_partial_answered_stock_qty" in plan.trace.rules_fired
    assert plan.trace.task_question is None
    (spec,) = ht.inventory_specs(plan)
    assert [row["canonical_code"] for row in spec.entities] == ["SRTWC8152-SH-300"]
    assert spec.filters.get("requested_quantities") == {ht.uuid_of("SRTWC8152-SH-300"): 6}
    assert not spec.filters.get("not_checked"), "nothing is dropped and nothing is 'Not checked'"
    task = _stock_task(new_state)
    assert task.status == task_mod.OPEN
    assert [slot.label for slot in task.slots] == PROD_LINES
    assert [slot.value for slot in task.slots] == [None, 6, None, None, None]


def test_the_open_question_after_a_partial_fill_still_owes_the_other_lines():
    state = _state_with(_task([None] * 5))
    v = verdict(entities=[], open_question_answer=_fill((2, None, 6)))
    new_state, _plan = apply(state, v, build_policy())
    question = task_mod.open_question(new_state.focus.tasks)
    assert question["kind"] == task_mod.QUANTITIES
    assert question["owed"] == [1, 3, 4, 5]
    assert question["items"][1] == {"position": 2, "code": "SRTWC8152-SH-300", "qty": 6}


def test_a_code_with_a_quantity_on_the_entity_is_the_same_partial_answer():
    """"SRTWC8152-SH-300 x 6 Units Have stock?" read as `entities[].quantity`, the parser's
    other shape for the same answer (mode null): still that one line, never the list."""
    state = _state_with(_task([None] * 5))
    v = verdict(
        domain_hint="inventory",
        intent_hint="check_stock",
        entities=[ht.asked("SRTWC8152-SH-300", 6)],
    )
    new_state, plan = apply(state, v, build_policy())
    assert "task_partial_answered_stock_qty" in plan.trace.rules_fired
    assert plan.trace.task_question is None
    (spec,) = ht.inventory_specs(plan)
    assert [row["canonical_code"] for row in spec.entities] == ["SRTWC8152-SH-300"]
    assert [slot.value for slot in _stock_task(new_state).slots] == [None, 6, None, None, None]


def test_a_fragment_the_parser_resolved_onto_a_product_entity_is_that_line():
    """The parser may carry the typed fragment as the entity's raw ("8152") with the LISTED
    code as its canonical_code (the list-fragment addendum). That entity is line 2."""
    state = _state_with(_task([None] * 5))
    entity = ht.asked("8152", 6, canonical_code="SRTWC8152-SH-300")
    v = verdict(entities=[entity], open_question_answer=_fill((None, "SRTWC8152-SH-300", 6)))
    new_state, plan = apply(state, v, build_policy())
    assert "task_partial_answered_stock_qty" in plan.trace.rules_fired
    (spec,) = ht.inventory_specs(plan)
    assert [row["canonical_code"] for row in spec.entities] == ["SRTWC8152-SH-300"]
    assert plan.trace.task_question is None


def test_two_lines_filled_out_of_five_fetches_those_two():
    state = _state_with(_task([None] * 5))
    v = verdict(entities=[], open_question_answer=_fill((2, None, 6), (4, None, 10)))
    new_state, plan = apply(state, v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert [row["canonical_code"] for row in spec.entities] == ["SRTWC8152-SH-300", "SRTWC8357-300-RL"]
    assert spec.filters.get("requested_quantities") == {
        ht.uuid_of("SRTWC8152-SH-300"): 6,
        ht.uuid_of("SRTWC8357-300-RL"): 10,
    }
    assert [slot.value for slot in _stock_task(new_state).slots] == [None, 6, None, 10, None]


# --------------------------------------------------------------------------- #
# AC-3: after the tool answers, the task merges - owed lines kept, no re-ask
# --------------------------------------------------------------------------- #


def test_after_reply_keeps_the_owed_lines_and_says_no_question():
    task = _task([None, 6, None, None, None])
    envelopes = ht.envelopes(ht.row("SRTWC8152-SH-300", needs_quantity=False, requested_qty=6))
    reply = task_mod.after_reply((task,), envelopes, turn_no=4, asked=[], demand_qty=None)
    assert reply.text is None and reply.pick is None
    (merged,) = [t for t in reply.tasks if t.kind == "stock_qty"]
    assert merged.status == task_mod.OPEN
    assert [slot.label for slot in merged.slots] == PROD_LINES
    assert [slot.value for slot in merged.slots] == [None, 6, None, None, None]
    assert merged.opened_at_turn == task.opened_at_turn


def test_after_reply_marks_the_task_answered_once_the_last_line_is_answered():
    task = _task([3, 6, 2, 10, 1])
    envelopes = ht.envelopes(ht.row("SRTWC8366-300-RL-UF", needs_quantity=False, requested_qty=1))
    reply = task_mod.after_reply((task,), envelopes, turn_no=9, asked=[], demand_qty=None)
    (merged,) = [t for t in reply.tasks if t.kind == "stock_qty"]
    assert merged.status == task_mod.ANSWERED
    assert [slot.value for slot in merged.slots] == [3, 6, 2, 10, 1]


def test_a_partial_block_that_still_needs_a_quantity_is_not_merged():
    """Reviewer, 6 Oct 2026 (AC-3's "none `needs_quantity`" made explicit): the stock tool
    answers the partial fetch for line 2 but still marks that row `needs_quantity`. That is
    not a partial answer, so the task rebuilds from the block as on main (one line, open,
    its own "How many units of X?") and the noted 6 is not wiped into a re-printed list."""
    task = _task([None, 6, None, None, None])
    envelopes = ht.envelopes(ht.row("SRTWC8152-SH-300", needs_quantity=True, requested_qty=None))
    reply = task_mod.after_reply((task,), envelopes, turn_no=4, asked=[], demand_qty=None)
    (rebuilt,) = [t for t in reply.tasks if t.kind == "stock_qty"]
    assert rebuilt.status == task_mod.OPEN
    assert [slot.label for slot in rebuilt.slots] == ["SRTWC8152-SH-300"]
    assert reply.text == "How many units of SRTWC8152-SH-300?"
    assert LIST not in (reply.text or "")


def test_a_full_first_answer_still_rebuilds_from_the_block_alone():
    """Every line answered in one go (today's path): the block IS the task, nothing to
    carry, status ANSWERED - unchanged."""
    task = _task([None, None])  # two-line task, both answered by this reply
    task = ht.stock_task([("SRTWC8152-SH-300", 6), ("SRTWC8357-300-RL", 10)])
    envelopes = ht.envelopes(
        ht.row("SRTWC8152-SH-300", needs_quantity=False, requested_qty=6),
        ht.row("SRTWC8357-300-RL", needs_quantity=False, requested_qty=10),
    )
    reply = task_mod.after_reply((task,), envelopes, turn_no=4, asked=[], demand_qty=None)
    (merged,) = [t for t in reply.tasks if t.kind == "stock_qty"]
    assert merged.status == task_mod.ANSWERED
    assert [slot.value for slot in merged.slots] == [6, 10]


def test_a_parked_task_is_not_merged_into_a_new_ask_reply():
    """Q2 (a): the dealer asked about another product; the reply's block is that product's
    and the parked list stays parked, untouched."""
    parked = replace(_task([None, 6, None, None, None]), status=task_mod.PARKED)
    envelopes = ht.envelopes(ht.row("SRTWC9999", needs_quantity=False, requested_qty=5))
    reply = task_mod.after_reply((parked,), envelopes, turn_no=6, asked=[ht.asked("SRTWC9999", 5)], demand_qty=None)
    stock = [t for t in reply.tasks if t.kind == "stock_qty"]
    assert all(t.status != task_mod.OPEN or [s.label for s in t.slots] != PROD_LINES for t in stock)


# --------------------------------------------------------------------------- #
# AC-4 / AC-5: the next fills answer their own line alone
# --------------------------------------------------------------------------- #


def test_the_next_fill_answers_its_line_alone_not_the_earlier_one():
    state = _state_with(_task([None, 6, None, None, None]), turn_no=5)
    v = verdict(entities=[], open_question_answer=_fill((None, "SRTWC8357-300-RL", 10)))
    new_state, plan = apply(state, v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert [row["canonical_code"] for row in spec.entities] == ["SRTWC8357-300-RL"]
    assert spec.filters.get("requested_quantities") == {ht.uuid_of("SRTWC8357-300-RL"): 10}
    assert [slot.value for slot in _stock_task(new_state).slots] == [None, 6, None, 10, None]
    assert plan.trace.task_question is None


def test_a_bare_number_fills_and_fetches_the_owed_lines_only():
    """Q3 (a), F2 kept: "10" over lines 1, 3, 4, 5 still owed fills those four and fetches
    them; line 2 (answered at 6 earlier) is not fetched again."""
    state = _state_with(_task([None, 6, None, None, None]), turn_no=5)
    v = verdict(demand_qty=10, entities=[])
    new_state, plan = apply(state, v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert [row["canonical_code"] for row in spec.entities] == [
        "BRC21466UW-2-MY", "SRTWC8152-SH-UF-300", "SRTWC8357-300-RL", "SRTWC8366-300-RL-UF",
    ]
    assert [slot.value for slot in _stock_task(new_state).slots] == [10, 6, 10, 10, 10]
    assert plan.trace.task_question is None


def test_restating_an_answered_line_reanswers_that_line_alone():
    """"8152 x 6" again (the owner's own frustrated repeat): nothing changed on the task,
    and the answer is that line again, never the list."""
    state = _state_with(_task([None, 6, None, None, None]), turn_no=5)
    v = verdict(entities=[], open_question_answer=_fill((None, "SRTWC8152-SH-300", 6)))
    new_state, plan = apply(state, v, build_policy())
    assert plan.trace.task_question is None
    (spec,) = ht.inventory_specs(plan)
    assert [row["canonical_code"] for row in spec.entities] == ["SRTWC8152-SH-300"]
    assert [slot.value for slot in _stock_task(new_state).slots] == [None, 6, None, None, None]


def test_changing_an_answered_line_reanswers_it_at_the_new_quantity():
    state = _state_with(_task([None, 6, None, None, None]), turn_no=5)
    v = verdict(entities=[], open_question_answer=_fill((2, None, 10)))
    new_state, plan = apply(state, v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert spec.filters.get("requested_quantities") == {ht.uuid_of("SRTWC8152-SH-300"): 10}
    assert [slot.value for slot in _stock_task(new_state).slots] == [None, 10, None, None, None]


# --------------------------------------------------------------------------- #
# AC-7: the explicit instructions keep their rulings
# --------------------------------------------------------------------------- #


def test_all_still_fills_every_line_at_once_the_answered_one_included():
    """"3 for all" (mode all, 26 Sep F2, `apply.py` unchanged): "all" is the dealer's own
    word for every line, so the line answered earlier at 6 is re-noted at 3 and fetched
    with the rest. Only a BARE number is limited to the owed lines (Q3 (a), below)."""
    state = _state_with(_task([None, 6, None, None, None]), turn_no=5)
    v = verdict(entities=[], open_question_answer={"mode": "all", "items": [], "qty_for_all": 3})
    new_state, plan = apply(state, v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert spec.filters.get("requested_quantities") == {ht.uuid_of(c): 3 for c in PROD_LINES}
    assert [slot.value for slot in _stock_task(new_state).slots] == [3, 3, 3, 3, 3]


def test_done_with_blanks_still_drops_and_names_the_rest():
    state = _state_with(_task([None] * 5))
    v = verdict(
        entities=[],
        open_question_answer={
            "mode": "done",
            "items": [{"position": 2, "code": None, "qty": 6}],
            "qty_for_all": None,
        },
    )
    _new_state, plan = apply(state, v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert spec.filters.get("requested_quantities") == {ht.uuid_of("SRTWC8152-SH-300"): 6}
    assert set(spec.filters.get("not_checked") or []) == set(PROD_LINES) - {"SRTWC8152-SH-300"}


def test_just_proceed_with_nothing_noted_still_ends_the_task():
    state = _state_with(_task([None] * 5))
    v = verdict(entities=[], proceed_anyway=True)
    new_state, plan = apply(state, v, build_policy())
    assert plan.fetch == []
    assert plan.trace.task_question.startswith("Not checked: ")
    assert not any(t.kind == "stock_qty" for t in new_state.focus.tasks)


def test_a_one_line_task_still_asks_its_one_question():
    task = ht.stock_task([("SRTWC8152-SH-300", None)])
    assert task_mod.StockQtyTask().question(task) == "How many units of SRTWC8152-SH-300?"


def test_a_resume_that_names_nothing_asks_only_what_is_still_owed():
    """"back to the stock check" over the mixed task: the resume arm (unchanged) asks the
    owed lines as point form - the dealer asked for the question back."""
    state = _state_with(_task([None, 6, None, None, None]), turn_no=7)
    v = verdict(domain_hint="inventory", intent_hint="check_stock", entities=[])
    _new_state, plan = apply(state, v, build_policy())
    assert "task_resumed_stock_qty" in plan.trace.rules_fired
    assert plan.trace.task_question.splitlines()[0] == "How many units for each?"


# --------------------------------------------------------------------------- #
# AC-6: the 6 Oct transcript through the Console, after the fix
# --------------------------------------------------------------------------- #


def test_the_owner_transcript_of_6_oct_after_the_fix():
    console = _console_after_the_list()
    text = console.say(
        "I need 8152 6 unit",
        verdict(entities=[], open_question_answer=_fill((None, "SRTWC8152-SH-300", 6))),
    )
    assert text == _answered({"SRTWC8152-SH-300": 6})
    task = _stock_task(console.state)
    assert task.status == task_mod.OPEN
    assert [slot.value for slot in task.slots] == [None, 6, None, None, None]

    text = console.say(
        "8357 x 10",
        verdict(entities=[], open_question_answer=_fill((None, "SRTWC8357-300-RL", 10))),
    )
    assert text == _answered({"SRTWC8357-300-RL": 10})
    assert [slot.value for slot in _stock_task(console.state).slots] == [None, 6, None, 10, None]

    # The frustrated repeat: the same one line again, never the list.
    text = console.say(
        "SRTWC8152-SH-300 x 6 Units Have stock?",
        verdict(domain_hint="inventory", intent_hint="check_stock", entities=[ht.asked("SRTWC8152-SH-300", 6)]),
    )
    assert text == _answered({"SRTWC8152-SH-300": 6})

    # One number for the rest (Q3 (a)).
    text = console.say("10", verdict(demand_qty=10, entities=[]))
    assert text == _answered({"BRC21466UW-2-MY": 10, "SRTWC8152-SH-UF-300": 10, "SRTWC8366-300-RL-UF": 10})
    assert _stock_task(console.state).status == task_mod.ANSWERED
    assert "How many units for each?" not in "\n".join(console.transcript[2:])


def test_a_new_product_ask_over_the_open_lines_proceeds_and_parks_them():
    """Q2 (a): "stock SRTWC9999 x 5" is its own stock ask; the open list is parked, not
    re-printed, and the answer is that product's."""
    console = _console_after_the_list()
    console.say(
        "I need 8152 6 unit",
        verdict(entities=[], open_question_answer=_fill((None, "SRTWC8152-SH-300", 6))),
    )
    text = console.say(
        "stock SRTWC9999 x 5",
        verdict(
            domain_hint="inventory",
            intent_hint="check_stock",
            domain_in_message=True,
            entities=[ht.asked("SRTWC9999", 5)],
        ),
    )
    assert text == _answered({"SRTWC9999": 5})
    assert "How many units for each?" not in text
    assert "task_parked_stock_qty" in console.plans[-1].trace.rules_fired
    # At most one stock task per conversation (R1 scope cut): the new ask's own answered
    # check is the task now, and the old list is gone, not re-printed. A later
    # "8357 x 10" is a plain stock ask for that code and gets the same stock lines.
    task = _stock_task(console.state)
    assert task.status == task_mod.ANSWERED
    assert [s.label for s in task.slots] == ["SRTWC9999"]


@pytest.mark.parametrize("shape", ["position", "code"])
def test_the_list_is_never_repeated_after_any_partial_answer(shape: str):
    console = _console_after_the_list()
    for position, code, qty in ((2, "SRTWC8152-SH-300", 6), (4, "SRTWC8357-300-RL", 10), (1, "BRC21466UW-2-MY", 2)):
        item = (position, None, qty) if shape == "position" else (None, code, qty)
        text = console.say(f"{code} x {qty}", verdict(entities=[], open_question_answer=_fill(item)))
        assert text == _answered({code: qty})
    assert not any(line.startswith("-> How many units") for line in console.transcript[2:])
