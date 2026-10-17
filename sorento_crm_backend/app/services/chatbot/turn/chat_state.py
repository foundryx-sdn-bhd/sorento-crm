# CHAT-STATE: the one explicit conversation state the parser is shown and answers.
#
# `view` / `render` state the focus, the open question, the last list and the pending task
# as a fixed four line block. `apply_changes` validates the parser's `state_changes` and
# carries the prior state accordingly (CARRY only: what this message names is still
# written by `apply._focus_rules`). Pure: no I/O, no word of the customer's message is
# ever read here; an op carries no value, the value is the parse's own existing field.
from __future__ import annotations

import copy
import json
from datetime import date
from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID

from app.services.chatbot.turn import question as question_mod
from app.services.chatbot.turn import task as task_mod
from app.services.chatbot.turn.pending import ESCALATION_OFFER_KINDS
from app.services.chatbot.turn.pending import ask as pending_ask
from app.services.chatbot.turn.pending import option_entities
from app.services.chatbot.turn.state import Focus, State

OPS: tuple[str, ...] = (
    "keep", "new_subject", "set", "clear", "pick", "answer", "switch_domain", "close_question",
)

#: The fixed axes; every `policy.kinds` kind is an axis too.
FIXED_AXES: tuple[str, ...] = (
    "product", "customer", "warehouse", "brand", "tier", "channel", "period", "status", "document", "sort", "quantity",
)

#: Subject keys the State block prints that only a `clear` can act on.
CLEAR_ONLY_AXES: tuple[str, ...] = ("domain", "report", "relation")

#: Owner ruling 16 Oct 2026 ~23:00 (R16): a follow-up keeps the held period; a new subject keeps
#: it too unless the parser says `skip` / `clear` / `set` for the period. The one spot.
NEW_SUBJECT_KEEPS_PERIOD = True

#: R15: what an `unclear` held slot does when the held entity's type cannot settle it. "ask"
#: raises one real-name question; "assume" applies the slot. Storage is undecided (owner), so
#: the default sits behind one function.
UNCLEAR_SLOT_DEFAULT = "ask"

#: R15: the held entity's registry kind settles an unclear customer slot: a customer (debtor)
#: means delivery. A supplier kind does not exist in `chatbot_entity_kinds` today.
KIND_DOMAIN: dict[str, str] = {"customer": "order"}

#: The slots the parser states per message.
SLOT_AXES: tuple[str, ...] = ("customer", "period")
SLOT_OPS: tuple[str, ...] = ("keep", "skip", "clear", "unclear")

#: The question this module raises for an unclear period (a Pending kind of its own).
PERIOD_ASK = "period_ask"
CUSTOMER_ASK = "held_customer_ask"
_SLOT_ASKS = (PERIOD_ASK, CUSTOMER_ASK)


def unclear_slot_default() -> str:
    return UNCLEAR_SLOT_DEFAULT

#: Axes tied to the domain they were asked in; a domain switch drops them.
_DOMAIN_BOUND = (
    "status", "document", "sort", "sales_channel", "top_selling", "sales_ranking_frame", "low_stock", "set_page",
)

#: Keys that are machine identity, never printed.
_HIDDEN_KEYS = frozenset({"uuid", "uuids", "id", "key", "payload"})



# --------------------------------------------------------------------------- #
# view / render
# --------------------------------------------------------------------------- #


