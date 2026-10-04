"""Collect the REQUIRED fields an ask needs before it runs (LOWSTOCK-FILTER-ASK, owner Q2).

`documentation/plans/chatbot/lowstock-filter-ask-behaviour-card.md`. One helper, shared by
every ask that must not run until some fields are settled (the low stock report today,
IDEATION-CAPTURE #1444 next). It knows nothing about any one ask: an ask type registers its
required fields, and the helper owns the rest.

The rule, per field, in the order the ask type lists them:

1. given in the message and valid -> taken, nothing asked;
2. "all" (the message said "all categories", or the reply is "all") -> settled as `ALL`,
   where the field allows it;
3. otherwise -> asked, ONE field per reply, with the field's own question.

Changing what an ask requires is config: add or remove a `FieldSpec` on its `AskType`.

API
---

* `FieldSpec(name, noun, question, resolve, allow_all=True, required=True)` - one field.
  `resolve(db, word, extras) -> Resolved` reads a typed word: `Resolved("ok", value, label)`,
  `Resolved("ambiguous", options=((value, label), ...))` (the helper lists them with numbers),
  or `Resolved("unknown")`. `extras` is the ask's carried context (below), so a field can be
  read in the light of another word (the low stock report's brand narrows its category).
  `required=False` is a field that is NEVER asked: taken when the message gives it (a
  numbered pick when the word names several), absent otherwise, and an unknown word is
  simply not taken. Flipping it to True is how an ask starts requiring the field.
* `register(AskType(name, fields, reroute, cancelled, give_up))` - one ask, registered
  once at import (`ASKS`). `reroute` is laid over the
  parser verdict when a reply answers the open question, so the turn goes back to the same
  ask (for the low stock report: its intent and domain). `give_up` may hold `{word}`.
* `collect(db, ask, *, given=None, slot=None, reply=None, extras=None) -> Outcome`.
  A fresh ask passes `given` (field name -> the raw word the message named, `ALL`, or a
  `Resolved` the caller already settled itself);
  an answering turn passes the `slot` it got back and the `reply` text. `extras` is
  anything the ask wants carried across the question untouched (the low stock report's
  supplier and grouping words). `Outcome.done` -> every required field is settled, run with
  `Outcome.values` (`{name: {"value", "label"}}`). Otherwise send `Outcome.reply` and keep
  `Outcome.slot` (None when nothing stays open: cancelled or given up).
* `reply_verdict(verdict, slot, text, *, asks=ASKS) -> (verdict, rule)` - the engine seam, read
  before anything routes the message. The open question is stated to the parser as the turn's
  `Open question:` object (`turn/question.of_required_ask`), so the PARSER says whether the
  message answers it (LOWSTOCK-SEMANTIC, crew ruling Q4, 4 Oct 2026): a declared
  `open_question_answer` (fill / pick / all / done / cancel) is the answer, and the verdict is rerouted to the ask, its entities cleared, `is_affirmative` and
  `topic_reset` cleared, and `required_ask` / `required_ask_reply` / `required_ask_answer`
  carry the slot, the text and the parser's answer to the lane. Anything else (a new question,
  "clear", an empty message) drops the question (`required_ask_dropped`): a new question
  always wins. Never the message's length or punctuation. `collect(..., cancel=True)` is the
  parser's declared cancel. An optional field's pick also takes "all" / "none", and two misses
  on it settle it as "no filter" and go on.

The slot is one turn long: the engine consumes it on the next message, and the lane that
asks again hands a fresh one back (`required_ask` on its envelope).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services.chatbot import jsc

ALL = "all"

#: Words that settle a field as "all" (lower-cased, whitespace-folded). "all <noun>" and
#: "all <noun>s" are accepted too (`_is_all`).
ALL_WORDS = frozenset({"all", "any", "everything", "every", "semua", "全部", "all of them"})
#: Verdict keys only the engine sets (`reply_verdict`, and an ask's own pre-lane reading
#: such as `low_stock_ask.take_entities`). The engine strips them off the parser's output
#: before it reads anything, so a slot can never be forged through the parser.
ENGINE_KEYS = frozenset({"required_ask", "required_ask_reply", "required_ask_answer", "low_stock_frame"})
#: An OPTIONAL field also takes these as "no filter" (it was never required).
NONE_WORDS = frozenset({"none", "no", "no filter", "skip"})
CANCEL_WORDS = frozenset({"cancel", "stop", "never mind", "nevermind", "forget it", "batal"})
MAX_MISSES = 2
#: `open_question_answer.mode` values that ANSWER the open question (the parser's own
#: reading; LOWSTOCK-SEMANTIC, crew ruling Q4 4 Oct 2026). `None` answers nothing.
ANSWER_MODES = frozenset({"fill", "pick", "all", "done", "cancel"})


@dataclass(frozen=True)
class Resolved:
    status: str  # "ok" | "ambiguous" | "unknown"
    value: Any = None
    label: str = ""
    options: tuple[tuple[Any, str], ...] = ()


@dataclass(frozen=True)
class FieldSpec:
    name: str
    noun: str
    question: str
    resolve: Callable[[Any, str, dict[str, Any]], Resolved]
    allow_all: bool = True
    required: bool = True
    #: An OPTIONAL field whose word the message named but nothing matched is said and
    #: asked once (LOWSTOCK-SEMANTIC Q3); a second miss settles it as "no filter" and the
    #: miss is kept on the value (`missed`) so the reply can say so. False: dropped quietly.
    ask_unknown: bool = False
    #: The first line of a numbered pick, when "Which <noun> do you mean?" does not fit.
    pick_head: str | None = None


@dataclass(frozen=True)
class AskType:
    name: str
    fields: tuple[FieldSpec, ...]
    reroute: dict[str, Any]
    cancelled: str
    give_up: str


#: Every registered ask type, by name (`register`). The engine seam reads the open slot's
#: ask here, so an ask registers once at import and needs no engine change.
ASKS: dict[str, AskType] = {}


def register(ask: AskType) -> AskType:
    ASKS[ask.name] = ask
    return ask


@dataclass
class Outcome:
    values: dict[str, dict[str, Any]] = field(default_factory=dict)
    reply: str | None = None
    slot: dict[str, Any] | None = None
    extras: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False

    @property
    def done(self) -> bool:
        return self.reply is None and not self.cancelled


def _norm(text: str | None) -> str:
    return " ".join((text or "").split()).strip().lower().rstrip(".!")


def _plural(noun: str) -> str:
    return noun[:-1] + "ies" if noun.endswith("y") else noun + "s"


def _is_all(text: str, spec: FieldSpec) -> bool:
    word = _norm(text)
    return word in ALL_WORDS or word in {f"all {spec.noun}", f"all {_plural(spec.noun)}"}


def _miss_line(word: str, spec: FieldSpec) -> str:
    return f"I don't know '{word}' as a {spec.noun}.\n\n{spec.question}"


def _pick_line(spec: FieldSpec, options: list[list[Any]]) -> str:
    takes_all = spec.allow_all or not spec.required
    head = spec.pick_head or (
        f"Which {spec.noun} do you mean? Reply with a number" + (' or "all":' if takes_all else ":")
    )
    return "\n".join([head, *[f"{i}. {label}" for i, (_value, label) in enumerate(options, start=1)]])


def _slot(ask: AskType, values: dict, extras: dict, *, asking: str, options=None, misses: int = 0) -> dict:
    return {
        "ask": ask.name,
        "values": values,
        "asking": asking,
        "options": [list(o) for o in options] if options else [],
        "misses": misses,
        "extras": extras,
    }


def _settle(db: Any, spec: FieldSpec, word: str, extras: dict[str, Any]) -> Resolved:
    if _is_all(word, spec) or (not spec.required and _norm(word) in NONE_WORDS):
        return Resolved("ok", value=ALL, label=ALL) if spec.allow_all or not spec.required else Resolved("unknown")
    return spec.resolve(db, word, extras)


def _from_pick(spec: FieldSpec, options: list[list[Any]], word: str) -> Resolved | None:
    """A reply to a numbered list: a number in range, or an option's own label."""
    text = _norm(word)
    if re.fullmatch(r"\d+", text):
        n = int(text)
        if 1 <= n <= len(options):
            value, label = options[n - 1]
            return Resolved("ok", value=value, label=label)
        return Resolved("unknown")
    for value, label in options:
        if _norm(str(label)) == text:
            return Resolved("ok", value=value, label=label)
    return None


