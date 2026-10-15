"""PV2 apply_turn (CONTRACT-PV2-13oct.md section 3): ONE function for every domain.

Pure except through `resolver`. Code decides from structured fields only: no word or
fuzzy matching on the user's text (option keys and T-keys are compared EXACTLY; phrases
go to the resolver, which matches against data). Clarify, never guess: anything the
parser output and the data do not settle becomes a question with real options.

Per ask, in order (section 3):
  escalation.requested -> HandOver
  clarify.needed / unclear -> ClarifyAct
  small_talk -> state unchanged
  answers_bot_question -> exact option key of the pending question, its payload applied
  refines_held -> per-field ops against HELD
  new_topic -> held pushed onto topics, held = this ask
  switch_back -> a printed T-key swaps with held
Then the new items go to the resolver: ambiguity -> ClarifyAct, nothing -> truthful
ClarifyAct(not_found), else Lookup. The short-reply safety checks each count one MISS on
the pending question; two misses on the same question offer a hand-over (to a contact who
cannot be offered one: v1's "I still need X for Y.", and the question closes).
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.services.chatbot.pv2 import clarify_copy
from app.services.chatbot.pv2.register import shown
from app.services.chatbot.pv2.schema import Ask, Entity, Filter, ParserV2Output, Spec
from app.services.chatbot.pv2.state import (
    MISSES_BEFORE_HANDOVER,
    LAST_ROWS_CAP,
    SCALAR_PAYLOAD_KINDS,
    Action,
    ClarifyAct,
    ConversationState,
    HandOver,
    Held,
    Lookup,
    Miss,
    OfferHandover,
    Option,
    PendingQuestion,
    ResolvedRef,
    ResolvedSpec,
    SmallTalk,
    apply_payload,
    new_sid,
    push_topic,
    set_refs,
    topic_index,
)

#: The 11 Oct rule (carried + new subject -> which_held): on these domains one subject is
#: answered at a time, so a new entity of a held kind with no `refers_to_held` and op
#: `add` asks which one is meant instead of answering both.
SINGLE_SUBJECT_DOMAINS: frozenset[str] = frozenset({"product_attachment"})

#: Pending kinds whose answer is consumed outside held (ideation, a v1 open question):
#: the pick is passed on to the answer layer on the Lookup.
PASS_THROUGH_PENDING = frozenset({"idea_similar", "idea_missing_field", "idea_multi", "v1_open_question"})

#: Pending kinds whose question is an open one: a free value answers it.
FREE_VALUE_PENDING = frozenset({"idea_missing_field", "v1_open_question"})

def local_date(now: datetime) -> date:
    return now.astimezone(ZoneInfo("Asia/Kuala_Lumpur")).date() if now.tzinfo else now.date()


#: `Ask.cleared` names (section 1).
CLEARABLE = frozenset({"attachment_type", "period", "report", "specs"})


class ResolverLike(Protocol):
    """The subset of PV2-RESOLVE's `Resolver` (section 4) apply_turn calls. Resolution /
    SpecResolution are read by attribute: `status` ("exact" | "ambiguous" | "none"),
    `refs` / `specs`, `options`, `phrase`."""

    def resolve_entities(self, ents: list[Entity], *, held: Held) -> list[Any]: ...

    def resolve_specs(self, specs: list[Spec], *, held: Held) -> list[Any]: ...

    def resolve_attachment_type(self, key: str | None, *, held: Held) -> Any: ...


class ClarifyPolicy:
    """Ask-or-default rows (PR #1570 shape). Tonight a constant table whose default is
    ask: a single candidate is still asked unless a row says default."""

    def __init__(self, rows: dict[tuple[str, str | None], Literal["ask", "default"]] | None = None):
        self.rows = dict(rows or {})

    def decide(self, about: str, domain: str | None) -> Literal["ask", "default"]:
        return self.rows.get((about, domain)) or self.rows.get((about, None)) or "ask"


DEFAULT_POLICY = ClarifyPolicy()


class _Miss(Exception):
    """A short-reply safety check failed: re-ask (one MISS)."""

    def __init__(
        self, about: str = "other", *, nothing_held: bool = False, entity: Entity | None = None,
        origin: dict | None = None,
    ):
        super().__init__(about)
        self.about = about
        self.nothing_held = nothing_held
        self.entity = entity  # the entity whose miss raised it (RG-7c)
        self.origin = origin  # the open question re-asked with this origin, whatever the act (RG-1b)


#: The acts that can answer (or fail to answer) an open question.
_ANSWER_ACTS = frozenset({"answers_bot_question", "unclear"})


def apply_turn(
    state: ConversationState,
    out: ParserV2Output,
    *,
    resolver: ResolverLike,
    policy: ClarifyPolicy = DEFAULT_POLICY,
    can_offer_handover: bool = True,
    now: datetime | None = None,
    domain_teams: dict[str, str] | None = None,
    offer_mode: Literal["offer", "refer", "none"] = "offer",
    roster_for: Any = None,
    dym_tags: Any = None,
    stock_availability_only: bool = False,
    language: str | None = None,
    granted_reveals: frozenset[str] | None = None,
) -> tuple[ConversationState, list[Action]]:
    """Apply one parser output to the state; one Action per ask (section 3).

    `can_offer_handover` is False for staff and for a contact whose escalation is barred
    (`turn/state.offers_escalation`, owner rulings #1262 slice 11 and ESCALATION-CONTROL):
    their second miss re-asks instead of offering a hand-over. `granted_reveals` are the
    contact's grants: a domain menu offers no domain the contact may not ask (`_domain_options`); None (a caller
    that judges no grants here, never the turn) filters nothing."""
    st = state.model_copy(deep=True)
    st.turn_no = state.turn_no + 1
    # What a not-found document says after its sentence: the offer, v1's refer line, or nothing.
    mode = offer_mode if offer_mode != "offer" else ("offer" if can_offer_handover else "none")
    doc_offer = _DocOffer(mode, domain_teams, roster_for, dym_tags)
    doc_offer.availability_only = stock_availability_only
    language = language or out.language  # R5-6: the turn's language when the caller computed it
    today = local_date(now) if now is not None else None
    actions: list[Action] = []

    if out.escalation is not None and out.escalation.requested:
        st.pending = None
        st.last_lookup_ask = st.last_reply = None
        st.last_menu = []  # R6 B: this turn answered no question
        return st, [HandOver(ask_index=0, team_key=out.escalation.team_key)]

    # A clarify flag beside a new_topic ask does not re-ask an OPEN question as a miss: the new
    # topic replaces it and its own policy asks what it still needs.
    phrase = out.clarify.phrase
    carried = bool(phrase) and (
        out.clarify.about == "entity"
        and any(phrase in (e.phrase, e.doc_number, e.key) for a in out.asks for e in a.entities)
        or out.clarify.about == "spec_attribute" and any(phrase == s.phrase for a in out.asks for s in a.specs)
    )  # the flag is about an entity or spec an ask carries: the ask's own resolution answers it (RG-2)
    flagged = out.clarify.needed and not (
        carried
        or any(a.act == "new_topic" for a in out.asks) and st.pending is not None
        or any(a.act == "switch_back" and a.switch_back_to is None for a in out.asks) and len(st.topics) == 1
        or any(_picks_open_question(a, st.pending) for a in out.asks)  # R5-1: the pick is read before the flag
        or any(_moves_on_from_a_confirm(a, st.pending) for a in out.asks)  # R6 C: a new ask on the open confirm
    )
    # R6 A: a flagged turn whose asks carry something (an entity, a domain, a period, ...) runs them, so held keeps
    # what the parse settled; the flag's own question is asked only for what they leave open (`_flag_question`).
    flag_about = (out.clarify.about or "other") if flagged and any(_carries(a) for a in out.asks) else None
    if (flagged or not out.asks) and flag_about is None:
        about = _entity_about(st, out.clarify.about or "other")
        if st.pending is not None:
            # A garbled answer to the open question re-asks it with its real options and
            # counts a MISS (two -> the hand-over offer), never an option-less new question.
            act = _miss(st, 0, st.pending, _Miss(about), language, can_offer_handover, mode)
        else:
            # R6 A: a domain or open question offers the domains that take what is held.
            options = _options_for(st, about) or (
                _domain_options(st, granted_reveals, dealer=stock_availability_only)
                if about in ("domain", "other") else []
            )
            # An entity question without options never quotes a phrase (no list to pick from).
            phrase = out.clarify.phrase if options or about != "which_held" else None
            act = _clarify(st, 0, about, phrase, language, options=options)
            act.pending.origin = _origin_of(out.asks[0]) if out.asks else None
        st.pending = None if getattr(act, "closes", False) else act.pending
        st.last_lookup_ask = None
        st.last_menu = []  # R6 B: this turn answered no question
        st.last_reply = _outcome([act])
        return st, [act]

    first_question: PendingQuestion | None = None
    pending_in = st.pending
    held_in = st.held.model_copy(deep=True)  # the subject the open question was asked about
    answered_pending = False
    # T-keys name the topics MEMORY printed, not ones an earlier ask of this message pushed.
    printed_topics = [t.model_copy(deep=True) for t in st.topics]
    for i, ask in enumerate(out.asks):
        if _bare_switch_back_beside_a_new_ask(out.asks, i, printed_topics):
            continue  # it adds nothing: the other ask carries the subject in that same domain
        if reads_as_answer(ask, pending_in, held_in):  # the held the pre-check judged with (start of turn)
            ask = ask.model_copy(update={"act": "answers_bot_question"})  # R5-1: the open list owns the number
        held_before, topics_before = st.held.model_copy(deep=True), list(st.topics)
        try:
            action, answered = _one_ask(
                st, i, ask, pending_in, out, resolver, policy, language, today, printed_topics,
                doc_offer,
            )
        except _Miss as miss:
            st.held, st.topics = held_before, topics_before
            if ask.act == "unclear" and _carries(ask):
                st.held = _unclear_frame(st.held, ask)  # R6 A: kept while its question is asked
            # Only an answer (or an unclear reply) re-asks the open question; a new
            # question that missed asks its own and replaces it.
            answering = ask.act in _ANSWER_ACTS and not (ask.act == "unclear" and _carries(ask))
            reask = pending_in if answering or miss.origin is not None else None
            if reask is not None and _is_offer(reask) and miss.origin is None:
                # R6 rule 1: a reply that answers none of an offer never prints it again: the offer is dropped and
                # the reply is asked about afresh.
                reask, miss = None, _Miss("domain")
            action = _miss(
                st, i, reask, miss, language, can_offer_handover, mode, granted=granted_reveals,
                dealer=stock_availability_only,
            )
            if reask is None and pending_in is not None and isinstance(action, ClarifyAct):
                action.reask = False  # its own question, not a retry of the open one
            if isinstance(action, ClarifyAct) and reask is None:
                action.pending.origin = _origin_of(ask)
                if miss.entity is not None:
                    action.pending.raised_by = _entity_id(miss.entity)
            if isinstance(action, ClarifyAct) and miss.origin is not None:
                action.pending.origin = miss.origin
        else:
            answered_pending = answered_pending or answered
            if flag_about is not None and isinstance(action, Lookup):
                action = _flag_question(
                    st, i, action, ask, flag_about, language, granted_reveals, dealer=stock_availability_only
                )
            action = _repeat_is_a_miss(st, action, pending_in, held_in, language, can_offer_handover, mode)
            _keep_origin(action, ask, st.held, pending_in, answered)
            if (
                isinstance(action, Lookup)
                and action.picked is None
                and pending_in is not None
                and pending_in.kind == "v1_open_question"
                and any(e.quantity is not None for e in ask.entities)
            ):
                products = action.held.refs.get("product", [])
                if (
                    products
                    and [r.key for r in products] == [r.key for r in held_before.refs.get("product", [])]
                    and all(r.quantity is not None for r in products)
                ):
                    action.requantify = True  # v1's requantify rule reads it, not a new subject
        actions.append(action)
        question = getattr(action, "pending", None)
        if question is not None and first_question is None and not getattr(action, "closes", False):
            first_question = question
        if isinstance(action, HandOver):
            break

    closed = any(getattr(a, "closes", False) for a in actions)
    if first_question is not None:
        st.pending = first_question
    elif answered_pending or closed or any(isinstance(a, (Lookup, HandOver)) for a in actions):
        st.pending = None
    st.last_lookup_ask = _last_lookup_ask(actions, pending_in if answered_pending else None)
    st.last_menu = _menu_refs(pending_in) if answered_pending else []
    if not st.held.is_empty() and st.held.sid is None:
        st.held.sid = new_sid(st)  # a pick answered without `_commit` (a pre-R2 held, a v1 or "all" pick)
    st.last_reply = _outcome(actions)
    return st, actions


#: R4a: a not-found phrase in LAST REPLY is the contact's words: one line, capped.
PHRASE_CAP = 40


def _outcome(actions: list[Action]) -> str | None:
    """R4a: what this turn's reply does (MEMORY's LAST REPLY): its first question (`not found <words>` when the
    question says nothing was found, the words being the contact's own, collapsed to one line and capped; else
    `asked <kind>`), else the domain its last lookup answered, else None (small talk, a hand-over)."""
    question = next((a for a in actions if isinstance(a, (ClarifyAct, OfferHandover))), None)
    if question is not None:
        missed = question.phrase if isinstance(question, ClarifyAct) and question.not_found else None
        words = " ".join((missed or "").split())[:PHRASE_CAP]
        return f"not found {words}" if words else f"asked {question.pending.kind}"
    lookups = [a for a in actions if isinstance(a, Lookup)]
    if not lookups:
        return None
    return f"answered {lookups[-1].domain}" if lookups[-1].domain else "answered"


# --------------------------------------------------------------------------- #
# One ask
# --------------------------------------------------------------------------- #


_FILTER_KINDS = frozenset({"brand", "product_class", "product_type", "attachment_type"})


def _new_subject_in_a_new_domain(st: ConversationState, ask: Ask) -> bool:
    """A refines_held ask that changes the domain AND names a NEW subject (an entity with a phrase, key
    or doc number that is not the held one) or opens a report on a subject without one starts a new
    topic (RG-7d: an entity that refers to held still carries). Structured fields only."""
    if ask.act != "refines_held" or ask.row_pick is not None or not ask.domain or st.held.is_empty():
        return False
    if ask.domain == st.held.domain:
        return False
    if ask.report is not None and st.held.report is None:
        return True
    held_keys = {str(v) for refs in st.held.refs.values() for r in refs for v in (r.key, r.phrase) if v}
    for e in ask.entities:
        named = e.key or e.phrase or e.doc_number
        if e.refers_to_held or e.op == "clear" or not named or e.kind in _FILTER_KINDS:
            continue  # a brand or class narrows the held subject, it is not a new one
        if e.kind not in st.held.refs or str(named) not in held_keys:
            return True
    return False


def _is_a_new_ask(ask: Ask) -> bool:
    """An option-less answer that carries its own subject (an entity naming something that is
    not the held one) or its own attachment kind is a new ask, not an answer to the offer."""
    named = any(
        not e.refers_to_held and (e.key or e.phrase or e.doc_number) and e.op != "clear" for e in ask.entities
    )
    return named or bool(ask.attachment_type)


def _moves_on_from_a_confirm(ask: Ask, pending: PendingQuestion | None) -> bool:
    """R6 C: a reply to an open confirm that picks nothing (no option key, value, number or entity) but names a
    domain or a period ("and last month?") is a new ask, never a miss on the confirm, whatever answer act the
    parser gave it (a new topic stays one): `_carried_by_the_confirm` says what it runs."""
    if pending is None or pending.kind != CONFIRM or ask.act not in ("answers_bot_question", "unclear", "refines_held"):
        return False
    answer = ask.answer
    picks = ask.row_pick is not None or answer is not None and bool(answer.option_key or answer.free_value)
    return not (picks or ask.entities) and bool(ask.domain or ask.period)


def _carried_by_the_confirm(i: int, ask: Ask, held: Held, pending: PendingQuestion) -> ClarifyAct | None:
    """R6 C (lead ruling 15 Oct): nothing is applied until a confirm is confirmed, and v1 is never handed a Lookup
    that drops its candidate ("Customer: all"). A new ask on an open confirm runs on held only when held already
    has the candidate (None: the caller refines); else the same yes/no confirm stays open, its original ask
    taking this one's domain, period and status, so a "yes" runs it on the candidate."""
    guess = pending.options[0].payload
    keys = {str(k) for k in guess.get("keys") or [guess.get("key")]}
    if keys <= {r.key for r in held.refs.get(str(guess.get("kind")), [])}:
        return None
    origin = valid_origin(pending.origin) or ask
    update = {"domain": ask.domain or origin.domain, "period": ask.period or origin.period,
              "status": ask.status or origin.status}
    kept = pending.model_copy(deep=True, update={"origin": _origin_of(origin.model_copy(update=update))})
    return ClarifyAct(ask_index=i, about="confirm", pending=kept)


