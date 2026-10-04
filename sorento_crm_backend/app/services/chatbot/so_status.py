"""SO-NUMBER-ASK: the chatbot's answer to "status of SO421624".

The resolver places DO numbers only (`entity_resolver._probe_customer_order` reads
`orders.order_number`), so an SO number reaches the order lane as a word nothing placed.
This module answers it from `sales_orders` directly, on the engine's own per-contact scoped
session (company scope applies, so another company's SO is simply not found) - the same
precedent `services.resolve_warehouse_token` set for a lane read that has no MCP tool.

One card per SO, words only (owner rulings on PR #1435, behaviour card v2):

    *SO421624* - HANLIM TRADING SDN BHD [A/C I]
    Ordered 15 Sep 2026 - Open
    Delivery: partly delivered

No DO numbers: the SO->DO link (`orders.sales_order_id`) is empty in the data, so delivery is
read off `sales_order_lines.qty_ordered` / `qty_delivered` instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.order import Customer, SalesOrder, SalesOrderLine
from app.services.contact_brand_scope import product_in_scope_clauses

#: AutoCount's SO numbering ("SO422056"). A word of this shape that nothing placed is an SO ask.
SO_NUMBER_RE = re.compile(r"^SO\d{4,}$")
_SEPARATORS = re.compile(r"[\s-]+")

CANCELLED_MARK = "❗ Cancelled"


def so_key(word: str) -> str:
    """"so 421624" and "SO-421624" are SO421624: AutoCount stores the number upper case and
    unbroken, and the resolver's own token key folds the same separators away."""
    return _SEPARATORS.sub("", word).upper()


def is_so_number(word: Any) -> bool:
    return isinstance(word, str) and bool(SO_NUMBER_RE.match(so_key(word)))


@dataclass
class SoLookup:
    """`cards` in the order the numbers were typed; `refused` when any SO exists outside the
    contact's links; `missing` the typed words no SO answered, as typed."""

    cards: list[str] = field(default_factory=list)
    refused: bool = False
    missing: list[str] = field(default_factory=list)


def _status_words(status: str | None) -> str:
    s = (status or "").strip().lower()
    if s == "cancelled":
        return CANCELLED_MARK
    return s.capitalize() if s else "Unknown"


def _delivery_words(lines: list[tuple[Any, Any]]) -> str | None:
    """Over the lines that are not cancelled: none delivered, every line in full, or between."""
    if not lines:
        return None
    if all((delivered or 0) <= 0 for _ordered, delivered in lines):
        return "not delivered yet"
    if all((delivered or 0) >= (ordered or 0) for ordered, delivered in lines):
        return "fully delivered"
    return "partly delivered"


def _card(so: SalesOrder, name: str | None, lines: list[tuple[Any, Any]]) -> str:
    head = f"*{so.so_number}*" + (f" - {name}" if name else "")
    status = _status_words(so.status)
    second = f"Ordered {so.order_date.day} {so.order_date:%b %Y} - {status}" if so.order_date else status
    out = [head, second]
    if status != CANCELLED_MARK:
        delivery = _delivery_words(lines)
        if delivery:
            out.append(f"Delivery: {delivery}")
    return "\n".join(out)


def _in_scope_so_ids(db: Session, so_ids: list[str]) -> set[str] | None:
    """CONTACT-BRAND-SCOPE: the ids among `so_ids` with at least one line the contact's brands
    allow; None when the session is unscoped. These answers are built before any tool runs, so
    the fetch guard never sees them: an SO with no in-scope line must read as a missing one."""
    from app.services.contact_brand_scope import product_in_scope_clauses

    clauses = product_in_scope_clauses(db, SalesOrderLine.product_id)
    if not clauses:
        return None
    if not so_ids:
        return set()
    return {
        str(i)
        for (i,) in db.query(SalesOrderLine.sales_order_id)
        .filter(SalesOrderLine.sales_order_id.in_(so_ids), *clauses)
        .distinct()
        .all()
    }


