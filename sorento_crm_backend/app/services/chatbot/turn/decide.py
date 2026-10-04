# decide(): the ONE reading of a message against the open question and the standing
# subject (owner go, 18 Sep 2026 - "we have been fixing bugs in circles").
#
# Four places used to decide this between them, and they disagreed at the edges: the pick
# helper, the outstanding question's own arm, the focus rules' shared-axis eviction, and
# the generic pending path. They interact on every turn, and every hand pass since the
# second one found a path one of them missed. They all read a `Decision` now, and this
# module is the only place the rule lives.
#
# Four outcomes, and nothing else:
#
#   ANSWER   the message answers the open question - a position, an offered option named
#            by its exact label, a yes to an offer, or "all" over a numbered menu. An
#            escalation offer takes one explicit position or the parser's own
#            `is_escalation_confirmation`, nothing weaker (#1323).
#   REFINE   the message keeps the standing subject and narrows it - the parser's own
#            "only" marker, entities that sit on axes the subject does not hold, or a
#            date window on its own (R15, R24).
#   NEW_ASK  the message names a subject of its own - `entity_op: replace`, an entity on
#            the subject's own axis, or a document named while an outstanding question is
#            open.
#   CARRY    nothing matched. The question stays open, unrepeated, and the message runs
#            as itself (owner ruling, S6 cluster 4).
#
# The Decision says WHAT the message is. Each arm keeps its own EFFECTS - what it writes
# to the focus, the pending and the trace - because those differ by arm and always did:
# a new ask drops an outstanding question but leaves a roster standing.
#
# Pure, like the rest of the package: no message text, no I/O, no imports from the old
# seams.
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.chatbot.contracts import DETAIL_OFFER_KINDS, SALES_REPORT_GROUP_BYS
from app.services.chatbot.turn.pending import ESCALATION_OFFER_KINDS, Pending
from app.services.chatbot.turn.state import KIND_FIELD_MAP, Focus

#: The four outcomes.
ANSWER = "answer"
REFINE = "refine"
NEW_ASK = "new_ask"
CARRY = "carry"

#: Contract 38 and 39: the outstanding report's own two questions. Business questions,
#: not escalation offers - answering one is a fetch. The sales report's detail offer
#: (PLAN-chatbot-sales-report.md S4 wiring point 7) joins through
#: `contracts.DETAIL_OFFER_KINDS` rather than a second literal: its offer has exactly
#: the shape `outstanding_detail`'s has (a numbered roster of scopes, re-run with
#: `detail=`, sticky across its own pick), so this arm reads both by membership.
OUTSTANDING_KINDS: frozenset[str] = frozenset({"outstanding_scope", *DETAIL_OFFER_KINDS})

#: The scope an option names, as the documents the focus then carries. One table, read
#: both ways: a picked option becomes a `document` list, and a `document` the parser
#: emitted in WORDS ("sales order", "both") answers the same question.
DOCUMENT_BY_SCOPE: dict[str, list[str]] = {"so": ["SO"], "do": ["DO"], "both": ["SO", "DO"]}
SCOPE_BY_DOCUMENT: dict[tuple[str, ...], str] = {
    ("SO",): "so",
    ("DO",): "do",
    ("DO", "SO"): "both",
}

#: STUCK-QTY-LOOP (crew report 2, 4 Oct 2026): the verdict marker `turn/held.py::consume`
#: sets when this message names an intent other than the one the conversation was in.
#: `apply._focus_rules` then drops the carried ask-owned `focus.extra` kinds the message
#: did not name (`apply.INTENT_OWNED_EXTRA`): "taiyang only" asked for stock after a ranking by sales agent William must not
#: carry William into the stock lookup as a miss. The SUBJECT (products, customers) still
#: carries: "cert?" after "any gunmetal basin has incoming?" is about those basins (#833).
NEW_INTENT = "_new_intent"

#: A position no roster ever prints (they count from 1): a pick of nothing on offer, which
#: the generic re-print rule answers by asking the same question again.
NOT_OFFERED = 0


