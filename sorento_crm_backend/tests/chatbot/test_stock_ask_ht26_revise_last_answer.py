"""Owner hand test 26 Sep 2026 (PR #1247), slice 5: revise the last answer.

T7 "srtgv332-diy 20pcs" was answered (SRTGV332-DIY x 20). T8 "how about 100?" then
parsed as a product "100" the parser was not confident of, beside demand_qty 100, and
the spec tier matched four products to it ("4 products have stock."). Even the ideal
emission (entities [], demand_qty 100) fetched SRTGV332-DIY with no quantity and asked
"How many units do you need?" again, because nothing kept what was answered. T12/T13
did the same for ELP3754 x 10 -> "how about 100?".

Four parts, one test group each: the answered check is KEPT on the focus, the parser is
told about it ("Last answered: ..."), a bare number revises it (the engine half, which
needs no prompt change), and the digits guard in `_normalise_demand_qty` turns T8's
observed emission into the ideal one. The prompt rule is asserted to exist; it goes live
only when the owner moves the `production` label of `chatbot_semantic_parser` onto a
version that carries it.
"""
from __future__ import annotations

from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.state import Focus, focus_from_wire, focus_to_wire

from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import build_policy, verdict


def _answered(code: str, qty: int):
    return task_mod.tasks_after_reply(
        (),
        ht.envelopes(ht.row(code, needs_quantity=False, requested_qty=qty, branch="in_stock")),
        turn_no=7,
    )


def _after(code: str, qty: int, *, turn_no: int = 8):
    tasks = _answered(code, qty)
    return ht.state(
        Focus(tasks=tasks, domains=["inventory"], products=ht.product_rows(code)),
        turn_no=turn_no,
    )


# --------------------------------------------------------------------------- #
# The answered check is kept, and the parser is told
# --------------------------------------------------------------------------- #


def test_t7_answered_check_is_kept_as_answered():
    (task,) = _answered("SRTGV332-DIY", 20)
    assert task.status == task_mod.ANSWERED
    assert [(s.label, s.value) for s in task.slots] == [("SRTGV332-DIY", 20)]


def test_t7_hint_line_says_what_was_last_answered():
    assert task_mod.hint_lines(_answered("SRTGV332-DIY", 20)) == [
        "Last answered: SRTGV332-DIY x 20."
    ]


def test_answered_status_survives_the_session_wire():
    focus = Focus(tasks=_answered("SRTGV332-DIY", 20))
    back = focus_from_wire(focus_to_wire(focus))
    assert back.tasks[0].status == task_mod.ANSWERED


def test_the_answered_check_asks_nothing():
    from app.services.chatbot.engine import _stock_ask_reply
    from app.services.chatbot.turn import compose as turn_compose

    class _Spec:
        domain, entities, filters = "inventory", [{"uuid": "x"}], {}

    class _Plan:
        fetch = [_Spec()]

    composed = turn_compose.Answer(text="SRTGV332-DIY x 20: yes, we have stock.")
    state_out = ht.state()
    out = _stock_ask_reply(
        composed,
        state_out,
        ht.envelopes(ht.row("SRTGV332-DIY", needs_quantity=False, requested_qty=20)),
        _Plan(),
        verdict(entities=[ht.asked("SRTGV332-DIY", quantity=20)]),
        turn_no=7,
    )
    assert out is composed
    assert state_out.focus.tasks[0].status == task_mod.ANSWERED


# --------------------------------------------------------------------------- #
# A bare number revises it (engine half, no prompt change needed)
# --------------------------------------------------------------------------- #


def _assert_fetches(plan, code: str, qty: int):
    specs = ht.inventory_specs(plan)
    assert len(specs) == 1, plan.fetch
    assert [e["uuid"] for e in specs[0].entities] == [ht.uuid_of(code)]
    assert specs[0].filters.get("requested_quantities") == {ht.uuid_of(code): qty}
    assert "task_revised_stock_qty" in plan.trace.rules_fired


def test_t8_ideal_emission_revises_to_one_hundred():
    _state2, plan = apply(
        _after("SRTGV332-DIY", 20),
        verdict(entities=[], demand_qty=100, correction=True, domain_hint="inventory"),
        build_policy(),
    )
    _assert_fetches(plan, "SRTGV332-DIY", 100)


def test_t8_observed_emission_is_guarded_and_revises_to_one_hundred():
    v = verdict(
        entities=[
            {
                "raw": "100",
                "hint": "product",
                "canonical_code": "100",
                "current_message": True,
                "confident": False,
            }
        ],
        demand_qty=100,
        domain_hint="inventory",
    )
    _state2, plan = apply(_after("SRTGV332-DIY", 20), v, build_policy())
    assert v["entities"] == [], "the guard drops the not-confident '100' before the resolver"
    _assert_fetches(plan, "SRTGV332-DIY", 100)


