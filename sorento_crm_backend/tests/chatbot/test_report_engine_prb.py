"""Phase 2 RED tests - chat fix PR-B, the report engine (slices S1 to S4).

`documentation/plans/chatbot/PLAN-chat-fix-reports-16oct.md` and
`chat-fix-reports-acceptance-criteria.md` (AC-B1 to AC-B6, AC-B14).

Whole console turns on the `test_report_ask_lane.py` harness (real engine, lane and MCP
presenter; only the parser and the MCP transport are doubles). Every parser output below is a
dict the parser would emit: nothing here depends on the message words (AC-B14), the `body`
passed to `console.say` is decoration, and the near-miss tests prove it by sending words that
say one thing and parser fields that say another.

Wording is not pinned except where a question's text already exists on main (`PERIOD_Q`,
`TOPN_Q`, `METRIC_Q`, `BASIS_Q`): the combined reply must carry the four details NUMBERED 1 to 4,
the engine's own choice of phrasing is the coder's.
"""
from __future__ import annotations

import re
from typing import Any

import pytest

from tests.chatbot.test_engine import _parser_output, stub_access  # noqa: F401
from tests.chatbot.test_report_ask_clarify import _answer_keys
from tests.chatbot.test_report_ask_lane import (  # noqa: F401 - fixtures + helpers
    PERIOD_Q,
    SEP,
    TOPN_Q,
    _e,
    _rank,
    _refine,
    _reply,
    _seed_cabana,
    _t1,
    console,
    dealer,
)

METRIC_Q = "By quantity or by amount?"
BASIS_Q = "Delivered, ordered or outstanding?"
THIS_YEAR = {"date_filter_start": "2026-01-01", "date_filter_end": "2026-12-31"}
_NUMBERED = re.compile(r"^\s*([1-9])[.)]", re.M)


def _numbered(text: str) -> list[int]:
    return [int(n) for n in _NUMBERED.findall(text)]


def _find_slot(value: Any) -> dict[str, Any] | None:
    """The open `sales_ranking` required-ask slot inside the console's saved session."""
    if isinstance(value, dict):
        if value.get("ask") == "sales_ranking" and "asking" in value:
            return value
        for v in value.values():
            found = _find_slot(v)
            if found:
                return found
    elif isinstance(value, list):
        for v in value:
            found = _find_slot(v)
            if found:
                return found
    return None


def _open_slot(console) -> dict[str, Any]:
    slot = _find_slot(console.session_vars)
    assert slot is not None, console.session_vars
    return slot


def _bare(**overrides: Any) -> dict[str, Any]:
    """A ranking ask that names an axis and nothing else: no period, count, metric or basis."""
    base: dict[str, Any] = dict(
        group_by="customer", top_n=None, measure=None, basis=None, rank_by=None,
        date_filter_start=None, date_filter_end=None,
    )
    base.update(overrides)
    return _rank(**base)


def _answer(**overrides: Any) -> dict[str, Any]:
    """The parser's reading of a reply to the report question: every field it set, entities []."""
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint="order", intent_hint="check_order",
        order_status="sales_ranking", entities=[], group_by=None, top_n=None, measure=None,
        rank_by=None, basis=None, rank_direction=None, ranking_refine=None,
        date_filter_start=None, date_filter_end=None,
    )
    base.update(overrides)
    return _parser_output(**base)


# --------------------------------------------------------------------------- #
# S1 (AC-B1): one reply lists every missing detail
# --------------------------------------------------------------------------- #


def test_b1_a_bare_ranking_gets_one_reply_with_all_four_questions_numbered(console) -> None:
    text, calls = console.say(_bare(), "top customers")
    assert calls == [], calls
    assert _numbered(text) == [1, 2, 3, 4], text
    # The four details are the period, the count, the measure, the basis (one line each).
    low = text.lower()
    for noun in ("period", "how many", "quantity", "delivered"):
        assert noun in low, (noun, text)


def test_b1_the_slot_records_all_four_as_open(console) -> None:
    console.say(_bare(), "top customers")
    slot = _open_slot(console)
    assert not set(slot.get("values") or {}) & {"period", "top_n", "metric", "basis"}, slot
    # No default is ever applied (4 Oct ruling): nothing settled, nothing carried as a value.
    assert slot.get("misses", 0) == 0, slot


def test_b1_two_missing_details_are_one_reply_numbered_1_and_2(console) -> None:
    text, calls = console.say(_rank(group_by="customer", measure=None, basis=None, rank_by=None, **THIS_YEAR), "x")
    assert calls == [], calls
    assert _numbered(text) == [1, 2], text
    assert "period" not in text.lower() and TOPN_Q not in text, text


