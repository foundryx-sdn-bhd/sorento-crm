"""Owner console test of round 4 (26 Sep 2026 08:18Z to 08:21Z, :3087, head 9f20c24c1),
PR #1247 round 6. The owner's words: "why i can't do all, and when i do all, why so
complicated, can it be point form, we can do like numbered list, then " - " and let
them put in (no emdash), well of course they can just say one number like 10 to apply
to all".

Observed (chatbot.turns, contact 437264483):

    check stock srtwc286
    -> SRTWC286 matches 10 products. Which one?  (numbered, good)
    all
    -> How many units do you need for SRTWC286-SH, SRTWC286-SH-150, ... and
       SRTWC286-SH-UF?                             (one long sentence)
    <answer>
    -> the same question again                     (the answer did not apply)
    <answer>
    -> the same question again

Rulings:
1. "all" works: picking every product opens ONE quantity question for all of them.
2. That question is point form, one numbered line per product ending " - " for the
   dealer to fill. The dealer may reply per line ("1. 10, 2. 5", "SRTWC286-SH 10") or
   with ONE number that applies to all. No em dashes.

Only the parser is stubbed, the same `Console` the round 4 and 5 replays drive. Round 7:
every message goes through the parser (the owner dropped round 6's fast path), so every
turn here carries the parser's own reading; the numbered lines are mapped onto the
question's lines by `apply._numbered_lines_are_the_products`.
"""
from __future__ import annotations

import pytest

from app.services.chatbot.turn import task as task_mod

from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_stock_ask_ht26_r4_owner_replay import (
    OWNER_FAMILY,
    TOO_BIG,
    Console,
    _numbered,
)

LIST = _numbered("SRTWC286 matches 10 products. Which one?", OWNER_FAMILY)

#: The ruling's own shape: a header, then one "N. CODE - " line per product.
POINT_FORM = "\n".join(
    ["How many units for each?", *[f"{i}. {code} - " for i, code in enumerate(OWNER_FAMILY, 1)]]
)

ALL_READINGS = {
    # The prompt's "ALL WITH NO ALL OPTION IS EVERY NUMBER" rule.
    "every_position": verdict(reference_positions=list(range(1, 11)), entities=[]),
    # Contract 31, R21: "all" over a numbered menu.
    "broaden_all": verdict(broaden_axis="all", entities=[]),
}


def _line(raw: str, quantity: int, code: str | None = None) -> dict:
    return {
        "raw": raw,
        "hint": "product",
        "canonical_code": code,
        "current_message": True,
        "confident": True,
        "hint_confident": True,
        "quantity": quantity,
    }


#: The parser's reading of each message these tests send (round 7: no fast path).
PARSED = {
    "1. 10, 2. 5": verdict(entities=[_line("1", 10), _line("2", 5)]),
    "1. SRTWC286-SH - 10\n2. SRTWC286-SH-150 - 5": verdict(
        entities=[
            _line("SRTWC286-SH", 10, "SRTWC286-SH"),
            _line("SRTWC286-SH-150", 5, "SRTWC286-SH-150"),
        ]
    ),
}


def _say_parsed(console: Console, message: str) -> str:
    if message.strip().isdigit():
        parsed = verdict(demand_qty=int(message), entities=[])
    elif message in PARSED:
        parsed = PARSED[message]
    else:
        parsed = verdict(
            entities=[
                _line(pos, int(qty))
                for pos, qty in (line.split(". ") for line in message.splitlines())
            ]
        )
    return console.say(message, parsed)


def _answered(quantities: dict[str, int]) -> str:
    return "\n".join(f"{code} x {qty}: {TOO_BIG}" for code, qty in quantities.items())


def _after_all(reading: str = "every_position") -> Console:
    console = Console()
    assert console.first_ask("check stock srtwc286") == LIST
    console.say("all", ALL_READINGS[reading])
    return console


# --------------------------------------------------------------------------- #
# Ruling 1 + 2: "all" opens ONE point-form question
# --------------------------------------------------------------------------- #


#: AVAIL-MODE-REPLIES (owner, 2 Oct 2026, Q4 (b)) supersedes ruling 1 for the EXPLICIT
#: "all": `broaden_all` over a stock pick is refused and the list stays open
#: (`test_explicit_all_is_refused_since_avail_mode_replies`). A pick of every position is
#: still a pick, so the point-form rulings below keep running on that reading. Through the
#: engine a BARE typed "all" is refused on either reading (`engine._is_bare_all`,
#: tests/chatbot/test_avail_mode_scenarios.py S34); this Console runs apply only.
PICK_READINGS = ["every_position"]


def test_explicit_all_is_refused_since_avail_mode_replies():
    from app.services.chatbot.turn.apply import STOCK_PICK_NOT_ALL

    console = Console()
    assert console.first_ask("check stock srtwc286") == LIST
    console.say("all", ALL_READINGS["broaden_all"])
    assert console.transcript[-1] == f"-> {STOCK_PICK_NOT_ALL}"
    assert console.state.pending is not None
    assert [o["code"] for o in console.state.pending.options] == OWNER_FAMILY


@pytest.mark.parametrize("reading", PICK_READINGS)
def test_all_opens_one_point_form_question_for_every_product(reading):
    console = _after_all(reading)
    assert console.transcript[-1] == f"-> {POINT_FORM}"
    assert console.state.pending is None
    (task,) = [t for t in console.state.focus.tasks if t.kind == "stock_qty"]
    assert [slot.label for slot in task.slots] == OWNER_FAMILY
    assert task.status == task_mod.OPEN


