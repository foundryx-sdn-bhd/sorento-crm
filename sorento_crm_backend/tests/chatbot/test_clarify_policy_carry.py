"""CLARIFY-POLICY AC-140..AC-148: the carried-subject ask (rows `carry_combine`, `carry_unclear`,
pending kind `carry_scope`). Real engine + real routes + MCP presenter (`Console`), parser fields
stubbed in the verdict as everywhere else.

ASSUMPTIONS flagged: the "today" label (AC-141) is driven by `date_mode="today"` plus the resolved
`date_filter_start/end`; the customer is named by its seeded name "ZZT Dealer Sdn Bhd".
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from sqlalchemy import text

from tests.chatbot._turn_helpers import do_window, entity, verdict
from tests.chatbot.test_do_ask_range_engine import dealer, office  # noqa: F401 - fixtures
from tests.chatbot.test_dealer_eta_stock_routing import CODE
from tests.chatbot.test_engine import stub_access  # noqa: F401 - fixture

CUSTOMER = "ZZT Dealer Sdn Bhd"
ORDERS = ("crm_order_management_orders_list", "crm_order_management_orders_by_product_list")
Q_COMBINE = (
    f"Delivery for {CUSTOMER}: only {CODE} (from earlier) or all products?\n1. Only {CODE}\n2. All products"
)
Q_UNCLEAR = f"Delivery today: for {CODE} (from earlier) or all products?\n1. {CODE}\n2. All products"


def _product_ask(**extra: Any) -> dict[str, Any]:
    return verdict(
        domain_hint="order", intent_hint="check_order", entities=[entity(CODE, hint="product")],
        **{**do_window(), **extra},
    )


def _customer_ask(**extra: Any) -> dict[str, Any]:
    return verdict(
        domain_hint="order", intent_hint="check_order", entities=[entity("ZZT Dealer", hint="customer")],
        **{**do_window(), **extra},
    )


def _today_ask(**extra: Any) -> dict[str, Any]:
    today = date.today().isoformat()
    return verdict(
        domain_hint="order", intent_hint="check_order", entities=[], date_mode="today",
        date_filter_start=today, date_filter_end=today, **extra,
    )


def _orders(console) -> list[dict[str, Any]]:
    return [a for n, a in console.calls if n in ORDERS]


def _ask_unclear(session_factory) -> None:
    """AC-141 / carry_unclear cases: the seed is `carry` (round 3), so the row is set to `ask`."""
    from tests.chatbot.test_clarify_policy_table import _write_all_rows

    _write_all_rows(session_factory(), carry_unclear={"mode": "ask"})


def _carried_product(console) -> None:
    console.say(f"delivery for {CODE}", _product_ask())
    console.calls.clear()


# --- AC-140 / AC-141 / AC-142 / AC-147 ------------------------------------------------------ #


def test_ac140_a_carried_product_and_a_newly_named_customer_asks_before_fetching(dealer):  # noqa: F811
    _carried_product(dealer)
    reply = dealer.say("delivery for zzt dealer", _customer_ask())
    assert reply == Q_COMBINE, reply
    assert _orders(dealer) == [], dealer.calls


def test_ac141_a_carried_product_and_no_new_subject_asks_before_fetching(dealer, session_factory):  # noqa: F811
    _ask_unclear(session_factory)
    _carried_product(dealer)
    reply = dealer.say("delivery today", _today_ask())
    assert reply == Q_UNCLEAR, reply
    assert _orders(dealer) == [], dealer.calls


def test_ac142_a_backward_reference_carries_with_no_question(dealer):  # noqa: F811
    _carried_product(dealer)
    reply = dealer.say("delivery for this", _today_ask(anaphora={"backward_reference": True}))
    assert "(from earlier)" not in reply, reply
    assert _orders(dealer) and str(_orders(dealer)[0].get("product_ids")), dealer.calls
    assert _orders(dealer)[0].get("product_ids"), _orders(dealer)[0]


def test_ac147_a_continuation_carries_with_no_question(dealer):  # noqa: F811
    _carried_product(dealer)
    reply = dealer.say("eta?", _today_ask(continuation=True))
    assert "(from earlier)" not in reply, reply
    assert _orders(dealer)[0].get("product_ids"), dealer.calls


# --- AC-143: the answer ---------------------------------------------------------------------- #


def _answer(mode: str, *picked: int) -> dict[str, Any]:
    return verdict(
        message_type="casual", domain_in_message=False, entities=[],
        open_question_answer={"mode": mode, "picked": list(picked), "items": [], "qty_for_all": None},
    )


def test_ac143_pending_kind_is_carry_scope(dealer):  # noqa: F811
    _carried_product(dealer)
    dealer.say("delivery for zzt dealer", _customer_ask())
    assert ((dealer.state or {}).get("open_question") or {}).get("kind") == "carry_scope", dealer.state


def test_ac143_position_1_keeps_the_carried_product(dealer):  # noqa: F811
    _carried_product(dealer)
    dealer.say("delivery for zzt dealer", _customer_ask())
    dealer.say("1", _answer("pick", 1))
    orders = _orders(dealer)
    assert orders and orders[-1].get("product_ids") and orders[-1].get("customer_ids"), dealer.calls


def test_ac143_position_2_drops_the_carried_product(dealer):  # noqa: F811
    _carried_product(dealer)
    dealer.say("delivery for zzt dealer", _customer_ask())
    dealer.say("2", _answer("pick", 2))
    orders = _orders(dealer)
    assert orders and orders[-1].get("customer_ids") and not orders[-1].get("product_ids"), dealer.calls


def test_ac143_a_parser_all_drops_the_carried_product(dealer):  # noqa: F811
    _carried_product(dealer)
    dealer.say("delivery for zzt dealer", _customer_ask())
    dealer.say("all of them", _answer("all"))
    orders = _orders(dealer)
    assert orders and not orders[-1].get("product_ids"), dealer.calls


# --- AC-144: exclusions ---------------------------------------------------------------------- #


def test_ac144_restating_the_carried_product_asks_nothing(dealer):  # noqa: F811
    _carried_product(dealer)
    reply = dealer.say(f"delivery for {CODE} again", _product_ask())
    assert "(from earlier)" not in reply, reply
    assert _orders(dealer), dealer.calls


def test_ac144_no_carried_subject_asks_nothing(dealer):  # noqa: F811
    reply = dealer.say("delivery for zzt dealer", _customer_ask())
    assert "(from earlier)" not in reply, reply
    assert _orders(dealer), dealer.calls


# --- AC-145: the modes ------------------------------------------------------------------------ #


@pytest.mark.parametrize("kind", ["carry_combine", "carry_unclear"])
def test_ac145_mode_carry_keeps_the_subject_with_no_ask(dealer, session_factory, kind):  # noqa: F811
    from tests.chatbot.test_clarify_policy_table import _write_all_rows

    _write_all_rows(session_factory(), **{kind: {"mode": "carry"}})
    _carried_product(dealer)
    if kind == "carry_combine":
        reply = dealer.say("delivery for zzt dealer", _customer_ask())
    else:
        reply = dealer.say("delivery today", _today_ask())
    assert "(from earlier)" not in reply, reply
    assert _orders(dealer)[0].get("product_ids"), dealer.calls


@pytest.mark.parametrize("kind", ["carry_combine", "carry_unclear"])
def test_ac145_mode_drop_drops_the_subject_with_no_ask(dealer, session_factory, kind):  # noqa: F811
    from tests.chatbot.test_clarify_policy_table import _write_all_rows

    _write_all_rows(session_factory(), **{kind: {"mode": "drop"}})
    _carried_product(dealer)
    if kind == "carry_combine":
        reply = dealer.say("delivery for zzt dealer", _customer_ask())
    else:
        reply = dealer.say("delivery today", _today_ask())
    assert "(from earlier)" not in reply, reply
    assert _orders(dealer) and not _orders(dealer)[0].get("product_ids"), dealer.calls


# --- AC-146: cand-0230 shape ------------------------------------------------------------------ #


def test_ac146_customer_then_product_then_a_bare_month_never_lists_rows_filtered_by_both(office, session_factory):  # noqa: F811
    """An office contact: a dealer's own linked customer never triggers a carry ask (guard below)."""
    dealer = office
    _ask_unclear(session_factory)
    dealer.say("delivery for zzt dealer", _customer_ask())
    dealer.calls.clear()
    second = dealer.say(f"delivery for {CODE}", _product_ask())
    assert "(from earlier)" in second, second
    third = dealer.say("september", _today_ask())
    assert "(from earlier)" in third, third
    both = [a for a in _orders(dealer) if a.get("product_ids") and a.get("customer_ids")]
    assert both == [], dealer.calls


