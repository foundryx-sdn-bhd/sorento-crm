"""CA-3 RED tests: the `chat_report` lane (UAC-1 to 5, 15, 19, 21, 22).

Plan contract: documentation/plans/chatbot/PLAN-chat-analytics-ca3-chat-dataset-10oct.md "Lane".
Full console turns, the way `test_report_ask_lane.py` drives them: the REAL engine, lane and MCP
presenter; only the parser and the MCP transport are doubles. A turn's `crm_report_ask` calls are
returned beside the reply, so "nothing runs before the period and the count are settled" is pinned
as "no call on that turn".

Pinned where the plan is silent (flagged to the captain):
* the cancel line and the give-up line of UAC-4a are not worded by the plan: only "no call", the
  word "cancel" (any case) for a cancel, and "the questions are not asked a third time" are pinned;
* the lane gate's refusal wording is not fixed: one line, no call, and "not enabled for your
  account" (the sales refusal's own words, `Sales report is not enabled for your account.`);
* a 422 / 5xx has "the existing technical reply": pinned as "a non-empty line, no raw code, and the
  next ask still runs" (the turn closed).
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

import pytest

from app.services.chatbot import engine as engine_mod
from tests.chatbot.test_engine import CONTACT_ID, _parser_output
from tests.chatbot.test_outstanding_lane import (
    AnswerServices,
    FetchServices,
    _present_response,
    _seed_contact,
)
from tests.chatbot.test_top_selling_round6 import _seed_borrowable_envelope

TOOL = "crm_report_ask"
GRANT = "chatbot.analytics"
SALES_GRANT = "sales_orders.sales_report"
PERIOD_Q = "Which period? For example this week, September, or 1 to 15 Sep."
COUNT_Q = "How many? For example top 5."
_SNAKE = re.compile(r"\b[a-z]+_[a-z0-9_]+\b")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
WEEK = {"date_filter_start": "2026-10-05", "date_filter_end": "2026-10-11"}
SEP = {"date_filter_start": "2026-09-01", "date_filter_end": "2026-09-30"}
CUSTOMER_NAME = "ZZT CHAT CUSTOMER SDN BHD"
PRODUCT_CODE = "SRTWT7408"


def _e(raw: str, hint: str) -> dict[str, Any]:
    return {"raw": raw, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}


def _chat(*entities: dict[str, Any], group_by: str | None = "topic", measure: str | None = "asks",
          subject: str | None = None, **overrides: Any) -> dict[str, Any]:
    """The parser's reading of "top 5 questions this week" (plan contract, "Parser output")."""
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint="chat_analytics", intent_hint="chat_report",
        domain_in_message=True, entities=list(entities), top_n=5, rank_direction="top",
        chat_report={"group_by": group_by, "measure": measure, "subject": subject}, **WEEK,
    )
    base.update(overrides)
    return _parser_output(**base)


def _reply(**overrides: Any) -> dict[str, Any]:
    """The parser's reading of a short reply to one of the lane's questions."""
    base: dict[str, Any] = dict(
        message_type="business_query", domain_hint="master_products", intent_hint="check_product",
        entities=[], chat_report=None,
    )
    base.update(overrides)
    return _parser_output(**base)


def _body(args: dict[str, Any]) -> dict[str, Any]:
    group_by = args.get("group_by")
    rows = (
        [
            {"rank": 1, "name": "Availability", "value": 4, "numerator": None, "denominator": None},
            {"rank": 2, "name": "Order status", "value": 1, "numerator": None, "denominator": None},
        ]
        if group_by else []
    )
    return {
        "status": "ok", "message": None, "dataset": "chat", "basis": args.get("basis", "asks"),
        "measure": args.get("measure", "asks"), "unit": "asks", "group_by": group_by,
        "group_label": (group_by or "").title() or None, "sort": args.get("sort", "desc"),
        "date_from": args.get("date_from"), "date_to": args.get("date_to"), "filters": [],
        "rows": rows, "more": 0, "total_count": len(rows), "total": {"value": 5},
        "coverage": {
            "percent": 90, "taxonomy_version": 2,
            "line": "Topics labelled: 90% of asks (taxonomy v2). From bot traces, 5 Sep 2026 on.",
        },
    }