def _is_key(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        UUID(value.strip())
    except ValueError:
        return False
    return True


def _clean(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return None if not text or _is_key(text) else text


def _plain(value: Any) -> Any:
    """A copy with machine identity (keys, uuid-shaped strings) taken out."""
    if isinstance(value, dict):
        return {
            k: _plain(v) for k, v in value.items()
            if k not in _HIDDEN_KEYS and not _is_key(v) and _plain(v) not in (None, "", [], {})
        }
    if isinstance(value, (list, tuple)):
        return [p for p in (_plain(v) for v in value if not _is_key(v)) if p not in (None, "", [], {})]
    return value


def _names(rows: Any, *order: str) -> list[str]:
    out: list[str] = []
    for row in rows or ():
        if isinstance(row, dict):
            text = next((t for t in (_clean(row.get(k)) for k in order) if t), None)
        else:
            text = _clean(row)
        if text and text not in out:
            out.append(text)
    return out


def _products(rows: Any) -> list[str]:
    out: list[str] = []
    for row in rows or ():
        name = next(iter(_names([row], "canonical_code", "raw", "name")), None)
        if not name:
            continue
        qty = row.get("quantity") if isinstance(row, dict) else None
        out.append(f"{name} x{qty:g}" if isinstance(qty, (int, float)) and not isinstance(qty, bool) else name)
    return out


def _pairs(slot: Any) -> str | None:
    """A report slot as `key value` pairs, scalars only, machine identity left out."""
    if not isinstance(slot, dict):
        return None
    parts = []
    for key, value in _plain(slot).items():
        if isinstance(value, (list, tuple)):
            value = ",".join(str(v) for v in value if isinstance(v, (str, int, float)))
        if isinstance(value, (str, int, float, bool)) and value != "":
            parts.append(f"{key} {value}")
    return ", ".join(parts) or None


def _subject(focus: Focus) -> dict[str, Any]:
    out: dict[str, Any] = {
        "domain": _names([{"d": d} for d in focus.domains or ()], "d"),
        "customer": _names(focus.customers, "name", "raw", "canonical_code"),
        "product": _products(focus.products),
        "warehouse": _names(focus.warehouse, "name", "raw", "canonical_code"),
        "brand": _names(focus.brands),
        "tier": _names(focus.tier),
        "channel": _clean(focus.sales_channel),
        "period": [
            t for t in (_clean((focus.date_window or {}).get(k)) for k in ("start", "end")) if t
        ] if isinstance(focus.date_window, dict) else [],
        "status": _clean(focus.status),
        "document": _names(focus.document),
        "sort": [
            t for t in (_clean((focus.sort or {}).get(k)) for k in ("by", "dir")) if t
        ] if isinstance(focus.sort, dict) else [],
        "report": [
            f"{label} {pairs}".strip()
            for label, pairs in (
                ("top_selling", _pairs(focus.top_selling)),
                ("sales_ranking_frame", _pairs(focus.sales_ranking_frame)),
                ("low_stock", _pairs(focus.low_stock)),
            )
            if pairs or (label == "top_selling" and focus.status == "top_selling")
        ],
        "relation": _clean(focus.relation),
    }
    for kind, rows in sorted((focus.extra or {}).items()):
        names = _names(rows, "name", "raw", "canonical_code")
        if names and kind not in out:
            out[kind] = names
    return {k: v for k, v in out.items() if v}


def _list_item(row: dict[str, Any]) -> dict[str, Any] | None:
    position = row.get("position")
    if not isinstance(position, int) or isinstance(position, bool):
        return None
    item: dict[str, Any] = {"position": position}
    code, label = _clean(row.get("code")), _clean(row.get("label"))
    if code:
        item["code"] = code
    if label and label != code:
        item["label"] = label
    return item


def _same_list(items: list[dict[str, Any]], oq: dict[str, Any] | None) -> bool:
    options = (oq or {}).get("options")
    if not options:
        return False
    def key(row: dict[str, Any]) -> tuple[Any, Any]:
        return row.get("position"), row.get("code") or row.get("label")
    return {key(i) for i in items} == {key(o) for o in options if isinstance(o, dict)}


def _open_question(focus: Focus, oq: dict[str, Any] | None, pending: Any = None) -> dict[str, Any] | None:
    obj = _plain(oq) if oq else {}
    if obj and pending is not None and getattr(pending, "kind", None) in ESCALATION_OFFER_KINDS:
        # A hand-over offer states its kind and the team it would go to (a name, never an id),
        # so a yes or a no can be read as accepting or declining it.
        obj["offer"] = pending.kind
        team = _clean(getattr(pending, "team", None))
        if team:
            obj["hand_over_to"] = team.replace("_", " ")
    extras: dict[str, Any] = {}
    if focus.set_clarify:
        extras["clarify"] = _plain(focus.set_clarify)
    if isinstance(focus.set_page, dict) and focus.set_page.get("other_brands"):
        extras["other_brands"] = _plain(focus.set_page["other_brands"])
    asked = (focus.top_selling or {}).get("asked") if isinstance(focus.top_selling, dict) else None
    if asked:
        extras["asked"] = _plain(asked)
    extras = {k: v for k, v in extras.items() if v}
    if not obj and not extras:
        return None
    if not obj:
        obj = {"kind": question_mod.FREE, "owed": ["answer"]}
    return {**obj, **extras}


def view(state: State, open_question: dict[str, Any] | None) -> dict[str, Any]:
    """The state as one plain, key-free dict: what the parser is shown and what the trace logs."""
    focus = state.focus
    oq = _open_question(focus, open_question, state.pending)
    items = [i for i in (_list_item(r) for r in (focus.last_list or []) if isinstance(r, dict)) if i]
    stock_is_question = question_mod.is_the_stock_question(open_question)
    task = None if stock_is_question else task_mod.open_question(focus.tasks)
    return {
        "subject": _subject(focus),
        "open_question": oq,
        "last_list": items or None,
        "last_list_is_question": bool(items) and _same_list(items, oq),
        "task": _plain(task) if task else None,
        "task_is_question": stock_is_question,
    }


def _list_text(items: list[dict[str, Any]]) -> str:
    return "; ".join(
        " ".join(str(p) for p in (i["position"], i.get("code"), i.get("label")) if p is not None)
        for i in items
    )


def _subject_text(subject: dict[str, Any]) -> str:
    parts = []
    for key, value in subject.items():
        if key == "period" and value:
            value = [" to ".join(value)]
        elif key == "sort":
            value = [" ".join(value)]
        parts.append(f"{key} {', '.join(value) if isinstance(value, list) else value}")
    return "; ".join(parts)


def render(v: dict[str, Any]) -> str | None:
    """The fixed four line block, or None when the state is empty (nothing is added)."""
    if not (v["subject"] or v["open_question"] or v["last_list"] or v["task"] or v["task_is_question"]):
        return None
    subject = f"- subject: {_subject_text(v['subject'])}" if v["subject"] else "- subject: none"
    oq = v["open_question"]
    question = "- open question: " + (json.dumps(oq, ensure_ascii=False) if oq else "none")
    items = v["last_list"]
    if not items:
        last = "- last list: none"
    else:
        domain = ", ".join(v["subject"].get("domain") or [])
        head = f"- last list ({domain + ', ' if domain else ''}{len(items)}): "
        last = head + ("the open question's options" if v["last_list_is_question"] else _list_text(items))
    if v["task_is_question"]:
        task = "- pending task: the open question"
    elif v["task"]:
        task = "- pending task: " + json.dumps(v["task"], ensure_ascii=False)
    else:
        task = "- pending task: none"
    return "\n".join(["State:", subject, question, last, task])


# --------------------------------------------------------------------------- #
# apply_changes
# --------------------------------------------------------------------------- #


@dataclass
class Applied:
    valid: bool
    state: State
    verdict: dict[str, Any]
    log: dict[str, Any]


def _entities(verdict: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in (verdict.get("entities") or []) if isinstance(e, dict)]


def _named_kinds(verdict: dict[str, Any]) -> set[str]:
    return {
        str(e.get("hint")) for e in _entities(verdict)
        if e.get("hint") and e.get("current_message") is not False
    }


def _has_field(verdict: dict[str, Any], axis: str) -> bool:
    """Does this message's own parse carry a value for the axis (one value, one place)?"""
    if axis == "tier":
        return bool(verdict.get("access_levels")) or "tier" in _named_kinds(verdict)
    if axis == "channel":
        return bool(verdict.get("sales_channel"))
    if axis == "period":
        return any(verdict.get(k) for k in ("date_mode", "date_filter_start", "date_filter_end"))
    if axis == "status":
        return any(verdict.get(k) for k in ("status", "order_status"))
    if axis == "document":
        return bool(verdict.get("document"))
    if axis == "sort":
        return bool(verdict.get("sort_by") or verdict.get("sort_dir"))
    if axis == "quantity":
        return verdict.get("demand_qty") is not None or any(e.get("quantity") is not None for e in _entities(verdict))
    return axis in _named_kinds(verdict)


def _message_domains(verdict: dict[str, Any]) -> list[str]:
    asks = [a.get("domain") for a in (verdict.get("asks") or []) if isinstance(a, dict) and a.get("domain")]
    if asks:
        return asks
    return [verdict["domain_hint"]] if verdict.get("domain_hint") else []


def _clear_axis(focus: Focus, axis: str) -> None:
    if axis == "channel":
        focus.sales_channel = None
    elif axis == "period":
        focus.date_window = None
    elif axis == "status":
        focus.status = None
    elif axis == "document":
        focus.document = []
    elif axis == "domain":
        focus.domains = []
    elif axis == "report":
        focus.top_selling = focus.sales_ranking_frame = focus.low_stock = None
    elif axis == "relation":
        focus.relation = None
    elif axis == "sort":
        focus.sort = None
    elif axis == "tier":
        focus.tier = []
    elif axis == "brand":
        focus.brands = []
    elif axis == "quantity":
        for row in focus.products:
            row.pop("quantity", None)
    elif axis in ("product", "customer", "warehouse"):
        setattr(focus, {"product": "products", "customer": "customers", "warehouse": "warehouse"}[axis], [])
    else:
        focus.extra.pop(axis, None)


def _write_set(focus: Focus, axis: str, verdict: dict[str, Any]) -> None:
    """The scalar axes a `set` writes onto the focus; entity axes are written by the
    existing kind rules from the parse's entities."""
    if axis in ("product", "customer", "warehouse", "brand") or axis in (focus.extra or {}):
        # The stated entities replace the held value of this axis; the kind rules write them.
        _clear_axis(focus, axis)
    elif axis == "channel":
        focus.sales_channel = verdict.get("sales_channel")
    elif axis == "sort":
        focus.sort = {k: verdict.get(f"sort_{k}") for k in ("by", "dir") if verdict.get(f"sort_{k}")}
    elif axis == "status":
        focus.status = verdict.get("status") or verdict.get("order_status") or focus.status
    elif axis == "document":
        focus.document = list(verdict.get("document") or [])
    elif axis == "period":
        window = {k: verdict.get(f"date_filter_{k}") for k in ("start", "end") if verdict.get(f"date_filter_{k}")}
        if window:
            focus.date_window = window
    elif axis == "tier":
        if verdict.get("access_levels"):
            focus.tier = [t for t in verdict["access_levels"] if isinstance(t, str)]


def _drop_domain_bound(focus: Focus) -> None:
    # The held period is not domain bound (R16): it carries across a switch unless the parser
    # says `skip` / `clear` period.
    for name in _DOMAIN_BOUND:
        setattr(focus, name, [] if name == "document" else None)


def _reject(state: State, verdict: dict[str, Any], log: dict[str, Any], reasons: list[dict[str, Any]]) -> Applied:
    log["rejected"] = reasons
    return Applied(False, state, {k: v for k, v in verdict.items() if k != "state_changes"}, log)


def _brand_words(brands: list[Any]) -> set[str]:
    """The registry's brand names and codes, folded; rows may be plain strings or
    `{brand_name, brand_code}` dicts."""
    words: set[str] = set()
    for brand in brands or ():
        values = [brand] if isinstance(brand, str) else [brand.get("brand_name"), brand.get("brand_code")] if isinstance(brand, dict) else []
        words.update(str(v).strip().casefold() for v in values if v)
    return words


def _held(focus: Focus, axis: str) -> bool:
    """Does the focus hold anything on this axis (a `clear` of nothing is invalid)?"""
    if axis == "customer":
        return bool(focus.customers)
    if axis == "period":
        return bool(focus.date_window)
    if axis == "channel":
        return bool(focus.sales_channel)
    if axis == "status":
        return bool(focus.status)
    if axis == "document":
        return bool(focus.document)
    if axis == "domain":
        return bool(focus.domains)
    if axis == "report":
        return bool(focus.top_selling or focus.sales_ranking_frame or focus.low_stock)
    if axis == "relation":
        return bool(focus.relation)
    if axis == "sort":
        return bool(focus.sort)
    if axis == "tier":
        return bool(focus.tier)
    if axis == "brand":
        return bool(focus.brands)
    if axis == "quantity":
        return any(isinstance(r, dict) and r.get("quantity") is not None for r in focus.products)
    if axis == "product":
        return bool(focus.products)
    if axis == "warehouse":
        return bool(focus.warehouse)
    return bool((focus.extra or {}).get(axis))


def _day(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)).strftime("%d/%m/%Y")
    except ValueError:
        return _clean(value)


def _window_label(window: dict[str, Any]) -> str:
    bounds = [d for d in (_day(window.get("start")), _day(window.get("end"))) if d]
    return " to ".join(bounds) or "the same period"


def _period_ask(state: State, window: dict[str, Any], domain: str | None) -> Any:
    options = [
        {"position": 1, "label": _window_label(window), "entity_type": "period", "payload": {"window": dict(window)}},
        {"position": 2, "label": "Another period", "entity_type": "period", "payload": {"window": None}},
    ]
    return pending_ask(
        PERIOD_ASK, options, asked_at_turn=state.turn_no, expects="pick",
        payload={"domain": domain, "slot": "period"},
    )


def _customer_ask(state: State, rows: list[dict[str, Any]], domain: str | None) -> Any:
    name = next(iter(_names(rows, "name", "raw", "canonical_code")), None) or "The same customer"
    options = [
        {"position": 1, "label": name, "entity_type": "customer", "payload": {"keep": True}},
        {"position": 2, "label": "All customers", "entity_type": "customer", "payload": {"keep": False}},
    ]
    return pending_ask(
        CUSTOMER_ASK, options, asked_at_turn=state.turn_no, expects="pick",
        payload={"domain": domain, "slot": "customer"},
    )


def _hand_over(state: State) -> Any:
    option = {
        "position": 1, "label": "Customer service", "entity_type": "team",
        "payload": {"team": "customer_service", "agent": None, "brand_code": None},
    }
    return pending_ask(
        "team_pick", [option], team="customer_service", expects="yes_no", asked_at_turn=state.turn_no,
        payload={"agent": None, "brand_code": None},
    )


def _focus_questions(focus: Focus) -> bool:
    """A question the focus itself holds for one turn: a clarify, the other-brands offer, a
    top selling question."""
    return bool(
        focus.set_clarify
        or (isinstance(focus.set_page, dict) and focus.set_page.get("other_brands"))
        or (isinstance(focus.top_selling, dict) and focus.top_selling.get("asked"))
    )


def _pickable(row: dict[str, Any]) -> bool:
    # A stored row names its kind; only product rows are pickable (a row written before the
    # kind was stored has none and reads as a product).
    return bool(row.get("code")) and row.get("entity_type", "product") == "product"


def set_aside(state: State, slots: dict[str, str]) -> State:
    """The slots the parser said `skip` for, taken off the focus for this turn and kept on
    the state so the tail can put them back. The stored focus never loses them."""
    aside: dict[str, Any] = {}
    focus = copy.deepcopy(state.focus)
    if slots.get("customer") == "skip" and focus.customers:
        aside["customers"], focus.customers = focus.customers, []
    if slots.get("period") == "skip" and focus.date_window:
        aside["date_window"], focus.date_window = focus.date_window, None
    return replace(state, focus=focus, held_aside=aside) if aside else state


def restored(state: State) -> State:
    """The state with the set-aside slots back on the focus (unless the turn wrote its own)."""
    aside = getattr(state, "held_aside", None)
    if not aside:
        return state
    focus = copy.deepcopy(state.focus)
    if aside.get("customers") and not focus.customers:
        focus.customers = aside["customers"]
    if aside.get("date_window") and not focus.date_window:
        focus.date_window = aside["date_window"]
    return replace(state, focus=focus, held_aside=None)


def apply_changes(
    state: State,
    verdict: dict[str, Any],
    *,
    open_question: dict[str, Any] | None,
    domains: list[str],
    kinds: list[str],
    brands: list[Any],
    positions_read_elsewhere: bool,
) -> Applied:
    """Validate the parser's `state_changes` and carry the prior state by them.

    Returns the new `State` (with `state_led` set) and the verdict with `state_changes`
    popped; an invalid change returns the inputs unchanged with the reasons in the log.
    `log["slots"]` is the stance stated per held slot, `{axis: op}`."""
    changes = verdict.get("state_changes")
    shown = view(state, open_question)
    log: dict[str, Any] = {
        "shown": render(shown),
        "before": shown,
        "changes": copy.deepcopy(changes),
        "rejected": [],
        "slots": {},
    }
    if log["shown"] is None:
        # Nothing shown, nothing to carry: a first ask runs exactly as it does without the key.
        return Applied(True, state, {k: v for k, v in verdict.items() if k != "state_changes"}, log)
    if not isinstance(changes, list) or not changes:
        return _reject(state, verdict, log, [{"op": None, "reason": "no changes declared"}])

    axes = set(FIXED_AXES) | set(kinds) | set(CLEAR_ONLY_AXES) | {k for k, v in (state.focus.extra or {}).items() if v}
    focus = copy.deepcopy(state.focus)
    # A carried customer row with no code and no key names nothing: it is not carried.
    focus.customers = [
        c for c in focus.customers
        if not isinstance(c, dict) or c.get("uuid") or c.get("uuids") or c.get("canonical_code") or c.get("raw") or c.get("name")
    ]
    # The same op stated twice (several `set product`) is one op.
    seen_ops: list[Any] = []
    deduped = []
    for c in changes:
        key = json.dumps(c, sort_keys=True, default=str)
        if key not in seen_ops:
            seen_ops.append(key)
            deduped.append(c)
    changes = deduped
    ignored: list[dict[str, Any]] = []
    ops = [c.get("op") if isinstance(c, dict) else None for c in changes]
    rejected: list[dict[str, Any]] = []

    def fail(op: Any, reason: str) -> None:
        rejected.append({"op": op, "reason": reason})

    # A `keep` that names no slot (no axis, "null", or any other axis) is a plain keep.
    plain_keeps = sum(
        1 for c in changes if isinstance(c, dict) and c.get("op") == "keep" and c.get("axis") not in SLOT_AXES
    )
    if plain_keeps + ops.count("new_subject") > 1:
        fail("keep", "keep and new_subject are exclusive")
    seen_axes: set[str] = set()
    set_axes: list[str] = []
    slots: dict[str, str] = {}
    picks: list[int] = []
    ignored_ops = 0
    # A set the parse backs with a value wins over a slot op on the same axis; a period the
    # message states replaces the held one with or without a `set` (R16).
    declared_sets = {
        c.get("axis") for c in changes
        if isinstance(c, dict) and c.get("op") == "set" and c.get("axis") in axes and _has_field(verdict, c["axis"])
    }
    states_period = _has_field(verdict, "period")
    for change in changes:
        op = change.get("op") if isinstance(change, dict) else None
        axis, position = (change.get("axis"), change.get("position")) if isinstance(change, dict) else (None, None)
        if op in ("keep", "skip", "unclear") and axis in SLOT_AXES and (
            axis in declared_sets or (axis == "period" and states_period)
        ):
            # The stated value is the change; the slot op is logged, not rejected.
            ignored.append({"axis": axis, "reason": f"{op} {axis} dropped: the message states its own"})
        elif op not in OPS and op not in SLOT_OPS:
            fail(op, "unknown op")
        elif op == "keep" and axis not in SLOT_AXES:
            pass
        elif op in ("set", "clear") or (op in SLOT_OPS and axis is not None) or op == "skip" or op == "unclear":
            if op in ("skip", "unclear", "keep") and axis not in SLOT_AXES:
                fail(op, f"{op} takes customer or period, not {axis}")
            elif axis not in axes:
                fail(op, f"unknown axis {axis}")
            elif axis in seen_axes:
                fail(op, f"axis {axis} repeated")
            elif op == "clear" and not _held(focus, axis):
                ignored.append({"axis": axis, "reason": f"nothing held for {axis}"})
                ignored_ops += 1
            else:
                seen_axes.add(axis)
                if op == "set":
                    if not _has_field(verdict, axis):
                        # A set the parse carries no value for changes nothing; the rest applies.
                        ignored.append({"axis": axis, "reason": f"no value for {axis} in the parse"})
                        ignored_ops += 1
                    else:
                        set_axes.append(axis)
                elif axis in SLOT_AXES:
                    slots[axis] = op
        elif op == "pick":
            if not isinstance(position, int) or isinstance(position, bool):
                fail(op, "pick needs a position")
            else:
                picks.append(position)
        elif op == "answer":
            mode = (verdict.get("open_question_answer") or {}).get("mode")
            if open_question is None and not _focus_questions(focus):
                fail(op, "no open question to answer")
            elif mode is None:
                fail(op, "no answer declared")
        elif op == "switch_domain":
            target = _message_domains(verdict)
            if not target or any(d not in domains for d in target):
                fail(op, "no known domain in the parse")

    if ignored_ops and ignored_ops == len(changes):
        # Every part was a no-op: there is no change to apply.
        for item in ignored:
            fail("set", item["reason"])

    if brands and "brand" in set_axes:
        known = _brand_words(brands)
        for e in _entities(verdict):
            if e.get("hint") == "brand" and e.get("current_message") is not False:
                if not ({str(e.get("canonical_code") or "").casefold(), str(e.get("raw") or "").casefold()} & known):
                    fail("set", f"unknown brand {e.get('canonical_code') or e.get('raw')}")

    options = {
        o.get("position"): o for o in (open_question or {}).get("options") or [] if isinstance(o, dict)
    }
    rows = {r.get("position"): r for r in (focus.last_list or []) if isinstance(r, dict)}
    roster_picks: list[int] = []
    data_picks: list[dict[str, Any]] = []
    for position in picks:
        if position in options:
            roster_picks.append(position)
        elif position in rows and _pickable(rows[position]):
            data_picks.append(rows[position])
        else:
            fail("pick", f"position {position} is not a pickable item of the list")

    if rejected:
        return _reject(state, verdict, log, rejected)

    # ---- carry, on the copy, keyed off the ops ----
    pending = state.pending
    asked_before = getattr(state.pending, "kind", None) in _SLOT_ASKS
    out = {k: v for k, v in verdict.items() if k != "state_changes"}
    if states_period and "period" not in set_axes and slots.get("period") not in ("skip", "clear"):
        set_axes.append("period")
    slot_pick = None
    if asked_before and roster_picks:
        slot_pick, roster_picks = roster_picks[0], []
    if slots.get("customer") == "unclear":
        # R15: the held entity's registry kind settles it, before any switch is computed; a
        # customer means delivery, and this wins over the parse's own domain and asks.
        mapped = [KIND_DOMAIN.get(str(row.get("hint"))) for row in focus.customers if isinstance(row, dict)]
        if mapped and all(mapped):
            out["domain_hint"] = mapped[0]
            out["asks"] = []
    new_subject = "new_subject" in ops
    if new_subject:
        window = focus.date_window if NEW_SUBJECT_KEEPS_PERIOD else None
        focus = Focus(
            customers=focus.customers, tier=focus.tier, brands=focus.brands,
            tasks=focus.tasks, last_list=focus.last_list, date_window=window,
        )
        pending = None
    switch_to = _message_domains(out)
    held = list(focus.domains or [])
    if "switch_domain" in ops or (held and switch_to and not any(d in held for d in switch_to)):
        held_values = {
            "channel": ("sales_channel", focus.sales_channel),
            "status": ("status", focus.status),
            "document": ("document", focus.document),
            "sort": ("sort_by", (focus.sort or {}).get("by")),
        }
        _drop_domain_bound(focus)
        if switch_to:
            focus.domains = list(switch_to)
        # A parse field that merely restates a value just dropped does not bring it back
        # (value comparison only; a different value or an explicit `set` still applies).
        for axis_name, (field, value) in held_values.items():
            if value and axis_name not in set_axes and out.get(field) == value:
                out[field] = [] if field == "document" else None
                if field == "sort_by":
                    out["sort_dir"] = None
    for change in changes:
        axis = change.get("axis")
        if change["op"] == "clear":
            _clear_axis(focus, axis)
        elif change["op"] == "set" and axis in set_axes:
            _write_set(focus, axis, verdict)
    if "period" in set_axes and "period" not in declared_sets:
        _write_set(focus, "period", verdict)

    slot_ask = None
    period_answer = (
        ("answer" in ops or slot_pick is not None) and pending is not None and asked_before
    )
    answering = ("answer" in ops or bool(roster_picks)) and not period_answer
    if period_answer:
        # The answer fills only the period slot; nothing else of the state moves.
        declared = verdict.get(question_mod.ANSWER_KEY) or {}
        chosen = slot_pick if slot_pick is not None else (
            declared.get("picked") or verdict.get(question_mod.POSITIONS_KEY) or [None]
        )[0]
        option = next((o for o in pending.options if o.get("position") == chosen), None)
        if option is not None and pending.kind == PERIOD_ASK:
            focus.date_window = (option.get("payload") or {}).get("window")
        elif option is not None and not (option.get("payload") or {}).get("keep"):
            focus.customers = []
        pending = None
        out[question_mod.ANSWER_KEY] = dict(question_mod.NO_ANSWER)
        out[question_mod.POSITIONS_KEY] = []
    elif not answering:
        out, _ = question_mod.without_phantom_answer(
            out, open_question=None, positions_read_elsewhere=positions_read_elsewhere, focus_has_product=False
        )
    if roster_picks:
        out[question_mod.ANSWER_KEY] = {
            **question_mod.NO_ANSWER,
            "mode": "pick",
            "picked": list(roster_picks),
            "qty_for_all": (verdict.get(question_mod.ANSWER_KEY) or {}).get("qty_for_all"),
        }
        out[question_mod.POSITIONS_KEY] = list(roster_picks)
    if "close_question" in ops:
        if question_mod.is_the_stock_question(open_question):
            # Only an owed quantities question is cancelled, and only by a lone close.
            if open_question.get("kind") == question_mod.QUANTITIES and set(ops) == {"close_question"}:
                out[question_mod.ANSWER_KEY] = {**question_mod.NO_ANSWER, "mode": "cancel"}
        elif open_question is not None:
            pending, focus.required_ask = None, None
        focus.set_clarify = None
        if isinstance(focus.top_selling, dict) and "asked" in focus.top_selling:
            focus.top_selling = {k: v for k, v in focus.top_selling.items() if k != "asked"}
        if isinstance(focus.set_page, dict) and "other_brands" in focus.set_page:
            focus.set_page = {k: v for k, v in focus.set_page.items() if k != "other_brands"}
    if slots.get("customer") == "unclear" and focus.customers and unclear_slot_default() == "ask":
        mapped = [KIND_DOMAIN.get(str(row.get("hint"))) for row in focus.customers if isinstance(row, dict)]
        if not (mapped and all(mapped)):
            # The type cannot settle it (R11): ask once, real names; a second one hands over.
            if asked_before:
                slot_ask, pending = _hand_over(state), None
            else:
                slot_ask = _customer_ask(state, focus.customers, (switch_to or focus.domains or [None])[0])
    if slots.get("period") == "unclear" and focus.date_window and unclear_slot_default() == "ask":
        if asked_before:
            slot_ask, pending = _hand_over(state), None
        else:
            slot_ask = _period_ask(state, focus.date_window, (switch_to or focus.domains or [None])[0])
    held_products = list(state.focus.products)
    # Values come from the parse as stated; a restated held row (current_message false) goes.
    dropped_entities = [{"kind": e.get("hint")} for e in _entities(out) if e.get("current_message") is False]
    entities = [copy.deepcopy(e) for e in _entities(out) if e.get("current_message") is not False]
    if "product" in set_axes and len(held_products) == 1 and held_products[0].get("quantity") is not None:
        mine = [e for e in entities if e.get("hint") == "product"]
        if len(mine) == 1 and mine[0].get("quantity") is None and out.get("demand_qty") is None:
            mine[0]["quantity"] = held_products[0]["quantity"]
    picked_kinds = {"product" for _ in data_picks}
    if pending is not None or state.pending is not None:
        for position in roster_picks:
            option = next((o for o in (state.pending.options if state.pending else []) if o.get("position") == position), None)
            if option and option.get("entity_type"):
                picked_kinds.add(str(option["entity_type"]))
    if picked_kinds:
        # A pick consumes the name it points at: a typed entity of the picked kind that no
        # `set` claims is not a second subject.
        kept_entities = []
        for e in entities:
            if e.get("hint") in picked_kinds and e.get("hint") not in set_axes:
                dropped_entities.append({"kind": e.get("hint")})
            else:
                kept_entities.append(e)
        entities = kept_entities
    for row in data_picks:
        # The code only: a stored key is never trusted, the resolver settles the code fresh
        # under the contact's current scope.
        entities.extend(option_entities({k: v for k, v in row.items() if k not in ("uuid", "uuids")}))
    if data_picks:
        out[question_mod.POSITIONS_KEY] = []
    out["entities"] = entities

    led = (
        "answer" if answering
        else "new_ask" if new_subject
        else "refine" if (set_axes or "clear" in ops or data_picks or period_answer)
        else "carry"
    )
    log["rejected"] = []
    log["slots"] = slots
    log["ignored"] = ignored
    log["dropped_entities"] = dropped_entities
    return Applied(
        True, replace(state, focus=focus, pending=pending, state_led=led, slot_ask=slot_ask), out, log
    )