@dataclass(frozen=True)
class Decision:
    """What a message did to the open question and to the standing subject.

    `kind` is one of the four above and `why` names the single rule that decided it -
    together they are what the trace records and what the console drawer prints, so an
    operator reading a turn sees the decision itself, not a list of rules that fired.

    The rest are the facts the arms need and used to re-derive for themselves, each read
    off the verdict exactly once:

    * `positions` - the option numbers an ANSWER picked.
    * `entities` - the entities the message carries, as the arms see them.
    * `window` - the date window this message named, or None.
    * `scope` - the outstanding scope an ANSWER picked or a NEW_ASK named ("so", "do",
      "both"), and for a REFINE the scope the open question was already about.
    * `declined` - the parser's explicit "no thanks" flag.
    * `negated` - `is_affirmative` came back false, which is a weaker signal and is read
      with the entities beside it.
    * `replaces_every_axis` - `entity_op: replace`, the op a turn carries when its own
      entities ARE the whole scope on every axis.
    * `own_domain` - an ANSWER whose message also named a domain of its own
      (`names_its_own_domain`): the pick is answered in THAT domain, not the roster's
      (issue #1352, the judgement table in PLAN-picker-domain-judgement-29sep.md).
    """

    kind: str
    why: str
    positions: tuple[int, ...] = ()
    entities: tuple[dict[str, Any], ...] = ()
    window: dict[str, Any] | None = None
    scope: str | None = None
    declined: bool = False
    negated: bool = False
    replaces_every_axis: bool = False
    own_domain: bool = False

    @property
    def answers(self) -> bool:
        return self.kind == ANSWER

    @property
    def refines(self) -> bool:
        return self.kind == REFINE

    @property
    def starts_fresh(self) -> bool:
        """Does this turn's own scope replace every carried kind the message did not
        name? A NEW ASK that says WHAT it is asking (a domain or status word of its own)
        starts from that domain's defaults, and `entity_op: replace` says the same thing
        about the axes rather than about the domain. A refinement never does: it combines
        across kinds and replaces only within the one it named.

        This is what `_focus_rules`' eviction reads, and it replaces
        `SHARED_AXIS_BY_DOMAIN` - a per-domain table of which kinds "say the same thing",
        which could not tell "outstanding DO for 7445" from "for 7445" because both name
        a product on the order domain's own shared axis (owner ruling, 17 Sep 2026).
        """
        return self.kind == NEW_ASK and self.why in (
            "entity_op_replace",
            "domain_in_message",
        )

    def as_trace(self) -> dict[str, Any]:
        trace: dict[str, Any] = {"kind": self.kind, "why": self.why}
        if self.own_domain:
            trace["own_domain"] = True
        return trace


def _positions_by_label(pending: Pending, verdict: dict[str, Any]) -> list[int]:
    """The offered positions this message named by their own LABEL, exactly.

    Half of the pick rule (owner ruling, hand pass 3): a customer who types
    "SRTWC286-SH-150" back at a roster that offered it has answered position 2, and the
    parser emits that as an ENTITY, because an entity is what it is. The comparison is
    an exact string match against the option's own label, case-insensitive and nothing
    more: the engine matches labels, it never reads words (D1, AC-1520), so a paraphrase
    ("the SH one", "the DO list") stays the parser's job and arrives as
    `reference_positions`.

    Only an entity THIS message named counts. A carried one rides along on every turn of
    a conversation about it, and reading that as an answer would answer the question in
    the same breath it was asked.
    """
    named = {
        str(value).strip().lower()
        for e in (verdict.get("entities") or [])
        if isinstance(e, dict) and e.get("current_message") is True
        for value in (e.get("canonical_code"), e.get("raw"))
        if isinstance(value, str) and value.strip()
    }
    if not named:
        return []
    return [
        o["position"]
        for o in pending.options
        if o.get("position") is not None
        and named & _option_words(o)
    ]


def _option_words(option: dict[str, Any]) -> set[str]:
    """An option's label plus the aliases its own writer gave it (the sales report's
    drill-downs: "DO" for Delivery orders, `presenters._SALES_OPTION_ALIASES`), each
    lower-cased. Still an exact match: the aliases are data on the option, not words this
    engine reads."""
    words = [option.get("label"), *((option.get("payload") or {}).get("aliases") or [])]
    return {w.strip().lower() for w in words if isinstance(w, str) and w.strip()}


