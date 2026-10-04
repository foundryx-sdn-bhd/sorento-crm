# Task: what the conversation still OWES, as one axis on the focus.
#
# Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not merged, owner ruling
# 24 Sep 2026) for chatbot-stock-ask-v2 S3 (PLAN-chatbot-stock-ask-v2-24sep.md, ruling
# R1: "keep #1118's per-product quantity collection (Focus.tasks, StockQtyTask)").
#
# The focus holds what the conversation is ABOUT. It does not hold what is still owed,
# and neither does `pending`: a genuine new ask CLOSES a roster while an owed
# collection must PARK and come back. So a task is its own axis, with its own
# lifecycle, and the roster's one-shot rules are untouched.
#
# SCOPE CUT from #1118 (R1, PRINCIPLES.md "simplest thing that works"): #1118 also
# built an `IdeationTask` kind and a `task_pick` tie between two open task kinds
# (D24(b)) - both deliberately left OUT here. Ideation already has its own
# independent mechanism on main (`app/services/chatbot/lanes/ideate.py`,
# `session_state.py`) that does not need this generic Task wrapper, and a tie between
# two kinds is machinery for a problem that cannot occur while `TASK_KINDS` has one
# entry (at most one task per kind is ever open, so `claimed` below can never exceed
# length 1). `TASK_KINDS = {"stock_qty": StockQtyTask()}` is the only registry entry;
# the `TaskKind` protocol and `run()`'s generic shape stay, so a THIRD kind (real or a
# test double) still drives the same seams with no per-kind arm anywhere else.
#
# Pure, like the rest of this package: no I/O, no message words, no import of `head`,
# `dialogue`, `tail` or `engine`, and no import of `turn/state.py` either (that module
# imports THIS one, for `Focus.tasks`).
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Protocol

from app.services.chatbot.turn.plan import FetchSpec

OPEN = "open"
PARKED = "parked"
#: Owner hand test 26 Sep, slice 5: the stock check the last reply ANSWERED, kept so a
#: follow-up can revise it ("how about 100?"). It asks nothing and claims nothing but
#: that revision; the next new ask closes it.
ANSWERED = "answered"

#: Owner ruling 26 Sep 2026 (hand test F1): a dealer's stock ask ends here, never in an
#: escalation offer. `dealer_stock.py` holds the rest of that rule.
REFER_TO_SALESMAN = "Please refer to your salesman."

#: PR #1247 round 6 (owner console test of round 4): the verdict key a numbered-lines
#: reply ("1. 10, 2. 5") may ride on, {slot key: quantity}. Keyed by the slot's own key,
#: so nothing is resolved again. Round 7: the fast path that wrote it is gone and the
#: live parser's schema does not declare it, so today only a recorded or harness verdict
#: carries it; the parser's own reading is mapped onto the lines by
#: `apply._numbered_lines_are_the_products`.
SLOT_QUANTITIES = "slot_quantities"

#: PR #1247 round 8 (review B1): with `SLOT_QUANTITIES`, the lines named ARE the whole
#: answer. A list pasted back with blanks means the blank lines are skipped, whatever an
#: earlier turn noted on them.
ONLY_THESE_LINES = "slot_quantities_only"

#: The header of the point-form question (round 6, ruling 2).
EACH_QUESTION = "How many units for each?"

#: The stock question's two kinds on the parser's `Open question:` object
#: (`open_question`, `turn/question.py`): still asking, and just answered.
QUANTITIES = "quantities"
LAST_ANSWER = "last_answer"


@dataclass(frozen=True)
class Slot:
    """One thing a task is still collecting. `key` is the machine identity the fetch
    sends (a product uuid); `label` is what a person calls it (the product code);
    `value` is what the contact has said, or None while it is still owed."""

    key: str
    label: str
    value: Any = None


@dataclass(frozen=True)
class Task:
    """One open collection, carried on `Focus.tasks` and persisted inside the focus
    wire, at most one per kind.

    `not_checked` is what this task STOPPED asking about - the slots a "just proceed"
    dropped. It rides on the task because the reply has to name them, and the task is
    the one thing that knows which ones got dropped.
    """

    kind: str
    domain: str
    status: str = OPEN
    opened_at_turn: int = 0
    touched_at_turn: int = 0
    slots: tuple[Slot, ...] = ()
    not_checked: tuple[str, ...] = ()
    #: PR #1247 round 8 (review S1): the number "Is 10 for all 3 products, or for one of
    #: them?" asked about, kept for the ONE next turn so "2" or "all" can place it.
    asked_qty: int | None = None


class TaskKind(Protocol):
    """What a kind has to answer. One implementation per kind, no per-kind arms
    anywhere else.

    `claims(verdict)` is the routing question: is this turn's value MINE?
    """

    def claims(self, verdict: dict[str, Any]) -> bool: ...

    def missing(self, task: Task) -> tuple[Slot, ...]: ...

    def fill(self, task: Task, verdict: dict[str, Any]) -> Task: ...

    def to_fetch(self, task: Task) -> FetchSpec | None: ...

    def question(self, task: Task) -> str | None: ...