def test_ac146_the_carry_asks_count_for_the_loop_breaker(dealer, session_factory):  # noqa: F811
    _ask_unclear(session_factory)
    _carried_product(dealer)
    first = dealer.say("delivery today", _today_ask())
    second = dealer.say("delivery today", _today_ask())
    third = dealer.say("delivery today", _today_ask())
    assert first == second == Q_UNCLEAR, (first, second)
    assert third != Q_UNCLEAR and "(from earlier)" not in third, third


# --- AC-148: no expiry ------------------------------------------------------------------------ #


def test_ac148_a_carried_subject_survives_a_previous_turn_days_old(dealer, session_factory):  # noqa: F811
    _carried_product(dealer)
    db = session_factory()
    db.execute(text("UPDATE chatbot.turns SET created_at = created_at - interval '5 days'"))
    db.commit()
    reply = dealer.say("delivery for zzt dealer", _customer_ask())
    assert reply == Q_COMBINE, reply


def test_ac148_a_carried_subject_survives_several_turns_in_between(dealer):  # noqa: F811
    _carried_product(dealer)
    for n in range(3):
        dealer.say(f"delivery for {CODE} {n}", _product_ask())
    dealer.calls.clear()
    assert dealer.say("delivery for zzt dealer", _customer_ask()) == Q_COMBINE


