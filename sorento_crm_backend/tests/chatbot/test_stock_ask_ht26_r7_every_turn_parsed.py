"""PR #1247 round 7: every message goes through the parser.

Owner correction, 26 Sep 2026 ~08:33Z (verbatim, chat): "wait for the small talk skip
... the tia is a typo of tiga so it supposed to pass through parser, we shouldn't be so
rigid to say small talk bypass the parser". Ruling 4 of the round 4 console test is
DROPPED. Round 6 built it anyway (`head/fast_path.py`: "tia" answered "You're welcome."
with no model call, and a bare number or numbered lines read from the message's shape),
and round 7 takes it out, both halves.

What stays (rulings 1 to 3, as round 6 built them): "all" opens one point-form question,
one number applies to every line, per-line answers fill their lines, and a 429 is waited
out inside the one `llm_call` wrapper. The per-line answer now comes from the PARSER:
its entities, keyed by the product code or by the line's number, mapped to the open
question's lines (`apply._numbered_lines_are_the_products`).

The owner's 08:18Z to 08:21Z session is replayed on the same `Console` the round 4 to 6
replays drive, with the parser's answer stubbed per turn (no provider key on this VM).
"""
from __future__ import annotations

import importlib
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.state import Focus, focus_to_wire

from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._turn_helpers import verdict
from tests.chatbot.test_engine import (  # noqa: F401 - fixtures used by name
    CONTACT_ID,
    _envelope,
    _parser_output,
    seeded,
    stub_access,
    stub_parser,
)
from tests.chatbot.test_stock_ask_ht26_r4_owner_replay import OWNER_FAMILY, TOO_BIG, Console
from tests.chatbot.test_stock_ask_ht26_r6_all_point_form import ALL_READINGS, LIST, POINT_FORM

WELCOME = "You're welcome."


def _entity(raw: str, quantity: int | None, code: str | None = None) -> dict[str, Any]:
    return {
        "raw": raw,
        "hint": "product",
        "canonical_code": code,
        "current_message": True,
        "confident": True,
        "hint_confident": True,
        "quantity": quantity,
    }


def _answered(quantities: dict[str, int]) -> str:
    return "\n".join(f"{code} x {qty}: {TOO_BIG}" for code, qty in quantities.items())


def _after_all() -> Console:
    console = Console()
    assert console.first_ask("check stock srtwc286") == LIST
    console.say("all", ALL_READINGS["every_position"])
    return console


def _message(text: str) -> dict[str, Any]:
    return {
        "event_type": "message.received",
        "contact": {"id": CONTACT_ID},
        "message": {
            "messageId": f"ZZT-r7-{abs(hash(text))}",
            "contactId": CONTACT_ID,
            "channelId": "whatsapp",
            "traffic": "incoming",
            "message": {"type": "text", "text": text},
        },
    }


# --------------------------------------------------------------------------- #
# W1: the fast path is gone, both halves
# --------------------------------------------------------------------------- #


def test_the_fast_path_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.services.chatbot.head.fast_path")
    assert not hasattr(engine_mod, "fast_path")


def _one_product_question_state() -> dict[str, Any]:
    """The session after "How many units of SRTWC286-SH?" was asked."""
    focus = Focus(
        products=ht.product_rows("SRTWC286-SH"),
        domains=["inventory"],
        tasks=(
            task_mod.Task(
                kind="stock_qty",
                domain="inventory",
                slots=(task_mod.Slot(ht.product_rows("SRTWC286-SH")[0]["uuid"], "SRTWC286-SH"),),
                opened_at_turn=2,
                touched_at_turn=2,
            ),
        ),
    )
    return {"focus": focus_to_wire(focus), "open_question": None}


def test_tia_under_the_open_quantity_question_is_parsed_never_small_talk(
    session_factory, seeded, stub_parser, stub_access, monkeypatch
):
    """The owner's replay: "tia" (a typo of "tiga", three) under "How many units of
    SRTWC286-SH?" goes to the parser, and the parser's reading (demand_qty 3) is what
    the turn applies. Round 6 answered "You're welcome." and never asked the parser."""
    blocks: list[str] = []
    stub_parser(
        _parser_output(
            message_type="business_query",
            intent_hint=None,
            domain_hint=None,
            demand_qty=3,
            entities=[],
        ),
        on_call=blocks.append,
    )
    stub_access()
    import json

    from app.services.ai_assistant_service import MCPRuntimeClient

    tools: list[tuple[str, dict[str, Any]]] = []

    def _tool(self, tool_name, args):
        tools.append((tool_name, args))
        return json.dumps({"stock_availability": []})

    monkeypatch.setattr(MCPRuntimeClient, "call_tool", _tool)
    result = engine_mod.run_turn(
        _envelope(
            is_test=True,
            previous_conversation_state=_one_product_question_state(),
            message=_message("tia"),
        ),
        session_factory=session_factory,
    )
    assert len(blocks) == 1, "the parser must read every message, 'tia' included"
    assert "tia" in blocks[0]
    assert (result.reply or {}).get("text") != WELCOME
    # The parser's "tiga" is the quantity the stock check is fetched at.
    uuid = ht.product_rows("SRTWC286-SH")[0]["uuid"]
    stock = [args for name, args in tools if name == "crm_inventory_stock_balance_list"]
    assert stock, tools
    assert json.loads(stock[0]["requested_quantities"]) == {uuid: 3}


