"""Owner hand test of round 3 (26 Sep 2026 ~07:25Z, :3087 console, head 40cb26022,
prompt v32), PR #1247. The owner's five turns, replayed through the real turn engine.

Transcript (verbatim, observed):

    check stock srtwc286
    -> SRTWC286 matches 10 products. Which one?   (ten codes, one per line, no numbers)
    1
    -> How many units of SRTWC286-SH?
    10
    -> SRTWC286-SH x 10: 🚫 the quantity is more than what I can confirm here, ...
    2
    -> How many units of SRTWC286-SH?              <- the defect
    3
    -> SRTWC286-SH x 3: 🚫 the quantity is more than what I can confirm here, ...

Rulings:
1. A which-one pick list is numbered ("1. SRTWC286-SH"), like the other pickers
   (`turn/compose.py::compose_question`, `f"{position}. {label}"`).
2. After a quantity has been answered, a bare number is a revised quantity for that
   same product ("2" -> SRTWC286-SH x 2, answered straight away), never a pick from the
   old list and never a re-ask. A real new pick is a code or a new "check stock"
   (round 5, owner ruling 26 Sep ~08:25Z: the picker is not sticky).

Only the parser is stubbed. Each turn's verdict is the one the live parser's reading
has to have been for the observed reply to come out of this engine: of every emission
for "2" replayed here (`demand_qty` 2; `reference_positions` [2]; both; `demand_qty`
beside the carried product), only `reference_positions: [2]` with no quantity
reproduces "How many units of SRTWC286-SH?" on the pre-fix engine. Everything after the
parser is the real code: `turn/apply.py::apply`, the tail's open-question carry
(`engine._write_session`'s "the lane's own question, else the one APPLY carried") and
`engine._stock_ask_reply` over the stock tool's `stock_availability` block.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.services.chatbot.turn import compose as turn_compose
from app.services.chatbot.turn import pending as turn_pending
from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.state import Focus

from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import build_policy, verdict

#: The ten codes the owner's console printed, in its order.
OWNER_FAMILY = [
    "SRTWC286-SH",
    "SRTWC286-SH-150",
    "SRTWC286-SH-200",
    "SRTWC286-SH-NEW",
    "SRTWC286-SH-NEW-150",
    "SRTWC286-SH-NEW-200",
    "SRTWC286-SH-NEW-P",
    "SRTWC286-SH-P",
    "SRTWC286-SH-PP",
    "SRTWC286-SH-UF",
]

TOO_BIG = "🚫 the quantity is more than what I can confirm here. Please refer to your salesman."


def _tool(plan) -> tuple[list[dict[str, Any]], str]:
    """The stock tool for an availability-only contact: one entry per product, needing
    a quantity unless the fetch carried one (the spec's own `requested_quantities`, else
    the quantity this message stated beside the code - `turn_runtime._spec_quantities`).
    Every quantity here is more than the fixture's stock, so an answered entry is the
    presenter's R14 "too big" line."""
    rows = []
    for spec in ht.inventory_specs(plan):
        carried = dict(spec.filters.get("requested_quantities") or {})
        for entity in spec.entities:
            qty = carried.get(entity.get("uuid"))
            if qty is None:
                qty = entity.get("quantity")
            rows.append(
                ht.row(
                    entity["canonical_code"],
                    needs_quantity=qty is None,
                    requested_qty=qty,
                    branch="too_big" if qty is not None else None,
                )
            )
    answered = [r for r in rows if not r["needs_quantity"]]
    text = (
        "\n".join(f"{r['product_code']} x {r['requested_qty']}: {TOO_BIG}" for r in answered)
        if answered
        else "How many units do you need?"
    )
    return ht.envelopes(*rows), text


class Console:
    """One dealer conversation, turn by turn, the way `engine.run_turn` sequences it."""

    def __init__(self) -> None:
        self.state = ht.state(availability_only=True, turn_no=0)
        self.transcript: list[str] = []
        self.plans: list[Any] = []

    def _reply(self, applied, plan, v, turn_no):
        from app.services.chatbot.engine import _stock_ask_reply

        if plan.trace.task_question:
            return plan.trace.task_question, None
        if plan.ask is not None:
            answer = turn_compose.compose_question(plan.ask, applied)
            return answer.text, answer.question
        envelopes, text = _tool(plan)
        answer = _stock_ask_reply(
            turn_compose.Answer(text=text), applied, envelopes, plan, v, turn_no=turn_no
        )
        return answer.text, answer.question

    def first_ask(self, message: str) -> str:
        """T1 "check stock srtwc286": the resolver placed the ten family rows, and the
        stock tool answered each of them needing a quantity."""
        from app.services.chatbot.engine import _stock_ask_reply

        focus = Focus(products=ht.product_rows(*OWNER_FAMILY), domains=["inventory"])
        applied = replace(self.state, focus=focus, turn_no=1)

        class _Spec:
            domain = "inventory"
            entities = ht.product_rows(*OWNER_FAMILY)
            filters: dict[str, Any] = {}

        class _Plan:
            fetch = [_Spec()]
            trace = None

        answer = _stock_ask_reply(
            turn_compose.Answer(text="How many units do you need?"),
            applied,
            ht.envelopes(*[ht.row(code) for code in OWNER_FAMILY]),
            _Plan(),
            verdict(domain_hint="inventory", entities=[ht.asked("srtwc286")]),
            turn_no=1,
        )
        self.state = replace(applied, pending=answer.question)
        self.transcript += [message, f"-> {answer.text}"]
        return answer.text

    def say(self, message: str, v: dict[str, Any]) -> str:
        turn_no = self.state.turn_no + 1
        loaded = replace(self.state, pending=turn_pending.tick(self.state.pending), turn_no=turn_no)
        applied, plan = apply(loaded, v, build_policy())
        text, question = self._reply(applied, plan, v, turn_no)
        self.state = replace(
            applied, pending=question if question is not None else applied.pending
        )
        self.plans.append(plan)
        self.transcript += [message, f"-> {text}"]
        return text


