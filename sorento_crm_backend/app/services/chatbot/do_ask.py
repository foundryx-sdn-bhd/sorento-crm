"""A delivery order list ask carries a date range of at most 31 days.

DO-ASK-SIMPLIFY rules 3 and 4 (`documentation/plans/chatbot/PLAN-do-ask-simplify-2oct.md`,
owner answers 2 Oct 2026; applies to every contact, owner 4 Oct). A DO list ask is a fetch of one of `fetch.ORDER_TOOLS` that is
not a quantity ask and not one of the order-status buckets (outstanding, SO outstanding),
which have no delivery date to range over:

* no range: nothing is fetched and the bot asks which period, suggesting this month and
  last month (rule 3);
* a range longer than 31 days, inclusive, rolling: nothing is fetched and the bot says how
  long the asked range is, suggesting its last and its first month (rule 4);
* an ask that names one or more DO/SO/order numbers needs no range.

The reply names its suggestions as words to type, not numbers to pick, so the answer is an
ordinary dated message the engine already carries onto the open ask (the subject stays on
the focus), and no pending question has to be armed.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.services.chatbot import jsc
from app.services.ledger_family import customer_group_of, customer_header_words

#: Owner Q1 (a), 2 Oct 2026: at most 31 days, both ends included.
MAX_DAYS = 31

#: The gate entity types that name a specific order (`turn/state.py` EXTRA_KIND_ALIASES).
_ORDER_TYPES = frozenset({"order", "customer_order", "order_number"})

#: Order-status buckets with no delivery date to range over (Q3: reports are exempt).
_EXEMPT_STATUSES = frozenset({"outstanding", "so_outstanding"})


#: The reveal key a DO answer path checks outside `output_structurer` (security round 1).
TRANSPORTER_KEY = "delivery_orders.transporter"


def granted(ctx: dict[str, Any], key: str) -> bool:
    """Does this contact hold the `contact_field_reveals` grant `key`? (`ctx["access"]
    ["attributes"]`, filled by `head/access.py`; None is the empty grant set.)"""
    access = ctx.get("access") if isinstance(ctx, dict) else None
    attributes = access.get("attributes") if isinstance(access, dict) else None
    return isinstance(attributes, (list, tuple, set, frozenset)) and key in attributes


#: Security S1 / review S5: a transporter named without its reveal is refused, never
#: silently dropped from the filter (that would answer a different question).
TRANSPORTER_REFUSED = "Sorry, delivery orders can't be looked up by transporter for your account."


def today_myt() -> date:
    """Today in Malaysia time (UTC+8, no DST), the formula `fetch._current_myt_year` uses."""
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date()


def _parse(value: Any) -> date | None:
    try:
        return date.fromisoformat(jsc.js_string(value or "")[:10])
    except ValueError:
        return None


def _month(d: date) -> str:
    return d.strftime("%b %Y")


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _span(start: date, end: date, days: int) -> str:
    """Whole calendar months are said in months ("6 months"), anything else in days."""
    if start.day == 1 and (end + timedelta(days=1)).day == 1:
        months = (end.year - start.year) * 12 + end.month - start.month + 1
        return f"{months} months"
    return f"{days} days"


def _subject(entities: list[Any]) -> str | None:
    names = [
        jsc.js_string(e.get("display_name") or e.get("title") or "")
        for e in entities
        if isinstance(e, dict) and e.get("entity_type") == "customer"
    ]
    names = [n for n in names if n]
    return customer_header_words([(n, customer_group_of(n)) for n in names]) or None


def _names_an_order(entities: list[Any]) -> bool:
    return any(isinstance(e, dict) and e.get("entity_type") in _ORDER_TYPES for e in entities)


def _is_quantity_ask(semantic_input: dict[str, Any]) -> bool:
    return any(
        jsc.nullish_str(a).strip() == "quantity" for a in jsc.array(semantic_input.get("requested_attributes"))
    )


def range_reply(
    *, ctx: dict[str, Any], tool_name: str, order_tools: Any, entities: list[Any], semantic_input: dict[str, Any]
) -> str | None:
    """The line that answers this DO list ask instead of a fetch, or None to fetch."""
    if tool_name not in order_tools:
        return None
    if jsc.js_string(semantic_input.get("order_status") or "").strip() in _EXEMPT_STATUSES:
        return None
    if _is_quantity_ask(semantic_input) or _names_an_order(entities):
        return None
    today = today_myt()
    start = _parse(semantic_input.get("date_filter_start"))
    end = _parse(semantic_input.get("date_filter_end"))
    if start is None and end is None:
        this_month = today.replace(day=1)
        last_month = (this_month - timedelta(days=1)).replace(day=1)
        subject = _subject(entities)
        return (
            f"Which period for {subject}?\n" if subject else "Which period?\n"
        ) + (
            f"- This month ({_month(this_month)})\n"
            f"- Last month ({_month(last_month)})\n"
            "Or type a month (e.g. August) or dates (e.g. 15 Sep to 10 Oct)."
        )
    end = end or today
    if start is not None and start > end:
        start, end = end, start  # a parser that swapped the two ends
    days = (end - start).days + 1 if start is not None else None
    if days is not None and days <= MAX_DAYS:
        return None
    # Suggest the asked range's last month, then its first, within what has happened: a
    # range wholly in the future suggests this month.
    last = min(end, today)
    suggestions = [_month(last)]
    if start is None:
        lead = ""
    else:
        if start <= last and _month(start) not in suggestions:
            suggestions.append(_month(start))
        lead = f"That is {_span(start, end, days)} ({_ddmmyyyy(start)} to {_ddmmyyyy(end)}). "
    return (
        f"{lead}I can show up to {MAX_DAYS} days of delivery orders at a time:\n"
        + "".join(f"- {s}\n" for s in suggestions)
        + "Or type a month or dates."
    )
