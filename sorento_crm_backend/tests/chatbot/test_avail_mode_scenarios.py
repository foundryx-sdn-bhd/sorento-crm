"""AVAIL-MODE-REPLIES regression scenario suite (owner, 2 Oct 2026).

Every stock-asking pattern an availability-only (dealer) contact can send, through the real
engine (`tests/chatbot/_avail_mode_console.py`). Each test id (`S01` ...) is a row of the
owner's alignment page `documentation/mockups/avail-mode-scenarios/index.html` and of the
written catalogue `tests/chatbot/AVAIL-MODE-SCENARIOS.md`, which also says what full mode
does instead. A reply asserted here is the reply on the page, word for word.

Plan: documentation/plans/chatbot/PLAN-avail-mode-replies-02oct.md (behaviour card + owner
rulings Q1-Q5).
"""
from __future__ import annotations

from datetime import date

import pytest

from tests.chatbot._avail_mode_console import AvailConsole, Stock
from tests.chatbot._r9_engine_console import OWNER_FAMILY, answer, numbered, product, reply, stock
from tests.chatbot._turn_helpers import entity
from tests.chatbot.test_engine import stub_access  # noqa: F401 - a pytest fixture

pytestmark = pytest.mark.usefixtures("stub_access")

R = "Please refer to your salesman."
ETA = date(2026, 10, 19)
NOT_ALL = "Please reply with the number of the code you need."
PICK10 = numbered("SRTWC286 x 10: which one?", OWNER_FAMILY)
TICK, CROSS, BLOCKED = "\u2705", "\u274c", "\U0001F6AB"
#: Owner v2 note 3 (Q3 now (b)): every picker in ONE message, the numbering running on.
PICK6022 = "\n".join(["SRTWC6022 x 4: which one?", "11. SRTWC6022-SH-UF", "12. SRTWC6022-SH-UF-NEW"])
PICK2 = f"{PICK10}\n\n{PICK6022}"


@pytest.fixture
def console(session_factory, monkeypatch, stub_access):
    def make(**stock_facts: Stock) -> AvailConsole:
        return AvailConsole(
            session_factory, monkeypatch, stub_access, phone="+60100000901", **stock_facts
        )

    return make


def _pick(*positions: int):
    return reply(
        reference_positions=list(positions),
        open_question_answer=answer("pick", picked=list(positions)),
    )


def _eta_ask(*codes: str):
    return reply(entities=[product(c) for c in codes], domain_hint="incoming", intent_hint="check_incoming")


# ================================================================== one code


def test_S01_exact_code_enough_stock(console):
    c = console(SRT5674=Stock(on_hand=100))
    assert c.say("SRT5674 x 50 got stock?", stock(product("SRT5674", 50))) == f"SRT5674 x 50: {TICK} {R}"


def test_S02_exact_code_short_within_the_cap_names_the_count(console):
    c = console(SRT5674=Stock(on_hand=30, x=100))
    assert c.say("SRT5674 x 50", stock(product("SRT5674", 50))) == f"SRT5674 x 50: {TICK} 30 available. {R}"


def test_S03_no_stock_shipment_due(console):
    c = console(SRTW2000=Stock(on_hand=0, x=200, eta=ETA))
    assert c.say("SRTW2000 x 150", stock(product("SRTW2000", 150))) == f"SRTW2000 x 150: {CROSS} ETA 19/10/2026."


def test_S04_no_stock_nothing_incoming(console):
    c = console(SRT5674=Stock(on_hand=0))
    assert c.say("SRT5674 x 5", stock(product("SRT5674", 5))) == f"SRT5674 x 5: {CROSS} No stock and no incoming. {R}"


def test_S05_above_the_category_max_says_nothing_of_our_stock(console):
    c = console(CWCX604=Stock(on_hand=30, x=200))
    assert c.say("CWCX604 x 300", stock(product("CWCX604", 300))) == (
        f"CWCX604 x 300: {BLOCKED} the quantity is more than what I can confirm here. {R}"
    )


def test_S06_no_category_max_set_is_above_it(console):
    c = console(CWCX604=Stock(on_hand=30, x=0))
    assert c.say("CWCX604 x 5", stock(product("CWCX604", 5))) == (
        f"CWCX604 x 5: {BLOCKED} the quantity is more than what I can confirm here. {R}"
    )


def test_S07_exact_code_without_a_quantity_asks_for_it(console):
    c = console(SRT5674=Stock(on_hand=100))
    assert c.say("SRT5674 got stock?", stock(product("SRT5674"))) == "How many units of SRT5674?"
    assert c.say("50", reply(demand_qty=50)) == f"SRT5674 x 50: {TICK} {R}"


def test_S08_prefix_of_exactly_one_code_is_that_code(console):
    c = console(SRTWC287_S_150=Stock(on_hand=9))
    assert c.say("SRTWC287-S x 3", stock(product("SRTWC287-S", 3))) == f"SRTWC287-S-150 x 3: {TICK} {R}"