class Console:
    def __init__(self, session_factory, monkeypatch) -> None:
        self.session_factory, self.monkeypatch = session_factory, monkeypatch
        self.session_vars: dict[str, Any] = {}
        self.refuse: dict[str, Any] | None = None
        self.refuse_status = 403
        self.body_over: dict[str, Any] = {}

    def say(self, qf: dict[str, Any], body: str) -> tuple[str, list[dict[str, Any]]]:
        from app.services.chatbot import console_service
        from app.services.chatbot.head import parser as parser_mod

        self.monkeypatch.setattr(parser_mod, "parse", lambda config, user_block: qf)
        captured: list[tuple[str, dict[str, Any]]] = []
        present = _present_response()

        def _mcp(name: str, args: dict[str, Any]) -> Any:
            captured.append((name, dict(args)))
            if name != TOOL:
                return json.dumps({"has_result": False, "items": []})
            if self.refuse is not None:
                from sorento_crm_mcp.http_client import http_error_body

                raw = http_error_body(
                    json.dumps(self.refuse), status_code=self.refuse_status,
                    path="/api/v1/order-management/report-ask", method="GET",
                )
                return present(name, raw)
            return present(name, json.dumps({**_body(args), **self.body_over}))

        self.monkeypatch.setattr(engine_mod.business_services, "fetch_services", lambda db: FetchServices(mcp_call=_mcp))
        self.monkeypatch.setattr(
            engine_mod.business_services,
            "answer_services_for",
            lambda session_factory: AnswerServices(
                mcp_probe=lambda name, args: {"data": []}, family_fetch=lambda query: {"data": []}
            ),
        )
        db = self.session_factory()
        try:
            result = console_service.run_console_turn(
                db, contact_respond_id=str(CONTACT_ID), text=body,
                session_vars=copy.deepcopy(self.session_vars), run_id="zzt-chat-report-lane",
            )
        finally:
            db.close()
        self.session_vars = result.session_vars or {}
        text = result.reply_text or ""
        assert chr(0x2014) not in text and chr(0x2013) not in text and " · " not in text, (body, text)
        return text, [args for name, args in captured if name == TOOL]


def _seed(session_factory) -> dict[str, str]:
    from app.services.company_scope import DEFAULT_COMPANY_ID
    from tests._mc_lookup_seed import customer, product

    db = session_factory()
    try:
        ids = {
            "product": product(db, company_id=DEFAULT_COMPANY_ID, code=PRODUCT_CODE).id,
            "customer": customer(db, company_id=DEFAULT_COMPANY_ID, name=CUSTOMER_NAME).id,
        }
        db.commit()
        return {k: str(v) for k, v in ids.items()}
    finally:
        db.close()


def _console(session_factory, monkeypatch, *, grants: list[str]) -> Console:
    from app.services.chatbot import console_service
    from app.services.chatbot.head import parser as parser_mod

    _seed_contact(session_factory, variables={})
    _seed_borrowable_envelope(session_factory)
    console = Console(session_factory, monkeypatch)
    console.ids = _seed(session_factory)
    monkeypatch.setattr(console_service, "SessionLocal", session_factory)
    monkeypatch.setattr(
        engine_mod,
        "check_access",
        lambda db, *, agent_code, contact_id, space_id: {
            "allowed": True, "decision": "allow", "agent_name": "General",
            "attributes": grants, "all_attributes_allowed": None,
        },
    )
    monkeypatch.setattr(engine_mod, "default_space_id", lambda db: "364817")
    monkeypatch.setattr(
        parser_mod,
        "resolve_config",
        lambda db, *, current_date, override_version_id=None: parser_mod.ParserConfig(
            system_prompt="stub", prompt_version=39, provider="openai", model="gpt-test", api_key="sk-test",
        ),
    )
    return console


@pytest.fixture
def console(session_factory, monkeypatch):
    return _console(session_factory, monkeypatch, grants=[GRANT])


@pytest.fixture
def console_no_grant(session_factory, monkeypatch):
    return _console(session_factory, monkeypatch, grants=[])


@pytest.fixture
def console_sales_only(session_factory, monkeypatch):
    return _console(session_factory, monkeypatch, grants=[SALES_GRANT])


# --------------------------------------------------------------------------- grant (UAC-15)


def test_the_domain_is_gated_by_the_chat_analytics_reveal() -> None:
    from app.services.chatbot.lanes.business import answer

    assert answer.DOMAIN_GRANT_REQUIRED["chat_analytics"] == GRANT
    assert answer.DOMAIN_GRANT_REQUIRED["sales"] == SALES_GRANT, "the sales gate is untouched"


def test_no_grant_refuses_in_one_line_and_calls_nothing(console_no_grant) -> None:
    text, calls = console_no_grant.say(_chat(), "top 5 questions this week")
    assert calls == [], calls
    assert text.strip() and "\n" not in text.strip(), text
    assert "not enabled for your account" in text, "worded like the sales refusal: a feature that is not enabled"


def test_the_sales_report_reveal_alone_is_refused_before_any_call(console_sales_only) -> None:
    text, calls = console_sales_only.say(_chat(), "top 5 questions this week")
    assert calls == [] and "not enabled for your account" in text, (text, calls)


# --------------------------------------------------------------------------- args (UAC-1, 2, 5)