def lookup(
    db: Session,
    words: Iterable[str],
    *,
    linked: Iterable[tuple[str, str, str]] | None,
) -> SoLookup:
    """Answer each typed SO word. `linked` is the contact's enforced customer scope
    (`(id, name, code)` per link), or None when no scope is enforced (staff, unlinked).

    In scope: the SO's customer is a link, or it has no customer and its debtor code is the
    code of a link IN THE SAME COMPANY (both SO importers keep the code when it resolves to
    nobody, and one code can name different debtors in two companies)."""
    typed = [w.strip() for w in words if is_so_number(w)]
    out = SoLookup()
    if not typed:
        return out
    keys = {so_key(w) for w in typed}
    rows = (
        db.query(SalesOrder, Customer.customer_name)
        .outerjoin(Customer, Customer.id == SalesOrder.customer_id)
        .filter(SalesOrder.so_number.in_(keys))
        .all()
    )
    allowed = _in_scope_so_ids(db, [so.id for so, _ in rows])
    if allowed is not None:
        rows = [(so, name) for so, name in rows if str(so.id) in allowed]
    by_number: dict[str, list[tuple[SalesOrder, str | None]]] = {}
    for so, master_name in rows:
        by_number.setdefault(so.so_number.upper(), []).append((so, master_name))
    lines_by_so: dict[str, list[tuple[Any, Any]]] = {}
    if rows:
        for so_id, ordered, delivered in (
            db.query(SalesOrderLine.sales_order_id, SalesOrderLine.qty_ordered, SalesOrderLine.qty_delivered)
            .filter(
                SalesOrderLine.sales_order_id.in_([so.id for so, _ in rows]),
                func.coalesce(SalesOrderLine.line_status, "") != "cancelled",
                *product_in_scope_clauses(db, SalesOrderLine.product_id),
            )
            .all()
        ):
            lines_by_so.setdefault(str(so_id), []).append((ordered, delivered))

    links = list(linked) if linked is not None else None
    link_ids = {str(i) for i, _n, _c in links} if links is not None else set()
    link_codes: set[tuple[str, str]] = set()
    if link_ids and any(so.customer_id is None for so, _ in rows):
        link_codes = {
            (str(company_id), code.strip().upper())
            for company_id, code in db.query(Customer.company_id, Customer.customer_code)
            .filter(Customer.id.in_(link_ids))
            .all()
            if code
        }

    def _in_scope(so: SalesOrder) -> bool:
        if links is None:
            return True
        if so.customer_id:
            return str(so.customer_id) in link_ids
        return (str(so.company_id), (so.debtor_code or "").strip().upper()) in link_codes

    seen: set[str] = set()
    for word in typed:
        key = so_key(word)
        if key in seen:
            continue
        seen.add(key)
        found = by_number.get(key) or []
        if not found:
            out.missing.append(word)
            continue
        visible = [(so, name) for so, name in found if _in_scope(so)]
        if not visible:
            out.refused = True
            continue
        for so, master_name in visible:
            out.cards.append(_card(so, so.debtor_name or master_name, lines_by_so.get(str(so.id), [])))
    return out


def reply_text(result: SoLookup, *, refusal: str) -> str:
    """Cards, then the scope refusal, then one miss line - each its own paragraph."""
    parts = list(result.cards)
    if result.refused and refusal:
        parts.append(refusal)
    if result.missing:
        words = result.missing
        joined = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " or " + words[-1]
        parts.append(f"I could not find {joined}.")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# The SO LIST: "all my sales orders" / "my SOs" (owner option (2), behaviour card rulings)
# --------------------------------------------------------------------------- #


def _day(d: Any) -> str:
    return f"{d.day} {d:%b %Y}"


def period_reply(start: Any, end: Any, subject: str | None) -> str | None:
    """Q1 (a): the DO ask's period rule for an SO list, or None when the period is fine.

    The same rule as `do_ask.range_reply` (its cap, its "today", its month and span words),
    in the words of a sales order list; `do_ask` is the DO list's own and is not bent to
    speak about sales orders."""
    from datetime import timedelta

    from app.services.chatbot import do_ask

    today = do_ask.today_myt()
    if start is None and end is None:
        this_month = today.replace(day=1)
        last_month = (this_month - timedelta(days=1)).replace(day=1)
        return (f"Which period for {subject}?\n" if subject else "Which period?\n") + (
            f"- This month ({do_ask._month(this_month)})\n"
            f"- Last month ({do_ask._month(last_month)})\n"
            "Or type a month (e.g. August) or dates (e.g. 15 Sep to 10 Oct)."
        )
    end = end or today
    if start is not None and start > end:
        start, end = end, start
    days = (end - start).days + 1 if start is not None else None
    if days is not None and days <= do_ask.MAX_DAYS:
        return None
    last = min(end, today)
    suggestions = [do_ask._month(last)]
    lead = ""
    if start is not None:
        if start <= last and do_ask._month(start) not in suggestions:
            suggestions.append(do_ask._month(start))
        lead = (
            f"That is {do_ask._span(start, end, days)} "
            f"({do_ask._ddmmyyyy(start)} to {do_ask._ddmmyyyy(end)}). "
        )
    return (
        f"{lead}I can show up to {do_ask.MAX_DAYS} days of sales orders at a time:\n"
        + "".join(f"- {s}\n" for s in suggestions)
        + "Or type a month or dates."
    )