#: How far `broaden_to` widens the axis `broaden_axis` names (owner ruling, 17 Sep 2026).
FAMILY = "family"
EVERYTHING = "all"


def broaden_kind(verdict: dict[str, Any]) -> str | None:
    """The entity kind `broaden_axis` names, or None for "all" / nothing.

    Contract 31, R21. The parser emits the AXIS it was asked to widen, and "all" is only
    the commonest value of it: "okay nvm for all products" and "for any products" both
    emitted `broaden_axis: "product"` over an order question carrying SRTWC286-SH, and
    both were answered for SRTWC286-SH anyway (turns 6095ce66 / d8ab659e, 17 Sep 2026) -
    the key had exactly one reader and it tested for the string "all".
    """
    axis = verdict.get("broaden_axis")
    if not isinstance(axis, str):
        return None
    axis = axis.strip().lower()
    if not axis or axis == EVERYTHING:
        return None
    return axis


def broaden_level(verdict: dict[str, Any]) -> str | None:
    """`broaden_to`: "family", "all", or None - HOW FAR the axis is widened.

    A key of its own because the axis alone cannot tell "all variants of 286" (the
    family stands, the variant goes) from "for all products" (the axis goes). Absent
    reads as None, so a verdict recorded before the key existed behaves exactly as it
    did.
    """
    level = verdict.get("broaden_to")
    if not isinstance(level, str):
        return None
    level = level.strip().lower()
    return level if level in (FAMILY, EVERYTHING) else None


def broadens_the_roster(verdict: dict[str, Any], pending: Pending) -> bool:
    """Does this message widen the very axis the open roster is a choice of?

    `broaden_axis: "all"` widens every axis and therefore this one too - contract 31's
    own rule, unchanged. A NAMED axis does it when the roster is about that kind and the
    message asked for any widening at all, at EITHER level: a roster IS the family, so
    "all variants of 286" and "for all products" both come to the same thing over it
    (hand pass 2 item 10). "for all products" over a CUSTOMER picker answers nothing.
    """
    if verdict.get("broaden_axis") == EVERYTHING:
        return True
    axis = broaden_kind(verdict)
    return (
        axis is not None
        and broaden_level(verdict) is not None
        and pending.kind == f"{axis}_pick"
    )


def picked_positions(pending: Pending, verdict: dict[str, Any]) -> tuple[list[int], str] | None:
    """Which of the open question's positions this message picked, and by which signal.

    Owner ruling, hand pass 3 (17 Sep 2026), superseding S6 cluster 4's
    `answers_open_question`: that key is retired from the schema, the prompt and here,
    and a message answers the open question in exactly three ways at this seam - a
    `reference_positions` entry (the parser resolves every paraphrase, every ordinal and
    every worded label onto a POSITION, which is the one signal every recorded verdict
    also carries), an entity matching an offered option by exact label, or
    `broaden_axis: "all"` over a numbered menu. Anything else is not an answer: the
    question stays open and the message runs as itself.

    A recorded verdict that still carries the retired key is simply not read at all.
    """
    raw = verdict.get("reference_positions")
    positions = (
        [int(p) for p in raw if isinstance(p, (int, float)) and not isinstance(p, bool)]
        if isinstance(raw, list)
        else []
    )
    broadens = broadens_the_roster(verdict, pending)
    if pending.kind in ESCALATION_OFFER_KINDS:
        if pending.expects == "yes_no":
            # Defect 4 (owner hand pass 6, 17 Sep 2026): a YES/NO offer (one team, no
            # numbered menu - `compose._team_pick_question`'s single-team branch) is
            # answered by a yes, never by a position. "how about SO?" carried
            # `reference_positions: [1]` over exactly this shape and position 1
            # escalated a business question about a document - the
            # `is_escalation_confirmation` arm further down in `decide()` is the only
            # door a yes_no pending answers through (#1323).
            return None
        # A handover is the most expensive thing the bot can do with a message, so it
        # takes an EXPLICIT signal and nothing weaker: ONE position the customer typed
        # (which is how a multi-team roster is answered at all, contract 108), or the
        # parser's semantic confirmation the acceptance arm reads for itself (#1323). A
        # label match and a broaden are both too weak to hand a conversation to a human
        # on, and so is a SET of
        # positions: there is no handing one conversation to every team at once, which
        # is the rule contract 31 already keeps for "all". Measured on
        # `console/handpass3-owner-17sep-purchase-cost-po.json` step 3, where the ten
        # positions of a product roster, typed over an offer the live engine had left
        # open, assigned a human to a question about purchase orders.
        if broadens or len(positions) != 1:
            return None
        return positions, "positions"
    if positions:
        return positions, "positions"
    # Prod turns 339 and 342 (contact 487555417, 23 Sep 2026): "Photo srt446-RG" and
    # "Srt446-RG list price" both label-matched an offered option even though the
    # parser itself said `domain_in_message: true` - the message named its own
    # question. The parser decides ask vs pick; this engine only resolves WHICH
    # position a bare label names, so a label match counts only when this message
    # did not also say what it was asking.
    if domain_in_message(verdict) is not True:
        labelled = _positions_by_label(pending, verdict)
        if labelled:
            return labelled, "label_match"
    if broadens and pending.options:
        # Contract 31, R21: "all" over a numbered menu is a pick of EVERY option, not a
        # widening of the search - the parser reads the word as a broaden (`entity_op:
        # "clear"`, `broaden_axis: "all"`) because that is what it means anywhere else,
        # and over an open roster it means the opposite. Read off the parser's own field
        # rather than the word; live, "all" over a three-family customer picker ran the
        # plain order list with no status and no dates, where "1" answered correctly.
        return [o["position"] for o in pending.options], "broaden_all"
    return None


