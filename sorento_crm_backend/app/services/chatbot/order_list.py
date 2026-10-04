# The order list conversation: a delivery order, sales order or outstanding list the
# customer is reading, and the words that move it (#1262 fix lane round 7, the owner's
# hand test of round 6, 27 Sep 2026).
#
# Two seams, both in `engine._run_stages`:
#
# * `order_list_verdict` reads the message against the open list BEFORE the verdict is
#   applied, the way the top selling lane reads a message against its ranking: a brand
#   word alone switches the brand and re-runs the list (R3), the clear words drop the
#   brand and re-run it (R5), and a fresh order ask that names its own subject but no
#   brand starts with all brands (R4). The parser's own reading of these messages is
#   not stable (v40 read "how aobut mocha" as a request for help), so the words decide.
# * `list_reply` takes the escalate offer and the routing picker out of a reply inside
#   the list that answered (R6). An empty result is a no-answer and keeps its offer
#   (owner ruling 4 Oct 2026, PICKER-ESCALATION). The escalation lane stays one message
#   away: a request for a person never reaches a business reply at all.
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from app.services.chatbot import jsc
from app.services.chatbot.dealer_stock import refers_to_salesman
from app.services.chatbot.turn.pending import ESCALATION_OFFER_KINDS

_WORD_RE = re.compile(r"[0-9a-z]+")

#: Statuses with a lane of their own, never an order list.
_NOT_A_LIST = frozenset({"sales_report", "sales_analysis", "top_selling"})

#: Words that may sit beside a brand word in "how about mocha", "mocha only?",
#: "then sorento brand". Matched allowing one slip in a word of four letters or more
#: ("aobut").
_SWITCH_FILLER = frozenset(
    {
        "how", "about", "what", "and", "or", "then", "only", "instead", "brand", "brands",
        "for", "the", "ok", "okay", "pls", "please", "now", "so", "show", "me", "try",
        "with", "in", "just", "check", "see", "same", "but", "also",
    }
)

_CLEAR_VERBS = frozenset({"all", "any", "every", "both", "clear", "remove", "no", "drop", "reset", "without"})
_BRAND_NOUNS = frozenset({"brand", "brands"})
_CLEAR_FILLER = frozenset({"the", "filter", "pls", "please", "ok", "okay", "show", "just", "a", "any"})

def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").casefold())


def _is_filler(word: str) -> bool:
    from app.services.chatbot.turn_runtime import _osa_distance

    if word in _SWITCH_FILLER:
        return True
    return len(word) >= 4 and any(len(f) >= 4 and _osa_distance(word, f) <= 1 for f in _SWITCH_FILLER)


def is_open_order_list(focus: Any) -> bool:
    """Is the conversation on an order list (a DO, SO or outstanding list)?"""
    domains = list(getattr(focus, "domains", None) or [])
    return "order" in domains and getattr(focus, "status", None) not in _NOT_A_LIST


def clears_the_brand(text: str) -> bool:
    """"all brand", "all brands", "any brand", "clear the brand", "remove the brand",
    "no brand" and the like: a brand noun, a clearing word, nothing else."""
    words = _words(text)
    if not words or len(words) > 5:
        return False
    return (
        bool(set(words) & _BRAND_NOUNS)
        and bool(set(words) & _CLEAR_VERBS)
        and all(w in _BRAND_NOUNS | _CLEAR_VERBS | _CLEAR_FILLER for w in words)
    )


def brand_word_alone(db: Session, text: str) -> str | None:
    """The brand word of a message that is a brand word and filler only ("how aobut
    mocha", "mocha?", "sorento"), read against the live brands the same way the brand
    filter reads it (`turn_runtime.brand_rows_for_word`). None for anything else."""
    from app.services.chatbot.turn_runtime import active_brands, brand_rows_for_word

    words = _words(text)
    if not words or len(words) > 5:
        return None
    others = [w for w in words if not _is_filler(w)]
    if len(others) != 1:
        return None
    word = others[0]
    try:
        brands = active_brands(db)
    except Exception:  # noqa: BLE001 - no brand list means no brand word, never a failure
        return None
    return word if brand_rows_for_word(brands, word) else None


def _continuation(verdict: dict[str, Any], *, entities: list[dict[str, Any]]) -> dict[str, Any]:
    """The verdict of a bare continuation of the open list: an order read with no domain
    word of its own, so the document, status, customer and dates carry (round 5 R2), and
    nothing of the parser's escalation reading left on it."""
    escalation = verdict.get("escalation") if isinstance(verdict.get("escalation"), dict) else {}
    return {
        **verdict,
        "message_type": "business_query",
        "intent_hint": "check_order",
        "domain_hint": "order",
        "domain_in_message": False,
        "entities": entities,
        "escalation": {**escalation, "is_escalation_confirmation": False, "escalation_declined": False},
        "person_mention": None,
        "reference_positions": [],
        "reference_target": None,
        "is_affirmative": None,
        "scope_intent": "specific",
        "broaden_axis": None,
        "correction": False,
        "topic_reset": False,
        "document": None,
        "status": None,
        "order_status": None,
        "date_mode": None,
        "date_filter_start": None,
        "date_filter_end": None,
    }