def test_S09_vague_code_with_a_quantity_picks_then_answers(console):
    c = console(SRTWC286_SH_150=Stock(on_hand=50))
    assert c.say("srtwc286 x 10", stock(product("srtwc286", 10))) == PICK10
    assert c.say("2", _pick(2)) == f"SRTWC286-SH-150 x 10: {TICK} {R}"


def test_S10_vague_code_without_a_quantity_picks_then_asks_it(console):
    c = console()
    assert c.say("check stock srtwc286", stock(product("srtwc286"))) == numbered(
        "SRTWC286 matches 10 products. Which one?", OWNER_FAMILY
    )
    assert c.say("2", _pick(2)) == "How many units of SRTWC286-SH-150?"


def test_S11_not_found_with_a_near_code_is_a_did_you_mean(console):
    c = console(ELP3754=Stock(on_hand=20))
    assert c.say("ELP3753 x 10", stock(product("ELP3753", 10))) == "Couldn't find ELP3753. Did you mean ELP3754?"
    assert c.say("yes", reply(is_affirmative=True, open_question_answer=answer("yes"))) == f"ELP3754 x 10: {TICK} {R}"


def test_S12_not_found_and_nothing_near(console):
    c = console()
    assert c.say("FOO99 x 1", stock(product("FOO99", 1))) == f'Couldn\'t find: "FOO99" (product).\n\n{R}'


# ================================================================== ETA asks


def test_S14_eta_ask_one_code_one_line(console):
    c = console(SRTW2000=Stock(eta=ETA))
    assert c.say("ETA SRTW2000?", _eta_ask("SRTW2000")) == f"SRTW2000: {TICK} ETA 19/10/2026\n\n{R}"


def test_S15_eta_ask_several_codes_one_line_each(console):
    c = console(SRTW2000=Stock(eta=ETA))
    assert c.say("ETA SRTW2000 and MWT5727SS-CR", _eta_ask("SRTW2000", "MWT5727SS-CR")) == (
        f"SRTW2000: {TICK} ETA 19/10/2026\n\nMWT5727SS-CR: No ETA\n\n{R}"
    )


def test_S16_eta_ask_with_a_code_not_found(console):
    c = console(SRTW2000=Stock(eta=ETA))
    assert c.say("ETA SRTW2000 and FOO99", _eta_ask("SRTW2000", "FOO99")) == (
        f"SRTW2000: {TICK} ETA 19/10/2026\n\nCouldn't find: FOO99.\n\n{R}"
    )


# ================================================================== several codes


def test_S17_all_exact_one_line_each_in_asked_order(console):
    c = console(SRT5674=Stock(on_hand=100), CWCX604=Stock(on_hand=30, x=200), SRTW2000=Stock(eta=ETA))
    out = c.say(
        "SRT5674 x 50, CWCX604 x 40, SRTW2000 x 10",
        stock(product("SRT5674", 50), product("CWCX604", 40), product("SRTW2000", 10)),
    )
    assert out == (
        f"SRT5674 x 50: {TICK} {R}\n\nCWCX604 x 40: {TICK} 30 available. {R}\n\n"
        f"SRTW2000 x 10: {CROSS} ETA 19/10/2026."
    )


def test_S18_exact_and_not_found(console):
    c = console(SRT5674=Stock(on_hand=100))
    out = c.say("SRT5674 x 5, FOO99 x 1", stock(product("SRT5674", 5), product("FOO99", 1)))
    assert out == f"SRT5674 x 5: {TICK} {R}\n\nCouldn't find: FOO99."


def test_S19_exact_and_a_prefix_of_one_code(console):
    c = console(SRT5674=Stock(on_hand=100), SRTWC287_S_150=Stock(on_hand=9))
    out = c.say("SRT5674 x 5, SRTWC287-S x 3", stock(product("SRT5674", 5), product("SRTWC287-S", 3)))
    assert out == f"SRT5674 x 5: {TICK} {R}\n\nSRTWC287-S-150 x 3: {TICK} {R}"


def test_S20_exact_and_vague_answers_the_exact_and_picks_the_vague(console):
    c = console(SRT5674=Stock(on_hand=100), SRTWC286_SH_150=Stock(on_hand=50))
    out = c.say("SRT5674 x 5, srtwc286 x 10", stock(product("SRT5674", 5), product("srtwc286", 10)))
    assert out == f"SRT5674 x 5: {TICK} {R}\n\n{PICK10}"
    assert c.stored_question["kind"] == "product_pick"
    assert c.say("2", _pick(2)) == f"SRTWC286-SH-150 x 10: {TICK} {R}"


def test_S21_two_vague_codes_both_pickers_in_one_message(console):
    """Owner v2 note 3 (Q3 now (b)): one message for all pickers, numbering running on."""
    c = console(SRTWC286_SH_150=Stock(on_hand=50))
    assert c.say("srtwc286 x 10, srtwc6022 x 4", stock(product("srtwc286", 10), product("srtwc6022", 4))) == PICK2
    assert [o["code"] for o in c.stored_question["options"]] == OWNER_FAMILY + [
        "SRTWC6022-SH-UF",
        "SRTWC6022-SH-UF-NEW",
    ]
    assert c.say("2 and 11", _pick(2, 11)) == (
        f"SRTWC286-SH-150 x 10: {TICK} {R}\n\nSRTWC6022-SH-UF x 4: {CROSS} No stock and no incoming. {R}"
    )


