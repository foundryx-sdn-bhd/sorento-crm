"""PR #1247 (chatbot stock ask v2 S3), round 9 (issue #1293): one open-question object
for every question the bot asks, and one declared answer the parser returns for it.

The owner, 26 Sep 2026 ~14:15Z, after the round 8 console test: "Like I want to say 'the
first one I need two'. It's quite weird that it replies in that way. So maybe our parser
can be better. And I'm also curious in your methodology ... not too much hard coding,
hard routing."

The exchange (14:07Z to 14:08Z, :3087, contact 900000008):
  "check stock STWC2867"      -> "Couldn't find STWC2867. Did you mean: 1. SRTWC286-SH
                                  2. SRTWC286-SH-P"
  "the first one, I need 2"   -> "STWC2867 x 2: which one? 1. ... 2. ..."   (wrong)
  "1, I need 2"               -> the same                                 (wrong)
  "SRTWC286-SH x 2"           -> answered

Design rule under test: the parser READS, the code APPLIES. Every question is one
object (`turn/question.py`), the parser answers it in `open_question_answer`, the apply
layer acts on that object and falls back to shape rules only when it is absent. No
test here feeds the apply layer a customer's WORDS to decide on: every verdict is the
parser's reading, stubbed as the live parser should give it.

Sections: A the schema, B the object, C the block, D the pick answer applied, E the
fallback, F the header, G the owner's four 26 Sep sessions through `engine.run_turn`,
H ten phrasings per question kind, I the prompt contract, J the publish migration.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from app.services.chatbot import dealer_stock as dealer
from app.services.chatbot.head import parser as parser_mod
from app.services.chatbot.turn import pending as turn_pending
from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.apply import apply
from app.services.chatbot.turn.state import Focus

from tests.chatbot import _ht26_fixtures as ht
from tests.chatbot._r9_engine_console import (
    OWNER_FAMILY,
    TOO_BIG,
    EngineConsole,
    answer,
    numbered,
    product,
    reply,
    stock,
)
from tests.chatbot._turn_helpers import build_policy, verdict
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
DYM = ["SRTWC286-SH", "SRTWC286-SH-P"]
DYM_TEXT = numbered("Couldn't find STWC2867. Did you mean:", DYM)


def _question():
    from app.services.chatbot.turn import question as question_mod

    return question_mod


def _dym_pick(quantity: Any = None, codes: list[str] | None = None, typed: str = "STWC2867"):
    """The dealer's did-you-mean pick, as `dealer_stock.did_you_mean` mints it."""
    codes = codes or DYM
    _text, pick = dealer.did_you_mean(
        typed,
        [{"label": c, "product": c, "uuid": ht.uuid_of(c), "entity_type": "product"} for c in codes],
        quantity=quantity,
        asked_at_turn=1,
    )
    return pick


def _missed_focus(typed: str = "STWC2867", quantity: Any = None) -> Focus:
    row = {
        "raw": typed,
        "hint": "product",
        "canonical_code": typed,
        "current_message": False,
        "confident": True,
    }
    if quantity is not None:
        row["quantity"] = quantity
    return Focus(domains=["inventory"], products=[row])


def _dym_state(quantity: Any = None, codes: list[str] | None = None):
    return ht.state(
        _missed_focus(quantity=quantity),
        pending=_dym_pick(quantity, codes),
        turn_no=2,
        availability_only=True,
    )


# =============================================================================== #
# A. The declared answer: one object for every question kind
# =============================================================================== #


def test_a_open_question_answer_declares_pick_yes_no_and_picked():
    prop = parser_mod.PARSE_OUTPUT_JSON_SCHEMA["properties"]["open_question_answer"]
    assert prop["additionalProperties"] is False
    assert set(prop["required"]) == {"mode", "picked", "items", "qty_for_all"}
    assert prop["properties"]["mode"]["enum"] == [
        "pick",
        "yes",
        "no",
        "fill",
        "all",
        "done",
        "cancel",
        None,
    ]
    picked = prop["properties"]["picked"]
    assert picked == {"type": "array", "items": {"type": "integer"}}
    # Still tolerated absent: a recorded emission predates the key.
    assert "open_question_answer" in parser_mod.TOLERATED_ABSENT


# =============================================================================== #
# B. `turn/question.py`: every question the bot asks, as one object
# =============================================================================== #


def test_b_did_you_mean_is_a_pick_one_owing_the_pick_and_the_quantity():
    obj = _question().open_question(_dym_pick(), ())
    assert obj == {
        "kind": "pick_one",
        "options": [
            {"position": 1, "code": "SRTWC286-SH"},
            {"position": 2, "code": "SRTWC286-SH-P"},
        ],
        "owed": ["pick", "quantity"],
    }


def test_b_a_pick_carrying_a_quantity_states_it_and_owes_only_the_pick():
    obj = _question().open_question(_dym_pick(quantity=2), ())
    assert obj["kind"] == "pick_one"
    assert obj["qty"] == 2
    assert obj["owed"] == ["pick"]


def test_b_the_family_which_one_is_a_pick_one_too():
    obj = _question().open_question(ht.family_pick(codes=OWNER_FAMILY), ())
    assert obj["kind"] == "pick_one"
    assert [o["code"] for o in obj["options"]] == OWNER_FAMILY
    assert [o["position"] for o in obj["options"]] == list(range(1, 11))