def collect(
    db: Any,
    ask: AskType,
    *,
    given: dict[str, Any] | None = None,
    slot: dict[str, Any] | None = None,
    reply: str | Resolved | None = None,
    extras: dict[str, Any] | None = None,
    cancel: bool = False,
) -> Outcome:
    values: dict[str, dict[str, Any]] = dict((slot or {}).get("values") or {})
    carried = dict((slot or {}).get("extras") or {})
    carried.update(extras or {})
    specs = {s.name: s for s in ask.fields}

    if slot is not None and reply is not None:
        said_reply = reply.label if isinstance(reply, Resolved) else reply
        if cancel or (isinstance(reply, str) and _norm(reply) in CANCEL_WORDS):
            return Outcome(values=values, reply=ask.cancelled, slot=None, extras=carried, cancelled=True)
        spec = specs.get(jsc.js_string(slot.get("asking")))
        if spec is not None:
            options = list(slot.get("options") or [])
            if isinstance(reply, Resolved):
                # The caller already read the answer (the parser's own words, resolved).
                got = reply
            else:
                got = _from_pick(spec, options, reply) if options else None
                if got is None:
                    got = _settle(db, spec, reply, carried)
            if got.status == "ok":
                values[spec.name] = {"value": got.value, "label": got.label}
            elif got.status == "ambiguous" and not (options and int(slot.get("misses") or 0) + 1 >= MAX_MISSES):
                # A pick answered with a word that needs another pick is counted as a miss,
                # so it never loops (never-stuck rule); a free question's first pick is not.
                opts = [list(o) for o in got.options]
                misses = int(slot.get("misses") or 0) + (1 if options else 0)
                return Outcome(values=values, reply=_pick_line(spec, opts),
                               slot=_slot(ask, values, carried, asking=spec.name, options=opts,
                                          misses=misses), extras=carried)
            else:
                word = " ".join(str(said_reply or "").split())
                misses = int(slot.get("misses") or 0) + 1
                if misses >= MAX_MISSES and not spec.required:
                    # An optional field was only ever a pick over a word the message
                    # gave: two misses settle it as "no filter" and the ask goes on,
                    # keeping the word so the reply can say it was not found.
                    values[spec.name] = {"value": ALL, "label": ALL, "missed": word}
                    return collect(db, ask, given=given, slot={**slot, "values": values, "asking": None},
                                   extras=carried)
                if misses >= MAX_MISSES:
                    return Outcome(values=values, reply=ask.give_up.format(word=word), slot=None, extras=carried)
                return Outcome(values=values, reply=_miss_line(word, spec),
                               slot=_slot(ask, values, carried, asking=spec.name, options=options, misses=misses),
                               extras=carried)

    for spec in ask.fields:
        if spec.name in values:
            continue
        word = (given or {}).get(spec.name)
        if word == ALL and spec.allow_all:
            values[spec.name] = {"value": ALL, "label": ALL}
            continue
        got = word if isinstance(word, Resolved) else None
        if got is None and isinstance(word, str) and word.strip():
            got = _settle(db, spec, word, carried)
        if got is not None:
            if got.status == "ok":
                values[spec.name] = {"value": got.value, "label": got.label}
                continue
            if got.status == "ambiguous":
                opts = [list(o) for o in got.options]
                return Outcome(values=values, reply=_pick_line(spec, opts),
                               slot=_slot(ask, values, carried, asking=spec.name, options=opts), extras=carried)
            said = " ".join(word.split()) if isinstance(word, str) else " ".join(got.label.split())
            if not spec.required and not spec.ask_unknown:
                continue
            if not spec.required:
                return Outcome(values=values, reply=_miss_line(said, spec),
                               slot=_slot(ask, values, carried, asking=spec.name, misses=1), extras=carried)
            return Outcome(values=values, reply=_miss_line(said, spec),
                           slot=_slot(ask, values, carried, asking=spec.name), extras=carried)
        if not spec.required:
            continue
        return Outcome(values=values, reply=spec.question,
                       slot=_slot(ask, values, carried, asking=spec.name), extras=carried)
    return Outcome(values=values, extras=carried)