#: Pending kinds a bare number never answers: a hand-over is accepted only by an explicit yes or a typed
#: option, and an idea's missing field takes the number as its value.
NO_NUMBER_PENDING = frozenset({"offer_handover", "offer_doc_handover", "idea_missing_field"})


def _number(ask: Ask) -> int | None:
    """The printed position an ask names: a free value that is a bare number, else its row pick."""
    free = ask.answer.free_value if ask.answer is not None else None
    return int(free) if isinstance(free, str) and free.isdecimal() else ask.row_pick


def _numbered(pending: PendingQuestion | None) -> list[Option]:
    """R5-1: the options a printed number names, in printed order: those keyed by their number (an entity
    question, a did-you-mean, a v1 menu), else every option of a PV2 question by position (a confirm too: it prints
    the group name only, so a number is its stored option as MEMORY numbers it, 1 yes and 2 no; R6 lead ruling).
    None for a hand-over offer or a question that takes a number as its value."""
    if pending is None or pending.kind in NO_NUMBER_PENDING:
        return []
    keyed = [o for o in pending.options if o.key.isdigit()]
    return keyed if keyed or pending.kind == "v1_open_question" else list(pending.options)


def _records(pending: PendingQuestion | None) -> list[Option]:
    """The records a question offers: a confirm's guessed family as its accounts (R5-3: a reply naming one account
    picks it), else its numbered options."""
    if pending is not None and pending.kind == CONFIRM and pending.options:
        return _accounts(pending.options[0]) or pending.options[:1]
    return _numbered(pending)


def _nth(options: list[Option], n: int) -> Option | None:
    """The option printed as number `n` (its key when options are keyed by number, else its position)."""
    if any(o.key.isdigit() for o in options):
        return next((o for o in options if o.key == str(n)), None)
    return options[n - 1] if 1 <= n <= len(options) else None


def _accounts(option: Option) -> list[Option]:
    """R5-3: a family option's accounts, one option per key keyed by its printed number and labelled by the
    account's own name when the resolver gave them (else the family's); [] for one account."""
    keys = [str(k) for k in option.payload.get("keys") or []]
    if len(keys) < 2:
        return []
    names = [str(n) for n in option.payload.get("labels") or []]
    names = names if len(names) == len(keys) else [option.label] * len(keys)
    base = {k: v for k, v in option.payload.items() if k not in ("keys", "labels")}
    return [
        Option(key=str(n), label=name, payload={**base, "key": key, "label": name})
        for n, (key, name) in enumerate(zip(keys, names), start=1)
    ]


#: R5-3: the questions whose options are records a reply may name instead of picking (a family confirm, an
#: account menu): the resolver reads the named entity and only what it finds among the options is picked.
SCOPED_PICK_PENDING = frozenset({"clarify_confirm", "clarify_entity"})


def _scoped_pick(ask: Ask, pending: PendingQuestion, held: Held, resolver: ResolverLike) -> tuple[Option | None, Ask]:
    """R5-3: a reply naming an entity of the question's kind (no key, no number): the resolver reads it, and the
    one option whose keys hold what it found is picked, narrowed to those keys (one account of a family), so the
    family is never guessed again. The named entity is consumed. (None, ask) when it finds no single option."""
    options = _records(pending)
    kinds = {o.payload.get("kind") for o in options}
    ent = next((e for e in ask.entities if e.kind in kinds and (e.key or e.phrase) and e.op != "clear"), None)
    if ent is None:
        return None, ask
    res = resolver.resolve_entities([ent], held=held)[0]
    labels = {r.key: r.label for r in getattr(res, "refs", None) or []}
    found = list(labels) or [
        str(k) for o in getattr(res, "options", None) or [] for k in o.payload.get("keys") or [o.payload.get("key")]
    ]
    hits = []
    for o in options:
        covered = [str(k) for k in o.payload.get("keys") or [o.payload.get("key")]]
        mine = [k for k in covered if k in found]
        if mine:
            hits.append((o, mine))
    if len(hits) > 1:
        # R5 fix 1: a containing search finds every account the words are inside ("A/C I" is inside "[A/C II]"):
        # only the account whose ledger marker names the level the phrase names (ledger_family's own parsing on
        # both sides) is meant.
        from app.services.ledger_family import account_level

        level = account_level(ent.phrase or ent.key)
        hits = [(o, keys) for o, keys in hits if level is not None and account_level(o.label) == level]
    if len(hits) != 1:
        return None, ask
    option, keys = hits[0]
    payload = {k: v for k, v in option.payload.items() if k not in ("keys", "labels")}
    payload.update(key=keys[0], label=labels.get(keys[0], option.label) if len(keys) == 1 else option.label)
    if len(keys) > 1:
        payload["keys"] = keys
    rest = ask.model_copy(update={"entities": [e for e in ask.entities if e is not ent]})
    return option.model_copy(update={"payload": payload}), rest


def _picks_open_question(ask: Ask, pending: PendingQuestion | None) -> bool:
    """R5-1: the ask names a printed number while the open question numbers its options: the number is that
    question's (in range or not), never a row of the last answer, whatever act the parser gave it. R5-5: an
    option key the open question offered, with no new subject beside it, answers it whatever the act (the
    parser's answer wins over its act, so the held question kind is kept)."""
    if _number(ask) is not None and _numbered(pending):
        return True
    key = ask.answer.option_key if ask.answer is not None else None
    return (
        pending is not None and key is not None and any(o.key == key for o in pending.options)
        and not _is_a_new_ask(ask)
    )


def answers_by_number(ask: Ask, pending: PendingQuestion | None) -> bool:
    """R5-1: a printed number on a question that numbers its options: it picks that question's option
    (`number_runs_question` says when its own ask then runs)."""
    return _number(ask) is not None and bool(_numbered(pending))


def _keyed_pick(ask: Ask, pending: PendingQuestion) -> Option | None:
    """The option an answer's option key picks: its key, the carry question's "all" (its last option), or on a
    candidate question (keyed by position) the code its payload holds."""
    key = ask.answer.option_key if ask.answer is not None else None
    if key is None:
        return None
    picked = next((o for o in pending.options if o.key == key), None)
    if key == "all" and pending.kind == CARRY_SCOPE:
        picked = pending.options[-1]  # "all <kind>s": the parser's "all" drops the carried kind (v1 carry_scope_keeps)
    if picked is None and pending.kind in ("clarify_entity", "clarify_did_you_mean"):
        picked = next((o for o in pending.options if o.payload.get("key") == key), None)
    if picked is None and key.isdecimal() and not any(o.key.isdigit() for o in pending.options):
        picked = _nth(_numbered(pending), int(key))  # R6: MEMORY's printed position echoed as the key
    return picked


def number_runs_question(ask: Ask, pending: PendingQuestion | None) -> bool:
    """R5 fix 5: the ONE condition under which an answer's number runs the open question's own ask (`_answer`'s
    number branch, where the parser's domain is dropped); the grant pre-check judges by it too. Not when the
    answer is a refine (`answer_is_a_refine`: it runs in the parser's domain) or an option key picks."""
    if pending is None or ask.act != "answers_bot_question" or answer_is_a_refine(ask, pending):
        return False
    return _keyed_pick(ask, pending) is None and answers_by_number(ask, pending)


def reads_as_answer(ask: Ask, pending: PendingQuestion | None, held: Any) -> bool:
    """R5-1 / R5-5: a refine or a new topic that picks the open question is read as its answer (apply_turn and the
    grant pre-check read it alike, both with the held the turn started with). A number on a numbered question is
    its answer whatever the domain (R5 fix 2). An option key is not when a new topic names a domain other than the
    question's own (its origin's, else held's): "no, what promotions are on" stays a new ask (R5 fix 1)."""
    if ask.act not in ("refines_held", "new_topic") or not _picks_open_question(ask, pending):
        return False
    if answers_by_number(ask, pending):
        return True
    origin = valid_origin(pending.origin) if pending is not None else None
    own = origin.domain if origin is not None and origin.domain else getattr(held, "domain", None)
    return not (ask.act == "new_topic" and ask.domain and ask.domain != own)


def _roster_kind(pending: PendingQuestion) -> str | None:
    """R5-4: the entity kind of a v1 roster (its options' `entity_type`, as v1 recorded them): a free value
    answering it goes to v1 as a typed value of that kind and v1's own roster matcher decides. None for any
    other question."""
    from app.services.chatbot.turn.pending import is_roster

    if pending.kind != "v1_open_question" or not pending.v1_kind or not is_roster(pending.v1_kind):
        return None
    kinds = {o.payload.get("entity_type") for o in pending.options} - {None}
    return kinds.pop() if len(kinds) == 1 else None


def answer_is_a_refine(ask: Ask, pending: PendingQuestion | None) -> bool:
    """An answer that names no option but carries a structured quantity on the held product
    (under a v1 product pick) or a structured period (under a report question) is a refine of
    the held subject, not an answer to the open question. Structured fields only."""
    if pending is None or ask.answer is None or ask.answer.option_key is not None:
        return False
    if pending.kind in ("v1_open_question", "offer_handover", "offer_doc_handover", CONFIRM) and _is_a_new_ask(ask):
        return True
    if (
        pending.kind == "v1_open_question"
        and ask.answer.free_value is None
        and any(e.quantity is not None and e.refers_to_held and not e.key and not e.phrase for e in ask.entities)
    ):
        return True
    return pending.kind in ("clarify_report_measure", "clarify_report_breakdown", "clarify_report_period") and ask.period is not None


#: Domains whose answer reads a quantity (v1: "a quantity is a stock question"). R3 registry.
QUANTITY_DOMAINS: frozenset[str] = frozenset({"inventory"})


def _count_only(e: Entity) -> bool:
    """A count that names nothing: a quantity, no key, phrase or document number."""
    return e.quantity is not None and not (e.key or e.phrase or e.doc_number)


def _held_count(st: ConversationState, ask: Ask, pending: PendingQuestion | None) -> Ask | None:
    """RG-4 (#1617): an ask whose only entities are counts naming nothing counts the held subject. Returned
    reshaped: on a new topic the counts point back at held, and with no domain named a held domain that
    reads no quantity becomes the stock ask. An answer counts only with nothing pending. None otherwise."""
    if (
        ask.act == "switch_back" or (ask.act == "answers_bot_question" and pending is not None)
        or ask.row_pick is not None or ask.attachment_type or not ask.entities
        or not all(_count_only(e) and st.held.refs.get(e.kind) for e in ask.entities)
    ):
        return None
    update: dict[str, Any] = {}
    if ask.act == "new_topic":
        update["entities"] = [e.model_copy(update={"refers_to_held": True}) for e in ask.entities]
    if not ask.domain and st.held.domain not in QUANTITY_DOMAINS:
        update["domain"] = "inventory"
    return ask.model_copy(update=update)