def test_a_complete_ask_runs_the_route_with_the_resolved_args(console) -> None:
    text, calls = console.say(_chat(), "top 5 questions this week")
    (args,) = calls
    assert args["dataset"] == "chat", args
    assert args["group_by"] == "topic" and args["top_n"] == 5, args
    assert args["measure"] == "asks" and args["basis"] == "asks", args
    assert args["date_from"] == "2026-10-05" and args["date_to"] == "2026-10-11", args
    assert args["sort"] == "desc", args
    assert args["contact_id"] and args["space_id"], args
    assert "1. Availability: 4 asks" in text and "Total: 5 asks" in text, text
    assert PERIOD_Q not in text and COUNT_Q not in text, text


def test_measure_null_reads_as_asks(console) -> None:
    _text, calls = console.say(_chat(measure=None), "top 5 questions this week")
    (args,) = calls
    assert args["measure"] == "asks" and args["basis"] == "asks", args


@pytest.mark.parametrize(
    "measure,basis",
    [("asks", "asks"), ("contacts", "asks"), ("failed", "turns"), ("failure_rate", "turns")],
)
def test_the_basis_follows_the_measure(console, measure, basis) -> None:
    _text, calls = console.say(_chat(measure=measure), "top 5 topics this week")
    (args,) = calls
    assert args["measure"] == measure and args["basis"] == basis, args


@pytest.mark.parametrize(
    "group_by", ["topic", "subject", "product", "customer", "outcome", "failure_reason", "week", "month", "day", "language"],
)
def test_every_ranking_axis_is_passed_through(console, group_by) -> None:
    _text, calls = console.say(_chat(group_by=group_by, top_n=5), "top 5 by that")
    (args,) = calls
    assert args["group_by"] == group_by, args


def test_a_subject_filter_goes_as_subjects(console) -> None:
    _text, calls = console.say(_chat(group_by="product", subject="inventory"), "most asked products about stock")
    (args,) = calls
    assert args["subjects"] == ["inventory"] and args["group_by"] == "product", args


def test_a_customer_name_resolves_to_its_id(console) -> None:
    _text, calls = console.say(_chat(_e(CUSTOMER_NAME, "customer")), "top 5 questions from that customer this week")
    (args,) = calls
    assert args["customer_ids"] == [console.ids["customer"]], args


def test_a_product_code_resolves_to_its_id(console) -> None:
    _text, calls = console.say(_chat(_e(PRODUCT_CODE, "product")), "top 5 questions about that product this week")
    (args,) = calls
    assert args["product_ids"] == [console.ids["product"]], args


def test_a_name_that_resolves_to_nothing_ends_in_i_couldnt_find(console) -> None:
    text, calls = console.say(_chat(_e("Nonexistent Trading Sdn Bhd", "customer")), "top 5 questions from nobody")
    assert calls == [], "never an empty ranking that looks like data"
    assert "I couldn't find" in text, text


# --------------------------------------------------------------------------- asking back (UAC-3, 4, 4a)


def test_no_period_asks_which_period_then_the_reply_runs_the_ask(console) -> None:
    text, calls = console.say(_chat(date_filter_start=None, date_filter_end=None), "top 5 questions")
    assert text.strip() == PERIOD_Q and calls == [], (text, calls)
    text, calls = console.say(_reply(**SEP), "September")
    (args,) = calls
    assert args["group_by"] == "topic" and args["top_n"] == 5, args
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args


def test_a_ranking_with_no_count_asks_how_many_then_the_reply_runs_the_ask(console) -> None:
    text, calls = console.say(_chat(top_n=None), "top questions this week")
    assert text.strip() == COUNT_Q and calls == [], (text, calls)
    _text, calls = console.say(_reply(top_n=5), "5")
    (args,) = calls
    assert args["top_n"] == 5 and args["group_by"] == "topic", args
    assert args["date_from"] == "2026-10-05", args


def test_both_missing_asks_both_in_one_reply_one_per_line(console) -> None:
    text, calls = console.say(_chat(top_n=None, date_filter_start=None, date_filter_end=None), "top questions")
    assert calls == [], calls
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    assert PERIOD_Q in lines and COUNT_Q in lines, lines
    assert lines.index(PERIOD_Q) < lines.index(COUNT_Q), lines


def test_one_reply_can_answer_both(console) -> None:
    console.say(_chat(top_n=None, date_filter_start=None, date_filter_end=None), "top questions")
    _text, calls = console.say(_reply(top_n=5, **SEP), "top 5 for September")
    (args,) = calls
    assert args["top_n"] == 5 and args["date_from"] == "2026-09-01", args


