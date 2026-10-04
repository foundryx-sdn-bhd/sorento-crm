"""LOWSTOCK-FILTER-ASK: the shared required-field collection helper.

`documentation/plans/chatbot/lowstock-filter-ask-behaviour-card.md`, owner ruling Q2: one
helper, configured per ask type with the list of REQUIRED fields. For each field: take it
from the message when given and valid, accept "all" when the user says so, otherwise ask
only for what is missing. Changing the required set is config, not code.

Pure: every resolver here is a fake, so the helper is pinned without a database. The low
stock wiring through a real turn is `test_low_stock_filter_ask.py`.
"""
from __future__ import annotations

from app.services.chatbot import required_fields as rf

CATEGORIES = {"water tap": ["SRT-FT", "CB-FT"], "water closet": ["SRT-WC"], "srt-ft": ["SRT-FT"]}


def _category(_db, word: str, _extras=None) -> rf.Resolved:
    found = CATEGORIES.get(word.strip().lower())
    if found is None:
        return rf.Resolved("unknown")
    return rf.Resolved("ok", value=found, label=", ".join(found))


def _colour(_db, word: str, _extras=None) -> rf.Resolved:
    w = word.strip().lower()
    if w == "blue":
        return rf.Resolved("ok", value="BL", label="Blue")
    if w == "bl":
        return rf.Resolved("ambiguous", options=(("BL", "Blue"), ("BK", "Black")))
    return rf.Resolved("unknown")


CATEGORY = rf.FieldSpec(
    name="category",
    noun="category",
    question='Which product category? Reply with a category (e.g. water tap) or "all".',
    resolve=_category,
)
COLOUR = rf.FieldSpec(name="colour", noun="colour", question="Which colour?", resolve=_colour, allow_all=False)

ONE = rf.AskType(
    name="test_one",
    fields=(CATEGORY,),
    reroute={"intent_hint": "test_one"},
    cancelled="Test cancelled.",
    give_up="I still can't place '{word}'. Ask again.",
)
TWO = rf.AskType(
    name="test_two",
    fields=(CATEGORY, COLOUR),
    reroute={"intent_hint": "test_two"},
    cancelled="Test cancelled.",
    give_up="I still can't place '{word}'. Ask again.",
)


def _start(ask, **given):
    return rf.collect(None, ask, given=given)


def _reply(ask, outcome, text):
    return rf.collect(None, ask, slot=outcome.slot, reply=text)


class TestFromTheMessage:
    def test_a_field_named_in_the_message_is_taken_and_nothing_is_asked(self):
        out = _start(ONE, category="water tap")
        assert out.done and out.reply is None and out.slot is None
        assert out.values["category"] == {"value": ["SRT-FT", "CB-FT"], "label": "SRT-FT, CB-FT"}

    def test_all_in_the_message_settles_the_field(self):
        out = _start(ONE, category=rf.ALL)
        assert out.done
        assert out.values["category"]["value"] == rf.ALL

    def test_a_missing_field_is_asked_with_its_own_question(self):
        out = _start(ONE)
        assert not out.done
        assert out.reply == CATEGORY.question
        assert out.slot["ask"] == "test_one" and out.slot["asking"] == "category"

    def test_only_the_missing_field_is_asked(self):
        out = _start(TWO, category="water closet")
        assert out.reply == "Which colour?"
        assert out.slot["values"]["category"]["value"] == ["SRT-WC"]

    def test_an_unknown_word_in_the_message_is_said_and_the_field_asked(self):
        out = _start(ONE, category="spaceship")
        assert out.reply == "I don't know 'spaceship' as a category.\n\n" + CATEGORY.question

    def test_extras_ride_on_the_slot_and_come_back(self):
        out = rf.collect(None, ONE, given={}, extras={"split": "supplier"})
        assert out.slot["extras"] == {"split": "supplier"}
        back = _reply(ONE, out, "water tap")
        assert back.done and back.extras == {"split": "supplier"}