# --- round 3 --------------------------------------------------------------------------------- #


def test_the_seed_of_carry_unclear_is_carry_so_delivery_today_keeps_the_product_and_asks_nothing(dealer):  # noqa: F811
    _carried_product(dealer)
    reply = dealer.say("delivery today", _today_ask())
    assert "(from earlier)" not in reply, reply
    assert _orders(dealer) and _orders(dealer)[0].get("product_ids"), dealer.calls


def test_a_dealers_own_linked_customer_on_the_focus_never_triggers_a_carry_ask(dealer):  # noqa: F811
    dealer.say("delivery for zzt dealer", _customer_ask())
    dealer.calls.clear()
    reply = dealer.say(f"delivery for {CODE}", _product_ask())
    assert "(from earlier)" not in reply, reply
    assert _orders(dealer), dealer.calls


def test_the_same_guard_on_a_session_that_predates_any_focus_mark(dealer):  # noqa: F811
    """The focus customer row is the plain pre-existing shape (no extra keys), equal to the
    contact's linked customer."""
    dealer.say("delivery for zzt dealer", _customer_ask())
    plain = ("uuid", "raw", "hint", "canonical_code", "current_message", "confident", "display_name")
    focus = (dealer.state or {}).get("focus") or {}
    focus["customers"] = [{k: v for k, v in row.items() if k in plain} for row in focus.get("customers") or []]
    assert focus["customers"], dealer.state
    dealer.calls.clear()
    reply = dealer.say(f"delivery for {CODE}", _product_ask())
    assert "(from earlier)" not in reply, reply
    assert _orders(dealer), dealer.calls
