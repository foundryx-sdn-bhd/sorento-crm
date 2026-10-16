"""F3 (crew GO 16 Oct 2026): a delivered one-line stock answer is stated to the parser
once. "Last answered: X x N." stays; the `Open question: {"kind":"last_answer"}` object
is dropped for ONE line (it made the parser treat the delivered answer as a question to
answer). A multi-line answered check keeps the object byte for byte. A one-line
revision ("3 for all"-style) is read from demand_qty, so it needs no object."""
from __future__ import annotations

from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot.turn import question as question_mod
from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.state import Focus

from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import build_policy, verdict


def _block(tasks) -> str:
    focus = Focus(tasks=tasks, domains=["inventory"])
    oq = question_mod.open_question(None, tasks)
    lines = parser_mod.open_task_lines(focus, oq)
    oq_line = parser_mod.open_question_line(oq)
    return "\n".join([*lines, *([oq_line] if oq_line else [])])


def test_one_line_answered_check_prints_last_answered_and_no_object():
    tasks = (ht.stock_task([("SRTGV332-DIY", 20)], status=task_mod.ANSWERED),)
    assert task_mod.open_question(tasks) is None
    assert question_mod.open_question(None, tasks) is None
    block = _block(tasks)
    assert "Last answered: SRTGV332-DIY x 20." in block
    assert "Open question" not in block
    assert "last_answer" not in block


def test_multi_line_answered_check_keeps_the_object_exactly():
    tasks = (ht.stock_task([("SRTWC286-SH", 10), ("SRTWC286-SH-150", 5)], status=task_mod.ANSWERED),)
    assert task_mod.open_question(tasks) == {
        "kind": "last_answer",
        "items": [
            {"position": 1, "code": "SRTWC286-SH", "qty": 10},
            {"position": 2, "code": "SRTWC286-SH-150", "qty": 5},
        ],
        "owed": [],
    }
    block = _block(tasks)
    assert 'Open question: {"kind":"last_answer","items":[{"position":1,"code":"SRTWC286-SH","qty":10},{"position":2,"code":"SRTWC286-SH-150","qty":5}],"owed":[]}' in block
    assert "Last answered:" in block


def test_one_line_open_task_is_still_a_quantities_object():
    tasks = (ht.stock_task([("SRTGV332-DIY", None)]),)
    assert task_mod.open_question(tasks)["kind"] == "quantities"


def test_one_line_revision_reads_demand_qty_without_the_object():
    answered = task_mod.tasks_after_reply(
        (),
        ht.envelopes(ht.row("SRTGV332-DIY", needs_quantity=False, requested_qty=20, branch="in_stock")),
        turn_no=7,
    )
    assert question_mod.open_question(None, answered) is None
    state = ht.state(
        Focus(tasks=answered, domains=["inventory"], products=ht.product_rows("SRTGV332-DIY")),
        turn_no=8,
    )
    _s, plan = apply(
        state,
        verdict(entities=[], demand_qty=3, correction=True, domain_hint="inventory"),
        build_policy(),
    )
    specs = ht.inventory_specs(plan)
    assert len(specs) == 1
    assert specs[0].filters.get("requested_quantities") == {ht.uuid_of("SRTGV332-DIY"): 3}
    assert "task_revised_stock_qty" in plan.trace.rules_fired


def test_first_message_has_no_object_and_no_line():
    assert question_mod.open_question(None, ()) is None
    assert _block(()) == ""