def test_S21b_one_list_answered_the_other_asked_again_with_its_numbers(console):
    c = console(SRTWC286_SH_150=Stock(on_hand=50))
    c.say("srtwc286 x 10, srtwc6022 x 4", stock(product("srtwc286", 10), product("srtwc6022", 4)))
    assert c.say("2", _pick(2)) == f"SRTWC286-SH-150 x 10: {TICK} {R}\n\n{PICK6022}"
    assert c.say("12", _pick(12)) == f"SRTWC6022-SH-UF-NEW x 4: {CROSS} No stock and no incoming. {R}"


def test_S22_vague_and_not_found(console):
    c = console()
    out = c.say("srtwc286 x 10, FOO99 x 1", stock(product("srtwc286", 10), product("FOO99", 1)))
    assert out == f"Couldn't find: FOO99.\n\n{PICK10}"


def test_S22b_exact_two_vague_and_not_found_in_one_reply(console):
    c = console(SRT5674=Stock(on_hand=100))
    out = c.say(
        "SRT5674 x 5, srtwc286 x 10, srtwc6022 x 4, FOO99 x 1",
        stock(product("SRT5674", 5), product("srtwc286", 10), product("srtwc6022", 4), product("FOO99", 1)),
    )
    assert out == f"SRT5674 x 5: {TICK} {R}\n\nCouldn't find: FOO99.\n\n{PICK2}"


def test_S23_all_not_found(console):
    c = console()
    out = c.say("FOO99 x 1, BAR12 x 2", stock(product("FOO99", 1), product("BAR12", 2)))
    assert out == f'Couldn\'t find: "FOO99" (product), "BAR12" (product).\n\n{R}'


def test_S24_a_code_named_twice_adds_up_into_one_line(console):
    """Owner Q2 (a)."""
    c = console(SRT5674=Stock(on_hand=100))
    out = c.say("SRT5674 x 2, SRT5674 x 3", stock(product("SRT5674", 2), product("SRT5674", 3)))
    assert out == f"SRT5674 x 5: {TICK} {R}"


def test_S25_several_codes_without_quantities_point_form_then_per_line(console):
    c = console(SRT5674=Stock(on_hand=100), CWCX604=Stock(on_hand=100, x=200))
    assert c.say("SRT5674, CWCX604 got stock?", stock(product("SRT5674"), product("CWCX604"))) == (
        "How many units for each?\n1. SRT5674 - \n2. CWCX604 - "
    )
    out = c.say(
        "1. 10, 2. 5",
        reply(open_question_answer=answer("fill", items=[(1, "SRT5674", 10), (2, "CWCX604", 5)])),
    )
    assert out == f"SRT5674 x 10: {TICK} {R}\n\nCWCX604 x 5: {TICK} {R}"


def test_S26_no_quantities_and_not_found_names_the_miss_first(console):
    c = console()
    out = c.say("SRT5674, CWCX604, FOO99 got stock?", stock(product("SRT5674"), product("CWCX604"), product("FOO99")))
    assert out == "Couldn't find: FOO99.\n\nHow many units for each?\n1. SRT5674 - \n2. CWCX604 - "


def test_S27_some_with_quantities_answers_those_and_asks_the_rest(console):
    c = console(SRT5674=Stock(on_hand=100), CWCX604=Stock(on_hand=100, x=200))
    out = c.say("SRT5674 x 5 and CWCX604", stock(product("SRT5674", 5), product("CWCX604")))
    assert out == f"SRT5674 x 5: {TICK} {R}\n\nHow many units of CWCX604?"
    assert c.say("7", reply(demand_qty=7)) == f"CWCX604 x 7: {TICK} {R}"


def test_S28_one_number_applies_to_every_product_asked(console):
    c = console(SRT5674=Stock(on_hand=100), CWCX604=Stock(on_hand=100, x=200))
    c.say("SRT5674, CWCX604 got stock?", stock(product("SRT5674"), product("CWCX604")))
    assert c.say("10", reply(demand_qty=10)) == f"SRT5674 x 10: {TICK} {R}\n\nCWCX604 x 10: {TICK} {R}"


def test_S29_short_stock_beside_an_eta_line(console):
    c = console(SRT5674=Stock(on_hand=30), SRTW2000=Stock(x=200, eta=ETA))
    out = c.say("SRT5674 x 50, SRTW2000 x 150", stock(product("SRT5674", 50), product("SRTW2000", 150)))
    assert out == f"SRT5674 x 50: {TICK} 30 available. {R}\n\nSRTW2000 x 150: {CROSS} ETA 19/10/2026."


# ================================================================== picker replies