def customer_names(db: Session, customer_ids: Iterable[str]) -> dict[str, str]:
    """The customers' names, in the order given (link order), so the header reads as the
    contact's own accounts are listed everywhere else."""
    ids = list(dict.fromkeys(str(i) for i in customer_ids if i))
    if not ids:
        return {}
    found = {
        str(cid): name or ""
        for cid, name in db.query(Customer.id, Customer.customer_name).filter(Customer.id.in_(ids)).all()
    }
    return {cid: found[cid] for cid in ids if cid in found}


def list_text(db: Session, names_by_id: dict[str, str], start: Any, end: Any) -> str:
    """Q2 (a) newest first, every status; Q3 (a) the group named once, or on every row when
    the customers span groups. Only SOs whose customer is one of `names_by_id` (the turn's
    customers in scope), on the engine's company-scoped session."""
    from app.services.ledger_family import family_words, group_names

    header_names = family_words(list(names_by_id.values())) or "your account"
    window = f"{_day(start)} to {_day(end)}"
    rows = (
        db.query(SalesOrder)
        .filter(
            SalesOrder.customer_id.in_(list(names_by_id)),
            SalesOrder.order_date >= start,
            SalesOrder.order_date <= end,
        )
        .order_by(SalesOrder.order_date.desc(), SalesOrder.so_number.desc())
        .all()
    )
    allowed = _in_scope_so_ids(db, [so.id for so in rows])
    if allowed is not None:
        rows = [so for so in rows if str(so.id) in allowed]
    if not rows:
        return f"No sales orders for {header_names} from {_day(start)} to {_day(end)}."
    lines_by_so: dict[str, list[tuple[Any, Any]]] = {}
    for so_id, ordered, delivered in (
        db.query(SalesOrderLine.sales_order_id, SalesOrderLine.qty_ordered, SalesOrderLine.qty_delivered)
        .filter(
            SalesOrderLine.sales_order_id.in_([so.id for so in rows]),
            func.coalesce(SalesOrderLine.line_status, "") != "cancelled",
            *product_in_scope_clauses(db, SalesOrderLine.product_id),
        )
        .all()
    ):
        lines_by_so.setdefault(str(so_id), []).append((ordered, delivered))
    several_groups = len(group_names(list(names_by_id.values()))) > 1
    out = [f"Sales orders for {header_names}, {window}:"]
    for so in rows:
        parts = [so.so_number, _day(so.order_date)]
        if several_groups:
            group = group_names([so.debtor_name or names_by_id.get(str(so.customer_id)) or ""])
            if group:
                parts.append(group[0])
        status = _status_words(so.status)
        parts.append(status)
        if status != CANCELLED_MARK:
            delivery = _delivery_words(lines_by_so.get(str(so.id), []))
            if delivery:
                parts.append(delivery)
        out.append(" - ".join(parts))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# The words decide an SO list ask (tester on 99f7edb3: the parser's reading of "okay how
# about all my sales order?" was an SO answer in 1 of 3 runs and a DO answer in the others)
# --------------------------------------------------------------------------- #

_WORDS_RE = re.compile(r"[0-9a-z]+")
_UPPER_SO_RE = re.compile(r"(?<![0-9A-Za-z])SOs?(?![0-9A-Za-z])")
_TYPED_SO_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z])SO[\s-]*\d{4,}", re.IGNORECASE)


def says_outstanding(text: str) -> bool:
    """Does the message type "outstanding" (typo-tolerant)?"""
    from app.services.chatbot.turn_runtime import _osa_distance

    return any(len(w) >= 8 and _osa_distance(w, "outstanding") <= 2 for w in _WORDS_RE.findall((text or "").casefold()))


def names_sales_orders(text: str) -> bool:
    """Does the message ask about sales orders as a list: "sales order(s)", "SOs", or "SO"
    in capitals (a lower-case "so" is the English word), with no SO number typed and no
    "outstanding" (that word, typo-tolerant, keeps the outstanding report)?"""
    raw = text or ""
    if _TYPED_SO_NUMBER_RE.search(raw):
        return False
    words = _WORDS_RE.findall(raw.casefold())
    if says_outstanding(raw):
        return False
    pairs = zip(words, words[1:])
    return (
        any(a == "sales" and b in ("order", "orders") for a, b in pairs)
        or "sos" in words
        or bool(_UPPER_SO_RE.search(raw))
    )