def _number(value: Any) -> int | None:
    """A quantity the parser stated, as an int, or None. `bool` is not a number here:
    `True` is 1 in Python and a quantity of one is not what a boolean meant."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _entity_codes(entity: dict[str, Any]) -> set[str]:
    codes = set()
    for name in ("canonical_code", "code", "raw"):
        value = entity.get(name)
        if isinstance(value, str) and value.strip():
            codes.add(value.strip().casefold())
    uuid = entity.get("uuid")
    if isinstance(uuid, str) and uuid.strip():
        codes.add(uuid.strip().casefold())
    return codes


def _only_task_slots(
    task: "Task", verdict: dict[str, Any], *, allow_quantities: bool = False
) -> bool:
    """Does this message name anything the task is not already collecting for?

    Review round 6 (#1118). Empty entities name nothing new (the original rule); so do
    entities that are all products this task already holds. `allow_quantities` is the
    difference between the two readings that need this question answered:

    * the RESUME arm says no - a quantity is a fill, not a resume;
    * the FILL arm says yes - a quantity for a product the task already holds is
      exactly what a fill IS, and it must not be mistaken for a fresh ask.
    """
    entities = [e for e in (verdict.get("entities") or []) if isinstance(e, dict)]
    if not entities:
        return True
    known: set[str] = set()
    for slot in task.slots:
        for value in (slot.key, slot.label):
            if isinstance(value, str) and value.strip():
                known.add(value.strip().casefold())
    if not known:
        return False
    for entity in entities:
        if not allow_quantities and _number(entity.get("quantity")) is not None:
            return False
        if not (_entity_codes(entity) & known):
            return False
    return True


def _slot_quantities(verdict: dict[str, Any]) -> dict[str, int]:
    """The numbered-lines reading (`SLOT_QUANTITIES`), as {slot key: int}."""
    raw = verdict.get(SLOT_QUANTITIES)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        quantity = _number(value)
        if isinstance(key, str) and key.strip() and quantity is not None:
            out[key] = quantity
    return out


def _named_slots(task: "Task", verdict: dict[str, Any]) -> tuple["Slot", ...]:
    """The task's slots this message named, in the task's own order."""
    codes: set[str] = set()
    for entity in verdict.get("entities") or []:
        if isinstance(entity, dict):
            codes |= _entity_codes(entity)
    return tuple(
        slot
        for slot in task.slots
        if any(
            isinstance(value, str) and value.strip().casefold() in codes
            for value in (slot.key, slot.label)
        )
    )


#: SEC-S2 (#1118 security review, round 1): how many slots one stock task may carry,
#: and how many of them a sentence enumerates before it counts the rest. An ask that
#: names no product at all used to open a task with one slot per CATALOGUE row (the
#: tool's own page cap is 5000) and then echo every code into the reply and into every
#: later parser block. A task is a question about what the dealer named, not about the
#: book.
MAX_SLOTS = 20
MAX_NAMED = 10


def _named(labels: list[str]) -> str:
    """The labels a QUESTION prints ("for C and D"), at most `MAX_NAMED` of them, with
    the rest counted rather than listed (SEC-S2)."""
    if len(labels) <= MAX_NAMED:
        return _join(labels)
    shown = labels[:MAX_NAMED]
    return f"{', '.join(shown)} and {len(labels) - MAX_NAMED} others"


def _listed(labels: list[str]) -> str:
    """The same cap for a plain COMMA list - the parser hint's own shape, a fact
    stated to the model rather than a sentence said to a person."""
    if len(labels) <= MAX_NAMED:
        return ", ".join(labels)
    shown = labels[:MAX_NAMED]
    return f"{', '.join(shown)} and {len(labels) - MAX_NAMED} others"


def _join(words: list[str]) -> str:
    if len(words) > 1:
        return ", ".join(words[:-1]) + " and " + words[-1]
    return words[0] if words else ""


class StockQtyTask:
    """The dealer's owed quantities, one slot per product.

    The slots are OPENED from the stock tool's own reply (`needs_quantity` per
    product): the backend owns the rule about who must state a quantity, and the
    engine never reads a policy to decide it.
    """

    label = "stock check"

    def claims(self, verdict: dict[str, Any]) -> bool:
        if verdict.get("proceed_anyway") is True:
            return True
        if _slot_quantities(verdict):
            return True
        for entity in verdict.get("entities") or []:
            if isinstance(entity, dict) and _number(entity.get("quantity")) is not None:
                return True
        return _number(verdict.get("demand_qty")) is not None

    def missing(self, task: Task) -> tuple[Slot, ...]:
        return tuple(slot for slot in task.slots if slot.value is None)

    def fill(self, task: Task, verdict: dict[str, Any]) -> Task:
        slots = list(task.slots)
        if verdict.get(ONLY_THESE_LINES) is True:
            slots = [replace(slot, value=None) for slot in slots]
        by_code: dict[str, int] = {}
        for entity in verdict.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            quantity = _number(entity.get("quantity"))
            if quantity is None:
                continue
            for code in _entity_codes(entity):
                by_code[code] = quantity
        # Round 6: numbered lines, already matched to their slots by key.
        for key, quantity in _slot_quantities(verdict).items():
            by_code[key.strip().casefold()] = quantity
        for i, slot in enumerate(slots):
            for code in (slot.key, slot.label):
                if not isinstance(code, str):
                    continue
                named = by_code.get(code.strip().casefold())
                if named is not None:
                    # A restated quantity REPLACES the earlier one, and nothing is
                    # asked twice for a product already noted.
                    slots[i] = replace(slot, value=named)
                    break
        if verdict.get("proceed_anyway") is True:
            # "go ahead with what you have": answer what is noted and drop every
            # product still missing a quantity, naming them in the reply. When
            # NOTHING is noted, that leaves a task with no slots at all - there is
            # nothing to fetch and nothing to answer, so the task is finished and the
            # reply is the one line that says which products went unchecked.
            dropped = tuple(slot.label for slot in slots if slot.value is None)
            return replace(
                task,
                slots=tuple(slot for slot in slots if slot.value is not None),
                not_checked=dropped,
            )
        still_missing = [slot for slot in slots if slot.value is None]
        bare = _number(verdict.get("demand_qty"))
        if len(still_missing) == 1 and not by_code:
            # Deterministic fallback, for a prompt version that does not fill
            # `entities[].quantity` yet: a bare number with exactly ONE slot still
            # owed belongs to that slot.
            if bare is not None:
                index = slots.index(still_missing[0])
                slots[index] = replace(still_missing[0], value=bare)
        elif still_missing and bare is not None and not by_code and not verdict.get("entities"):
            # Owner ruling 26 Sep 2026 (hand test F2, "okay"): one bare number after a
            # question about several products applies to each product still owed.
            # Round 6 (owner console test of round 4, "they can just say one number
            # like 10 to apply to all"): a family the dealer picked "all" of is such a
            # question too - the only way a family reaches here as a task (slice 2).
            slots = [replace(slot, value=bare) if slot.value is None else slot for slot in slots]
        return replace(task, slots=tuple(slots))

    def to_fetch(self, task: Task) -> FetchSpec | None:
        if not task.slots:
            # "just proceed" with nothing noted leaves no product to ask about.
            # There is nothing to fetch, and `question()` below says the one thing
            # left to say.
            return None
        entities = [
            {
                "raw": slot.label,
                "hint": "product",
                "canonical_code": slot.label,
                "uuid": slot.key,
                "current_message": True,
                "confident": True,
            }
            for slot in task.slots
        ]
        # SEC-N4 (#1118 security review, round 1): a slot value read back from a
        # session row written by an older build (or by hand) may be a string, a float
        # or anything else JSON can carry, and `requested_quantities` is validated at
        # the route - a bad value there is a 400 that kills the WHOLE fetch, not just
        # that product. Coerced here, once, and a value that cannot be a quantity is
        # simply not sent (the backend then answers `needs_quantity` for it, which is
        # the truth).
        quantities = {}
        for slot in task.slots:
            quantity = _number(slot.value)
            if quantity is not None:
                quantities[slot.key] = quantity
        filters: dict[str, Any] = {"task": task.kind}
        if quantities:
            filters["requested_quantities"] = quantities
        if task.not_checked:
            filters["not_checked"] = list(task.not_checked)
        return FetchSpec(
            domain=task.domain, entities=entities, filters=filters, date_window=None
        )

    def question(self, task: Task) -> str | None:
        """The same sentence the MCP presenter says when the tool reply itself carries
        a gap - said HERE only on the turn that resumes the task without fetching
        anything, so the dealer reads one wording for one question."""
        if not task.slots and task.not_checked:
            # The dealer said to proceed and had noted nothing, so the whole ask went
            # unchecked. One line, and the task is over - never an empty task that
            # keeps printing a hint about a question nobody is answering.
            return f"Not checked: {_named(list(task.not_checked))}."
        missing = [slot.label for slot in self.missing(task)]
        if not missing:
            return None
        if len(task.slots) == 1:
            # Owner hand test 26 Sep, slice 2: one product, one question, named.
            return f"How many units of {missing[0]}?"
        # Round 6, ruling 2 (owner console test of round 4): point form, one numbered
        # line per product ending " - " for the dealer to fill, what is noted already
        # filled in. The numbers stay put, so "1. 10, 2. 5" always means these lines.
        # Every slot is listed (at most `MAX_SLOTS`, SEC-S2): a line not printed is a
        # line the dealer cannot fill.
        lines = [
            f"{i}. {slot.label} - {'' if slot.value is None else slot.value}"
            for i, slot in enumerate(task.slots, 1)
        ]
        return "\n".join([EACH_QUESTION, *lines])

    def hint(self, task: Task, *, open_question_shown: bool = True) -> str:
        """The line the parser reads. `open_question_shown` False (the block carries no
        `Open question:` object, e.g. under another open question) prints the lines here
        as round 7 did, so the parser never reads a pointer to a line that is absent."""
        if task.status == ANSWERED:
            answered = [
                f"{slot.label} x {slot.value}"
                for slot in task.slots
                if slot.value is not None
            ]
            return f"Last answered: {_listed(answered)}."
        parts = [f"Open task: {self.label}."]
        if len(task.slots) > 1:
            # PR #1247 round 8: the point-form question's lines, what is noted and what
            # is owed ride on the `Open question:` object (`open_question`), stated once.
            if open_question_shown:
                parts.append("Asked as numbered lines: see Open question.")
                return " ".join(parts)
            lines = "; ".join(
                f"{i}. {slot.label} - {'' if slot.value is None else slot.value}".rstrip()
                for i, slot in enumerate(task.slots, 1)
            )
            parts.append(f"Asked as numbered lines, a blank quantity still owed: {lines}.")
            return " ".join(parts)
        noted = [
            f"{slot.label} x {slot.value}" for slot in task.slots if slot.value is not None
        ]
        if noted:
            parts.append(f"Noted: {_listed(noted)}.")
        missing = [slot.label for slot in self.missing(task)]
        if missing:
            parts.append(f"Still needs a quantity for: {_listed(missing)}.")
        return " ".join(parts)


#: The registry. One entry today (R1 scope cut) - a second kind pays for nothing but
#: its own class.
TASK_KINDS: dict[str, Any] = {"stock_qty": StockQtyTask()}


def kind_of(task: Any) -> Any:
    return TASK_KINDS.get(_value(task, "kind"))


def _value(task: Any, name: str, default: Any = None) -> Any:
    """One read for a task row that may be a `Task` or the dict it is stored as."""
    if isinstance(task, dict):
        return task.get(name, default)
    return getattr(task, name, default)


# --------------------------------------------------------------------------- #
# The wire shape - carried INSIDE the focus (no new session key)
# --------------------------------------------------------------------------- #


def task_to_wire(task: Task) -> dict[str, Any]:
    return {
        "kind": task.kind,
        "domain": task.domain,
        "status": task.status,
        "opened_at_turn": task.opened_at_turn,
        "touched_at_turn": task.touched_at_turn,
        "slots": [
            {"key": slot.key, "label": slot.label, "value": slot.value}
            for slot in task.slots
        ],
        "not_checked": list(task.not_checked),
        **({"asked_qty": task.asked_qty} if task.asked_qty is not None else {}),
    }


def task_from_wire(raw: Any) -> Task | None:
    """Tolerant, like every other read on this path: a task that cannot be read is a
    forgotten collection, not a failed turn."""
    if not isinstance(raw, dict) or not raw.get("kind"):
        return None
    slots = []
    for row in raw.get("slots") or []:
        if not isinstance(row, dict) or row.get("key") is None:
            continue
        slots.append(
            Slot(
                key=str(row.get("key")),
                label=str(row.get("label") or row.get("key")),
                value=row.get("value"),
            )
        )
    status = raw.get("status")
    return Task(
        kind=str(raw["kind"]),
        domain=str(raw.get("domain") or ""),
        status=status if status in (OPEN, PARKED, ANSWERED) else OPEN,
        opened_at_turn=raw.get("opened_at_turn") or 0,
        touched_at_turn=raw.get("touched_at_turn") or 0,
        slots=tuple(slots),
        not_checked=tuple(
            str(name) for name in (raw.get("not_checked") or []) if name is not None
        ),
        asked_qty=_number(raw.get("asked_qty")),
    )


# --------------------------------------------------------------------------- #
# The lifecycle, as one pure step `apply()` runs before decide's four outcomes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TaskOutcome:
    tasks: tuple[Task, ...] = ()
    fetch: FetchSpec | None = None
    fetch_domain: str | None = None
    question: str | None = None
    question_domain: str | None = None
    parked_kinds: tuple[str, ...] = ()
    closed_kinds: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()


def run(
    tasks: tuple[Task, ...],
    verdict: dict[str, Any],
    *,
    decision_kind: str,
    positions: list[int] | None = None,
    pending: Any = None,
    turn_no: int = 0,
) -> TaskOutcome:
    """Every task rule, in the order the plan names: claims -> fill, complete ->
    fetch, park, resume, close. One function, so the arms cannot disagree about one
    turn.

    `decision_kind` is `decide()`'s own reading of this message; `positions` are the
    option numbers it picked (unused while `TASK_KINDS` has one entry - kept on the
    signature so a caller that already threads them through does not need an arm of
    its own). Nothing here reads a word.
    """
    rules: list[str] = []
    tasks = tuple(tasks)

    if not tasks:
        return TaskOutcome(tasks=(), rules=())

    named_domain = verdict.get("domain_hint")

    # (1) Close or park on a topic reset. A reset aimed at a task's OWN domain ends
    # it; one aimed elsewhere parks it, and the focus keeps the task through the wipe
    # (`apply._focus_rules` empties every other axis).
    if verdict.get("topic_reset") is True:
        kept: list[Task] = []
        closed: list[str] = []
        for task in tasks:
            aimed_here = not named_domain or task.domain == named_domain
            if aimed_here or task.status == ANSWERED:
                closed.append(task.kind)
                rules.append(f"task_closed_on_reset_{task.kind}")
                continue
            kept.append(replace(task, status=PARKED))
            rules.append(f"task_parked_{task.kind}")
        return TaskOutcome(
            tasks=tuple(kept), closed_kinds=tuple(closed), rules=tuple(rules)
        )

    # Owner hand test 26 Sep, slice 4 (T14): a message that names a product the task
    # does NOT hold is a stock question of its own, never a fill. Claimed, the task's
    # own fetch replaced the whole turn's (`apply.py`, `task_drives_the_fetch`) and
    # "ELP3754 10 and SRTKT1631SS 20" over a one-slot ELP3754 task dropped SRTKT1631SS.
    claimed = [
        task
        for task in tasks
        if task.status != ANSWERED
        and TASK_KINDS.get(task.kind) is not None
        and TASK_KINDS[task.kind].claims(verdict)
        and _only_task_slots(task, verdict, allow_quantities=True)
    ]

    out = []
    parked_kinds: list[str] = []
    fetch = None
    fetch_domain = None
    question = None
    question_domain = None
    for task in tasks:
        impl = TASK_KINDS.get(task.kind)
        if impl is None:
            out.append(task)
            continue
        if task.status == ANSWERED:
            revised = _revised(task, verdict, turn_no)
            if revised is not None and fetch is None:
                # Owner hand test 26 Sep, slice 5 (T8, T13): "how about 100?" after
                # SRTGV332-DIY x 20 was answered is the same product at a new quantity.
                out.append(revised)
                rules.append(f"task_revised_{task.kind}")
                fetch = impl.to_fetch(revised)
                fetch_domain = revised.domain
            elif decision_kind == "new_ask":
                rules.append(f"task_answered_closed_{task.kind}")
            else:
                out.append(task)
            continue
        if claimed and task is claimed[0]:
            # (2) fill: the value goes to the kind that claims it, whatever the
            # current subject. A fill that moved a slot re-runs the domain's own
            # fetch over the WHOLE task, which is what lets the backend answer or
            # re-ask per product; a fill that moved nothing re-asks what is still
            # owed, unless this turn asked a question of its own - a fresh stock ask
            # carries its own products and its own reply rebuilds the task.
            filled = replace(
                impl.fill(task, verdict), status=OPEN, touched_at_turn=turn_no
            )
            moved = filled.slots != task.slots
            # The fill MERGES into the slots the task already has, so a quantity for
            # one product leaves the others exactly as they were - and while any of
            # them is still owed there is nothing to answer yet. A fetch happens when
            # NO slot is missing, or on a `proceed_anyway`, which drops the missing
            # ones and so leaves none behind either.
            still_missing = impl.missing(filled) if hasattr(impl, "missing") else ()
            spec = impl.to_fetch(filled) if (moved and not still_missing) else None
            if spec is None and moved and not filled.slots:
                # The fill emptied the task (a "just proceed" with nothing noted). It
                # is finished - it does not ride on as an empty task - and its own
                # last sentence names what went unchecked.
                rules.append(f"task_closed_on_proceed_{task.kind}")
                if question is None:
                    question = impl.question(filled)
                    question_domain = filled.domain
                continue
            out.append(filled)
            rules.append(f"task_filled_{task.kind}")
            if spec is not None:
                fetch = spec
                fetch_domain = filled.domain
            elif question is None and (
                (still_missing and _only_task_slots(task, verdict, allow_quantities=True))
                or (not moved and decision_kind != "new_ask")
            ):
                # A turn that gave or RESTATED a quantity for this task's own products
                # always speaks, whatever `decide()` made of the sentence (a quantity
                # beside a product code reads as a new ask): the dealer just answered
                # part of this question, so the reply is what is noted and what is
                # still owed - even when the restated number changed nothing. A
                # message that names a product the task does NOT hold is a fresh
                # stock question of its own and rebuilds the task, so it falls through
                # to the ordinary plan.
                question = impl.question(filled)
                question_domain = filled.domain
            continue
        named = _named_slots(task, verdict)
        if (
            task.domain
            and task.domain == named_domain
            and _only_task_slots(task, verdict)
            and _asks_to_resume(verdict, named)
        ):
            # (3) resume: the task's own topic, named again with nothing new. Only
            # what is still missing is asked; nothing is asked twice.
            resumed = replace(task, status=OPEN, touched_at_turn=turn_no)
            if named and len(named) < len(task.slots):
                # Owner hand test 26 Sep, slice 3 (T5): "check stock SRTWC286-SH-UF"
                # over a ten-product question names ONE of them - the dealer has
                # picked, so the task narrows to what they named and asks only that.
                resumed = replace(resumed, slots=named)
                rules.append(f"task_narrowed_{task.kind}")
            out.append(resumed)
            rules.append(f"task_resumed_{task.kind}")
            if question is None:
                question = impl.question(resumed)
                question_domain = resumed.domain
            continue
        if decision_kind == "new_ask" and not claimed:
            # (4) park: the dealer asked something ELSE. Silent - the reply says
            # nothing about the parked task. A turn that answered one of the open
            # tasks is not "something else" for the others.
            out.append(replace(task, status=PARKED))
            parked_kinds.append(task.kind)
            rules.append(f"task_parked_{task.kind}")
            continue
        out.append(task)

    return TaskOutcome(
        tasks=tuple(out),
        fetch=fetch,
        fetch_domain=fetch_domain,
        question=question,
        question_domain=question_domain if question else None,
        parked_kinds=tuple(parked_kinds),
        rules=tuple(rules),
    )


#: The intent a stock check is asked and resumed under ("back to the stock check":
#: `chatbot_parser_prompt.py`, the parser's own resume example).
STOCK_INTENT = "check_stock"


def _asks_to_resume(verdict: dict[str, Any], named: tuple[Slot, ...]) -> bool:
    """Is this message an explicit ask to resume the stock check (STUCK-QTY-LOOP)?

    The resume used to fire on ANY inventory message that named nothing new, and an
    entity-less inventory message is also a low stock report (`low_stock_ask.take_words`
    moves its brand and category words off the entities) and a bare "clear" the parser
    did not flag: the stored question was replayed on every one of them (owner, 4 Oct
    2026, prompt v42). A resume now needs the parser's own signal: a stock intent, or
    one of the task's own products named under no other intent."""
    intent = verdict.get("intent_hint")
    if intent == STOCK_INTENT:
        return True
    return not intent and bool(named)