@pytest.mark.parametrize(
    "overrides, question",
    [
        (dict(top_n=None), TOPN_Q),
        (dict(date_filter_start=None, date_filter_end=None), PERIOD_Q),
        (dict(measure=None), METRIC_Q),
        (dict(basis=None), BASIS_Q),
    ],
    ids=["top_n", "period", "metric", "basis"],
)
def test_b1_near_miss_exactly_one_missing_detail_is_asked_alone_with_todays_text(console, overrides, question) -> None:
    text, calls = console.say(_rank(group_by="customer", **{**dict(top_n=5, measure="amount", basis="delivered", **THIS_YEAR), **overrides}), "x")
    assert calls == [], calls
    assert text.strip() == question, text
    assert _numbered(text) == [], text


def test_b1_near_miss_a_ranking_missing_nothing_runs_without_asking(console) -> None:
    text, calls = console.say(_rank(group_by="customer", top_n=5, **THIS_YEAR), "x")
    assert len(calls) == 1, (text, calls)


def test_b14_opt_in_only_another_ask_type_still_asks_one_field_per_turn() -> None:
    """The shared helper keeps one question per turn unless an ask opts in; the sales ranking
    ask's fields are the only opt-in (the chat report and every other register user stay)."""
    from app.services.chatbot import required_fields as rf

    def ok(_db, word, _extras):
        return rf.Resolved("ok", value=word, label=word)

    ask = rf.AskType(
        name="zzt_other",
        fields=(
            rf.FieldSpec(name="a", noun="alpha", question="Which alpha?", resolve=ok),
            rf.FieldSpec(name="b", noun="beta", question="Which beta?", resolve=ok),
        ),
        reroute={}, cancelled="x", give_up="y",
    )
    outcome = rf.collect(None, ask)
    assert outcome.reply == "Which alpha?", outcome.reply
    assert "beta" not in (outcome.reply or "").lower()
    assert outcome.slot["asking"] == "a"


def test_b14_the_sales_ranking_ask_is_the_only_one_that_asks_all_at_once() -> None:
    """Every OTHER registered ask answers a fresh, fully empty collect with exactly one field's
    question (no numbered list), the sales ranking ask answers with all of its four."""
    from app.services.chatbot import required_fields as rf
    from app.services.chatbot.lanes.business import report_ask  # noqa: F401 - registers
    from app.services.chatbot.lanes.business import chat_report  # noqa: F401 - registers

    assert "sales_ranking" in rf.ASKS
    outcome = rf.collect(None, rf.ASKS["sales_ranking"])
    assert _numbered(outcome.reply or "") == [1, 2, 3, 4], outcome.reply
    for name, ask in rf.ASKS.items():
        if name == "sales_ranking" or len([f for f in ask.fields if f.required]) < 2:
            continue
        reply = rf.collect(None, ask).reply or ""
        assert _numbered(reply) != [1, 2, 3, 4] and reply.count("\n") < 3, (name, reply)


# --------------------------------------------------------------------------- #
# S1 (AC-B2): one answer settles several details, read from the parser's fields
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "first",
    [
        dict(),
        dict(top_n=10),
        dict(top_n=10, measure="qty"),
        dict(top_n=10, **THIS_YEAR),
    ],
    ids=["all_four_open", "count_known", "count_and_metric_known", "count_and_period_known"],
)
def test_b2_one_answer_settles_period_count_metric_and_basis_and_runs(console, first) -> None:
    """"2026, top 10, by quantity delivered": the parser fills dates, top_n, measure, basis."""
    console.say(_bare(**first), "top customers")
    text, calls = console.say(
        _answer(top_n=10, measure="qty", basis="delivered", **THIS_YEAR), "2026, top 10, by quantity delivered"
    )
    assert len(calls) == 1, (text, calls)
    args = calls[0]
    assert args["group_by"] == "customer" and args["top_n"] == 10, args
    assert args["measure"] == "qty" and args["basis"] == "delivered", args
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-12-31", args
    assert _numbered(text) == [] or "AGENT A" in text, text


def test_b2_the_metric_may_arrive_as_rank_by_the_top_selling_key(console) -> None:
    console.say(_bare(), "top customers")
    text, calls = console.say(
        _answer(top_n=10, rank_by="quantity", basis="ordered", **THIS_YEAR), "x"
    )
    assert len(calls) == 1, (text, calls)
    args = calls[0]
    assert args["measure"] == "qty" and args["basis"] == "ordered" and args["top_n"] == 10, args