#: RG-1 (#1570): the kinds that narrow each domain's answer, in the order a carried one is asked about (v1
#: policy rows; R3 registry). One held but left unnamed beside a newly named other is asked about, not carried
#: (a customer carries: `SILENT_CARRY_KINDS`).
CARRY_SCOPE_KINDS: dict[str, tuple[str, ...]] = {"order": ("product", "customer"), "sales": ("product", "customer")}
CARRY_SCOPE = "clarify_carry_scope"
#: Owner rule 15 Oct: a held customer carries beside a newly named product with no question (the dealer's own
#: customer too: dropping it would widen the reply to the dealer's other accounts). Only a held product beside a
#: newly named customer is asked about.
SILENT_CARRY_KINDS = frozenset({"customer"})


def _carried(i: int, ask: Ask, before: Held, held: Held) -> str | None:
    """RG-1 (#1570): the held narrowing kind A this ask leaves unnamed beside a newly named other one B, whether B
    was held before or not (RG-1c). None when the ask points back at held (`refers_to_held`, or a remove of a held
    item) or opens a report."""
    if (
        i > 0 or ask.report is not None or held.report is not None
        or any(e.refers_to_held or e.op == "remove" for e in ask.entities)
    ):
        return None
    kinds = CARRY_SCOPE_KINDS.get(held.domain or "", ())
    named = {e.kind for e in ask.entities}
    new = {e.kind for e in ask.entities if e.op in ("set", "add")}
    for kind in kinds:
        if kind not in named and before.refs.get(kind) and new & (set(kinds) - {kind}):
            return kind
    return None


def _carry(st: ConversationState, i: int, ask: Ask, before: Held, held: Held, language: str, qs: list) -> Held:
    """RG-1 / RG-1c: an ask that leaves a held kind unnamed beside a newly named other one holds only its own items,
    that kind and the period (never another held item), and asks "only A or all?" (a customer is never asked
    about: it carries): now when the ask raised no question of its own, else once those questions' picks land the
    new item (`PendingQuestion.carry`)."""
    kind = _carried(i, ask, before, held)
    if kind is None:
        return held
    keep = held.model_copy(deep=True)
    named = {e.kind for e in ask.entities}
    for k, refs in before.refs.items():
        if k != kind and k not in named:
            old = {r.key for r in refs}
            keep.refs = set_refs(keep.refs, k, [r for r in keep.refs.get(k, []) if r.key not in old], "set")
    keep.refs = set_refs(keep.refs, kind, before.refs[kind], "set")
    keep.period = held.period or before.period
    if kind in SILENT_CARRY_KINDS:
        return keep
    if qs:
        qs[0].pending.carry = kind
    else:
        question = _carry_question(st, i, keep, kind, language)
        if question is not None:
            qs.append(question)
    return keep


def _carry_question(st: ConversationState, i: int, held: Held, kind: str, language: str) -> ClarifyAct | None:
    """RG-1 (#1570): "Delivery for B: only A (from earlier) or all <A>s?" over `held`; nothing is fetched. Pick 1
    keeps A, pick 2 clears it."""
    kinds = CARRY_SCOPE_KINDS.get(held.domain or "", ())
    new = [r for k in kinds if k != kind for r in held.refs.get(k, [])]
    carried = held.refs.get(kind, [])
    if not new or not carried:
        return None
    asked, options = clarify_copy.carry_scope(
        held.domain, kind, _labels(new, language), _labels(carried, language), language
    )
    return _clarify(st, i, "carry_scope", None, language, options=options, asked=asked)


def _labels(refs: list[ResolvedRef], language: str) -> str:
    return ", ".join(dict.fromkeys(clarify_copy.safe_label(r.label, language) for r in refs))


def _carry_reply(ask: Ask, pending: PendingQuestion | None) -> dict | None:
    """RG-1b: while the carry question is open, a reply carrying no more than a period or a status (no entity,
    pick, spec, report, attachment, form or other domain) is no answer to it. Returns the question's ask with
    what the reply adds, for the pick; None for any other reply."""
    origin = valid_origin(pending.origin) if pending is not None and pending.kind == CARRY_SCOPE else None
    picks = ask.row_pick is not None or ask.answer is not None and (ask.answer.option_key or ask.answer.free_value)
    if (
        origin is None or ask.act not in ("answers_bot_question", "refines_held", "new_topic") or picks
        or ask.entities or ask.specs or ask.report or ask.low_stock or ask.attachment_type or ask.portal_form
        or ask.ideation or ask.cleared or ask.domain not in (None, origin.domain)
    ):
        return None
    return _origin_of(
        origin.model_copy(update={"period": ask.period or origin.period, "status": ask.status or origin.status})
    )


#: RG-6 (#1517 ruling): the domains whose answer needs a subject. An ask naming none is a which-product question,
#: never a tool call. R3 registry.
SUBJECT_REQUIRED_DOMAINS: frozenset[str] = frozenset({"inventory", "master_products"})


def _no_subject(held: Held, ask: Ask) -> bool:
    """RG-6: no refs, specs, report, low stock ask, period or form, in a domain that needs a subject."""
    return held.domain in SUBJECT_REQUIRED_DOMAINS and not (
        any(held.refs.values()) or held.specs or held.report or ask.low_stock or held.period or held.portal_form
    )


def _which_product(st: ConversationState, i: int, language: str) -> ClarifyAct:
    asked = clarify_copy.text("which_product", language)
    return _clarify(st, i, "entity", None, language, options=_referent_options(st, "product"), kind="product", asked=asked)


def _referent_options(st: ConversationState, kind: str) -> list[Option]:
    """R6 residual (I2): an entity question with no options of its own offers what a reference of its kind can mean
    (`_referents`: the rows the last answer printed, held, saved topics, the menu just answered); none: none. Only
    when its numbering keeps every printed row's number (a number on an open question is that question's, R5-1), so
    a number never names one record on the answer and another on the question."""
    refs = _referents(st, kind) or []
    rows = [r.label for r in shown(st)]
    if rows and [r.label for r in refs[: len(rows)]] != rows:
        return []
    return _which_held_options(refs)


def _one_ask(
    st: ConversationState,
    i: int,
    ask: Ask,
    pending: PendingQuestion | None,
    out: ParserV2Output,
    resolver: ResolverLike,
    policy: ClarifyPolicy,
    language: str,
    today: date | None,
    printed_topics: list[Held],
    can_offer: Any = None,
) -> tuple[Action, bool]:
    """(action, answered_pending). Mutates `st` (held/topics) only on success."""
    if pending is not None and _moves_on_from_a_confirm(ask, pending):
        kept = _carried_by_the_confirm(i, ask, st.held, pending)
        if kept is not None:
            if ask.act == "unclear":
                st.held = _unclear_frame(st.held, ask)  # R6 A: the confirm stays open, held keeps the parsed frame
            return kept, False
        ask = ask.model_copy(update={"act": "refines_held"})  # R6 C: held has the candidate: a refine on it
    if ask.act == "unclear" and _carries(ask):
        # R6 A: an unclear ask that carries something runs as a refine of held (a new topic when nothing is held),
        # so held keeps its entities, domain and period.
        ask = ask.model_copy(update={"act": "new_topic" if st.held.is_empty() else "refines_held"})
    if ask.act == "unclear":
        about = out.clarify.about or "other"
        if pending is not None:
            raise _Miss(about)
        act = _clarify(st, i, about, out.clarify.phrase, language, options=_options_for(st, about))
        act.pending.origin = _origin_of(ask)  # held did not take the ask: a pick completes all of it
        return act, False

    if ask.act == "small_talk":
        return SmallTalk(ask_index=i), False

    # R5-1: a row pick no open question owns names a shown row whatever the act (an answer to no question refines).
    on_rows = ask.act in ("refines_held", "new_topic") or ask.act == "answers_bot_question" and pending is None
    picked = shown_row(shown(st), ask) if on_rows else None
    if picked is not None:
        # R4a: a pick of a printed row is that row named by its printed code, before the act is decided, so it
        # routes as a copied code does. A row the resolver cannot look up by label (a report breakdown's month,
        # brand, category) is no record to look up: the pick misses, as before.
        if picked.kind not in LABEL_KINDS:
            raise _Miss("other")
        act = "refines_held" if ask.act == "answers_bot_question" else ask.act
        ask = ask.model_copy(update={"entities": [picked, *ask.entities], "row_pick": None, "act": act})

    if ask.act in ("refines_held", "new_topic") and not st.held.refs.get("product"):
        # R5-2 / R6 B: "this product" with no product held: what it can refer to (a shown document's lines first).
        refs = _referents(st, "product") if any(_points_back(e) for e in ask.entities if e.kind == "product") else None
        if refs == []:
            # Lines the contact may not see (dropped by the reveal filter) are never named: ask which product.
            act = _which_product(st, i, language)
            act.pending.origin = _origin_of(ask)
            return act, False
        if refs and len(refs) == 1:
            # As a shown row is: the product named by its code, so it routes as a copied code does.
            code = clarify_copy.printed_code(refs[0].key, refs[0].label, language)
            ask = ask.model_copy(update={"entities": [
                e.model_copy(update={"phrase": code, "refers_to_held": False})
                if e.kind == "product" and _points_back(e) else e
                for e in ask.entities
            ]})
        elif refs:
            act = _clarify(st, i, "entity", None, language, options=_which_held_options(refs), kind="product")
            act.pending.origin = _origin_of(ask)  # the pick completes this ask
            return act, False

    reply = _carry_reply(ask, pending)
    if reply is not None:
        raise _Miss("carry_scope", origin=reply)  # RG-1b: no answer to the carry question: it is asked again

    counted = _held_count(st, ask, pending)
    if counted is not None:
        ask = counted  # a bare count: a refine (or a new topic) on the held subject
    elif ask.act == "answers_bot_question" and not answer_is_a_refine(ask, pending):
        return _answer(st, i, ask, pending, resolver, policy, language, today, can_offer)

    if ask.act == "switch_back":
        printed = topic_index(ask.switch_back_to, printed_topics)
        if printed is None and ask.switch_back_to is None and len(printed_topics) == 1:
            printed = 0  # no key and exactly one saved topic: that one
        if printed is None:
            raise _Miss("which_held")
        back = printed_topics[printed]
        idx = next((j for j, t in enumerate(st.topics) if t == back), None)
        rest = [t for j, t in enumerate(st.topics) if j != idx]
        st.topics = push_topic(rest, st.held)
        st.held = back.model_copy(deep=True)
        qs = _Qs(can_offer)
        held = _apply_fields(st, st.held, ask, resolver, policy, language, i, qs, refine=True, today=today)
        return _commit(st, i, held, ask, qs), False

    if ask.act == "new_topic" or _new_subject_in_a_new_domain(st, ask):
        if i > 0 or not st.held.is_empty():
            st.topics = push_topic(st.topics, st.held)
        qs = _Qs(can_offer)
        before = st.held.model_copy(deep=True)
        held = _apply_fields(st, Held(), ask, resolver, policy, language, i, qs, refine=False, today=today)
        if (
            before.refs.get("product")
            and not held.refs.get("product")
            and qs  # the new topic ended in a question (not found, did-you-mean, ambiguous)
            and any(e.kind in DOCUMENT_KINDS and not e.refers_to_held for e in ask.entities)
        ):
            held.refs = set_refs(held.refs, "product", before.refs["product"], "set")  # the held product stays
        held = _carry(st, i, ask, before, held, language, qs)
        if not qs and _no_subject(held, ask):
            qs.append(_which_product(st, i, language))
        return _commit(st, i, held, ask, qs), False

    # refines_held
    if st.held.is_empty() and not ask.domain and ask.row_pick is None:
        raise _Miss("which_held", nothing_held=True)
    if i > 0 or (ask.domain and ask.domain != st.held.domain and not ask.cleared):
        # A later ask, or a domain switch on the same subject, saves what was held so a
        # switch back can return to it (the refine keeps the held subject). A refine that clears
        # fields is reshaping the same topic, not leaving it.
        st.topics = push_topic(st.topics, st.held)
    row = None
    if ask.row_pick is not None:
        rows = shown(st)
        if not (1 <= ask.row_pick <= len(rows)):
            raise _Miss("other")
        row = rows[ask.row_pick - 1]
    qs = _Qs(can_offer)
    base = st.held
    drilled = False
    if row is not None and base.report is not None and _is_report_row(row):
        base, drilled = _drill(st, base, row, ask, language, i, qs)
    held = _apply_fields(st, base, ask, resolver, policy, language, i, qs, refine=True, today=today)
    if counted is not None:
        from app.services.chatbot.pv2.bridge import takes_attachment_kinds

        if not takes_attachment_kinds(held.domain):
            held.attachment_type, held.attachment_types = None, []  # the stock ask, not the photo again
    if (
        ask.attachment_type
        and not held.refs.get("product")
        and not any(e.kind == "product" and not e.refers_to_held for e in ask.entities)
    ):
        # An attachment ask with no product held: the newest saved topic's product, else ask.
        saved = st.topics[0].refs.get("product") if st.topics else None
        if not saved:
            raise _Miss("which_held", nothing_held=True)
        held.refs = set_refs(held.refs, "product", saved, "set")
    if row is not None and not drilled:
        held = apply_payload(held, row.payload)
    if drilled and ask.report is None and not qs:
        _plan_report(st, held, resolver, language, i, qs, today)
    held = _carry(st, i, ask, st.held, held, language, qs)
    if not qs and _no_subject(held, ask):
        qs.append(_which_product(st, i, language))
    action = _commit(st, i, held, ask, qs)
    if isinstance(action, Lookup):
        action.row = row
    return action, False


def _is_report_row(row: Option) -> bool:
    from app.services.chatbot.pv2 import reports

    return reports.ROW_PAYLOAD in row.payload


