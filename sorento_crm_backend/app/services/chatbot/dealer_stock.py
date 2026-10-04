# The dealer's stock ask never offers an escalation (owner ruling 26 Sep 2026, hand
# test F1: "dealer ask cannot have escalation, cannot have direct escalation to
# warehouse, their contact point is sales person").
#
# A dealer is an availability-only contact (`Profile.stock_availability_only`). Every
# place a stock ask would say "would you like me to escalate to warehouse team?" or
# store a team pick says "Please refer to your salesman." instead, with no pending
# question; a did-you-mean becomes a pick of the suggested code(s), carrying the typed
# quantity. Staff contacts (detailed / compact) never reach this module.
#
# Pure: no I/O. Lives outside `turn/` because it reads reply TEXT (the escalation
# sentence a composer already printed), which the turn package never does.
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from app.services.chatbot.turn import refer
from app.services.chatbot.turn.pending import ESCALATION_OFFER_KINDS, Pending, ask
from app.services.chatbot.turn.task import MAX_SLOTS, REFER_TO_SALESMAN, numbered

#: Every escalation sentence the stock ask's composers print
#: (`lanes/business/answer.py`, `turn/compose.py`), whole: the "reply with a code"
#: lead-in goes with it, since what it offered next is gone.
_ESCALATION = re.compile(
    r"\s*(?:Reply (?:with )?a (?:code|number|date) to (?:continue|pick), or )?"
    r"(?:[Ww]ould you like me|[Ss]hall I|[Dd]o you want me) to escalate[^?\n]*\?"
    r"|\s*Reply a number to pick, or 'yes' to escalate to [^.\n]*\."
)


def refers_to_salesman(text: str) -> tuple[str, bool]:
    """`text` with every escalation offer taken out, and whether one was."""
    stripped, count = _ESCALATION.subn("", text or "")
    return stripped.rstrip(), bool(count)


def without_escalation(text: str, question: Pending | None) -> tuple[str, Pending | None]:
    """The dealer's version of a stock reply: no escalation sentence, no escalation
    pending, and "Please refer to your salesman." wherever one was offered. A roster the
    reply also carried (a pick of products) stays, without its attached offer."""
    from app.services.chatbot.order_list import _picker_frame_lines, _without_picker
    from app.services.chatbot.turn.pending import without_escalation as strip_pending

    stripped, had_sentence = refers_to_salesman(text)
    offered = had_sentence
    # REFER-ONLY-FIXES (owner console, 3 Oct 2026): the routing picker the offer hung off
    # goes with it, its numbered staff rows included, never left under the refer line,
    # and the question loses the member options it no longer shows.
    whole, prefixes = _picker_frame_lines()
    if any(line.strip() in whole or line.strip().startswith(prefixes) for line in stripped.splitlines()):
        question, dropped = strip_pending(question)
        stripped = _without_picker(stripped, dropped or [])
        offered = True
    if question is not None:
        if question.kind in ESCALATION_OFFER_KINDS:
            question = None
            offered = True
        elif (question.payload or {}).get("escalate_offered") is True:
            question = replace(
                question, payload={**question.payload, "escalate_offered": False}
            )
            offered = True
    if not offered:
        return text, question
    body = stripped.strip()
    if REFER_TO_SALESMAN in body:
        # ESCALATION-CONTROL: a blocked contact's composer already printed the line.
        return body, question
    return refer.after(body), question


def did_you_mean(
    typed: str,
    candidates: list[dict[str, Any]],
    *,
    quantity: Any = None,
    asked_at_turn: int | None = None,
) -> tuple[str, Pending] | None:
    """The dealer's did-you-mean: the suggested code(s) as a pick carrying the typed
    quantity, and no escalation. One candidate reads "Couldn't find ELP3753. Did you
    mean ELP3754?" and a "yes" answers ELP3754 (`apply._answer_pending`); a "no" refers
    the dealer to their salesman."""
    options: list[dict[str, Any]] = []
    for row in candidates or []:
        if not isinstance(row, dict):
            continue
        code = row.get("product") or row.get("value") or row.get("label")
        if not code or not row.get("uuid"):
            continue
        if row.get("entity_type") not in (None, "product"):
            continue
        options.append(
            {
                "position": len(options) + 1,
                "label": str(code),
                "code": str(code),
                "uuid": str(row["uuid"]),
                "entity_type": "product",
            }
        )
    options = options[:MAX_SLOTS]
    if not options:
        return None
    shown = (typed or "").strip().upper() or "that"
    labels = [o["label"] for o in options]
    if len(labels) == 1:
        text = f"Couldn't find {shown}. Did you mean {labels[0]}?"
    else:
        # PICKER-NO-CAP (owner, 2 Oct 2026): every option the pick carries is printed.
        text = "\n".join([f"Couldn't find {shown}. Did you mean:", *numbered(labels)])
    pick = ask(
        "product_pick",
        options,
        asked_at_turn=asked_at_turn,
        payload={
            "domain": "inventory",
            "domains": ["inventory"],
            "stock_pick": True,
            "did_you_mean": True,
            "typed": shown,
            "count": len(labels),
            "stock_qty": quantity,
        },
    )
    return text, pick