def test_answering_one_asks_only_the_other_again(console) -> None:
    console.say(_chat(top_n=None, date_filter_start=None, date_filter_end=None), "top questions")
    text, calls = console.say(_reply(**SEP), "September")
    assert calls == [] and text.strip() == COUNT_Q, (text, calls)
    _text, calls = console.say(_reply(top_n=3), "3")
    (args,) = calls
    assert args["top_n"] == 3 and args["date_from"] == "2026-09-01", args


def test_a_trend_never_asks_how_many(console) -> None:
    for group_by in ("week", "month", "day"):
        text, calls = console.say(_chat(group_by=group_by, top_n=None), f"questions per {group_by}")
        (args,) = calls
        assert COUNT_Q not in text, (group_by, text)
        assert not args.get("top_n"), (group_by, args)


def test_a_trend_with_no_period_asks_only_the_period(console) -> None:
    text, calls = console.say(_chat(group_by="week", top_n=None, date_filter_start=None, date_filter_end=None), "asks per week")
    assert text.strip() == PERIOD_Q and calls == [], (text, calls)


def test_a_total_needs_no_count(console) -> None:
    text, calls = console.say(_chat(group_by=None, top_n=None), "how many questions this week")
    (args,) = calls
    assert not args.get("group_by") and COUNT_Q not in text, (args, text)


def test_cancel_ends_the_ask_with_one_line(console) -> None:
    console.say(_chat(top_n=None, date_filter_start=None, date_filter_end=None), "top questions")
    text, calls = console.say(_reply(message_type="casual", intent_hint=None), "cancel")
    assert calls == [] and "cancel" in text.lower(), (text, calls)
    assert "\n" not in text.strip(), text


def test_two_unreadable_replies_end_with_a_clear_line_never_a_loop(console) -> None:
    console.say(_chat(date_filter_start=None, date_filter_end=None), "top 5 questions")
    text, calls = console.say(_reply(), "whenever")
    assert calls == [] and PERIOD_Q in text, (text, calls)
    text, calls = console.say(_reply(), "dunno")
    assert calls == [] and text.strip(), (text, calls)
    assert PERIOD_Q not in text, "asked a third time: that is a loop"


# --------------------------------------------------------------------------- errors, no data (UAC-19, 21, 22)


def test_a_403_is_said_as_the_routes_message(console) -> None:
    console.refuse = {"message": "Chat analytics is not enabled for your account.", "detail": None,
                      "code": "chat_analytics_not_enabled"}
    text, calls = console.say(_chat(), "top 5 questions this week")
    assert calls, "the route is the second gate"
    assert text.strip() == "Chat analytics is not enabled for your account.", text


def test_the_dealer_refusal_is_said_as_its_message(console) -> None:
    console.refuse = {"message": "That breakdown is not available for your account.", "detail": None,
                      "code": "report_dimension_not_allowed"}
    text, _calls = console.say(_chat(group_by="customer"), "top 5 customers asking")
    assert text.strip() == "That breakdown is not available for your account.", text


def test_a_404_is_i_couldnt_find(console) -> None:
    console.refuse_status = 404
    console.refuse = {"message": "Customer not found", "detail": None, "code": "not_found"}
    text, _calls = console.say(_chat(_e(CUSTOMER_NAME, "customer")), "top 5 questions this week")
    assert "I couldn't find" in text, text


@pytest.mark.parametrize("status", [422, 500])
def test_a_422_or_5xx_is_one_clear_line_and_the_turn_closes(console, status) -> None:
    console.refuse_status = status
    console.refuse = {"message": "boom", "detail": None, "code": "unknown_group_by"}
    text, calls = console.say(_chat(), "top 5 questions this week")
    assert calls and text.strip() and str(status) not in text and "unknown_group_by" not in text, text
    console.refuse = None
    _text, calls = console.say(_chat(), "top 5 questions this week")
    assert len(calls) == 1, "the failed turn left nothing pending"


def test_a_period_with_no_asks_says_so(console) -> None:
    console.body_over = {"rows": [], "total_count": 0, "total": {"value": 0},
                         "message": "No asks in that period. Chat data starts 5 Sep 2026."}
    text, _calls = console.say(_chat(), "top 5 questions this week")
    assert "No asks in that period. Chat data starts 5 Sep 2026." in text, text


def test_a_rate_limited_ask_says_too_many_reports(console) -> None:
    console.body_over = {"status": "busy", "rows": [], "message": "Too many reports in the last 10 minutes."}
    text, _calls = console.say(_chat(), "top 5 questions this week")
    assert text.strip() == "Too many reports in the last 10 minutes.", text


# --------------------------------------------------------------------------- reply shape (UAC-11, 14)