class TestTheReply:
    def test_a_reply_settles_the_field(self):
        out = _reply(ONE, _start(ONE), "Water Tap")
        assert out.done and out.values["category"]["value"] == ["SRT-FT", "CB-FT"]

    def test_all_settles_it(self):
        for word in ("all", "ALL", "any", "semua", "all categories", "全部"):
            out = _reply(ONE, _start(ONE), word)
            assert out.done and out.values["category"]["value"] == rf.ALL, word

    def test_all_is_refused_where_the_field_does_not_take_it(self):
        first = _start(TWO, category="water tap")
        out = _reply(TWO, first, "all")
        assert not out.done
        assert out.reply == "I don't know 'all' as a colour.\n\nWhich colour?"

    def test_an_unknown_reply_is_said_once_and_asked_again(self):
        out = _reply(ONE, _start(ONE), "spaceship")
        assert not out.done
        assert out.reply == "I don't know 'spaceship' as a category.\n\n" + CATEGORY.question
        assert out.slot["misses"] == 1

    def test_the_second_miss_in_a_row_ends_the_ask(self):
        first = _reply(ONE, _start(ONE), "spaceship")
        out = _reply(ONE, first, "rocket")
        assert not out.done and out.slot is None
        assert out.reply == "I still can't place 'rocket'. Ask again."

    def test_a_good_reply_after_a_miss_still_settles(self):
        first = _reply(ONE, _start(ONE), "spaceship")
        out = _reply(ONE, first, "water closet")
        assert out.done and out.values["category"]["value"] == ["SRT-WC"]

    def test_cancel_ends_the_ask(self):
        for word in ("cancel", "stop", "never mind"):
            out = _reply(ONE, _start(ONE), word)
            assert out.cancelled and out.slot is None and out.reply == "Test cancelled.", word

    def test_values_already_settled_are_kept(self):
        first = _start(TWO, category="water closet")
        out = _reply(TWO, first, "blue")
        assert out.done
        assert out.values == {
            "category": {"value": ["SRT-WC"], "label": "SRT-WC"},
            "colour": {"value": "BL", "label": "Blue"},
        }


class TestNumberedPick:
    def test_several_matches_are_listed_with_numbers(self):
        first = _start(TWO, category="water closet")
        out = _reply(TWO, first, "bl")
        assert out.reply == "Which colour do you mean? Reply with a number:\n1. Blue\n2. Black"
        assert out.slot["options"] == [["BL", "Blue"], ["BK", "Black"]]

    def test_a_number_picks(self):
        pick = _reply(TWO, _reply(TWO, _start(TWO, category="water closet"), "bl"), "2")
        assert pick.done and pick.values["colour"] == {"value": "BK", "label": "Black"}

    def test_the_label_picks_too(self):
        pick = _reply(TWO, _reply(TWO, _start(TWO, category="water closet"), "bl"), "black")
        assert pick.done and pick.values["colour"]["value"] == "BK"

    def test_a_number_out_of_range_is_a_miss(self):
        out = _reply(TWO, _reply(TWO, _start(TWO, category="water closet"), "bl"), "7")
        assert not out.done and out.slot["misses"] == 1
        assert out.reply.startswith("I don't know '7' as a colour.")

    def test_all_on_a_pick_settles_all_where_allowed(self):
        def _amb(_db, word, _extras):
            return rf.Resolved("ambiguous", options=((["SRT-FT"], "SRT-FT"), (["CB-FT"], "CB-FT")))

        spec = rf.FieldSpec(name="category", noun="category", question="Which?", resolve=_amb)
        ask = rf.AskType(name="t", fields=(spec,), reroute={}, cancelled="c", give_up="g {word}")
        first = rf.collect(None, ask, given={"category": "tap"})
        assert first.reply == 'Which category do you mean? Reply with a number or "all":\n1. SRT-FT\n2. CB-FT'
        out = rf.collect(None, ask, slot=first.slot, reply="all")
        assert out.done and out.values["category"]["value"] == rf.ALL