def _names_own_subject(verdict: dict[str, Any]) -> bool:
    return any(
        isinstance(e, dict)
        and e.get("current_message") is not False
        and jsc.js_string(e.get("hint") or "").strip().lower() in ("customer", "product")
        for e in (verdict.get("entities") or [])
    )


def _names_a_brand(verdict: dict[str, Any]) -> bool:
    return any(
        isinstance(e, dict) and jsc.js_string(e.get("hint") or "").strip().lower() == "brand"
        for e in (verdict.get("entities") or [])
    )


def order_list_verdict(db: Session, verdict: dict[str, Any], state: Any, text: str) -> tuple[dict[str, Any], Any, str | None]:
    """The verdict and state to apply for this message, and the rule that fired.

    * R5 the clear words over an open list: a continuation with the brand dropped.
    * R3 a brand word alone over an open list: a continuation filtered by that brand.
    * R4 an order ask that names its own subject and no brand: all brands, whatever
      the conversation carried. A bare continuation ("and hanlim?", a number, "what
      about August") names no domain word and keeps the brand.
    """
    focus = state.focus
    pending = state.pending
    if pending is not None and getattr(pending, "kind", None) in ESCALATION_OFFER_KINDS:
        open_pending = None
    else:
        open_pending = pending
    if is_open_order_list(focus):
        if clears_the_brand(text):
            state = replace(state, focus=replace(focus, outstanding_brand_ids=[]), pending=open_pending)
            return _continuation(verdict, entities=[]), state, "order_list_brand_cleared"
        # A word the parser read as answering the bot's own escalate offer answers it.
        answers_offer = open_pending is not pending and (verdict.get("escalation") or {}).get(
            "is_escalation_confirmation"
        ) is True
        word = None if answers_offer else brand_word_alone(db, text)
        if word is not None:
            entity = {"raw": word, "hint": "brand", "canonical_code": None, "current_message": True, "confident": True}
            return _continuation(verdict, entities=[entity]), replace(state, pending=open_pending), "order_list_brand_switched"
    if (
        focus.outstanding_brand_ids
        and jsc.js_string(verdict.get("domain_hint") or "").strip() == "order"
        and verdict.get("domain_in_message") is True
        and _names_own_subject(verdict)
        and not _names_a_brand(verdict)
    ):
        return verdict, replace(state, focus=replace(focus, outstanding_brand_ids=[])), "order_list_fresh_ask_all_brands"
    return verdict, state, None


def list_reply(answer: Any, *, was_open: bool, fetch_plan: Any, envelopes: list[dict[str, Any]], order_status: Any) -> Any:
    """R6: a reply inside an order list that was already open (`was_open`, read before
    this turn applied) carries no escalate offer and no routing picker. A first ask is
    answered as before.

    An empty list is left as composed (owner ruling 4 Oct 2026, PICKER-ESCALATION: "any
    no answer should get the escalation question, that's the gist"): a contact who may
    escalate is offered a person, a barred one keeps "Please refer to your salesman."
    R6's 27 Sep one-line empty list dropped both, after a customer picker too, since the
    picker turn had already put the order domain in focus."""
    fetch = list(getattr(fetch_plan, "fetch", None) or [])
    if not was_open or len(fetch) != 1 or fetch[0].domain != "order" or not envelopes:
        return answer
    if jsc.js_string(order_status or "").strip() in _NOT_A_LIST:
        return answer
    from app.services.chatbot.turn.fetch import envelope_missed

    envelope = envelopes[0]
    if envelope_missed(envelope) and not envelope.get("denied"):
        return answer
    text, _had = refers_to_salesman(getattr(answer, "text", "") or "")
    question = answer.question
    dropped_options: list[dict[str, Any]] | None = None
    if question is not None:
        if question.kind in ESCALATION_OFFER_KINDS:
            dropped_options = [o for o in (question.options or []) if isinstance(o, dict)]
            question = None
        elif (question.payload or {}).get("escalate_offered") is True:
            question = replace(question, payload={**question.payload, "escalate_offered": False})
    if dropped_options is not None:
        companies = [
            str(row.get("company_name"))
            for row in ((answer.question.payload or {}).get("roster_plan") or [])
            if isinstance(row, dict) and row.get("company_name")
        ]
        text = _without_picker(text, dropped_options, companies=companies)
    if text == answer.text and question is answer.question and answer.offer is None:
        return answer
    return replace(answer, text=text, question=question, offer=None)