def _numbered(head: str, codes: list[str]) -> str:
    return "\n".join([head, *[f"{i}. {code}" for i, code in enumerate(codes, 1)]])


# --------------------------------------------------------------------------- #
# The owner's five turns, as observed on the live parser
# --------------------------------------------------------------------------- #


def _owner_run(two: dict[str, Any], three: dict[str, Any]) -> Console:
    console = Console()
    console.first_ask("check stock srtwc286")
    console.say("1", verdict(reference_positions=[1], entities=[]))
    console.say("10", verdict(demand_qty=10, entities=[]))
    console.say("2", two)
    console.say("3", three)
    return console


def test_owner_replay_after_the_fix():
    console = _owner_run(
        verdict(reference_positions=[2], entities=[]),
        verdict(demand_qty=3, entities=[]),
    )
    assert console.transcript == [
        "check stock srtwc286",
        "-> " + _numbered("SRTWC286 matches 10 products. Which one?", OWNER_FAMILY),
        "1",
        "-> How many units of SRTWC286-SH?",
        "10",
        f"-> SRTWC286-SH x 10: {TOO_BIG}",
        "2",
        f"-> SRTWC286-SH x 2: {TOO_BIG}",
        "3",
        f"-> SRTWC286-SH x 3: {TOO_BIG}",
    ]


def test_owner_turn_two_revises_the_quantity_and_picks_nothing():
    console = _owner_run(
        verdict(reference_positions=[2], entities=[]),
        verdict(demand_qty=3, entities=[]),
    )
    plan = console.plans[2]  # the "2" turn
    assert "bare_number_is_the_quantity" in plan.trace.rules_fired
    assert "task_revised_stock_qty" in plan.trace.rules_fired
    (spec,) = ht.inventory_specs(plan)
    assert [e["canonical_code"] for e in spec.entities] == ["SRTWC286-SH"]
    assert spec.filters.get("requested_quantities") == {ht.uuid_of("SRTWC286-SH"): 2}


def test_owner_replay_with_the_quantity_emission_reads_the_same():
    """The other reading of "2" the parser may give (the prompt's "Last answered:" rule):
    the same transcript."""
    console = _owner_run(
        verdict(demand_qty=2, entities=[]),
        verdict(demand_qty=3, entities=[]),
    )
    assert console.transcript[7] == f"-> SRTWC286-SH x 2: {TOO_BIG}"
    assert console.transcript[9] == f"-> SRTWC286-SH x 3: {TOO_BIG}"


def test_a_bare_position_after_an_answered_quantity_twice_revises_twice():
    console = _owner_run(
        verdict(reference_positions=[2], entities=[]),
        verdict(reference_positions=[3], entities=[]),
    )
    assert console.transcript[7] == f"-> SRTWC286-SH x 2: {TOO_BIG}"
    assert console.transcript[9] == f"-> SRTWC286-SH x 3: {TOO_BIG}"


def test_a_bare_position_while_the_quantity_is_still_asked_is_that_quantity():
    """"How many units of SRTWC286-SH?" then "10" read as position 10: still the
    quantity, never the 10th code of a list nobody is asking about any more."""
    console = Console()
    console.first_ask("check stock srtwc286")
    console.say("1", verdict(reference_positions=[1], entities=[]))
    text = console.say("10", verdict(reference_positions=[10], entities=[]))
    assert text == f"SRTWC286-SH x 10: {TOO_BIG}"


# --------------------------------------------------------------------------- #
# After the pick: a code is a new ask, a number is never a pick
# --------------------------------------------------------------------------- #


