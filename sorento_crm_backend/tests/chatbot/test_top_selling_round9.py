"""Top selling fix lane round 9: the owner's hand test after round 8 (28 Sep 2026 12:13
to 12:18 MYT, console :3083, contact 900000039, PR #1273).

The owner's PR comment "Owner hand test after round 8: 'salesman' as the answer to the
who question falls to low signal" is the work list:

    "ok i think #1273 is okay already, just that why when i say salesman instead of
    sales agent it goes to low signal, i thought it is supposed to be the saem?"

After "Do you mean customer SAMPLE - WILLIAM or sales agent WT I, WT III, WT IV? Reply 1
for the customer, 2 for the sales agent." the owner typed "saleman" (12:13:51) and
"salesman" (12:17:38), and both got the low signal greeting. A word answer naming the
sales agent side (salesman, sales man, saleman, sales person, salesperson, agent, sales
agent, rep, the person label or code itself) or the customer side (customer, client, the
customer name) is that side of the question, exactly like 1 or 2, and so is a typo one
edit away from those words.

Every message goes through `console_service.run_console_turn` the way the console runs
it (round 7's `Console`), with the real resolver on seeded rows. Postgres only.
"""
from __future__ import annotations

import pytest

from app.services.company_scope import DEFAULT_COMPANY_ID
from tests.chatbot.test_top_selling_round5 import ASK_METRIC, _answer, _ask, _calls, _e, _low_signal, _position
from tests.chatbot.test_top_selling_round7 import console_factory  # noqa: F401  (the fixture)
from tests.chatbot.test_top_selling_round8 import RANKED, WILLIAM_WHO, _seed_william_ledger

#: The low signal greeting's opening, never a reply to the who question.
GREETING = "How can I help"

AGENT_WORDS = [
    "salesman", "saleman", "sales man", "sale man", "salesmen", "sales person", "salesperson",
    "agent", "sales agent", "rep", "sales rep", "the salesman", "salesman la", "slaesman", "agnet",
    "salesmna", "WT", "wt i", "WT III",
]
CUSTOMER_WORDS = [
    "customer", "client", "the customer", "custmer", "cutsomer", "clinet", "SAMPLE - WILLIAM",
]


def _william_customer_id(session_factory) -> str:
    from sqlalchemy import text

    db = session_factory()
    try:
        # Raw SQL: the ORM query is company scoped and this session carries no scope.
        (row,) = db.execute(text("SELECT id FROM customers WHERE customer_name = 'SAMPLE - WILLIAM'")).all()
        return str(row[0])
    finally:
        db.close()


def _asked_who(c) -> None:
    text, captured = c.say(_ask(top_n=10, entities=[_e("william", "customer")]), "top 10 hot selling item by william")
    assert text == WILLIAM_WHO, text
    assert captured == [], captured


def _console(console_factory, session_factory):
    _seed_william_ledger(session_factory)
    c = console_factory(wt_alias="William")
    c.codes = list(RANKED)
    return c


class TestWordAnswers:
    @pytest.mark.parametrize("word", AGENT_WORDS)
    def test_an_agent_word_answers_like_two(self, console_factory, session_factory, word) -> None:
        c = _console(console_factory, session_factory)
        _asked_who(c)
        text, captured = c.say(_low_signal(), word)
        assert text == ASK_METRIC, (word, text)
        text, captured = c.say(_answer(rank_by="amount"), "amount")
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == c.seeded.agents("WT"), (word, args)
        assert "customer_ids" not in args, (word, args)

    @pytest.mark.parametrize("word", CUSTOMER_WORDS)
    def test_a_customer_word_answers_like_one(self, console_factory, session_factory, word) -> None:
        c = _console(console_factory, session_factory)
        _asked_who(c)
        text, captured = c.say(_low_signal(), word)
        assert text == ASK_METRIC, (word, text)
        text, captured = c.say(_answer(rank_by="amount"), "amount")
        (args,) = _calls(captured)
        assert args["customer_ids"] == [_william_customer_id(session_factory)], (word, args)
        assert "sales_agent_ids" not in args, (word, args)

    @pytest.mark.parametrize("word", ["salesman", "customer"])
    def test_a_word_reading_as_a_position_still_binds_by_its_word(self, console_factory, session_factory, word) -> None:
        """The parser may read the word as some other thing; the word still answers."""
        c = _console(console_factory, session_factory)
        _asked_who(c)
        text, _ = c.say(_position(1, message_type="business_query", order_status="top_selling", domain_hint="order"), word)
        assert text == ASK_METRIC, (word, text)
        _text, captured = c.say(_answer(rank_by="amount"), "amount")
        (args,) = _calls(captured)
        if word == "salesman":
            assert "sales_agent_ids" in args and "customer_ids" not in args, args
        else:
            assert "customer_ids" in args and "sales_agent_ids" not in args, args

    def test_a_new_ranking_ask_is_not_read_as_an_answer(self, console_factory, session_factory) -> None:
        """"sale" is one edit from "sales", but a whole new ask while the question is open
        asks the question again rather than taking the agent."""
        c = _console(console_factory, session_factory)
        _asked_who(c)
        text, captured = c.say(
            _ask(top_n=10, entities=[_e("william", "customer")]), "top 10 hot sale item by william"
        )
        assert text == WILLIAM_WHO, text
        assert captured == [], captured


# --------------------------------------------------------------------------- #
# The owner's messages, in order
# --------------------------------------------------------------------------- #


class TestOwnerTranscript:
    def test_replay(self, console_factory, session_factory) -> None:
        c = _console(console_factory, session_factory)

        # 12:13:42
        _asked_who(c)
        # 12:13:51: "saleman" is the agent, as "2" is.
        text, captured = c.say(_low_signal(), "saleman")
        assert GREETING not in text, text
        assert text == ASK_METRIC, text
        # 12:14:13 (the owner re-asked and typed "2" first because of the greeting)
        text, captured = c.say(_answer(rank_by="amount"), "amount")
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == c.seeded.agents("WT"), args
        assert "customer_ids" not in args, args
        assert text.startswith("*Top 10 selling items*"), text

        # 12:17:33
        _asked_who(c)
        # 12:17:38: "salesman" is the agent too.
        text, captured = c.say(_low_signal(), "salesman")
        assert GREETING not in text, text
        assert text == ASK_METRIC, text
        # 12:18:00
        text, captured = c.say(_answer(rank_by="amount"), "by amount")
        (args,) = _calls(captured)
        assert sorted(args["sales_agent_ids"]) == c.seeded.agents("WT"), args
        assert "customer_ids" not in args, args
        assert text.startswith("*Top 10 selling items*"), text