def test_the_point_form_question_has_no_em_or_en_dash():
    text = _after_all().transcript[-1]
    assert chr(0x2014) not in text and chr(0x2013) not in text
    assert all(line.endswith(" - ") for line in text.splitlines()[1:])


def test_two_products_ask_in_point_form_too():
    """The format belongs to every multi-product stock question, not to "all" alone."""
    task = task_mod.Task(
        kind="stock_qty",
        domain="inventory",
        slots=(task_mod.Slot("u1", "ELP3754"), task_mod.Slot("u2", "SRTKT1631SS")),
    )
    assert task_mod.StockQtyTask().question(task) == (
        "How many units for each?\n1. ELP3754 - \n2. SRTKT1631SS - "
    )


def test_one_product_still_asks_its_one_line_question():
    task = task_mod.Task(kind="stock_qty", domain="inventory", slots=(task_mod.Slot("u1", "ELP3754"),))
    assert task_mod.StockQtyTask().question(task) == "How many units of ELP3754?"


# --------------------------------------------------------------------------- #
# ONE number applies to all
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("reading", PICK_READINGS)
def test_owner_replay_one_number_applies_to_all(reading):
    console = _after_all(reading)
    text = _say_parsed(console, "10")
    assert text == _answered({code: 10 for code in OWNER_FAMILY})
    assert "task_drives_the_fetch" in console.plans[-1].trace.rules_fired


@pytest.mark.parametrize(
    "parsed",
    [
        verdict(demand_qty=10, entities=[]),
        # The list the question prints is numbered 1 to 10, so the live parser may
        # read a bare "10" as the tenth line. With nothing open it is the quantity.
        verdict(reference_positions=[10], entities=[]),
    ],
    ids=["demand_qty", "position"],
)
def test_one_number_read_by_the_parser_applies_to_all_too(parsed):
    console = _after_all()
    assert console.say("10", parsed) == _answered({code: 10 for code in OWNER_FAMILY})


# --------------------------------------------------------------------------- #
# Per line
# --------------------------------------------------------------------------- #


def test_per_line_reply_fills_those_lines_and_asks_the_rest_in_point_form():
    console = _after_all()
    text = _say_parsed(console, "1. 10, 2. 5")
    lines = text.splitlines()
    assert lines[0] == "How many units for each?"
    assert lines[1] == "1. SRTWC286-SH - 10"
    assert lines[2] == "2. SRTWC286-SH-150 - 5"
    assert lines[3:] == [f"{i}. {code} - " for i, code in enumerate(OWNER_FAMILY[2:], 3)]
    # The rest then take one number.
    text = _say_parsed(console, "8")
    assert text == _answered(
        {code: (10 if i == 0 else 5 if i == 1 else 8) for i, code in enumerate(OWNER_FAMILY)}
    )


def test_every_line_answered_at_once_answers_the_check():
    console = _after_all()
    message = "\n".join(f"{i}. {i * 3}" for i in range(1, 11))
    assert _say_parsed(console, message) == _answered(
        {code: i * 3 for i, code in enumerate(OWNER_FAMILY, 1)}
    )


def test_the_dealer_may_paste_the_lines_back_filled_in():
    console = _after_all()
    message = "1. SRTWC286-SH - 10\n2. SRTWC286-SH-150 - 5"
    text = _say_parsed(console, message)
    assert text.splitlines()[1:3] == ["1. SRTWC286-SH - 10", "2. SRTWC286-SH-150 - 5"]


def test_a_code_and_a_quantity_from_the_parser_fills_that_line():
    """"SRTWC286-SH 10": the parser's own per-product reading (`entities[].quantity`)."""
    console = _after_all()
    parsed = verdict(
        entities=[
            {
                "raw": "SRTWC286-SH",
                "hint": "product",
                "canonical_code": "SRTWC286-SH",
                "current_message": True,
                "confident": True,
                "quantity": 10,
            }
        ]
    )
    text = console.say("SRTWC286-SH 10", parsed)
    assert text.splitlines()[:3] == [
        "How many units for each?",
        "1. SRTWC286-SH - 10",
        "2. SRTWC286-SH-150 - ",
    ]


def test_the_full_owner_transcript_after_the_fix():
    console = _after_all()
    _say_parsed(console, "1. 10, 2. 5")
    _say_parsed(console, "10")
    assert console.transcript[:4] == ["check stock srtwc286", f"-> {LIST}", "all", f"-> {POINT_FORM}"]
    assert console.transcript[4] == "1. 10, 2. 5"
    assert console.transcript[6] == "10"
    assert console.transcript[7] == "-> " + _answered(
        {code: (10 if i == 0 else 5 if i == 1 else 10) for i, code in enumerate(OWNER_FAMILY)}
    )


# --------------------------------------------------------------------------- #
# Earlier rulings, still standing
# --------------------------------------------------------------------------- #


def test_round_five_one_product_pick_then_numbers_still_revise():
    console = Console()
    console.first_ask("check stock srtwc286")
    assert console.say("1", verdict(reference_positions=[1], entities=[])) == (
        "How many units of SRTWC286-SH?"
    )
    assert _say_parsed(console, "10") == f"SRTWC286-SH x 10: {TOO_BIG}"
    assert _say_parsed(console, "2") == f"SRTWC286-SH x 2: {TOO_BIG}"
    assert console.say("3", verdict(reference_positions=[3], entities=[])) == (
        f"SRTWC286-SH x 3: {TOO_BIG}"
    )