def test_S30_bare_all_is_refused_and_the_list_stays_open(console):
    c = console()
    assert c.say("srtwc286 x 10", stock(product("srtwc286", 10))) == PICK10
    calls = len(c.stock_calls)
    out = c.say("all", reply(broaden_axis="all", entity_op="clear", open_question_answer=answer("all")))
    assert out == NOT_ALL
    assert len(c.stock_calls) == calls, "an 'all' fetches nothing"
    question = c.stored_question
    assert question is not None and question["kind"] == "product_pick"
    assert [o["code"] for o in question["options"]] == OWNER_FAMILY


def test_S31_all_of_them_as_a_broaden_alone_is_refused_too(console):
    c = console()
    c.say("srtwc286 x 10", stock(product("srtwc286", 10)))
    assert c.say("all of them", reply(broaden_axis="all", entity_op="clear")) == NOT_ALL


def test_S32_picking_every_number_is_allowed_and_answered(console):
    """Owner Q4 (b): only the explicit "all" is refused; "1,2,...,10" is answered."""
    c = console(SRTWC286_SH_150=Stock(on_hand=50))
    c.say("srtwc286 x 10", stock(product("srtwc286", 10)))
    every = list(range(1, len(OWNER_FAMILY) + 1))
    out = c.say(",".join(map(str, every)), _pick(*every))
    assert out.split("\n\n") == [
        f"{code} x 10: {TICK if code == 'SRTWC286-SH-150' else CROSS + ' No stock and no incoming.'} {R}"
        for code in OWNER_FAMILY
    ]


def test_S33_after_a_refusal_a_number_answers_that_code(console):
    c = console(SRTWC286_SH_150=Stock(on_hand=50))
    c.say("srtwc286 x 10", stock(product("srtwc286", 10)))
    assert c.say("all", reply(broaden_axis="all", open_question_answer=answer("all"))) == NOT_ALL
    assert c.say("2", _pick(2)) == f"SRTWC286-SH-150 x 10: {TICK} {R}"


@pytest.mark.parametrize("typed", ["all", "All of them", "semua", "all pls"])
def test_S34_all_read_by_the_parser_as_every_position_is_still_refused(console, typed):
    """The live prompt reads "all" / "semua" over a pick as EVERY position; the engine
    reads the bare word itself, so the parser's expansion is not a way round rule 4."""
    c = console()
    c.say("srtwc286 x 10", stock(product("srtwc286", 10)))
    every = list(range(1, len(OWNER_FAMILY) + 1))
    assert c.say(typed, _pick(*every)) == NOT_ALL


def test_S35_several_numbers_each_answered(console):
    c = console(SRTWC286_SH_150=Stock(on_hand=50))
    c.say("srtwc286 x 10", stock(product("srtwc286", 10)))
    assert c.say("1 and 2", _pick(1, 2)) == (
        f"SRTWC286-SH x 10: {CROSS} No stock and no incoming. {R}\n\nSRTWC286-SH-150 x 10: {TICK} {R}"
    )


# ================================================================== quantity + position picks (v2 note 2)


def _qty_of(*pairs: tuple[int, int]):
    """ "2 of 3" as the parser reads it: option 3, quantity 2 (the number after "of" is
    always the option)."""
    return reply(
        reference_positions=[p for p, _q in pairs],
        demand_qty=pairs[0][1] if len(pairs) == 1 else None,
        open_question_answer=answer("pick", items=[(p, None, q) for p, q in pairs]),
    )


FAMILY_ASK = numbered("SRTWC286 matches 10 products. Which one?", OWNER_FAMILY)


@pytest.mark.parametrize("typed", ["2 of 3", "i want 2 of the third one", "2 of 3rd product"])
def test_S36_S37_quantity_of_a_position(console, typed):
    c = console()
    assert c.say("check stock srtwc286", stock(product("srtwc286"))) == FAMILY_ASK
    assert c.say(typed, _qty_of((3, 2))) == f"SRTWC286-SH-200 x 2: {CROSS} No stock and no incoming. {R}"


def test_S38_several_quantity_position_pairs(console):
    c = console()
    c.say("check stock srtwc286", stock(product("srtwc286")))
    assert c.say("2 of 1 and 5 of 3", _qty_of((1, 2), (3, 5))) == (
        f"SRTWC286-SH x 2: {CROSS} No stock and no incoming. {R}\n\nSRTWC286-SH-200 x 5: {CROSS} No stock and no incoming. {R}"
    )


def test_S39_a_quantity_in_the_pick_replaces_the_typed_one(console):
    c = console(SRTWC286_SH_150=Stock(on_hand=50))
    assert c.say("srtwc286 x 10", stock(product("srtwc286", 10))) == PICK10
    assert c.say("2 of 2", _qty_of((2, 2))) == f"SRTWC286-SH-150 x 2: {TICK} {R}"


# ================================================================== reviewer round 1