def test_b2_near_miss_the_words_say_everything_but_the_parser_set_nothing_is_a_miss(console) -> None:
    """AC-B14: the reply text names all four, the parser's fields are empty: nothing runs."""
    console.say(_bare(), "top customers")
    text, calls = console.say(_answer(), "2026, top 10, by quantity delivered")
    assert calls == [], calls
    assert text.strip(), text


def test_b2_near_miss_parser_fields_drive_it_even_when_the_words_say_otherwise(console) -> None:
    console.say(_bare(), "top customers")
    text, calls = console.say(
        _answer(top_n=3, measure="amount", basis="ordered", **THIS_YEAR), "2025, top 10, by quantity delivered"
    )
    assert len(calls) == 1, (text, calls)
    args = calls[0]
    assert args["top_n"] == 3 and args["measure"] == "amount" and args["basis"] == "ordered", args
    assert args["date_from"] == "2026-01-01", args


# --------------------------------------------------------------------------- #
# S1 (AC-B3): a partial answer is not a miss
# --------------------------------------------------------------------------- #


def test_b3_a_partial_answer_reasks_only_metric_and_basis_in_one_reply(console) -> None:
    console.say(_bare(), "top customers")
    text, calls = console.say(_answer(top_n=10, **THIS_YEAR), "2026, top 10")
    assert calls == [], calls
    assert _numbered(text) == [1, 2], text
    low = text.lower()
    assert "period" not in low and TOPN_Q not in text and "how many" not in low, text
    assert "quantity" in low and "delivered" in low, text
    slot = _open_slot(console)
    assert slot.get("misses", 0) == 0, f"a partial answer is not a miss: {slot}"


def test_b3_a_later_answer_completes_it_with_the_earlier_details_kept(console) -> None:
    console.say(_bare(), "top customers")
    console.say(_answer(top_n=10, **THIS_YEAR), "2026, top 10")
    text, calls = console.say(_answer(measure="qty", basis="delivered"), "quantity, delivered")
    assert len(calls) == 1, (text, calls)
    args = calls[0]
    assert args["top_n"] == 10, args
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-12-31", args
    assert args["measure"] == "qty" and args["basis"] == "delivered" and args["group_by"] == "customer", args


def test_b3_a_partial_answer_never_gives_up_after_several_turns(console) -> None:
    """Each partial answer settles something, so none counts toward the give-up (MAX_MISSES)."""
    console.say(_bare(), "x")
    console.say(_answer(top_n=10), "10")
    console.say(_answer(**THIS_YEAR), "2026")
    text, calls = console.say(_answer(measure="amount"), "amount")
    assert calls == [], (text, calls)
    assert "I still can't read" not in text, text
    assert "delivered" in text.lower(), text
    text, calls = console.say(_answer(basis="delivered"), "delivered")
    assert len(calls) == 1, (text, calls)
    args = calls[0]
    assert args["top_n"] == 10 and args["measure"] == "amount" and args["date_from"] == "2026-01-01", args


def test_b3_near_miss_an_empty_answer_is_still_a_miss(console) -> None:
    console.say(_bare(), "x")
    console.say(_answer(top_n=10), "10")
    text, calls = console.say(_answer(), "whatever")
    assert calls == [], calls
    slot = _open_slot(console)
    assert slot.get("misses", 0) >= 1, slot


# --------------------------------------------------------------------------- #
# S2 (AC-B4): "delivered" fills the basis, never the delivery-order list
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "keys",
    [
        dict(status="delivered", document=["DO"]),
        dict(status="delivered", document=None),
        dict(status="delivered", document=["DO", "SO"]),
    ],
    ids=["do", "no_document", "do_so"],
)
def test_b4_a_delivered_answer_keeps_the_report_ask_and_fills_the_basis(console, keys) -> None:
    console.say(_rank(group_by="customer", top_n=5, measure="amount", basis=None, **THIS_YEAR), "top 5 customers by amount 2026")
    text, calls = console.say(
        _answer(order_status="delivered", basis="delivered", intent_hint="check_order", **keys), "delivered"
    )
    assert len(calls) == 1, (text, calls)
    args = calls[0]
    assert args["basis"] == "delivered" and args["group_by"] == "customer", args
    assert args["top_n"] == 5 and args["measure"] == "amount", args
    assert args["date_from"] == "2026-01-01", args