def test_the_reply_is_one_fact_per_line_with_no_ids_or_snake_case(console) -> None:
    text, _calls = console.say(_chat(), "top 5 questions this week")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert lines[0] == "Top 2 topics by asks", lines
    assert lines[1].startswith("Period: "), lines
    assert lines[-1].startswith("Topics labelled: 90% of asks"), lines
    assert not UUID_RE.search(text) and not _SNAKE.search(text), text


# --------------------------------------------------------------------------- review round 1


def _sales_ranking_verdict() -> dict[str, Any]:
    return _parser_output(
        message_type="business_query", domain_hint="order", intent_hint="check_order",
        order_status="sales_ranking", entities=[], group_by="sales_agent", top_n=10,
        chat_report=None, **SEP,
    )


def _chat_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in calls if a.get("dataset") == "chat"]


def test_review_a_new_sales_ranking_over_an_open_period_question_is_not_the_answer(console) -> None:
    text, calls = console.say(_chat(date_filter_start=None, date_filter_end=None), "top 5 questions")
    assert text.strip() == PERIOD_Q and calls == [], (text, calls)
    text, calls = console.say(_sales_ranking_verdict(), "top 10 sales agents for September")
    assert _chat_calls(calls) == [], "a different ask must not complete the chat report"
    assert "Availability" not in text and "Topics labelled" not in text, text


@pytest.mark.parametrize("intent", [None, "chat_report"])
def test_review_a_period_reply_with_no_or_chat_intent_is_the_answer(console, intent) -> None:
    console.say(_chat(date_filter_start=None, date_filter_end=None), "top 5 questions")
    _text, calls = console.say(_reply(intent_hint=intent, **SEP), "September")
    (args,) = _chat_calls(calls)
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args


def test_review_domain_chat_analytics_with_no_intent_still_runs_the_report(console) -> None:
    _text, calls = console.say(_chat(intent_hint=None), "top 5 questions this week")
    (args,) = _chat_calls(calls)
    assert args["group_by"] == "topic" and args["top_n"] == 5, args


def test_review_domain_chat_analytics_with_no_intent_asks_the_period(console) -> None:
    text, calls = console.say(
        _chat(intent_hint=None, date_filter_start=None, date_filter_end=None), "top 5 questions"
    )
    assert calls == [] and PERIOD_Q in text, (text, calls)


def test_review_a_dealer_cannot_tell_a_missing_customer_from_anothers(console) -> None:
    from tests.chatbot.test_report_ask_lane import _link_dealer, _other_customer

    _link_dealer(console)
    other = _other_customer(console, "ZZT OTHER DEALER SDN BHD")
    text_other, calls_other = console.say(
        _chat(_e("ZZT OTHER DEALER SDN BHD", "customer")), "top 5 questions for ZZT OTHER DEALER SDN BHD this week"
    )
    text_none, calls_none = console.say(
        _chat(_e("Nonexistent Trading Sdn Bhd", "customer")), "top 5 questions for Nonexistent Trading Sdn Bhd this week"
    )
    for args in calls_other + calls_none:
        assert other not in (args.get("customer_ids") or []), args
    assert text_other.strip() and text_other.strip() == text_none.strip(), (text_other, text_none)


# --------------------------------------------------------------------------- reply_verdict unit (review round 2)

LONG_NEW_ASK = "show me the top 10 customers by sales amount in September please"


def _open_period_slot() -> dict[str, Any]:
    from app.services.chatbot.lanes.business import chat_report

    return {"ask": chat_report.ASK_NAME, "asking": "period", "values": {}, "extras": {}, "misses": 0}


def _long_verdict(**over: Any) -> dict[str, Any]:
    base = {
        "message_type": "business_query", "domain_hint": "order", "intent_hint": "check_order",
        "order_status": "sales_ranking", "group_by": "customer", "top_n": 10,
        "date_filter_start": "2026-09-01", "date_filter_end": "2026-09-30", "open_question_answer": None,
    }
    base.update(over)
    return base


def test_review_reply_verdict_drops_the_open_period_question_for_a_new_sales_ranking() -> None:
    from app.services.chatbot import required_fields

    verdict, rule = required_fields.reply_verdict(_long_verdict(), _open_period_slot(), LONG_NEW_ASK)
    assert rule == "required_ask_dropped", rule
    assert verdict["domain_hint"] == "order", "the new ask keeps its own domain"


@pytest.mark.parametrize("intent", [None, "chat_report"])
def test_review_reply_verdict_takes_a_same_shape_period_reply_as_the_answer(intent) -> None:
    from app.services.chatbot import required_fields

    verdict, rule = required_fields.reply_verdict(
        _long_verdict(domain_hint="master_products", order_status=None, group_by=None, intent_hint=intent),
        _open_period_slot(), LONG_NEW_ASK,
    )
    assert rule == "required_ask_answer", rule
    assert verdict["domain_hint"] == "chat_analytics", "rerouted to the chat report"


