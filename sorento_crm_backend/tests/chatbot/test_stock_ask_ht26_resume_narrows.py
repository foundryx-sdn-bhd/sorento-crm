"""Owner hand test 26 Sep 2026 (PR #1247), slice 3: a resume that names some of an open
task's products narrows the task to them.

T5 "check stock SRTWC286-SH-UF" over the ten-slot SRTWC286-SH* task T1 left open (the
focus state the orchestrator's trace check confirmed: `task_resumed_stock_qty` fired and
nothing was fetched) re-asked all ten. It now asks only for the product named, and T6
"10" then answers it. A task written before slice 2 (sessions already holding a
ten-slot family task) is exactly this fixture.
"""
from __future__ import annotations

from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.state import Focus

from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import build_policy, verdict


def _family_task():
    return ht.stock_task([(code, None) for code in ht.SRTWC286_FAMILY])


def test_t5_naming_one_product_of_the_task_narrows_it_and_asks_only_that():
    focus = Focus(tasks=(_family_task(),), domains=["inventory"])
    state2, plan = apply(
        ht.state(focus),
        verdict(domain_hint="inventory", entities=[ht.asked("SRTWC286-SH-UF")]),
        build_policy(),
    )

    assert plan.fetch == []
    assert "task_resumed_stock_qty" in plan.trace.rules_fired
    assert "task_narrowed_stock_qty" in plan.trace.rules_fired
    assert plan.trace.task_question == "How many units of SRTWC286-SH-UF?"
    (task,) = state2.focus.tasks
    assert [s.label for s in task.slots] == ["SRTWC286-SH-UF"]


def test_t5_then_t6_ten_answers_the_narrowed_product():
    focus = Focus(tasks=(_family_task(),), domains=["inventory"])
    state2, _plan = apply(
        ht.state(focus),
        verdict(domain_hint="inventory", entities=[ht.asked("SRTWC286-SH-UF")]),
        build_policy(),
    )
    state3, plan = apply(
        ht.state(state2.focus, turn_no=6),
        verdict(demand_qty=10, entities=[]),
        build_policy(),
    )

    specs = ht.inventory_specs(plan)
    assert len(specs) == 1
    assert specs[0].filters.get("requested_quantities") == {ht.uuid_of("SRTWC286-SH-UF"): 10}
    assert [e["uuid"] for e in specs[0].entities] == [ht.uuid_of("SRTWC286-SH-UF")]


def test_naming_two_of_the_task_keeps_both_in_task_order():
    focus = Focus(tasks=(_family_task(),), domains=["inventory"])
    state2, plan = apply(
        ht.state(focus),
        verdict(
            domain_hint="inventory",
            entities=[ht.asked("SRTWC286-SH-GD"), ht.asked("SRTWC286-SH-UF")],
        ),
        build_policy(),
    )
    (task,) = state2.focus.tasks
    assert [s.label for s in task.slots] == ["SRTWC286-SH-UF", "SRTWC286-SH-GD"]
    # Round 6, ruling 2: point form, one numbered line per product.
    assert plan.trace.task_question == (
        "How many units for each?\n1. SRTWC286-SH-UF - \n2. SRTWC286-SH-GD - "
    )


def test_a_resume_that_names_nothing_still_asks_the_whole_task():
    # STUCK-QTY-LOOP (owner, 4 Oct 2026): "back to the stock check" carries the stock
    # intent; an inventory message with no intent and nothing named no longer resumes
    # (it replayed the stored question on a low stock report and an unflagged "clear").
    task = ht.stock_task([("ELP3754", None), ("SRTKT1631SS", None)])
    state2, plan = apply(
        ht.state(Focus(tasks=(task,), domains=["inventory"])),
        verdict(domain_hint="inventory", intent_hint="check_stock", entities=[]),
        build_policy(),
    )
    (kept,) = state2.focus.tasks
    assert [s.label for s in kept.slots] == ["ELP3754", "SRTKT1631SS"]
    assert "task_narrowed_stock_qty" not in plan.trace.rules_fired