OPTIONAL_COLOUR = rf.FieldSpec(
    name="colour", noun="colour", question="Which colour?", resolve=_colour, allow_all=False, required=False,
)
WITH_OPTIONAL = rf.AskType(
    name="test_opt", fields=(CATEGORY, OPTIONAL_COLOUR), reroute={}, cancelled="c", give_up="g {word}",
)


class TestOptionalField:
    def test_an_optional_field_is_never_asked(self):
        out = _start(WITH_OPTIONAL, category="water tap")
        assert out.done and "colour" not in out.values

    def test_an_optional_field_given_is_taken(self):
        out = _start(WITH_OPTIONAL, category="water tap", colour="blue")
        assert out.done and out.values["colour"]["value"] == "BL"

    def test_an_unknown_optional_word_is_not_taken_and_not_asked(self):
        out = _start(WITH_OPTIONAL, category="water tap", colour="spaceship")
        assert out.done and "colour" not in out.values

    def test_an_ambiguous_optional_word_is_a_numbered_pick(self):
        out = _start(WITH_OPTIONAL, category="water tap", colour="bl")
        assert out.reply == 'Which colour do you mean? Reply with a number or "all":\n1. Blue\n2. Black'
        picked = _reply(WITH_OPTIONAL, out, "1")
        assert picked.done and picked.values["colour"]["value"] == "BL"

    def test_all_or_none_on_an_optional_pick_means_no_filter(self):
        for word in ("all", "none", "no"):
            out = _reply(WITH_OPTIONAL, _start(WITH_OPTIONAL, category="water tap", colour="bl"), word)
            assert out.done and out.values["colour"]["value"] == rf.ALL, word

    def test_two_misses_on_an_optional_pick_go_on_without_it(self):
        first = _reply(WITH_OPTIONAL, _start(WITH_OPTIONAL, category="water tap", colour="bl"), "purple")
        assert first.reply.startswith("I don't know 'purple' as a colour.")
        out = rf.collect(None, WITH_OPTIONAL, slot=first.slot, reply="green", given={"colour": "bl"})
        assert out.done and out.values["colour"]["value"] == rf.ALL
        assert out.values["category"]["value"] == ["SRT-FT", "CB-FT"]

    def test_an_optional_word_given_before_the_question_is_still_read_after_it(self):
        first = _start(WITH_OPTIONAL, colour="blue")
        assert first.reply == CATEGORY.question
        out = rf.collect(None, WITH_OPTIONAL, slot=first.slot, reply="water tap", given={"colour": "blue"})
        assert out.done and out.values["colour"]["value"] == "BL"

    def test_a_caller_resolved_value_is_taken_as_is(self):
        out = _start(ONE, category=rf.Resolved("ok", value=["SRT-FT", "SRT-WC"], label="SRT-FT, SRT-WC"))
        assert out.done and out.values["category"]["value"] == ["SRT-FT", "SRT-WC"]

    def test_the_resolver_sees_the_extras(self):
        seen = {}

        def _spy(_db, word, extras):
            seen.update(extras)
            return rf.Resolved("ok", value=word, label=word)

        spec = rf.FieldSpec(name="category", noun="category", question="?", resolve=_spy)
        ask = rf.AskType(name="t", fields=(spec,), reroute={}, cancelled="c", give_up="g")
        rf.collect(None, ask, given={"category": "tap"}, extras={"brand": "Sorento"})
        assert seen == {"brand": "Sorento"}


class TestConfigIsTheRequiredSet:
    def test_adding_a_field_to_the_config_asks_it_with_no_other_change(self):
        assert _start(ONE, category="water tap").done
        assert not _start(TWO, category="water tap").done


