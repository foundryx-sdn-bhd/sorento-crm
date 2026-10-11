"""CLARIFY-POLICY AC-101 / AC-104 (owner Q1): the DO / SO period ask is UNCHANGED, named subject or
not. Regression guards that pass on today's code. Fake names only.
"""
from __future__ import annotations

import uuid
from typing import Any

from tests.chatbot._turn_helpers import entity, verdict
from tests.chatbot._dps_helpers import SkipSwitchMonkeypatch
from tests.chatbot.test_customer_scope_lane import _link_customers, _other_customer
from tests.chatbot.test_do_ask_range_engine import _ask, dealer, office  # noqa: F401 - fixtures
from tests.chatbot.test_engine import _parser_output, stub_access  # noqa: F401 - fixture
from tests.chatbot.test_outstanding_lane import _run_turn, _seed_contact
from tests.chatbot.test_so_number_ask import OUTSTANDING_KEY, _seed_so

DISCLOSE = "Another month? Reply with the month."  # the v1 default line: must NOT appear
ZZT = "ZZT PERIOD TRADING SDN BHD [A/C I]"
ZZT_GROUP = "ZZT PERIOD TRADING SDN BHD"


# --- DO list, through the engine ------------------------------------------------------------ #


def test_ac101_a_named_customer_with_no_dates_asks_which_period_and_fetches_nothing(dealer):  # noqa: F811
    reply = dealer.say("delivery to zzt dealer", _ask())
    assert "Which period" in reply and DISCLOSE not in reply, reply
    assert [n for n, _ in dealer.calls if n == "crm_order_management_orders_list"] == [], dealer.calls


def test_ac101_an_office_contact_naming_a_customer_is_asked_the_period_too(office):  # noqa: F811
    reply = office.say("delivery to zzt dealer", _ask())
    assert "Which period" in reply, reply


def test_ac101_a_range_over_31_days_still_gets_the_over_31_days_ask(dealer):  # noqa: F811
    reply = dealer.say(
        "delivery to zzt dealer january to june",
        _ask(date_filter_start="2026-01-01", date_filter_end="2026-06-30"),
    )
    assert "I can show up to 31 days of delivery orders at a time" in reply, reply


# --- SO list ------------------------------------------------------------------------------- #


def _so_ask(named: bool, **over: Any) -> dict[str, Any]:
    ents = [{"raw": "zzt period", "hint": "customer", "canonical_code": None, "current_message": True, "confident": True}]
    base: dict[str, Any] = dict(
        domain_hint="order", intent_hint="check_order", order_status=None,
        entities=ents if named else [], document=["SO"], self_reference=not named,
        date_filter_start=None, date_filter_end=None,
    )
    base.update(over)
    return _parser_output(**base)


def _so_turn(session_factory, monkeypatch, qf, own_id, body="zzt period sales orders"):
    result, captured = _run_turn(
        session_factory, monkeypatch, qf=qf, text_body=body, msg_id=f"ZZT-clp-so-{uuid.uuid4().hex[:10]}",
        attributes=[OUTSTANDING_KEY],
        matches={"zzt period": {"uuid": own_id, "entity_type": "customer", "canonical_code": "ZZTP-1"}},
        mcp_response={"has_result": True, "response": "ZZT ROWS"},
    )
    return ((result.reply or {}).get("text") or ""), captured


def _seed_eight(session_factory) -> str:
    _seed_contact(session_factory, variables={})
    (cid,) = _link_customers(session_factory, ZZT)
    for day in range(1, 9):
        _seed_so(session_factory, f"SO7000{day:02d}", customer_id=cid, status="open", lines=[(1, 0)],
                 debtor_name=None, order_date=f"2026-08-{day:02d}")
    return cid


def _numbers(reply: str) -> list[str]:
    return [ln.split(" - ")[0] for ln in reply.strip().split("\n") if ln.startswith("SO")]


def test_ac101_a_named_customer_so_list_with_no_dates_still_asks_which_period(session_factory, monkeypatch):
    cid = _seed_eight(session_factory)
    reply, captured = _so_turn(session_factory, monkeypatch, _so_ask(True), cid)
    assert reply.startswith(f"Which period for {ZZT_GROUP}?\n- This month ("), reply
    assert captured == [], captured


def test_ac101_a_bare_so_list_with_no_subject_still_asks_which_period(session_factory, monkeypatch):
    cid = _seed_eight(session_factory)
    reply, captured = _so_turn(session_factory, monkeypatch, _so_ask(False), cid, body="all my sales orders")
    assert reply.startswith(f"Which period for {ZZT_GROUP}?\n- This month ("), reply
    assert captured == [], captured


def test_ac104_switch_on_keeps_newest_20_and_no_ask(session_factory, monkeypatch):
    cid = _seed_eight(session_factory)
    reply, _ = _so_turn(session_factory, SkipSwitchMonkeypatch(monkeypatch, True), _so_ask(True), cid)
    assert len(_numbers(reply)) == 8 and "Which period" not in reply and "Another month?" not in reply, reply