def _date_window(verdict: dict[str, Any]) -> dict[str, Any] | None:
    """The window this message named, as one value. `_focus_rules` writes it and the
    outstanding arm tests it, so it is derived once."""
    if not (
        verdict.get("date_mode")
        or verdict.get("date_filter_start")
        or verdict.get("date_filter_end")
    ):
        return None
    return {
        "mode": verdict.get("date_mode"),
        "start": verdict.get("date_filter_start"),
        "end": verdict.get("date_filter_end"),
    }


def _question_filters(pending: Pending | None) -> dict[str, Any]:
    if pending is None:
        return {}
    filters = pending.payload.get("filters")
    return filters if isinstance(filters, dict) else {}


def _question_subject_axes(pending: Pending | None) -> set[str]:
    """The focus slots the OPEN QUESTION's own subject occupies.

    The axis is `KIND_FIELD_MAP`'s - one slot per kind - deliberately NOT the order
    domain's shared "which order" axis, which collapses product, customer and order
    number into one. That collapse is the right answer for the order LIST (hand pass 2
    item 5, where the question is which orders are meant) and the wrong one when the
    question is which subject this turn narrows: a product report and a customer report
    are two different things a report can be about. Main's own
    `_outstanding_subject_axes` made the same carve-out for the same reason.
    """
    filters = _question_filters(pending)
    axes: set[str] = set()
    if filters.get("product_code") or filters.get("product_codes"):
        axes.add(KIND_FIELD_MAP["product"])
    if filters.get("customer_ids"):
        axes.add(KIND_FIELD_MAP["customer"])
    return axes


def _stored_scope(pending: Pending | None) -> str | None:
    scope = _question_filters(pending).get("scope")
    return scope if scope in DOCUMENT_BY_SCOPE else None


def _named_scope(verdict: dict[str, Any]) -> str | None:
    named = tuple(sorted(str(d).strip().upper() for d in (verdict.get("document") or [])))
    return SCOPE_BY_DOCUMENT.get(named)


def _picked_scope(pending: Pending, positions: list[int]) -> str | None:
    matched = [o for o in pending.options if o.get("position") in positions] if positions else []
    values = [(o.get("payload") or {}).get("value") for o in matched]
    if pending.kind == "sales_report_detail":
        # AC-SR-28: the sales report's drill-downs are a `group_by`, one at a time (the
        # first picked); never a document.
        return next((v for v in values if v in SALES_REPORT_GROUP_BYS), None)
    # "all" over the scope question picks every option, and every option at once IS the
    # widest one - answering "both" rather than the first row on the list.
    scope = "both" if "both" in values else next((v for v in values if v), None)
    return scope if scope in DOCUMENT_BY_SCOPE else None


