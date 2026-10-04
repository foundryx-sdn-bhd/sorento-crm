# The open question: every question the bot asks, as ONE object the parser answers
# (PR #1247 round 9, issue #1293).
#
# Owner, 26 Sep 2026 ~14:15Z: "I'm also curious in your methodology in making this work
# because I want to make sure this is as human and as natural as possible and not too
# much hard coding, hard routing." The rule this module serves: the parser READS, the
# code APPLIES. Whatever the bot last asked (a pick, a yes/no, the quantities) is stated
# to the parser as one object with its kind, its options and what is still owed; the
# parser returns one declared answer (`open_question_answer`); `turn/apply.py` acts on
# that answer and falls back to shape rules only when it is absent.
#
# A VIEW, not new state: the object is built from what the turn already stores (the
# pending pick or offer, else the stock task), so nothing here can disagree with it.
# Pure: no I/O, no word of the customer's message is ever read here.
from __future__ import annotations

from typing import Any

from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.pending import Pending

PICK_ONE = "pick_one"
CHOOSE_BRAND = "choose_brand"
CONFIRM = "confirm"
QUANTITIES = task_mod.QUANTITIES
LAST_ANSWER = task_mod.LAST_ANSWER
#: Declared so the prompt and the code share one list, and never written: the owner's
#: no-paging, full-counts ruling means the bot never asks how many to show. The first
#: question of that kind is the trigger to build it.
HOW_MANY_TO_SHOW = "how_many_to_show"
FREE = "free"

KINDS: tuple[str, ...] = (
    PICK_ONE,
    CHOOSE_BRAND,
    CONFIRM,
    QUANTITIES,
    LAST_ANSWER,
    HOW_MANY_TO_SHOW,
    FREE,
)


def _options(pending: Pending) -> list[dict[str, Any]]:
    """The options as the parser needs them: the position, the code, and the printed
    label only when it is not the code (a customer's name beside its account code)."""
    out: list[dict[str, Any]] = []
    for option in pending.options:
        position = option.get("position")
        if not isinstance(position, int) or isinstance(position, bool):
            continue
        label = option.get("label")
        code = option.get("code") or label
        row: dict[str, Any] = {"position": position, "code": code}
        if label and label != code:
            row["label"] = label
        out.append(row)
    return out


def _kind(pending: Pending, options: list[dict[str, Any]]) -> str:
    if not options:
        return FREE
    payload = pending.payload or {}
    if pending.expects == "yes_no" or (payload.get("stock_pick") and len(options) == 1):
        # "Couldn't find ELP3753. Did you mean ELP3754?" and a one-team escalation offer
        # are answered by a yes or a no, never by a number.
        return CONFIRM
    if pending.kind == "brand_pick":
        return CHOOSE_BRAND
    return PICK_ONE


def of_pending(pending: Pending | None) -> dict[str, Any] | None:
    """The open pick or offer as the object, or None when nothing is open."""
    if pending is None:
        return None
    options = _options(pending)
    kind = _kind(pending, options)
    payload = pending.payload or {}
    stock = bool(payload.get("stock_pick"))
    qty = task_mod._number(payload.get("stock_qty")) if stock else None
    if kind == FREE:
        owed = ["answer"]
    elif kind == CONFIRM:
        owed = ["yes_no"]
    else:
        # A stock pick needs a quantity too; one given already rides on the pick.
        owed = ["pick", "quantity"] if stock and qty is None else ["pick"]
    obj: dict[str, Any] = {"kind": kind, "options": options, "owed": owed}
    if qty is not None:
        obj["qty"] = qty
    return obj


def of_required_ask(slot: Any) -> dict[str, Any] | None:
    """A `required_fields` question left open (the low stock report's category, supplier
    or grouping) as the object, so the PARSER reads whether the next message answers it
    (LOWSTOCK-SEMANTIC, crew ruling Q4): `about` names the ask, `owed` the field, and the
    numbered options ride as a `pick_one`. The question's own words come from the ask's
    registered field. None when nothing is open."""
    from app.services.chatbot import required_fields as rf

    if not isinstance(slot, dict) or not slot.get("asking"):
        return None
    ask = rf.ASKS.get(str(slot.get("ask") or ""))
    field = next((f for f in (ask.fields if ask else ()) if f.name == slot.get("asking")), None)
    if field is None:
        return None
    options = [
        {"position": i, "code": str(o[1])}
        for i, o in enumerate(slot.get("options") or [], start=1)
        if isinstance(o, (list, tuple)) and len(o) == 2
    ]
    obj: dict[str, Any] = {
        "kind": PICK_ONE if options else FREE,
        "about": ask.name,
        "question": field.question,
        "owed": [field.name],
    }
    if options:
        obj["options"] = options
    return obj