def test_S21c_a_quantity_typed_over_a_handed_on_list_keeps_its_numbers(console):
    """Reviewer B3: the SRTWC6022 list handed on as 11-12 stays 11-12 when "4" is typed."""
    c = console()
    c.say("srtwc286 x 10, srtwc6022", stock(product("srtwc286", 10), product("srtwc6022")))
    c.say("2", _pick(2))
    assert c.say("4", reply(demand_qty=4)) == PICK6022
    assert c.say("12", _pick(12)) == f"SRTWC6022-SH-UF-NEW x 4: {CROSS} No stock and no incoming. {R}"


def test_S40_exact_code_without_a_quantity_beside_a_vague_one_asks_the_pick_first(console):
    """Reviewer B4: one question at a time, nothing lost. The pick comes first; the exact
    code's quantity is asked once the pick is answered, and the pick's own x 10 stays."""
    c = console(SRT5674=Stock(on_hand=100), SRTWC286_SH_150=Stock(on_hand=50))
    assert c.say("SRT5674 and srtwc286 x 10", stock(product("SRT5674"), product("srtwc286", 10))) == PICK10
    assert c.say("2", _pick(2)) == f"SRTWC286-SH-150 x 10: {TICK} {R}\n\nHow many units of SRT5674?"
    assert c.say("5", reply(demand_qty=5)) == f"SRT5674 x 5: {TICK} {R}"


def test_S41_a_picked_code_owing_a_quantity_does_not_drop_the_other_list(console):
    """Reviewer S1: the SRTWC6022 list is asked before the picked code's quantity."""
    c = console(SRTWC286_SH_150=Stock(on_hand=50))
    first = c.say("srtwc286, srtwc6022 x 4", stock(product("srtwc286"), product("srtwc6022", 4)))
    assert first == numbered("SRTWC286 matches 10 products. Which one?", OWNER_FAMILY) + f"\n\n{PICK6022}"
    assert c.say("2", _pick(2)) == PICK6022
    assert c.say("11", _pick(11)) == (
        f"SRTWC6022-SH-UF x 4: {CROSS} No stock and no incoming. {R}\n\nHow many units of SRTWC286-SH-150?"
    )
    assert c.say("3", reply(demand_qty=3)) == f"SRTWC286-SH-150 x 3: {TICK} {R}"


def test_S42_a_near_miss_inside_a_mix_is_listed_without_a_did_you_mean(console):
    """Reviewer S4: the did-you-mean is for a message that names one code (S11)."""
    c = console(SRT5674=Stock(on_hand=100), ELP3754=Stock(on_hand=20))
    out = c.say("SRT5674 x 5, ELP3753 x 1", stock(product("SRT5674", 5), product("ELP3753", 1)))
    assert out == f"SRT5674 x 5: {TICK} {R}\n\nCouldn't find: ELP3753."


# ================================================================== tester-local pass on 7fa5d654
# The live parser's own readings, as the crew tester's console trace recorded them.


def test_S43_two_of_three_read_by_the_live_parser_as_option_two_is_option_three_qty_two(console):
    """Step 7: the live parser read "2 of 3" as option 2, quantity 3. The number after
    "of" is the option (v2 note 2), so the engine reads the bare shape itself."""
    c = console()
    c.say("check stock srtwc286", stock(product("srtwc286")))
    misread = reply(
        reference_positions=[2], demand_qty=3, open_question_answer=answer("pick", items=[(2, None, 3)])
    )
    assert c.say("2 of 3", misread) == f"SRTWC286-SH-200 x 2: {CROSS} No stock and no incoming. {R}"


@pytest.mark.parametrize("typed", ["2 of the third one", "i want 2 of 3rd product", "2 of no 3", "2 pcs of 3"])
def test_S43b_quantity_of_a_position_worded_any_way(console, typed):
    c = console()
    c.say("check stock srtwc286", stock(product("srtwc286")))
    misread = reply(
        reference_positions=[2], demand_qty=3, open_question_answer=answer("pick", items=[(2, None, 3)])
    )
    assert c.say(typed, misread) == f"SRTWC286-SH-200 x 2: {CROSS} No stock and no incoming. {R}"


def test_S43c_several_pairs_read_off_the_message(console):
    c = console()
    c.say("check stock srtwc286", stock(product("srtwc286")))
    misread = reply(
        reference_positions=[2, 5],
        open_question_answer=answer("pick", items=[(2, None, 1), (5, None, 3)]),
    )
    assert c.say("2 of 1 and 5 of 3", misread) == (
        f"SRTWC286-SH x 2: {CROSS} No stock and no incoming. {R}\n\nSRTWC286-SH-200 x 5: {CROSS} No stock and no incoming. {R}"
    )


def test_S44_a_code_named_twice_merged_by_the_live_parser_still_adds_up(console):
    """Step 12: the live parser merged "SRT5674 x 2, SRT5674 x 3" into ONE entity with
    the last quantity (`entity_op: replace_combine`), so the S24 sum never ran."""
    c = console(SRT5674=Stock(on_hand=100))
    merged = stock(product("SRT5674", 3), entity_op="replace_combine")
    assert c.say("SRT5674 x 2, SRT5674 x 3", merged) == f"SRT5674 x 5: {TICK} {R}"