def _drill(
    st: ConversationState, held: Held, row: Option, ask: Ask, language: str, i: int, qs: list[ClarifyAct]
) -> tuple[Held, bool]:
    """Section 5: "the 2nd one" on a report drills into that row (`reports.drill_row`): the
    held spec narrows to the row, its filter refs ride on `held.refs`. A row the report
    cannot narrow by says v1's own line instead (`DrillRefused`), held unchanged."""
    from app.services.chatbot.pv2 import reports

    try:
        spec, extra = reports.drill_row(held.report, row, incoming=ask.report)
    except reports.DrillRefused as exc:
        qs.append(_clarify(st, i, "other", None, language, options=[], asked=exc.line))
        return held, False
    out = held.model_copy(deep=True)
    out.report = spec
    for dim, refs in (extra or {}).items():
        out.refs[dim] = [
            ResolvedRef(kind=str(_get(r, "kind") or dim), key=str(_get(r, "key")), label=str(_get(r, "label")))
            for r in refs
        ]
    return out, True


def _commit(st: ConversationState, i: int, held: Held, ask: Ask, qs: list[ClarifyAct]) -> Action:
    """Held becomes this ask's subject; the first open question (if any) is the action,
    else a Lookup."""
    if not held.is_empty():
        bare = held.model_copy(update={"sid": None})

        def same(t: Held) -> bool:
            return t.model_copy(update={"sid": None}) == bare

        if held.sid is None:
            # A subject starts. One already in topics (same content) comes back under its own id and its copy
            # stays there; any other gets a fresh id, kept through topics and back.
            back = next((t for t in st.topics if t.sid and same(t)), None)
            held.sid = back.sid if back is not None else new_sid(st)
        elif any(t.sid == held.sid and not same(t) for t in st.topics):
            held.sid = new_sid(st)  # it differs from the topic that holds its id (a refine): its own id
    st.held = held
    if qs:
        return qs[0]
    return Lookup(
        ask_index=i, domain=held.domain, held=held.model_copy(deep=True), ask=ask,
        misses=list(getattr(qs, "misses", [])),
    )


class _DocOffer:
    """How a not-found document ends its sentence (see `apply_turn`)."""

    def __init__(
        self, mode: str = "offer", teams: dict[str, str] | None = None, roster_for: Any = None,
        dym_tags: Any = None,
    ) -> None:
        self.dym_tags = dym_tags
        self.mode = mode
        self.teams = teams
        self.roster_for = roster_for
        self.availability_only = False


class _Qs(list):
    """The questions an ask raised, plus the list items that found nothing."""

    def __init__(self, can_offer: Any = None) -> None:
        super().__init__()
        self.misses: list[Miss] = []
        self.can_offer = can_offer or _DocOffer()


#: A reference found under a different kind than the parser named answers in the domain of
#: the data actually found (a bare number the parser called a container that is a delivery).
DOMAIN_BY_FOUND_KIND: dict[str, str] = {
    "delivery_order": "order", "sales_order": "order", "customer_po_ref": "order",
    "inbound_shipment": "incoming",
    "purchase_order": "purchase_order", "purchase_request": "purchase_order",
}


def _answer(
    st: ConversationState,
    i: int,
    ask: Ask,
    pending: PendingQuestion | None,
    resolver: ResolverLike,
    policy: ClarifyPolicy,
    language: str,
    today: date | None,
    can_offer: Any = None,
) -> tuple[Action, bool]:
    if pending is None:
        raise _Miss("domain")  # R6 A: an answer to no question: what to check, with what is held as its options
    key = ask.answer.option_key if ask.answer is not None else None
    free = ask.answer.free_value if ask.answer is not None else None
    picked = _keyed_pick(ask, pending)
    if key == "all" and picked is None:
        # Every option of the question (the contract token for "all of them").
        every = [o for o in pending.options if o.payload.get("kind") != "handover"]
        if pending.kind == "v1_open_question" and every:
            first = every[0]
            return (
                Lookup(
                    ask_index=i, domain=st.held.domain, held=st.held.model_copy(deep=True), ask=ask,
                    picked=first, pending_kind=pending.kind, pick_all=[o.key for o in every],
                    pending_v1_kind=pending.v1_kind,
                ),
                True,
            )
        if pending.kind in ("clarify_entity", "clarify_did_you_mean") and every:
            if getattr(can_offer, "availability_only", False):
                # An availability-only contact is never handed every match: v1's own refusal,
                # the same question kept open.
                from app.services.chatbot.turn.apply import STOCK_PICK_NOT_ALL

                return ClarifyAct(
                    ask_index=i, about="entity", pending=pending.model_copy(deep=True), note=STOCK_PICK_NOT_ALL
                ), True
            held = st.held.model_copy(deep=True)
            for n, o in enumerate(every):
                # The first lands as a single pick would (it replaces unless its ask landed a same-kind item);
                # the rest join it.
                held = apply_payload(held, {**o.payload, "op": "add"} if n else o.payload)
            owed = _carry_question(st, i, held, pending.carry, language) if pending.carry else None
            if owed is not None:
                # RG-1c: every match landed: the carry question their ask still owes, before any fetch.
                owed.pending.origin = _waiting(valid_origin(pending.origin) or ask, held)
                return _commit(st, i, held, ask, [owed]), True
            st.held = held
            return Lookup(ask_index=i, domain=held.domain, held=held.model_copy(deep=True), ask=ask), True
    number = _number(ask)
    if picked is None and number_runs_question(ask, pending):
        # R5-1: a bare number (the reply's free value or a row pick) names the printed position; past the end it
        # is asked again, never a wrong row and never a typed value.
        picked = _nth(_numbered(pending), number)
        if picked is None:
            raise _Miss("other")
        # R5 fix 3: the number answers THIS question, so its own ask runs (R5-5); the parser's domain is ignored.
        ask = ask.model_copy(update={"domain": None})
    if picked is None and free is not None and pending.kind != "v1_open_question":
        # R5-3 (captain ruling 15 Oct): the PARSER's free_value equal, exactly, to one option label the bot itself
        # printed picks that option, as its key does. It never reads the raw user message, does no fuzzy or partial
        # matching, and v1 rosters are excluded (v1's own matcher decides there).
        same = [o for o in _numbered(pending) if o.label == free]
        picked = same[0] if len(same) == 1 else None
    if picked is None and key is None and free is None and number is None and pending.kind in SCOPED_PICK_PENDING:
        picked, ask = _scoped_pick(ask, pending, st.held, resolver)
    if picked is None:
        if free is not None and free == "all" and pending.kind == "v1_open_question":
            raise _Miss("other")  # "all" is the option key's token, never a free value
        if free is not None and pending.kind in FREE_VALUE_PENDING:
            held = st.held.model_copy(deep=True)
            for ent in ask.entities:
                if ent.key is None and ent.phrase is None:
                    _set_quantity(held, ent)  # a count restated beside the free value
            st.held = held
            return (
                Lookup(
                    ask_index=i,
                    domain=st.held.domain,
                    held=held.model_copy(deep=True),
                    ask=ask,
                    pending_kind=pending.kind,
                    free_value=free,
                    free_kind=_roster_kind(pending),
                ),
                True,
            )
        raise _Miss("other")

    if pending.kind == CONFIRM and picked.key == "no":
        # N1 (fix 4): a no drops the guess and asks openly for the full name; nothing is held or fetched.
        kind = pending.options[0].payload.get("kind")
        asked = clarify_copy.text("type_full_name", language, kind=clarify_copy.kind_word(kind, language))
        return _clarify(st, i, "entity", None, language, options=[], kind=kind, asked=asked), True

    payload_kind = picked.payload.get("kind")
    answered = picked.payload  # the item of the original ask this reply answers
    if pending.kind == "offer_handover" or payload_kind == "handover":
        if picked.payload.get("value") is True:
            return HandOver(ask_index=i, team_key=picked.payload.get("team_key"), agent_key=picked.payload.get("agent_key")), True
        answered = _declined_item(pending)
        if answered is None:
            return SmallTalk(ask_index=i, declined_offer=True), True

    if pending.kind in PASS_THROUGH_PENDING or payload_kind == "choice":
        return (
            Lookup(
                ask_index=i,
                domain=st.held.domain,
                held=st.held.model_copy(deep=True),
                ask=ask,
                picked=picked,
                pending_kind=pending.kind,
                free_value=free,
            ),
            True,
        )

    # A pick on a report's own question (a policy or filter option) replays nothing: the report's
    # questions come after every other item's, and held already carries the report.
    origin = None if {"report", "dimension"} & picked.payload.keys() else valid_origin(pending.origin)
    held = apply_payload(st.held, picked.payload)
    qs = _Qs(can_offer)
    rest = None
    if origin is not None:
        # The pick completes the ORIGINAL ask: what is left of it applies to the picked subject through
        # the same path as any ask. Its attachment kinds were vetted when it was asked, so they copy as
        # they are.
        rest = _without_picked(origin, answered, pending.kind, pending.raised_by)
        held = _complete_deferred(held, _deferred_of(rest))
        joined = answered.get("kind")
        joins = frozenset({joined} if joined else ())
        found_before: frozenset[str] = frozenset()
        if pending.kind == CARRY_SCOPE:
            # RG-1c: a carry pick lands no item; what its ask still waits on joins the items it landed, and one
            # that finds nothing beside them is a miss of that list, as it was in the ask.
            joins = found_before = frozenset(k for k, refs in held.refs.items() if refs)
        held = _apply_fields(
            st, held, rest.model_copy(update={"attachment_type": None}), resolver, policy,
            language, i, qs, refine=True, today=today, joins=joins, found_before=found_before,
        )
    else:
        held = _complete_deferred(held, pending.deferred)  # no usable origin: state saved before R2, or a report pick
    if pending.kind == "clarify_report_breakdown" and "report" in picked.payload:
        # v1's drill-down pick: the held report is unchanged; the reply runs v1's report by it.
        return (
            Lookup(
                ask_index=i,
                domain=st.held.domain,
                held=st.held.model_copy(deep=True),
                ask=ask,
                picked=picked,
                pending_kind=pending.kind,
            ),
            True,
        )
    if "report" in picked.payload and held.report is not None:
        _plan_report(st, held, resolver, language, i, qs, today)
    held = _apply_fields(st, held, ask, resolver, policy, language, i, qs, refine=True, today=today)
    if pending.carry and not qs:
        # RG-1c: the pick landed the new item its ask named: the carry question that ask still owes.
        owed = _carry_question(st, i, held, pending.carry, language)
        if owed is not None:
            qs.append(owed)
    action = _commit(st, i, held, ask, qs)
    if isinstance(action, ClarifyAct) and rest is not None:
        action.pending.origin = _waiting(rest, st.held)  # what is still to ask, so chained picks end
    if isinstance(action, ClarifyAct) and action.pending.kind != CARRY_SCOPE:
        action.pending.carry = action.pending.carry or pending.carry  # still owed after the next pick
    if isinstance(action, Lookup):
        action.picked = picked
        action.pending_kind = pending.kind
    return action, True


# --------------------------------------------------------------------------- #
# Fields: entities, specs, scalars
# --------------------------------------------------------------------------- #


def _apply_fields(
    st: ConversationState,
    base: Held,
    ask: Ask,
    resolver: ResolverLike,
    policy: ClarifyPolicy,
    language: str,
    i: int,
    qs: list[ClarifyAct],
    *,
    refine: bool,
    today: date | None = None,
    joins: frozenset[str] = frozenset(),
    found_before: frozenset[str] = frozenset(),
) -> Held:
    """`base` with this ask's fields applied. An item that needs the user appends its
    question to `qs` and is left out; every other item still applies (section 3: held =
    this ask, then the unresolved items go to the resolver and are asked). `joins`: the kinds
    already landed for this ask (a pick on one of its items), which its other items join.
    `found_before`: the kinds its ask found when it was asked, so an item of one that finds
    nothing is a miss of that list, as it was then (a carry pick)."""
    held = base.model_copy(deep=True)
    if ask.domain:
        held.domain = ask.domain
    if ask.portal_form:
        held.domain = "portal_link"  # the field names a portal form
        held.portal_form = ask.portal_form
    if ask.status is not None:
        held.status = ask.status

    for name in ask.cleared:
        if name in CLEARABLE:
            setattr(held, name, [] if name == "specs" else None)
            if name == "attachment_type":
                held.attachment_types = []

    ask, filtered = _report_entity_filters(held, ask, resolver, refine=refine)
    held = _apply_entities(
        st, held, base, ask, resolver, policy, language, i, qs, refine=refine, joins=joins, found_before=found_before
    )
    held = _apply_specs(st, held, ask.specs, resolver, policy, language, i, qs)

    if ask.attachment_type:
        kinds = [ask.attachment_type] if isinstance(ask.attachment_type, str) else list(ask.attachment_type)
        keys: list[str] = []
        for kind_key in kinds:  # every kind the ask names; none dropped silently
            res = resolver.resolve_attachment_type(kind_key, held=held)
            status = getattr(res, "status", "none")
            refs = list(getattr(res, "refs", None) or [])
            if status == "exact" and refs:
                keys.append(refs[0].key)
            else:
                options = list(getattr(res, "options", None) or [])
                # No candidates: the held product carries no files to pick from, said truly.
                qs.append(
                    _clarify(
                        st, i, "attachment_type", None, language,
                        options=options, kind="attachment_type", not_found=not options,
                    )
                )
        if keys:
            held.attachment_type = keys[0]
            held.attachment_types = keys if len(keys) > 1 else []
    if ask.period is not None:
        held.period = ask.period
        if refine and held.report is not None and ask.report is None:
            held.report = held.report.model_copy(update={"period": ask.period})
            filtered = True  # the held report is planned again with its new period
    if filtered and ask.report is None and held.report is not None:
        _plan_report(st, held, resolver, language, i, qs, today)
    if ask.report is not None or (held.report is not None and ({"report", "period"} & set(ask.cleared))):
        from app.services.chatbot.pv2 import reports

        held.report = reports.edit_report(held.report if refine else None, ask.report, cleared=ask.cleared)
        if held.report is not None:
            _plan_report(st, held, resolver, language, i, qs, today)
    held = _expand_family(_expand_sets(st, held, ask, resolver, policy, language, i, qs), resolver, qs)
    return held