# --------------------------------------------------------------------------- focused extractor (revision 10 Oct 2026)

COULDNT_READ = "couldn't read that report request"
EXTRACTED = {"group_by": "topic", "measure": "failed", "subject": "inventory", "top_n": 5,
             "date_from": "2026-10-05", "date_to": "2026-10-11"}


def _extractor(monkeypatch, result):
    """Replace the extractor with a recorder. The lane must call it through the module attribute
    (`chat_report_extractor.extract_chat_report(...)`) so this seam holds."""
    from app.services.chatbot.analytics import chat_report_extractor

    seen: list[str] = []

    def fake(db, text, *args, **kwargs):
        seen.append(text)
        return dict(result) if result is not None else None

    monkeypatch.setattr(chat_report_extractor, "extract_chat_report", fake)
    return seen


def test_extractor_a_fresh_report_calls_it_once_and_its_values_drive_the_route(console, monkeypatch) -> None:
    seen = _extractor(monkeypatch, EXTRACTED)
    disagreeing = _chat(group_by="product", measure="asks", subject=None, top_n=3, **SEP)
    _text, calls = console.say(disagreeing, "top 5 failed topics about stock this week")
    assert seen == ["top 5 failed topics about stock this week"], seen
    (args,) = _chat_calls(calls)
    assert args["group_by"] == "topic" and args["measure"] == "failed" and args["basis"] == "turns", args
    assert args["subjects"] == ["inventory"] and args["top_n"] == 5, args
    assert args["date_from"] == "2026-10-05" and args["date_to"] == "2026-10-11", args


def test_extractor_none_says_one_line_runs_nothing_and_opens_no_question(console, monkeypatch) -> None:
    _extractor(monkeypatch, None)
    text, calls = console.say(_chat(), "top 5 questions this week")
    assert calls == [] and COULDNT_READ in text and "\n" not in text.strip(), (text, calls)
    _text, calls = console.say(_reply(top_n=5, **SEP), "5")
    assert _chat_calls(calls) == [], "nothing was left open to answer"


def test_extractor_is_not_called_on_the_answering_turn(console, monkeypatch) -> None:
    """Lead sign-off 10 Oct (owner live D2): the reply is read with the open ask. The answering
    turn calls the extractor ONCE more, with `open_ask` set to the held fields."""
    from app.services.chatbot.analytics import chat_report_extractor

    seen: list[tuple[str, object]] = []
    held = {**EXTRACTED, "date_from": None, "date_to": None}

    def fake(db, text, *args, open_ask=None, **kwargs):
        seen.append((text, open_ask))
        if open_ask is None:
            return dict(held)
        return {**held, "date_from": "2026-09-01", "date_to": "2026-09-30"}

    monkeypatch.setattr(chat_report_extractor, "extract_chat_report", fake)
    text, calls = console.say(_chat(), "top 5 failed topics about stock")
    assert text.strip() == PERIOD_Q and calls == [] and len(seen) == 1, (text, calls, seen)
    _text, calls = console.say(_reply(**SEP), "September")
    assert len(seen) == 2 and seen[1][0] == "September", seen
    assert isinstance(seen[1][1], dict) and seen[1][1].get("group_by") == "topic", seen
    (args,) = _chat_calls(calls)
    assert args["date_from"] == "2026-09-01" and args["date_to"] == "2026-09-30", args
    assert args["group_by"] == "topic" and args["measure"] == "failed", "the extracted ask is kept"


def test_extractor_topic_without_a_count_asks_how_many_and_keeps_the_period(console, monkeypatch) -> None:
    """Lead sign-off 10 Oct (owner live D2): the reply is read with the open ask, so the count
    reply is a second extractor call carrying `open_ask`."""
    from app.services.chatbot.analytics import chat_report_extractor

    seen: list[tuple[str, object]] = []

    def fake(db, text, *args, open_ask=None, **kwargs):
        seen.append((text, open_ask))
        return {**EXTRACTED, "top_n": None if open_ask is None else 5}

    monkeypatch.setattr(chat_report_extractor, "extract_chat_report", fake)
    text, calls = console.say(_chat(), "top failed topics this week")
    assert text.strip() == COUNT_Q and calls == [], (text, calls)
    _text, calls = console.say(_reply(top_n=5), "5")
    assert [t for t, _ in seen] == ["top failed topics this week", "5"], seen
    assert isinstance(seen[1][1], dict), seen
    (args,) = _chat_calls(calls)
    assert args["top_n"] == 5 and args["date_from"] == "2026-10-05" and args["date_to"] == "2026-10-11", args


# --------------------------------------------------------------------------- extractor stand-in for the older tests


