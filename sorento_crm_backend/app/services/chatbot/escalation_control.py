# ESCALATION-CONTROL (owner, 30 Sep 2026): "we need to be able to control each contact
# that they cannot access the escalation: cannot force escalate, won't be offered
# escalation; this is for dealer".
#
# A barred contact (`turn.state.escalation_barred`: the contact page's switch unticked,
# `respond_contacts.escalation_allowed`) is never offered a hand-off and cannot force one. The offer
# sites read `turn.state.offers_escalation` at source; this module is the backstop the
# engine runs on every composed reply (`strip_offers`), and the reply the forced path
# gives instead of a hand-off (`barred_reply`).
#
# Pure: no I/O.
from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.services.chatbot.dealer_stock import refers_to_salesman
from app.services.chatbot.turn import refer
from app.services.chatbot.turn.pending import without_escalation as strip_pending


def barred_reply(profile: Any = None) -> str:
    """The reply a barred contact gets where a hand-off (or an offer of one) would be.

    Owner ruling, 30 Sep 2026: the dealer line the stock ask already prints,
    `turn/task.py::REFER_TO_SALESMAN` ("Please refer to your salesman."), never a
    second wording and never the salesperson's name (REFER-SALESMAN AC-RS02). Printed
    through `turn/refer.py`, so the turn is marked for Customer asks."""
    return refer.sentence()


def strip_text(text: str, question: Any, profile: Any) -> tuple[str, Any, bool]:
    """`(text, question, offered)`: the reply with no escalation offer in it - the offer
    sentence, a routing picker and an armed escalation question all go, and the barred
    reply stands where the offer was. `offered` says whether anything was taken out."""
    from app.services.chatbot.order_list import _picker_frame_lines, _without_picker

    text = text or ""
    stripped, had_sentence = refers_to_salesman(text)
    kept_question, dropped = strip_pending(question)
    whole, prefixes = _picker_frame_lines()
    has_frame = any(
        line.strip() in whole or line.strip().startswith(prefixes) for line in stripped.splitlines()
    )
    if dropped or has_frame:
        companies = [
            str(row.get("company_name"))
            for row in ((getattr(question, "payload", None) or {}).get("roster_plan") or [])
            if isinstance(row, dict) and row.get("company_name")
        ] if dropped else []
        stripped = _without_picker(stripped, dropped or [], companies=companies)
    offered = had_sentence or has_frame or bool(dropped) or kept_question is not question
    if not offered:
        return text, question, False
    referral = barred_reply(profile)
    body = stripped.strip()
    if referral not in body:
        body = f"{body}\n\n{referral}" if body else referral
    return body, kept_question, True


def strip_offers(answer: Any, profile: Any) -> Any:
    """`strip_text` over a composed `Answer`, whose `offer` goes too."""
    text, question, offered = strip_text(
        getattr(answer, "text", "") or "", getattr(answer, "question", None), profile
    )
    if getattr(answer, "offer", None) is not None:
        offered = True
        if text == (getattr(answer, "text", "") or "") and barred_reply(profile) not in text:
            body = text.strip()
            text = f"{body}\n\n{barred_reply(profile)}" if body else barred_reply(profile)
    if not offered:
        return answer
    return replace(answer, text=text, question=question, offer=None)