#: A brand / product class the ask names while a report is held narrows the report: the
#: entity kind -> the report filter dimension it filters.
REPORT_FILTER_DIMENSION = {"brand": "brand", "product_class": "category"}


def _report_entity_filters(held: Held, ask: Ask, resolver: Any, *, refine: bool) -> tuple[Ask, bool]:
    """On a refine of a held report, or beside a report the ask opens (RG-7d), brand / product_class
    entities become that dimension's report filter instead of held refs; the first "set" of a
    dimension replaces its filter, later ones join it. Returns the ask without them (an opened report
    carrying the filters) and whether any applied."""
    from app.services.chatbot.pv2.answer import REPORT_DOMAINS

    opens = ask.report is not None and (not refine or held.report is None)
    spec = ask.report if opens else held.report if refine else None
    if spec is None or held.domain not in REPORT_DOMAINS:
        return ask, False
    keep: list[Entity] = []
    changed = False
    filters = list(spec.filters)
    set_dims: set[str] = set()
    for ent in ask.entities:
        dim = REPORT_FILTER_DIMENSION.get(ent.kind)
        if dim is None or ent.refers_to_held:
            keep.append(ent)
            continue
        current = next((f for f in filters if f.dimension == dim), None)
        if ent.op == "clear":
            filters = [f for f in filters if f.dimension != dim]
            changed = True
            continue
        res = resolver.resolve_entities([ent], held=held)[0]
        refs = list(getattr(res, "refs", None) or [])
        if getattr(res, "status", "none") != "exact" or not refs:
            keep.append(ent)
            continue
        keys = [str(_get(r, "key")) for r in refs]
        have = list(current.keys) if current is not None else []
        if ent.op == "add" or (ent.op == "set" and dim in set_dims):
            keys = have + [k for k in keys if k not in have]
        elif ent.op == "remove":
            keys = [k for k in have if k not in keys]
        if ent.op == "set":
            set_dims.add(dim)
        filters = [f for f in filters if f.dimension != dim]
        if keys:
            filters.append(Filter(dimension=dim, op="in", keys=keys, values=[]))
        changed = True
    if not changed:
        return ask, False
    if opens:
        return ask.model_copy(update={"entities": keep, "report": spec.model_copy(update={"filters": filters})}), True
    held.report = held.report.model_copy(update={"filters": filters})
    return ask.model_copy(update={"entities": keep}), True


def _plan_report(
    st: ConversationState,
    held: Held,
    resolver: Any,
    language: str,
    i: int,
    qs: list[ClarifyAct],
    today: date | None,
) -> None:
    """Section 4/5 REPORTS: the held spec's filters through `resolve_report_spec` (when
    the resolver has one), then `reports.plan_report`. Ambiguous filter names, and the ONE
    combined policy question for missing fields (owner ruling 4 Oct: a ranking never
    defaults the metric or the basis), become this ask's question with the lane's own copy
    and options. Settled filter refs ride on `held.refs` under their dimension."""
    from app.services.chatbot.pv2 import reports

    resolve = getattr(resolver, "resolve_report_spec", None)
    try:
        res = resolve(held.report, held=held, profile=None) if resolve is not None else None
    except NotImplementedError:
        res = None
    except AttributeError as exc:
        # RESOLVE forwards to `reports.resolve_report_spec`, not on this build yet: the
        # filters stay unresolved and are said below. Any other AttributeError is a bug.
        if "resolve_report_spec" not in str(exc):
            raise
        res = None
    if res is not None:
        options = [_option(o) for o in (getattr(res, "options", None) or [])]
        if getattr(res, "status", None) == "ambiguous" and options:
            qs.append(_clarify(st, i, "entity", getattr(res, "phrase", None), language, options=options))
            return
        for dim, refs in (getattr(res, "refs", None) or {}).items():
            held.refs[dim] = [
                ResolvedRef(kind=dim, key=str(_get(r, "key")), label=str(_get(r, "label") or _get(r, "key")))
                for r in refs
            ]
    if today is None:
        return
    plan = reports.plan_report(held.report, refs=report_refs(held), today=today)
    if plan.status == "unresolved":
        # A filter no resolver settled to live rows on this build: said truthfully by its
        # dimension's label (never a key), never run unfiltered, never an empty reply, and
        # dropped from the held spec so the line is said once (REPORTS' own `_say` rule).
        from app.services.chatbot.pv2 import report_measures as rm

        labels = ", ".join(dict.fromkeys(rm.dimension_label(d) for d in plan.unresolved))
        held.report = held.report.model_copy(
            update={"filters": [f for f in held.report.filters if f.dimension not in plan.unresolved]}, deep=True
        )
        qs.append(
            _clarify(
                st, i, "other", None, language, options=[],
                asked=clarify_copy.text("filter_unavailable", language, labels=labels),
            )
        )
        return
    if plan.status == "ask":
        held.report = plan.spec
        qs.append(
            _clarify(
                st, i, plan.about or "report_measure", None, language,
                options=[_option(o) for o in plan.options],
                asked=plan.reply,
            )
        )


def report_refs(held: Held) -> dict[str, list[dict[str, str]]]:
    """`held.refs` in the shape `reports.plan_report` / `answer_report` read: dimension ->
    [{kind, key, label}]."""
    out: dict[str, list[dict[str, str]]] = {}
    for kind, refs in held.refs.items():
        dim = REPORT_DIMENSION_OF_KIND.get(kind, kind)
        out.setdefault(dim, []).extend({"kind": r.kind, "key": r.key, "label": r.label} for r in refs)
    return out


#: Entity kinds whose report filter dimension has another name (`report_measures`).
REPORT_DIMENSION_OF_KIND = {"warehouse": "location", "product_type": "category"}


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _option(o: Any) -> Option:
    if isinstance(o, Option):
        return o
    return Option(key=str(_get(o, "key")), label=str(_get(o, "label")), payload=dict(_get(o, "payload") or {}))


def _apply_entities(
    st: ConversationState,
    held: Held,
    base: Held,
    ask: Ask,
    resolver: ResolverLike,
    policy: ClarifyPolicy,
    language: str,
    i: int,
    qs: list[ClarifyAct],
    *,
    refine: bool,
    joins: frozenset[str] = frozenset(),
    found_before: frozenset[str] = frozenset(),
) -> Held:
    to_resolve: list[Entity] = []
    for ent in _fold_quantity_only(ask.entities):
        if ask.portal_form and ent.kind == "form":
            continue  # the portal form is the subject; a form lookup would only block it
        has_held = bool(base.refs.get(ent.kind)) if refine else False
        if ent.refers_to_held and not refine:
            # A new ask about "this one": the subject held before this message.
            carried_refs = st.held.refs.get(ent.kind) or []
            if not carried_refs:
                raise _Miss("which_held", nothing_held=True, entity=ent)
            held.refs = set_refs(held.refs, ent.kind, carried_refs, "set")
            if ent.key is None and ent.phrase is None:
                _set_quantity(held, ent)
                continue
            has_held = True
        if ent.refers_to_held and not has_held:
            raise _Miss("which_held", nothing_held=True, entity=ent)
        if ent.op == "clear":
            held.refs = set_refs(held.refs, ent.kind, [], "clear")
            continue
        if (
            not ent.refers_to_held and ent.key is None and ent.phrase is None and ent.doc_number is None
            and ent.quantity is not None and ent.op == "set" and held.refs.get(ent.kind)
        ):
            _set_quantity(held, ent)  # a count with nothing named restates the held kind's
            continue
        if ent.refers_to_held and ent.key is None and ent.phrase is None:
            _set_quantity(held, ent)  # "this one": the held subject stays; a restated count replaces
            continue
        if ent.op == "remove" and not has_held:
            raise _Miss("which_held", nothing_held=True, entity=ent)
        to_resolve.append(ent)
    if not to_resolve:
        return held

    results = resolver.resolve_entities(to_resolve, held=held)
    set_kinds: set[str] = set(joins)
    asked: list[tuple[ClarifyAct, Entity]] = []  # the entity questions this ask raised
    listed = Counter(e.kind for e in to_resolve)
    found = {
        e.kind for e, r in zip(to_resolve, results)
        if getattr(r, "status", "none") == "exact" and getattr(r, "refs", None)
    }
    all_miss: dict[str, list[str]] = {}
    said: set[str] = set()
    for kind, n in listed.items():
        group = [(e, r) for e, r in zip(to_resolve, results) if e.kind == kind]
        if (
            n >= 2
            and kind in DOCUMENT_KINDS
            and all(
                getattr(r, "status", "none") == "none" and not getattr(r, "options", None)
                and (e.doc_number or e.phrase)
                for e, r in group
            )
        ):
            all_miss[kind] = [str(e.doc_number or e.phrase) for e, _ in group]
    for ent, res in zip(to_resolve, results):
        status = getattr(res, "status", "none")
        refs = list(getattr(res, "refs", None) or [])
        options = list(getattr(res, "options", None) or [])
        if (
            (listed[ent.kind] >= 2 and ent.kind in found or ent.kind in found_before)
            and (status == "none" or (status == "ambiguous" and getattr(res, "did_you_mean", False)))
            and isinstance(qs, _Qs)
        ):
            words = ent.phrase or ent.doc_number
            if words:
                qs.misses.append(Miss(kind=ent.kind, phrase=words))
            continue
        if status == "ambiguous" and getattr(res, "did_you_mean", False) and ent.kind == "product" and ent.phrase and options:
            # v1's own did-you-mean sentence, written here (no v1 re-lookup), with PV2's pending.
            qs.append(_did_you_mean(st, i, ent, held, options, qs.can_offer, language))
            asked.append((qs[-1], ent))
            continue
        if status == "ambiguous" and len(options) == 1 and not getattr(res, "did_you_mean", False):
            if getattr(res, "guess", False):
                # C2 (crew 15 Oct (a)): one option the words only suggest is a yes/no confirm naming it.
                qs.append(_confirm(st, i, ent, _stamped(ent, options)[0], language))
                asked.append((qs[-1], ent))
                continue
            # C2 (crew 15 Oct): a unique single option (the contact's own one customer) asks nothing; it is used.
            held = apply_payload(held, {**options[0].payload, "op": ent.op, **_qty_payload(ent)})
            continue
        pool = list(getattr(res, "pool", None) or (options if not getattr(res, "more", 0) else []))
        if any(o.payload.get("set_id") for o in pool):
            pool = []  # one set code in two companies is a pick, never their members merged (RG-5b)
        if (
            status == "ambiguous" and ent.kind == "product" and pool and not getattr(res, "did_you_mean", False)
            and _lists_all(held.domain)
            and not getattr(qs.can_offer, "availability_only", False)
        ):
            # v1's narrowing for this domain answers every match directly (`list_all`).
            every = [
                ResolvedRef(kind="product", key=str(o.payload.get("key") or o.key), label=o.label,
                            phrase=ent.phrase, quantity=ent.quantity)
                for o in pool
            ]
            held.refs = set_refs(held.refs, "product", every, _list_op(ent, set_kinds))
            continue
        if (
            status == "ambiguous" and ent.kind == "product" and pool and ent.phrase
            and not getattr(res, "did_you_mean", False) and _lists_all(held.domain)
        ):
            # An availability-only dealer is never handed the list: the typed partial goes to
            # v1 as one reference and v1's AVAIL-MODE rule asks which one.
            ref = ResolvedRef(kind="product", key=ent.phrase, label=ent.phrase, phrase=ent.phrase, quantity=ent.quantity)
            held.refs = set_refs(held.refs, "product", [ref], _list_op(ent, set_kinds))
            continue
        if status == "ambiguous" or (status == "exact" and not refs and options):
            qs.append(_clarify(st, i, "entity", ent.phrase, language, options=_stamped(ent, options), kind=ent.kind))
            qs[-1].pending.more = int(getattr(res, "more", 0) or 0)
            asked.append((qs[-1], ent))
            continue
        if status != "exact" or not refs:
            if not options and ent.kind in DOCUMENT_KINDS and (ent.doc_number or ent.phrase):
                if ent.kind in all_miss:
                    # Every item of the list missed: ONE sentence names them all, as v1 does.
                    if ent.kind not in said:
                        said.add(ent.kind)
                        qs.append(_doc_not_found(
                            st, i, ent, held, language, qs.can_offer, words_list=all_miss[ent.kind]))
                    continue
                qs.append(_doc_not_found(st, i, ent, held, language, qs.can_offer))
                continue
            qs.append(
                _clarify(
                    st, i, "entity", ent.phrase or ent.doc_number, language,
                    options=_stamped(ent, options or _referent_options(st, ent.kind)), kind=ent.kind,
                    not_found=not options,
                )
            )
            asked.append((qs[-1], ent))
            continue
        if ent.op == "remove":
            current = {r.key for r in held.refs.get(ent.kind, [])}
            if not current.intersection(r.key for r in refs):
                if {r.key for r in st.held.refs.get(ent.kind, [])}.intersection(r.key for r in refs):
                    continue  # held before this ask (or the pick), already replaced by it: done (RG-7f/7g)
                raise _Miss("which_held", entity=ent)
            held.refs = set_refs(held.refs, ent.kind, refs, "remove")
            continue
        sets_of = getattr(resolver, "family_sets", None)
        family = len(refs) == 1 and refs[0].family and held.domain in SET_DOMAINS and sets_of is not None
        sets = sets_of(refs[0].key) if family else []
        if sets:
            # RG-5 (#1443): a base code whose products belong to ACTIVE sets is one pick of those sets.
            qs.append(_clarify(st, i, "entity", ent.phrase, language, options=_stamped(ent, sets), kind=ent.kind))
            asked.append((qs[-1], ent))
            continue
        carried = base.refs.get(ent.kind, []) if refine else []
        if (
            refine
            and ent.op == "add"
            and not ent.refers_to_held
            and carried
            and held.domain in SINGLE_SUBJECT_DOMAINS
            and not {r.key for r in carried}.issuperset(r.key for r in refs)
        ):
            options = _which_held_options([*carried, *refs])
            qs.append(_clarify(st, i, "which_held", None, language, options=options, kind=ent.kind))
            continue
        if refs[0].kind != ent.kind and refs[0].kind in DOMAIN_BY_FOUND_KIND:
            held.domain = DOMAIN_BY_FOUND_KIND[refs[0].kind]
        if ent.quantity is not None:
            refs = [r.model_copy(update={"quantity": ent.quantity}) for r in refs]
        # Several entities of one kind in one ask are one list: the first "set" replaces
        # what was held, the later ones join it.
        kind = refs[0].kind  # filed under the kind FOUND
        op = "add" if ent.op == "set" and kind in set_kinds else ent.op
        set_kinds.add(kind)
        held.refs = set_refs(held.refs, kind, refs, op)
    for q, ent in asked:
        if ent.op != "remove" and ent.kind in set_kinds:
            # A pick joins the same-kind items that landed beside it instead of replacing them (RG-7b); the
            # marker says so (an explicit add's op is no landed item).
            q.pending.options = [
                o.model_copy(update={"payload": {**o.payload, "op": "add", "landed_beside": True}})
                if o.payload.get("kind") == ent.kind else o
                for o in q.pending.options
            ]
    return held