def test_b4_an_ordered_answer_keeps_the_report_ask_too(console) -> None:
    console.say(_rank(group_by="customer", top_n=5, measure="amount", basis=None, **THIS_YEAR), "x")
    text, calls = console.say(_answer(order_status="ordered", status="ordered", basis="ordered"), "ordered")
    assert len(calls) == 1, (text, calls)
    args = calls[0]
    assert args["basis"] == "ordered" and args["group_by"] == "customer", args


def test_b4_near_miss_a_delivered_status_with_no_report_question_open_is_not_a_ranking(console) -> None:
    """With nothing asked, a delivery-order ask is still the order list, no report call."""
    text, calls = console.say(
        _answer(order_status="delivered", status="delivered", document=["DO"], basis="delivered"), "delivered orders"
    )
    assert calls == [], calls


# --------------------------------------------------------------------------- #
# S3 (AC-B5): the engine applies the parser's refine for every report type
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "order_status",
    ["sales_ranking", "top_selling", None, "delivered", "outstanding"],
    ids=["sales_ranking", "top_selling", "none", "delivered", "outstanding"],
)
def test_b5_a_refine_changes_only_the_channel_whatever_report_type_the_parser_picked(console, order_status) -> None:
    _t1(console)  # a held ranking: sales agents, top 3, Cabana, September, amount, delivered
    text, calls = console.say(
        _refine(order_status=order_status, sales_channel="project"), "only the project channel"
    )
    assert len(calls) == 1, (text, calls)
    args = calls[0]
    assert args["channel"] == "project", args
    assert args["group_by"] == "sales_agent" and args["top_n"] == 3, args
    assert args["brand_ids"] == [console.cabana], args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args
    assert args["measure"] == "amount" and args["basis"] == "delivered", args


def test_b5_a_held_sales_total_is_refined_too(console) -> None:
    """A sales total (no axis) is a held report as well: the next refine re-runs the total."""
    console.say(_rank(_e("Sorento", "brand"), group_by=None, top_n=None, **SEP), "sales total for sorento September")
    text, calls = console.say(_refine(order_status=None, sales_channel="dealer"), "dealer only")
    assert len(calls) == 1, (text, calls)
    args = calls[0]
    assert args["channel"] == "dealer" and not args.get("group_by"), args
    assert args["brand_ids"] and args["date_from"] == "2026-09-01", args


def test_b5_near_miss_no_refine_flag_means_a_fresh_ask_even_with_a_held_report(console) -> None:
    _t1(console)
    text, calls = console.say(
        _rank(group_by="sales_agent", top_n=3, ranking_refine=False, order_status="sales_ranking",
              date_filter_start=None, date_filter_end=None, sales_channel="project"),
        "top 3 sales agents project",
    )
    assert calls == [], (text, calls)
    assert text.strip() == PERIOD_Q, text


def test_b5_near_miss_a_refine_with_no_held_report_of_any_type_asks(console) -> None:
    text, calls = console.say(_refine(order_status=None, sales_channel="project"), "project only")
    assert calls == [], (text, calls)


def test_b5_near_miss_another_ask_in_between_drops_the_held_report(console) -> None:
    from tests.chatbot.test_report_ask_lane import CUSTOMER_NAME, _order_ask

    _t1(console)
    console.say(_order_ask(_e(CUSTOMER_NAME, "customer")), f"orders for {CUSTOMER_NAME}")
    text, calls = console.say(_refine(order_status=None, sales_channel="project"), "project only")
    assert calls == [], (text, calls)


def test_b5_a_multi_ask_report_stores_its_frame_so_the_next_refine_applies(session_factory, monkeypatch, stub_access) -> None:
    """A report produced through the multi-ask path (sub_asks) is a held report like a single ask."""
    from tests.chatbot.test_multi_ask_runner import _console as multi_console
    from tests.chatbot.test_multi_ask_runner import owner_sub_asks
    from tests.chatbot._turn_helpers import entity, verdict

    c = multi_console(session_factory, monkeypatch, stub_access, "+60000009611")
    c.say(
        "SRTWC286-SH 2026 sold total and by sales agent",
        verdict(
            domain_hint="purchase_cost", intent_hint="check_po_cost",
            entities=[entity("SRTWC286-SH", hint="product")],
            sub_asks=[a for a in owner_sub_asks() if a["measure"] in ("sold_total", "sold_by")],
            family=False,
        ),
    )
    before = len(c.tools.args("crm_report_ask"))
    assert before >= 1
    c.say(
        "only the project channel",
        verdict(
            domain_hint="order", intent_hint="check_order", order_status="sales_ranking",
            ranking_refine=True, entities=[], group_by=None, top_n=None, measure=None, basis=None,
            sales_channel="project", date_filter_start=None, date_filter_end=None,
        ),
    )
    refined = c.tools.args("crm_report_ask")[before:]
    assert refined, "the refine did not re-run the held report"
    assert refined[-1].get("channel") == "project", refined
    assert refined[-1].get("date_from") == "2026-01-01" and refined[-1].get("date_to") == "2026-12-31", refined
    assert refined[-1].get("product_ids") or refined[-1].get("product_code"), refined