def _keeps_subject(
    verdict: dict[str, Any], pending: Pending, entities: list[dict[str, Any]]
) -> bool:
    """R15 (owner ruling, 13 Sep 2026): does the PARSER's own verdict say this turn kept
    the subject of the open question and merely narrowed it?

    Two signals, both the parser's, neither a word list:

    * `entity_op: "reuse"` - the parser read the turn as carrying no new value at all ("i
      want to see this month only" is `reuse` plus a date window), so whatever it names is
      a filter over the report already on screen.
    * `entity_op: "replace_combine"` whose entities all sit on axes OTHER than the stored
      subject's. There is only ever ONE subject, so an entity on the subject's own axis
      REPLACES it, and that is a new ask.

    R24 (owner round 9b) narrows it to the shape a refinement actually has: a turn the
    parser classifies as a business question OF ITS OWN - `business_query` with a non-null
    `domain_hint` - is a new ask whatever axes its entities sit on. The axis test alone
    called "delivery status for hanlim" (a customer under a PRODUCT-subject offer, so a
    different axis) a refinement of the old product's report: "I kind of can't escape this
    loop."
    """
    if verdict.get("entity_op") not in ("reuse", "replace_combine"):
        return False
    if verdict.get("message_type") == "business_query" and verdict.get("domain_hint"):
        return False
    subject_axes = _question_subject_axes(pending)
    if not subject_axes:
        # Nothing stored to keep. An entity would be NAMING the subject, not narrowing
        # it, so the turn is the new ask the arms below already call it.
        return False
    for entity in entities:
        axis = KIND_FIELD_MAP.get(entity.get("hint"))
        if axis is None or axis in subject_axes:
            return False
    return True


def domain_in_message(verdict: dict[str, Any]) -> bool | None:
    """Does THIS message carry a domain or status word of its own?

    The parser's own boolean, read as-is - a domain WORD is a fact about the message's
    own text, which is exactly the kind of fact the engine may not compute for itself
    (D1/AC-1520). `None` means the verdict was recorded before the key existed.
    """
    value = verdict.get("domain_in_message")
    return value if isinstance(value, bool) else None


def names_its_own_domain(verdict: dict[str, Any]) -> bool:
    """Did THIS message name a domain of its own, beside whatever it picked?

    Issue #1352 (owner, 29 Sep 2026: "the parser reports, the engine judges"). The parser
    fills every field as the message says, a pick included, so "4 stock" arrives as a
    position AND `domain_in_message: true` with `domain_hint: "inventory"`. Both halves are
    the parser's own fields, read as-is: the flag says the message carries a domain word,
    `domain_hint` (or `asks`) says which. `asks` alone counts, as it always has (hand pass
    6 defect 1: "stock, incoming and PO for all of them").

    The flag without a domain (`domain_in_message: true`, `domain_hint: null`, no `asks`)
    names nothing to answer in, so it reads as False and the pick keeps the roster's
    domain; the prompt's consistency rule is what stops the parser emitting that shape. A
    carried `domain_hint` with the flag false is the previous turn's domain, never this
    message's, and reads as False too: a bare "7" answers the roster it was picked off.
    """
    if verdict.get("asks"):
        return True
    return domain_in_message(verdict) is True and bool(verdict.get("domain_hint"))


def backward_reference(verdict: dict[str, Any]) -> bool:
    """Does THIS message point at something OUTSIDE it with a pronoun (R-b, owner hand
    pass 6, 17 Sep 2026)?

    The parser's own `anaphora.backward_reference` flag, read as-is - "did golden win
    deliver these?" names a customer and points "these" at the products a customer
    named on an earlier turn; "any outstanding quantity for this customer" points
    "this customer" at one already carried. Neither pronoun is an entity (the prompt's
    own PRONOUN REFERENCE rule), so the message's own `entities` never re-state what it
    stands for - which is exactly why `_subject_reading`'s table cannot be trusted to
    keep it on its own: a customer named beside a bare pronoun still reads as
    `domain_in_message: true` with entities, the table's own NEW_ASK row.
    """
    anaphora = verdict.get("anaphora")
    return isinstance(anaphora, dict) and anaphora.get("backward_reference") is True