def _stamped(ent: Entity, options: list[Option]) -> list[Option]:
    """An entity question's options with the entity's quantity and phrase: the phrase names which item of
    the ask a pick answers (RG-7b). An explicit add stays one: the pick joins what is held."""
    stamp = {
        **_qty_payload(ent), **({"phrase": ent.phrase} if ent.phrase else {}),
        **({"op": "add"} if ent.op == "add" else {}),
    }
    return [o.model_copy(update={"payload": {**o.payload, **stamp}}) for o in options]


def _list_op(ent: Entity, set_kinds: set[str]) -> str:
    """The op a `list_all` product answer lands with: a "set" after a product of the same ask joins it."""
    op = "add" if ent.op == "set" and "product" in set_kinds else ent.op
    set_kinds.add("product")
    return op


#: Entity kinds whose miss says v1's own not-found sentence: the documents and a product.
DOCUMENT_KINDS = frozenset({"order", "delivery_order", "sales_order", "purchase_order", "inbound_shipment", "product"})

#: R4a: the kinds the resolver looks up by a printed label (a row pick re-resolves only these).
LABEL_KINDS = DOCUMENT_KINDS | {"customer", "warehouse"}


def shown_row(rows: list[Option], ask: Ask) -> Entity | None:
    """R4a: the printed register row an ask's row_pick names, as the entity a copied code would be (its printed
    label as its kind; the stored key is never acted on). None when the pick names no register row."""
    n = ask.row_pick
    if n is None and ask.act == "answers_bot_question" and ask.answer is not None:
        # R6 A: an answer's bare number (its option key or free value) names the printed row too.
        word = ask.answer.option_key or ask.answer.free_value
        n = int(word) if isinstance(word, str) and word.isdecimal() else None
    if n is None or not (1 <= n <= len(rows)):
        return None
    row = rows[n - 1]
    kind = row.payload.get("row_kind")
    return Entity(kind=kind, key=None, phrase=row.label, op="set", refers_to_held=False) if kind else None


#: R5-2: the documents whose product lines "this product" can name.
LINE_DOCUMENT_KINDS = frozenset({"delivery_order", "sales_order"})


def _points_back(e: Entity) -> bool:
    """"this one": an entity that refers to held and names nothing itself."""
    return e.refers_to_held and not (e.key or e.phrase or e.doc_number)


def _line_products(st: ConversationState) -> list[str] | None:
    """R5-2: the product codes of the one document the last answer showed (its printed product rows and the
    lines kept beside it; only lines the contact may see are there); None when it showed none or several."""
    # R5 fix 1: the LAST answer's rows only (`shown`): an answer that printed none leaves no document to read.
    rows = shown(st)
    docs = [(n, r) for n, r in enumerate(rows, start=1)
            if r.payload.get("row_kind") in LINE_DOCUMENT_KINDS and "in" not in r.payload]
    if len(docs) != 1:
        return None
    n, doc = docs[0]
    children = [r.label for r in rows if r.payload.get("in") == n and r.payload.get("row_kind") == "product"]
    key = next((e.key for e in st.register if e.kind == doc.payload["row_kind"] and e.label == doc.label), None)
    return list(dict.fromkeys([*st.doc_lines.get(key or "", []), *children]))


def _referents(st: ConversationState, kind: str | None = None) -> list[ResolvedRef] | None:
    """R6 B: what a follow-up reference naming nothing ("this product", "both", "the others", "that one") can mean,
    in order: the lines of the one document the last answer showed (`_line_products`: only lines the contact may
    see) and the product rows it printed, the held refs (no document once those lines are known: the reference
    reads the lines, not the number), the products of the saved topics, then the records of the question the last
    pick answered. `kind` keeps that kind only (a product reference is never offered a document). None when the last
    answer showed no single document and nothing else is there."""
    lines = _line_products(st)
    rows = [r.label for r in shown(st) if r.payload.get("row_kind") == "product"]
    keys = {e.label: e.key for e in reversed(st.register) if e.kind == "product"}  # the newest entry per label
    documents = DOCUMENT_KINDS - {"product"} if lines else frozenset()
    held = [r for refs in st.held.refs.values() for r in refs if r.kind not in documents]
    out: dict[tuple[str, str], ResolvedRef] = {}
    for r in [
        *(ResolvedRef(kind="product", key=keys.get(c, c), label=c) for c in dict.fromkeys([*(lines or []), *rows])),
        *held,
        *(r for t in st.topics for r in t.refs.get("product", [])),
        *st.last_menu,
    ]:
        if kind is None or r.kind == kind:
            out.setdefault((r.kind, r.key), r)
    return list(out.values()) if out or lines is not None else None


def _menu_refs(pending: PendingQuestion | None) -> list[ResolvedRef]:
    """R6 B: the records a question offered (as printed), for `ConversationState.last_menu`."""
    return [
        ResolvedRef(kind=str(o.payload["kind"]), key=str(o.payload["key"]), label=o.label)
        for o in _records(pending)
        if o.payload.get("kind") not in (None, *SCALAR_PAYLOAD_KINDS) and o.payload.get("key")
    ]


def _lists_all(domain: str | None) -> bool:
    """v1's narrowing rule for the domain's product kind is `list_all` (policy rows)."""
    from app.services.chatbot.turn.policy import default_policy

    # Limited to the stock domain: order / sales also carry `list_all` in the seed, but their
    # existing pins ask which product (the owner's 'WC286 stock' case is the stock ask).
    if domain != "inventory":
        return False
    row = default_policy().domain(domain)
    return bool(row is not None and (row.narrowing or {}).get("product") == "list_all")


def _offer_team(offer: Any, held: Held) -> str:
    """The domain's escalation team: the live rows when given, else the seed policy."""
    if offer.teams is not None:
        team = offer.teams.get(held.domain or "")
    else:
        from app.services.chatbot.turn.policy import default_policy

        row = default_policy().domain(held.domain or "")
        team = row.escalation_team_code if row is not None else None
    return team or "customer_service"


#: v1's did-you-mean prints at most this many candidates (`miss_suggest._DYM_CAP_DEFAULT`).
DYM_CAP = 3


def _dym_tags(offer: Any, held: Held, codes: list[str]) -> dict[str, str]:
    """v1's " - has stock details" / " - no stock details" per candidate, from the context's probe."""
    if offer.dym_tags is None or not held.domain:
        return {}
    from app.services.chatbot.lanes.business.miss_suggest import DOMAIN_PROBE

    noun = (DOMAIN_PROBE.get(held.domain) or {}).get("noun")
    if not noun:
        return {}
    found = offer.dym_tags(held.domain, codes) or {}
    return {c: f" - {'has' if found.get(c) else 'no'} {noun}" for c in codes if c in found}


def _did_you_mean(
    st: ConversationState, i: int, ent: Entity, held: Held, options: list[Option], offer: Any, language: str
) -> ClarifyAct:
    """v1's did-you-mean sentence (lanes/business/answer.py build_suggest_offer): the typed
    token, the suggested codes, and its escalation clause per the contact's offer mode."""
    options = options[:DYM_CAP]  # v1's `_DYM_CAP_DEFAULT`
    keys = [str(o.payload.get("key") or o.key) for o in options]
    # R5-7: the printed code is the key, or the label when the key is an id (never printed).
    codes = [clarify_copy.printed_code(k, o.label, language) for k, o in zip(keys, options)]
    tags = _dym_tags(offer, held, codes)
    lines = "\n".join(f"{n}. {c}{tags.get(c, '')}" for n, c in enumerate(codes, start=1))
    head = clarify_copy.did_you_mean_head(f'"{ent.phrase}" (product)', language)
    asked = f"{head}\n{lines}\nReply with a code to continue"
    picks = [  # R6 A: each option is labelled by the code it printed
        Option(key=str(n), label=c, payload={
            "kind": "product", "key": k, "label": o.label, "op": "add" if ent.op == "add" else "set",
            "phrase": ent.phrase, **_qty_payload(ent)})
        for n, (o, k, c) in enumerate(zip(options, keys, codes), start=1)
    ]
    extra: list[Option] = []
    if offer.mode == "offer":
        team = _offer_team(offer, held)
        offer_line = clarify_copy.escalate_offer(team.replace("_", " ").strip(), language)
        asked += (
            f", or would you like me to escalate to {team.replace('_', ' ').strip()} team?"
            if clarify_copy.lang(language) == "en" else f". {offer_line}"
        )
        extra = [
            Option(key=o.key, label=o.label, payload={**o.payload, "team_key": team})
            for o in clarify_copy.handover_options(language)
        ]
    elif offer.mode == "refer":
        from app.services.chatbot.turn.task import REFER_TO_SALESMAN

        asked += f". {REFER_TO_SALESMAN}"
    else:
        asked += "."
    pending = PendingQuestion(
        kind="clarify_did_you_mean", asked=asked, options=[*picks, *extra], asked_turn=st.turn_no, misses=0
    )
    return ClarifyAct(ask_index=i, about="entity", phrase=ent.phrase, pending=pending)


#: Crew 15 Oct (a): the yes/no confirm of one guessed option, keyed "yes" (that option) and "no".
CONFIRM = "clarify_confirm"


def _confirm(st: ConversationState, i: int, ent: Entity, option: Option, language: str) -> ClarifyAct:
    """The yes/no confirm ("Did you mean <name>?") of the one option a partial word suggests: nothing numbered,
    no code. "yes" picks it as a pick on an entity question does; "no" asks openly (`_answer`)."""
    # R6 C (owner rule, ledger_family.group_names): a family is named by its group name only, never an account
    # list or a count. "yes" is every account; a reply naming one account still narrows to it (R5-3).
    asked = clarify_copy.text("confirm", language, label=clarify_copy.safe_label(option.label, language))
    pick = option.model_copy(update={"key": "yes"})
    no = Option(key="no", label=clarify_copy.text("no", language), payload={"value": False})
    pending = PendingQuestion(kind=CONFIRM, asked=asked, options=[pick, no], asked_turn=st.turn_no, misses=0)
    return ClarifyAct(ask_index=i, about="confirm", phrase=ent.phrase, pending=pending)


def _doc_not_found(
    st: ConversationState, i: int, ent: Entity, held: Held, language: str, offer: Any,
    *, words_list: list[str] | None = None,
) -> ClarifyAct:
    """v1's not-found sentence for a document number (`Couldn't find: "<n>" (<kind>).`),
    with its escalation offer for a non-staff contact (`_esc_offer`, the domain's team)."""
    words = ent.doc_number or ent.phrase
    label = ent.kind.replace("_", " ")
    asked = clarify_copy.not_found_line(", ".join(f'"{w}" ({label})' for w in (words_list or [words])), language)
    options: list[Option] = []
    roster = None
    if offer.roster_for is not None and ent.kind != "product":
        # Staff: v1's own member picker (sentence, header, numbered members, close).
        team = _offer_team(offer, held)
        sentence = asked + " " + clarify_copy.escalate_offer(team.replace("_", " ").strip(), language)
        roster = offer.roster_for(team, sentence)
    if roster:
        asked = roster["text"]
        options = [
            Option(key=str(n), label=m["name"], payload={
                "kind": "handover", "value": True, "team_key": team, "agent_key": m["user_id"]})
            for n, m in enumerate(roster["members"], start=1)
        ]
        options += [
            Option(key=o.key, label=o.label, payload={**o.payload, "team_key": team})
            for o in clarify_copy.handover_options(language)
        ]
    elif offer.mode == "refer":
        from app.services.chatbot.turn.task import REFER_TO_SALESMAN

        asked += f" {REFER_TO_SALESMAN}"
    elif offer.mode == "offer":
        team = _offer_team(offer, held)
        asked += " " + clarify_copy.escalate_offer(team.replace("_", " ").strip(), language)
        options = [
            Option(key=o.key, label=o.label, payload={**o.payload, "team_key": team})
            for o in clarify_copy.handover_options(language)
        ]
    offered = bool(options)
    if not offered:
        options = _stamped(ent, _referent_options(st, ent.kind))  # R6 residual: what the words may have meant
    pending = PendingQuestion(
        kind="offer_doc_handover" if offered else "clarify_entity",
        asked=asked, options=options, asked_turn=st.turn_no, misses=0,
    )
    return ClarifyAct(ask_index=i, about="entity", phrase=words, pending=pending, not_found=True)