def test_t13_elp3754_ten_then_how_about_one_hundred():
    _state2, plan = apply(
        _after("ELP3754", 10, turn_no=13),
        verdict(entities=[], demand_qty=100, correction=True),
        build_policy(),
    )
    _assert_fetches(plan, "ELP3754", 100)


def test_the_revised_reply_is_kept_as_the_new_last_answer():
    tasks = task_mod.tasks_after_reply(
        _after("SRTGV332-DIY", 20).focus.tasks,
        ht.envelopes(
            ht.row("SRTGV332-DIY", needs_quantity=False, requested_qty=100, branch="too_big")
        ),
        turn_no=8,
    )
    assert task_mod.hint_lines(tasks) == ["Last answered: SRTGV332-DIY x 100."]


def test_a_new_ask_after_an_answered_check_closes_it():
    state2, plan = apply(
        _after("SRTGV332-DIY", 20),
        verdict(domain_hint="inventory", entities=[ht.asked("ELP3754", quantity=5)]),
        build_policy(),
    )
    assert "task_answered_closed_stock_qty" in plan.trace.rules_fired
    assert state2.focus.tasks == ()
    assert "task_revised_stock_qty" not in plan.trace.rules_fired


def test_a_bare_number_after_a_two_product_answer_revises_nothing():
    tasks = task_mod.tasks_after_reply(
        (),
        ht.envelopes(
            ht.row("ELP3754", needs_quantity=False, requested_qty=10),
            ht.row("SRTKT1631SS", needs_quantity=False, requested_qty=20),
        ),
        turn_no=14,
    )
    focus = Focus(tasks=tasks, domains=["inventory"])
    _state2, plan = apply(ht.state(focus), verdict(entities=[], demand_qty=100), build_policy())
    assert "task_revised_stock_qty" not in plan.trace.rules_fired


def test_the_guard_keeps_a_confident_digit_code():
    v = verdict(
        entities=[
            {
                "raw": "7445",
                "hint": "product",
                "canonical_code": "7445",
                "current_message": True,
                "confident": True,
            }
        ],
        demand_qty=7445,
    )
    apply(ht.state(), v, build_policy())
    assert [e["raw"] for e in v["entities"]] == ["7445"]


# --------------------------------------------------------------------------- #
# The parser prompt rule (live only once the production label moves)
# --------------------------------------------------------------------------- #


def test_the_stock_task_addendum_teaches_the_last_answered_line():
    from app.services.chatbot_parser_prompt import (
        ESCALATION_CONFIRMATION_ADDENDUM,
        SELF_REFERENCE_ADDENDUM,
        KNOWN_BRANDS_ADDENDUM,
        ACCOUNT_LEDGER_ADDENDUM,
        MEMORY_ADDENDUM,
        PO_SPO_WAREHOUSE_ADDENDUM,
        QUANTITY_ADDENDUM,
        SALES_ANALYSIS_ADDENDUM,
        SEMANTIC_PARSER_PROMPT,
        SPECIFICATION_ADDENDUM,
        STOCK_TASK_ADDENDUM,
        TOP_SELLING_ADDENDUM,
    )

    assert '"Last answered:"' in STOCK_TASK_ADDENDUM
    assert '"how about 100?"' in STOCK_TASK_ADDENDUM
    assert "correction true" in STOCK_TASK_ADDENDUM
    # SALES_ANALYSIS_ADDENDUM (#1267 S1) stacks after this one, then SPECIFICATION_ADDENDUM
    # (fix round 8 on PR #833), then #1262's QUANTITY_ADDENDUM and KNOWN_BRANDS_ADDENDUM,
    # then the top selling addendum (PR #1273), then #1323's
    # ESCALATION_CONFIRMATION_ADDENDUM, then chatbot memory lane A's MEMORY_ADDENDUM,
    # newest outermost.
    assert (
        SEMANTIC_PARSER_PROMPT.removesuffix(MEMORY_ADDENDUM).removesuffix(ACCOUNT_LEDGER_ADDENDUM).removesuffix(PO_SPO_WAREHOUSE_ADDENDUM)
        .removesuffix(ESCALATION_CONFIRMATION_ADDENDUM).removesuffix(SELF_REFERENCE_ADDENDUM)
        .removesuffix(TOP_SELLING_ADDENDUM)
        .removesuffix(KNOWN_BRANDS_ADDENDUM)
        .removesuffix(QUANTITY_ADDENDUM)
        .removesuffix(SPECIFICATION_ADDENDUM)
        .removesuffix(SALES_ANALYSIS_ADDENDUM)
        .endswith(STOCK_TASK_ADDENDUM)
    )