def reply_verdict(
    verdict: dict[str, Any],
    slot: dict[str, Any] | None,
    text: str,
    *,
    asks: dict[str, AskType] | None = None,
) -> tuple[dict[str, Any], str | None]:
    if not isinstance(slot, dict) or not slot.get("asking"):
        return verdict, None
    ask = (ASKS if asks is None else asks).get(jsc.js_string(slot.get("ask")))
    if ask is None:
        return verdict, "required_ask_dropped"
    # The parser was shown this question as the turn's `Open question:` object
    # (`turn/question.of_required_ask`), so whether the message answers it is ITS reading,
    # never the message's length or punctuation (LOWSTOCK-SEMANTIC, crew ruling Q4 and the
    # STUCK-QTY-LOOP pending-question rule): only a declared answer is the answer. Anything
    # else is a new question, which always wins (a whole new low stock ask included: it
    # settles from its own words), and "clear" is the parser's `topic_reset` with no answer.
    answer = verdict.get("open_question_answer")
    mode = answer.get("mode") if isinstance(answer, dict) else None
    answers = mode in ANSWER_MODES
    if not (text or "").strip() or not answers:
        return verdict, "required_ask_dropped"
    rerouted = {
        **verdict, **ask.reroute, "entities": [], "open_question_answer": None,
        "is_affirmative": None, "topic_reset": False,
        "required_ask": slot, "required_ask_reply": text,
        "required_ask_answer": dict(answer) if answers else None,
    }
    return rerouted, "required_ask_answer"