def _outstanding_domain_mismatch(pending: Pending | None, verdict: dict[str, Any]) -> bool:
    """#1262 slice 4 (F3): does THIS message's own `domain_hint` name a domain that
    disagrees with the open OUTSTANDING offer's stored one (`pending.payload
    ["domain"]`)? Read purely off the parser's own fields against the pending's own
    stored field - never a word list, never a text match (the same "read the
    parser's own output" discipline `domain_in_message`/`backward_reference` above
    already follow). A photo read as an incoming ask (`domain_hint: "incoming"`)
    under an open ORDER-domain offer is the mismatch this exists for; a null
    `domain_hint` (the ordinary refining turn, which names no domain at all) or one
    equal to the offer's own domain is not.
    """
    if pending is None or pending.kind not in OUTSTANDING_KINDS:
        return False
    domain_hint = verdict.get("domain_hint")
    if not isinstance(domain_hint, str) or not domain_hint:
        return False
    offer_domain = pending.payload.get("domain")
    return isinstance(offer_domain, str) and bool(offer_domain) and domain_hint != offer_domain


def _subject_reading(
    verdict: dict[str, Any],
    focus: Focus,
    pending: Pending | None,
    entities: list[dict[str, Any]],
    window: dict[str, Any] | None,
    facts: dict[str, Any],
) -> Decision:
    """The message read against the STANDING SUBJECT, once the open question is settled.

    This is the reading the shared-axis eviction in `_focus_rules` needs on every turn,
    open question or not, and the one the outstanding arm needs to tell a narrowing from
    a fresh question.
    """
    named = tuple(entities)
    if facts["replaces_every_axis"] and entities:
        # The retired head's own rule, kept: `replace` means this turn's entities ARE the
        # whole scope, on every axis. It is only ever stamped by a turn that has already
        # folded in whatever it means to keep, so it outranks the table below on the one
        # verdict that could carry both.
        return Decision(NEW_ASK, "entity_op_replace", entities=named, window=window, **facts)

    # THE TABLE (owner ruling, 17 Sep 2026). A message is a new question when it says
    # WHAT IT IS ASKING, and a refinement when it only says WHICH ONE. The parser answers
    # the first half (`domain_in_message`: does this message carry a domain or a status
    # word of its own?) and the entities answer the second, and between them the four
    # rows are the whole discriminator:
    #
    #   | domain_in_message | entities | reading                                        |
    #   | true              | yes      | NEW_ASK - the domain's defaults plus what it   |
    #   |                   |          |   names; every other carried kind is dropped   |
    #   | true              | no       | a domain switch over the standing subject      |
    #   |                   |          |   ("how about delivery?") - today's rule        |
    #   | false             | yes      | REFINE - combine across kinds, replace within  |
    #   |                   |          |   the same kind                                |
    #   | false             | no       | the CARRY / ANSWER paths above                 |
    #
    # This retires `scope_exclusive`, which asked the wrong question of the two turns it
    # was introduced for: "Outstsnding DO for 7445" (c45e2929) and "For srtwc286 only"
    # (67df5114) differ because the FIRST names a document and a status and the second
    # names nothing but a product, not because one of them carries an "only" marker. Item
    # 2 (captain ruling, 17 Sep 2026): the key is gone from the schema, `DECLARED_KEYS`
    # and the prompt entirely, not merely unread.
    #
    # `None` is a verdict recorded before the key existed - those fall through to the
    # rules below, unchanged, so the replay corpus still measures what it measured.
    in_message = domain_in_message(verdict)
    if in_message is True and entities:
        if backward_reference(verdict):
            # R-b (owner hand pass 6, 17 Sep 2026): a pronoun this message carries
            # points at something the message itself never names, so the NEW_ASK
            # row's own eviction (drop every kind this turn did not name) would drop
            # exactly the thing the pronoun stands for. Read as a REFINE instead -
            # combine across kinds, replace only the kind this message DID name -
            # which keeps the carried subject on every other kind, the same as a
            # message with `domain_in_message: false` would.
            return Decision(
                REFINE,
                "domain_in_message_anaphora",
                entities=named,
                window=window,
                scope=_stored_scope(pending),
                **facts,
            )
        return Decision(NEW_ASK, "domain_in_message", entities=named, window=window, **facts)
    if in_message is False and entities:
        if _outstanding_domain_mismatch(pending, verdict):
            # #1262 slice 4 (F3), AC-S4-1: this message DID name an entity, but the
            # parser's own `domain_hint` says it is about a DIFFERENT domain than the
            # open outstanding offer's - read as NEW_ASK (the plan's own words,
            # "else NEW_ASK"), the SAME reading a message naming its own entity on
            # the standing subject's own domain already gets (`_answer_outstanding`
            # drops the question rather than mis-resolving it). A photo read as
            # "X5: M210-GM" under an open ORDER-domain offer is this row's own case:
            # no domain WORD (`domain_in_message: false`), but the parser still knows
            # it is an incoming ask.
            return Decision(
                NEW_ASK, "names_its_own_entity", entities=named, window=window, **facts
            )
        return Decision(
            REFINE,
            "refines_standing_subject",
            entities=named,
            window=window,
            scope=_stored_scope(pending),
            **facts,
        )
    if in_message is True and not entities and _outstanding_domain_mismatch(pending, verdict):
        # #1262 slice 4 (F3), AC-S4-2, owner ruling (hand pass 3, T6 half): "got eta"
        # names ANOTHER domain of its own, with no entity at all - the table's row 2
        # ("true, no -> a domain switch over the standing subject") is exactly this
        # shape, and closing the offer is what stops three more turns replaying its
        # poisoned filters (`_answer_outstanding`'s own NEW_ASK arm reads this same
        # `why` and drops the question).
        return Decision(NEW_ASK, "domain_switch", entities=named, window=window, **facts)
    if pending is not None and (entities or window) and _keeps_subject(verdict, pending, entities):
        return Decision(
            REFINE,
            "narrows_the_open_question",
            entities=named,
            window=window,
            scope=_stored_scope(pending),
            **facts,
        )
    if entities:
        return Decision(NEW_ASK, "names_its_own_entity", entities=named, window=window, **facts)
    return Decision(CARRY, "nothing_answered", window=window, **facts)