def _fold_quantity_only(ents: list[Entity]) -> list[Entity]:
    """A count that names no item, beside exactly one named item of its kind, is that
    item's quantity."""
    out = list(ents)
    for j, e in enumerate(ents):
        if e.quantity is None or e.key is not None or e.phrase is not None or e.refers_to_held:
            continue
        named = [k for k, o in enumerate(out) if k != j and o is not None and o.kind == e.kind
                 and (o.key is not None or o.phrase is not None) and o.quantity is None]
        if len(named) == 1:
            out[named[0]] = out[named[0]].model_copy(update={"quantity": e.quantity})
            out[j] = None  # type: ignore[call-overload]
    return [e for e in out if e is not None]


def _qty_payload(ent: Entity) -> dict[str, Any]:
    return {"quantity": ent.quantity.model_dump()} if ent.quantity is not None else {}


def _set_quantity(held: Held, ent: Entity) -> None:
    """A count with no product named ("14 ada stock") restates the held refs' quantity."""
    if ent.quantity is None:
        return
    held.refs[ent.kind] = [r.model_copy(update={"quantity": ent.quantity}) for r in held.refs.get(ent.kind, [])]


def _apply_specs(
    st: ConversationState,
    held: Held,
    specs: list[Spec],
    resolver: ResolverLike,
    policy: ClarifyPolicy,
    language: str,
    i: int,
    qs: list[ClarifyAct],
) -> Held:
    clears = [s for s in specs if s.op == "clear"]
    for s in clears:
        held.specs = [h for h in held.specs if s.attribute is not None and h.attribute != s.attribute]
    live = [s for s in specs if s.op != "clear"]
    if not live:
        return held
    results = resolver.resolve_specs(live, held=held)
    narrowed = False  # an earlier size of this ask narrowed the held products
    for spec, res in zip(live, results):
        status = getattr(res, "status", "none")
        resolved: list[ResolvedSpec] = list(getattr(res, "specs", None) or [])
        options = list(getattr(res, "options", None) or [])
        products = list(getattr(res, "products", None) or [])
        if products:
            # RG-2 (#1582): the products holding the size on any axis are the subject; no axis question. A second
            # size of the ask is resolved among the first's hits (RG-2b), so the two narrow each other.
            held.refs = set_refs(held.refs, "product", _narrowed(held, products), "set")
            narrowed = True
            continue
        sizes = list(getattr(res, "sizes_on_file", None) or [])
        if sizes:
            qs.append(_size_miss(st, i, spec, held, sizes, language, narrowed))
            continue
        if status == "ambiguous" and len(options) == 1 and policy.decide("spec_attribute", held.domain) == "default":
            held = apply_payload(held, options[0].payload)
            continue
        if (
            status == "exact"
            and options
            and getattr(res, "inferred", False)
            and policy.decide("spec_attribute", held.domain) == "ask"
        ):
            # Code placed the number on the one attribute whose real range holds it: the
            # policy decides, and tonight's policy asks with the resolver's labelled option
            # (section 4). Without an option there is nothing truthful to print: taken.
            qs.append(_clarify(st, i, "spec_attribute", spec.phrase, language, options=options))
            continue
        if status != "exact" or not resolved:
            qs.append(
                _clarify(
                    st, i, "spec_attribute", spec.phrase, language,
                    options=options, kind="spec", not_found=status == "none" and not options,
                )
            )
            continue
        for r in resolved:
            if spec.op == "remove":
                held.specs = [h for h in held.specs if h.attribute != r.attribute]
            elif spec.op == "add":
                held.specs = [*held.specs, r]
            else:
                held.specs = [h for h in held.specs if h.attribute != r.attribute] + [r]
    return held


def _narrowed(held: Held, products: list[ResolvedRef]) -> list[ResolvedRef]:
    """The products a size found, each keeping the quantity and phrase of the held product it narrows (the same
    code, else one of its family, else the one held product)."""
    current = held.refs.get("product", [])
    by_key = {r.key: r for r in current}
    only = current[0] if len(current) == 1 else None
    out = []
    for p in products:
        kin = next((r for r in current if p.family_code and p.family_code in (r.family_code, r.key)), None)
        src = by_key.get(p.key) or kin or only
        out.append(p.model_copy(update={"quantity": src.quantity, "phrase": src.phrase}) if src is not None else p)
    return out


def _size_miss(
    st: ConversationState, i: int, spec: Spec, held: Held, sizes: list[str], language: str, narrowed: bool = False
) -> ClarifyAct:
    """RG-2 (#1582): "No 300 mm for SRTWC286. Sizes on file: Length 250, 600 mm; ...", no options and no
    closest-product picks. Variants a size narrowed from a family are named by the family their sizes were read
    on, unless an earlier size of the same ask narrowed them (then those variants are what was searched)."""
    size = " ".join(x for x in (f"{spec.number:g}", spec.unit) if x)
    refs = held.refs.get("product", [])
    subject = ", ".join(dict.fromkeys(  # R5-7: a key that is an id prints the ref's label
        clarify_copy.printed_code(r.key if narrowed else r.family_code or r.key, r.label, language) for r in refs
    ))
    asked = clarify_copy.text("size_miss", language, size=size, subject=subject, sizes="; ".join(sizes))
    return _clarify(
        st, i, "spec_attribute", spec.phrase, language, options=[], kind="spec", not_found=True, asked=asked
    )


# --------------------------------------------------------------------------- #
# Questions
# --------------------------------------------------------------------------- #


def _which_held_options(refs: list[ResolvedRef]) -> list[Option]:
    """One numbered option per record; a customer group held as several accounts (one ref per account key, one
    label) is ONE option carrying every account key (owner rule: the group name once, never per account)."""
    groups: dict[tuple[str, str], list[ResolvedRef]] = {}
    for r in refs:
        groups.setdefault((r.kind, r.label) if r.kind == "customer" else (r.kind, r.key), []).append(r)
    options = []
    for n, group in enumerate(groups.values(), start=1):
        r = group[0]
        payload: dict[str, Any] = {"kind": r.kind, "key": r.key, "label": r.label, "op": "set"}
        if len(group) > 1:
            payload["keys"] = [g.key for g in group]
        options.append(Option(key=str(n), label=r.label, payload=payload))
    return options


#: Domains where v1's `list_all` answers every member of a held family (like the stock ask).
FAMILY_DOMAINS = frozenset({"incoming", "product_attachment"})


def _expand_family(held: Held, resolver: ResolverLike, qs: Any) -> Held:
    """A held FAMILY key (a ref the resolver marked `family`, never an exact product code) in
    incoming / product_attachment becomes its member codes, so v1 answers for the members that
    have data instead of asking which variant. v1 itself narrows these domains to one code
    (`narrow_to_code` / `narrow_by_tier`); expanding is crew's ruling (owner turns 4 and 6), not
    `list_all` parity. Availability-only dealers keep the family (v1's which-one)."""
    offer = getattr(qs, "can_offer", None)
    expand = getattr(resolver, "family_members", None)
    if held.domain not in FAMILY_DOMAINS or expand is None or getattr(offer, "availability_only", False):
        return held
    refs = held.refs.get("product") or []
    if len(refs) != 1 or not refs[0].family:
        return held
    members = expand(refs[0].key) or []
    if len(members) < 2:
        return held
    out = held.model_copy(deep=True)
    out.refs = set_refs(
        out.refs, "product",
        [m.model_copy(update={"quantity": refs[0].quantity, "phrase": refs[0].phrase}) for m in members], "set",
    )
    return out


#: Domains where a set code answers for its members (v1 `gate.SET_EXPANDING_DOMAINS`, #1443 crew ruling). R3 registry.
SET_DOMAINS = frozenset({"inventory"})


def _expand_sets(
    st: ConversationState, held: Held, ask: Ask, resolver: ResolverLike, policy: ClarifyPolicy, language: str,
    i: int, qs: list[ClarifyAct],
) -> Held:
    """RG-5 (#1443): in the set domains a held set code becomes its member products, each counted as the asked
    quantity times its quantity in the set (none asked: none). A set with no visible members (deactivated since
    it was resolved) is no set match (RG-5b): its code is resolved again as a fresh ask of it is, beside the
    other held products."""
    members_of = getattr(resolver, "set_members", None)
    refs = held.refs.get("product") or []
    if held.domain not in SET_DOMAINS or members_of is None or not any(r.set_id for r in refs):
        return held
    out: list[ResolvedRef] = []
    stale: list[Entity] = []
    for ref in refs:
        members = members_of(ref.set_id) if ref.set_id else []
        if ref.set_id and not members:
            stale.append(Entity(kind="product", key=None, phrase=ref.phrase or ref.key, op="add",
                                refers_to_held=False, quantity=ref.quantity))
            continue
        if not members:
            out.append(ref)
            continue
        for member, per_set in members:
            qty = ref.quantity.model_copy(update={"number": ref.quantity.number * per_set}) if ref.quantity else None
            out.append(member.model_copy(update={"phrase": ref.phrase, "quantity": qty}))
    held = held.model_copy(deep=True)
    held.refs = set_refs(held.refs, "product", out, "set")
    if stale:
        held = _apply_entities(
            st, held, held, ask.model_copy(update={"entities": stale}), resolver, policy, language, i, qs,
            refine=False, found_before=frozenset({"product"} if out else ()),
        )
    return held


def _bare_switch_back_beside_a_new_ask(asks: list[Ask], i: int, topics: list[Held]) -> bool:
    """A switch_back that names no entity, spec or attachment, beside a new_topic / refine that
    carries a subject in the same domain as the topic it would switch to. Structured fields only."""
    ask = asks[i]
    if ask.act != "switch_back" or ask.entities or ask.specs or ask.attachment_type:
        return False
    idx = topic_index(ask.switch_back_to, topics)
    if idx is None and ask.switch_back_to is None and len(topics) == 1:
        idx = 0
    target = ask.domain or (topics[idx].domain if idx is not None else None)
    if not target:
        return False
    return any(
        o.act in ("new_topic", "refines_held") and o.domain == target and any(not e.refers_to_held for e in o.entities)
        for j, o in enumerate(asks) if j != i
    )


def _origin_of(ask: Ask) -> dict:
    """The full ask a question interrupts, kept so a pick completes it."""
    return ask.model_dump(mode="json")


#: The fields that name an entity: `PendingQuestion.raised_by` records and is matched on these alone (not
#: the quantity a count beside it may have been folded into, nor a field a later schema adds or drops).
ENTITY_ID_FIELDS = ("kind", "key", "phrase", "doc_number", "op", "refers_to_held")


def _entity_id(e: Entity) -> dict:
    return {f: getattr(e, f) for f in ENTITY_ID_FIELDS}


def _is_raiser(e: Entity, raised_by: dict) -> bool:
    return all(raised_by.get(f) == getattr(e, f) for f in ENTITY_ID_FIELDS)


def _keep_origin(
    action: Action, ask: Ask, held: Held, pending_in: PendingQuestion | None, answered: bool
) -> None:
    """A question raised after `_commit` keeps what of its ask still waits on a question; one raised
    while answering the open question keeps the open question's own origin (a re-ask already carries it)."""
    if not isinstance(action, ClarifyAct) or action.pending.origin is not None:
        return
    if answered:
        action.pending.origin = pending_in.origin if pending_in is not None else None
    else:
        action.pending.origin = _waiting(ask, held)


def _waiting(ask: Ask, held: Held) -> dict:
    """The ask a question raised after `_commit` keeps: held already carries its clears, its ops and
    every entity or spec that resolved, so a pick replays none of them; what is left waits on a question.
    The domain and report are the ones held took (the kind found corrects a domain, a plan drops a filter
    it cannot run), so replaying them only asks the report's open question again; held carries the
    attachment kinds that were vetted, so none is copied."""
    have = {str(v) for refs in held.refs.values() for r in refs for v in (r.key, r.phrase) if v}
    attributes = {s.attribute for s in held.specs}

    def waits(e: Entity) -> bool:
        named = e.key or e.phrase or e.doc_number
        return e.op in ("set", "add") and named is not None and str(named) not in have

    entities = [e for e in ask.entities if waits(e)]
    specs = [s for s in ask.specs if s.op in ("set", "add") and s.attribute not in attributes]
    report = held.report if ask.report is not None else None
    update = {
        "domain": held.domain, "report": report, "entities": entities, "specs": specs, "attachment_type": None,
        "cleared": [],
    }
    return _origin_of(ask.model_copy(update=update))


def _last_lookup_ask(actions: list[Action], answered: PendingQuestion | None) -> dict | None:
    """The ask the turn's first Lookup answered (a pick answers the question's original ask)."""
    first = next((a for a in actions if isinstance(a, Lookup)), None)
    if first is None:
        return None
    if answered is not None and answered.origin is not None:
        return answered.origin
    return _origin_of(first.ask) if first.ask is not None else None


def valid_origin(origin: dict | None) -> Ask | None:
    """The saved original ask, or None when absent or drifted from the schema (the `deferred` path runs)."""
    if origin is None:
        return None
    try:
        return Ask.model_validate(origin)
    except ValidationError:
        return None


def _without_picked(
    origin: Ask, payload: dict[str, Any], question: str, raised_by: dict | None = None
) -> Ask:
    """The original ask minus the item the pick answered: the entity whose phrase the option carries,
    so the other same-kind items stay and join the pick (RG-7b; an option with no phrase answers every
    entity of its kind, as before R2). On a which_held question: the entity that raised it, every
    entity that pointed back at held ("this one": the pick is the held subject it meant) and the
    picked kind's other items, while any other remove replays (RG-7c); with no raiser recorded, every
    remove goes too (R2 fix 6). Also the spec a spec question asked about (a pick among the closest
    products answers the spec that found nothing), the attachment kind or domain picked."""
    kind = payload.get("kind")
    phrase = payload.get("phrase")
    which_held = question == "clarify_which_held"

    def answered(e: Entity) -> bool:
        if not which_held:
            # R6 A: the entity the option was stamped with (its phrase), whatever kind the resolver found it as.
            if raised_by is not None and _is_raiser(e, raised_by):
                return True
            return e.phrase == phrase if phrase is not None else e.kind == kind
        if raised_by is None:
            return e.kind == kind or e.refers_to_held or e.op == "remove"
        return _is_raiser(e, raised_by) or e.refers_to_held or (e.kind == kind and e.op != "remove")

    update: dict[str, Any] = {"entities": [e for e in origin.entities if not answered(e)]}
    if question == "clarify_spec_attribute":
        update["specs"] = [s for s in origin.specs if s.attribute not in (None, payload.get("attribute"))]
    if kind == "attachment_type":
        update["attachment_type"] = None
    elif kind == "domain":
        update["domain"] = None
    return origin.model_copy(update=update)


