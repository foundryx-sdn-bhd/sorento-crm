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
        **{**do_window(), "requested_attributes": ["delivery"], **extra},
    )


def _customer_ask(**extra: Any) -> dict[str, Any]:
    return verdict(
        domain_hint="order", intent_hint="check_order", entities=[entity("ZZT Dealer", hint="customer")],
        **{**do_window(), "requested_attributes": ["delivery"], **extra},
    )


def _today_ask(**extra: Any) -> dict[str, Any]:
    today = date.today().isoformat()
    return verdict(
        domain_hint="order", intent_hint="check_order", entities=[], date_mode="today",
        date_filter_start=today, date_filter_end=today, **{"requested_attributes": ["delivery"], **extra},
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


def _bare_month(month: str = "october") -> dict[str, Any]:
    """How the live parser reads a bare month word: no domain, not a domain-in-message, a month mode."""
    return verdict(
        message_type="casual", domain_hint=None, domain_in_message=False, entities=[], date_mode="month",
        date_filter_start="2026-10-01", date_filter_end="2026-10-31",
    )


def test_ac146_customer_then_product_then_a_bare_month_asks_the_carry_question_again(office, session_factory):  # noqa: F811
    """An office contact: a dealer's own linked customer never triggers a carry ask (guard below).
    The open carry_scope question is not answered by a bare month, so it is asked AGAIN (same
    copy), never answered with a fetch filtered by customer AND product."""
    dealer = office
    dealer.say("delivery for zzt dealer", _customer_ask())
    dealer.calls.clear()
    second = dealer.say(f"delivery for {CODE}", _product_ask())
    assert "(from earlier)" in second and ((dealer.state or {}).get("open_question") or {}).get("kind") == "carry_scope", second
    third = dealer.say("october", _bare_month())
    assert third == second, (second, third)
    both = [a for a in _orders(dealer) if a.get("product_ids") and a.get("customer_ids")]
    assert both == [], dealer.calls


def _domain_month() -> dict[str, Any]:
    """How the parser reads "delivery in october": the domain named in the message, a month window,
    no entity of its own."""
    return verdict(
        domain_hint="order", intent_hint="check_order", domain_in_message=True, entities=[],
        date_mode="month", date_filter_start="2026-10-01", date_filter_end="2026-10-31",
    )


def test_ac146_a_month_with_the_domain_word_under_an_open_carry_question_asks_it_again(office):  # noqa: F811
    """Reviewer B1: "delivery in october" (domain_in_message true, no entity) answers neither
    option, so the carry question is asked again, never a fetch over customer AND product."""
    office.say("delivery for zzt dealer", _customer_ask())
    second = office.say(f"delivery for {CODE}", _product_ask())
    assert ((office.state or {}).get("open_question") or {}).get("kind") == "carry_scope", second
    office.calls.clear()
    third = office.say("delivery in october", _domain_month())
    assert third == second, (second, third)
    assert ((office.state or {}).get("open_question") or {}).get("kind") == "carry_scope", office.state
    both = [a for a in _orders(office) if a.get("product_ids") and a.get("customer_ids")]
    assert both == [], office.calls


def test_ac146_the_same_for_a_held_product_and_a_new_customer(dealer):  # noqa: F811
    _carried_product(dealer)
    second = dealer.say("delivery for zzt dealer", _customer_ask())
    assert second == Q_COMBINE, second
    dealer.calls.clear()
    third = dealer.say("delivery in october", _domain_month())
    assert third == Q_COMBINE, third
    assert _orders(dealer) == [], dealer.calls


def test_ac146_the_month_given_with_the_domain_word_is_kept_for_the_pick(office):  # noqa: F811
    office.say("delivery for zzt dealer", _customer_ask())
    office.say(f"delivery for {CODE}", _product_ask())
    office.say("delivery in october", _domain_month())
    office.calls.clear()
    reply = office.say("2", _answer("pick", 2))
    assert "Which period" not in reply, reply
    orders = _orders(office)
    assert orders, office.calls
    assert orders[-1].get("actual_delivery_date_from") == "2026-10-01", orders[-1]
    assert not orders[-1].get("customer_ids"), orders[-1]


def test_ac146_a_new_ask_in_another_domain_closes_the_carry_question_and_drops_the_held_subject(office):  # noqa: F811
    office.say("delivery for zzt dealer", _customer_ask())
    office.say(f"delivery for {CODE}", _product_ask())
    office.calls.clear()
    office.say(
        "any stock?",
        verdict(domain_hint="inventory", intent_hint="check_stock", domain_in_message=True, entities=[]),
    )
    assert ((office.state or {}).get("open_question") or {}).get("kind") != "carry_scope", office.state
    assert not ((office.state or {}).get("focus") or {}).get("customers"), office.state
    both = [a for _n, a in office.calls if a.get("product_ids") and a.get("customer_ids")]
    assert both == [], office.calls


def test_ac146_an_open_carry_question_and_two_non_answers_ends_in_the_loop_breaker(dealer, session_factory):  # noqa: F811
    _ask_unclear(session_factory)
    _carried_product(dealer)
    first = dealer.say("delivery today", _today_ask())
    second = dealer.say("october", _bare_month())
    third = dealer.say("october", _bare_month("october again"))
    assert first == Q_UNCLEAR and second == Q_UNCLEAR, (first, second)
    assert third != Q_UNCLEAR and "(from earlier)" not in third, third
    assert _orders(dealer) == [], dealer.calls


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


# --- round 4: the live reading is a FRESH question (domain_in_message true) ------------------ #
# Live, the parser reads "delivery for HANLIM" / "delivery today" after "SRT391-GM stock" as a new
# ask: domain_in_message true, no anaphora, no continuation. Today's focus rules then drop the held
# product silently; R2 / Q9 say ask.


def _fresh(ask: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {**ask, "domain_in_message": True, "anaphora": {"backward_reference": False}, "continuation": False, **extra}


def test_fresh_reading_held_product_and_a_new_customer_asks_and_fetches_nothing(dealer):  # noqa: F811
    _carried_product(dealer)
    reply = dealer.say("delivery for zzt dealer", _fresh(_customer_ask()))
    assert reply == Q_COMBINE, reply
    assert _orders(dealer) == [], dealer.calls


def test_fresh_reading_delivery_today_with_carry_unclear_ask_asks(dealer, session_factory):  # noqa: F811
    _ask_unclear(session_factory)
    _carried_product(dealer)
    reply = dealer.say("delivery today", _fresh(_today_ask()))
    assert reply == Q_UNCLEAR, reply
    assert _orders(dealer) == [], dealer.calls


def test_fresh_reading_delivery_today_with_the_seed_carry_asks_nothing(dealer):  # noqa: F811
    """Seed carry_unclear = carry: no ask. MEASURED: with this stubbed fresh verdict (entities [])
    the held product is KEPT in the fetch, not dropped as the brief expected, so only the no-ask
    half is pinned."""
    _carried_product(dealer)
    reply = dealer.say("delivery today", _fresh(_today_ask()))
    assert "(from earlier)" not in reply, reply
    assert _orders(dealer), dealer.calls


def test_fresh_reading_with_entity_op_replace_and_a_new_customer_still_asks(dealer):  # noqa: F811
    """R2: the user did not say to drop the product, so a replace op on the customer still asks."""
    _carried_product(dealer)
    reply = dealer.say("delivery for zzt dealer", _fresh(_customer_ask(), entity_op="replace"))
    assert reply == Q_COMBINE, reply
    assert _orders(dealer) == [], dealer.calls


# --- review round: translations (ms / zh keep the code, translate the question) ----------------- #


@pytest.mark.parametrize("text", ["ada penghantaran hari ini?", "今天有送货吗"], ids=["ms", "zh"])
def test_the_carry_question_and_its_options_are_translated_and_keep_the_code(dealer, session_factory, text):  # noqa: F811
    _ask_unclear(session_factory)
    _carried_product(dealer)
    reply = dealer.say(text, _today_ask())
    assert CODE in reply, reply
    assert "from earlier" not in reply and "All products" not in reply and "Delivery today" not in reply, reply
    assert "1." in reply and "2." in reply, reply


@pytest.mark.parametrize("text", ["ada penghantaran hari ini?", "今天有送货吗"], ids=["ms", "zh"])
def test_the_break_line_is_translated_and_keeps_the_code(dealer, session_factory, text):  # noqa: F811
    _ask_unclear(session_factory)
    _carried_product(dealer)
    dealer.say(text, _today_ask())
    dealer.say(text, _today_ask())
    third = dealer.say(text, _today_ask())
    assert "I still need" not in third and "Please refer to your salesman" not in third, third
    assert "escalate this to our team" not in third, third
    assert CODE in third, third


# --- round 6: the month given while the question was open is kept for the ask it belongs to ------ #


def test_the_month_given_while_the_carry_question_is_open_is_kept_when_it_is_answered(office):  # noqa: F811
    """cand-0230: customer, then product (carry question), then a bare month (re-asked), then "2".
    The carried kind is the customer, so position 2 is "All customers": the fetch runs for the
    product over that month with the carried customer dropped, and no "Which period" question."""
    office.say("delivery for zzt dealer", _customer_ask())
    office.say(f"delivery for {CODE}", _product_ask())
    again = office.say("十月", _bare_month())
    assert CODE in again and ((office.state or {}).get("open_question") or {}).get("kind") == "carry_scope", again
    office.calls.clear()
    reply = office.say("2", _answer("pick", 2))
    assert "Which period" not in reply, reply
    orders = _orders(office)
    assert orders, office.calls
    assert orders[-1].get("actual_delivery_date_from") == "2026-10-01", orders[-1]
    assert orders[-1].get("actual_delivery_date_to") == "2026-10-31", orders[-1]
    assert not orders[-1].get("customer_ids"), orders[-1]  # position 2 drops the carried customer
    assert orders[-1].get("product_ids"), orders[-1]  # the product named this ask stays


# --- round 6b: turns that must not re-ask an open carry_scope question; nits ------------------ #


def _open_carry(console) -> str:
    _carried_product(console)
    reply = console.say("delivery for zzt dealer", _customer_ask())
    assert ((console.state or {}).get("open_question") or {}).get("kind") == "carry_scope", console.state
    return reply


def test_a_thanks_under_an_open_carry_question_gets_the_casual_reply_and_keeps_the_question(dealer):  # noqa: F811
    _open_carry(dealer)
    reply = dealer.say("thanks", verdict(message_type="casual", domain_hint=None, entities=[], domain_in_message=False))
    assert "(from earlier)" not in reply, reply
    assert ((dealer.state or {}).get("open_question") or {}).get("kind") == "carry_scope", dealer.state
    dealer.calls.clear()
    dealer.say("2", _answer("pick", 2))
    orders = _orders(dealer)
    assert orders and not orders[-1].get("product_ids"), dealer.calls  # the later "2" still answers it


def test_an_escalation_request_under_an_open_carry_question_runs_escalation_and_closes_it(dealer):  # noqa: F811
    _open_carry(dealer)
    reply = dealer.say(
        "I want to talk to a person",
        verdict(message_type="request_for_help", intent_hint="escalation", domain_hint=None, entities=[],
                domain_in_message=False),
    )
    assert "(from earlier)" not in reply, reply
    assert ((dealer.state or {}).get("open_question") or {}).get("kind") != "carry_scope", dealer.state


def test_start_over_under_an_open_carry_question_resets_and_asks_nothing(dealer):  # noqa: F811
    _open_carry(dealer)
    reply = dealer.say(
        "start over",
        verdict(message_type="business_query", domain_hint=None, entities=[], topic_reset=True,
                domain_in_message=False),
    )
    assert "(from earlier)" not in reply, reply
    state = dealer.state or {}
    assert not (state.get("open_question") or {}), state.get("open_question")
    focus = state.get("focus") or {}
    assert not focus.get("products") and not focus.get("customers"), focus


def test_the_break_line_for_a_carried_customer_names_customers_not_products(office, session_factory):  # noqa: F811
    _ask_unclear(session_factory)
    office.say("delivery for zzt dealer", _customer_ask())
    office.say("delivery today", _today_ask())
    office.say("delivery today", _today_ask())
    third = office.say("delivery today", _today_ask())
    assert "I still need which customers to include for" in third, third
    assert "which products" not in third, third


@pytest.mark.parametrize("text", ["ada penghantaran Oktober?", "十月的送货"], ids=["ms", "zh"])
@pytest.mark.parametrize("mode,end", [("month", "2026-10-31"), ("range", "2026-10-15")], ids=["month", "range"])
def test_the_carry_question_date_words_are_translated(dealer, session_factory, text, mode, end):  # noqa: F811
    _ask_unclear(session_factory)
    _carried_product(dealer)
    ask = verdict(
        domain_hint="order", intent_hint="check_order", entities=[], date_mode=mode,
        date_filter_start="2026-10-01", date_filter_end=end,
    )
    reply = dealer.say(text, ask)
    assert CODE in reply, reply
    lowered = reply.lower()
    assert "month" not in lowered and "range" not in lowered and "from earlier" not in lowered, reply


# --- live check 13 Oct (crew on 1be8b721f, parser v52): owner rule "always ask when unclear" -- #


def _orders_for_customer_last_month() -> dict[str, Any]:
    """How the live parser reads "orders for <customer> last month": no delivery attribute."""
    return verdict(
        domain_hint="order", intent_hint="check_order", domain_in_message=True, entity_op="replace_combine",
        entities=[entity("ZZT Dealer", hint="customer")], date_mode="month",
        date_filter_start="2026-09-01", date_filter_end="2026-09-30",
    )


def _how_about_the_product() -> dict[str, Any]:
    """How the live parser reads "how about <code>" right after that answer: the product named,
    no domain word of its own, `replace_combine` (`decide`: REFINE, combine across kinds)."""
    return verdict(
        domain_hint="order", intent_hint="check_order", domain_in_message=False, continuation=True,
        entity_op="replace_combine", entities=[entity(CODE, hint="product")],
    )


def test_a_carried_customer_meeting_a_new_product_by_refine_asks_before_fetching(office):  # noqa: F811
    """Owner rule 13 Oct: the customer is not dropped or combined silently; the bot asks, in the
    word the parser read (orders, not delivery)."""
    office.say("orders for zzt dealer last month", _orders_for_customer_last_month())
    office.calls.clear()
    reply = office.say(f"how about {CODE}", _how_about_the_product())
    assert reply == (
        f"Orders for {CODE}: only {CUSTOMER} (from earlier) or all customers?\n1. Only {CUSTOMER}\n2. All customers"
    ), reply
    assert _orders(office) == [], office.calls


def test_the_label_follows_the_parser_delivery_attribute(dealer):  # noqa: F811
    _carried_product(dealer)
    assert dealer.say("delivery for zzt dealer", _customer_ask()) == Q_COMBINE
    dealer.say("start over", verdict(topic_reset=True, entities=[]))
    _carried_product(dealer)
    asked = dealer.say("orders for zzt dealer", _customer_ask(requested_attributes=[]))
    assert asked.startswith(f"Orders for {CUSTOMER}: only {CODE} (from earlier)"), asked


def test_a_miss_header_names_the_window_the_fetch_used(office, session_factory):  # noqa: F811
    """Crew item 2 (pre-existing on main): the carried September window narrowed the fetch, so
    the miss header must not say "all dates"."""
    from tests.chatbot.test_clarify_policy_table import _write_all_rows

    _write_all_rows(session_factory(), carry_combine={"mode": "carry"})
    office.say("orders for zzt dealer last month", _orders_for_customer_last_month())
    office.calls.clear()
    reply = office.say(f"how about {CODE}", _how_about_the_product())
    orders = _orders(office)
    assert orders and orders[-1].get("actual_delivery_date_from") == "2026-09-01", office.calls
    assert "Dates: 01/09/2026 to 30/09/2026" in reply and "Dates: all dates" not in reply, reply


def _staff_tier(session_factory) -> None:
    from tests.chatbot.test_dealer_eta_stock_routing import CONTACT_ID

    db = session_factory()
    db.execute(
        text("UPDATE respond_contacts SET chatbot_profile = CAST(:p AS jsonb) WHERE respond_io_id = :c"),
        {"p": '{"tier": "office"}', "c": str(CONTACT_ID)},
    )
    db.commit()


def _two_customer_groups(console) -> None:
    import uuid

    from app.models.base import set_company_scope
    from app.models.order import Customer
    from tests.chatbot.test_rearch_s3_attribute_first import SORENTO

    db = console.sf()
    set_company_scope(db, frozenset({SORENTO}))
    for name in ("ZZT HANLIM SDN BHD", "ZZT HANLIM TRADING SDN BHD"):
        db.add(Customer(id=str(uuid.uuid4()), customer_code=f"ZZT{uuid.uuid4().hex[:6]}", customer_name=name, company_id=SORENTO))
    db.commit()


def test_a_staff_miss_after_the_customer_pick_offers_no_routing_list(office, session_factory, monkeypatch):  # noqa: F811
    """Live check 13 Oct item 3 (crew trace, prompt v52): staff (`tier: office`) are offered no
    escalation (`turn/state.py::offers_escalation`), so a miss never prints "choose who to route
    to"; and the miss header names the October window the fetch used."""
    from app.services.chatbot.tail import member_offer as member_mod

    rosters = [{"body": [{"user_id": "u1", "respond_user_id": "ru1", "name": "ZZT Staff A"}, {"user_id": "u2", "respond_user_id": "ru2", "name": "ZZT Staff B"}]}]
    monkeypatch.setattr(member_mod, "fetch_rosters", lambda db, plan, ctx: rosters)
    _staff_tier(session_factory)
    _two_customer_groups(office)
    office.say(f"{CODE} stock", verdict(domain_hint="inventory", intent_hint="check_stock", entities=[entity(CODE, hint="product")]))
    office.say(
        "delivery for zzt hanlim",
        verdict(
            domain_hint="order", intent_hint="check_order", domain_in_message=True, requested_attributes=["delivery"],
            entities=[entity("ZZT HANLIM", hint="customer")],
        ),
    )
    office.say(
        "delivery in october",
        verdict(
            domain_hint="order", intent_hint="check_order", domain_in_message=True, continuation=True, entity_op="reuse",
            requested_attributes=["delivery"], entities=[], date_filter_start="2026-10-01", date_filter_end="2026-10-31",
        ),
    )
    office.say("1", _answer("pick", 1))
    office.calls.clear()
    reply = office.say("2", _answer("pick", 2))
    assert "choose who to route to" not in reply.lower(), reply
    assert "assign automatically" not in reply, reply
    assert "Dates: all dates" not in reply, reply


def test_a_refine_after_a_delivery_ask_keeps_the_word_delivery(office):  # noqa: F811
    """Reviewer S1: "how about <code>" carries no delivery attribute of its own; the held ask
    was a delivery ask (the parser's own reading, kept on the focus), so it says Delivery."""
    office.say(
        "delivery for zzt dealer last month",
        # The live shape (15 of 48 recorded delivery turns): the attribute alone, no document.
        _orders_for_customer_last_month() | {"requested_attributes": ["delivery"]},
    )
    reply = office.say(f"how about {CODE}", _how_about_the_product())
    assert reply.startswith(f"Delivery for {CODE}: only {CUSTOMER} (from earlier)"), reply


def test_the_held_delivery_reading_survives_the_session_wire():
    from app.services.chatbot.turn.state import Focus, focus_from_wire, focus_to_wire

    assert focus_from_wire(focus_to_wire(Focus(delivery_ask=True))).delivery_ask is True
    assert focus_from_wire(focus_to_wire(Focus(delivery_ask=False))).delivery_ask is False
    assert focus_from_wire({}).delivery_ask is None  # a focus stored before this slot