def _revised(task: Task, verdict: dict[str, Any], turn_no: int) -> Task | None:
    """The answered one-product check at the bare number this message states, or None.

    One product only: "how about 100?" after a two-product answer does not say which
    one it means. A message that names a product of its own, or states a quantity
    beside one, is a stock ask of its own and is answered as one.

    PR #1247 round 8: lines of any answered check are revised by `SLOT_QUANTITIES`,
    which `apply._open_question_answer` writes from the parser's declared answer
    (`open_question_answer`) to the "last_answer" object ("3 for all of them", "2. 10"). The other lines keep their
    quantities, and the whole check is answered again."""
    quantities = {key.strip().casefold(): qty for key, qty in _slot_quantities(verdict).items()}
    if quantities:
        if not any(slot.key.strip().casefold() in quantities for slot in task.slots):
            return None
        slots = tuple(
            replace(slot, value=quantities.get(slot.key.strip().casefold(), slot.value))
            for slot in task.slots
        )
        return replace(task, status=OPEN, touched_at_turn=turn_no, slots=slots)
    if len(task.slots) != 1:
        return None
    bare = _number(verdict.get("demand_qty"))
    if bare is None:
        return None
    for entity in verdict.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        if _number(entity.get("quantity")) is not None:
            return None
        if entity.get("hint") in (None, "product") and not _only_task_slots(
            task, {"entities": [entity]}
        ):
            return None
    (slot,) = task.slots
    return replace(
        task,
        status=OPEN,
        touched_at_turn=turn_no,
        slots=(replace(slot, value=bare),),
    )