def decide(
    verdict: dict[str, Any], focus: Focus, pending: Pending | None = None
) -> Decision:
    """One message, one reading. Pure: the same three inputs always give the same
    Decision, and nothing here writes to any of them.
    """
    escalation = verdict.get("escalation") or {}
    entities = [e for e in (verdict.get("entities") or []) if e]
    window = _date_window(verdict)
    facts: dict[str, Any] = {
        "declined": escalation.get("escalation_declined") is True,
        "negated": verdict.get("is_affirmative") is False,
        "replaces_every_axis": verdict.get("entity_op") == "replace",
    }

    if pending is None:
        return _subject_reading(verdict, focus, None, entities, window, facts)

    picked = picked_positions(pending, verdict)
    positions = list(picked[0]) if picked else []

    if pending.kind in OUTSTANDING_KINDS:
        # The outstanding question's own order, which is NOT the generic one: a turn that
        # narrows an open report names a filter too, so the refinement is read before
        # anything else, and a document named in words outranks a position riding along
        # with it (owner ruling, hand pass 3 row 5; browser pass 6 item 5, turn e0d6459c).
        reading = _subject_reading(verdict, focus, pending, entities, window, facts)
        if not positions and reading.refines:
            return reading
        if picked and picked[1] == "label_match" and pending.kind == "sales_report_detail":
            # AC-SR-28: "by product", "DO" typed back at the drill offer arrives as an
            # ENTITY (the label-match arm of `picked_positions`); that entity IS the pick,
            # not a new subject. The outstanding report's kinds keep their order.
            entities = []
        if entities:
            # D17 point 3: a stray position riding along with an entity is still a new
            # ask, because the parser is told never to emit both.
            return Decision(
                NEW_ASK,
                reading.why if reading.kind == NEW_ASK else "names_its_own_entity",
                positions=tuple(positions),
                entities=tuple(entities),
                window=window,
                **facts,
            )
        named_scope = _named_scope(verdict)
        own_status = verdict.get("status") or verdict.get("order_status")
        sales_drill = pending.kind == "sales_report_detail" and own_status in (None, "", "sales_report")
        if sales_drill and positions:
            # Fix round 3, F6: a picked position (a number, or the typed label itself) wins
            # over a `document` on the same verdict - the parser carries a stale one from an
            # earlier "DO" turn - so the pick is read below, never the document.
            named_scope = None
        if named_scope is not None and sales_drill:
            # Fix round 1, B1 (AC-SR-28): "DO" / "delivery orders" typed at the sales
            # report's drill offer comes back as `document: ["DO"]` (the parser's document
            # enum). Here it picks the Delivery orders option; SO (or both), or DO when that
            # option is not on offer, is a pick of nothing offered, re-asked like a number
            # past the end. A message that names its own status ("DO outstanding") is a new
            # ask and falls through to the named-document arm below (fix round 2, R1), as
            # every outstanding kind's named document does.
            offered = [
                o["position"]
                for o in pending.options
                if (o.get("payload") or {}).get("value") == "delivery_order" and o.get("position") is not None
            ]
            if named_scope == "do" and offered:
                return Decision(
                    ANSWER, "named_document_pick", positions=tuple(offered[:1]), window=window,
                    scope="delivery_order", **facts,
                )
            return Decision(ANSWER, "named_document_not_offered", positions=(NOT_OFFERED,), window=window, **facts)
        if named_scope is not None:
            # "Sales order", typed straight after the DO detail list, emitted
            # `document: ["SO"]` AND `reference_positions: [1]`, and the position won:
            # position 1 of that offer is its only option, "Delivery order list", so the
            # same twenty DO lines came back byte for byte at a customer who had just
            # said which paper they wanted.
            return Decision(
                NEW_ASK,
                "named_document",
                positions=tuple(positions),
                window=window,
                scope=named_scope,
                **facts,
            )
        picked_scope = _picked_scope(pending, positions)
        if picked_scope is not None:
            return Decision(
                ANSWER,
                "picked_scope",
                positions=tuple(positions),
                window=window,
                scope=picked_scope,
                **facts,
            )

    if positions:
        # A position nobody offered is still an ATTEMPT at this question - the arm
        # re-prints it rather than dropping it silently. Issue #1352: the position and the
        # domain fields are read together - a pick whose message also named its own domain
        # is answered in that domain (`own_domain`), a bare pick in the roster's.
        return Decision(
            ANSWER,
            picked[1] if picked else "positions",
            positions=tuple(positions),
            entities=tuple(entities),
            window=window,
            own_domain=names_its_own_domain(verdict),
            **facts,
        )

    if pending.kind in ESCALATION_OFFER_KINDS:
        # Issue #1323 (owner ruling, 28 Sep 2026: "the chatbot should be able to
        # understand whether the user means escalation ... semantically"). An escalation
        # offer is accepted on the parser's ONE semantic verdict and nothing else, for
        # every offer shape (a yes/no offer, a team menu, a company pick).
        # `is_affirmative` stays the AFFIRMATION flag and never hands over by itself:
        # turn 9d9c417d carried it true beside ten product codes and a stock ask the
        # parser itself had called `is_escalation_confirmation: false`, and the stock ask
        # went to the warehouse team unanswered.
        accepted = escalation.get("is_escalation_confirmation") is True
        why = "escalation_confirmation"
    else:
        accepted = (
            verdict.get("is_affirmative") is True
            or escalation.get("is_escalation_confirmation") is True
        )
        why = "affirmative"
    if accepted and not (facts["declined"] or facts["negated"]):
        # A decline outranks every acceptance signal - the acceptance arm has always read
        # it that way, and now the generic path does too.
        return Decision(ANSWER, why, entities=tuple(entities), window=window, **facts)

    return _subject_reading(verdict, focus, pending, entities, window, facts)