def test_S44b_a_code_named_once_is_not_summed_with_another_number(console):
    c = console(SRT5674=Stock(on_hand=100))
    assert c.say("SRT5674 x 2 for site 3", stock(product("SRT5674", 2))) == f"SRT5674 x 2: {TICK} {R}"


def test_S45_eta_ask_for_a_code_with_no_shipment_and_a_code_not_found(console):
    """Step 15 (as scripted): SRTW2000 has no shipment. The real dealer view lists it
    with no ETA, so the reply is the catalogue's S15/S16 shape, not "Related products"."""
    c = console()
    assert c.say("ETA SRTW2000 and FOO99", _eta_ask("SRTW2000", "FOO99")) == (
        f"SRTW2000: No ETA\n\nCouldn't find: FOO99.\n\n{R}"
    )


# ================================================================== tester re-run on 2eb2a00ef
# The live parser intermittently read a plain "CODE x N" stock ask as `check_incoming`
# (step 3 failed 2 of 3 runs, step 1 once), so the dealer was told "CODE: ETA not
# confirmed yet". The engine reads the message's own words: codes with quantities and no
# ETA word are a stock ask, whatever the parser guessed.


def _misread_as_eta(*entities, **extra):
    return reply(entities=list(entities), domain_hint="incoming", intent_hint="check_incoming", **extra)


def test_S46_code_x_qty_misread_as_an_eta_ask_is_still_a_stock_ask(console):
    c = console(CWCX604=Stock(on_hand=30, x=200))
    assert c.say("CWCX604 x 300", _misread_as_eta(product("CWCX604", 300))) == (
        f"CWCX604 x 300: {BLOCKED} the quantity is more than what I can confirm here. {R}"
    )


def test_S46b_misread_through_asks_too(console):
    c = console(SRT5674=Stock(on_hand=30))
    misread = _misread_as_eta(product("SRT5674", 50), asks=[{"domain": "incoming"}])
    assert c.say("SRT5674 x 50", misread) == f"SRT5674 x 50: {TICK} 30 available. {R}"


@pytest.mark.parametrize("typed", ["SRTW2000 x 10 when arrive?", "ETA SRTW2000 x 10", "SRTW2000 x 10 bila sampai"])
def test_S46c_a_quantity_ask_with_an_eta_word_stays_an_eta_ask(console, typed):
    c = console(SRTW2000=Stock(eta=ETA))
    assert c.say(typed, _misread_as_eta(product("SRTW2000", 10))) == f"SRTW2000: {TICK} ETA 19/10/2026\n\n{R}"


# ================================================================== owner hand test, 3 Oct 2026
# The owner's own messages (copy :3109 at 186f4f9ff). Availability access only.

DYM5764_HEAD = "Couldn't find SRT5764. Did you mean:"
#: The three suggestions tie on score, so their order is the database's collation (C
#: puts SRT57-CR first, en_US last; CI run 37096129151). The reply is pinned line by line
#: in whichever order it came.
DYM5764_CODES = {"SRT57-CR", "SRT5713", "SRT5732"}


def _dym5764_positions(out: str) -> dict[str, int]:
    """The did-you-mean for srt5764, asserted whole: its header, then exactly the three
    suggestions numbered 1-3. Returns code -> its position."""
    head, *lines = out.split("\n")
    assert head == DYM5764_HEAD, out
    assert [line.split(". ", 1)[0] for line in lines] == ["1", "2", "3"], out
    positions = {line.split(". ", 1)[1]: int(line.split(". ", 1)[0]) for line in lines}
    assert set(positions) == DYM5764_CODES, out
    return positions


@pytest.mark.parametrize(
    "parsed",
    [
        stock(product("srt5764 xx", 10)),
        stock(product("srt5764"), product("xx", 10)),
        stock(product("srt5764"), product("10")),
    ],
    ids=["one_token", "xx_token", "ten_token"],
)
def test_S47_unknown_code_never_dumps_the_catalogue(console, parsed, monkeypatch):
    """Fine-tune 1: 'srt5764 xx 10' listed what looked like every product. An unknown
    code goes to the did-you-mean; a word or a bare number is never a product.

    The live resolver's describe / semantic tiers matched the stray "xx" / "10" to a
    page of catalogue rows (2001, 2002, 2120H, 1/2" ULTRA CIRCULAR, 32MM TAIL PIECE
    COUPLING, ...); this sandbox has no embedding provider, so those matches are
    added to the resolver's answer here, as the copy returned them."""
    from app.services.chatbot import turn_runtime

    c = console()
    junk = ["2001", "2002", "2120H", "1/2 ULTRA CIRCULAR", "32MM TAIL PIECE COUPLING"]
    real = turn_runtime.resolve_kinds

    def broad(*args, **kwargs):
        out = real(*args, **kwargs)
        extra = [
            {"raw": code, "canonical_code": code, "uuid": c.uuid_of[code], "hint": "product"}
            for code in junk
        ]
        out.resolved_candidates.setdefault("product", []).extend(extra)
        out.compatible_entities.extend(
            {"uuid": e["uuid"], "entity_type": "product", "code": e["raw"]} for e in extra
        )
        return out

    monkeypatch.setattr(turn_runtime, "resolve_kinds", broad)
    out = c.say("srt5764 xx 10", parsed)
    _dym5764_positions(out)
    assert "How many units" not in out