#: The escalate offer sentence the miss composer and the silent-company offer print
#: (`lanes/business/answer.py::what_you_want_reply`, `answer_bridge._silent_company_offer`),
#: at the end of a line or alone on it.
_OFFER_SENTENCE = re.compile(r"\s*Would you like me to escalate to .+? team\?\s*$")
#: The multi-company picker's own lines (`tail/member_offer.build_cs_member_offer`,
#: `tail/reply_ladder`): a "[ Company: no customer-service members are configured ...
#: omitted. ]" note and a row's "(Company / Company)" tail. Its `*Company:*` group header
#: is matched by the company names the dropped question's `roster_plan` carries, never by
#: shape: a field whose value is empty renders as `*Label:*` too (`lanes/business/fetch.py`).
_NO_MEMBERS_NOTE = re.compile(r"^\[ .+ omitted\.? \]$")
_COMPANIES_TAIL = re.compile(r"\s*\([^()]*\)$")
#: A multi-company picker's `*Company:*` group header (`build_cs_member_offer`), read
#: only inside a picker block, where no business field can stand.
_GROUP_HEADER = re.compile(r"^\*[^*]+:\*$")


def _without_companies(row_text: str) -> str:
    return _COMPANIES_TAIL.sub("", row_text).strip()


def _picker_frame_lines() -> tuple[frozenset[str], tuple[str, ...]]:
    """The whole lines a routing picker prints besides its rows, and the opening words
    of the one whose tail names the companies - the same strings the printing sites
    use (`tail/member_offer.py`), never a second copy."""
    from app.services.chatbot.tail import member_offer as member_mod

    return (
        frozenset(
            {member_mod.PICKER_HEADER, member_mod.PICKER_CLOSE, member_mod.ROSTER_HEADER, member_mod.ROSTER_CLOSE}
        ),
        (member_mod.PICKER_MULTI_CLOSE_PREFIX,),
    )


def _picker_headers() -> frozenset[str]:
    """The lines a picker's member rows sit directly under (`tail/member_offer.py`)."""
    from app.services.chatbot.tail import member_offer as member_mod

    return frozenset({member_mod.PICKER_HEADER, member_mod.ROSTER_HEADER})


def _without_picker(text: str, options: list[dict[str, Any]], *, companies: list[str] = ()) -> str:
    """`text` without the routing picker whose question was taken out: its numbered rows,
    its header and close, and the escalate offer sentence the picker hangs off.

    CHATBOT-EMPTY-ROUTE-PICK (owner console, 30 Sep 2026): only the rows went, so the
    customer read "Please choose who to route to (reply with the number):", nothing, and
    "If you have no preference, just reply 'yes' ..." over a list that was not there -
    and a "yes" would have answered a question that no longer existed. Nothing here
    prints; the strings are the printing sites' own.
    """
    labels = {
        (str(o.get("position")), jsc.js_string(o.get("label") or "").strip())
        for o in options
        if jsc.js_string(o.get("label") or "").strip()
    }
    whole, prefixes = _picker_frame_lines()
    headers = _picker_headers()
    group_headers = {f"*{name}:*" for name in companies} | ({"*Other:*"} if companies else set())
    kept: list[str] = []
    removed = False  # was the line before this one taken out (a blank next to it goes too)
    # REFER-ONLY-FIXES (owner console, 3 Oct 2026): every line under a picker header up to
    # the next blank is the picker's own (a member row, a `*Company:*` group header, a
    # no-members note), whatever question is still attached. Matching rows by the
    # question's labels alone left the staff names under the refer line when an upstream
    # site had already dropped that question (`answer_bridge.answer_for`'s barred arm).
    in_picker = False
    for line in (text or "").splitlines():
        bare = line.strip()
        if bare in headers:
            in_picker = True
        elif not bare:
            in_picker = False
        m = re.match(r"^\s*(\d+)[.)]\s*(.+?)\s*$", line)
        if (
            bare in whole
            or bare.startswith(prefixes)
            or bare in group_headers
            or (options and _NO_MEMBERS_NOTE.match(bare))
            or (in_picker and bare and (m or _GROUP_HEADER.match(bare) or _NO_MEMBERS_NOTE.match(bare)))
            or (m and ((m.group(1), m.group(2)) in labels or (m.group(1), _without_companies(m.group(2))) in labels))
        ):
            removed = True
            continue
        stripped = _OFFER_SENTENCE.sub("", line)
        if stripped != line:
            removed = True
            if not stripped.strip():
                continue
            line = stripped
        if not line.strip() and (removed or not kept or not kept[-1].strip()):
            # A blank beside a removed line, a blank on top, or a second blank in a row.
            continue
        removed = False
        kept.append(line.rstrip())
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(kept)