def open_question(pending: Pending | None, tasks: Any, required_ask: Any = None) -> dict[str, Any] | None:
    """The ONE question on the table: a required field the LAST reply asked for (the slot
    lives one turn, so it is always the newest question, newer than a carried roster pick
    or a stock task still in the focus), else the open pick or offer, else the stock
    question (`task.open_question`), else None."""
    obj = of_required_ask(required_ask)
    if obj is not None:
        return obj
    obj = of_pending(pending)
    if obj is not None:
        return obj
    return task_mod.open_question(tasks)


#: The parser's answer when there is no `Open question:` line, as the prompt states it
#: ("mode null, picked [], items [], qty_for_all null when there is no Open question
#: line or the message does not answer it").
NO_ANSWER: dict[str, Any] = {"mode": None, "picked": [], "items": [], "qty_for_all": None}

ANSWER_KEY = "open_question_answer"
POSITIONS_KEY = "reference_positions"


def without_phantom_answer(
    verdict: dict[str, Any],
    *,
    open_question: dict[str, Any] | None,
    positions_read_elsewhere: bool,
    focus_has_product: bool,
) -> tuple[dict[str, Any], bool]:
    """The verdict with a declared answer to a question that was never asked taken out.

    CHATBOT-EMPTY-ROUTE-PICK (owner console, 30 Sep 2026, parser v37): "Zhin heng
    delivered on 23/9" arrived with `open_question_answer {"mode": "pick", "picked":
    [1]}` while the previous bot turn was a data answer and nothing was open. The
    parser answers the `Open question:` object this module builds (`open_question`);
    when that object is None no line was shown, so a non-null answer answers nothing
    that exists, and the positions that ride with a pick index nothing either. Both
    go, here, before any reader: `apply._open_pick_answer` and `decide.picked_positions`
    already guard on `state.pending`, but `gate.py`'s `pick_applied` (a non-empty
    `reference_positions` suppresses "Which customer do you mean?") and the stock and
    top-selling position readers do not all share that guard.

    `positions_read_elsewhere`: two questions are not `Open question:` objects yet are
    answered by a position - the ideation lane's media menu (`ideation.pending_media`,
    read by `lanes/ideate.py::build_arguments` as `reference_positions`) and the top
    selling questions ("How many?", "By quantity or by amount?", "Customer or sales
    agent?", `focus.top_selling.asked`, read by `turn/apply.py::record_top_selling_asked`
    and its `top_selling_position_is_*` rules, so "the first one" answers them). While
    one of those is outstanding the positions stay and only the declared answer goes.

    `focus_has_product`: a bare number with nothing open and a product in focus is
    "that product again" (owner ruling, round 4 hand test: "1" after the escalation
    of MWC-SC8609-PP went out is the product answered again, `business_query`). The
    positions are the one signal that keeps a casual-typed "1" out of idle chat
    (`turn/apply.py::_IDLE_CHAT_DISQUALIFIERS`), so with a product in focus they stay
    unless the parser ALSO declared a pick - a declared pick with nothing open is the
    v37 phantom above, and its positions index nothing. With no product in focus a
    bare number answers nothing either way and the positions go.
    Returns `(verdict, dropped)`; the same object, untouched, when there is nothing to
    drop.
    """
    if open_question is not None:
        return verdict, False
    answer = verdict.get(ANSWER_KEY)
    declared = isinstance(answer, dict) and answer.get("mode") is not None
    raw = verdict.get(POSITIONS_KEY)
    positions = (
        bool(raw)
        and isinstance(raw, list)
        and not positions_read_elsewhere
        and (declared or not focus_has_product)
    )
    if not declared and not positions:
        return verdict, False
    out = dict(verdict)
    if declared:
        out[ANSWER_KEY] = dict(NO_ANSWER)
    if positions:
        out[POSITIONS_KEY] = []
    return out, True


def is_the_stock_question(obj: dict[str, Any] | None) -> bool:
    """Does this object state the stock task (so its `Open task:` hint is redundant)?"""
    return bool(obj) and obj.get("kind") in (QUANTITIES, LAST_ANSWER)