def typed_so_numbers_verdict(verdict: dict[str, Any], text: str) -> tuple[dict[str, Any], str | None]:
    """The verdict with the SO numbers the message TYPED as its SO entities, and the rule
    that fired. Cloud pass on PR #1435 (live parser): after the SO422056 card, "status of
    SO421624" came back as `{"raw": "SO422056", "current_message": true}` in 2 of 4 runs, the
    number copied off "Previous response". A current-message SO number the words do not hold
    is dropped, and a typed one the parser left out is added, in the order typed. Leaves the
    verdict alone when the message types no SO number."""
    typed: list[str] = []
    for m in _TYPED_SO_NUMBER_RE.finditer(text or ""):
        word = m.group(0)
        digits = word.lstrip("SOso -")
        # A lower-case "so" set apart from a short number is the English word next to a
        # quantity ("price 1500 so 1500 pcs ok"), not an SO number (reviewer, S1).
        if not word.startswith("SO") and word[2:3] in (" ", "-") and len(digits) < 6:
            continue
        key = so_key(word)
        if key not in typed:
            typed.append(key)
    if not typed:
        return verdict, None
    entities = [e for e in (verdict.get("entities") or []) if isinstance(e, dict)]

    def _is_current_so(e: dict[str, Any]) -> bool:
        return e.get("current_message") is not False and is_so_number(e.get("raw"))

    parsed = [so_key(str(e.get("raw"))) for e in entities if _is_current_so(e)]
    if parsed == typed:
        return verdict, None
    kept = [e for e in entities if not _is_current_so(e)]
    hint = next((e.get("hint") for e in entities if _is_current_so(e) and e.get("hint")), "order")
    typed_entities = [
        {"raw": key, "hint": hint, "canonical_code": None, "current_message": True, "confident": True}
        for key in typed
    ]
    return {**verdict, "entities": typed_entities + kept}, "typed_so_numbers"


#: The report statuses the parser carries off the conversation onto a message that typed
#: none of them (cloud pass on PR #1435, live parser).
_CARRIED_REPORT_STATUSES = frozenset(
    {"outstanding", "so_outstanding", "do_outstanding", "outstanding_both", "sales_report"}
)
_UPPER_DOC_RE = re.compile(r"(?<![0-9A-Za-z])(?:SO|DO)s?(?![0-9A-Za-z])")


def _names_a_document_or_report(text: str) -> bool:
    raw = text or ""
    words = _WORDS_RE.findall(raw.casefold())
    pairs = list(zip(words, words[1:]))
    return (
        says_outstanding(raw)
        or "report" in words
        or bool(_UPPER_DOC_RE.search(raw))
        or any(a in ("sales", "delivery") and b in ("order", "orders") for a, b in pairs)
    )


def carried_status_verdict(
    verdict: dict[str, Any], text: str, *, focus_document: Any, focus_status: Any, pending_kind: str | None
) -> tuple[dict[str, Any], str | None]:
    """The verdict without the report status and document the parser carried off the
    conversation, and the rule that fired. Cloud pass on PR #1435, live parser, 3 of 3:

    * "1" under the outstanding summary came back `document: ["SO"]`, `so_outstanding`,
      position 1. The document read as a NAMED one (`turn/decide`'s named-document arm, a
      new ask for the report) and the summary printed again. A pick whose words name no
      document and no report is the offer's own answer (`pick_under_offer`).
    * "september" after the SO list's "Which period?" came back `so_outstanding`, and the
      list never ran (`turn_runtime._asks_for_so_list` reads an empty status). On the SO
      list (SO document, no status, no outstanding offer open) a report status the words do
      not hold is not this message's (`period_on_so_list`).

    "outstanding", "report", or a document typed in the message keeps the parser's reading."""
    if _names_a_document_or_report(text):
        return verdict, None
    status = verdict.get("status")
    order_status = verdict.get("order_status")
    carried = status in _CARRIED_REPORT_STATUSES or order_status in _CARRIED_REPORT_STATUSES
    cleared = {
        "status": None if status in _CARRIED_REPORT_STATUSES else status,
        "order_status": None if order_status in _CARRIED_REPORT_STATUSES else order_status,
    }
    if pending_kind == "outstanding_detail":
        raw_positions = verdict.get("reference_positions")
        oqa = verdict.get("open_question_answer") if isinstance(verdict.get("open_question_answer"), dict) else {}
        picks = (
            (isinstance(raw_positions, list) and bool(raw_positions))
            or bool(oqa.get("picked"))
            or oqa.get("mode") == "pick"
        )
        if picks and (carried or verdict.get("document")):
            return {**verdict, **cleared, "document": []}, "pick_under_offer"
        return verdict, None
    if pending_kind in ("outstanding_scope", "sales_report_detail"):
        return verdict, None
    on_so_list = [str(d).upper() for d in (focus_document or [])] == ["SO"] and not focus_status
    if on_so_list and carried and _is_a_bare_period(text):
        return {**verdict, **cleared}, "period_on_so_list"
    return verdict, None