# --------------------------------------------------------------------------- #
# S4 (AC-B6): a new axis is asked, the held period and product are not
# --------------------------------------------------------------------------- #


def _held_product_ranking(console) -> None:
    from tests.chatbot.test_report_ask_lane import PRODUCT_CODE

    console.say(
        _rank(_e(PRODUCT_CODE, "product"), group_by="sales_agent", top_n=3, measure="amount", basis="delivered", **THIS_YEAR),
        f"top 3 sales agents for {PRODUCT_CODE} 2026",
    )


def test_b6_a_new_axis_is_asked_and_keeps_the_held_period_and_product(console) -> None:
    from tests.chatbot.test_report_ask_lane import PRODUCT_CODE

    _held_product_ranking(console)
    text, calls = console.say(
        _rank(group_by="customer", top_n=None, measure=None, basis=None, ranking_refine=True,
              date_filter_start=None, date_filter_end=None, drill_rank=None),
        "and the top customer",
    )
    # Still asked (8 Oct rule): nothing runs on a guess.
    assert calls == [], calls
    low = text.lower()
    assert "period" not in low and PERIOD_Q not in text, f"the held period is asked again: {text}"
    assert text.strip(), text
    # The answer completes it with the held period and product.
    text, calls = console.say(_answer(top_n=1, measure="amount", basis="delivered"), "1, amount, delivered")
    assert len(calls) == 1, (text, calls)
    args = calls[0]
    assert args["group_by"] == "customer" and args["top_n"] == 1, args
    assert args["date_from"] == "2026-01-01" and args["date_to"] == "2026-12-31", args
    assert args.get("product_code") == PRODUCT_CODE or args.get("product_ids"), args


def test_b6_the_new_axis_does_not_run_on_a_guess(console) -> None:
    _held_product_ranking(console)
    text, calls = console.say(
        _rank(group_by="customer", top_n=1, ranking_refine=True, measure=None, basis=None,
              date_filter_start=None, date_filter_end=None),
        "the top customer",
    )
    assert calls == [] or all(c.get("group_by") != "customer" for c in calls) or text.strip(), (text, calls)
    assert calls == [], calls


def test_b6_near_miss_a_new_axis_that_names_its_own_period_uses_it_not_the_held_one(console) -> None:
    _held_product_ranking(console)
    text, calls = console.say(
        _rank(group_by="customer", top_n=1, ranking_refine=True, measure="amount", basis="delivered",
              date_filter_start="2026-09-01", date_filter_end="2026-09-30"),
        "the top customer in september",
    )
    for c in calls:
        assert c["date_from"] == "2026-09-01" and c["date_to"] == "2026-09-30", c


def test_b6_near_miss_a_fresh_unrelated_ask_with_no_held_report_still_asks_the_period(console) -> None:
    text, calls = console.say(_bare(group_by="customer", top_n=1, measure="amount", basis="delivered"), "top customer")
    assert calls == [] and text.strip() == PERIOD_Q, (text, calls)


# --------------------------------------------------------------------------- #
# S1b (AC-B1 for the top selling ask): metric and basis in ONE reply
# --------------------------------------------------------------------------- #

from tests.chatbot.test_outstanding_lane import _seed_contact  # noqa: E402
from tests.chatbot.test_outstanding_lane import _session_of  # noqa: E402
from tests.chatbot.test_top_selling_lane import (  # noqa: E402,F401 - fixtures + helpers
    ASK_BASIS as TS_BASIS_Q,
    ASK_GROUP as TS_GROUP_Q,
    ASK_METRIC as TS_METRIC_Q,
    _calls as ts_calls,
    _ts,
    _turn as ts_turn,
    route,
)


def _asked(session_factory) -> Any:
    def find(v: Any) -> Any:
        if isinstance(v, dict):
            ts = v.get("top_selling")
            if isinstance(ts, dict) and "asked" in ts:
                return ts["asked"]
            for x in v.values():
                got = find(x)
                if got is not None:
                    return got
        elif isinstance(v, list):
            for x in v:
                got = find(x)
                if got is not None:
                    return got
        return None

    return find(_session_of(session_factory))


def _ts_bare(**overrides: Any) -> dict[str, Any]:
    return _ts(rank_by=None, basis=None, top_n=100, **overrides)