@pytest.fixture(autouse=True)
def _extractor_reads_the_parser_verdict(request, monkeypatch):
    """The pre-extractor tests drive the lane through the parser verdict alone. The lane now asks
    the focused extractor for a fresh report, and the test DB has no API key, so the real one
    would answer None. Stand in with the values the test's verdict carries; `test_extractor_*`
    install their own fake and this steps aside for them."""
    if request.node.name.startswith("test_extractor_"):
        return
    from app.services.chatbot.analytics import chat_report_extractor
    from app.services.chatbot.head import parser as parser_mod

    def fake(db, text, *args, **kwargs):
        verdict = parser_mod.parse(None, "")  # Console.say patched it to return this turn's verdict
        report = verdict.get("chat_report") or {}
        return {
            "group_by": report.get("group_by"),
            "measure": report.get("measure") or "asks",
            "subject": report.get("subject"),
            "top_n": verdict.get("top_n"),
            "date_from": verdict.get("date_filter_start"),
            "date_to": verdict.get("date_filter_end"),
            # lead sign-off 10 Oct (owner live round 3: customer_words / product_words): the stub
            # stands in for the extractor, so it reads the two arrays off the parser's entities.
            "customer_words": [e["raw"] for e in verdict.get("entities") or []
                               if e.get("hint") == "customer" and e.get("raw")],
            "product_words": [e["raw"] for e in verdict.get("entities") or []
                              if e.get("hint") == "product" and e.get("raw")],
        }

    monkeypatch.setattr(chat_report_extractor, "extract_chat_report", fake)


# --------------------------------------------------------------------------- re-review


def test_review_reply_verdict_drops_the_open_period_question_for_a_plain_order_count() -> None:
    from app.services.chatbot import required_fields

    verdict = {
        "message_type": "business_query", "domain_hint": "order", "intent_hint": "check_order",
        "group_by": None, "order_status": None, "top_n": None, "open_question_answer": None,
        "entities": [{"raw": "Ah Seng Motor", "hint": "customer", "current_message": True}],
        "date_filter_start": "2026-10-01", "date_filter_end": "2026-10-10",
    }
    out, rule = required_fields.reply_verdict(
        verdict, _open_period_slot(), "how many orders did Ah Seng Motor place with us this month so far"
    )
    assert rule == "required_ask_dropped", rule
    assert out["domain_hint"] == "order", out


def _dealer_args(monkeypatch, found, own, entity_words=("Zzt Dealer",)):
    from app.services.chatbot.lanes.business import chat_report

    monkeypatch.setattr(chat_report, "_resolve_word", lambda db, kind, word: list(found))
    verdict = _chat(*[_e(w, "customer") for w in entity_words])
    # lead sign-off 10 Oct: customer word via the extractor reading
    verdict["chat_report"] = {**verdict["chat_report"], "customer_words": list(entity_words)}
    return chat_report._args(None, verdict, verdict["entities"], dealer=True, own_ids=own)


def test_review_a_dealer_word_keeps_only_the_own_customer_ids(monkeypatch) -> None:
    args, line = _dealer_args(monkeypatch, ["own-1", "other-9"], ["own-1"])
    assert line is None and args["customer_ids"] == ["own-1"], (args, line)


def test_review_a_dealer_word_naming_only_another_customer_gets_the_missing_name_line(monkeypatch) -> None:
    _args_none, outside = _dealer_args(monkeypatch, ["other-9"], ["own-1"])
    _args_none2, missing = _dealer_args(monkeypatch, [], ["own-1"])
    assert _args_none is None and outside and outside == missing, (outside, missing)


def test_extractor_a_total_with_a_subject_runs_straight_to_the_route_asking_no_count(console, monkeypatch) -> None:
    _extractor(monkeypatch, {"group_by": None, "measure": "failed", "subject": "promotion", "top_n": None,
                             "date_from": "2026-09-26", "date_to": "2026-10-10"})
    text, calls = console.say(_chat(), "how many promotion questions could the bot not answer last 2 weeks")
    (args,) = _chat_calls(calls)
    assert args["subjects"] == ["promotion"] and args["measure"] == "failed" and args["basis"] == "turns", args
    assert not args.get("group_by"), args
    assert COUNT_Q not in text and PERIOD_Q not in text, text


# --------------------------------------------------------------------------- owner hand test (period lost, entity leak)

PERIOD_OCT = {"group_by": "product", "measure": "asks", "subject": None, "top_n": None,
              "date_from": "2026-10-07", "date_to": "2026-10-10"}


def _extractor_by_text(monkeypatch, readings: dict[str, dict | None]):
    """Like `_extractor`, but answers per message text (a second call would see the bare reply)."""
    from app.services.chatbot.analytics import chat_report_extractor

    seen: list[str] = []

    def fake(db, text, *args, **kwargs):
        seen.append(text)
        got = readings.get(text)
        return dict(got) if got is not None else None

    monkeypatch.setattr(chat_report_extractor, "extract_chat_report", fake)
    return seen