def test_b_a_one_option_did_you_mean_is_a_confirm():
    obj = _question().open_question(_dym_pick(quantity=10, codes=["ELP3754"]), ())
    assert obj == {
        "kind": "confirm",
        "options": [{"position": 1, "code": "ELP3754"}],
        "owed": ["yes_no"],
        "qty": 10,
    }


def test_b_a_yes_no_offer_is_a_confirm():
    offer = turn_pending.ask(
        "team_pick",
        [{"position": 1, "label": "Warehouse", "entity_type": "team"}],
        expects="yes_no",
    )
    obj = _question().open_question(offer, ())
    assert obj["kind"] == "confirm"
    assert obj["owed"] == ["yes_no"]


def test_b_a_brand_roster_is_choose_brand():
    roster = turn_pending.ask(
        "brand_pick",
        [
            {"position": 1, "label": "Sorento", "code": "SRT", "entity_type": "brand"},
            {"position": 2, "label": "Hafele", "code": "HFL", "entity_type": "brand"},
        ],
    )
    obj = _question().open_question(roster, ())
    assert obj["kind"] == "choose_brand"
    # The printed label and the code differ: both go to the parser.
    assert obj["options"][0] == {"position": 1, "code": "SRT", "label": "Sorento"}
    assert obj["owed"] == ["pick"]


def test_b_a_customer_roster_is_a_pick_one_owing_only_the_pick():
    roster = turn_pending.ask(
        "customer_pick",
        [{"position": 1, "label": "Hanlim Sdn Bhd", "code": "ZZT-B094", "entity_type": "customer"}],
    )
    obj = _question().open_question(roster, ())
    assert obj == {
        "kind": "pick_one",
        "options": [{"position": 1, "code": "ZZT-B094", "label": "Hanlim Sdn Bhd"}],
        "owed": ["pick"],
    }


def test_b_a_question_with_no_options_is_free():
    obj = _question().open_question(turn_pending.ask("outstanding_scope", []), ())
    assert obj == {"kind": "free", "options": [], "owed": ["answer"]}


def test_b_the_stock_task_is_quantities_or_last_answer():
    open_task = ht.stock_task([("SRTWC286-SH", 10), ("SRTWC286-SH-150", None)])
    obj = _question().open_question(None, (open_task,))
    assert obj["kind"] == "quantities"
    assert obj["owed"] == [2]
    answered = ht.stock_task([("SRTWC286-SH", 10)], status=task_mod.ANSWERED)
    assert _question().open_question(None, (answered,))["kind"] == "last_answer"


def test_b_an_open_pick_is_the_question_even_with_a_stock_task_behind_it():
    behind = ht.stock_task([("SRTWC286-SH", 10)], status=task_mod.ANSWERED)
    assert _question().open_question(_dym_pick(), (behind,))["kind"] == "pick_one"


def test_b_nothing_open_is_none():
    assert _question().open_question(None, ()) is None


def test_b_how_many_to_show_is_declared_and_never_written():
    """The owner's no-paging, full-counts ruling means the bot never asks how many to
    show. The kind is part of the contract so the parser prompt and the code share one
    list; no builder writes it until a question of that kind exists."""
    question_mod = _question()
    assert question_mod.HOW_MANY_TO_SHOW in question_mod.KINDS
    assert set(question_mod.KINDS) == {
        "pick_one",
        "choose_brand",
        "confirm",
        "quantities",
        "last_answer",
        "how_many_to_show",
        "free",
    }


# =============================================================================== #
# C. The block: the object goes to the parser for a pick too
# =============================================================================== #


def test_c_the_block_states_a_pick_object_and_keeps_the_open_task_lines():
    focus = Focus(
        domains=["inventory"],
        tasks=(ht.stock_task([("SRTWC286-SH", None), ("SRTWC286-SH-150", None)]),),
    )
    obj = _question().open_question(_dym_pick(), focus.tasks)
    block = parser_mod.build_user_block(
        previous_response=DYM_TEXT,
        latest_user_message="the first one, I need 2",
        pending_kind="product_pick",
        focus=focus,
        open_question=obj,
    )
    assert 'Open question: {"kind":"pick_one"' in block
    # A pick is not the stock question: the task prints its own lines, never a
    # pointer to an object that is not the one shown.
    assert "see Open question" not in block
    assert "1. SRTWC286-SH" in next(line for line in block.splitlines() if line.startswith("Open task:"))


def test_c_the_engine_sends_the_did_you_mean_as_a_pick_object(
    session_factory, monkeypatch, stub_access
):
    console = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000047")
    console.say("check stock STWC2867", stock(product("STWC2867")))
    console.say("the first one, I need 2", _first_one_two())
    block = console.last_block
    assert (
        'Open question: {"kind":"pick_one","options":[{"position":1,"code":"SRTWC286-SH"},'
        '{"position":2,"code":"SRTWC286-SH-P"}],"owed":["pick","quantity"]}'
    ) in block


# =============================================================================== #
# D. The pick answer, applied (no word read anywhere)
# =============================================================================== #


def _first_one_two() -> dict[str, Any]:
    """"the first one, I need 2" as the round 9 parser reads it."""
    return reply(demand_qty=2, reference_positions=[1], open_question_answer=answer("pick", picked=[1], qty_for_all=2))


