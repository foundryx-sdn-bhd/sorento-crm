"""The sales analysis' company question as an open question (owner hand test, 13 Oct 2026).

A contact in several companies who names none is asked which one (`api/v1/sales/analysis`); the
route hands the options on as a structured list. The lane stores that question as a `required_ask`
slot so the reply is read as its answer by the PARSER (`open_question_answer`, or its own
`sales_company` field), never by the reply's words, and the analysis then re-runs for that
company with the filters the first message carried.
"""
from __future__ import annotations

from typing import Any

from app.services.chatbot import jsc
from app.services.chatbot import required_fields as rf

ASK_NAME = "sales_analysis_company"
QUESTION_FALLBACK = "Which company?"
#: The semantic-input keys the first message set, carried across the question.
CARRIED = ("sales_channel", "group_by", "sales_basis", "date_filter_start", "date_filter_end", "top_n")


def _resolve_company(_db: Any, word: str, extras: dict[str, Any]) -> rf.Resolved:
    names = [str(n) for n in extras.get("companies") or []]
    hit = next((n for n in names if n.casefold() == " ".join(str(word or "").split()).casefold()), None)
    return rf.Resolved("ok", value=hit, label=hit) if hit else rf.Resolved("unknown")


COMPANY = rf.FieldSpec(name="company", noun="company", question=QUESTION_FALLBACK, resolve=_resolve_company)

ANALYSIS_COMPANY_ASK = rf.register(rf.AskType(
    name=ASK_NAME,
    fields=(COMPANY,),
    reroute={"message_type": "business_query", "order_status": "sales_analysis", "status": None},
    cancelled="Cancelled.",
    give_up="I still can't place '{word}'.",
    answered_by_parser=True,
))


def slot_for(options: list[str], question: str, semantic_input: dict[str, Any]) -> dict[str, Any]:
    """The open question the analysis route's clarify leaves: the options, and the filters to re-run with."""
    return {
        "ask": ASK_NAME,
        "values": {},
        "asking": "company",
        "options": [[n, n] for n in options],
        "misses": 0,
        "question": question or QUESTION_FALLBACK,
        "extras": {
            "companies": list(options),
            "args": {k: semantic_input.get(k) for k in CARRIED if semantic_input.get(k) is not None},
        },
    }


def company_named(parse_output: dict[str, Any], slot: dict[str, Any]) -> str | None:
    """The option this answer names, off the parser's fields: a declared pick's position, else its own
    `sales_company`. None when it names none of them."""
    names = [str(o[1]) for o in slot.get("options") or [] if isinstance(o, (list, tuple)) and len(o) == 2]
    answer = parse_output.get("required_ask_answer")
    for position in jsc.array(answer.get("picked") if isinstance(answer, dict) else None):
        if isinstance(position, int) and not isinstance(position, bool) and 1 <= position <= len(names):
            return names[position - 1]
    said = " ".join(str(parse_output.get("sales_company") or "").split()).casefold()
    return next((n for n in names if said and n.casefold() == said), None)
