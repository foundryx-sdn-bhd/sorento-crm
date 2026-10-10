"""CA-3 security finding at 8c02a225d, RED: the chat report's asker-rule scope (dealer only) must
not leak onto the other asks of a multi-ask turn.

A contact with customer links that classifies SALESPERSON (active user, role slug `salesperson`,
no office access type) is customer-scoped by the generic rule for an orders ask (main behaviour):
a customer word outside its links is refused ("Sorry, that isn't under your account..."). A turn
that ALSO carries a chat report must not change that, and a contact without the
`chatbot.analytics` reveal must not trigger the chat scoping at all. Parser and MCP are fakes.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text

from tests.chatbot.test_chat_report_lane import _chat, _e
from tests.chatbot.test_customer_scope_lane import (
    OUTSTANDING_KEY,
    REPORT,
    _calls,
    _ent,
    _link_customers,
    _other_customer,
    _turn,
)
from tests.chatbot.test_engine import CONTACT_ID, _parser_output
from tests.chatbot.test_outstanding_lane import _seed_contact

OWN = "ZZT SCOPE OWN SDN BHD"
FOREIGN = "ZZT SCOPE FOREIGN SDN BHD"
REFUSAL = "isn't under your account"
GRANT = "chatbot.analytics"
FULL = {"group_by": "topic", "measure": "asks", "subject": None, "top_n": 5,
        "date_from": "2026-01-01", "date_to": "2026-12-31",
        "askers": [], "category_words": [], "brand_words": []}


def _salesperson_user(session_factory) -> None:
    from app.models.user import User, UserRole, UserRoleAssignment

    db = session_factory()
    try:
        contact_id = db.execute(
            text("SELECT id FROM respond_contacts WHERE respond_io_id = :c"), {"c": str(CONTACT_ID)}
        ).scalar_one()
        role = db.query(UserRole).filter_by(slug="salesperson").first()
        if role is None:
            role = UserRole(id=str(uuid.uuid4()), slug="salesperson", name=f"ZZT Salesperson {uuid.uuid4().hex[:6]}")
            db.add(role)
            db.flush()
        user_id = str(uuid.uuid4())
        db.add(User(id=user_id, email=f"zzt-{user_id[:8]}@zzt.test", name="ZZT Salesperson",
                    status="ACTIVE", respond_contact_id=contact_id))
        db.flush()
        db.add(UserRoleAssignment(id=str(uuid.uuid4()), user_id=user_id, role_id=role.id))
        db.commit()
    finally:
        db.close()


def _fake_extractor(monkeypatch) -> None:
    from app.services.chatbot.analytics import chat_report_extractor

    monkeypatch.setattr(chat_report_extractor, "extract_chat_report", lambda db, text, *a, **k: dict(FULL))


def _two_asks(foreign_id: str) -> dict[str, Any]:
    return _parser_output(
        domain_hint=None, intent_hint=None, order_status=None, entities=[_ent(FOREIGN)],
        asks=[
            {"domain": "chat_analytics", "intent": "chat_report"},
            {"domain": "order", "intent": "check_order"},
        ],
    )


def _setup(session_factory):
    _seed_contact(session_factory, variables={})
    _link_customers(session_factory, OWN)
    foreign_id = _other_customer(session_factory, FOREIGN)
    _salesperson_user(session_factory)
    return foreign_id


def _matches(foreign_id: str) -> dict[str, dict[str, Any]]:
    return {FOREIGN: {"uuid": foreign_id, "entity_type": "customer", "canonical_code": "ZZT-F"}}


def test_a_chat_plus_orders_turn_keeps_the_orders_fetch_customer_scoped(session_factory, monkeypatch) -> None:
    foreign_id = _setup(session_factory)
    _fake_extractor(monkeypatch)
    reply, captured = _turn(
        session_factory, monkeypatch, _two_asks(foreign_id),
        f"top 5 questions this year and outstanding for {FOREIGN}",
        attributes=(OUTSTANDING_KEY, GRANT), matches=_matches(foreign_id), mcp_response={"has_result": False},
    )
    assert REFUSAL in reply, reply
    assert foreign_id not in str(_calls(captured, REPORT)), captured


def test_without_the_chat_reveal_a_chat_plus_orders_turn_is_still_scoped(session_factory, monkeypatch) -> None:
    foreign_id = _setup(session_factory)
    _fake_extractor(monkeypatch)
    reply, captured = _turn(
        session_factory, monkeypatch, _two_asks(foreign_id),
        f"top 5 questions this year and outstanding for {FOREIGN}",
        attributes=(OUTSTANDING_KEY,), matches=_matches(foreign_id), mcp_response={"has_result": False},
    )
    assert REFUSAL in reply, reply
    assert foreign_id not in str(_calls(captured, REPORT)), captured


def test_a_chat_only_turn_still_runs_unscoped_for_that_contact(session_factory, monkeypatch) -> None:
    """Guard: the asker rule (not the links) scopes a chat report, so the salesperson's report
    about a customer outside its links runs and is not refused by the generic gate."""
    foreign_id = _setup(session_factory)
    _fake_extractor(monkeypatch)
    verdict = _chat(_e(FOREIGN, "customer"))
    reply, captured = _turn(
        session_factory, monkeypatch, verdict, f"top 5 questions from {FOREIGN} this week",
        attributes=(GRANT,), matches=_matches(foreign_id), mcp_response={"has_result": False},
    )
    assert REFUSAL not in reply, reply
    assert any(name == "crm_report_ask" for name, _args in captured), captured


def test_baseline_an_orders_only_turn_is_refused_for_a_customer_outside_the_links(session_factory, monkeypatch) -> None:
    """Pins the main behaviour the two-ask tests compare against (green today)."""
    foreign_id = _setup(session_factory)
    verdict = _parser_output(domain_hint="order", intent_hint="check_order", order_status="outstanding_both",
                             entities=[_ent(FOREIGN)])
    reply, captured = _turn(
        session_factory, monkeypatch, verdict, f"outstanding for {FOREIGN}",
        attributes=(OUTSTANDING_KEY,), matches=_matches(foreign_id), mcp_response={"has_result": False},
    )
    assert REFUSAL in reply and _calls(captured, REPORT) == [], (reply, captured)


# --------------------------------------------------------------------------- the pure seam

import copy  # noqa: E402

import pytest  # noqa: E402

SCOPE = {"ids": ["c1"], "names": {"c1": "ZZT OWN"}, "linked": [["c1", "ZZT OWN", "ZZT-1"]],
         "staff": False, "enforced": True, "refusal": "Sorry, that isn't under your account."}


@pytest.mark.parametrize("asker", ["salesperson", "staff", "unclassified", "dealer"])
@pytest.mark.parametrize("granted", [True, False])
def test_scope_for_chat_analytics_is_relaxed_only_for_a_granted_non_dealer(asker, granted) -> None:
    """The engine must apply `scope_for` per fetch at fetch time, against the final plan
    (security should-fix at e6e93c5d4: re-plan after the relaxation)."""
    from app.services.chatbot.lanes.business import chat_report

    got = chat_report.scope_for("chat_analytics", SCOPE, asker=asker, granted=granted)
    relaxed = granted and asker != "dealer"
    assert got["enforced"] is (not relaxed), (asker, granted, got)
    assert {k: v for k, v in got.items() if k != "enforced"} == {k: v for k, v in SCOPE.items() if k != "enforced"}


@pytest.mark.parametrize("domain", ["order", "sales", "inventory", "incoming", "purchase_order"])
@pytest.mark.parametrize("asker", ["salesperson", "staff", "unclassified", "dealer"])
def test_scope_for_any_other_fetch_domain_is_unchanged_whatever_the_asker(domain, asker) -> None:
    """The engine must apply `scope_for` per fetch at fetch time, against the final plan
    (security should-fix at e6e93c5d4: re-plan after the relaxation)."""
    from app.services.chatbot.lanes.business import chat_report

    assert chat_report.scope_for(domain, SCOPE, asker=asker, granted=True) == SCOPE


def test_scope_for_never_mutates_its_input() -> None:
    from app.services.chatbot.lanes.business import chat_report

    before = copy.deepcopy(SCOPE)
    chat_report.scope_for("chat_analytics", SCOPE, asker="salesperson", granted=True)
    chat_report.scope_for("order", SCOPE, asker="salesperson", granted=True)
    assert SCOPE == before


@pytest.mark.parametrize("asker", [None, "", "unknown", "Dealer ", "admin"])
def test_scope_for_fails_closed_for_an_unresolved_or_unknown_asker(asker) -> None:
    """Security nit at ef8efcc3b: relaxed only for asker in {staff, salesperson, unclassified}."""
    from app.services.chatbot.lanes.business import chat_report

    assert chat_report.scope_for("chat_analytics", SCOPE, asker=asker, granted=True) == SCOPE