@pytest.mark.parametrize(
    "picked",
    [
        lambda n: reply(entities=[product(str(n))]),
        lambda n: reply(entities=[product(str(n))], demand_qty=n),
        lambda n: reply(reference_positions=[n], demand_qty=n, open_question_answer=answer("pick", picked=[n])),
        lambda n: reply(demand_qty=n),
    ],
    ids=["as_a_product", "as_a_product_and_qty", "as_a_pick_and_qty", "as_a_qty"],
)
def test_S48_a_number_over_a_did_you_mean_is_that_option(console, picked):
    """Fine-tune 2: 'srt5764 10' -> did-you-mean -> '2' (SRT5713's number) must answer
    SRT5713 x 10, the same position reading every other picker uses (whatever the parser
    made of the number)."""
    c = console(SRT5713=Stock(on_hand=50))
    n = _dym5764_positions(c.say("srt5764 10", stock(product("srt5764", 10))))["SRT5713"]
    assert c.say(str(n), picked(n)) == f"SRT5713 x 10: {TICK} {R}"


def test_S48b_a_number_past_the_list_is_still_a_quantity(console):
    c = console()
    c.say("srtwc286", stock(product("srtwc286")))
    out = c.say("88", reply(demand_qty=88))
    assert out.startswith("SRTWC286 x 88: which one?"), out


def test_S49_eta_with_a_date_is_a_tick_and_none_is_no_eta(console):
    """Fine-tune 3: an ETA reads '✅ ETA dd/mm/yyyy'; no ETA reads 'No ETA'."""
    c = console(SRTW2000=Stock(eta=ETA))
    assert c.say("ETA SRTW2000 and MWT5727SS-CR", _eta_ask("SRTW2000", "MWT5727SS-CR")) == (
        f"SRTW2000: {TICK} ETA 19/10/2026\n\nMWT5727SS-CR: No ETA\n\n{R}"
    )


def test_S50_a_bare_eta_after_an_exact_code_asks_only_that_code(console):
    """Fine-tune 4: 'srtw2000 20' then 'eta' listed SRTW2000-SS-CR, -A, -NL as well."""
    c = console(SRTW2000=Stock(x=200, eta=ETA))
    c.say("srtw2000 20", stock(product("srtw2000", 20)))
    out = c.say("eta", reply(entities=[], domain_hint="incoming", intent_hint="check_incoming"))
    assert out == f"SRTW2000: {TICK} ETA 19/10/2026\n\n{R}"
    (last,) = [a for name, a in c.tool_calls if name == "crm_incoming_stock_list"][-1:]
    assert [c.codes[p] for p in last["product_ids"]] == ["SRTW2000"]


def test_S50b_an_exact_code_eta_ask_and_its_bare_follow_up_stay_exact(console):
    c = console()
    first = c.say("eta SRTWC286-SH", _eta_ask("SRTWC286-SH"))
    assert first == f"SRTWC286-SH: No ETA\n\n{R}"
    assert c.say("eta", reply(entities=[], domain_hint="incoming", intent_hint="check_incoming")) == first


def test_S50c_an_exact_code_stock_ask_carries_only_that_code(console):
    c = console(SRTW2000=Stock(x=200, eta=ETA))
    c.say("srtw2000 20", stock(product("srtw2000", 20)))
    carried = [p.get("canonical_code") for p in (c.state or {}).get("focus", {}).get("products", [])]
    assert carried == ["SRTW2000"]


# ================================================================== tester re-run on a5ba9dc9f (3 Oct)
# The live parser's other readings of the owner's messages (crew-tester, 3 runs each).

BARE_ETA = reply(entities=[], domain_hint="incoming", intent_hint="check_incoming")


@pytest.mark.parametrize(
    "parsed",
    [
        stock(entity("srt5764 xx 10", confident=False)),
        stock(entity("srt5764 xx 10", None, confident=False)),
        stock(entity("srt5764", confident=False), entity("xx", None, confident=False), demand_qty=10),
        stock(entity("srt5764 xx", confident=False), demand_qty=10),
    ],
    ids=["unsure_product", "unsure_unlabeled", "unsure_split", "unsure_with_qty"],
)
def test_S51_an_unsure_capture_with_a_code_is_still_the_did_you_mean(console, parsed):
    """Fix 1 runs 2-3: the parser read 'srt5764 xx 10' as an unsure capture and the reply
    was 'I captured "srt5764 xx 10" but couldn't tell which part is which.' A code-like
    token the catalogue does not carry is the did-you-mean, every run."""
    c = console()
    out = c.say("srt5764 xx 10", parsed)
    _dym5764_positions(out)