def test_b1b_a_top_selling_ask_with_neither_metric_nor_basis_gets_one_numbered_reply(
    session_factory, monkeypatch, route
) -> None:
    _seed_contact(session_factory, variables={})
    reply, captured = ts_turn(session_factory, monkeypatch, _ts_bare(), "top 100 hot selling item")
    assert ts_calls(captured) == [], captured
    assert _numbered(reply) == [1, 2], reply
    low = reply.lower()
    assert "quantity" in low and "amount" in low and "delivered" in low and "ordered" in low, reply


def test_b1b_the_recorded_asked_axis_covers_both_questions(session_factory, monkeypatch, route) -> None:
    _seed_contact(session_factory, variables={})
    ts_turn(session_factory, monkeypatch, _ts_bare(), "top 100 hot selling item")
    asked = _asked(session_factory)
    assert asked, asked
    assert asked != "metric", f"only the metric axis is recorded as asked: {asked!r}"
    flat = asked if isinstance(asked, (list, tuple, set)) else [asked]
    assert "basis" in " ".join(str(a) for a in flat) or "both" in " ".join(str(a) for a in flat), asked


def test_b1b_one_reply_carrying_rank_by_and_basis_settles_both_and_runs(session_factory, monkeypatch, route) -> None:
    _seed_contact(session_factory, variables={})
    ts_turn(session_factory, monkeypatch, _ts_bare(), "top 100 hot selling item")
    reply, captured = ts_turn(
        session_factory, monkeypatch, _ts(rank_by="amount", basis="ordered", top_n=None), "x"
    )
    calls = ts_calls(captured)
    assert len(calls) == 1, (reply, calls)
    assert calls[0]["rank_by"] == "amount" and calls[0]["basis"] == "ordered" and calls[0]["n"] == 100, calls


def test_b1b_a_reply_with_only_rank_by_reasks_only_the_basis_and_is_still_an_answer(
    session_factory, monkeypatch, route
) -> None:
    _seed_contact(session_factory, variables={})
    ts_turn(session_factory, monkeypatch, _ts_bare(), "top 100 hot selling item")
    reply, captured = ts_turn(session_factory, monkeypatch, _ts(rank_by="quantity", basis=None, top_n=None), "x")
    assert ts_calls(captured) == [], captured
    assert reply.strip() == TS_BASIS_Q, reply
    # Still read as an answer (not a new ask): the count and metric carry, the basis completes.
    reply, captured = ts_turn(session_factory, monkeypatch, _ts(rank_by=None, basis="delivered", top_n=None), "x")
    calls = ts_calls(captured)
    assert len(calls) == 1, (reply, calls)
    assert calls[0]["rank_by"] == "quantity" and calls[0]["basis"] == "delivered" and calls[0]["n"] == 100, calls


def test_b1b_a_reply_with_only_the_basis_reasks_only_the_metric(session_factory, monkeypatch, route) -> None:
    _seed_contact(session_factory, variables={})
    ts_turn(session_factory, monkeypatch, _ts_bare(), "top 100 hot selling item")
    reply, captured = ts_turn(session_factory, monkeypatch, _ts(rank_by=None, basis="ordered", top_n=None), "x")
    assert ts_calls(captured) == [], captured
    assert reply.strip() == TS_METRIC_Q, reply
    # Still read as an answer: the count and the basis it settled carry to the next reply.
    reply, captured = ts_turn(session_factory, monkeypatch, _ts(rank_by="amount", basis=None, top_n=None), "x")
    calls = ts_calls(captured)
    assert len(calls) == 1, (reply, calls)
    assert calls[0]["rank_by"] == "amount" and calls[0]["basis"] == "ordered" and calls[0]["n"] == 100, calls


def test_b1b_near_miss_the_words_say_both_but_the_parser_set_neither_is_a_miss(
    session_factory, monkeypatch, route
) -> None:
    _seed_contact(session_factory, variables={})
    ts_turn(session_factory, monkeypatch, _ts_bare(), "top 100 hot selling item")
    reply, captured = ts_turn(
        session_factory, monkeypatch, _ts(rank_by=None, basis=None, top_n=None), "by quantity, delivered"
    )
    assert ts_calls(captured) == [], captured


def test_b1b_unchanged_the_group_question_is_still_asked_alone_first(session_factory, monkeypatch, route) -> None:
    _seed_contact(session_factory, variables={})
    reply, captured = ts_turn(
        session_factory, monkeypatch, _ts(rank_group="unclear", rank_by=None, basis=None, top_n=3), "top selling by category"
    )
    assert reply.strip() == TS_GROUP_Q, reply
    assert ts_calls(captured) == [], captured