def test_d_a_pick_with_a_quantity_answers_the_picked_product_at_it():
    new, plan = apply(_dym_state(), _first_one_two(), build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert [e["canonical_code"] for e in spec.entities] == ["SRTWC286-SH"]
    assert spec.filters.get("requested_quantities") == {ht.uuid_of("SRTWC286-SH"): 2}
    assert "open_question_answer_pick" in plan.trace.rules_fired
    assert "stock_pick_takes_quantity" not in plan.trace.rules_fired
    assert new.pending is None


def test_d_the_quantity_can_ride_on_the_picked_item():
    v = reply(
        demand_qty=2,
        open_question_answer=answer("pick", picked=[1], items=[(1, "SRTWC286-SH", 2)]),
    )
    _new, plan = apply(_dym_state(), v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert spec.filters.get("requested_quantities") == {ht.uuid_of("SRTWC286-SH"): 2}


def test_d_a_pick_by_code_alone_places_the_option():
    v = reply(open_question_answer=answer("pick", items=[(None, "srtwc286-sh-p", 3)]))
    _new, plan = apply(_dym_state(), v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert [e["canonical_code"] for e in spec.entities] == ["SRTWC286-SH-P"]
    assert spec.filters.get("requested_quantities") == {ht.uuid_of("SRTWC286-SH-P"): 3}


def test_d_a_pick_with_no_quantity_fetches_the_pick_so_the_quantity_is_asked():
    v = reply(reference_positions=[2], open_question_answer=answer("pick", picked=[2]))
    _new, plan = apply(_dym_state(), v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert [e["canonical_code"] for e in spec.entities] == ["SRTWC286-SH-P"]
    assert not spec.filters.get("requested_quantities")


def test_d_both_picked_each_at_its_own_quantity():
    v = reply(
        open_question_answer=answer(
            "pick", picked=[1, 2], items=[(1, None, 2), (2, None, 5)]
        ),
    )
    _new, plan = apply(_dym_state(), v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert sorted(e["canonical_code"] for e in spec.entities) == DYM
    assert spec.filters.get("requested_quantities") == {
        ht.uuid_of("SRTWC286-SH"): 2,
        ht.uuid_of("SRTWC286-SH-P"): 5,
    }


def test_d_one_quantity_for_every_picked_option():
    v = reply(open_question_answer=answer("pick", picked=[1, 2], qty_for_all=4))
    _new, plan = apply(_dym_state(), v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert spec.filters.get("requested_quantities") == {
        ht.uuid_of("SRTWC286-SH"): 4,
        ht.uuid_of("SRTWC286-SH-P"): 4,
    }


def test_d_the_carried_quantity_stays_when_the_pick_states_none():
    v = reply(open_question_answer=answer("pick", picked=[1]))
    _new, plan = apply(_dym_state(quantity=7), v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert spec.filters.get("requested_quantities") == {ht.uuid_of("SRTWC286-SH"): 7}


def test_d_a_position_not_offered_is_not_applied():
    v = reply(demand_qty=2, open_question_answer=answer("pick", picked=[3], qty_for_all=2))
    _new, plan = apply(_dym_state(), v, build_policy())
    assert "open_question_answer_pick" not in plan.trace.rules_fired
    assert not ht.inventory_specs(plan)


def test_d_a_quantity_of_zero_is_not_applied():
    v = reply(open_question_answer=answer("pick", picked=[1], qty_for_all=0))
    _new, plan = apply(_dym_state(), v, build_policy())
    assert "open_question_answer_pick" not in plan.trace.rules_fired


def test_d_a_quantity_of_zero_on_an_item_is_not_applied():
    v = reply(open_question_answer=answer("pick", items=[(1, None, 0)]))
    _new, plan = apply(_dym_state(), v, build_policy())
    assert "open_question_answer_pick" not in plan.trace.rules_fired


def test_d_none_of_them_refers_the_dealer_to_their_salesman():
    v = reply(is_affirmative=False, open_question_answer=answer("no"))
    new, plan = apply(_dym_state(), v, build_policy())
    assert plan.trace.task_question == task_mod.REFER_TO_SALESMAN
    assert new.pending is None
    assert not ht.inventory_specs(plan)


def test_d_yes_to_a_one_option_did_you_mean_answers_it_at_the_carried_quantity():
    state = ht.state(
        _missed_focus("ELP3753", 10),
        pending=_dym_pick(10, ["ELP3754"], typed="ELP3753"),
        turn_no=2,
        availability_only=True,
    )
    _new, plan = apply(state, reply(open_question_answer=answer("yes")), build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert [e["canonical_code"] for e in spec.entities] == ["ELP3754"]
    assert spec.filters.get("requested_quantities") == {ht.uuid_of("ELP3754"): 10}
    assert "open_question_answer_yes" in plan.trace.rules_fired


def test_d_a_pick_answer_never_escalates_through_a_yes_no_offer():
    """The object only writes the fields `decide()` already reads, so the handover
    guard (one explicit position or a plain yes, never a position over a yes/no offer)
    still holds."""
    offer = turn_pending.ask(
        "team_pick",
        [{"position": 1, "label": "Warehouse", "entity_type": "team"}],
        expects="yes_no",
    )
    state = ht.state(Focus(domains=["order"]), pending=offer, turn_no=3)
    _new, plan = apply(state, reply(open_question_answer=answer("pick", picked=[1])), build_policy())
    assert plan.trace.lane != "escalation"


def test_d_a_pick_object_with_nothing_open_is_ignored():
    state = ht.state(Focus(domains=["inventory"]), turn_no=3, availability_only=True)
    _new, plan = apply(state, reply(open_question_answer=answer("pick", picked=[1])), build_policy())
    assert "open_question_answer_pick" not in plan.trace.rules_fired


# =============================================================================== #
# E. The fallback: the object absent (a recorded emission), mode null
# =============================================================================== #


def test_e_a_position_beside_a_quantity_is_the_pick_and_the_quantity():
    v = reply(demand_qty=2, reference_positions=[1])
    _new, plan = apply(_dym_state(), v, build_policy())
    (spec,) = ht.inventory_specs(plan)
    assert [e["canonical_code"] for e in spec.entities] == ["SRTWC286-SH"]
    assert spec.filters.get("requested_quantities") == {ht.uuid_of("SRTWC286-SH"): 2}
    assert "stock_pick_takes_position_and_quantity" in plan.trace.rules_fired


def test_e_a_lone_number_under_a_family_pick_is_still_the_quantity():
    """Round 5: "88" (or "2", read both ways) under "which one?" is the quantity, the
    pick asked again carrying it."""
    state = ht.state(_missed_focus("SRTWC286"), pending=ht.family_pick(codes=OWNER_FAMILY), turn_no=2, availability_only=True)
    _new, plan = apply(state, reply(demand_qty=2, reference_positions=[2]), build_policy())
    assert "stock_pick_takes_quantity" in plan.trace.rules_fired
    assert not ht.inventory_specs(plan)


# =============================================================================== #
# F. Headers never repeat a code the resolver did not recognise
# =============================================================================== #


def test_f_the_did_you_mean_re_ask_does_not_lead_with_the_unknown_code():
    _new, plan = apply(_dym_state(), reply(demand_qty=2), build_policy())
    assert plan.trace.task_question == numbered("Which one do you need 2 of?", DYM)
    assert "STWC2867" not in plan.trace.task_question


def test_f_the_family_re_ask_still_names_the_family_it_matched():
    state = ht.state(_missed_focus("SRTWC286"), pending=ht.family_pick(codes=OWNER_FAMILY), turn_no=2, availability_only=True)
    _new, plan = apply(state, reply(demand_qty=88), build_policy())
    assert plan.trace.task_question.startswith("SRTWC286 x 88: which one?")


# =============================================================================== #
# G. The owner's four 26 Sep console sessions, through `engine.run_turn`
# =============================================================================== #


LIST = numbered("SRTWC286 matches 10 products. Which one?", OWNER_FAMILY)


def _point_form(codes: list[str], values: dict[int, int] | None = None) -> str:
    values = values or {}
    return "\n".join(
        [
            "How many units for each?",
            *[
                f"{i}. {code} - {values[i]}" if i in values else f"{i}. {code} - "
                for i, code in enumerate(codes, 1)
            ],
        ]
    )


def _answered(*pairs: tuple[str, int]) -> str:
    return "\n\n".join(f"{code} x {qty}: {TOO_BIG}" for code, qty in pairs)


def test_g_1406_the_first_one_i_need_2(session_factory, monkeypatch, stub_access):
    """Issue #1293: the owner's exchange, 14:07Z to 14:08Z, as it reads after the fix."""
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000044")
    assert c.say("check stock STWC2867", stock(product("STWC2867"))) == DYM_TEXT
    assert c.say("the first one, I need 2", _first_one_two()) == _answered(("SRTWC286-SH", 2))
    # The pick is spent: the same did-you-mean, asked again, answers "1, I need 2" too.
    assert c.say("check stock STWC2867", stock(product("STWC2867"))) == DYM_TEXT
    assert c.say(
        "1, I need 2",
        reply(demand_qty=2, reference_positions=[1], open_question_answer=answer("pick", picked=[1], qty_for_all=2)),
    ) == _answered(("SRTWC286-SH", 2))
    assert c.say("SRTWC286-SH x 2", stock(product("SRTWC286-SH", 2))) == _answered(("SRTWC286-SH", 2))
    # Never the unknown code in a header once the pick resolved.
    assert all("STWC2867 x" not in line for line in c.transcript)


def test_g_1406_replayed_with_a_recorded_emission_reads_the_same(
    session_factory, monkeypatch, stub_access
):
    """The fallback: the parser left the object out (mode null) and read the position
    and the quantity as it always has."""
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000054")
    c.say("check stock STWC2867", stock(product("STWC2867")))
    assert c.say("the first one, I need 2", reply(demand_qty=2, reference_positions=[1])) == _answered(
        ("SRTWC286-SH", 2)
    )


def test_g_1406_a_quantity_alone_asks_which_without_the_unknown_code(
    session_factory, monkeypatch, stub_access
):
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000059")
    c.say("check stock STWC2867", stock(product("STWC2867")))
    assert c.say("I need 2", reply(demand_qty=2)) == numbered("Which one do you need 2 of?", DYM)
    assert c.say("the second", reply(reference_positions=[2], open_question_answer=answer("pick", picked=[2]))) == _answered(
        ("SRTWC286-SH-P", 2)
    )


def test_g_0718_round_3_hand_test(session_factory, monkeypatch, stub_access):
    """07:18Z (round 3 hand test, rulings 1 and 2): the list is numbered; a bare number
    after an answered quantity revises it, never a pick and never a re-ask."""
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000061")
    assert c.say("check stock srtwc286", stock(product("srtwc286"))) == LIST
    assert c.say("1", reply(reference_positions=[1], open_question_answer=answer("pick", picked=[1]))) == (
        "How many units of SRTWC286-SH?"
    )
    assert c.say("10", reply(demand_qty=10, open_question_answer=answer("fill", items=[(1, None, 10)]))) == _answered(
        ("SRTWC286-SH", 10)
    )
    assert c.say("2", reply(demand_qty=2, open_question_answer=answer("fill", items=[(1, None, 2)]))) == _answered(
        ("SRTWC286-SH", 2)
    )
    assert c.say("3", reply(demand_qty=3, open_question_answer=answer("fill", items=[(1, None, 3)]))) == _answered(
        ("SRTWC286-SH", 3)
    )


def test_g_0818_round_4_all_is_point_form_and_one_number_is_every_line(
    session_factory, monkeypatch, stub_access
):
    """08:18Z (round 4 console test, rulings 1 and 2, and the 08:33Z correction:
    "tia" is a typo of "tiga", a quantity the parser reads)."""
    from app.services.chatbot.turn.apply import STOCK_PICK_NOT_ALL

    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000063")
    assert c.say("check stock srtwc286", stock(product("srtwc286"))) == LIST
    every = reply(reference_positions=list(range(1, 11)), open_question_answer=answer("pick", picked=list(range(1, 11))))
    # AVAIL-MODE-REPLIES (owner, 2 Oct 2026, Q4 (b)) supersedes ruling 1 for the bare
    # word: "all" is refused and the list stays open; every number typed out is a pick.
    assert c.say("all", every) == STOCK_PICK_NOT_ALL
    assert c.say("1,2,3,4,5,6,7,8,9,10", every) == _point_form(OWNER_FAMILY)
    assert c.say("tia", reply(demand_qty=3, open_question_answer=answer("all", qty_for_all=3))) == _answered(
        *[(code, 3) for code in OWNER_FAMILY]
    )


def test_g_1011_round_7_session(session_factory, monkeypatch, stub_access):
    """10:11Z to 10:14Z, the round 7 console test (rows as numbered in its PR comment),
    every row as ruled in round 8, through the engine this time."""
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000060")
    # Row 1 and 2.
    assert c.say("check stock srtwc286", stock(product("srtwc286"))) == LIST
    assert c.say(
        "semua of them",
        reply(reference_positions=list(range(1, 11)), open_question_answer=answer("pick", picked=list(range(1, 11)))),
    ) == _point_form(OWNER_FAMILY)
    # Row 3: one number per line, positions in order.
    assert c.say(
        "10\n20\n30\n40\n5",
        reply(open_question_answer=answer("fill", items=[(1, None, 10), (2, None, 20), (3, None, 30), (4, None, 40), (5, None, 5)])),
    ) == _point_form(OWNER_FAMILY, {1: 10, 2: 20, 3: 30, 4: 40, 5: 5})
    # Row 4: the list pasted back, 1 = 10, 2 = 5, the rest blank: the whole answer.
    row4 = c.say(
        "check stock\n" + _point_form(OWNER_FAMILY, {1: 10, 2: 5}),
        reply(open_question_answer=answer("done", items=[(1, "SRTWC286-SH", 10), (2, "SRTWC286-SH-150", 5)])),
    )
    assert row4.startswith(_answered(("SRTWC286-SH", 10), ("SRTWC286-SH-150", 5)))
    assert "Not checked: " in row4 and "SRTWC286-SH-UF" in row4
    # Row 7: the typo resolves to SRTWC286-SH-150 (resolver), then "5".
    assert c.say("check stock SRTWC286-SH-15", stock(product("SRTWC286-SH-15"))) == (
        "How many units of SRTWC286-SH-150?"
    )
    assert c.say("5", reply(demand_qty=5, open_question_answer=answer("fill", items=[(1, None, 5)]))) == _answered(
        ("SRTWC286-SH-150", 5)
    )
    # Row 8: a did-you-mean of three; "3" picks SRTWC286-SH; 50; "howa bout 30".
    assert c.say("SRTWC286-150", stock(product("SRTWC286-150"))) == numbered(
        "Couldn't find SRTWC286150. Did you mean:", ["SRTWC286-SH-150", "SRTWC287-S-150", "SRTWC286-SH"]
    )
    assert c.say("3", reply(reference_positions=[3], open_question_answer=answer("pick", picked=[3]))) == (
        "How many units of SRTWC286-SH?"
    )
    assert c.say("50", reply(demand_qty=50, open_question_answer=answer("fill", items=[(1, None, 50)]))) == _answered(
        ("SRTWC286-SH", 50)
    )
    assert c.say(
        "howa bout 30",
        reply(demand_qty=30, correction=True, open_question_answer=answer("fill", items=[(1, None, 30)])),
    ) == _answered(("SRTWC286-SH", 30))
    # Row 9: three named products, each with its own quantity, all answered.
    three = [("SRTWC286-SH-150", 5), ("SRTWC287-S-150", 10), ("SRTWC286-SH", 1)]
    assert c.say(
        "1. SRTWC286-SH-150 - 5\n2. SRTWC287-S-150 - 10\n3. SRTWC286-SH - 1",
        stock(*[product(code, qty) for code, qty in three]),
    ) == _answered(*three)
    # Row 10: "how about 3 for all of them" is those three at 3.
    assert c.say(
        "how about 3 for all of them",
        reply(correction=True, open_question_answer=answer("all", qty_for_all=3)),
    ) == _answered(*[(code, 3) for code, _q in three])
    # Row 12: a bare number over a three-product answer asks which, over the same three.
    assert c.say("10", reply(demand_qty=10)) == numbered(
        "Is 10 for all 3 products, or for one of them?", [code for code, _q in three]
    )
    assert c.say("all", reply(open_question_answer=answer("all"))) == _answered(*[(code, 10) for code, _q in three])


# =============================================================================== #
# H. Ten natural phrasings per question kind, through `engine.run_turn`
#
# Each message is paired with the answer object the parser should return for it. The
# words are the parser's to read (the prompt contract, section I); what these prove is
# that every such answer lands the same way.
# =============================================================================== #


#: The did-you-mean pick with the quantity in the same breath (pick_one).
PICK_WITH_QTY = [
    ("the first one, I need 2", answer("pick", picked=[1], qty_for_all=2)),
    ("1, I need 2", answer("pick", picked=[1], qty_for_all=2)),
    ("first one x2", answer("pick", picked=[1], qty_for_all=2)),
    ("number 1 please, 2 units", answer("pick", picked=[1], qty_for_all=2)),
    ("SRTWC286-SH, 2 pcs", answer("pick", items=[(None, "SRTWC286-SH", 2)])),
    ("the top one, two", answer("pick", picked=[1], qty_for_all=2)),
    ("yang pertama, 2 unit", answer("pick", picked=[1], qty_for_all=2)),
    ("nombor satu, dua", answer("pick", picked=[1], qty_for_all=2)),
    ("第一个，要两个", answer("pick", picked=[1], qty_for_all=2)),
    ("yi hao, liang ge", answer("pick", picked=[1], qty_for_all=2)),
]


@pytest.mark.parametrize("message,obj", PICK_WITH_QTY, ids=[m for m, _o in PICK_WITH_QTY])
def test_h_pick_one_with_a_quantity(session_factory, monkeypatch, stub_access, message, obj):
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000042")
    c.say("check stock STWC2867", stock(product("STWC2867")))
    assert c.say(message, reply(open_question_answer=obj)) == _answered(("SRTWC286-SH", 2))


#: Several picked off the family list (pick_one, "both" / "all" / "1 and 3").
MULTI_PICK = [
    ("1 and 3", [1, 3]),
    ("first and third", [1, 3]),
    ("the second and the fourth", [2, 4]),
    ("1, 2", [1, 2]),
    ("both SRTWC286-SH and SRTWC286-SH-150", [1, 2]),
    ("satu dan tiga", [1, 3]),
    ("yang kedua dan ketiga", [2, 3]),
    ("一和三", [1, 3]),
    ("di er ge he di san ge", [2, 3]),
    # AVAIL-MODE-REPLIES: a bare "all" is refused (S30, S34); every number is a pick.
    ("1,2,3,4,5,6,7,8,9,10", list(range(1, 11))),
]


@pytest.mark.parametrize("message,picked", MULTI_PICK, ids=[m for m, _p in MULTI_PICK])
def test_h_pick_one_several_opens_their_quantities(session_factory, monkeypatch, stub_access, message, picked):
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000043")
    c.say("check stock srtwc286", stock(product("srtwc286")))
    out = c.say(message, reply(open_question_answer=answer("pick", picked=picked)))
    assert out == _point_form([OWNER_FAMILY[p - 1] for p in picked])


#: "both" over the two-option did-you-mean, with and without one quantity (pick_one).
BOTH = [
    ("both", answer("pick", picked=[1, 2]), None),
    ("both, 2 each", answer("pick", picked=[1, 2], qty_for_all=2), 2),
    ("dua-dua", answer("pick", picked=[1, 2]), None),
    ("两个都要", answer("pick", picked=[1, 2]), None),
    ("liang ge dou yao, 3 each", answer("pick", picked=[1, 2], qty_for_all=3), 3),
    ("semua, 4 unit", answer("pick", picked=[1, 2], qty_for_all=4), 4),
    ("1 and 2", answer("pick", picked=[1, 2]), None),
    ("both of them please, 5", answer("pick", picked=[1, 2], qty_for_all=5), 5),
    ("the two, 1 each", answer("pick", picked=[1, 2], qty_for_all=1), 1),
    ("kedua-duanya", answer("pick", picked=[1, 2]), None),
]


@pytest.mark.parametrize("message,obj,qty", BOTH, ids=[m for m, _o, _q in BOTH])
def test_h_pick_one_both(session_factory, monkeypatch, stub_access, message, obj, qty):
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000049")
    c.say("check stock STWC2867", stock(product("STWC2867")))
    out = c.say(message, reply(open_question_answer=obj))
    if qty is None:
        assert out == _point_form(DYM)
    else:
        assert out == _answered(*[(code, qty) for code in DYM])


#: The one-option did-you-mean (confirm): five yeses answer it, five noes refer.
CONFIRM = [
    ("yes", "yes"),
    ("ya", "yes"),
    ("betul, yang itu", "yes"),
    ("对", "yes"),
    ("yup that one", "yes"),
    ("no", "no"),
    ("tak", "no"),
    ("bukan", "no"),
    ("不是", "no"),
    ("nope, not that one", "no"),
]


@pytest.mark.parametrize("message,mode", CONFIRM, ids=[m for m, _x in CONFIRM])
def test_h_confirm(session_factory, monkeypatch, stub_access, message, mode):
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000066")
    assert c.say("check stock ELP3753 10", stock(product("ELP3753", 10))) == (
        "Couldn't find ELP3753. Did you mean ELP3754?"
    )
    # The object alone: `is_affirmative` left unset, so the declared answer is what
    # carries the yes or the no.
    out = c.say(message, reply(open_question_answer=answer(mode)))
    if mode == "yes":
        assert out == _answered(("ELP3754", 10))
    else:
        assert out == task_mod.REFER_TO_SALESMAN


#: The point-form quantities question over three products (quantities).
QUANTITIES = [
    ("1. 10, 2. 5, 3. 1", answer("fill", items=[(1, None, 10), (2, None, 5), (3, None, 1)]), [10, 5, 1]),
    ("10 / 5 / 1", answer("fill", items=[(1, None, 10), (2, None, 5), (3, None, 1)]), [10, 5, 1]),
    ("SRTWC286-SH 10, SRTWC286-SH-150 5, SRTWC286-SH-200 1",
     answer("fill", items=[(None, "SRTWC286-SH", 10), (None, "SRTWC286-SH-150", 5), (None, "SRTWC286-SH-200", 1)]), [10, 5, 1]),
    ("3 for all", answer("all", qty_for_all=3), [3, 3, 3]),
    ("semua 3", answer("all", qty_for_all=3), [3, 3, 3]),
    ("每个三个", answer("all", qty_for_all=3), [3, 3, 3]),
    ("first 10, second 5, third 1", answer("fill", items=[(1, None, 10), (2, None, 5), (3, None, 1)]), [10, 5, 1]),
    ("satu 10, dua 5, tiga 1", answer("fill", items=[(1, None, 10), (2, None, 5), (3, None, 1)]), [10, 5, 1]),
    ("yi 10 er 5 san 1", answer("fill", items=[(1, None, 10), (2, None, 5), (3, None, 1)]), [10, 5, 1]),
    ("dua dua ja", answer("all", qty_for_all=2), [2, 2, 2]),
]


@pytest.mark.parametrize("message,obj,qtys", QUANTITIES, ids=[m for m, _o, _q in QUANTITIES])
def test_h_quantities(session_factory, monkeypatch, stub_access, message, obj, qtys):
    three = OWNER_FAMILY[:3]
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000062")
    c.say("check stock srtwc286", stock(product("srtwc286")))
    assert c.say("1 2 3", reply(open_question_answer=answer("pick", picked=[1, 2, 3]))) == _point_form(three)
    assert c.say(message, reply(open_question_answer=obj)) == _answered(*zip(three, qtys))


#: Revising a three-product answer (last_answer).
LAST_ANSWER = [
    ("make line 2 10", answer("fill", items=[(2, None, 10)]), [3, 10, 3]),
    ("the second one 10 instead", answer("fill", items=[(2, None, 10)]), [3, 10, 3]),
    ("SRTWC286-SH-150 10", answer("fill", items=[(None, "SRTWC286-SH-150", 10)]), [3, 10, 3]),
    ("how about 5 for all of them", answer("all", qty_for_all=5), [5, 5, 5]),
    ("semua 5", answer("all", qty_for_all=5), [5, 5, 5]),
    ("全部五个", answer("all", qty_for_all=5), [5, 5, 5]),
    ("yang pertama 1", answer("fill", items=[(1, None, 1)]), [1, 3, 3]),
    ("di san ge yao 8", answer("fill", items=[(3, None, 8)]), [3, 3, 8]),
    ("first 1 and last 8", answer("fill", items=[(1, None, 1), (3, None, 8)]), [1, 3, 8]),
    ("tukar yang kedua jadi sepuluh", answer("fill", items=[(2, None, 10)]), [3, 10, 3]),
]


@pytest.mark.parametrize("message,obj,qtys", LAST_ANSWER, ids=[m for m, _o, _q in LAST_ANSWER])
def test_h_last_answer(session_factory, monkeypatch, stub_access, message, obj, qtys):
    three = OWNER_FAMILY[:3]
    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000064")
    c.say("check stock srtwc286", stock(product("srtwc286")))
    c.say("1 2 3", reply(open_question_answer=answer("pick", picked=[1, 2, 3])))
    assert c.say("3 each", reply(open_question_answer=answer("all", qty_for_all=3))) == _answered(
        *[(code, 3) for code in three]
    )
    assert c.say(message, reply(correction=True, open_question_answer=obj)) == _answered(*zip(three, qtys))


def test_h_family_order_survives_a_reshuffled_heap(session_factory, monkeypatch, stub_access):
    """Deploy of d79b46c5 (shard 1, `test_h_last_answer[全部五个]`): "1 2 3" picked
    SRTWC286-SH-NEW-P, -P and -PP, because the product probes had no ORDER BY and the
    family came back in the order its rows sat on disk. Move the first six rows to the
    end of the heap (a non-HOT update: the code changes and changes back, so each gets a
    new row position and new index entries) and the list must not move."""
    from sqlalchemy import text

    c = EngineConsole(session_factory, monkeypatch, stub_access, phone="+60900000065")
    db = session_factory()
    moved = OWNER_FAMILY[:6]
    db.execute(
        text("UPDATE products SET product_code = product_code || '~' WHERE product_code = ANY(:codes)"),
        {"codes": moved},
    )
    db.execute(
        text("UPDATE products SET product_code = left(product_code, -1) WHERE product_code = ANY(:codes)"),
        {"codes": [f"{code}~" for code in moved]},
    )
    db.commit()
    assert c.say("check stock srtwc286", stock(product("srtwc286"))) == LIST
    c.say("1 2 3", reply(open_question_answer=answer("pick", picked=[1, 2, 3])))
    assert c.say("3 each", reply(open_question_answer=answer("all", qty_for_all=3))) == _answered(
        *[(code, 3) for code in OWNER_FAMILY[:3]]
    )


#: A brand roster (choose_brand), at the apply seam: no dealer stock ask mints one,
#: the operator's Chatbot Domains policy can (`pending.is_roster`).
BRAND_PICKS = [
    ("Sorento", [1]),
    ("the first brand", [1]),
    ("SRT", [1]),
    ("Hafele please", [2]),
    ("the second one", [2]),
    ("yang kedua", [2]),
    ("第二个", [2]),
    ("both brands", [1, 2]),
    ("dua-dua jenama", [1, 2]),
    ("er ge dou yao", [1, 2]),
]


@pytest.mark.parametrize("message,picked", BRAND_PICKS, ids=[m for m, _p in BRAND_PICKS])
def test_h_choose_brand(message, picked):
    roster = turn_pending.ask(
        "brand_pick",
        [
            {"position": 1, "label": "Sorento", "code": "SRT", "uuid": "b-srt", "entity_type": "brand"},
            {"position": 2, "label": "Hafele", "code": "HFL", "uuid": "b-hfl", "entity_type": "brand"},
        ],
        payload={"domain": "promotion", "domains": ["promotion"]},
    )
    assert _question().open_question(roster, ())["kind"] == "choose_brand"
    state = ht.state(Focus(domains=["promotion"]), pending=roster, turn_no=3)
    new, plan = apply(state, reply(domain_hint=None, intent_hint=None, open_question_answer=answer("pick", picked=picked)), build_policy())
    assert "open_question_answer_pick" in plan.trace.rules_fired
    # A brand is a code-only axis on the focus (`apply._CODE_ONLY_FIELDS`).
    assert sorted(new.focus.brands) == sorted({1: "SRT", 2: "HFL"}[p] for p in picked)


# =============================================================================== #
# I. The prompt contract (published by migration, section J)
# =============================================================================== #


def test_i_the_addendum_states_every_kind_and_the_pick_answer():
    from app.services.chatbot_parser_prompt import SEMANTIC_PARSER_PROMPT, STOCK_TASK_ADDENDUM

    assert STOCK_TASK_ADDENDUM in SEMANTIC_PARSER_PROMPT
    text = STOCK_TASK_ADDENDUM
    for kind in ("pick_one", "choose_brand", "confirm", "quantities", "last_answer", "how_many_to_show", "free"):
        assert f'"{kind}"' in text, kind
    for phrase in (
        '"picked"',
        "the first one, I need 2",
        "both",
        "1 and 3",
        "none of them",
        "pertama",
        "dua",
        "第一个",
        "san ge",
    ):
        assert phrase in text, phrase
    # The retired name of the quantity question is gone with it.
    assert "stock_quantities" not in text
    assert EM_DASH not in text and EN_DASH not in text


# =============================================================================== #
# J. The prompt is published as a new, unlabelled version by migration
# =============================================================================== #


_MIGRATION = Path(__file__).resolve().parents[2] / "alembic" / "versions" / "sa2_r9_open_question.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("sa2_r9_open_question", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_j_the_migration_chains_onto_a_committed_revision_with_a_short_id():
    module = _load_migration()
    assert module.revision == "sa2_r9_open_question"
    assert len(module.revision) <= 32
    # Chained onto a committed revision (`alembic-reparent.sh` moves it onto main's
    # head at merge time, so the parent id itself is not pinned here).
    parents = [
        path
        for path in _MIGRATION.parent.glob("*.py")
        if f'\nrevision = "{module.down_revision}"' in path.read_text()
    ]
    assert len(parents) == 1


def test_j_the_migration_publishes_the_contract_unlabelled_and_is_idempotent():
    from app.models.ai_prompt import AIPromptLabel, AIPromptVersion
    from tests._pg_fixture import blank_session

    module = _load_migration()
    with blank_session() as db:
        bind = db.connection()
        module.publish(bind)
        rows = db.query(AIPromptVersion).filter(AIPromptVersion.name == "chatbot_semantic_parser").all()
        # The seeded v1 is the fallback constant itself; the published one is the
        # constant plus the policy blocks, stamped by this revision.
        carrying = [r for r in rows if (r.config_json or {}).get("sa2_r9_open_question")]
        assert len(carrying) == 1
        assert '"pick_one"' in carrying[0].template
        labelled = {
            row.version_id
            for row in db.query(AIPromptLabel).filter(AIPromptLabel.name == "chatbot_semantic_parser")
        }
        assert carrying[0].id not in labelled
        module.publish(bind)
        again = db.query(AIPromptVersion).filter(AIPromptVersion.name == "chatbot_semantic_parser").count()
        assert again == len(rows)
