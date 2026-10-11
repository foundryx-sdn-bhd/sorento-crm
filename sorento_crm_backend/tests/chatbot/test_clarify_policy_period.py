"""CLARIFY-POLICY S4 (AC-17..AC-23): the DO / SO period ask becomes default-and-disclose for a
NAMED subject. UAC: documentation/plans/chatbot/clarify-policy-acceptance-criteria.md.

The DO half is also pinned by the updated tests in test_do_ask_range*.py,
test_do_period_skip_do_list.py, test_rearch_r4/r5; this file adds the SO list, the empty default
(AC-23), the 31-day refusal (AC-20) and scope-before-default (AC-22). Fake names only.
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

DISCLOSE = "Another month? Reply with the month."
ZZT = "ZZT PERIOD TRADING SDN BHD [A/C I]"
ZZT_GROUP = "ZZT PERIOD TRADING SDN BHD"


# --- DO list, through the engine ------------------------------------------------------------ #


def test_ac23_a_named_subject_default_with_no_rows_says_none_found_plus_disclose(dealer):  # noqa: F811
    reply = dealer.say("delivery to zzt dealer", _ask())
    assert "No delivery orders found for" in reply and "ZZT Dealer" in reply, reply
    assert reply.endswith(DISCLOSE), reply
    assert "Which period" not in reply, reply


def test_ac20_a_named_subject_over_31_days_still_gets_the_over_31_days_ask(dealer):  # noqa: F811
    reply = dealer.say(
        "delivery to zzt dealer january to june",
        _ask(date_filter_start="2026-01-01", date_filter_end="2026-06-30"),
    )
    assert "I can show up to 31 days of delivery orders at a time" in reply, reply
    assert DISCLOSE not in reply, reply


def test_ac22_the_default_fetch_is_scoped_to_the_contacts_links(dealer):  # noqa: F811
    dealer.say("delivery to zzt dealer", _ask())
    orders = [a for n, a in dealer.calls if n == "crm_order_management_orders_list"]
    assert orders, dealer.calls
    assert orders[0].get("customer_ids"), f"the default must stay scoped to a customer: {orders[0]}"
    assert not orders[0].get("actual_delivery_date_from"), orders[0]


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


def test_ac18_a_named_customer_so_list_with_no_dates_is_the_newest_5_plus_disclose(session_factory, monkeypatch):
    cid = _seed_eight(session_factory)
    reply, captured = _so_turn(session_factory, monkeypatch, _so_ask(True), cid)
    assert "Which period" not in reply, reply
    assert _numbers(reply) == [f"SO7000{d:02d}" for d in range(8, 3, -1)], reply
    assert reply.rstrip().endswith(DISCLOSE), reply


def test_ac19_a_bare_so_list_with_no_subject_still_asks_which_period(session_factory, monkeypatch):
    cid = _seed_eight(session_factory)
    reply, captured = _so_turn(session_factory, monkeypatch, _so_ask(False), cid, body="all my sales orders")
    assert reply.startswith(f"Which period for {ZZT_GROUP}?\n- This month ("), reply
    assert captured == [], captured


def test_ac22_the_so_default_never_shows_another_customers_orders(session_factory, monkeypatch):
    cid = _seed_eight(session_factory)
    foreign = _other_customer(session_factory, "ZZT FOREIGN SDN BHD")
    _seed_so(session_factory, "SO799999", customer_id=foreign, lines=[(1, 0)], debtor_name=None, order_date="2026-09-20")
    reply, _ = _so_turn(session_factory, monkeypatch, _so_ask(True), cid)
    assert "SO799999" not in reply and "FOREIGN" not in reply, reply


def test_ac21_switch_on_keeps_newest_20_with_no_disclose_line(session_factory, monkeypatch):
    cid = _seed_eight(session_factory)
    reply, _ = _so_turn(session_factory, SkipSwitchMonkeypatch(monkeypatch, True), _so_ask(True), cid)
    assert len(_numbers(reply)) == 8 and "Another month?" not in reply, reply