class TestReplyVerdict:
    """The engine seam: while a question is open, the next message is read as its answer
    unless it is plainly a different ask."""

    def test_no_open_question_leaves_the_verdict_alone(self):
        verdict = {"intent_hint": "check_stock"}
        assert rf.reply_verdict(verdict, None, "hello", asks={"test_one": ONE}) == (verdict, None)

    def test_the_rerouted_verdict_never_resets_the_topic(self):
        slot = _start(ONE).slot
        verdict = {"intent_hint": None, "message_type": "business_query", "topic_reset": True,
                   "is_affirmative": False}
        out, rule = rf.reply_verdict(verdict, slot, "cancel", asks={"test_one": ONE})
        assert rule == "required_ask_answer"
        assert out["topic_reset"] is False

    def test_a_short_reply_is_rerouted_to_the_ask_with_the_slot(self):
        slot = _start(ONE).slot
        verdict, rule = rf.reply_verdict(
            {"intent_hint": "check_product", "entities": [{"raw": "water tap"}]}, slot, "water tap",
            asks={"test_one": ONE},
        )
        assert rule == "required_ask_answer"
        assert verdict["intent_hint"] == "test_one"
        assert verdict["entities"] == []
        assert verdict["required_ask"] == slot
        assert verdict["required_ask_reply"] == "water tap"

    def test_a_longer_message_the_parser_reads_as_another_ask_drops_the_question(self):
        slot = _start(ONE).slot
        verdict = {"intent_hint": "check_stock", "entities": [{"raw": "CB100"}]}
        out, rule = rf.reply_verdict(verdict, slot, "how many CB100 in BRW", asks={"test_one": ONE})
        assert out == verdict and rule == "required_ask_dropped"

    def test_a_question_mark_is_another_ask_too(self):
        slot = _start(ONE).slot
        verdict = {"intent_hint": "check_stock"}
        out, rule = rf.reply_verdict(verdict, slot, "CB100?", asks={"test_one": ONE})
        assert out == verdict and rule == "required_ask_dropped"

    def test_a_longer_message_the_parser_reads_as_the_same_ask_is_still_the_answer(self):
        slot = _start(ONE).slot
        out, rule = rf.reply_verdict(
            {"intent_hint": "test_one"}, slot, "the water tap one please", asks={"test_one": ONE}
        )
        assert rule == "required_ask_answer"

    def test_a_longer_message_that_is_not_a_business_message_drops_the_question(self):
        slot = _start(ONE).slot
        verdict = {"intent_hint": None, "message_type": "casual"}
        out, rule = rf.reply_verdict(verdict, slot, "ok thanks I will check later", asks={"test_one": ONE})
        assert out == verdict and rule == "required_ask_dropped"

    def test_a_short_casual_reply_is_still_the_answer(self):
        slot = _start(ONE).slot
        _out, rule = rf.reply_verdict({"intent_hint": None, "message_type": "casual"}, slot, "all",
                                      asks={"test_one": ONE})
        assert rule == "required_ask_answer"

    def test_an_empty_message_drops_the_question(self):
        slot = _start(ONE).slot
        _out, rule = rf.reply_verdict({"intent_hint": None}, slot, "", asks={"test_one": ONE})
        assert rule == "required_ask_dropped"

    def test_the_rerouted_verdict_drops_the_parsers_open_question_answer(self):
        slot = _start(ONE).slot
        out, _rule = rf.reply_verdict(
            {"intent_hint": None, "open_question_answer": {"mode": "cancel"}}, slot, "cancel",
            asks={"test_one": ONE},
        )
        assert out["open_question_answer"] is None

    def test_the_rerouted_verdict_carries_no_affirmation_even_when_the_parser_said_false(self):
        """Live parser shape for "cancel": is_affirmative false. The reroute makes the turn
        an answer, not a decline, so the parser's is_affirmative must not survive it."""
        slot = _start(ONE).slot
        out, rule = rf.reply_verdict(
            {"intent_hint": None, "message_type": "business_query", "is_affirmative": False}, slot, "cancel",
            asks={"test_one": ONE},
        )
        assert rule == "required_ask_answer"
        assert out["is_affirmative"] is None

    def test_an_unknown_ask_type_in_the_slot_is_dropped(self):
        out, rule = rf.reply_verdict({"intent_hint": None}, {"ask": "gone", "asking": "x"}, "1", asks={})
        assert rule == "required_ask_dropped"