def test_no_the_second_one_after_the_pick_is_the_quantity_not_a_pick():
    """Round 4 reopened the list here. Owner ruling 26 Sep ~08:25Z (round 5): the picker
    is not sticky, so the old list is forgotten and the number revises the quantity."""
    console = Console()
    console.first_ask("check stock srtwc286")
    console.say("1", verdict(reference_positions=[1], entities=[]))
    console.say("10", verdict(demand_qty=10, entities=[]))
    text = console.say(
        "no, the 2nd one", verdict(reference_positions=[2], is_affirmative=False, entities=[])
    )
    assert text == f"SRTWC286-SH x 2: {TOO_BIG}"
    assert "stock_pick_reopened" not in console.plans[-1].trace.rules_fired
    text = console.say("5", verdict(reference_positions=[5], entities=[]))
    assert text == f"SRTWC286-SH x 5: {TOO_BIG}"


def test_naming_a_code_after_an_answered_quantity_asks_about_that_code():
    console = Console()
    console.first_ask("check stock srtwc286")
    console.say("1", verdict(reference_positions=[1], entities=[]))
    console.say("10", verdict(demand_qty=10, entities=[]))
    loaded = replace(console.state, turn_no=4)
    _state2, plan = apply(
        loaded,
        verdict(domain_hint="inventory", entities=[ht.asked("SRTWC286-SH-UF")]),
        build_policy(),
    )
    assert "bare_number_is_the_quantity" not in plan.trace.rules_fired
    assert "stock_pick_reopened" not in plan.trace.rules_fired
    assert "task_answered_closed_stock_qty" in plan.trace.rules_fired


def test_a_plain_no_after_an_answered_quantity_reopens_nothing():
    console = Console()
    console.first_ask("check stock srtwc286")
    console.say("1", verdict(reference_positions=[1], entities=[]))
    console.say("10", verdict(demand_qty=10, entities=[]))
    loaded = replace(console.state, turn_no=4)
    state2, plan = apply(loaded, verdict(is_affirmative=False, entities=[]), build_policy())
    assert "stock_pick_reopened" not in plan.trace.rules_fired
    assert state2.pending is None


def test_a_position_under_an_open_roster_of_another_kind_is_left_to_it():
    """The bare-number rule is for a stock check with NOTHING open to pick from."""
    task = replace(
        ht.stock_task([("SRTWC286-SH", 10)], status=task_mod.ANSWERED),
    )
    roster = turn_pending.ask(
        "customer_pick",
        [{"position": 1, "label": "HANLIM", "uuid": "c-1", "entity_type": "customer"},
         {"position": 2, "label": "GOLDEN WIN", "uuid": "c-2", "entity_type": "customer"}],
        payload={"domain": "order", "domains": ["order"]},
    )
    _state2, plan = apply(
        ht.state(Focus(tasks=(task,), domains=["order"]), pending=roster),
        verdict(reference_positions=[2], entities=[]),
        build_policy(),
    )
    assert "bare_number_is_the_quantity" not in plan.trace.rules_fired
    assert "answer_pending" in plan.trace.rules_fired


# --------------------------------------------------------------------------- #
# Ruling 1: the which-one list is numbered
# --------------------------------------------------------------------------- #


def test_the_family_pick_is_numbered_one_code_per_line():
    text = task_mod.pick_question("SRTWC286", OWNER_FAMILY)
    assert text == _numbered("SRTWC286 matches 10 products. Which one?", OWNER_FAMILY)


def test_the_family_pick_with_a_quantity_is_numbered_too():
    text = task_mod.pick_question("SRTWC286", OWNER_FAMILY[:3], 88)
    assert text == _numbered("SRTWC286 x 88: which one?", OWNER_FAMILY[:3])


def test_more_than_ten_numbers_every_one():
    """PICKER-NO-CAP (owner, 2 Oct 2026): no cut at ten, and no "others" line when
    every match is listed."""
    codes = [f"SRTX1-{i:02d}" for i in range(12)]
    lines = task_mod.pick_question("SRTX1", codes).splitlines()
    assert lines[1] == "1. SRTX1-00"
    assert lines[12] == "12. SRTX1-11"
    assert len(lines) == 13


def test_the_dealer_did_you_mean_list_is_numbered():
    from app.services.chatbot.dealer_stock import did_you_mean

    text, _pick = did_you_mean(
        "elp3753",
        [
            {"product": code, "uuid": ht.uuid_of(code), "entity_type": "product"}
            for code in ("ELP3754", "ELP3756")
        ],
        quantity=10,
    )
    assert text == "Couldn't find ELP3753. Did you mean:\n1. ELP3754\n2. ELP3756"


def test_a_correction_flag_beside_a_bare_position_is_still_the_quantity():
    """The parser sets `correction` on "how about 100?", so it never reopens the list."""
    console = _owner_run(
        verdict(reference_positions=[2], correction=True, entities=[]),
        verdict(demand_qty=3, entities=[]),
    )
    assert console.transcript[7] == f"-> SRTWC286-SH x 2: {TOO_BIG}"


def test_the_stock_task_addendum_teaches_the_bare_number():
    from app.services.chatbot_parser_prompt import STOCK_TASK_ADDENDUM

    assert "It is NEVER a position on a" in STOCK_TASK_ADDENDUM