def _declined_item(pending: PendingQuestion) -> dict | None:
    """A did-you-mean declined after part of its ask landed (its picks join those items, `landed_beside`): the
    item it asked about, so the rest of the ask is answered without it (RG-7g). None otherwise: v1's declined
    lane."""
    if pending.kind != "clarify_did_you_mean" or pending.origin is None:
        return None
    return next(
        ({"kind": o.payload["kind"], "phrase": o.payload["phrase"]} for o in pending.options
         if o.payload.get("landed_beside") and o.payload.get("phrase")),
        None,
    )


def _deferred_of(ask: Ask) -> dict | None:
    """The ask a question asked back would otherwise drop: its domain, attachment kinds, quantity."""
    kinds = [ask.attachment_type] if isinstance(ask.attachment_type, str) else list(ask.attachment_type or [])
    qty = next((e.quantity for e in ask.entities if e.quantity is not None), None)
    if not (ask.domain or kinds or qty):
        return None
    return {
        "domain": ask.domain,
        "attachment_types": kinds,
        "quantity": qty.model_dump() if qty is not None else None,
    }


def _complete_deferred(held: Held, deferred: dict | None) -> Held:
    """The picked subject answers the ask that was interrupted (its domain and attachment kinds)."""
    if not deferred:
        return held
    out = held.model_copy(deep=True)
    if deferred.get("domain"):
        out.domain = deferred["domain"]
    kinds = list(deferred.get("attachment_types") or [])
    if kinds:
        out.attachment_type = kinds[0]
        out.attachment_types = kinds if len(kinds) > 1 else []
    return out


def _entity_about(st: ConversationState, about: str) -> str:
    """A question about an entity is never emitted with no options to pick: it becomes the
    general question about what is held."""
    return "which_held" if about == "entity" and not _options_for(st, about) else about


def _options_for(st: ConversationState, about: str) -> list[Option]:
    """Real options for a model-raised clarify: what a reference can mean for which_held (`_referents`), else
    none (an open question; never a guessed list)."""
    if about == "which_held":
        return _which_held_options(_referents(st) or [])
    return []


#: The domains an availability-only dealer is never offered in a menu (staff figures: cost, sales, purchase orders).
STAFF_ONLY_DOMAINS = frozenset({"purchase_cost", "sales", "purchase_order"})


def _domain_options(
    st: ConversationState, granted: frozenset[str] | None = None, *, dealer: bool = False
) -> list[Option]:
    """R6 A: the domains that take every kind held (else every kind of the rows the last answer printed), in v1's
    domain-label order and labelled by v1's own words (`DOMAIN_LABELS`); a pick sets the domain. Never a domain
    whose grant (`DOMAIN_GRANT_REQUIRED`) the contact lacks, nor a staff-only one to an availability-only `dealer`.
    R6 rule 2: never none: with nothing held or shown, or no domain taking all of it, every domain the contact may
    ask is offered."""
    from app.services.chatbot.lanes.business.answer import DOMAIN_GRANT_REQUIRED, DOMAIN_LABELS
    from app.services.chatbot.lanes.business.gate import ALLOWED

    kinds = {k for k, refs in st.held.refs.items() if refs} or {
        r.payload.get("row_kind") for r in shown(st) if r.payload.get("row_kind")
    }
    every = [
        d for d in DOMAIN_LABELS
        if d in ALLOWED and d != "portal_link"  # a portal form is no domain to pick
        and (granted is None or d not in DOMAIN_GRANT_REQUIRED or DOMAIN_GRANT_REQUIRED[d] in granted)
        and not (dealer and d in STAFF_ONLY_DOMAINS)
    ]
    fit = [d for d in every if kinds and kinds <= set(ALLOWED[d])]
    return [Option(key=d, label=DOMAIN_LABELS[d], payload={"kind": "domain", "key": d}) for d in fit or every]


def _is_offer(pending: PendingQuestion) -> bool:
    """R6 rule 1: an offer, not a question about the subject: PV2's hand-over offers, and a mirrored v1 escalation
    offer (its team, member or company roster) or yes / no offer. A v1 picker of records is no offer."""
    if pending.kind in ("offer_handover", "offer_doc_handover"):
        return True
    if pending.kind != "v1_open_question":
        return False
    from app.services.chatbot.turn.pending import ESCALATION_OFFER_KINDS

    return (pending.v1_kind or "") in ESCALATION_OFFER_KINDS or {"yes", "no"} <= {o.key for o in pending.options}


def _unclear_frame(held: Held, ask: Ask) -> Held:
    """R6 A: `held` with the domain and period an unclear ask named, kept while a question is asked instead."""
    frame = {k: v for k, v in (("domain", ask.domain), ("period", ask.period)) if v is not None}
    return held.model_copy(update=frame)


def _carries(ask: Ask) -> bool:
    """R6 A: the ask names something to look up or a frame for it (an entity, a spec, a domain, a period, a report,
    an attachment kind, a low stock ask or a form), not only a pick."""
    return bool(
        ask.entities or ask.specs or ask.domain or ask.period or ask.report or ask.attachment_type
        or ask.low_stock or ask.portal_form
    )


def _flag_question(
    st: ConversationState, i: int, lookup: Lookup, ask: Ask, about: str, language: str,
    granted: frozenset[str] | None = None, *, dealer: bool = False,
) -> Action:
    """R6 A: a flagged turn whose ask ran keeps what the ask settled in held and asks the parser's question with
    options from what is held and shown: what to check (the domains that take what is held; with none to offer,
    only when no domain is known), or for a which question naming nothing, which one. Else the Lookup stands."""
    options = _domain_options(st, granted, dealer=dealer) if about in ("domain", "other") else []
    if about in ("domain", "other") and (options or lookup.held.domain is None):
        q = _clarify(st, i, about, None, language, options=options)
    elif about in ("entity", "which_held") and not any(e.key or e.phrase or e.doc_number for e in ask.entities):
        options = _options_for(st, "which_held")
        if not options:
            return lookup
        q = _clarify(st, i, "which_held", None, language, options=options)
    else:
        return lookup
    q.pending.origin = _waiting(ask, st.held)
    return q


def _clarify(
    st: ConversationState,
    i: int,
    about: str,
    phrase: str | None,
    language: str,
    *,
    options: list[Option],
    kind: str | None = None,
    not_found: bool = False,
    asked: str | None = None,
) -> ClarifyAct:
    given = asked
    asked = asked or clarify_copy.question_for(about, phrase, language, kind=kind, not_found=not_found)
    if not_found and not phrase and kind != "attachment_type" and given is None:
        # Nothing the user typed to name: ask openly rather than print an internal key.
        asked = clarify_copy.question_for(about, None, language, kind=kind)
    if about == "entity":
        # Keyed by position, like a v1 question: the printed number IS the key, and the
        # payload keeps the code it picks.
        options = [
            o.model_copy(update={"key": str(n), "payload": {"key": o.key, **o.payload}})
            for n, o in enumerate(options, start=1)
        ]
    pending = PendingQuestion(
        kind=f"clarify_{about}",
        asked=asked,
        options=list(options),
        asked_turn=st.turn_no,
        misses=0,
    )
    return ClarifyAct(ask_index=i, about=about, phrase=phrase, pending=pending, not_found=not_found)


def _miss(
    st: ConversationState,
    i: int,
    pending: PendingQuestion | None,
    miss: _Miss,
    language: str,
    can_offer_handover: bool = True,
    mode: str = "none",
    *,
    granted: frozenset[str] | None = None,
    dealer: bool = False,
) -> Action:
    """One MISS: re-ask the pending question with its real options; the second miss on
    the same question offers a hand-over instead, or, to a contact who cannot be offered
    one, says v1's "I still need X for Y." and closes the question (RG-1b)."""
    if pending is None:
        about = _entity_about(st, miss.about)
        asked = clarify_copy.question_for(about, None, language, nothing_held=miss.nothing_held)
        question = PendingQuestion(
            kind=f"clarify_{about}",
            asked=asked,
            options=_options_for(st, about)
            or (_domain_options(st, granted, dealer=dealer) if about == "domain" else []),
            asked_turn=st.turn_no,
            misses=1,
        )
        # R6 A: no question was open, so this is a fresh question, never "didn't catch your choice".
        return ClarifyAct(ask_index=i, about=about, pending=question)
    misses = pending.misses + 1
    if not can_offer_handover and misses >= MISSES_BEFORE_HANDOVER and pending.kind != "offer_handover":
        return _still_need(st, i, pending, language, mode)
    if can_offer_handover and misses >= MISSES_BEFORE_HANDOVER and pending.kind != "offer_handover":
        offer = PendingQuestion(
            kind="offer_handover",
            asked=clarify_copy.text("offer_handover", language),
            options=clarify_copy.handover_options(language),
            asked_turn=st.turn_no,
            misses=0,
        )
        return OfferHandover(ask_index=i, pending=offer)
    again = pending.model_copy(deep=True)
    again.misses = misses
    return ClarifyAct(
        ask_index=i,
        about=pending.kind.removeprefix("clarify_"),
        pending=again,
        reask=True,
    )


def _repeat_is_a_miss(
    st: ConversationState,
    action: Action,
    pending_in: PendingQuestion | None,
    held_in: Held,
    language: str,
    can_offer_handover: bool,
    mode: str = "none",
) -> Action:
    """The same question asked again about the same subject (same kind, wording, options and
    held refs: v1's kind|param|subject fingerprint) counts as a MISS on it, so a repeated
    question reaches the hand-over offer (or the staff cap) like every other short-reply
    check (section 3). RG-1b: every clarify kind, not only which_held; a not-found line or a
    did-you-mean is not one. RG-1c: the re-ask is the new question (a restated ask keeps its
    new details) with one more miss."""
    if not isinstance(action, ClarifyAct) or pending_in is None or action.reask or action.not_found:
        return action
    q = action.pending
    if (
        q.kind == "clarify_did_you_mean"
        or q.asked_turn == pending_in.asked_turn  # the open question kept open (a refused "all"), not asked again
        or pending_in.kind != q.kind
        or q.asked != pending_in.asked
        or [(o.key, o.label) for o in q.options] != [(o.key, o.label) for o in pending_in.options]
        or q.kind != CONFIRM and _subject(held_in) != _subject(st.held)  # R5-3: a confirm's subject is its guess
    ):
        return action
    again = _miss(st, action.ask_index, pending_in, _Miss(action.about), language, can_offer_handover, mode)
    if isinstance(again, ClarifyAct) and again.reask:
        again.pending = q.model_copy(update={"misses": again.pending.misses})
    return again


def _subject(held: Held) -> list[tuple[str, str]]:
    return sorted((kind, r.key) for kind, refs in held.refs.items() for r in refs)


#: RG-1b: what the cap's line says is missing, per question kind (v1 `clarify_policy._MISSING_WORDS`).
STILL_MISSING = {
    "clarify_report_period": "period", "clarify_attachment_type": "attachment_type",
    "clarify_report_measure": "measure", "clarify_report_breakdown": "breakdown",
    "clarify_other": "what", "clarify_domain": "what",
}
#: The questions about one item of a kind: the line names the one kind its options carry (several: none, a
#: "which one" line), else this one.
STILL_WHICH = {
    "clarify_entity": "product", "clarify_which_held": "product", "clarify_did_you_mean": "product", CONFIRM: "product",
    "clarify_spec_attribute": "spec",
}


def _still_need(st: ConversationState, i: int, pending: PendingQuestion, language: str, mode: str) -> ClarifyAct:
    """RG-1b (#1570): v1's loop-breaker line naming what is still missing for the held subject (products first,
    then customers; the carried kind for the carry question), no options, and the question closes. A contact
    whose escalation is barred also gets v1's refer line; staff get the line alone."""
    carried = next((o.payload["clear"] for o in pending.options if o.payload.get("clear")), None)  # carry pick 2
    refs = st.held.refs.get(carried) if carried else st.held.refs.get("product") or st.held.refs.get("customer")
    missing = carried or STILL_MISSING.get(pending.kind, "more")
    kind = None
    if not carried and pending.kind in STILL_WHICH:
        missing = "which"
        kinds = {o.payload["kind"] for o in pending.options if o.payload.get("kind") not in (None, "handover")}
        kind = (kinds.pop() if len(kinds) == 1 else None) if kinds else STILL_WHICH[pending.kind]
    line = clarify_copy.still_need(missing, _labels(refs or [], language) or None, language, kind=kind)
    if mode == "refer":
        from app.services.chatbot.turn.task import REFER_TO_SALESMAN

        line += f"\n\n{REFER_TO_SALESMAN}"  # after a blank line, as v1 prints it
    closed = PendingQuestion(kind=pending.kind, asked=line, asked_turn=st.turn_no)
    return ClarifyAct(ask_index=i, about=pending.kind.removeprefix("clarify_"), pending=closed, closes=True)


def record_rows(state: ConversationState, rows: list[Option]) -> ConversationState:
    """The rows the answer just printed, for the next turn's `row_pick` (cap 20)."""
    st = state.model_copy(deep=True)
    st.last_rows = list(rows)[:LAST_ROWS_CAP]
    return st