#: What a period answer is made of, besides its month and day numbers.
_PERIOD_WORDS = frozenset(
    {"this", "last", "month", "to", "from", "until", "till", "for", "in", "of", "the", "and",
     "ok", "okay", "please", "pls", "how", "about", "then", "st", "nd", "rd", "th", "may"}
)


def _is_a_bare_period(text: str) -> bool:
    """Is the message a period and nothing else ("september", "last month", "15 Sep to 10
    Oct")? Reviewer, B1: on the SO list, "which ones are still pending delivery?" or "how
    much did I buy in september" are asks of their own, not the list's period."""
    words = _WORDS_RE.findall((text or "").casefold())
    names_a_period = any(w in _MONTH_WORDS or w == "month" for w in words)
    return names_a_period and all(
        w in _MONTH_WORDS or w in _PERIOD_WORDS or w.isdigit() or re.fullmatch(r"\d+(st|nd|rd|th)", w)
        for w in words
    )


_MONTH_WORDS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4,
    "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8,
    "september": 9, "sept": 9, "sep": 9, "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def typed_month(text: str, today: Any) -> tuple[Any, Any] | None:
    """The one calendar month the message types ("september", "Sep 2026", "this month",
    "last month"), as (first day, last day); None for anything else (two months, a date
    range, no month). A month after `today`'s is last year's: the SO list looks back. "may"
    is left to the parser: it is an English word ("may I see my SOs")."""
    from datetime import date, timedelta

    words = _WORDS_RE.findall((text or "").casefold())
    pairs = list(zip(words, words[1:]))
    this_month = date(today.year, today.month, 1)
    if ("this", "month") in pairs:
        start = this_month
    elif ("last", "month") in pairs:
        start = (this_month - timedelta(days=1)).replace(day=1)
    else:
        months = [_MONTH_WORDS[w] for w in words if w in _MONTH_WORDS]
        if len(months) != 1 or any(w.isdigit() and len(w) <= 2 for w in words):
            return None
        years = [int(w) for w in words if w.isdigit() and len(w) == 4 and 2000 <= int(w) <= 2099]
        year = years[0] if years else (today.year if months[0] <= today.month else today.year - 1)
        start = date(year, months[0], 1)
    end = (start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return start, end


def typed_words_verdict(verdict: dict[str, Any], text: str, today: Any) -> tuple[dict[str, Any], str | None]:
    """An SO-document order ask with the month and the "outstanding" its words type, when
    the parser dropped them. Cloud pass on PR #1435, live parser: "september" answering the
    SO list's "Which period?" came back with no dates in 2 of 3 runs (the question was asked
    again), and "my outstanding sales orders in september" came back with no status (the SO
    list's question) or no dates (a summary over every date) in 2 of 9."""
    domain = str(verdict.get("domain_hint") or "").strip()
    document = [str(d).upper() for d in (verdict.get("document") or [])]
    if domain not in ("", "order") or document != ["SO"]:
        return verdict, None
    fixed = dict(verdict)
    rules: list[str] = []
    if not verdict.get("date_filter_start") and not verdict.get("date_filter_end"):
        month = typed_month(text, today)
        if month is not None:
            fixed["date_filter_start"], fixed["date_filter_end"] = month[0].isoformat(), month[1].isoformat()
            rules.append("typed_month")
    if says_outstanding(text) and not (verdict.get("status") or verdict.get("order_status")):
        fixed["order_status"] = "so_outstanding"
        rules.append("typed_outstanding")
    if not rules:
        return verdict, None
    return {**fixed, "domain_hint": "order"}, "+".join(rules)


def so_list_verdict(verdict: dict[str, Any], text: str) -> tuple[dict[str, Any], str | None]:
    """The verdict an SO list ask is applied with, and the rule that fired: the SO document,
    the order domain, no status, whatever the parser read. Leaves a verdict the parser put
    in another domain (a product or a promotion question) alone."""
    domain = str(verdict.get("domain_hint") or "").strip()
    if domain not in ("", "order") or not names_sales_orders(text):
        return verdict, None
    fixed = {
        **verdict,
        "domain_hint": "order",
        "domain_in_message": True,
        "document": ["SO"],
        "status": None,
        "order_status": None,
    }
    return fixed, "so_list_words"
