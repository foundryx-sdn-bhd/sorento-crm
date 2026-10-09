"""The `chat_report` ask: what customers ask the bot, ranked or counted (CA-3).

`documentation/plans/chatbot/PLAN-chat-analytics-ca3-chat-dataset-10oct.md`, slice 5. The lane
half of `crm_report_ask` over `dataset=chat` (`GET /api/v1/order-management/report-ask`). Every
gate that protects data is the ROUTE's (the `chatbot.analytics` reveal, a dealer's customers, a
salesman's customers); the lane gate in `run_fetch` only refuses a contact without the reveal
before any call is made. This module turns the parser's reading into the route's params and asks
for the two fields the route requires, through the shared `required_fields` helper:

* the period (always);
* how many (`top_n`), only for a ranking by an axis that is not a trend. A week, month or day
  breakdown lists every bucket, and a total names no axis.

When BOTH are missing the lane asks both in ONE reply, one per line (card ruling Q1 b, Q2 b, 10 Oct
2026); whatever the answer leaves missing is asked again. Words become ids through the existing
resolvers (the engine's entity gate), never by matching strings here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.services.chatbot import jsc
from app.services.chatbot.analytics import chat_report_extractor
from app.services.chatbot import required_fields as rf
from app.services.chatbot.lanes.business import report_ask
from app.services.chatbot.lanes.business import services as business_services

ASK_NAME = "chat_report"
TOOL = report_ask.TOOL
DOMAIN = "chat_analytics"
GRANT = "chatbot.analytics"

PERIOD_QUESTION = "Which period? For example this week, September, or 1 to 15 Sep."
TOP_N_QUESTION = "How many? For example top 5."
CANCELLED = "Chat report cancelled."
GIVE_UP = (
    "I still can't read '{word}'. Ask again in one message, "
    "e.g. top 5 questions this week."
)
COULDNT_READ = "I couldn't read that report request. Try: top 5 questions this week."
NOT_ENABLED = "Chat analytics is not enabled for your account."
CATALOGUE_LINE = (
    "I can rank questions by topic, subject, product, customer, outcome, failure reason, week, month, "
    "day or language."
)

#: The ten ranking axes the route takes (`reports.ask.CHAT_DIMENSIONS`); the parser's enum is the same.
GROUP_BY = (
    "topic", "subject", "product", "customer", "outcome", "failure_reason", "week", "month", "day", "language",
)
MEASURES = ("asks", "failed", "failure_rate", "contacts")
#: A trend lists every bucket of the period: never asked "How many?".
TRENDS = ("week", "month", "day")
#: The measures counted in turns (a message once), not asks.
TURN_MEASURES = ("failed", "failure_rate")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _resolve_period(_db: Any, _word: str, extras: dict[str, Any]) -> rf.Resolved:
    dates = _dict(extras.get("reply_dates"))
    return report_ask.period_from(dates.get("start"), dates.get("end")) or rf.Resolved("unknown")


def _resolve_top_n(_db: Any, _word: str, extras: dict[str, Any]) -> rf.Resolved:
    return report_ask.top_n_from(extras.get("reply_top_n"))


PERIOD = rf.FieldSpec(name="period", noun="period", question=PERIOD_QUESTION, resolve=_resolve_period, allow_all=False)
TOP_N = rf.FieldSpec(name="top_n", noun="number", question=TOP_N_QUESTION, resolve=_resolve_top_n, allow_all=False)

CHAT_REPORT_ASK = rf.register(rf.AskType(
    name=ASK_NAME,
    fields=(PERIOD, TOP_N),
    reroute={
        "message_type": "business_query",
        "domain_hint": DOMAIN,
        "intent_hint": ASK_NAME,
    },
    cancelled=CANCELLED,
    give_up=GIVE_UP,
    # A reply carrying a period or a count is the answer however long it is.
    answer_keys=("date_filter_start", "top_n"),
    answer_drops=("chat_report",),
))


def is_chat_report(parse_output: dict[str, Any]) -> bool:
    """The parser's reading is a chat report: domain `chat_analytics` with the intent
    `chat_report` or none (the parser sometimes leaves the intent null)."""
    return jsc.js_string(parse_output.get("domain_hint") or "") == DOMAIN and jsc.js_string(
        parse_output.get("intent_hint") or ""
    ).strip() in ("", ASK_NAME)


def names_its_own_ask(verdict: dict[str, Any]) -> bool:
    """Engine seam before `required_fields.reply_verdict`: a verdict that is a chat report in
    its own right (the parser filled `chat_report` with an axis, a measure or a subject) is a
    NEW ask, so an open period / how many question is dropped, not answered by it. A reply to
    the question carries `chat_report` null."""
    if not (is_chat_report(verdict) or jsc.js_string(verdict.get("intent_hint") or "").strip() == ASK_NAME):
        return False
    report = _dict(verdict.get("chat_report"))
    return any(report.get(k) for k in ("group_by", "measure", "subject"))


def _needs_top_n(args: dict[str, Any]) -> bool:
    return bool(args.get("group_by")) and args["group_by"] not in TRENDS


def _given_top_n(args: dict[str, Any], top_n: Any) -> dict[str, Any]:
    """The count as the helper's `given`: a total or a trend needs none (settled as None,
    never asked); a number the first message named is taken."""
    if not _needs_top_n(args):
        return {"top_n": rf.Resolved("ok", value=None, label="")}
    got = report_ask.top_n_from(top_n)
    return {"top_n": got} if got.status == "ok" else {}


#: The route's per-filter cap (`order_management/report_ask._MAX_VALUES`).
MAX_IDS = 50


def _resolve_word(db: Any, kind: str, word: str) -> list[str]:
    """The ids a customer or product word names; none when it names nothing."""
    if db is None or not word:
        return []
    if kind == "customer":
        return [i for i, _name in business_services.customers_named(db, word)]
    from app.services.sales_report_service import _resolve_products

    return [str(p.id) for p in _resolve_products(db, word)] if len(word) >= 3 else []


def _args(
    db: Any, parse_output: dict[str, Any], entities: Any, *, dealer: bool, own_ids: Any = None
) -> tuple[dict[str, Any] | None, str | None]:
    """The route params a fresh ask names (every one but the period and top_n), or the one
    line to say instead of running."""
    report = _dict(parse_output.get("chat_report"))
    group_by = jsc.js_string(report.get("group_by") or "").strip().lower() or None
    if group_by is not None and group_by not in GROUP_BY:
        return None, CATALOGUE_LINE
    if dealer and group_by is not None:
        from app.services.reports.ask import CHAT_DEALER_KEYS

        if group_by not in CHAT_DEALER_KEYS:
            return None, report_ask.DEALER_REFUSAL
    measure = jsc.js_string(report.get("measure") or "").strip().lower() or "asks"
    if measure not in MEASURES:
        return None, CATALOGUE_LINE
    args: dict[str, Any] = {
        "dataset": "chat",
        "basis": "turns" if measure in TURN_MEASURES else "asks",
        "measure": measure,
        "sort": "asc" if parse_output.get("rank_direction") == "bottom" else "desc",
    }
    if group_by:
        args["group_by"] = group_by
    subject = jsc.js_string(report.get("subject") or "").strip().lower()
    if subject:
        args["subjects"] = [subject]

    # This message's customer and product words, through the existing resolvers (a customer
    # whose name carries the words, a product code by prefix, as the sales report's own).
    # A word that resolves to nothing is said, never an empty ranking that looks like data.
    for kind, param in (("customer", "customer_ids"), ("product", "product_ids")):
        ids: list[str] = []
        for entity in report_ask._current_entities(parse_output, kind):
            word = " ".join(jsc.js_string(entity.get("raw") or "").split())
            found = _resolve_word(db, kind, word)
            if dealer and kind == "customer":
                # A dealer's word only ever names its own customers: another dealer's name
                # gets the same line as a name that does not exist.
                own = {str(i) for i in own_ids or ()}
                found = [i for i in found if i in own]
            if not found:
                return None, f"I couldn't find the {kind} '{word}'."
            ids.extend(i for i in found if i not in ids)
        if ids:
            args[param] = ids[:MAX_IDS]
    return args, None


def _read_fresh(db: Any, parse_output: dict[str, Any], message_text: str) -> dict[str, Any] | None:
    """The parse output with the extractor's reading in place of the parser's chat_report, top_n
    and dates, or None when the extractor could not read the message."""
    today = datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).date()
    got = chat_report_extractor.extract_chat_report(db, message_text, today=today)
    if got is None:
        return None
    return {
        **parse_output,
        "chat_report": {k: got.get(k) for k in ("group_by", "measure", "subject")},
        "top_n": got.get("top_n"),
        "date_filter_start": got.get("date_from"),
        "date_filter_end": got.get("date_to"),
    }


def settle(
    db: Any, parse_output: dict[str, Any], entities: Any, *, dealer: bool = False, own_ids: Any = None,
    message_text: str = "",
) -> tuple[rf.Outcome | None, str | None]:
    """Every required field settled, or the line to send instead of running. `(outcome, None)`
    (send `outcome.reply` with `outcome.slot` unless `outcome.done`), or `(None, line)` for a
    word or an axis the ask cannot run with."""
    slot = parse_output.get("required_ask")
    if isinstance(slot, dict) and slot.get("ask") == ASK_NAME:
        return _answer(db, parse_output, slot), None

    # A fresh report: the focused extractor reads it, not the parser (revision 10 Oct 2026).
    read = _read_fresh(db, parse_output, message_text)
    if read is None:
        return None, COULDNT_READ
    parse_output = read
    args, line = _args(db, parse_output, entities, dealer=dealer, own_ids=own_ids)
    if args is None:
        return None, line
    top_n = parse_output.get("top_n")
    given: dict[str, Any] = _given_top_n(args, top_n)
    period = report_ask.period_from(parse_output.get("date_filter_start"), parse_output.get("date_filter_end"))
    if period is not None:
        given["period"] = period
    outcome = rf.collect(db, CHAT_REPORT_ASK, given=given, extras={"args": args, "top_n": top_n})
    return _both_questions(outcome, given), None


def _both_questions(outcome: rf.Outcome, given: dict[str, Any]) -> rf.Outcome:
    """The period question with the count question under it, when neither is settled: one
    reply asks both, one per line. The slot still asks the period first; an answer carrying
    either or both is read by `_answer`."""
    slot = outcome.slot
    if (
        slot is not None
        and slot.get("asking") == PERIOD.name
        and outcome.reply == PERIOD_QUESTION
        and "top_n" not in given
    ):
        outcome.reply = f"{PERIOD_QUESTION}\n{TOP_N_QUESTION}"
    return outcome


def _answer(db: Any, parse_output: dict[str, Any], slot: dict[str, Any]) -> rf.Outcome:
    """The reply to the period / count question, off the PARSER's own reading of it."""
    extras = _dict(slot.get("extras"))
    args = _dict(extras.get("args"))
    values = dict(_dict(slot.get("values")))
    reply_dates = {"start": parse_output.get("date_filter_start"), "end": parse_output.get("date_filter_end")}
    reply_period = report_ask.period_from(reply_dates["start"], reply_dates["end"])
    reply_top_n = report_ask.top_n_from(parse_output.get("top_n"))
    given = _given_top_n(args, extras.get("top_n"))
    if "top_n" not in given and reply_top_n.status == "ok":
        given["top_n"] = reply_top_n
    if (
        slot.get("asking") == PERIOD.name
        and reply_period is None
        and reply_top_n.status == "ok"
        and _needs_top_n(args)
        and "top_n" not in values
    ):
        # The reply gave the count only: keep it, and ask the period again (not a miss).
        values["top_n"] = {"value": reply_top_n.value, "label": reply_top_n.label}
        return rf.collect(db, CHAT_REPORT_ASK, slot={**slot, "values": values}, given=given)
    return rf.collect(
        db,
        CHAT_REPORT_ASK,
        slot=slot,
        reply=jsc.js_string(parse_output.get("required_ask_reply") or ""),
        given=given,
        extras={"reply_dates": reply_dates, "reply_top_n": parse_output.get("top_n")},
    )


def route_args(outcome: rf.Outcome) -> dict[str, Any]:
    """The settled ask as `crm_report_ask`'s params (the route's names)."""
    args = dict(_dict(outcome.extras.get("args")))
    period = _dict(_dict(outcome.values.get("period")).get("value"))
    args["date_from"] = period.get("from")
    args["date_to"] = period.get("to")
    top_n = _dict(outcome.values.get("top_n")).get("value")
    if _needs_top_n(args) and isinstance(top_n, int):
        args["top_n"] = top_n
    return args
