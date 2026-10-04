"""Phase 2 RED tests - an account number with no customer named THIS turn asks which customer.

`documentation/plans/chatbot/PLAN-account-ledger-2oct.md` Design 7 (owner Q2: no carry-over) and
AC-10. A customer entity with `account` and a null `raw`, no `self_reference`, answers exactly
"Which customer is Account N for?", fetches nothing, and nothing is carried onto the next turn.
A customer named in an EARLIER turn is never used to fill the gap.
"""
from __future__ import annotations

from typing import Any

from tests.chatbot.test_customer_scope_lane import (
    _ask,
    _calls,
    _ent,
    _link_customers,
    _turn,
    REPORT,
)
from tests.chatbot.test_outstanding_lane import (
    HANLIM_UUID_1,
    REPORT_HIT,
    _seed_contact,
)


def _unnamed(account: int) -> dict[str, Any]:
    entity = _ent("x")
    entity["raw"] = None
    entity["account"] = account
    return entity


def _question(account: int) -> str:
    return f"Which customer is Account {account} for?"


def test_unnamed_account_asks_which_customer_and_fetches_nothing(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    reply, captured = _turn(
        session_factory, monkeypatch, _ask([_unnamed(2)]), "account 2 outstanding", mcp_response=REPORT_HIT,
    )
    assert reply.strip() == _question(2), reply
    assert captured == []


def test_the_number_in_the_question_is_the_one_asked(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    reply, captured = _turn(
        session_factory, monkeypatch, _ask([_unnamed(3)]), "a/c iii sales", mcp_response=REPORT_HIT,
    )
    assert reply.strip() == _question(3), reply
    assert captured == []


def test_a_customer_named_in_an_earlier_turn_is_not_used(session_factory, monkeypatch) -> None:
    carried = {
        "raw": "hanlim", "hint": "customer", "uuid": HANLIM_UUID_1, "canonical_code": "300-H070",
        "current_message": False,
    }
    _seed_contact(
        session_factory,
        variables={"message_type": "business_query", "domain_hint": "order", "entities": [carried]},
    )
    reply, captured = _turn(
        session_factory, monkeypatch, _ask([_unnamed(2)]), "account 2 outstanding", mcp_response=REPORT_HIT,
    )
    assert reply.strip() == _question(2), reply
    assert captured == []
    assert HANLIM_UUID_1 not in str(captured)


def test_a_linked_contact_without_my_is_asked_too(session_factory, monkeypatch) -> None:
    """Without "my" the links are not named: ask, do not run the report on all of them."""
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, "ZZT ALPHA CO [A/C I]", "ZZT BRAVO CO [A/C II]")
    reply, captured = _turn(
        session_factory, monkeypatch, _ask([_unnamed(2)]), "account 2 outstanding", mcp_response=REPORT_HIT,
    )
    assert reply.strip() == _question(2), reply
    assert captured == []


def test_nothing_is_carried_onto_the_next_turn(session_factory, monkeypatch) -> None:
    """After the question, a bare follow-up must not run on any customer."""
    _seed_contact(session_factory, variables={})
    _turn(session_factory, monkeypatch, _ask([_unnamed(2)]), "account 2 outstanding", mcp_response=REPORT_HIT)
    _reply, captured = _turn(session_factory, monkeypatch, _ask(), "outstanding", mcp_response=REPORT_HIT)
    for args in _calls(captured, REPORT):
        assert not args.get("customer_ids"), args
        assert not args.get("customer_query"), args


def test_my_account_names_the_links_so_it_is_not_asked(session_factory, monkeypatch) -> None:
    """Q6 (a) guard: `self_reference` plus an account is NOT the unnamed ask."""
    _seed_contact(session_factory, variables={})
    ids = _link_customers(session_factory, "ZZT ALPHA CO [A/C I]")
    from sqlalchemy import text

    db = session_factory()
    db.execute(text("UPDATE customers SET account_level = 1 WHERE id = :i"), {"i": ids[0]})
    db.commit()
    reply, captured = _turn(
        session_factory, monkeypatch, _ask([_unnamed(1)], self_reference=True), "my account 1 outstanding",
        mcp_response=REPORT_HIT,
    )
    assert "Which customer is Account" not in reply
    (args,) = _calls(captured, REPORT)
    assert args["customer_ids"] == [ids[0]]