@pytest.mark.parametrize(
    "reply_verdict",
    [
        pytest.param(lambda: _reply(top_n=5), id="plain_reply"),
        pytest.param(
            lambda: _chat(group_by=None, measure=None, top_n=5, date_filter_start=None, date_filter_end=None),
            id="parser_reads_the_reply_as_a_chat_report_with_nothing_set",
        ),
        pytest.param(
            lambda: _chat(group_by="product", measure="asks", top_n=5, date_filter_start=None, date_filter_end=None),
            id="parser_reguesses_the_axis_on_the_reply",
        ),
    ],
)
def test_extractor_a_count_reply_keeps_the_period_the_first_message_gave(console, monkeypatch, reply_verdict) -> None:
    first = "top products customers ask about from 7 to 10 Oct"
    seen = _extractor_by_text(monkeypatch, {
        first: PERIOD_OCT,
        "top 5": {"group_by": "product", "measure": "asks", "subject": None, "top_n": 5,
                  "date_from": None, "date_to": None},
    })
    text, calls = console.say(_chat(group_by="product", top_n=None, **{"date_filter_start": "2026-10-07", "date_filter_end": "2026-10-10"}), first)
    assert text.strip() == COUNT_Q and calls == [], (text, calls)
    text, calls = console.say(reply_verdict(), "top 5")
    assert PERIOD_Q not in text, "the period was given in the first message: never asked"
    (args,) = _chat_calls(calls)
    assert args["top_n"] == 5 and args["date_from"] == "2026-10-07" and args["date_to"] == "2026-10-10", args
    # Lead sign-off 10 Oct (owner live D2): the reply is read with the open ask.
    assert seen == [first, "top 5"], "the reply is read once more, with the open ask"


MISS_LINES = ("couldn't find", "could not find", "did you mean")


def test_extractor_parser_product_entities_add_no_product_line_to_a_chat_report(console, monkeypatch) -> None:
    _extractor(monkeypatch, {"group_by": "topic", "measure": "asks", "subject": None, "top_n": None,
                             "date_from": "2026-01-01", "date_to": "2026-12-31"})
    verdict = _chat(_e("water closet", "product"), _e("basin", "product"), _e("salesperson", "product"), top_n=None)
    text, calls = console.say(verdict, "What is the top discussed water closet and basin for sorento in 2026")
    assert text.strip() == COUNT_Q, ("only the chat report's own question", text)
    lowered = text.lower()
    assert not any(m in lowered for m in MISS_LINES), text
    assert calls == []


# --------------------------------------------------------------------------- V1-DOC-SCOPE slice 3


def _hanlim_book(monkeypatch) -> list[dict[str, Any]]:
    """The resolver answers "hanlim" with TWO other customers (the R18 HANLIM book), so the
    gate would build a customer picker; returns the picker's DO probe calls."""
    from tests.chatbot.test_outstanding_lane import _ambiguous_hanlim_resolve_services

    probes: list[dict[str, Any]] = []

    def probe(**kwargs):
        probes.append(kwargs)
        return {"items": [], "has_result": False}

    services = _ambiguous_hanlim_resolve_services(probe)
    monkeypatch.setattr(engine_mod.business_services, "production_services", lambda db, *, space_id=None: services)
    return probes


@pytest.mark.parametrize("hint", ["customer", "brand"])
def test_v1_doc_scope_an_ungranted_unlinked_contact_gets_no_customer_names(console_no_grant, monkeypatch, hint) -> None:
    """V1-DOC-SCOPE slice 3 (crew ruling (b), 15 Oct): without `chatbot.analytics` an unlinked
    contact that is not document staff gets no other customer's name on a chat report ask,
    however the parser tagged the word, and no DO probe runs. (With the grant the owner's CA-3
    Q5(a) stands: the unmarked `test_a_customer_name_resolves_to_its_id` above.)"""
    probes = _hanlim_book(monkeypatch)
    text, calls = console_no_grant.say(_chat(_e("hanlim", hint)), "top 5 questions from hanlim this week")
    assert "HANLIM" not in text.upper(), text
    assert "Which one do you mean?" not in text, text
    assert probes == [] and calls == [], (probes, calls)




def test_v1_doc_scope_a_granted_unlinked_contact_runs_no_do_probe(console, monkeypatch) -> None:
    """The chat grant opens the chat report's customers, never their delivery orders: no DO
    probe runs for a granted unlinked contact (security review on 70bd1c303)."""
    probes = _hanlim_book(monkeypatch)
    console.say(_chat(_e("hanlim", "customer")), "top 5 questions from hanlim this week")
    assert probes == [], probes