def test_b1b_unchanged_one_missing_detail_is_asked_alone(session_factory, monkeypatch, route) -> None:
    _seed_contact(session_factory, variables={})
    reply, _c = ts_turn(session_factory, monkeypatch, _ts(rank_by=None, basis="delivered", top_n=5), "top 5 selling")
    assert reply.strip() == TS_METRIC_Q, reply
    assert _numbered(reply) == []


def test_b1b_unchanged_a_picked_row_is_never_asked_about(session_factory, monkeypatch, route) -> None:
    """detail_code (a picked row of a ranking that ran) never gets a metric or basis question."""
    from app.services.chatbot.lanes import business as business_pkg

    assert business_pkg._top_selling_question({"detail_code": "SRTWT7445", "rank_by": None, "basis": None}) is None


# --------------------------------------------------------------------------- #
# S2b (AC-B15): the open top selling question is answered by a parse that fills its field,
# whatever order_status the parser put on it (R12, R17)
# --------------------------------------------------------------------------- #

TS_TOOL_ARGS = ts_calls


def _open_ts_basis(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    reply, captured = ts_turn(
        session_factory, monkeypatch, _ts(rank_by="quantity", basis=None, top_n=5), "top 5 selling items"
    )
    assert reply.strip() == TS_BASIS_Q and ts_calls(captured) == [], (reply, captured)


@pytest.mark.parametrize(
    "keys",
    [
        dict(order_status="sales_ranking"),
        dict(order_status="sales_ranking", group_by="category"),
        dict(order_status="sales_report"),
        dict(order_status=None),
    ],
    ids=["sales_ranking_R12", "sales_ranking_category_R17", "sales_report", "none"],
)
def test_b15_a_basis_answer_tagged_with_another_report_type_runs_the_top_selling_list(
    session_factory, monkeypatch, route, keys
) -> None:
    _open_ts_basis(session_factory, monkeypatch)
    reply, captured = ts_turn(
        session_factory, monkeypatch,
        _parser_output(
            message_type="business_query", domain_hint="order", intent_hint="check_order", entities=[],
            basis="delivered", rank_by=None, top_n=None, measure=None, ranking_refine=None, **keys,
        ),
        "delivered",
    )
    calls = ts_calls(captured)
    assert len(calls) == 1, (reply, [n for n, _a in captured])
    assert calls[0]["basis"] == "delivered" and calls[0]["rank_by"] == "quantity" and calls[0]["n"] == 5, calls
    assert not [a for n, a in captured if n == "crm_report_ask"], captured


def test_b15_near_miss_the_other_report_type_with_no_open_field_filled_is_not_the_answer(
    session_factory, monkeypatch, route
) -> None:
    """A parse that fills neither the basis nor anything the question holds does not run the list."""
    _open_ts_basis(session_factory, monkeypatch)
    _reply, captured = ts_turn(
        session_factory, monkeypatch,
        _parser_output(
            message_type="business_query", domain_hint="order", intent_hint="check_order", entities=[],
            order_status="sales_ranking", basis=None, rank_by=None, top_n=None, measure=None, group_by=None,
        ),
        "x",
    )
    assert ts_calls(captured) == [], captured


# --------------------------------------------------------------------------- #
# S2c (AC-B15): a new report ask while the ranking basis question is open is not a failed answer
# --------------------------------------------------------------------------- #


def _open_ranking_basis(console) -> None:
    text, calls = console.say(
        _rank(group_by="customer", top_n=10, measure="qty", basis=None, **THIS_YEAR), "top 10 customers 2026 by qty"
    )
    assert text.strip() == BASIS_Q and calls == [], (text, calls)


def test_b15_a_new_report_ask_that_fills_no_open_field_is_never_a_failed_basis_answer(console) -> None:
    _open_ranking_basis(console)
    text, calls = console.say(
        _parser_output(
            message_type="business_query", domain_hint="order", intent_hint="check_order", entities=[],
            order_status="top_selling", group_by="customer", basis=None, rank_by=None, top_n=None, measure=None,
        ),
        "i want to see the top customer",
    )
    assert "i don't know" not in text.lower(), text
    assert "as a basis" not in text.lower(), text


def test_b15_near_miss_a_parse_that_fills_the_basis_is_still_the_answer(console) -> None:
    _open_ranking_basis(console)
    text, calls = console.say(
        _parser_output(
            message_type="business_query", domain_hint="order", intent_hint="check_order", entities=[],
            order_status="top_selling", group_by=None, basis="delivered", rank_by=None, top_n=None, measure=None,
        ),
        "delivered",
    )
    assert len(calls) == 1, (text, calls)
    assert calls[0]["basis"] == "delivered" and calls[0]["measure"] == "qty" and calls[0]["top_n"] == 10, calls


# --------------------------------------------------------------------------- #
# S2d guard: the slot's axis wins on an answering turn.
# The parser's group_by on an answering turn is noise 12 of 14 live turns (PR-B replay 16 Oct);
# the slot's axis wins.
# --------------------------------------------------------------------------- #

OCT = {"date_filter_start": "2026-10-01", "date_filter_end": "2026-10-31"}


def _open_ts_after_metric(session_factory, monkeypatch) -> None:
    _seed_contact(session_factory, variables={})
    reply, captured = ts_turn(
        session_factory, monkeypatch, _ts(rank_by=None, basis=None, top_n=5, **OCT), "top 5 products this month"
    )
    assert ts_calls(captured) == [], (reply, captured)
    reply, captured = ts_turn(
        session_factory, monkeypatch, _ts(rank_by="quantity", basis=None, top_n=None), "1"
    )
    assert ts_calls(captured) == [], (reply, captured)


def _axis_turn(session_factory, monkeypatch, **keys):
    base = dict(
        domain_hint="order", intent_hint="check_order", order_status="top_selling", entities=[],
        top_n=5, rank_by="quantity", basis="delivered", ranking_refine=True, group_by=None,
    )
    base.update(keys)
    return ts_turn(session_factory, monkeypatch, _parser_output(**base), "how about by brand instead?")


@pytest.mark.parametrize("group_by", [None, "product", "item"], ids=["no_axis", "same_axis_product", "same_axis_item"])
def test_b6d_near_miss_no_axis_or_the_same_axis_is_a_plain_answer(session_factory, monkeypatch, route, group_by) -> None:
    _open_ts_after_metric(session_factory, monkeypatch)
    reply, captured = _axis_turn(session_factory, monkeypatch, group_by=group_by)
    calls = ts_calls(captured)
    assert len(calls) == 1 and calls[0]["basis"] == "delivered" and calls[0]["n"] == 5, (reply, captured)
    assert not [a for n, a in captured if n == "crm_report_ask"], captured


@pytest.mark.parametrize("group_by", [None, "customer"], ids=["no_axis", "same_axis"])
def test_b6d_near_miss_sales_ranking_same_or_no_axis_is_a_plain_answer(console, group_by) -> None:
    console.say(_bare(top_n=5, **THIS_YEAR), "top 5 customers 2026")
    text, calls = console.say(_answer(group_by=group_by, measure="qty", basis="delivered"), "qty delivered")
    assert len(calls) == 1 and calls[0]["group_by"] == "customer" and calls[0]["top_n"] == 5, (text, calls)


def test_b6d_guard_a_noisy_group_by_on_a_count_answer_keeps_the_held_axis_R16(console) -> None:
    """The parser's group_by on an answering turn is noise 12 of 14 live turns (PR-B replay 16 Oct);
    the slot's axis wins."""
    first, c1 = console.say(_bare(top_n=None, measure="qty", basis="delivered", **THIS_YEAR), "top customers 2026")
    assert c1 == [] and first.strip() == TOPN_Q, first
    text, calls = console.say(_answer(top_n=5, group_by="sales_agent"), "top 5")
    assert len(calls) == 1, (text, calls)
    assert calls[0]["group_by"] == "customer" and calls[0]["top_n"] == 5, calls


def test_b6d_guard_a_noisy_group_by_on_a_basis_answer_keeps_the_top_selling_items_R12_R17(
    session_factory, monkeypatch, route
) -> None:
    """The parser's group_by on an answering turn is noise 12 of 14 live turns (PR-B replay 16 Oct);
    the slot's axis wins."""
    _open_ts_basis(session_factory, monkeypatch)
    reply, captured = ts_turn(
        session_factory, monkeypatch,
        _parser_output(
            message_type="business_query", domain_hint="order", intent_hint="check_order", entities=[],
            order_status="sales_ranking", group_by="category", basis="delivered", rank_by=None, top_n=None,
            measure=None, ranking_refine=None,
        ),
        "delivered",
    )
    calls = ts_calls(captured)
    assert len(calls) == 1 and calls[0]["basis"] == "delivered" and calls[0].get("group") in (None, "item"), (reply, captured)
    assert not [a for n, a in captured if n == "crm_report_ask"], captured