def test_tia_on_its_own_is_parsed_too(session_factory, seeded, stub_parser, stub_access):
    blocks: list[str] = []
    stub_parser(
        _parser_output(message_type="casual", intent_hint=None, domain_hint=None, entities=[]),
        on_call=blocks.append,
    )
    stub_access()
    result = engine_mod.run_turn(_envelope(message=_message("tia")), session_factory=session_factory)
    assert len(blocks) == 1 and "tia" in blocks[0]
    assert (result.reply or {}).get("text") != WELCOME


def test_tia_read_as_three_answers_the_one_product_question():
    """The same replay on the Console: the parser's "tiga" reading is the quantity."""
    console = Console()
    console.first_ask("check stock srtwc286")
    assert console.say("1", verdict(reference_positions=[1], entities=[])) == (
        "How many units of SRTWC286-SH?"
    )
    assert console.say("tia", verdict(demand_qty=3, entities=[])) == f"SRTWC286-SH x 3: {TOO_BIG}"


# --------------------------------------------------------------------------- #
# W2: numbered lines, through the parser
# --------------------------------------------------------------------------- #


LINE_READINGS = {
    # The parser names the line's product itself (the hint prints the lines).
    "codes": verdict(
        entities=[_entity("SRTWC286-SH", 10, "SRTWC286-SH"), _entity("SRTWC286-SH-150", 5, "SRTWC286-SH-150")]
    ),
    # The parser keeps the dealer's line numbers as the entities' raw text.
    "line_numbers": verdict(entities=[_entity("1", 10), _entity("2", 5)]),
    # The parser reads the line numbers as positions and the numbers as quantities.
    "positions": verdict(
        reference_positions=[1, 2], entities=[_entity("10", 10), _entity("5", 5)]
    ),
    # A recorded or harness verdict that already carries the lines.
    "slot_quantities": verdict(
        **{task_mod.SLOT_QUANTITIES: {ht.product_rows("SRTWC286-SH")[0]["uuid"]: 10,
                                     ht.product_rows("SRTWC286-SH-150")[0]["uuid"]: 5}},
        entities=[],
    ),
}


@pytest.mark.parametrize("reading", sorted(LINE_READINGS))
def test_numbered_lines_from_the_parser_fill_those_lines(reading):
    """Every reading maps onto lines 1 and 2. STOCK-QTY-PARTIAL-FILL (owner ruling R1,
    6 Oct 2026): those two lines are answered now and the other eight stay open on the
    task, never re-listed (before: the list again with the two lines filled in)."""
    console = _after_all()
    text = console.say("1. 10, 2. 5", LINE_READINGS[reading])
    assert text == _answered({"SRTWC286-SH": 10, "SRTWC286-SH-150": 5})
    (task,) = [t for t in console.state.focus.tasks if t.kind == "stock_qty"]
    assert task.status == task_mod.OPEN
    assert [s.value for s in task.slots] == [10, 5] + [None] * 8


def test_every_line_at_once_from_the_parser_answers_the_check():
    console = _after_all()
    parsed = verdict(entities=[_entity(str(i), i * 3) for i in range(1, 11)])
    message = "\n".join(f"{i}. {i * 3}" for i in range(1, 11))
    assert console.say(message, parsed) == _answered(
        {code: i * 3 for i, code in enumerate(OWNER_FAMILY, 1)}
    )


def test_a_line_number_off_the_list_is_not_a_line():
    """"11. 4" over a ten-line question names no line; nothing is filled from it."""
    from app.services.chatbot.turn.apply import _numbered_lines_are_the_products
    from app.services.chatbot.turn.plan import Trace

    console = _after_all()
    parsed = verdict(entities=[_entity("1", 10), _entity("11", 4)])
    trace = Trace()
    _numbered_lines_are_the_products(console.state, parsed, trace)
    assert "numbered_lines_are_the_products" not in trace.rules_fired
    assert [e["raw"] for e in parsed["entities"]] == ["1", "11"], "nothing half-mapped"


def test_the_open_question_hint_prints_its_lines_for_the_parser():
    """PR #1247 round 8: the point-form question's own lines ride on the parser's
    "Open question: {...}" object now (`task_mod.open_question`), not on the hint - the
    hint states only what is open/answered and no longer repeats the numbered lines."""
    console = _after_all()
    (task,) = [t for t in console.state.focus.tasks if t.kind == "stock_qty"]
    open_question = task_mod.open_question((task,))
    assert open_question is not None
    codes = [item["code"] for item in open_question["items"]]
    assert codes[0] == "SRTWC286-SH" and codes[-1] == "SRTWC286-SH-UF"
    (hint,) = task_mod.hint_lines((task,))
    assert "1. SRTWC286-SH" not in hint


# --------------------------------------------------------------------------- #
# The owner's 08:18Z to 08:21Z session, every turn through the parser
# --------------------------------------------------------------------------- #


def test_the_owner_session_replayed_with_every_turn_parsed():
    console = _after_all()
    # R1 (6 Oct 2026): lines 1 and 2 answered now, the rest kept open.
    assert console.say("1. 10, 2. 5", LINE_READINGS["line_numbers"]) == _answered(
        {"SRTWC286-SH": 10, "SRTWC286-SH-150": 5}
    )
    # "tia" (tiga): three for every line still owed, and only those are answered.
    text = console.say("tia", verdict(demand_qty=3, entities=[]))
    assert text == _answered({code: 3 for code in OWNER_FAMILY[2:]})
    assert console.transcript[:4] == ["check stock srtwc286", f"-> {LIST}", "all", f"-> {POINT_FORM}"]
    assert WELCOME not in "\n".join(console.transcript)