@pytest.mark.parametrize(
    "first",
    [
        reply(entities=[product("srtw2000")], domain_hint="order", intent_hint="check_order"),
        reply(entities=[product("srtw2000")], domain_hint="order", intent_hint="check_order", order_status="outstanding"),
        stock(product("srtw2000")),
    ],
    ids=["routed_to_orders", "routed_to_outstanding", "stock_ask"],
)
def test_S52_a_bare_eta_after_a_bare_code_asks_only_that_code(console, first):
    """Fix 4: 'srtw2000' then 'eta' (3/3) listed SRTW2000-SS-CR, SRTW2000-A and
    SRTW2000-NL. Turn 1 routed to an order list or the outstanding report, so the carried
    focus held the whole prefix family. Availability access: the exact code only."""
    c = console(SRTW2000=Stock(x=200, eta=ETA))
    c.say("srtw2000", first)
    out = c.say("eta", BARE_ETA)
    assert out == f"SRTW2000: {TICK} ETA 19/10/2026\n\n{R}"
    (last,) = [a for name, a in c.tool_calls if name == "crm_incoming_stock_list"][-1:]
    assert [c.codes[p] for p in last["product_ids"]] == ["SRTW2000"]


@pytest.mark.parametrize(
    "first",
    [
        reply(entities=[product("SRTWC286-SH")], domain_hint="order", intent_hint="check_order"),
        reply(entities=[product("SRTWC286-SH")], domain_hint="order", intent_hint="check_order", order_status="outstanding"),
    ],
    ids=["routed_to_orders", "routed_to_outstanding"],
)
def test_S52b_a_bare_eta_after_an_exact_family_head_asks_only_that_code(console, first):
    """'SRTWC286-SH' then 'eta' listed -NEW, -NEW-200, -150, -200, -P, -PP, -UF too."""
    c = console()
    c.say("SRTWC286-SH", first)
    assert c.say("eta", BARE_ETA) == f"SRTWC286-SH: No ETA\n\n{R}"


def test_S53_an_eta_ask_for_a_family_tells_nothing_until_picked(console):
    """'eta SRTWC286' (not a full code) is a which-one picker; no variant's ETA or stock
    is told until the dealer picks one, then only that one's."""
    c = console(**{"SRTWC286-SH-150": Stock(eta=ETA)})
    out = c.say("eta SRTWC286", _eta_ask("SRTWC286"))
    assert "ETA" not in out and TICK not in out and CROSS not in out and "19/10" not in out, out
    # Cloud live-parser pass at 2ff7f5e9: every line read "- has incoming".
    assert "has incoming" not in out and "no incoming" not in out and "None of these" not in out, out
    assert "1. SRTWC286-SH" in out and "2. SRTWC286-SH-150" in out, out
    assert c.say("2", _pick(2)) == f"SRTWC286-SH-150: {TICK} ETA 19/10/2026\n\n{R}"
    (last,) = [a for name, a in c.tool_calls if name == "crm_incoming_stock_list"][-1:]
    assert [c.codes[p] for p in last["product_ids"]] == ["SRTWC286-SH-150"]


# ================================================================== cloud live-parser pass at 2ff7f5e9 (3 Oct)


def _misread_as_product_info(*entities):
    return reply(entities=list(entities), domain_hint="master_products", intent_hint="check_product")


def test_S54_a_code_with_a_quantity_read_as_a_product_info_ask_is_still_a_stock_ask(console):
    """Live parser (gpt-5.4-mini), 1 of 3 runs: 'srt5764 xx 10' read as check_product /
    master_products, and the dealer got the staff did-you-mean with an escalation offer
    ('Reply with a code to continue, or would you like me to escalate to purchasing
    team?'). A code with a quantity and no ETA word is a stock ask, whatever domain the
    parser named."""
    c = console()
    _dym5764_positions(c.say("srt5764 xx 10", _misread_as_product_info(product("srt5764 xx", 10))))


def test_S54b_an_exact_code_with_a_quantity_read_as_product_info_answers_stock(console):
    c = console(SRT5674=Stock(on_hand=30))
    out = c.say("SRT5674 x 50", _misread_as_product_info(product("SRT5674", 50)))
    assert out == f"SRT5674 x 50: {TICK} 30 available. {R}"


@pytest.mark.parametrize(
    "parsed",
    [
        _misread_as_product_info(product("srt5764", 10)),
        reply(entities=[product("srt5764")], domain_hint="incoming", intent_hint="check_incoming"),
    ],
    ids=["product_info_code_only", "incoming_no_quantity"],
)
def test_S54c_the_quantity_need_not_follow_the_code(console, parsed):
    """Live parser, step 23 runs 1-2 on the second pass: the entity was 'srt5764' and
    the '10' came after 'xx', so 'code then number' never matched and the dealer got the
    incoming / staff did-you-mean. Any standalone number in a message with no ETA word
    makes it a stock ask."""
    c = console()
    _dym5764_positions(c.say("srt5764 xx 10", parsed))