def unpark(
    tasks: tuple[Task, ...], kinds: tuple[str, ...], before: tuple[Task, ...]
) -> tuple[Task, ...]:
    """Put back the status of a task this turn parked, when the turn turned out to ASK
    a question of its own rather than answer anything.

    Parking is what keeps a task silent while the OTHER question is answered. A turn
    that ends in a roster has answered nothing and taken the conversation nowhere, so
    there is nothing yet to be silent about - and a task parked by it would then need
    the dealer to name it again, for a detour that never happened.
    """
    if not kinds:
        return tasks
    was = {task.kind: task.status for task in before}
    return tuple(
        replace(task, status=was.get(task.kind, task.status))
        if task.kind in kinds
        else task
        for task in tasks
    )


# --------------------------------------------------------------------------- #
# What the TOOL's reply says about the task (the backend owns the rule)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StockReply:
    """What the stock tool's reply leaves behind: the tasks, and - when the reply is a
    question the engine can ask better than the presenter's bare "How many units do
    you need?" - the text to say instead, plus the which-one pick to store (a
    `product_pick` the engine mints as the turn's open question)."""

    tasks: tuple[Task, ...]
    text: str | None = None
    pick: dict[str, Any] | None = None


def _availability_block(envelopes: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    block: list[dict[str, Any]] | None = None
    for envelope in envelopes or []:
        rows = envelope.get("stock_availability") if isinstance(envelope, dict) else None
        if isinstance(rows, list) and rows:
            block = [row for row in rows if isinstance(row, dict)]
    return block


def _row_label(row: dict[str, Any]) -> str | None:
    label = row.get("product_code") or row.get("product_name")
    return str(label) if label else None


def _asked_tokens(asked: list[dict[str, Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    """(shown, casefolded, entity) per product this message named, in the order named.
    Shown upper-cased: product codes are, whatever case the dealer typed them in."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    for entity in asked or []:
        if not isinstance(entity, dict) or entity.get("hint") not in (None, "product"):
            continue
        for name in ("canonical_code", "raw"):
            value = entity.get(name)
            if isinstance(value, str) and value.strip():
                out.append((value.strip().upper(), value.strip().casefold(), entity))
                break
    return out


def _group(rows: list[dict[str, Any]], token: str) -> list[dict[str, Any]]:
    """The rows one typed token placed: its own code, or the family it is a prefix of
    (the same link `apply._in_family_of` reads, D29)."""
    return [
        row
        for row in rows
        if (_row_label(row) or "").casefold().startswith(token)
    ]


def numbered(labels: list[str], start: int = 1) -> list[str]:
    """One "1. CODE" line per option, the format every other picker prints
    (`turn/compose.py::compose_question`). Owner ruling 26 Sep 2026 (round 3 hand test):
    a which-one list is numbered, "like the other pickers". `start` is where a list
    sharing a message with the lists before it carries on (AVAIL-MODE-REPLIES)."""
    return [f"{i}. {label}" for i, label in enumerate(labels, start)]


def pick_question(
    typed: str,
    labels: list[str],
    quantity: Any = None,
    count: int | None = None,
    *,
    recognised: bool = True,
    start: int = 1,
) -> str:
    """The family pick (owner hand test 26 Sep, slice 2, the scout's wording), one
    numbered code per line. A number is read as a position only while this question is
    open. The list is not sticky (owner ruling 26 Sep, round 5): once one product is
    picked it is closed and forgotten, and a later bare number is that product's
    quantity (`apply._bare_position_is_the_quantity`).

    `recognised` False (a did-you-mean's typed code, round 9): the header never
    repeats a code the resolver did not recognise, so it asks by the quantity alone."""
    total = count if isinstance(count, int) and count > len(labels) else len(labels)
    qty = _number(quantity)
    if not recognised:
        head = f"Which one do you need {qty} of?" if qty is not None else "Which one?"
    elif qty is not None:
        head = f"{typed} x {qty}: which one?"
    else:
        head = f"{typed} matches {total} products. Which one?"
    # PICKER-NO-CAP (owner, 2 Oct 2026): every option the pick carries is printed, so
    # every number the dealer can reply with is one they can see. Only matches past the
    # carried options (`count`, the stock tool's own total) are counted.
    tail = (
        [f"and {total - len(labels)} others, reply with the full code."]
        if total > len(labels)
        else []
    )
    return "\n".join([head, *numbered(labels, start), *tail])


def after_reply(
    tasks: tuple[Task, ...],
    envelopes: list[dict[str, Any]],
    *,
    turn_no: int = 0,
    named_products: bool = True,
    asked: list[dict[str, Any]] | None = None,
    demand_qty: Any = None,
) -> StockReply:
    """`tasks_after_reply`, plus what the reply should SAY when it is a question.

    Owner hand test 26 Sep, slice 2 (T1, T3, T4). `asked` is this message's own product
    entities, read for the typed token only - the `stock_availability` block exists
    only for an availability-only contact, so none of this reaches a staff reply (R10):

    * An exact code wins. A typed token that IS a product's code keeps that product and
      drops the siblings the resolver's family grouping added ("SRTWC286-SH" also placed
      its nine SRTWC286-SH-* variants).
    * A family is a pick, not a task. A typed token whose rows are a family with no exact
      code among it ("srtwc286" placed ten SRTWC286-SH* products) is asked which one, the
      typed quantity riding on the pick; a bare number cannot be read against ten slots.
      AVAIL-MODE-REPLIES rule 5: that holds per token in a message that also names other
      codes, one pick at a time (the rest queued on the pick, `NEXT_PICKS`).
    * The other rows are answered now when they carry a quantity (the presenter printed
      their lines); the ones still owed one stay in the task, whose own question replaces
      the presenter's bare one, so the dealer reads which products still need it.
    """
    block = _availability_block(envelopes)
    if block is None:
        return StockReply(tasks=tasks)
    others = tuple(task for task in tasks if task.kind != "stock_qty")
    if not any(row.get("needs_quantity") is True for row in block):
        return StockReply(tasks=_rebuilt(tasks, block, turn_no=turn_no, named_products=named_products))

    rows = list(block)
    tokens = _asked_tokens(asked or []) if named_products else []
    # PR #1247 round 8, item 4: a row the dealer named by its own code is never a
    # sibling to drop, even when it shares a prefix with another code they named
    # ("SRTWC286-SH-150" beside "SRTWC286-SH").
    named = {token for _shown, token, _entity in tokens}
    for _shown, token, _entity in tokens:
        group = _group(rows, token)
        exact = [row for row in group if (_row_label(row) or "").casefold() == token]
        if exact and len(group) > len(exact):
            dropped = {
                id(row)
                for row in group
                if row not in exact and (_row_label(row) or "").casefold() not in named
            }
            rows = [row for row in rows if id(row) not in dropped]

    families = [
        (shown, token, entity)
        for shown, token, entity in tokens
        if len(_group(rows, token)) > 1
        and not any((_row_label(row) or "").casefold() == token for row in rows)
    ]
    # AVAIL-MODE-REPLIES rule 5 (owner, 2 Oct 2026): one message may name exact codes,
    # vague ones (a family, no exact code among it) and codes found nowhere. Every vague
    # token gets its which-one list in the SAME question (owner v2 note 3, Q3 (b)), the
    # numbering running on from one list to the next so no two options share a number.
    # The rows outside every family are answered now when they carry a quantity, and
    # only the ones still owed one stay in the task.
    options: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    in_family: set[int] = set()
    for shown, token, entity in families:
        group = [
            row
            for row in _group(rows, token)
            if id(row) not in in_family and row.get("needs_quantity") is True
        ]
        labelled = [
            (row, _row_label(row)) for row in group if row.get("product_id") and _row_label(row)
        ][:MAX_SLOTS]
        if len(labelled) < 2:
            continue
        in_family.update(id(row) for row in group)
        quantity = _number(entity.get("quantity"))
        if quantity is None and len(families) == 1:
            quantity = _number(demand_qty)
        start = len(options) + 1
        for row, label in labelled:
            options.append(
                {
                    "position": len(options) + 1,
                    "label": label,
                    "code": label,
                    "uuid": str(row["product_id"]),
                    "entity_type": "product",
                }
            )
        groups.append(
            {
                "typed": shown,
                "qty": quantity,
                "count": len(group),
                "positions": list(range(start, len(options) + 1)),
            }
        )

    rest = [row for row in rows if id(row) not in in_family]
    owed = [row for row in rest if row.get("needs_quantity") is True]
    if groups:
        # One question at a time (reviewer B4): the pick is asked now, and a code still
        # owed its quantity rides on it (`OWED`), asked once every list is answered.
        carried = [
            {"key": str(row["product_id"]), "label": _row_label(row)}
            for row in owed
            if row.get("product_id") and _row_label(row)
        ]
        return StockReply(
            tasks=others, text=picks_question(options, groups), pick=stock_pick(options, groups, carried)
        )

    rebuilt = _rebuilt(tasks, owed, turn_no=turn_no, named_products=named_products)
    stock = next((task for task in rebuilt if task.kind == "stock_qty"), None)
    text = None
    if stock is not None and StockQtyTask().missing(stock):
        text = StockQtyTask().question(stock)
    return StockReply(tasks=rebuilt, text=text)


#: AVAIL-MODE-REPLIES rule 5: the lists of a several-list pick the dealer has not answered
#: yet (`{"options", "groups"}`, the numbers kept), handed on by
#: `turn/apply.py::_spend_stock_pick` and asked again by `engine._stock_ask_reply`.
NEXT_PICKS = "next_picks"
#: The pick's own quantity per code, one per list (`after_reply`), for `_spend_stock_pick`.
QTY_BY_CODE = "qty_by_code"
#: Codes still owed a quantity while a pick is open (`{"key", "label"}` each): asked by
#: `engine._stock_ask_reply` once no list is left open, never beside a pick.
OWED = "owed"


def owed_of(tasks: tuple[Task, ...]) -> list[dict[str, Any]]:
    """The open stock task's slots still owed a quantity, as `OWED` entries."""
    task = next((t for t in tasks if t.kind == "stock_qty" and t.status == OPEN), None)
    if task is None:
        return []
    return [{"key": slot.key, "label": slot.label} for slot in task.slots if slot.value is None]


def with_owed(
    tasks: tuple[Task, ...], owed: list[dict[str, Any]], *, turn_no: int
) -> tuple[tuple[Task, ...], str | None]:
    """The stock task asking every code still owed a quantity: the open task's own owed
    slots and `owed`, once each in that order, and its question."""
    slots: list[Slot] = []
    for entry in [*owed_of(tasks), *owed]:
        key, label = entry.get("key"), entry.get("label")
        if key and label and all(slot.key != key for slot in slots):
            slots.append(Slot(key=str(key), label=str(label)))
    others = tuple(t for t in tasks if t.kind != "stock_qty")
    if not slots:
        return tasks, None
    task = Task(
        kind="stock_qty",
        domain="inventory",
        status=OPEN,
        opened_at_turn=turn_no,
        touched_at_turn=turn_no,
        slots=tuple(slots[:MAX_SLOTS]),
    )
    return others + (task,), StockQtyTask().question(task)


def picks_question(options: list[dict[str, Any]], groups: list[dict[str, Any]]) -> str:
    """Every list of a pick in one message, each under its own header, numbered as the
    options are (`groups[].positions`)."""
    parts = []
    for group in groups:
        shown = [o for o in options if o.get("position") in group["positions"]]
        if not shown:
            continue
        parts.append(
            pick_question(
                group["typed"],
                [str(o["label"]) for o in shown],
                group.get("qty"),
                group.get("count"),
                start=shown[0]["position"],
            )
        )
    return "\n\n".join(parts)


def stock_pick(
    options: list[dict[str, Any]], groups: list[dict[str, Any]], owed: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """The `product_pick` a which-one question is stored as. With one list it is exactly
    the single family pick it always was; with several, `groups` keeps each list's typed
    token, quantity and positions."""
    quantities = [g.get("qty") for g in groups]
    by_code = {
        str(o["label"]).casefold(): g["qty"]
        for g in groups
        if g.get("qty") is not None
        for o in options
        if o.get("position") in g["positions"]
    }
    return {
        "options": options,
        "payload": {
            "domain": "inventory",
            "domains": ["inventory"],
            "stock_pick": True,
            "typed": groups[0]["typed"],
            "count": groups[0]["count"] if len(groups) == 1 else len(options),
            # A quantity is owed (`question.of_pending`) only while some list has none.
            "stock_qty": quantities[0] if all(q is not None for q in quantities) else None,
            "groups": groups,
            QTY_BY_CODE: by_code,
            OWED: list(owed or []),
        },
    }


def tasks_after_reply(
    tasks: tuple[Task, ...],
    envelopes: list[dict[str, Any]],
    *,
    turn_no: int = 0,
    named_products: bool = True,
) -> tuple[Task, ...]:
    """`after_reply`'s tasks alone, for a caller with no typed tokens to narrow by."""
    return after_reply(
        tasks, envelopes, turn_no=turn_no, named_products=named_products
    ).tasks


def _rebuilt(
    tasks: tuple[Task, ...],
    block: list[dict[str, Any]],
    *,
    turn_no: int = 0,
    named_products: bool = True,
) -> tuple[Task, ...]:
    """The stock task, rebuilt from the stock tool's own `stock_availability` block.

    ONE rule for open, update and close, and it is the backend's rule, not the
    engine's: every entry becomes a slot, its `requested_qty` becomes the slot's
    value, and a reply where no entry still needs a quantity has no task left to
    carry. The engine never reads a policy to decide who must state a quantity - it
    reads what the reply said about it.

    `named_products` is SEC-S2 (#1118 security review, round 1): did the ask name a
    product at all? "What stock do you have?" names none, so the reply is a PAGE OF
    THE CATALOGUE and every row of it carries `needs_quantity` - which opened a task
    with one slot per product and then echoed every code into the reply and into
    every later parser block. A task is a question about what the dealer named; with
    nothing named there is no task to open, and the reply stays the presenter's own
    single question. An ALREADY OPEN task is still updated (it named its products
    when it opened).
    """
    others = tuple(task for task in tasks if task.kind != "stock_qty")
    answered = not any(row.get("needs_quantity") is True for row in block)

    existing = next((task for task in tasks if task.kind == "stock_qty"), None)
    if existing is None and not named_products:
        # SEC-S2: nothing was named, so there is nothing to collect FOR.
        return others
    slots: list[Slot] = []
    for row in block:
        key = row.get("product_id")
        # R-S4 / SEC-N3 (#1118): a row with no `product_code` must never put the UUID
        # in front of a person - not in the reply, not in the hint line the parser
        # reads next turn (the frontend's own "no UUIDs in the UI" rule, here at the
        # seam the text is built from). The product NAME is the fallback; a row with
        # neither is dropped, because there is no way to ask about it.
        label = row.get("product_code") or row.get("product_name")
        if not key or not label:
            continue
        slots.append(
            Slot(key=str(key), label=str(label), value=_number(row.get("requested_qty")))
        )
    # SEC-S2: a cap, not a truncation of the question the dealer asked - a reply that
    # needs more than this many products has stopped being a stock check and is a
    # catalogue dump. The remaining products are simply not COLLECTED for; the reply
    # itself is the presenter's and is unchanged.
    slots = slots[:MAX_SLOTS]
    if not slots:
        return others
    if answered and any(slot.value is None for slot in slots):
        # An answered entry with no quantity on it has nothing to revise.
        return others
    opened = existing.opened_at_turn if existing is not None else turn_no
    return others + (
        Task(
            kind="stock_qty",
            domain="inventory",
            # Owner hand test 26 Sep, slice 5: an answered check is KEPT, as what the
            # last reply answered, so "how about 100?" has something to revise.
            status=ANSWERED if answered else OPEN,
            opened_at_turn=opened,
            touched_at_turn=turn_no,
            slots=tuple(slots),
        ),
    )


def tasks_after_tool_status(
    tasks: tuple[Task, ...], *, kind: str, status: Any
) -> tuple[Task, ...]:
    """The one seam a kind's own `closes_on_tool_status` is read at (unused with only
    `stock_qty` registered; kept generic so a future kind that closes on a tool word
    plugs in here with no engine change)."""
    impl = TASK_KINDS.get(kind)
    if impl is None or not hasattr(impl, "closes_on_tool_status"):
        return tasks
    if not impl.closes_on_tool_status(status):
        return tasks
    return tuple(task for task in tasks if task.kind != kind)


def open_question(tasks: Any) -> dict[str, Any] | None:
    """The stock question as the structured object the parser reads (PR #1247 round 8).

    `kind` is "quantities" while the quantities are still asked and "last_answer"
    once the check is answered (a follow-up may revise it, "3 for all of them"); a parked
    check is not offered. `asked_qty` is present only after "Is N for all K products, or
    for one of them?". `items`
    are the question's numbered lines in their fixed order, `qty` null while owed, and
    `owed` names those positions. The parser answers it in `open_question_answer`.
    None with no stock task. Reads a `Task` or its wire dict, like `hint_lines`.
    """
    for row in tasks or ():
        if _value(row, "kind") != "stock_qty":
            continue
        task = row if isinstance(row, Task) else task_from_wire(row)
        if task is None or not task.slots or task.status == PARKED:
            # A parked check is not the question on the table (review S3).
            continue
        items = [
            {"position": i, "code": slot.label, "qty": _number(slot.value)}
            for i, slot in enumerate(task.slots, 1)
        ]
        return {
            "kind": LAST_ANSWER if task.status == ANSWERED else QUANTITIES,
            "items": items,
            "owed": [item["position"] for item in items if item["qty"] is None],
            **({"asked_qty": task.asked_qty} if task.asked_qty is not None else {}),
        }
    return None


def hint_lines(tasks: Any, *, open_question_shown: bool = True) -> list[str]:
    """One `Open task: ...` line per task, most recently touched first.

    Reads a task ROW, which may be a `Task` or the dict it is stored as - the parser
    block is built from whichever the caller holds.
    """
    rows = list(tasks or [])
    rows.sort(key=lambda row: _value(row, "touched_at_turn", 0) or 0, reverse=True)
    lines: list[str] = []
    for row in rows:
        impl = TASK_KINDS.get(_value(row, "kind"))
        if impl is None or not hasattr(impl, "hint"):
            continue
        as_task = row if isinstance(row, Task) else task_from_wire(row)
        if as_task is None:
            continue
        lines.append(impl.hint(as_task, open_question_shown=open_question_shown))
    return lines
