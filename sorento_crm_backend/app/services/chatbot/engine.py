"""`run_turn` - the head of the turn: receive, understand, check access, route.

One inbound WhatsApp message in, `{turn_id, ctx, item, branch_kind, delegate}` out. The
stages the S1 head owns map onto the five nodes it replaces in the live spine
(`get-session-vars`, `Call 'sub-query-reformulator'`, `check-access`, `build-ctx`,
`route-turn`), and `item` is byte-equal to what `route-turn` emits today so every n8n
reader downstream is unchanged (AC-101, AC-110).

**Session discipline.** `run_turn` takes a session FACTORY, not a session. The plan's
capacity section is explicit: never hold a DB session across LLM or MCP I/O, and the
96/100-connection incident is the evidence. The engine opens a session to read, closes it,
makes the parser call with nothing checked out, and reopens to check access and record the
turn. A request-scoped `Depends(get_db)` session could not satisfy that, which is why the
signature differs from the plan's original sketch (the plan is updated in the same change).

**D14, dry run.** `envelope.dry_run` is evaluated FIRST, before anything side-effecting
(H37: n8n called next-assignee and guarded afterwards). On a dry run the only row written
anywhere is the `chatbot.turns` record itself, every action carries `dry_run: true`, and
the response carries `session_patch` - null in S1, because the head writes no session
state; the tail (S2) is what fills it.
"""
from __future__ import annotations

import copy
import json
import logging
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace as dataclasses_replace
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models.base import set_company_scope
from app.models.chatbot_turn import ChatbotTurn
from app.services.chatbot import dispatch, jsc, llm_call, media_intake, send_order, trace as trace_mod
from app.services.chatbot.contracts import (
    BUSINESS_BRANCH_KINDS,
    CRM_COMPLETED_BRANCH_KINDS,
    SELF_CLOSING_BRANCH_KINDS,
    TURN_FAILURE_STAGES,
    Envelope,
    named_count,
)
from app.services.chatbot.delegate import enabled_lanes_from
from app.services.customer_group_service import family_overrides
from app.services.error_handler import AppException
from app.services.ledger_family import customer_groups
from app.services.chatbot.head import grounding, parser
from app.services.chatbot.head.access import check_access, default_space_id
from app.services.chatbot.head.build_ctx import build_ctx
from app.services.chatbot.lanes import business, canned as canned_lanes, casual
from app.services.chatbot.lanes import fallback as fallback_mod
from app.services.chatbot.lanes.escalation import run as run_escalation_lane, routing_line as escalation_routing_line
from app.services.chatbot.lanes.business import resolve_gate, services as business_services
from app.services.chatbot.usage import record_parser_usage
# Stages C to G (PLAN-chatbot-turn-rearch.md "Turn order"). `turn/` is the pure core -
# APPLY, the narrower, the plan, the router, the composer, the tail - and `turn_runtime`
# is everything that has to touch a database or a tool on its behalf.
from app.services.chatbot import session_state, turn_runtime
from app.services.chatbot.turn import pending as turn_pending
from app.services.chatbot.turn import question as turn_question
from app.services.chatbot.turn import state as turn_state
from app.services.chatbot.turn import compose as turn_compose
from app.services.chatbot.turn import context as context_mod
from app.services.chatbot.turn import episode_digest as episode_digest_mod
from app.services.chatbot.turn import fetch as run_fetch_mod
from app.services.chatbot.turn import memory as memory_mod
from app.services.chatbot.turn import profile_facts as profile_facts_mod
from app.services.chatbot.turn import tail as turn_tail
from app.services.chatbot.turn import task as turn_task
from app.services.chatbot.turn.apply import apply as turn_apply
from app.services.chatbot.turn.apply import is_product_shaped_entity
from app.services.chatbot.turn.apply import record_top_selling_asked
from app.services.chatbot.turn.decide import EVERYTHING, broaden_kind, broaden_level
from app.services.chatbot.turn.policy import load_policy
from app.services.chatbot.turn.route import route as turn_route
# Module level and by name, the same shape `app/api/v1/external/media.py` uses for its own
# enqueue-and-wait: the offload is one flag away from being the normal path, and a lazy
# import inside the branch would hide the dependency from anything reading this file.
from app.services.queue_service import cancel_job, enqueue_job, get_job_status, redis_conn

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Session]

# The branch kinds whose arm closes its OWN row, after its lane has produced an answer,
# AND for which `completes_here` alone is the whole question. Everything else closes at
# `routed` in the block below. `contracts` owns the list because `lanes/canned.py`
# subtracts the same one to know which kinds are ITS to compose.
#
# The business arms come off: they close their own row too, but whether they reach the
# arm that does is a SECOND question - the lane can be switched on and still hand the
# turn back (the resolver raised, the fetch was an outage, the exit was not an answer).
# `business_completes` is that answer and it sits beside this set in the guard, so a
# business turn that did not reach its answer half still closes as `delegated`.
_CRM_FINISHED_HERE: frozenset[str] = SELF_CLOSING_BRANCH_KINDS - BUSINESS_BRANCH_KINDS

# The arms whose whole answer IS the question the plan is asking, so the composer renders
# the roster and the tail stores it - rather than the canned registry answering with a
# sentence that names none of the options the customer is looking at. `business_query`
# belongs here for the same reason the other two do: a narrowing question raised inside a
# business domain now routes to that domain's own arm (`turn/route.py`), so the arm that
# has to render the roster is the business one. The escalation kinds are deliberately
# absent - a `team_pick` / `member_offer` / `company_pick` question is composed by the
# escalation lane, which knows the teams.
_ASK_BRANCH_KINDS: frozenset[str] = frozenset(
    {"clarify_menu", "check_promotion", "business_query"}
)

# "the caller did not pass a row", which `None` cannot mean here: `None` is the real value
# when the settings singleton does not exist yet.
_UNSET: Any = object()

GENERIC_ERROR_REPLY = parser.PARSER_ERROR_REPLY

# AC-703. The queue the offloaded turn runs on, classified `fast` in `worker.QUEUES`: a
# customer is watching "typing...", so it must never queue behind a 39-minute import.
CHAT_QUEUE = "chat"

# How often the waiting request looks at the job. Same order as `/external/media`'s own
# poll: short enough not to pad a fast turn, long enough not to spin.
WORKER_POLL_INTERVAL_SECONDS = 0.25


# A long reply the MCP presenter already split into WhatsApp-sized parts
# (`sorento_crm_mcp.presenters.whatsapp_parts`): each part after the first opens with
# its own "(k/m)" line, after a blank line.
_MARKED_PART_BREAK = re.compile(r"\n\n(?=\(\d+/\d+\)\n)")
_MARKED_PART_HEAD = re.compile(r"\((\d+)/(\d+)\)\n")


def split_marked_message(text: str) -> list[str]:
    """The presenter's marked parts of `text`, in order, or `[text]` when it carries
    none (TOP-N-UNCAP, owner 30 Sep 2026: a top 200 goes out as several messages, so
    delivery never leans on n8n chunking one over WhatsApp's 4096 characters).

    Only a COMPLETE run splits: "(1/m)" on a line of the first piece (a lane note may
    sit above it) and "(2/m)" to "(m/m)" opening the rest. Anything else is text that
    merely looks like a marker and goes out whole, as before."""
    pieces = _MARKED_PART_BREAK.split(text)
    if len(pieces) > 2 and pieces[1].startswith("(1/"):
        # A lane note above part 1 ("I can list at most ...") travels with part 1.
        pieces = [pieces[0] + "\n\n" + pieces[1], *pieces[2:]]
    if len(pieces) < 2:
        return [text]
    total = len(pieces)
    for k, piece in enumerate(pieces[1:], start=2):
        head = _MARKED_PART_HEAD.match(piece)
        if head is None or int(head.group(1)) != k or int(head.group(2)) != total:
            return [text]
    first = pieces[0]
    marker = f"(1/{total})\n"
    if not (first.startswith(marker) or f"\n{marker}" in first):
        return [text]
    return pieces


def split_send_actions(actions: list[Any]) -> list[Any]:
    """Each `send_message` whose text is a marked run becomes one action per part, in
    order. The quick replies and the result set ride on the LAST part only: they belong
    to the question the reply ends on. Every other action passes through untouched."""
    out: list[Any] = []
    for action in actions:
        text = action.get("text") if isinstance(action, dict) and action.get("kind") == "send_message" else None
        parts = split_marked_message(text) if isinstance(text, str) else []
        if len(parts) < 2:
            out.append(action)
            continue
        out.extend({**action, "text": part, "quick_replies": None, "result_set": None} for part in parts[:-1])
        out.append({**action, "text": parts[-1]})
    return out


class TurnResult:
    """What the endpoint serialises. A plain object so the route stays a thin adapter."""

    __slots__ = (
        "turn_id",
        "is_test",
        "ctx",
        "item",
        "branch_kind",
        "delegate",
        "delegate_payload",
        "reply",
        "actions",
        "session_patch",
        "duplicate",
        "status",
        "stage",
        "error",
    )

    def __init__(self, **kwargs: Any) -> None:
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))
        if self.actions is None:
            self.actions = []
        if self.duplicate is None:
            self.duplicate = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "is_test": bool(self.is_test),
            "ctx": self.ctx,
            "item": self.item,
            "branch_kind": self.branch_kind,
            "delegate": self.delegate,
            "delegate_payload": self.delegate_payload,
            "reply": self.reply,
            "actions": split_send_actions(self.actions),
            "session_patch": self.session_patch,
            "duplicate": self.duplicate,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _without_the_drill_pick_words(parse_output: dict[str, Any], pending: Any, trace: Any) -> dict[str, Any]:
    """The resolver's input on a turn that ANSWERED the sales report's drill offer.

    PR #1401 fix round 2, R2: "DO", "by product", "delivery orders" typed back at the
    `sales_report_detail` offer settle the pick (`turn/decide.py`, the label / alias /
    named-document arms), but the typed word also rode to the resolver as an entity, found
    nothing, and the reply closed with "I could not find DO." The word that settled the pick
    is the answer, not a subject, so it is dropped from what the resolver is asked about.
    Round 4: the focus never holds it either (`turn/apply.py`, the same rule), so no later
    turn carries it back here.
    """
    from app.services.chatbot.turn.apply import without_the_drill_pick_words

    entities = parse_output.get("entities") or []
    kept = without_the_drill_pick_words(entities, pending, trace)
    if len(kept) == len(entities):
        return parse_output
    return {**parse_output, "entities": kept}


def _without_carried_domain_on_a_roster_pick(
    parse_output: dict[str, Any], rules_fired: list[str]
) -> dict[str, Any]:
    """The resolver's verdict on a pick the engine answered in the ROSTER's domain.

    PR #1353 fix round 1 (owner hand test, v48: "promotion for srtwc286" -> "1" -> "2"
    answered with the wrong promotions). The parser now keeps `domain_hint` on a bare
    pick, but on this path (`pick_in_roster_domain`, `decide.names_its_own_domain`
    False) that hint is the roster's carried domain, not a word of this message, and the
    plan already answers in it. The resolver read it as a fresh ask: a second pick over
    an already-settled product hands it no token at all, and the gate's "no entities and
    'promotion' requires a scoping entity" exit (`not_found`) then answered "That would
    search every promotion we have" over the Dealer fetch that had just run for the
    product, while its tier gate re-asked the tier the pick had settled. A bare pick
    reaches the resolver with no domain of its own, exactly as a pick that named none
    always did. A message that named its own entity is left alone.
    """
    if "pick_in_roster_domain" not in rules_fired:
        return parse_output
    if any(
        isinstance(e, dict) and e.get("current_message") is True
        for e in (parse_output.get("entities") or [])
    ):
        return parse_output
    if not parse_output.get("domain_hint") and not parse_output.get("intent_hint"):
        return parse_output
    # PR #1353 fix round 2 (merge of main's #833): the carried `intent_hint` is the same
    # echo. `lanes/business/predicate.derive_require` reads `check_promotion` as the bare
    # "has a promotion" leg, so a pick over an already-settled product (no token for the
    # resolver) built a described set over nothing, and `run_fetch` answered "the
    # described set qualifies nothing" instead of the promotion fetch the pick planned.
    return {**parse_output, "domain_hint": None, "intent_hint": None}


def _picks_in_the_roster_domain(pending: Any, verdict: dict[str, Any]) -> list[int]:
    """The positions a bare pick answered over an open roster, in the roster's domain.

    PR #1353 fix round 2: the parser resolves WHICH position (a number, an ordinal, a
    typed label); this is only the engine's reading of whether the message also named a
    domain of its own. A pick naming none, or naming only the roster's own domain, is
    answered in the roster's domain (`apply._answer_pending`, contract 121). Anything
    else returns [] and the verdict is left as the parser wrote it.
    """
    from app.services.chatbot.turn.apply import _names_only_the_roster_domain
    from app.services.chatbot.turn.decide import names_its_own_domain, picked_positions

    if pending is None or not turn_pending.is_roster(pending.kind) or not pending.options:
        return []
    picked = picked_positions(pending, verdict)
    if picked is None:
        return []
    asked_for = pending.payload.get("domains") or (
        [pending.payload["domain"]] if pending.payload.get("domain") else []
    )
    if names_its_own_domain(verdict) and not _names_only_the_roster_domain(verdict, asked_for):
        return []
    return list(picked[0])


#: A bare pick as the roster's own reply line offers it ('Reply with the number(s), e.g.
#: "1", "1 and 2", or "all".'): whole numbers joined by commas, "&", "and" or spaces.
_BARE_POSITIONS = re.compile(r"#?\d+(?:\s*(?:,|&|\band\b)?\s*#?\d+)*")


def _bare_roster_positions(pending: Any, message: str) -> list[int] | None:
    """The positions a BARE pick names over an open numbered roster, or None.

    PR #1353 fix round 3 (owner retest, v48, chatbot.turns 29 Sep 2026 11:44 MYT, turn
    3f56a40d): over the tier roster (1 Office, 2 Dealer, 3 End user) with Office picked
    one turn earlier, the parser read the bare "2" as `reference_positions: [1]` and
    `open_question_answer: {pick, [1]}`, so the Office files were sent again. Owner
    ruling: the parser reports, the engine judges. A message that is nothing but a
    position ("2"), a list of them ("1 and 2", "1, 2") or "all" is read here, in code;
    every position must be on the roster (1 to the option count), else it is not a bare
    pick of THIS roster and the parser's reading stands. A word beside the number ("2
    dealer", "stock for 2"), an ordinal or a typed label is the parser's to read.
    """
    if pending is None or not turn_pending.is_roster(pending.kind) or not pending.options:
        return None
    if (pending.payload or {}).get("stock_pick"):
        # A bare number under a stock pick is its quantity (`apply._stock_pick_requantified`).
        return None
    offered = sorted(
        int(o["position"]) for o in pending.options
        if isinstance(o, dict) and isinstance(o.get("position"), int) and not isinstance(o.get("position"), bool)
    )
    if not offered:
        return None
    text = str(message or "").strip().lower().rstrip(".!")
    if text == "all":
        return offered
    if not _BARE_POSITIONS.fullmatch(text):
        return None
    positions = sorted({int(n) for n in re.findall(r"\d+", text)})
    if not positions or any(p not in offered for p in positions):
        return None
    return positions


#: AVAIL-MODE-REPLIES rule 4 (owner, 2 Oct 2026): a message that is nothing but "all" over
#: an availability-mode stock pick. The parser's prompt reads "all" / "semua" over a pick as
#: EVERY position, which is indistinguishable from "1,2,3" by then, so the engine reads the
#: bare word itself (owner Q4 (b): only the explicit "all" is refused; every number typed
#: out is a pick).
_BARE_ALL = re.compile(
    r"(?:all(?: of (?:them|it|these|those))?|every ?one|everything|semua|全部|都要)"
    r"(?: (?:please|pls|plz))?"
)


def _with_repeated_codes_summed(verdict: dict[str, Any], message: str) -> dict[str, Any]:
    """AVAIL-MODE-REPLIES (owner Q2 (a)): a code named twice in one message asks for the
    quantities added up. The live parser merges "SRT5674 x 2, SRT5674 x 3" into ONE
    entity carrying the last quantity (`entity_op: replace_combine`, tester-local pass on
    7fa5d654, step 12), so the quantities are read off the message beside that code. A
    code the parser kept as two entities is summed later (`turn_runtime._spec_quantities`)
    and is left alone here."""
    entities = verdict.get("entities")
    if not isinstance(entities, list):
        return verdict
    products = [e for e in entities if isinstance(e, dict) and e.get("hint") in (None, "product")]
    changed = False
    out = []
    for entity in entities:
        if entity in products and entity.get("quantity") is not None:
            codes = {
                str(entity.get(k)).strip()
                for k in ("raw", "canonical_code")
                if isinstance(entity.get(k), str) and entity.get(k).strip()
            }
            twins = [
                e for e in products
                if e is not entity and codes & {str(e.get(k) or "").strip() for k in ("raw", "canonical_code")}
            ]
            for code in sorted(codes, key=len, reverse=True):
                found = re.findall(
                    rf"(?<![\w-]){re.escape(code)}(?![\w-])\s*(?:x|\*|qty|:|-|=)?\s*(\d+)", message or "", re.IGNORECASE
                )
                if len(found) >= 2 and not twins:
                    entity = {**entity, "quantity": sum(int(n) for n in found)}
                    changed = True
                    break
        out.append(entity)
    if not changed:
        return verdict
    summed = {**verdict, "entities": out}
    if len(products) == 1 and verdict.get("demand_qty") is not None:
        summed["demand_qty"] = out[entities.index(products[0])]["quantity"]
    return summed


#: Words that make a message an ETA ask whatever else it says (the incoming domain's own
#: switch words, `turn/policy_rows.py`, plus the Malay and Chinese a dealer types).
_ETA_WORDS = re.compile(
    r"\b(?:eta|incoming|arriv\w*|shipments?|containers?|coming|when|bila|sampai|tiba)\b|几时|什么时候|到货|到",
    re.IGNORECASE,
)


def _as_a_stock_ask_by_its_own_words(verdict: dict[str, Any], message: str) -> dict[str, Any]:
    """AVAIL-MODE-REPLIES (tester re-run on 2eb2a00ef): the live parser intermittently read
    a plain "CWCX611 x 300" as `check_incoming` and the dealer was told "ETA not confirmed
    yet". A message whose own words name a product WITH a quantity and no ETA word is a
    stock ask, so the incoming reading is put back to stock. One prompt, read the same
    way every time by the engine (owner: no second prompt).

    The cloud live-parser pass at 2ff7f5e9 saw the same misread the other way: "srt5764 xx
    10" read as a product-info ask (`check_product` / `master_products`), which answered
    with the staff did-you-mean and an escalation offer. That reading is put back too."""
    misread = {"incoming", "master_products"}
    incoming = (
        verdict.get("intent_hint") in ("check_incoming", "check_product")
        or verdict.get("domain_hint") in misread
        or any(isinstance(a, dict) and a.get("domain") in misread for a in verdict.get("asks") or [])
    )
    text = str(message or "")
    if not incoming or _ETA_WORDS.search(text):
        return verdict
    codes = {
        str(e.get(k)).strip()
        for e in verdict.get("entities") or []
        if isinstance(e, dict) and e.get("hint") in (None, "product")
        for k in ("raw", "canonical_code")
        if isinstance(e.get(k), str) and e.get(k).strip()
    }
    # Any standalone number is the quantity, wherever it sits: the live parser's second
    # pass read "srt5764 xx 10" as the entity "srt5764" with the 10 after "xx".
    with_qty = bool(codes) and (
        verdict.get("demand_qty") is not None
        or any(
            isinstance(e, dict) and isinstance(e.get("quantity"), (int, float)) and not isinstance(e.get("quantity"), bool)
            for e in verdict.get("entities") or []
        )
        or re.search(r"(?<![\w./-])\d+(?![\w./-])", text) is not None
    )
    if not with_qty:
        return verdict
    asks = [
        {**a, "domain": "inventory"} if isinstance(a, dict) and a.get("domain") in misread else a
        for a in verdict.get("asks") or []
    ]
    return {
        **verdict,
        "intent_hint": "check_stock",
        "domain_hint": "inventory",
        **({"asks": asks} if verdict.get("asks") else {}),
    }


def _code_like(token: str) -> bool:
    """A typed token that can be a product code: letters AND digits ("srtwc286"). A word
    ("xx", "basin") or a bare number ("2", "10") is not one."""
    return any(ch.isdigit() for ch in token) and any(ch.isalpha() for ch in token)


def _unsure_capture_as_codes(verdict: dict[str, Any]) -> dict[str, Any]:
    """AVAIL-MODE-REPLIES (tester re-run on a5ba9dc9f, fix 1 runs 2-3): the live parser
    intermittently read "srt5764 xx 10" as ONE unsure capture (`confident: false`, or no
    hint), and the dealer was told 'I captured "srt5764 xx 10" but couldn't tell which
    part is which.' instead of the did-you-mean. An availability stock / ETA turn reads
    such a capture by its own words: each code-like word is a product, a bare number is
    its quantity, any other word is dropped (never a product)."""
    if verdict.get("domain_hint") not in ("inventory", "incoming") and verdict.get("intent_hint") not in (
        "check_stock",
        "check_incoming",
    ):
        return verdict
    entities = verdict.get("entities") or []
    out: list[Any] = []
    changed = False
    for e in entities:
        unsure = (
            isinstance(e, dict)
            and e.get("hint") in (None, "product")
            and (e.get("hint") is None or e.get("confident") is False or e.get("hint_confident") is False)
        )
        if not unsure:
            out.append(e)
            continue
        words = [w for w in re.split(r"[\s,]+", str(e.get("raw") or "")) if w]
        codes = [w for w in words if _code_like(w)]
        numbers = [int(w) for w in words if w.isdigit()]
        changed = True
        qty = e.get("quantity")
        if qty is None and len(numbers) == 1 and verdict.get("demand_qty") is None:
            qty = numbers[0]
        for code in codes:
            out.append(
                {
                    **e,
                    "raw": code,
                    "canonical_code": code,
                    "hint": "product",
                    "confident": True,
                    "hint_confident": True,
                    "quantity": qty,
                }
            )
    return {**verdict, "entities": out} if changed else verdict


def _exact_codes_only(
    parse_output: dict[str, Any],
    resolved_kinds: dict[str, dict[str, int]],
    resolved_candidates: dict[str, list[dict[str, Any]]],
    compatible_entities: list[dict[str, Any]],
    unplaced: dict[str, str],
):
    """AVAIL-MODE-REPLIES (owner hand test, 3 Oct 2026): an availability-access stock or
    ETA turn answers EXACT codes only, and steers the dealer to one when they typed less.

    Per product token this message (or the carried focus) names:
    * a code matching it exactly is the product, and its family is not (the bare "eta"
      after "srtw2000 20" listed SRTW2000-SS-CR, -A and -NL as well);
    * else a code-like token keeps the codes it is the prefix of, the family the
      which-one picker steers from ("srtwc286");
    * else a code-like token is not found, and goes to the did-you-mean ("srt5764");
    * a word or a bare number with no exact code is not a product at all: the describe /
      semantic matches the resolver found for "xx", "10" or a "2" typed over a
      did-you-mean listed a page of the catalogue.
    Returns the four resolver answers, or None when nothing changes."""
    def code_of(row: dict[str, Any]) -> str:
        return str(row.get("canonical_code") or row.get("code") or row.get("raw") or "").strip().casefold()

    tokens: list[tuple[str, str]] = []
    for e in parse_output.get("entities") or []:
        if not isinstance(e, dict) or e.get("hint") not in (None, "product"):
            continue
        typed = str(e.get("raw") or e.get("canonical_code") or "").strip()
        if typed and typed.casefold() not in {t for t, _ in tokens}:
            tokens.append((typed.casefold(), typed))
    candidates = [c for c in resolved_candidates.get("product") or [] if isinstance(c, dict)]
    if not tokens or not candidates:
        return None
    keep: set[str] = set()
    per_token: dict[str, int] = {}
    misses: dict[str, str] = {}
    ignored: set[str] = set()
    for fold, typed in tokens:
        exact = [c for c in candidates if code_of(c) == fold]
        family = [c for c in candidates if code_of(c).startswith(fold)] if _code_like(fold) else []
        chosen = exact or family
        if chosen:
            keep.update(str(c.get("uuid")) for c in chosen)
            per_token[fold] = len(chosen)
        elif _code_like(fold):
            misses[fold] = typed
        else:
            ignored.add(fold)
    kept = [c for c in candidates if str(c.get("uuid")) in keep]
    if len(kept) == len(candidates) and not misses and not ignored:
        return None
    kinds = dict(resolved_kinds)
    for fold, _typed in tokens:
        if fold in kinds and isinstance(kinds[fold], dict):
            row = {k: v for k, v in kinds[fold].items() if k != "product"}
            if per_token.get(fold):
                row["product"] = per_token[fold]
            kinds[fold] = row
    compatible = [
        e
        for e in compatible_entities
        if not isinstance(e, dict) or e.get("entity_type") != "product" or str(e.get("uuid")) in keep
    ]
    placed = {**{k: v for k, v in unplaced.items() if k not in ignored}, **misses}
    return kinds, {**resolved_candidates, "product": kept}, compatible, placed


def _is_bare_all(message: str) -> bool:
    text = re.sub(r"\s+", " ", str(message or "").strip().lower()).rstrip(".!? ")
    return bool(_BARE_ALL.fullmatch(text))


_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
}
#: AVAIL-MODE-REPLIES (owner v2 note 2): "<qty> of <option>" over a stock pick - "2 of 3",
#: "2 of the third one", "2 of 3rd product", "2 pcs of no 3". The number after "of" is
#: always the option. The live parser read "2 of 3" as option 2, quantity 3 (tester-local
#: pass on 7fa5d654, step 7), so the engine reads the shape itself, as it reads "all".
_QTY_OF_OPTION = re.compile(
    r"(\d+)\s*(?:pcs|pc|units?|nos|x)?\s+of\s+(?:the\s+)?(?:no\.?\s*|number\s+|#)?"
    r"(\d+|" + "|".join(_ORDINALS) + r")(?:st|nd|rd|th)?(?:\s+(?:one|product|code|item))?",
    re.IGNORECASE,
)


def _quantity_of_option(message: str) -> list[tuple[int, int]] | None:
    """`[(option, quantity), ...]` when the message's own words pair each quantity with an
    option ("2 of 1 and 5 of 3"), else None."""
    pairs = []
    for qty, option in _QTY_OF_OPTION.findall(str(message or "")):
        position = _ORDINALS.get(option.lower()) or (int(option) if option.isdigit() else None)
        if position is None or int(qty) < 1:
            return None
        pairs.append((position, int(qty)))
    return pairs or None


def _with_the_engine_pick(verdict: dict[str, Any], pending: Any, message: str) -> dict[str, Any]:
    """The verdict with the engine's own reading of a bare pick (`_bare_roster_positions`).

    The engine's positions replace the parser's `reference_positions` and
    `open_question_answer`, and a bare number names no domain (`domain_in_message`
    false, no `asks`), so the pick is answered in the roster's domain (contract 121) and
    round 2's picked-axis rule then runs on these positions. Left untouched: no roster
    open, a message that is not a bare pick, a reading the parser did not make a pick at
    all (a quantity, a top-N count - the readers before this one already settled
    those), and a reading that already agrees.
    """
    stock_pick = pending is not None and bool((pending.payload or {}).get("stock_pick"))
    bare = str(message or "").strip().lower().rstrip(".!")
    if stock_pick and _BARE_POSITIONS.fullmatch(bare):
        # Owner hand test, 3 Oct 2026 ("I thought our picker mechanism is the same and
        # unified?"): a bare number on the list is THAT option, for a stock pick and a
        # did-you-mean exactly as for every other picker (`_bare_roster_positions`),
        # whatever the parser made of it (a product "2", a quantity 2). A number past the
        # list stays the pick's quantity (`apply._stock_pick_requantified`).
        offered_positions = {o.get("position") for o in pending.options}
        positions = sorted({int(n) for n in re.findall(r"\d+", bare)})
        if positions and all(p in offered_positions for p in positions):
            return {
                **verdict,
                "entities": [],
                "demand_qty": None,
                "reference_positions": positions,
                "open_question_answer": {"mode": "pick", "picked": positions, "items": [], "qty_for_all": None},
                "domain_in_message": False,
                "asks": [],
            }
    pairs = _quantity_of_option(message) if stock_pick else None
    offered = {o.get("position") for o in (getattr(pending, "options", None) or [])}
    if pairs and all(position in offered for position, _qty in pairs):
        positions = [position for position, _qty in pairs]
        return {
            **verdict,
            "reference_positions": positions,
            "demand_qty": pairs[0][1] if len(pairs) == 1 else None,
            "open_question_answer": {
                "mode": "pick",
                "picked": positions,
                "items": [{"position": p, "code": None, "qty": q} for p, q in pairs],
                "qty_for_all": None,
            },
            "domain_in_message": False,
            "asks": [],
        }
    if pending is not None and (pending.payload or {}).get("stock_pick") and _is_bare_all(message):
        # `apply._stock_pick_refuses_all` keeps the list open on exactly this reading.
        return {
            **verdict,
            "reference_positions": [],
            "open_question_answer": {"mode": "all", "picked": [], "items": [], "qty_for_all": None},
            "broaden_axis": "all",
        }
    positions = _bare_roster_positions(pending, message)
    if positions is None:
        return verdict
    answer = verdict.get("open_question_answer")
    read_as_pick = bool(verdict.get("reference_positions")) or (
        isinstance(answer, dict) and answer.get("mode") == "pick"
    ) or verdict.get("broaden_axis") == "all"
    if not read_as_pick:
        return verdict
    picked = {"mode": "pick", "picked": positions}
    if (
        verdict.get("reference_positions") == positions
        and (answer is None or answer == picked)
        and verdict.get("domain_in_message") is not True
        and not verdict.get("asks")
    ):
        return verdict
    return {
        **verdict,
        "reference_positions": positions,
        "open_question_answer": picked,
        "domain_in_message": False,
        "asks": [],
    }


def _option_words(option: dict[str, Any]) -> set[str]:
    """Every name a roster option goes by: its label, code, name, uuids and tier value."""
    payload = option.get("payload") if isinstance(option.get("payload"), dict) else {}
    values = [
        option.get("label"), option.get("code"), option.get("name"), option.get("uuid"),
        payload.get("value"), payload.get("tier"), *(option.get("uuids") or []),
    ]
    return {str(v).strip().lower().replace("_", " ") for v in values if isinstance(v, str) and v.strip()}


def _with_the_picked_axis(
    verdict: dict[str, Any], pending: Any, positions: list[int]
) -> dict[str, Any]:
    """The roster's own axis, off the option(s) the customer picked.

    PR #1353 fix round 2 (owner retest, v48, chatbot.turns 29 Sep 2026 10:22 MYT):
    "promo srtwc286" -> tier roster -> "1" Office -> "2" answered Office again. With
    `previous_conversation_state` carrying access_levels ["Sorento Office"], the parser
    echoed that level on the bare "2", and the resolver's tier gate
    (`lanes/business/tier_gate.tier_gate`) stated the tier off the parser's
    `access_levels`: Office, recomposed to all three Office levels, over the Dealer just
    picked. Owner ruling: the parser reports, the engine judges. On a bare pick the
    picked option IS the axis, so a carried value of it that names none of the picked
    options is dropped:

    * `tier_pick`: `access_levels` keeps each carried level naming a picked tier ("Sorento
      Dealer" over a Dealer pick) and adds the picked tier's own value for every picked
      tier no carried level names. `tier_gate` reads a bare tier value as stated.
    * any other roster (product, customer): an entity of the roster's kind naming no
      picked option is dropped - the parser's echo of the previous pick is not a second
      subject. The picked option itself reaches the focus through `_answer_pending`.
    """
    matched = [o for o in pending.options if o.get("position") in positions]
    if not matched:
        return verdict
    kind = str(matched[0].get("entity_type") or "")
    if pending.kind == "tier_pick" or kind == "tier":
        from app.services.chatbot.lanes.business.tier_gate import parse_level

        picked: list[str] = []
        for o in matched:
            payload = o.get("payload") if isinstance(o.get("payload"), dict) else {}
            value = o.get("code") or payload.get("value") or payload.get("tier") or o.get("label")
            if isinstance(value, str) and value and value.strip().lower() not in picked:
                picked.append(value.strip().lower())
        carried = [a for a in (verdict.get("access_levels") or []) if isinstance(a, str)]

        def tier_of(level: str) -> str:
            parsed = parse_level(level)
            return parsed["tier"] if parsed else level.strip().lower().replace(" ", "_")

        kept = [a for a in carried if tier_of(a) in picked]
        named = {tier_of(a) for a in kept}
        levels = kept + [t for t in picked if t not in named]
        entities = [
            e for e in (verdict.get("entities") or [])
            if not (isinstance(e, dict) and e.get("hint") == "tier" and str(e.get("canonical_code") or e.get("raw") or "").strip().lower() not in picked)
        ]
        if levels == carried and len(entities) == len(verdict.get("entities") or []):
            return verdict
        return {**verdict, "access_levels": levels, "entities": entities}
    if not kind:
        return verdict
    words: set[str] = set()
    for o in matched:
        words |= _option_words(o)

    def names_a_pick(e: dict[str, Any]) -> bool:
        return any(
            isinstance(v, str) and v.strip().lower().replace("_", " ") in words
            for v in (e.get("uuid"), e.get("canonical_code"), e.get("raw"), e.get("name"))
        )

    entities = verdict.get("entities") or []
    kept_entities = [
        e for e in entities if not (isinstance(e, dict) and e.get("hint") == kind and not names_a_pick(e))
    ]
    if len(kept_entities) == len(entities):
        return verdict
    return {**verdict, "entities": kept_entities}


@contextmanager
def _session(factory: SessionFactory) -> Iterator[Session]:
    db = factory()
    try:
        yield db
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Company scope. The engine calls ROUTE functions in process (the resolver, stock,
# promotions, product attachments), so the router dependency that stamps the caller's
# company scope onto the request session - `apply_company_scope` - never runs for them.
# An unstamped session reads UNSET, `build_company_predicate` compiles UNSET to
# `false()` for every owned model, and the turn answers "Couldn't find: <code>" for a
# product that exists in the contact's own company. Measured in prod and locally,
# 6 Sep 2026 (H56).
#
# So the turn resolves the contact's scope ONCE, from the SAME identity rule the
# X-API-Key path uses (`resolve_contact_company_scope`, shared with that dependency),
# and every session it opens afterwards carries it.
# --------------------------------------------------------------------------- #


def _contact_company_scope(factory: SessionFactory, contact_respond_id: str) -> frozenset:
    """The contact's companies, on a session of its own, before the turn starts.

    The lookup itself needs NO scope: `respond_workspaces`, `respond_contacts` and
    `respond_contact_companies` are plain `Base` models, none of them
    `CompanyScopedMixin`, so no scope filter applies to reading them (which is what
    makes resolving the scope from an unscoped session sound rather than circular).

    Fail-closed on every unhappy path: an unknown contact, a contact with no company
    row, no default workspace, or a lookup that raised all give an EMPTY frozenset
    (0 owned rows), never `None` (which would mean every company).
    """
    from app.services.company_scope_resolver import resolve_contact_company_scope

    db = factory()
    try:
        return resolve_contact_company_scope(db, contact_respond_id, default_space_id(db))
    except Exception:  # noqa: BLE001 - a scope lookup must never fail the turn
        logger.warning(
            "chatbot: company scope lookup failed for contact %s, failing closed",
            contact_respond_id,
            exc_info=True,
        )
        return frozenset()
    finally:
        db.close()


def _scoped_factory(factory: SessionFactory, scope: frozenset) -> SessionFactory:
    """`factory`, wrapped so every session it opens is stamped with `scope`.

    The FACTORY is wrapped rather than each of the engine's ~15 `_session` call sites
    changed, because the factory is also what the lanes get (`answer_services_for`
    opens its own session from it for the family read) - one seam covers both, and a
    session opened anywhere in the turn cannot be missed.
    """

    def open_scoped_session() -> Session:
        db = factory()
        set_company_scope(db, scope)
        return db

    return open_scoped_session


# --------------------------------------------------------------------------- #
# Envelope readers. Each names the n8n node whose read it reproduces, so the
# by-name hazard the port removes stays traceable.
# --------------------------------------------------------------------------- #


def _refer_tracked(fn: Any) -> Any:
    """CUSTOMER-ASKS-REFER-ONLY: one refer mark per turn (`turn/refer.py`). A tail called
    from inside the head shares the head's mark."""
    import functools

    from app.services.chatbot.turn import refer

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with refer.tracking():
            return fn(*args, **kwargs)

    return wrapper


def _tf_message(envelope: Envelope) -> dict[str, Any]:
    """`tf-message`: the respond.io webhook body carried on the envelope."""
    return envelope.message or {}


def _inner_message(envelope: Envelope) -> dict[str, Any]:
    """`ctx.text.message.message` - the respond.io message payload itself."""
    return jsc.get(jsc.get(_tf_message(envelope), "message"), "message") or {}


def _message_id(envelope: Envelope) -> str | None:
    value = jsc.get(jsc.get(_tf_message(envelope), "message"), "messageId")
    return str(value) if value not in (None, "") else None


def _contact_respond_id(envelope: Envelope) -> str:
    """`sorento-sub-respond-findcontact-respond`'s `id`, carried on the envelope (D1).

    Presence is guaranteed by `Envelope`'s own validator, so a caller that omits it gets a
    422 naming the field instead of a 500 from in here. The guard stays as a belt: this is
    also reachable from `app/tasks/chat_turns.py` (S7) with an envelope rebuilt from a
    stored row, and a silent empty string would key a turn to nobody.
    """
    contact_id = jsc.get(envelope.contact, "id")
    if contact_id in (None, ""):  # pragma: no cover - Envelope validation catches it first
        raise ValueError("envelope.contact.id is required")
    return str(contact_id)


def _is_human_intervened(envelope: Envelope) -> bool:
    """`is-human-intervened`: `custom_fields.find(...)?.value?.toBoolean() == true`."""
    return memory_mod.contact_is_human_intervened(envelope.contact)


def _attachment_type(envelope: Envelope) -> Any:
    return jsc.get(jsc.get(_inner_message(envelope), "attachment"), "type")


def _reply_to_message_id(envelope: Envelope) -> str | None:
    """`tf-message.message.replyTo.id` - the quoted message, when the customer quoted."""
    value = jsc.get(jsc.get(jsc.get(_tf_message(envelope), "message"), "replyTo"), "id")
    return str(value) if value not in (None, "") else None


def build_latest_user_message(envelope: Envelope, session_block: Any = None) -> str:
    """The two-line string `Call 'sub-query-reformulator'` builds today, verbatim.

    The live expression (`export/live-spine-sorento-consume-main/workflow.json`, the
    `Call 'sub-query-reformulator'` node) chains THREE alternatives on line 1:
    the message text, an attachment's description, then `$json.message`. All three are
    reproduced. The third reads the node's own input - `get-session-vars`'s response,
    `{respond_io_id, session_vars}` - which carries no `message` key today, so it
    contributes nothing on any captured turn. It is implemented anyway rather than
    written off as dead: "verbatim" has to mean it, and the day that response grows a
    `message` key the port would otherwise diverge in silence.

    Line 1 is the text, or an image's description when there is no text. Line 2 is the
    quoted message n8n appends as `reply to: ...`; several ported blocks split it back off
    with `/\\s*reply to:/i`, so the exact shape (including the trailing newline) matters.
    """
    inner = _inner_message(envelope)
    text = jsc.get(inner, "text")
    if not jsc.truthy(text):
        text = jsc.get(jsc.get(inner, "attachment"), "description")
    if not jsc.truthy(text):
        text = jsc.get(session_block, "message")  # the third alternative, `|| $json.message`
    line1 = jsc.js_string(text) if jsc.truthy(text) else ""

    reply_to = jsc.get(jsc.get(_tf_message(envelope), "message"), "replyTo")
    quoted = jsc.get(reply_to, "message")
    line2 = ""
    if jsc.truthy(quoted):
        quoted_text = jsc.get(quoted, "text")
        body = quoted_text if jsc.truthy(quoted_text) else jsc.get(quoted, "title")
        line2 = "reply to: " + jsc.js_string(body) if jsc.truthy(body) else "reply to: "
    return f"{line1}\n{line2}\n"


# O2 / AC-112: the keys a DRY-RUN envelope may carry so a harness can drive a turn with no
# LLM and none of the contact's real memory. Declared in ONE order, and that order is what
# `harness_keys_ignored` reports, so two traces diff readably.
#
# `Envelope` is `extra="allow"`, so they arrive as extras rather than as declared fields -
# deliberately: they are a HARNESS contract, not part of the envelope every injector sends,
# and declaring them would invite a live producer to start setting them.
#
# `prompt_overrides` (S8a, AC-807) joined at the end: `{prompt_key: version_id}`, so the
# Prompts screen can run a real turn against the version an operator is EDITING rather than
# the published one. It belongs here and not on the envelope proper for the same reason the
# other three do, plus one of its own - pinning an unpublished prompt version is exactly a
# live producer must never be able to do, and being dry-run-only is what guarantees it.
HARNESS_KEYS = (
    "mock_reformulator_output",
    "previous_conversation_state",
    "referenced_result_set",
    "prompt_overrides",
)


def _prompt_override(envelope: Envelope, prompt_key: str, *, dry_run: bool) -> str | None:
    """The version id this dry run pins for `prompt_key`, or None.

    Returns None on a LIVE turn whatever the envelope says. The harness keys are already
    ignored when `dry_run` is false, and this is the one whose leak would mean a customer
    answered by an unpublished prompt, so it is checked here as well rather than relying on
    the caller having checked.
    """
    if not dry_run:
        return None
    overrides = _harness_value(envelope, "prompt_overrides")
    if not isinstance(overrides, dict):
        return None
    value = overrides.get(prompt_key)
    return str(value) if value else None


def _harness_keys_present(envelope: Envelope) -> list[str]:
    """Which harness keys this envelope carries, in the declared order.

    Membership, not truthiness: `previous_conversation_state: {}` is a harness saying "this
    contact remembers NOTHING", which is a different instruction from not saying anything.
    """
    extra = envelope.model_extra or {}
    return [key for key in HARNESS_KEYS if key in extra]


def _harness_value(envelope: Envelope, key: str) -> Any:
    return (envelope.model_extra or {}).get(key)


def _chat_console(envelope: Envelope) -> bool:
    """A dry run the chat console page sent (owner ruling 28 Sep 2026, chatbot stock ask
    v2): D14's `dry_run`, `ingress == "console"`, and the `console_origin` extra that only
    `console_service`'s two envelope builders set. The Prompts screen's "Run a turn" also
    says `ingress: console` but never sets the extra, so it is not one.

    `console_origin` is an extra rather than a declared field for the reason `HARNESS_KEYS`
    gives, and so the pinned external contract (AC-1507) is unchanged. Only the stock ask
    hooks read this (`_run_answer`); every other D14 guard still reads `dry_run` alone.
    """
    return bool(
        envelope.dry_run
        and envelope.ingress == "console"
        and _harness_value(envelope, "console_origin") is True
    )


def _inject_harness_session(
    session_block: dict[str, Any], envelope: Envelope
) -> dict[str, Any]:
    """G8: replace the stored memory with the harness's, FOR THIS TURN ONLY.

    Applied to the whole `session_block` rather than to the local `variables`, because the
    same object becomes `ctx.session` and the `received` record's `raw` - a turn whose
    trace showed the contact's real memory while the lane ran on injected memory would be
    the worst kind of unreadable.

    Fix 3 (console defect, contact 437264483, 23 Sep 2026): `session_state.five_keys`
    returns the STORED top-level five keys whenever ANY of them is present on
    `session_vars`, and only falls through to the `variables` nest when none is - so
    writing the harness value into `variables` alone, leaving the stored row's own
    top-level `focus`/`open_question`/etc untouched, meant injection silently did
    nothing for any contact whose stored row was already in the new five-key shape
    (every contact since #952). Two shapes the harness value can take, told apart by
    `session_state.FIVE_KEYS` membership (the same membership rule `_harness_keys_
    present` already uses: `{}` is a real instruction, not "nothing to do"):

    * carries any of the five keys (the console's own echo of `result.session_vars`),
      or is `{}` ("remembers nothing"): every one of the five keys is set from it
      (`value.get(key)`, so an omitted key becomes `None`) - the harness state
      REPLACES the memory for this turn, it does not merge with the stored row's.
    * neither (the legacy flat shape, `contracts.LegacyVariables`): unchanged
      behaviour - it is written to `variables`, and the five keys are stripped from
      `session_vars` so `session_state.five_keys` falls through to its legacy
      projection instead of reading the (now absent) stored top-level keys.

    A harness value that is not a dict at all (e.g. JSON `null`) is treated the same
    as `{}` - the memory is wiped for this turn. The console never sends one; this
    only matters for a hand-built envelope.

    Nothing here writes: the head persists no session state at all (the tail does, at S2),
    and D14 already forbids that write on a dry run. The guarantee is asserted by
    `TestHarnessInjectionsG8::test_the_injected_state_is_never_written_back`.
    """
    present = _harness_keys_present(envelope)
    if not ({"previous_conversation_state", "referenced_result_set"} & set(present)):
        return session_block
    session_vars = dict(jsc.get(session_block, "session_vars") or {})
    if "previous_conversation_state" in present:
        value = _harness_value(envelope, "previous_conversation_state")
        value = value if isinstance(value, dict) else {}
        session_vars.pop("variables", None)
        for key in session_state.FIVE_KEYS:
            session_vars.pop(key, None)
        if value == {} or any(key in value for key in session_state.FIVE_KEYS):
            for key in session_state.FIVE_KEYS:
                session_vars[key] = value.get(key)
        else:
            session_vars["variables"] = value
    if "referenced_result_set" in present:
        session_vars["referenced_result_set"] = _harness_value(envelope, "referenced_result_set")
    return {**session_block, "session_vars": session_vars}








def _read_session_vars(db: Session, *, respond_io_id: str, reply_to_id: str | None) -> dict:
    """`get-session-vars`: the same body `GET /external/conversation-variables/{id}` returns."""
    from app.services.conversation_variables_service import (
        get_for_contact,
        get_referenced_result_set,
        get_referenced_state,
    )

    state = get_for_contact(db, respond_io_id=respond_io_id)
    if reply_to_id is not None:
        state = {
            **state,
            "referenced_result_set": get_referenced_result_set(
                db, respond_io_id=respond_io_id, message_id=reply_to_id
            ),
            "referenced_state": get_referenced_state(
                db, respond_io_id=respond_io_id, message_id=reply_to_id
            ),
        }
    return {"respond_io_id": respond_io_id, "session_vars": state}


def _select_turn(
    db: Session,
    *,
    contact_respond_id: str,
    message_id: str | None,
    is_test: bool | None = None,
):
    """The raw lookup. Kept separate from `_existing_turn` so the post-collision retry
    below can re-read WITHOUT going through whatever a test has wrapped around the public
    helper - the forced-TOCTOU test synchronises on `_existing_turn`, and a second trip
    through that barrier would deadlock the very path being fixed.

    The ORDER BY is the ONE place the "which row" question is answered, for the head and
    for the id-less `/turn/complete` alike (`find_turn_for_message`). The HIGHEST attempt
    wins, agreed with the n8n side: when S2b's retry puts a second row on a message, the
    retry is the row being watched, and completing (or replaying) the older one would fold
    a lane's result into a row nobody is looking at and leave the live one delegated
    forever; `created_at` breaks a tie for a row written before `attempt` was populated.

    **`is_test` narrows the pair to one WORLD (H57), and `None` means "either".** D15's
    dedup question is "has this respond message already been turned into a turn", and a
    TEST turn is not an answer to it: without the flag a dry run from the Prompts screen
    against a real contact shadowed the live delivery of that same `messageId`, which came
    back `duplicate: true` carrying the test row's canned reply, so the customer was sent
    nothing at all. The head therefore always passes its envelope's own `dry_run`; the
    id-less complete route passes nothing, because the body it holds does not say which
    world it is in and it must be able to complete either (a clone turn completes by body
    too). With both a test and a live row on one pair, that route gets the NEWEST, which
    is the turn whose lane result is arriving."""
    if message_id is None:
        return None
    filters = [
        ChatbotTurn.contact_respond_id == contact_respond_id,
        ChatbotTurn.message_id == message_id,
    ]
    if is_test is not None:
        filters.append(ChatbotTurn.is_test.is_(is_test))
    return (
        db.query(ChatbotTurn)
        .filter(*filters)
        # NEWEST first: with a retry there can be several rows for one message, and the
        # one that matters is the last one - the earlier attempts are settled history.
        # `attempt` leads because it is the retry's own counter (S2b writes old + 1), so
        # it answers the question directly; `created_at` only breaks a tie for a row
        # written before `attempt` was populated.
        .order_by(ChatbotTurn.attempt.desc(), ChatbotTurn.created_at.desc())
        .first()
    )


def _awaiting_retry(row) -> bool:
    """Is this row a FAILED turn an operator asked to re-run (S2b)?

    D15 says a respond message already turned into a turn is a duplicate, and a retry
    re-posts that same message - same `message_id` - so without this it would come back
    as `duplicate: true` and Retry would be a no-op that reported success. The marker is
    what tells the two apart: an operator asked for this one.
    """
    return row is not None and row.status == "failed" and row.retry_requested_at is not None


def find_turn_for_message(db: Session, *, contact_respond_id: str, message_id: str | None):
    """Which row is this message's turn? Asked by the ENDPOINT as well as by the head.

    The id-less `/turn/complete` identifies a turn from `(contact, respond message id)`,
    which is the same question `_existing_turn` asks on arrival, so it asks it through the
    same lookup instead of writing a second `ORDER BY`: the HIGHEST attempt, once for both
    readers. Two orderings over one pair would disagree about which row IS the turn the
    moment a second row for a message existed (S2b's retry), and the disagreement would
    surface as a lane result folded into a row nobody is watching. Public because
    `app/api/v1/external/chat.py` is a doorway file; the package still EXPORTS only
    `run_turn` / `complete_turn` (D3).
    """
    return _select_turn(db, contact_respond_id=contact_respond_id, message_id=message_id)


def _existing_turn(
    db: Session, *, contact_respond_id: str, message_id: str | None, is_test: bool = False
):
    """D15: has this respond message already been turned into a turn IN THIS WORLD?

    Checked with a SELECT rather than left to the unique index so a legitimate double
    delivery (webhook plus failover poller) costs a lookup, not an exception and an LLM
    call. The index is the real guarantee under concurrency, and the collision it raises
    is caught in `run_turn` - the SELECT alone is a TOCTOU window, not a lock.

    `is_test` is the envelope's own `dry_run` (H57): a test row must never make a live
    delivery a duplicate, nor the other way round. The unique index carries `is_test` for
    the same reason (migration 481).
    """
    return _select_turn(
        db,
        contact_respond_id=contact_respond_id,
        message_id=message_id,
        is_test=is_test,
    )


def _insert_turn(
    db: Session,
    *,
    envelope: Envelope,
    contact_respond_id: str,
    retrying: ChatbotTurn | None = None,
    queued: bool = False,
) -> ChatbotTurn:
    """The turn row. `retrying` is the failed row an operator asked to re-run (S2b).

    `queued` (issue #1262): the row waits for its ticket at stage `queued`, which is what
    lets an earlier-arrived turn find it and answer it first when it was SENT first
    (`send_order`). The request claims it back with `_claim_own_row` once its ticket
    comes up.

    A retry is a NEW turn, not an edit of the old one: same message, next attempt, ingress
    `retry`. The old row keeps its trace and its failure - that is the record the operator
    was reading - and only loses its retry marker, so a second failure can be retried too.
    """
    row = ChatbotTurn(
        contact_respond_id=contact_respond_id,
        message_id=_message_id(envelope),
        ingress="retry" if retrying is not None else envelope.ingress,
        envelope=trace_mod.cap_document(envelope.model_dump(mode="json")),
        is_test=envelope.dry_run,
        status="processing",
        stage=send_order.QUEUED_STAGE if queued else "received",
        attempt=(retrying.attempt + 1) if retrying is not None else 1,
        trace=[],
        shadow_of=getattr(envelope, "shadow_of", None),
        started_at=_now(),
    )
    db.add(row)
    if retrying is not None:
        # Consumed. Leaving it set would make the NEXT delivery of this message look like
        # another requested re-run rather than the duplicate it is.
        retrying.retry_requested_at = None
    db.commit()
    db.refresh(row)
    return row


# The two states a turn ENDS in. `delegated` is not one of them: it is the handover the
# tail runs from, and the tail closes the row a second time when it finishes.
_TERMINAL_STATUSES = frozenset({"done", "failed"})


def _close_turn(
    db: Session,
    turn_id: str,
    *,
    status: str,
    stage: str | None,
    branch_kind: str | None,
    error: str | None,
    records: list[dict[str, Any]],
    response: dict[str, Any] | None = None,
) -> None:
    """Write the turn's outcome. FIRST terminal write wins.

    Not tidiness: a failure inside the tail closes the row itself (`failed` at
    `remembered`, where it really stopped) and then RE-RAISES, and the lane handler that
    called it catches that same exception and closes again (`failed` at `replied`). The
    second write is strictly less true than the first - it names the stage of the caller
    rather than the stage of the failure - so it is refused rather than allowed to win.

    The `delegated` handover is deliberately NOT terminal: `close_turn_for_tail` writes it
    before the tail runs and `complete_turn` supersedes it with `done`, which is the
    two-phase close every completed lane makes.
    """
    assert stage is None or stage in TURN_FAILURE_STAGES, (
        f"{stage!r} is not a declared turn stage - a typo here lands in the column and "
        f"reads as an unknown state on the trace screen. Declared: {TURN_FAILURE_STAGES}"
    )
    row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
    if row is None:  # pragma: no cover - the row was inserted two lines earlier
        return
    if row.finished_at is not None and row.status in _TERMINAL_STATUSES:
        # Debug, not warning: this is an EXPECTED sequence on the failure path (the tail
        # closes, re-raises, the lane handler catches), and an operator reading warnings
        # would be sent to look for a bug that is not there. What matters is that the row
        # keeps the first record; the second is only interesting when reading the log.
        logger.debug(
            "chatbot turn %s is already %s at %s; refused a second close as %s at %s",
            turn_id,
            row.status,
            row.stage,
            status,
            stage,
        )
        return
    row.status = status
    row.stage = stage
    row.branch_kind = branch_kind
    row.error = error
    row.trace = records
    # D15 needs the ORIGINAL answer, not just the fact that a turn happened: a duplicate
    # delivery replays this, and n8n's `build-ctx` / `route-turn` re-emitters would throw
    # on a null. It is also what S2b's Retry reads. Written HERE, at close, which is what
    # bounds the guarantee - see `_duplicate_result`.
    row.response = trace_mod.cap_document(response)
    row.finished_at = _now()
    db.commit()


def _record_parser_usage(
    db: Session,
    *,
    usage: dict[str, Any],
    started: float,
    contact_respond_id: str,
    dry_run: bool,
    answered: bool,
    turn_id: str | None = None,
) -> None:
    """One `ai_assistant_usage_logs` row for the turn's parser call.

    LIVE turns only (D14: a test envelope writes nothing outside `chatbot.turns`), and
    only when the provider reported something - a stubbed parse has no spend to record.
    Written on the session that is open anyway, so it costs no extra connection.
    """
    if dry_run or not usage:
        return
    record_parser_usage(
        db,
        usage=usage,
        response_time_ms=int((time.perf_counter() - started) * 1000),
        contact_respond_id=contact_respond_id,
        answered=answered,
        chatbot_turn_id=turn_id,
    )


def _duplicate_result(row: ChatbotTurn) -> TurnResult:
    """D15: the same respond message arrived twice. Replay the FIRST turn's answer.

    **`ctx` and `item` can still be null here, and that is not a defect to fix in the
    engine.** `response` is written by `_close_turn`, so it exists only once the first
    turn has FINISHED. Two cases return nulls:

    * the first turn is still `processing` - which is the LIKELY timing, not the edge
      case: a webhook delivery and a poller re-delivery arrive within the same second or
      two, well inside the 5 to 10 seconds a parse plus an access check takes;
    * the first turn `failed` BEFORE a lane owned it - the early stages (`intake`,
      `received`, `understood`, `access`) close the row with no `response` at all, so
      there is nothing to replay. A failure AFTER a lane owns the turn is different and
      deliberately so: both lane failure paths (`_run_business_answer`'s catch-all and
      the fetch-outage close in `_run_stages`) store the full shape, error reply
      included, so a duplicate of one of those replays the words the caller has already
      sent - which is exactly what stops it sending them twice.

    Making the second caller WAIT for the first would put a poll loop on a synchronous
    request for a message the caller must not answer twice anyway, which buys nothing:
    whatever it waited for, it still sends nothing. **The actual guarantee is on the n8n
    side** - the Switch on `duplicate` sits BEFORE the `build-ctx` / `route-turn`
    re-emitters, so a null `ctx` is never dereferenced (plan, S1 n8n section; AC-110).
    The response body is a courtesy for the trace screen and for a caller that wants to
    log what the original turn decided, never a contract the caller may depend on.

    `status` is the row's own, so `processing` (in flight), `failed` and `delegated` /
    `done` are all distinguishable: a caller that sees `duplicate: true, status:
    processing` knows the nulls mean "not finished yet", not "nothing to say".

    **`is_test` here is the ORIGINAL ROW's flag, not this call's envelope, and that is
    deliberate** (security review round 2, N5). Everywhere else the head stamps
    `result.is_test` from the CURRENT envelope, because the flag describes the call; on
    this path the whole answer describes the first turn, so its flag has to come from the
    same place its `reply` and `actions` do. Stamping the caller's flag on somebody else's
    answer would produce a result whose top-level field and whose per-action `dry_run`
    disagreed.

    Since H57 the two can no longer disagree anyway: the dedup lookup is narrowed to the
    envelope's OWN `is_test` (`_existing_turn`), so a duplicate is always a duplicate of a
    row from the same world and this flag always equals the caller's. The cross-world case
    the previous paragraph used to describe - a test envelope reading back `is_test: false`
    off a live row - is gone, because a test envelope for a message that already ran live
    now runs as its own turn instead of being answered from the customer's.
    """
    response = row.response if isinstance(row.response, dict) else {}
    return TurnResult(
        turn_id=str(row.id),
        is_test=bool(row.is_test),
        ctx=response.get("ctx"),
        item=response.get("item"),
        branch_kind=row.branch_kind,
        # D15: replay what the FIRST turn decided, never recompute it. The row's status
        # already records it, and recomputing would hand a different answer back for the
        # same message if `chatbot_completed_lanes` changed in between - which is the one
        # thing a duplicate must not do.
        delegate=row.branch_kind if row.status == "delegated" else None,
        delegate_payload=response.get("delegate_payload"),
        reply=response.get("reply"),
        actions=response.get("actions") or [],
        duplicate=True,
        # The row's own status, unmodified: `processing` means the first turn is still in
        # flight and the nulls above are "not yet", not "never".
        status=row.status,
        stage=row.stage,
    )


# --------------------------------------------------------------------------- #
# Send order (issue #1262): earlier-SENT messages are answered first
# --------------------------------------------------------------------------- #


def _claim_own_row(session_factory: SessionFactory, turn_id: str) -> bool:
    """The waiting request takes its own `queued` row back once its ticket comes up.

    False means a predecessor that held the slot claimed it first, because this message
    was sent before that one (`send_order`), and has already answered it.
    """
    with _session(session_factory) as db:
        return send_order.claim(db, turn_id)


def _answered_by_predecessor(session_factory: SessionFactory, turn_id: str) -> TurnResult:
    """This message was answered by the turn ahead of it. Replay that answer as a
    duplicate: its actions already went out on that turn's response, so the caller's
    Switch on `duplicate` sends nothing a second time."""
    with _session(session_factory) as db:
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
        return _duplicate_result(row)


def _answer_earlier_messages(
    envelope: Envelope,
    *,
    session_factory: SessionFactory,
    turn_id: str,
    contact_respond_id: str,
    contact_scope: frozenset,
    switches: "_TurnSwitches",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Answer, oldest first, every earlier-sent message of this contact the CRM already
    knows about and has not answered, within `send_order.within_bounds`. Returns their
    actions, in send order, and the facts this turn's own trace records about them.

    Each is answered as its own turn, on its own row with its own trace, exactly as its
    own delivery would have been, so the focus it leaves is what this turn then reads.

    Best effort, all of it: a failure here costs the ordering, never this turn, and never
    the answers already given (their rows are closed, so their own deliveries will be
    duplicates and these actions are the only copy that gets sent).
    """
    try:
        with _session(session_factory) as db:
            me = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
            if me is None:  # pragma: no cover - inserted by the caller
                return [], {}
            earlier = send_order.earlier_unanswered(db, contact_respond_id=contact_respond_id, me=me)
            if not earlier:
                return [], {}
            from app.services.media_access_service import resolve_media_settings

            media_wait = float(resolve_media_settings(db).sync_wait_seconds)
    except Exception:  # noqa: BLE001 - ordering is an improvement, not a precondition
        logger.warning(
            "chatbot send order: could not look up earlier messages for %s; answering "
            "this turn alone",
            contact_respond_id,
            exc_info=True,
        )
        return [], {}

    budget = send_order.N8N_CHAT_TURN_TIMEOUT_SECONDS
    if getattr(settings, "chatbot_turn_on_worker", False):
        # Offloaded, the API stops waiting on the worker sooner than n8n stops waiting
        # on the API.
        budget = min(budget, float(getattr(settings, "chatbot_turn_wait_seconds", 60)))
    taken, left, reasons = send_order.within_bounds(
        earlier,
        media_wait_seconds=media_wait,
        own_carries_media=media_intake.detect(_inner_message(envelope)) is not None,
        budget_seconds=budget,
    )
    left_count = len(left)
    answered = 0

    mine = envelope.model_dump(mode="json")
    my_message_id = _message_id(envelope)
    out: list[dict[str, Any]] = []
    for item in taken:
        if item.row_id is None and item.job_id is not None:
            # Today's path (pre-S6): n8n's `media-route` replies on its own when its
            # extraction fails or outlives its wait. Answering such a photo here too
            # would send the customer a second message, so only a photo that has been
            # READ is answered ahead; any other is left to its own delivery, with no row.
            try:
                status = media_intake.await_existing_job(
                    item.job_id, timeout_seconds=media_wait, session_factory=session_factory
                )
            except Exception:  # noqa: BLE001 - see the docstring: best effort
                logger.warning(
                    "chatbot send order: could not wait on an earlier photo of %s; it is "
                    "left to its own delivery",
                    contact_respond_id,
                    exc_info=True,
                )
                status = None
            if status != "completed":
                # Failed, still being read, or the wait raised: left to its own delivery,
                # and the take goes on. A ledger-only photo reaches `/chat/turn` after
                # every queued row, or never (n8n answers it on its reply arm), so
                # stopping here would put a known, earlier-sent text behind this turn
                # and gain the photo nothing (review round 3, S1).
                if "photo_not_read" not in reasons:
                    reasons.append("photo_not_read")
                left_count += 1
                continue
        try:
            if item.row_id is not None:
                earlier_envelope = Envelope.model_validate(item.envelope)
            else:
                earlier_envelope = Envelope.model_validate(send_order.ledger_envelope(mine, item))
        except ValueError:
            logger.warning(
                "chatbot send order: could not rebuild the envelope of an earlier message "
                "for %s; it is left to its own delivery",
                contact_respond_id,
                exc_info=True,
            )
            continue
        try:
            with _session(session_factory) as db:
                if item.row_id is not None:
                    if not send_order.claim(db, item.row_id):
                        continue
                    earlier_id = item.row_id
                else:
                    try:
                        earlier_id = str(
                            _insert_turn(db, envelope=earlier_envelope, contact_respond_id=contact_respond_id).id
                        )
                    except IntegrityError:
                        # Its own delivery reached the CRM in the meantime and owns it now.
                        db.rollback()
                        continue
        except Exception:  # noqa: BLE001 - see the docstring: best effort
            logger.warning(
                "chatbot send order: could not take an earlier message of %s; it is left "
                "to its own delivery",
                contact_respond_id,
                exc_info=True,
            )
            continue
        answered += 1
        try:
            result = _answer_claimed(
                earlier_envelope,
                session_factory=session_factory,
                turn_id=earlier_id,
                contact_respond_id=contact_respond_id,
                contact_scope=contact_scope,
                switches=switches,
                answered_ahead_by={"turn_id": turn_id, "message_id": my_message_id},
            )
        except Exception:  # noqa: BLE001 - its stages raised, and so did closing its row
            logger.warning(
                "chatbot send order: an earlier message of %s failed and its row could not "
                "be closed; answering this turn anyway",
                contact_respond_id,
                exc_info=True,
            )
            continue
        out.extend(result.actions or [])
    return out, {
        "earlier_answered_ahead": answered,
        "earlier_left_for_own_turns": left_count,
        "earlier_left_because": ", ".join(reasons) if reasons else None,
    }


def _answer_claimed(
    envelope: Envelope,
    *,
    session_factory: SessionFactory,
    turn_id: str,
    contact_respond_id: str,
    contact_scope: frozenset,
    switches: "_TurnSwitches",
    answered_ahead_by: dict[str, Any],
) -> TurnResult:
    """One earlier message's turn, on its own row: `run_turn`'s stages without the ticket
    (the caller already holds the contact's slot). A failure is recorded on that row and
    still hands back the error reply, as it would have on its own delivery.

    `answered_ahead_by` names the turn carrying this answer, for the row's first trace
    record: an operator can then tell why this reply went out on that turn's response."""
    turn_trace = trace_mod.TurnTrace()
    turn_trace.start()
    stage: list[str] = ["received"]
    actions: list[dict[str, Any]] = []
    try:
        media_box: dict[str, Any] = {"answered_ahead_by": answered_ahead_by}
        result = _run_stages(
            envelope,
            session_factory=session_factory,
            turn_trace=turn_trace,
            turn_id=turn_id,
            contact_respond_id=contact_respond_id,
            contact_scope=contact_scope,
            dry_run=False,
            actions=actions,
            stage=stage,
            switches=switches,
            media_box=media_box,
        )
        _apply_media_reply_prefix(result, media_box.get("outcome"))
        if media_box.get("outcome") is not None:
            _repersist_media_prefixed_reply(session_factory, turn_id, result, False)
        return result
    except Exception as exc:  # noqa: BLE001 - a failed turn is recorded, never dropped
        message = f"{type(exc).__name__}: {exc}"
        logger.exception("chatbot turn %s failed at stage %s", turn_id, stage[0])
        turn_trace.record(
            stage[0],  # type: ignore[arg-type]
            status="failed",
            summary="The turn stopped before it could be answered.",
            why="Something the turn depends on did not respond as expected.",
            facts={"stage": stage[0]},
            error=message,
            raw=None,
        )
        with _session(session_factory) as db:
            _close_turn(
                db,
                turn_id,
                status="failed",
                stage=stage[0],
                branch_kind=None,
                error=message,
                records=turn_trace.persisted(),
            )
        return _failed_result(turn_id, stage[0], message, actions, False)


# --------------------------------------------------------------------------- #
# The turn
# --------------------------------------------------------------------------- #


def _asks_outstanding(verdict: dict[str, Any]) -> bool:
    """Is this turn an OUTSTANDING order ask, in either vocabulary?

    R20's carve-out is decided on the ask's own delivery-status axis, and that axis is
    spelled twice on the wire: `status` is the v3 key the rearch reads, `order_status`
    the bucketed one an older prompt version (and every recorded verdict) still carries.
    Reading only one of them silenced the carve-out for half the corpus.
    """
    from app.services.chatbot.lanes.business.resolve_gate import OUTSTANDING_ORDER_STATUS

    if jsc.nullish_str(verdict.get("status")).strip() == "outstanding":
        return True
    return jsc.nullish_str(verdict.get("order_status")).strip() in OUTSTANDING_ORDER_STATUS


def _top_selling_dealer_scope(
    db: Session,
    parse_output: dict[str, Any],
    focus: Any,
    contact_respond_id: Any,
    space_id: str | None,
) -> dict[str, Any]:
    """Reviewer S2 on PR #1273 (owner: a dealer sees only its own customers; ruling
    pending owner confirmation). A linked dealer contact's top selling ask never
    reaches the generic customer resolver, whose picker lists every matching
    customer's name before the route could refuse. Its customer words are matched
    against its OWN ledgers only (`business_services.top_selling_dealer_ledgers`):
    all matched, the ranking runs on those ledgers (`dealer_customer_ids` on the slot);
    any word matching none of them, the lane refuses with the plain line and fetches
    nothing (`dealer_refused`). Staff and unlinked contacts are untouched here."""
    slot = focus.top_selling if isinstance(focus.top_selling, dict) else None
    if slot is not None:
        slot.pop("dealer_refused", None)
    own = business_services.top_selling_dealer_ledgers(db, contact_respond_id, space_id)
    if own is None:
        return parse_output
    entities = [e for e in (parse_output.get("entities") or []) if isinstance(e, dict)]

    def _is_customer(e: dict[str, Any]) -> bool:
        return e.get("hint") == "customer" or e.get("entity_type") == "customer"

    words = [jsc.js_string(e.get("raw")) for e in entities if _is_customer(e) and jsc.truthy(e.get("raw"))]
    focus.customers = []
    if slot is None:
        slot = focus.top_selling = {}
    if words:
        ids = business_services.top_selling_dealer_customer_ids(own, words)
        if ids is None:
            slot["dealer_refused"] = True
            slot.pop("dealer_customer_ids", None)
        else:
            slot["dealer_customer_ids"] = ids
    return {**parse_output, "entities": [e for e in entities if not _is_customer(e)]}


def _customer_scope_gate(
    scope: dict[str, Any] | None,
    verdict: dict[str, Any],
    focus: Any,
    parse_output: dict[str, Any],
    domains: Any,
) -> tuple[dict[str, Any], list[str] | None, bool, str | None]:
    """PLAN-chatbot-customer-scope-29sep.md D3: the upstream block, run BEFORE the
    resolver. Returns `(the resolver's input, the linked customer ids the turn is
    scoped to or None, refused)`.

    A contact linked to customers (`respond_contact_customers`) and holding no active
    office access type is scoped to them, in EVERY domain: the customer words it typed
    THIS message are matched against its OWN linked customers, and a word matching none
    of them refuses the turn (before the generic resolver, whose name probe and picker
    would list other customers, is asked about it). In the order domain the customer
    words are also stripped from the resolver's input and the linked customers are the
    turn's customers: the matched ones, or all of them for a message naming none
    (`self_reference` and a bare ask alike). Words carried from an earlier turn are never
    matched, so a refusal cannot lock the turns after it. `self_reference` ("my", "me",
    "our") means the links for staff too. Unlinked contacts, staff without "my" and top
    selling (its own block, `_top_selling_dealer_scope`) are untouched."""
    if not scope or focus.status == "top_selling":
        return parse_output, None, False, None
    self_reference = verdict.get("self_reference") is True
    enforced = bool(scope.get("enforced"))
    in_order = "order" in (domains or ())
    if not (enforced or (self_reference and in_order)):
        return parse_output, None, False, None
    entities = [e for e in (parse_output.get("entities") or []) if isinstance(e, dict)]

    def _is_customer(e: dict[str, Any]) -> bool:
        return e.get("hint") == "customer" or e.get("entity_type") == "customer"

    named = [
        e
        for e in entities
        if _is_customer(e) and e.get("current_message") is True and jsc.truthy(e.get("raw"))
    ]
    words = [jsc.js_string(e.get("raw")) for e in named]
    # ACCOUNT-LEDGER: the numbered account a word names, parallel to `words`; "my account 2"
    # names no customer (raw null) and narrows the links themselves.
    accounts = [_account_of(e) for e in named]
    my_account = next(
        (a for e in entities if _is_customer(e) and not jsc.truthy(e.get("raw")) and (a := _account_of(e))),
        None,
    )
    from app.services import contact_customer_scope as scope_mod

    links = scope_mod.ContactCustomerScope(
        linked=tuple((c, n, k) for c, n, k in scope["linked"]),
        staff=False,
        levels=dict(scope.get("levels") or {}),
    )
    without_customers = {**parse_output, "entities": [e for e in entities if not _is_customer(e)]}
    if not enforced:
        # Staff: "my" alone means the links; naming a customer is unscoped as today.
        if words:
            return parse_output, None, False, None
        ids = list(scope["ids"])
        if my_account is not None:
            ids = [c for c in ids if links.levels.get(c) == my_account]
            if not ids:
                return without_customers, None, True, None
        return parse_output, ids, False, None
    narrowed_refusal: str | None = None
    ids: list[str] | None = list(scope["ids"])
    if words:
        ids = links.match_words(words, accounts)
        if ids is None and any(accounts):
            # Q3: a refusal over an account names only the contact's ledgers of that name.
            of_name = links.match_words(words)
            if of_name is not None:
                narrowed_refusal = scope_mod.refusal_line_for(links, of_name)
    elif my_account is not None:
        ids = [c for c in ids if links.levels.get(c) == my_account] or None
    elif in_order and not self_reference and not _names_an_order_number(entities):
        # DO-ASK-SIMPLIFY tester pass 1: a message naming no customer ("this month",
        # answering the period question) continues the customer the conversation already
        # carries, kept inside the links; "my" means every link, and an order number is
        # looked up across every own account whatever the carry.
        link_ids = set(ids)
        carried = [
            str(row.get("uuid"))
            for row in getattr(focus, "customers", None) or []
            if isinstance(row, dict) and str(row.get("uuid")) in link_ids
        ]
        if carried:
            ids = list(dict.fromkeys(carried))
    if ids is None:
        return without_customers, None, True, narrowed_refusal
    if not in_order:
        return parse_output, None, False, None
    return without_customers, ids, False, None


def _with_account_answer(verdict: dict[str, Any], carried: Any) -> dict[str, Any]:
    """ACCOUNT-LEDGER S-3: the answer to "Which customer is Account N for?" gets Account N on
    its customer words that name no account of their own. Read from `focus.set_clarify`, which
    every other turn clears, so the number lives for that one answer only."""
    number = carried.get("account") if isinstance(carried, dict) else None
    if not isinstance(number, int) or isinstance(number, bool):
        return verdict
    entities = [
        {**e, "account": number}
        if isinstance(e, dict)
        and (e.get("hint") == "customer" or e.get("entity_type") == "customer")
        and jsc.truthy(e.get("raw"))
        and _account_of(e) is None
        else e
        for e in verdict.get("entities") or []
    ]
    return {**verdict, "entities": entities}


def _account_of(entity: dict[str, Any]) -> int | None:
    """The `account` the parser put on an entity: a whole number of at least 1, else None."""
    value = entity.get("account")
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def _unnamed_account(verdict: dict[str, Any], parse_output: dict[str, Any]) -> int | None:
    """ACCOUNT-LEDGER Q2: an Account number with no customer named THIS message (and no "my":
    `self_reference` names the links). Nothing is carried from an earlier turn to fill it."""
    if verdict.get("self_reference") is True:
        return None
    entities = [e for e in (parse_output.get("entities") or []) if isinstance(e, dict)]
    customers = [e for e in entities if e.get("hint") == "customer" or e.get("entity_type") == "customer"]
    if any(e.get("current_message") is True and jsc.truthy(e.get("raw")) for e in customers):
        return None
    return next((a for e in customers if not jsc.truthy(e.get("raw")) and (a := _account_of(e))), None)


def _screen_resolver_for_scope(
    scope: dict[str, Any],
    payload: dict[str, Any] | None,
    compatible: list[dict[str, Any]],
    candidates: dict[str, list[dict[str, Any]]],
) -> tuple[bool, list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[str]]:
    """The post-resolver filter behind the gate, for a contact whose scope is enforced and
    whatever the domain or hint (the resolver re-types brand, category, order and product
    tokens as customers under `order`). Every customer row outside the contact's own is
    dropped from the resolver's answer; a token whose ONLY matches were other customers,
    or a picker that listed any, refuses the turn. A DO number still resolves, but never
    carries the owning customer's name (`display`). Returns
    `(refused, compatible, candidates, dropped_ids)`; `dropped_ids` (CHATBOT-SELFREF-SCOPE
    R4) names the customer ids dropped, for the trace."""
    own = {str(i) for i in scope["ids"]}
    dropped_ids: list[str] = []

    def _foreign(row: Any) -> bool:
        return (
            isinstance(row, dict)
            and str(row.get("entity_type") or "").lower() == "customer"
            and str(row.get("uuid")) not in own
        )

    def _clean(rows: Any) -> list[Any]:
        kept = []
        for row in rows or []:
            if _foreign(row):
                if str(row.get("uuid")) not in dropped_ids:
                    dropped_ids.append(str(row.get("uuid")))
                continue
            if isinstance(row, dict) and str(row.get("entity_type") or "").lower() in ("customer_order", "order"):
                row = {k: v for k, v in row.items() if k not in ("display", "display_name")}
            kept.append(row)
        return kept

    refused = False
    dropped = False
    if isinstance(payload, dict):
        resolved = payload.get("resolved")
        if isinstance(resolved, dict):
            for resolution in resolved.get("resolutions") or []:
                if not isinstance(resolution, dict):
                    continue
                matches = resolution.get("matches") or []
                kept = _clean(matches)
                if len(kept) != len(matches):
                    dropped = True
                    if not kept:
                        refused = True
                resolution["matches"] = kept
            if isinstance(resolved.get("intersection"), list):
                resolved["intersection"] = _clean(resolved["intersection"])
            by_type = resolved.get("by_entity_type")
            if isinstance(by_type, dict):
                resolved["by_entity_type"] = {k: _clean(v) if isinstance(v, list) else v for k, v in by_type.items()}
        gate = payload.get("gate")
        if isinstance(gate, dict):
            for key in ("compatible_entities", "customer_probe_entities"):
                if isinstance(gate.get(key), list):
                    before = len(gate[key])
                    gate[key] = _clean(gate[key])
                    dropped = dropped or len(gate[key]) != before
        if dropped and payload.get("_exit_kind") == "offer":
            refused = True
    before = len(compatible)
    compatible = _clean(compatible)
    dropped = dropped or len(compatible) != before
    candidates = {
        kind: [r for r in rows if not _foreign(r) and not (str(kind).lower() == "customer" and str(r.get("uuid")) not in own)]
        if isinstance(rows, list)
        else rows
        for kind, rows in (candidates or {}).items()
    }
    return refused, compatible, candidates, dropped_ids


def _names_an_order_number(entities: list[dict[str, Any]]) -> bool:
    """Does this message name a DO/SO/order number (the parser's own order hints)?"""
    return any(
        jsc.js_string(e.get("hint") or e.get("entity_type") or "").strip().lower()
        in ("order", "customer_order", "order_number")
        and e.get("current_message") is not False
        for e in entities
    )


def _drill_offer_subject(scope_ids: list[str] | None, focus: Any, trace: Any) -> list[str] | None:
    """The accounts a sales report drill pick re-runs over, for a customer-scoped contact.

    PR #1401 fix round 3, F6/F7: a pick off the `sales_report_detail` offer settles the
    OFFER's own accounts onto the focus (`apply._settle_question_subject`), which after
    "sales of <one account>" is that one account. The scope gate then answered every
    link ("the linked customers are this turn's customers"), so "1" drilled over all of
    them. On an answering turn the offer's accounts, kept inside the links, are the subject.
    """
    answered = getattr(trace, "outstanding", None) or {}
    if scope_ids is None or answered.get("kind") != "sales_report_detail":
        return scope_ids
    links = set(scope_ids)
    settled = [
        str(e.get("uuid"))
        for e in getattr(focus, "customers", None) or []
        if isinstance(e, dict) and str(e.get("uuid")) in links
    ]
    return settled or scope_ids


def _scoped_compatible(
    scope: dict[str, Any], ids: list[str], compatible: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The resolver's rows with the linked customers `ids` as the only customer rows."""
    rows = [e for e in compatible if e.get("entity_type") != "customer"]
    for cid, name, code in scope["linked"]:
        if cid in ids:
            rows.append(
                {"uuid": cid, "entity_type": "customer", "code": code, "scope": True, **({"display_name": name} if name else {})}
            )
    return rows


def _pass_scope_gate(
    payload: dict[str, Any] | None, compatible: list[dict[str, Any]], *, force: bool
) -> dict[str, Any] | None:
    """The resolver's exit for a turn the scope gate answered. The linked customers are
    the subject the gate's "no entities and 'order' requires a scoping entity" arm asked
    for, so that arm (or, when `force`, any arm: the lane refuses before a fetch) is
    passed instead of ending in the miss text and its escalate offer."""
    if not isinstance(payload, dict):
        return payload
    gate = dict(payload.get("gate") if isinstance(payload.get("gate"), dict) else {})
    gate["compatible_entities"] = compatible
    if force or (
        gate.get("gate_passed") is False and str(gate.get("gate_reason") or "").startswith("no entities and")
    ):
        gate["gate_passed"] = True
        gate["gate_reason"] = "customer scope"
        gate["gate_clarification"] = ""
        exit_kind = "continue" if force or payload.get("_exit_kind") == "not_found" else payload.get("_exit_kind")
        return {**payload, "gate": gate, "_exit_kind": exit_kind}
    return {**payload, "gate": gate}


def _top_selling_narrowing(
    db: Session, parse_output: dict[str, Any], focus: Any, *, dealer: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Owner retest of top selling (27 Sep 2026, PR #1273): the words that narrow a
    ranking never reach the generic resolver, which read "fanny" (a sales agent) and
    "sorento" (a brand) as customers and answered "Could not find" for a brand under
    `order`. Returns the resolver's input without them, and the slot updates this
    message makes (`_apply_top_selling_updates` writes them after the resolver, so the
    resolver's re-entry into APPLY cannot drop them).

    * hint `category`: stripped; the lane resolves it (`category_words`, AC-1954).
    * hint `brand`, or a `customer` word that IS a brand's name or code: the brand
      filter (`brand_ids`); a word naming no brand is said once (`unknown`).
    * hint `sales_agent`: the agent filter (`agent_ids`); unknown is said once.
    * hint `customer` naming a sales agent: the agent, unless a customer carries the
      word too, then one question asks which (`who`). A dealer never has the customer
      table searched (its customer words are its own ledgers, `_top_selling_dealer_scope`).
    Only this message's own words are resolved; a carried row of these hints is just
    kept away from the resolver."""
    entities = [e for e in (parse_output.get("entities") or []) if isinstance(e, dict)]
    updates: dict[str, Any] = {}
    kept: list[dict[str, Any]] = []
    unknown: list[list[str]] = []

    def _add(key: str, ids: list[str]) -> None:
        bucket = updates.setdefault(key, [])
        bucket.extend(i for i in ids if i not in bucket)

    for e in entities:
        hint = jsc.js_string(e.get("hint") or e.get("entity_type") or "")
        raw = jsc.js_string(e.get("raw") or "").strip()
        current = e.get("current_message") is not False and not e.get("uuid")
        if hint == "category":
            continue
        if hint not in ("brand", "sales_agent", "customer"):
            kept.append(e)
            continue
        if not current or not raw:
            if hint == "customer":
                kept.append(e)
            continue
        if hint == "brand":
            brands = business_services.resolve_brand_token(db, raw)
            if brands:
                _add("brand_ids", [b[0] for b in brands])
            else:
                unknown.append(["brand", raw])
            continue
        if hint == "sales_agent":
            agents = business_services.resolve_sales_agent_token(db, raw)
            if agents:
                _add("agent_ids", [a[0] for a in agents])
            else:
                unknown.append(["sales agent", raw])
            continue
        brands = business_services.resolve_brand_token(db, raw, exact_only=True)
        if brands:
            _add("brand_ids", [b[0] for b in brands])
            continue
        agents = [] if dealer else business_services.resolve_sales_agent_token(db, raw)
        if not agents:
            kept.append(e)
            updates["customer_named"] = True
            continue
        customers = business_services.customers_named(db, raw)
        if customers:
            updates["who"] = {
                "word": raw,
                "customer_ids": [c[0] for c in customers],
                "customer_label": customers[0][1] if len(customers) == 1 else None,
                "agent_ids": [a[0] for a in agents],
                "agent_label": ", ".join(a[1] for a in agents),
            }
        else:
            _add("agent_ids", [a[0] for a in agents])
    if unknown:
        updates["unknown"] = unknown
    return {**parse_output, "entities": kept}, updates


def _without_carried_words(parse_output: dict[str, Any]) -> dict[str, Any]:
    """The resolver's input on a report a ranking handed over to (`focus.top_selling.
    hop`, PR #1273 round 7). What the ranking resolved is carried as resolved entities
    (the customer ids and labels on the focus); a word is never looked up again, least of
    all under another kind: "Jayden", the ranking's sales agent, came back from the
    customer resolver as SAMPLE JAYDEN, and "wt" as two customers whose names hold the
    letters. So a sales agent, category or brand word (the report cannot filter by them;
    `apply._hop_to_report` says so in one line) and any carried word with no id are
    dropped here."""
    kept = [
        e
        for e in (parse_output.get("entities") or [])
        if isinstance(e, dict)
        and jsc.js_string(e.get("hint") or e.get("entity_type") or "") not in ("sales_agent", "category", "brand")
        and (e.get("current_message") is not False or e.get("uuid"))
    ]
    return {**parse_output, "entities": kept}


def _apply_top_selling_updates(focus: Any, updates: dict[str, Any]) -> None:
    """Write `_top_selling_narrowing`'s updates onto the ranking's slot. A new agent,
    brand or customer word replaces that axis (the ranking is narrowed by what the
    customer names NOW, owner retest 27 Sep 2026)."""
    if not updates or focus.status != "top_selling":
        return
    slot = focus.top_selling if isinstance(focus.top_selling, dict) else {}
    for key in ("agent_ids", "brand_ids"):
        if key in updates:
            slot[key] = list(updates[key])
    if updates.get("customer_named"):
        slot.pop("customer_ids", None)
    if "who" in updates:
        slot["who"] = dict(updates["who"])
    if updates.get("unknown"):
        slot["unknown"] = [*(slot.get("unknown") or []), *[list(u) for u in updates["unknown"]]]
    focus.top_selling = slot


#: The words of the options "Do you mean customer X or sales agent Y? Reply 1 for the
#: customer, 2 for the sales agent." prints (`lanes/business._top_selling_ask_who`),
#: and the option each names. A reply carrying one of them picks that option, as a typed
#: label picks any other printed list (owner retest of round 4, 27 Sep 2026, R2).
#: Round 9 (owner hand test after round 8, 28 Sep 2026): "salesman" and "saleman" fell
#: to the low signal greeting; the other words people call either side mean the same.
TOP_SELLING_WHO_WORDS = {
    "customer": 1, "client": 1,
    "sales": 2, "agent": 2, "salesman": 2, "salesmen": 2, "salesperson": 2, "rep": 2,
}
_WORD_RE = re.compile(r"[a-z]+")
#: Words this short are matched exactly: one edit from "rep" is too many other words.
_WHO_TYPO_MIN_LEN = 4


def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _one_edit_apart(a: str, b: str) -> bool:
    """One insertion, deletion, substitution or swap of two neighbouring letters."""
    if a == b or abs(len(a) - len(b)) > 1:
        return a == b
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (
            len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]
        )
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1 :] == short for i in range(len(long_)))


def _who_word(word: str, *, typos: bool) -> int | None:
    """The option a word of the reply names, a typo one edit away included when `typos`."""
    if word in TOP_SELLING_WHO_WORDS:
        return TOP_SELLING_WHO_WORDS[word]
    if not typos or len(word) < _WHO_TYPO_MIN_LEN:
        return None
    near = {
        option
        for known, option in TOP_SELLING_WHO_WORDS.items()
        if len(known) >= _WHO_TYPO_MIN_LEN + 1 and _one_edit_apart(word, known)
    }
    return near.pop() if len(near) == 1 else None


def _top_selling_who_answer(db: Session, text: str, verdict: dict[str, Any], who: dict[str, Any]) -> int | None:
    """Which option of the open "customer or sales agent?" question this message
    picks: 1, 2, 0 for "neither" (the parser's own `is_affirmative: false`), or None
    when it answers something else. Round 9: a short reply is also read for typos of
    the option words ("saleman") and for the customer name or the agent's code or
    person label the question named ("WT", "SAMPLE - WILLIAM")."""
    bare = (text or "").strip()
    if bare in ("1", "2"):
        return int(bare)
    short = len(bare.split()) <= _TOP_SELLING_ANSWER_WORDS
    picked = {p for p in (_who_word(w, typos=short) for w in _words(text)) if p is not None}
    if 2 in picked:
        return 2
    if picked == {1}:
        return 1
    if short and bare:
        agents = {i for i, _ in business_services.resolve_sales_agent_token(db, bare)} & set(who.get("agent_ids") or [])
        customers = {i for i, _ in business_services.customers_named(db, bare)} & set(who.get("customer_ids") or [])
        if agents and not customers:
            return 2
        if customers and not agents:
            return 1
    hints = {e.get("hint") for e in (verdict.get("entities") or []) if isinstance(e, dict)}
    if "sales_agent" in hints:
        return 2
    if verdict.get("is_affirmative") is False:
        return 0
    return None


#: The options each ranking question prints (`lanes/business._top_selling_question`,
#: and the route's how-many reply), as the parser key its answer sets, the option words
#: and the values in the order the question lists them, so "1" / "2" is that option
#: (owner retest of round 5, 27 Sep 2026: "amount" under "By quantity or by amount?"
#: was read as a new order ask and the order list ran).
TOP_SELLING_OPTION_ANSWERS: dict[str, tuple[str, dict[str, str], tuple[str, ...]]] = {
    "metric": (
        "rank_by",
        {"qty": "quantity", "quantity": "quantity", "units": "quantity",
         "amount": "amount", "amt": "amount", "value": "amount", "rm": "amount"},
        ("quantity", "amount"),
    ),
    "group": (
        "rank_group",
        {"item": "item", "items": "item", "product": "item", "products": "item",
         "categories": "category", "category": "category"},
        ("item", "category"),
    ),
    "basis": (
        "basis",
        {"delivered": "delivered", "delivery": "delivered", "transferred": "delivered", "do": "delivered",
         "ordered": "ordered", "order": "ordered", "orders": "ordered", "booked": "ordered"},
        ("delivered", "ordered"),
    ),
}
#: An answer longer than this names more than the option; it is bound only when the
#: parser itself read it as the ranking.
_TOP_SELLING_ANSWER_WORDS = 4
_COUNT_RE = re.compile(r"^(?:top\s+)?(\d{1,3})$")


def _top_selling_question_answer(text: str, verdict: dict[str, Any], asked: Any) -> dict[str, Any] | None:
    """The parser keys the answer to the ranking question the last reply asked
    (`focus.top_selling.asked`), read off that question's own printed options, or None
    when the message does not answer it. The question lives on the focus, which the
    console carries between dry runs exactly as the contact's session does on WhatsApp,
    so the answer binds the same way on both (owner retest of round 5, 27 Sep 2026)."""
    bare = " ".join((text or "").lower().replace(",", " ").split()).strip(" .!?")
    words = _words(text)
    short = len(bare.split()) <= _TOP_SELLING_ANSWER_WORDS
    own = jsc.js_string(verdict.get("order_status") or "").strip() == "top_selling"
    if asked in TOP_SELLING_OPTION_ANSWERS:
        key, options, order = TOP_SELLING_OPTION_ANSWERS[asked]
        if verdict.get(key) in order:
            return None
        if bare in ("1", "2"):
            return {key: order[int(bare) - 1]}
        picked = {options[w] for w in words if w in options}
        if len(picked) == 1 and (short or own):
            return {key: picked.pop()}
        return None
    if asked == "how_many":
        match = _COUNT_RE.match(bare)
        if match and verdict.get("top_n") is None and int(match.group(1)) >= 1:
            return {"top_n": int(match.group(1))}
        return None
    if asked == "category":
        # "Which category do you mean? Reply with one code: ..." - the reply is the code.
        if bare and not bare.isdigit() and short and not verdict.get("entities"):
            return {"entities": [{
                "raw": (text or "").strip().strip(" .!?"), "hint": "category", "canonical_code": None,
                "current_message": True, "confident": True, "hint_confident": True,
            }]}
        return None
    return None


def _as_ranking_answer(verdict: dict[str, Any], **keys: Any) -> dict[str, Any]:
    """`verdict` re-read as a refinement of the ranking on screen: the top selling ask,
    no ask of its own, only its category and brand words kept."""
    kept = [
        e for e in (verdict.get("entities") or [])
        if isinstance(e, dict) and e.get("hint") in ("category", "brand")
    ]
    out = {
        **verdict,
        "message_type": "business_query",
        "domain_hint": "order",
        "intent_hint": "check_order",
        "order_status": "top_selling",
        "domain_in_message": None,
        "reference_positions": [],
        "entities": kept,
        "asks": None,
        # Never a document, a report status or a pick of the list on screen (PR #1273
        # round 7: "sales order?" arrived as `open_question_answer` picking row 1).
        "status": None,
        "document": None,
        "is_affirmative": None,
        "open_question_answer": dict(_NO_OPEN_QUESTION_ANSWER),
    }
    out.update(keys)
    return out


_NO_OPEN_QUESTION_ANSWER = turn_question.NO_ANSWER


def _positions_read_elsewhere(state: Any) -> bool:
    """Is a question that is NOT an `Open question:` object still waiting on a position
    (`turn_question.without_phantom_answer`): the ideation media menu, or one of the top
    selling questions the last reply asked (`focus.top_selling.asked`)."""
    ideation = state.ideation if isinstance(state.ideation, dict) else {}
    asked = (state.focus.top_selling or {}).get("asked") if isinstance(state.focus.top_selling, dict) else None
    return bool(ideation.get("pending_media")) or bool(asked)

#: Ranking words (PR #1273 round 7: "top 10 hot selling item by william in q1 2026" was
#: read as an order ask and fell into "Could not find order"). A rank word, an optional
#: count and up to two more words, then a selling word: "top 10 hot selling", "worst 20
#: selling", "most sold", "best sellers". "sold to hanlim" or "what did we sell" alone is
#: not a ranking and is left to the parser.
_RANKING_WORDS_RE = re.compile(
    r"\b(?:top|best|worst|worse|worsr|bottom|least|most|highest|lowest|hot|cold)\b"
    r"(?:\s+\d{1,3})?(?:\s+[a-z]+){0,2}?\s+(?:selling|sold|sellers?)\b"
)
_RANKING_COUNT_RE = re.compile(r"\b(?:top|best|worst|worse|worsr|bottom|least|lowest)\s+(\d{1,3})\b")
_RANKING_BOTTOM_WORDS = frozenset({"worst", "worse", "worsr", "bottom", "least", "lowest", "cold"})


def _ranking_words_claim(verdict: dict[str, Any], text: str) -> dict[str, Any] | None:
    """`verdict` re-read as the top selling ask when the message says one in so many
    words and the parser read it as something else; None otherwise. The parser's own
    entities and dates stand; the count and the direction are read off the words only
    when the parser gave none. A person ask is never claimed."""
    if jsc.js_string(verdict.get("order_status") or "").strip() == "top_selling":
        return None
    if verdict.get("message_type") == "request_for_help":
        return None
    lowered = (text or "").lower()
    if not _RANKING_WORDS_RE.search(lowered):
        return None
    out = {
        **verdict,
        "message_type": "business_query",
        "domain_hint": "order",
        "intent_hint": "check_order",
        "order_status": "top_selling",
        "domain_in_message": True,
        "status": None,
        "document": None,
        "reference_positions": [],
        "is_affirmative": None,
        "open_question_answer": dict(_NO_OPEN_QUESTION_ANSWER),
        "asks": None,
    }
    count = _RANKING_COUNT_RE.search(lowered)
    if out.get("top_n") is None and count and int(count.group(1)) >= 1:
        out["top_n"] = int(count.group(1))
    if out.get("rank_direction") is None and _RANKING_BOTTOM_WORDS & set(_words(lowered)):
        out["rank_direction"] = "bottom"
    return out


#: The basis words (PR #1273 round 7: "sales order?" was read as a pick of row 1, and
#: "based on sales order" left the basis on Delivered). A message made of one of these
#: and the filler below switches the ranking on screen to that basis. "can show me the
#: DO" is not one (round 5: the delivery order report), and neither is anything naming
#: more than the basis.
TOP_SELLING_BASIS_WORDS: dict[tuple[str, ...], str] = {
    ("sales", "order"): "ordered",
    ("sales", "orders"): "ordered",
    ("so",): "ordered",
    ("ordered",): "ordered",
    ("delivery", "order"): "delivered",
    ("delivery", "orders"): "delivered",
    ("do",): "delivered",
    ("delivered",): "delivered",
    ("delivery",): "delivered",
}
_BASIS_FILLER = frozenset({
    "based", "base", "basis", "on", "by", "in", "use", "using", "terms", "of", "i", "mean", "what", "about",
    "how", "then", "ok", "okay", "instead", "switch", "to", "please", "pls", "rank", "ranked", "ranking",
})


def _basis_words(text: str) -> str | None:
    words = [w for w in _words(text) if w not in _BASIS_FILLER]
    return TOP_SELLING_BASIS_WORDS.get(tuple(words))


#: The words a period alone is said in ("2025?", "july", "q1 2026", "in 2024", "last
#: year"), and the filler around them. PR #1273 round 7: over an open ranking a period
#: re-runs the ranking for it (round 5 R7), whatever the parser made of the message.
_MONTH_WORDS = frozenset({
    "jan", "january", "feb", "february", "mar", "march", "apr", "april", "may", "jun", "june", "jul", "july",
    "aug", "august", "sep", "sept", "september", "oct", "october", "nov", "november", "dec", "december",
})
_PERIOD_WORDS = _MONTH_WORDS | {"q1", "q2", "q3", "q4", "ytd", "quarter", "year", "month", "week", "today", "yesterday"}
_PERIOD_FILLER = frozenset({
    "in", "for", "of", "the", "what", "about", "how", "then", "ok", "okay", "i", "mean", "only", "just",
    "and", "to", "from", "until", "till", "please", "pls", "last", "this", "next", "year", "month",
    "quarter", "week",
})
_PERIOD_TOKEN_RE = re.compile(r"[a-z]+\d*|\d+")


def _period_only(text: str) -> bool:
    """Is the message only a period: a year, a month or a quarter (with filler)?"""
    tokens = _PERIOD_TOKEN_RE.findall((text or "").lower())
    if not tokens:
        return False
    named = False
    for token in tokens:
        if token.isdigit():
            if len(token) == 4 and 1900 <= int(token) <= 2100:
                named = True
            elif not (len(token) <= 2 and any(t in _MONTH_WORDS for t in tokens)):
                return False
        elif token in _PERIOD_WORDS:
            named = named or token not in _PERIOD_FILLER or token in ("year", "month", "quarter", "week")
        elif token not in _PERIOD_FILLER:
            return False
    return named


def _bare_year(text: str) -> int | None:
    """"2025" or "2025?": a year, never a rank (owner retest of round 4, R7)."""
    bare = (text or "").strip().rstrip("?").strip()
    if len(bare) == 4 and bare.isdigit() and 1900 <= int(bare) <= 2100:
        return int(bare)
    return None


def _split_noisy_token(db: Session, raw: str) -> tuple[list[dict[str, Any]], list[str]] | None:
    """Owner retest of round 4 (27 Sep 2026, R4): one entity carrying several things
    ("fanny water closet", "bathtub by sean") split into word groups, longest first,
    each matched against sales agents, categories, brands and customers in turn
    (`_classify_word_group`). The words the question's own options print ("sales
    agent", "customer") mark the kind of the group before them, and a leftover of three
    letters or more is returned to be said once. None when no group matches."""
    words = raw.split()
    if len(words) < 2:
        return None
    found: list[dict[str, Any]] = []
    leftover: list[str] = []
    i = 0
    while i < len(words):
        for size in range(len(words) - i, 0, -1):
            group = " ".join(words[i : i + size])
            hint = _classify_word_group(db, group)
            if hint:
                found.append({"raw": group, "hint": hint})
                i += size
                break
        else:
            word = words[i]
            if word.lower() in TOP_SELLING_WHO_WORDS:
                if TOP_SELLING_WHO_WORDS[word.lower()] == 2 and found and found[-1]["hint"] == "customer":
                    found[-1]["hint"] = "sales_agent"
            elif len(word) >= 3:
                leftover.append(word)
            i += 1
    if not found:
        return None
    return found, leftover


def _classify_word_group(db: Session, group: str) -> str | None:
    """The kind one word group names, matched strictly (a category by its code, name or
    class vocabulary term, never by containing a class word), so "fanny water closet"
    is not taken whole as the Water Closet class."""
    from app.services.product_class_signal import resolve_classes_for_term

    if group.lower() in TOP_SELLING_WHO_WORDS:
        return None
    if business_services.resolve_sales_agent_token(db, group):
        return "sales_agent"
    if business_services.resolve_category_token(db, group) or resolve_classes_for_term(db, group.lower()):
        return "category"
    if business_services.resolve_brand_token(db, group, exact_only=True):
        return "brand"
    if business_services.customers_named(db, group):
        return "customer"
    return None


def _resolves_whole(db: Session, raw: str, hint: str) -> bool:
    if hint == "sales_agent":
        return bool(business_services.resolve_sales_agent_token(db, raw))
    if hint == "category":
        return bool(business_services.resolve_category_token(db, raw)) or bool(
            business_services.resolve_category_class(db, raw)[0]
        )
    if hint == "brand":
        return bool(business_services.resolve_brand_token(db, raw))
    if hint == "customer":
        return bool(business_services.customers_named(db, raw))
    return False


def _noisy_split(db: Session, raw: str, hint: str) -> tuple[list[dict[str, Any]], list[str]] | None:
    """The split of a noisy token, or None to keep it whole. A token naming a sales
    agent or a brand beside another kind always splits ("fanny water closet"); any
    other token splits only when it does not resolve whole under its own hint (so
    "water tap" stays the Tap class and "SAMPLE - FANNY NG" stays one customer)."""
    split = _split_noisy_token(db, raw)
    if split is None:
        return None
    parts, _rest = split
    kinds = {p["hint"] for p in parts}
    if len(kinds) > 1 and kinds & {"sales_agent", "brand"}:
        return split
    if hint in ("customer", "sales_agent", "category", "brand") and _resolves_whole(db, raw, hint):
        return None
    return split


def _top_selling_verdict(
    db: Session, verdict: dict[str, Any], state: Any, text: str
) -> tuple[dict[str, Any], Any, str | None]:
    """Owner retest of top selling round 4 (27 Sep 2026, PR #1273): inside a ranking
    conversation the message is read against the question the bot asked, before the
    parser's reading can send it anywhere else. Returns the verdict to apply, the state
    (a customer picker the correction dismisses is closed) and the rule that fired.

    * R1/R2 an answer to "customer or sales agent?" ("2", "yeah sales agent", "fanny
      sales agent", "neither") binds to that question only (`top_selling_who`).
    * R3 "neither" or "customer is everyone" over a customer picker the ranking opened
      clears the customer; it never picks every row of the picker.
    * R7 over a ranked list only a bare 1 to N picks a rank; "2025" or "2025?" is the
      year, anything else is a new message (the positions are dropped).
    * R6 an out of scope reading inside a ranking asks one short question instead.
    * R2/R4 a customer word the message calls an agent is the agent; a word carrying
      several things splits into its parts, the leftover said once.

    Round 7 (owner retest of round 5, part 2): ranking words ("top 10 hot selling") are
    the ranking whatever the parser's status; an echoed word from an earlier turn is
    dropped; basis words switch the basis; a period alone re-runs the ranking for it.
    """
    focus = state.focus
    slot = focus.top_selling if isinstance(focus.top_selling, dict) else None
    ranking = focus.status == "top_selling" or bool(slot and slot.get("hop"))
    # An out of scope reading inside a ranking keeps round 5's one short question (R6).
    claimed = None if ranking and verdict.get("message_type") == "out_of_scope" else _ranking_words_claim(verdict, text)
    if claimed is not None:
        verdict = claimed
    if slot is not None:
        # R1 (round 7): a word the parser echoes from an earlier turn (`current_message:
        # false`, no id) is never read again: the ranking already resolved it, and the
        # report it hands over to carries what it resolved (`apply._hop_to_report`).
        entities = [e for e in (verdict.get("entities") or []) if isinstance(e, dict)]
        fresh = [e for e in entities if e.get("current_message") is not False or e.get("uuid")]
        if len(fresh) != len(entities):
            verdict = {**verdict, "entities": fresh}
    asks_ranking = jsc.js_string(verdict.get("order_status") or "").strip() == "top_selling"
    if not (ranking or asks_ranking):
        return verdict, state, None
    pending = state.pending
    if ranking and slot and slot.get("asked") == "who" and isinstance(slot.get("who"), dict):
        answer = _top_selling_who_answer(db, text, verdict, slot["who"])
        if answer is not None:
            return _as_ranking_answer(verdict, top_selling_who=answer), state, "top_selling_who_answer"
    if focus.status == "top_selling" and slot and slot.get("asked"):
        answer_keys = _top_selling_question_answer(text, verdict, slot.get("asked"))
        if answer_keys is not None:
            return _as_ranking_answer(verdict, **answer_keys), state, "top_selling_question_answer"
    if focus.status == "top_selling" and claimed is None:
        # R3 (round 7): "sales order?", "based on sales order", "by SO", "ordered" switch
        # the ranking on screen to Basis: Ordered; "delivered", "by DO" switch it back.
        basis = _basis_words(text)
        if basis is not None:
            return _as_ranking_answer(verdict, basis=basis), state, "top_selling_basis_words"
        # R2 (round 7): a year, a month or a quarter alone re-runs the ranking for it,
        # whether or not a ranked list is open to pick from.
        if _period_only(text):
            if verdict.get("date_filter_start") or verdict.get("date_filter_end") or verdict.get("date_mode"):
                return _as_ranking_answer(verdict), state, "top_selling_period"
            year = _bare_year(text)
            if year is not None:
                return (
                    _as_ranking_answer(
                        verdict, date_filter_start=f"{year}-01-01", date_filter_end=f"{year}-12-31", date_mode=None
                    ),
                    state,
                    "top_selling_period",
                )
    if ranking and pending is not None and pending.kind == "top_selling_pick":
        year = _bare_year(text)
        if year is not None:
            return (
                _as_ranking_answer(
                    verdict, date_filter_start=f"{year}-01-01", date_filter_end=f"{year}-12-31", date_mode=None
                ),
                state,
                "top_selling_year_not_rank",
            )
        bare = (text or "").strip()
        in_list = bare.isdigit() and 1 <= int(bare) <= len(pending.options)
        answer = verdict.get("open_question_answer")
        if isinstance(answer, dict) and answer.get("mode") == "pick" and not in_list:
            # The same rule for the parser's other way of picking a row (round 7).
            verdict = {**verdict, "open_question_answer": dict(_NO_OPEN_QUESTION_ANSWER)}
        if verdict.get("reference_positions") and not in_list:
            verdict = {**verdict, "reference_positions": []}
            if verdict.get("message_type") in ("casual", "clarification", "low_signal", None):
                return _as_ranking_answer(verdict), state, "top_selling_not_a_rank"
    if ranking and pending is not None and pending.kind == "customer_pick":
        widens = broaden_level(verdict) == EVERYTHING and broaden_kind(verdict) in (None, "customer")
        says_agent = any(TOP_SELLING_WHO_WORDS.get(w) == 2 for w in _words(text))
        if widens or says_agent or verdict.get("is_affirmative") is False:
            entities = [
                {**e, "hint": "sales_agent"} if says_agent and e.get("hint") == "customer" else e
                for e in (verdict.get("entities") or [])
                if isinstance(e, dict)
            ]
            state = dataclasses_replace(state, pending=None)
            return (
                _as_ranking_answer(
                    {**verdict, "entities": []},
                    entities=[e for e in entities if e.get("hint") in ("category", "brand", "sales_agent")],
                    broaden_axis="customer",
                    broaden_to="all",
                    correction=True,
                ),
                state,
                "top_selling_customer_picker_dismissed",
            )
    if ranking and verdict.get("message_type") == "out_of_scope":
        return _as_ranking_answer(verdict, top_selling_unclear=True), state, "top_selling_unclear"
    entities = [e for e in (verdict.get("entities") or []) if isinstance(e, dict)]
    says_agent = any(TOP_SELLING_WHO_WORDS.get(w) == 2 for w in _words(text))
    rebuilt: list[dict[str, Any]] = []
    leftover: list[str] = []
    changed = False
    for e in entities:
        hint = jsc.js_string(e.get("hint") or "")
        raw = " ".join(jsc.js_string(e.get("raw") or "").split())
        current = e.get("current_message") is not False and not e.get("uuid")
        if current and raw and hint == "specification" and not e.get("spec_key"):
            # Merge of main into PR #833 (fix round 13): the specification kind splits
            # "fanny water closet" into the class and an UNKNOWN specification "fanny"
            # before this reading; inside a ranking such a word is a group like any other.
            # A word that names nothing is the leftover said once ("marble").
            kind = _classify_word_group(db, raw)
            split = ([{"raw": raw, "hint": kind}], []) if kind else _split_noisy_token(db, raw)
            if split is None:
                split = ([], [w for w in raw.split() if len(w) >= 3 and w.lower() not in TOP_SELLING_WHO_WORDS])
        elif not current or not raw or hint not in ("customer", "sales_agent", "category", "brand", "promotion", "product"):
            rebuilt.append(e)
            continue
        else:
            split = _noisy_split(db, raw, hint)
        if split is None:
            if hint == "customer" and says_agent and business_services.resolve_sales_agent_token(db, raw):
                # "by sean salea agent": the message calls the name an agent (R2).
                rebuilt.append({**e, "hint": "sales_agent"})
                changed = True
            else:
                rebuilt.append(e)
            continue
        parts, rest = split
        for part in parts:
            if says_agent and part["hint"] == "customer" and business_services.resolve_sales_agent_token(db, part["raw"]):
                part["hint"] = "sales_agent"
            rebuilt.append({**e, "raw": part["raw"], "hint": part["hint"], "canonical_code": None, "hint_confident": True})
        leftover.extend(rest)
        changed = True
    if not changed:
        return verdict, state, "top_selling_ranking_words" if claimed is not None else None
    out = {**verdict, "entities": rebuilt}
    if leftover:
        out["top_selling_leftover"] = leftover
    if not asks_ranking and out.get("domain_hint") not in (None, "", "order"):
        # A word the parser sent to a promotion or document lookup, inside a ranking.
        out = _as_ranking_answer(out, entities=rebuilt)
    return out, state, "top_selling_split_token"


@_refer_tracked
def run_turn(
    envelope: Envelope, *, session_factory: SessionFactory, offload: bool | None = None
) -> TurnResult:
    """Run the head of one turn. NEVER raises for a business failure; records it.

    Three things happen before the stages, in this order and for these reasons: the D15
    dedup (a duplicate answers from the first turn and takes no ticket), the per-contact
    ticket (AC-709 - taken before the INSERT, because the insert is the widest thing that
    could reorder two messages a customer sent one after the other), and the row insert.
    Everything after that is wrapped, so an unexpected exception anywhere - a provider
    error while resolving config, an access-service failure, the stock predicate throwing
    on a contact row it could not read - closes the turn as `failed` with the
    stage it reached and hands the caller today's error reply. A turn left at `processing`
    with a null error and no trace is exactly the dropped turn H32 is about. The ticket is
    released in a `finally` around all of it.

    `offload` exists for ONE caller: the RQ job (`app/tasks/chat_turns.py`) passes `False`
    so the worker actually runs the turn instead of enqueuing it to itself forever. Every
    other caller leaves it None and gets whatever `CHATBOT_TURN_ON_WORKER` says.
    """
    contact_respond_id = _contact_respond_id(envelope)
    # H56. BEFORE the offload branch, so the invariant reads the same on every entry:
    # every session this turn opens - the dedup read, the stages, the lanes' own family
    # read, and the offload path's row close - carries the contact's company scope.
    # Resolved once, on a session of its own, from the same rule the X-API-Key
    # dependency uses. An unknown contact fails closed to zero rows.
    contact_scope = _contact_company_scope(session_factory, contact_respond_id)
    session_factory = _scoped_factory(session_factory, contact_scope)

    if offload is None:
        offload = bool(getattr(settings, "chatbot_turn_on_worker", False))
    if offload:
        return _run_on_worker(envelope, session_factory=session_factory)

    turn_trace = trace_mod.TurnTrace()
    turn_trace.start()

    message_id = _message_id(envelope)
    dry_run = envelope.dry_run

    # ONE read of the settings singleton for the whole turn, and it happens HERE rather
    # than at routing because S7 mode is a settings column now (AC-810) and the ticket is
    # taken before the row insert. The snapshot travels into `_run_stages`, which used to
    # do this read itself: two reads would be a second round trip for no new information,
    # and worse, a turn could order itself under S7 mode and then route as though it were
    # off. Read on the session the dedup already needs, never on one of its own.
    switches = _TurnSwitches()
    ordered = False
    ticket: int | None = None
    heartbeat: Any = None
    redis = None
    turn_started = time.monotonic()
    try:
        with _session(session_factory) as db:
            switches = _read_switches(db)
            # H57: a DRY RUN takes no ticket, waits on nothing and marks nothing. The
            # ordering keys (`chatbot:seq|done|running:{contact}`, dispatch.py) are keyed
            # on the contact id and are SHARED with that contact's live traffic, so a test
            # turn run from the Prompts screen against a real contact would otherwise take
            # a real place in the queue the customer's next WhatsApp message waits on - and
            # a test turn must never delay a real customer. Nothing is lost: ordering
            # decides the order of REPLIES to one contact, and a dry run sends none.
            ordered = _s7_mode(db, switches) and not dry_run
            existing = _existing_turn(
                db,
                contact_respond_id=contact_respond_id,
                message_id=message_id,
                is_test=dry_run,
            )
            retrying = existing if _awaiting_retry(existing) else None
            if existing is not None and retrying is None:
                # D15: the two injectors delivered the same respond message. No second turn
                # runs, no second LLM call, and the caller's Switch on `duplicate` sends
                # nothing. NO TICKET has been taken at this point, on purpose: a duplicate
                # that took one and released it immediately would advance the counter past
                # a turn that is still running and let its successor start beside it.
                return _duplicate_result(existing)

            if ordered:
                # AC-709. The ticket is taken HERE - after the dedup read, before the row
                # INSERT - and the position is the guarantee. `chatbot.turns.created_at`
                # is this session's transaction start, i.e. the moment the request reached
                # the engine; taking the ticket after the insert instead left the whole
                # write between the two, and under a burst that window is tens of
                # milliseconds of connection setup and commit. Two messages 50 ms apart
                # then took their tickets in the WRONG order and the CRM answered them
                # backwards - measured, 2 inversions in 6 turns, by
                # `tests/chatbot/test_s7_poller_batch_order.py` before this moved.
                redis = _ordering_redis()
                try:
                    ticket = dispatch.contact_ticket(redis, contact_respond_id)
                    # Alive from the take to the release, the wait included: a successor
                    # must see this turn as alive while it is still queued too.
                    try:
                        heartbeat = dispatch.start_heartbeat(
                            redis, contact_respond_id, ticket
                        )
                    except RuntimeError:
                        # No thread to be had ("can't start new thread"). The turn still
                        # runs; its stamp lapses after `dispatch.ALIVE_TTL_SECONDS`, after
                        # which successors stop waiting for it.
                        logger.error(
                            "chatbot ordering: no heartbeat for ticket %s of %s",
                            ticket,
                            contact_respond_id,
                            exc_info=True,
                        )
                except dispatch.ORDERING_ERRORS:
                    # Redis is not answering. Run the turn UNORDERED rather than failing
                    # it: out-of-order replies are a degradation, a chatbot that answers
                    # nothing is an outage, and until this flag existed a redis blip cost
                    # this path nothing at all.
                    logger.warning(
                        "chatbot ordering: redis is unavailable, running this turn for %s "
                        "unordered",
                        contact_respond_id,
                        exc_info=True,
                    )
                    ticket = None

            try:
                row = _insert_turn(
                    db,
                    envelope=envelope,
                    contact_respond_id=contact_respond_id,
                    retrying=retrying,
                    queued=ticket is not None,
                )
            except IntegrityError:
                # The SELECT above is a TOCTOU window, not a lock: a webhook delivery
                # racing a poller re-delivery can both miss and both insert. The unique
                # index is the real guarantee, so the loser reads the winner's row rather
                # than 500ing. The ticket it took is released by the `finally` below.
                db.rollback()
                winner = _select_turn(
                    db,
                    contact_respond_id=contact_respond_id,
                    message_id=message_id,
                    is_test=dry_run,
                )
                if winner is None or _awaiting_retry(winner):
                    # Either some OTHER constraint failed, or two retries of one message
                    # raced - neither is a duplicate, and guessing would answer the
                    # customer from a turn that never ran.
                    raise
                return _duplicate_result(winner)
            turn_id = str(row.id)

        # The stage the turn is currently in, for the catch-all below. A plain list because
        # the inner stages update it and the handler reads it.
        stage: list[str] = ["received"]
        actions: list[dict[str, Any]] = []
        earlier_actions: list[dict[str, Any]] = []
        send_order_facts: dict[str, Any] = {}
        if ticket is not None:
            # `stage[0]` carries `queued` through the wait, so the handler below files a
            # `QueueWait` under the stage it actually happened in without a special case
            # (AC-710). The wait itself happens after the row exists, so a queue timeout is
            # a recorded turn and not a vanished one.
            stage[0] = "queued"
        try:
            if ticket is not None:
                # Chatbot memory lane A round 3 (AC-MEM014): the drawer's own Order
                # panel reads this back through `trace_detail._order`, off the
                # "queued" STAGE record itself - folded in rather than a separate
                # "queue" event, so a successful wait and a queue TIMEOUT (the
                # `except Exception` handler below, same stage) carry `ticket` in
                # the one place. A turn that never queued (S7 off, or unordered)
                # never gets a "queued" stage record at all.
                queue_wait_started = time.monotonic()
                try:
                    timed_out = False
                    try:
                        dispatch.wait_for_turn(
                            redis,
                            contact_respond_id,
                            ticket,
                            timeout_s=dispatch.queue_wait_seconds(
                                getattr(settings, "chatbot_queue_wait_seconds", 30.0)
                            ),
                        )
                    except dispatch.QueueWait:
                        # CHATBOT-QUEUE-FIX (prod 1 Oct): a predecessor still alive past
                        # the cap no longer fails this turn with the generic error. The
                        # customer gets an answer, possibly out of order. A predecessor
                        # may be answering THIS message right now (`send_order`), which
                        # the claim below still catches.
                        timed_out = True
                        logger.warning(
                            "chatbot ordering: ticket %s for %s waited the full queue "
                            "budget behind a live predecessor, running it anyway",
                            ticket,
                            contact_respond_id,
                        )
                    turn_trace.record(
                        "queued",  # type: ignore[arg-type]
                        status="ok",
                        summary=(
                            "Waited the full queue budget for an earlier message; answering now."
                            if timed_out
                            else "Waited for this contact's earlier messages to finish."
                        ),
                        why="Replies to one contact are sent in the order the messages arrived.",
                        facts={
                            "ticket": ticket,
                            "wait_ms": int((time.monotonic() - queue_wait_started) * 1000),
                            **({"timed_out": True} if timed_out else {}),
                        },
                    )
                except dispatch.ORDERING_ERRORS:
                    # Redis went away mid-wait. Same call as above: answer unordered
                    # rather than not at all.
                    logger.warning(
                        "chatbot ordering: redis is unavailable mid-wait, running turn "
                        "%s unordered",
                        turn_id,
                        exc_info=True,
                    )
                stage[0] = "received"
                if not _claim_own_row(session_factory, turn_id):
                    # Sent before the turn that held the slot, so that turn answered it
                    # first (issue #1262). Its answer went out on that turn's response.
                    return _answered_by_predecessor(session_factory, turn_id)
            # Issue #1262: every earlier-SENT message of this contact the CRM already
            # knows about is answered before this one, on its own row, and its actions go
            # out ahead of this turn's. No timer: only messages the CRM has seen count.
            if not dry_run:
                earlier_actions, send_order_facts = _answer_earlier_messages(
                    envelope,
                    session_factory=session_factory,
                    turn_id=turn_id,
                    contact_respond_id=contact_respond_id,
                    contact_scope=contact_scope,
                    switches=switches,
                )
            media_box: dict[str, Any] = {"send_order": send_order_facts} if send_order_facts else {}
            result = _run_stages(
                envelope,
                session_factory=session_factory,
                turn_trace=turn_trace,
                turn_id=turn_id,
                contact_respond_id=contact_respond_id,
                contact_scope=contact_scope,
                dry_run=dry_run,
                actions=actions,
                stage=stage,
                switches=switches,
                media_box=media_box,
            )
            _apply_media_reply_prefix(result, media_box.get("outcome"))
            # Guarded on `media_box.get("outcome")` (not just inside the helper): a
            # plain text turn - the overwhelming majority - must not pay for a SELECT
            # that only ever matters when a media outcome actually ran.
            if media_box.get("outcome") is not None:
                _repersist_media_prefixed_reply(session_factory, turn_id, result, dry_run)
            # D14: `is_test` is decided on the ENVELOPE, so it belongs on every answer the
            # head returns, whichever arm produced it. Stamped at this ONE exit rather than
            # on each arm's own `TurnResult`, which is exactly how three arms - the canned
            # block, `_run_casual_lane` and `_run_escalation_arm` - came to leave it false
            # on a turn that wrote nothing. Every action already carried its own `dry_run`;
            # this is the top-level field a caller switches the whole turn on. The worker
            # offload above rebuilds its result from a job that came through here, so it is
            # stamped too, and a duplicate reads `is_test` off the row it replays.
            result.is_test = dry_run
            if earlier_actions:
                result.actions = [*earlier_actions, *(result.actions or [])]
            return result
        except Exception as exc:  # noqa: BLE001 - a failed turn is recorded, never dropped
            message = f"{type(exc).__name__}: {exc}"
            logger.exception("chatbot turn %s failed at stage %s", turn_id, stage[0])
            # AC-MEM014: a turn that fails while still at "queued" carries its ticket
            # on this failure record, the one place `trace_detail._order` reads
            # `ticket`/`wait_ms` from. A queue TIMEOUT no longer lands here
            # (CHATBOT-QUEUE-FIX): the turn runs anyway.
            failure_facts: dict[str, Any] = {"stage": stage[0]}
            if stage[0] == "queued" and ticket is not None:
                failure_facts["ticket"] = ticket
            turn_trace.record(
                stage[0],  # type: ignore[arg-type]
                status="failed",
                summary="The turn stopped before it could be answered.",
                why="Something the turn depends on did not respond as expected.",
                facts=failure_facts,
                error=message,
                raw=None,
            )
            with _session(session_factory) as db:
                _close_turn(
                    db,
                    turn_id,
                    status="failed",
                    stage=stage[0],
                    branch_kind=None,
                    error=message,
                    records=turn_trace.persisted(),
                )
            failed = _failed_result(turn_id, stage[0], message, actions, dry_run)
            if earlier_actions:
                # The earlier-sent messages were answered on their own rows; their
                # replies still go out, ahead of this turn's error reply.
                failed.actions = [*earlier_actions, *failed.actions]
            return failed
    finally:
        # AC-704. In a `finally`, and one that covers the ROW INSERT and the WAIT as well
        # as the stages, because the ONE thing worse than a failed turn is a failed turn
        # that never releases its ticket: every later message from that contact would then
        # wait out the whole queue window and fail as well, and the customer would watch
        # one broken turn break the conversation. The turn most in need of releasing is
        # the one that gave up waiting (AC-710) - it is the one whose predecessor may be
        # dead - and a `finally` on the stages alone would be the only one to skip it.
        #
        # `mark_done` only settles the counter over finished or dead tickets, so
        # releasing out of order can neither rewind it nor pass a live predecessor. The
        # heartbeat stops FIRST so no late beat can resurrect the released key.
        if heartbeat is not None:
            heartbeat.stop()
        if ticket is not None:
            try:
                dispatch.mark_done(redis, contact_respond_id, ticket)
            except dispatch.ORDERING_ERRORS:
                # ERROR, not a warning: this is the alert. The successors are not stuck
                # (the stopped heartbeat lets the alive key lapse within
                # `ALIVE_TTL_SECONDS`), but each waits that long for nothing.
                logger.error(
                    "chatbot ordering: could not release ticket %s for %s",
                    ticket,
                    contact_respond_id,
                    exc_info=True,
                )
        _log_slow_turn(turn_trace, contact_respond_id, ticket, turn_started)


# A turn this long holds its contact's queue slot long enough to matter. One log line with
# every stage's `ms`, so the stage that held it is in the server log, not only on the trace
# screen (prod 1 Oct: turn a45f took 3m52s, all of it in `understood`).
SLOW_TURN_LOG_SECONDS = 30.0


def _log_slow_turn(turn_trace: Any, contact: str, ticket: int | None, started: float) -> None:
    elapsed = time.monotonic() - started
    if elapsed < SLOW_TURN_LOG_SECONDS:
        return
    try:
        stages = ", ".join(
            f"{r.get('stage')}={r.get('ms')}ms" for r in turn_trace.records if isinstance(r, dict)
        )
    except Exception:  # noqa: BLE001 - a log line must never fail a turn
        stages = "?"
    logger.warning(
        "chatbot slow turn: %.1fs for %s (ticket %s): %s", elapsed, contact, ticket, stages
    )


def _ordering_redis() -> Any:
    """The connection the ordering keys live on: the one the queues already use.

    Its `decode_responses=False` is why `dispatch._as_int` exists - the tests drive a
    decoding client and the engine does not, and a ticket counter that reads differently
    depending on who is asking would be the worst kind of intermittent.
    """
    return redis_conn


def _run_on_worker(envelope: Envelope, *, session_factory: SessionFactory) -> TurnResult:
    """AC-703. Run the turn on the `chat` queue and wait for it, inside this request.

    The caller's contract does not change: n8n still gets the finished turn on the same
    response. What changes is which process holds the LLM wait - an API thread, or a
    worker. Off by default; the trigger for turning it on is measured (the plan's capacity
    section: beyond ~250 concurrent turns the API threads, not the model, are the limit).

    Enqueue-and-wait is `app/api/v1/external/media.py`'s pattern, for its reason: a job
    row that outlives the request means a slow turn degrades into a recorded one rather
    than a hung socket.

    **What this does NOT do is raise the concurrency ceiling, and the flag's trigger has
    to be read that way.** `/external/media` waits on the event loop (`async def`,
    `asyncio.to_thread`, `asyncio.sleep`); this endpoint is synchronous, so the wait
    happens on the API's threadpool thread and that thread is occupied either way. What
    moves is the LLM call's CPU and memory, off the API process and onto a worker that can
    be scaled and restarted on its own. Making the wait free as well means making
    `/chat/turn` async, which is a change to the endpoint and not to this function; it is
    named in the plan's capacity section rather than done here, because the measured
    trigger for the offload has not arrived either. The connection half of the same
    problem IS fixed: the request no longer holds a database transaction while it waits.
    """
    # Imported HERE, not at module level: the task module imports this engine (it is the
    # thing it runs), so a top-level import is a cycle. `enqueue_job` and `get_job_status`
    # stay at module level because the tests patch them by name on this module.
    from app.tasks.chat_turns import run_turn_job

    job = enqueue_job(
        run_turn_job,
        envelope.model_dump(mode="json"),
        queue_name=CHAT_QUEUE,
        job_timeout=int(getattr(settings, "chatbot_turn_wait_seconds", 60)) * 2,
    )
    deadline = time.monotonic() + float(getattr(settings, "chatbot_turn_wait_seconds", 60))
    while True:
        snapshot = get_job_status(job.id) or {}
        state = snapshot.get("status")
        if state == "finished":
            result = snapshot.get("result")
            if isinstance(result, dict):
                return TurnResult(**result)
            return _worker_failed(
                envelope,
                session_factory,
                "the offloaded turn finished without returning a turn",
            )
        if state in ("failed", "stopped", "canceled"):
            return _worker_failed(
                envelope,
                session_factory,
                f"the offloaded turn {state}: {(snapshot.get('exc_info') or '')[:300]}",
            )
        if time.monotonic() >= deadline:
            # STOP the job before answering. Left running, the worker finishes the turn
            # minutes later and closes the row `done` or `delegated` - a row carrying an
            # answer nobody will ever send, because the caller has already sent the
            # apology, and (when it delegated) a ghost n8n will never complete.
            # `cancel_job` sends the stop command to a started job and cancels a queued
            # one; it swallows its own errors, including the race where the worker
            # finished a millisecond ago.
            cancel_job(job.id)
            return _worker_failed(
                envelope,
                session_factory,
                f"the offloaded turn did not finish within "
                f"{getattr(settings, 'chatbot_turn_wait_seconds', 60)}s",
            )
        time.sleep(WORKER_POLL_INTERVAL_SECONDS)


def _worker_failed(
    envelope: Envelope, session_factory: SessionFactory, message: str
) -> TurnResult:
    """The caller still gets today's error reply when the offloaded turn does not answer.

    The turn id is read back off the row the WORKER inserted, so the operator opening the
    trace screen lands on the turn that actually ran rather than on a job id that means
    nothing there. Empty only when the worker never got as far as inserting.

    **The row is CLOSED here, not left open.** H32's invariant is that no turn sits at
    `processing` with a null error: the caller has already been handed the apology, so a
    row still claiming to be in flight is the dropped turn this whole inbox exists to
    prevent. Closed `failed` at `queued`, the stage the turn genuinely reached from the
    API's point of view.

    Tolerant of the worker winning the race: a row that already has `finished_at` is left
    exactly as the worker wrote it. Its answer is not sent (the caller has the apology),
    but overwriting a finished trace with "the offload timed out" would erase what
    actually happened.
    """
    turn_id = ""
    try:
        with _session(session_factory) as db:
            row = _select_turn(
                db,
                contact_respond_id=_contact_respond_id(envelope),
                message_id=_message_id(envelope),
                # The row THIS envelope wrote, not the other world's row for the same
                # message (H57).
                is_test=envelope.dry_run,
            )
            if row is not None:
                turn_id = str(row.id)
                if row.finished_at is None:
                    _close_turn(
                        db,
                        turn_id,
                        status="failed",
                        stage="queued",
                        branch_kind=None,
                        error=message,
                        records=trace_mod.TurnTrace.resume(row.trace).persisted(),
                        response=row.response if isinstance(row.response, dict) else None,
                    )
    except Exception:  # noqa: BLE001 - the reply matters more than the id
        logger.warning("chatbot offload: could not close the turn row", exc_info=True)
    logger.error("chatbot offload failed: %s", message)
    return _failed_result(turn_id, "queued", message, [], envelope.dry_run)


def _fanout_domain_hint(domains: list[Any], entities: Any) -> Any:
    """Which of a fan-out's domains the resolver gate is asked about (AC-1708, MB-1).

    `gate.run_gate` reads `parser.domain_hint` as the SOLE key into `gate.ALLOWED`, and
    that matrix is per-domain: the `order` row carries `customer`, while `incoming`,
    `inventory`, `promotion` and `spo_allocation` do not. A fan-out verdict names no
    domain of its own, so the hint has to be CHOSEN - and choosing `plan.domains[0]`
    (message order) meant "incoming and orders for hanlim" handed the gate a row with no
    `customer` in it, the ambiguous customer was dropped from the gate's own scan, and
    the turn answered "Here are the results." over two empty sections. The mirrored
    wording, "orders and incoming for hanlim", asked the question correctly.

    So: the first domain in the plan whose row COVERS every hint this turn's entities
    carry, and no injection at all when none does. Refusing to inject restores the
    generic picker (`turn/compose.py`'s own "Which one do you mean?"), which asks a badly
    worded question rather than losing the ask.
    """
    from app.services.chatbot.lanes.business.gate import ALLOWED

    hints = {
        jsc.nullish_str(e.get("hint")).strip().lower()
        for e in (entities or [])
        if isinstance(e, dict) and jsc.nullish_str(e.get("hint")).strip()
    }
    for domain in domains:
        row = ALLOWED.get(jsc.js_string(domain))
        if row is None:
            continue
        if hints <= {jsc.js_string(kind).strip().lower() for kind in row}:
            return domain
    return None


_WEEKDAY_ABBR = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _short_day_time(when: Any) -> str:
    if when is None:
        return ""
    # The dealers' clock, as the episode summaries read (reviewer pass at d89110c0, S14).
    when = episode_digest_mod.local_time(when)
    return f"{_WEEKDAY_ABBR[when.weekday()]} {when.strftime('%H:%M')}"


def _memory_intake(
    db: Session,
    *,
    contact_respond_id: str,
    dry_run: bool,
    turn_id: str | None = None,
    console: bool = False,
) -> dict[str, Any]:
    """Everything `context.assemble` needs beyond what `turn_runtime.load_profile`
    already reads, loaded once at intake and timed as `memory_ms` (contract section
    3/6.6): the effective level, the last 3 closed-frame summaries (level `past`/
    `full`), the live episode's earlier messages (level `conversation`/`past`/
    `full`), and the "About this contact" profile slice (level `full` only - the
    one level the parser sees it at).

    Security review 26 Sep 2026 (B1): `respond_io_id` is unique WITHIN a workspace
    only, never globally, so this resolves the ONE `respond_contacts` row through
    `turn_runtime.resolve_contact_pk` (workspace first, NULL-workspace fallback)
    before touching anything - an ambiguous or absent id degrades memory to `off`
    for this turn (no fact write, no frame read) rather than guessing whose row it
    is. `contact_pk` rides on the returned dict so every later fact write in this
    same turn (tier, tally, `profile_statement`) locks by PRIMARY KEY, never by the
    ambiguous string again. Never raises: a memory failure degrades to `off` with a
    trace note, it does not fail the turn.
    """
    from app.models.chatbot_turn import ChatbotTurn
    from app.models.conversation_frame import ConversationFrame
    from app.models.user import SystemSetting
    from app.services.chatbot import turn_runtime as turn_runtime_mod

    started = time.perf_counter()

    def _degraded(reason: str) -> dict[str, Any]:
        return {
            "effective_level": "off",
            "level_source": "off",
            "own_level": None,
            "contact_pk": None,
            "summaries": [],
            "earlier_messages": [],
            "profile_facts": None,
            "read_frames": [],
            "profile_snapshot": None,
            "ms": int((time.perf_counter() - started) * 1000),
            "degraded_reason": reason,
        }

    try:
        space_id = default_space_id(db)
        resolved = turn_runtime_mod.resolve_contact_pk(db, contact_respond_id, space_id)
    except Exception:  # noqa: BLE001 - a resolution failure degrades, never fails the turn
        logger.warning("chatbot: memory contact resolution failed", exc_info=True)
        return _degraded("resolution_error")
    if resolved is None:
        return _degraded("ambiguous_or_missing_contact")
    contact_pk, own_level = resolved
    # Frames and the tally are keyed by `respond_io_id` alone, so an id another
    # workspace's contact also carries would mix two people's memory. Refused here the
    # same way the staff GET refuses it (reviewer pass at d89110c0, N11).
    from app.models.access import RespondContact

    shared = (
        db.query(RespondContact.id)
        .filter(RespondContact.respond_io_id == contact_respond_id)
        .limit(2)
        .count()
    )
    if shared > 1:
        return _degraded("respond_id_shared")

    try:
        return _memory_intake_resolved(
            db,
            contact_respond_id=contact_respond_id,
            contact_pk=contact_pk,
            own_level=own_level,
            dry_run=dry_run,
            started=started,
            turn_id=turn_id,
            console=console,
        )
    except Exception:  # noqa: BLE001 - a memory read failure degrades, never fails the turn
        logger.warning("chatbot: memory intake failed", exc_info=True)
        return _degraded("intake_error")


def _memory_intake_resolved(
    db: Session,
    *,
    contact_respond_id: str,
    contact_pk: str,
    own_level: str | None,
    dry_run: bool,
    started: float,
    turn_id: str | None = None,
    console: bool = False,
) -> dict[str, Any]:
    from app.models.access import RespondContact
    from app.models.chatbot_turn import ChatbotTurn
    from app.models.conversation_frame import ConversationFrame
    from app.models.user import SystemSetting

    system_memory = db.query(SystemSetting.chatbot_memory).scalar()
    effective = memory_mod.resolve_level(own_level, system_memory)
    # AC-MEM052/056 (round 3): WHERE the level came from - the contact's own valid
    # level, the system default, or off because neither applies - traced on the
    # `context` event so a "why does this contact see nothing" question is
    # answered by the trace alone, without cross-referencing settings.
    level_source = memory_mod.resolve_level_source(own_level, system_memory)

    summaries: list[str] = []
    read_frames: list[dict[str, Any]] = []
    earlier_messages: list[dict[str, Any]] = []
    profile_facts_list: list[dict[str, Any]] | None = None
    newest_frame = None

    if effective in ("episodes", "full"):
        frames = (
            db.query(ConversationFrame)
            .filter(
                ConversationFrame.contact_respond_id == contact_respond_id,
                ConversationFrame.is_test.is_(dry_run),
                ConversationFrame.status == "closed",
            )
            .order_by(ConversationFrame.last_activity_at.desc())
            .limit(3)
            .all()
        )
        summaries = [memory_mod.frame_line(f) for f in frames]
        read_frames = [{"id": f.id} for f in frames]
        newest_frame = frames[0] if frames else None

    if effective in ("conversation", "episodes", "full"):
        if newest_frame is None and effective == "conversation":
            newest_frame = (
                db.query(ConversationFrame)
                .filter(
                    ConversationFrame.contact_respond_id == contact_respond_id,
                    ConversationFrame.is_test.is_(dry_run),
                )
                .order_by(ConversationFrame.last_activity_at.desc())
                .first()
            )
        live_query = db.query(ChatbotTurn).filter(
            ChatbotTurn.contact_respond_id == contact_respond_id,
            ChatbotTurn.is_test.is_(dry_run),
        )
        if turn_id is not None:
            # `_insert_turn` has already committed THIS turn's row; it is the current
            # message, never an earlier one (reviewer pass at d89110c0, S1).
            live_query = live_query.filter(ChatbotTurn.id != turn_id)
        if dry_run and console:
            # The console's world is console turns only, not other dry runs of the
            # same contact (Prompts screen, API) - S15.
            live_query = live_query.filter(ChatbotTurn.ingress == "console")
        if newest_frame is not None:
            live_query = live_query.filter(ChatbotTurn.created_at > newest_frame.last_activity_at)
        live_turns = live_query.order_by(ChatbotTurn.created_at.desc()).limit(3).all()
        for row in reversed(live_turns):  # oldest first, per the L3 header
            text_value = memory_mod._turn_message_text(row)
            if text_value:
                earlier_messages.append({"created_at": _short_day_time(row.created_at), "text": text_value})

    contact_row = db.query(RespondContact).filter(RespondContact.id == contact_pk).first()
    # The `memory` event's profile BEFORE, taken here, before any write this turn makes
    # (reviewer pass at d89110c0, S22).
    profile_snapshot = _profile_snapshot(contact_row)
    if effective == "full":
        if contact_row is not None:
            merged = profile_facts_mod.merged_facts_for_display(db, contact_row)
            crm = profile_facts_mod.crm_view(db, contact_row)
            # Security review 26 Sep 2026 (S2): structured, not the old joined
            # string - `context.py::_render_l5` builds "About this contact:"
            # straight from this, never by re-splitting a string on `;`.
            profile_facts_list = profile_facts_mod.structured_slice(merged, crm)

    return {
        "effective_level": effective,
        "level_source": level_source,
        "own_level": own_level,
        "contact_pk": contact_pk,
        "summaries": summaries,
        "earlier_messages": earlier_messages,
        "profile_facts": profile_facts_list,
        "read_frames": read_frames,
        "profile_snapshot": profile_snapshot,
        "ms": int((time.perf_counter() - started) * 1000),
    }


def _fact_value(facts: list[dict[str, Any]] | None, key: str) -> Any:
    for fact in facts or []:
        if isinstance(fact, dict) and fact.get("key") == key:
            return fact.get("value")
    return None


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _live_customer(db: Session, contact_pk: str | None) -> tuple[str | None, str | None]:
    """The contact's primary customer name and its salesperson, read live off the CRM
    link (never memory, so every level reads it; plan 7.3 examples 4 and 6)."""
    if not contact_pk:
        return None, None
    row = profile_facts_mod.primary_customer(db, contact_pk)
    if row is None:
        return None, None
    customer, agent = row
    return (customer.customer_name or None), profile_facts_mod.salesperson_name(agent)


def _clarifier_memory_slice(memory_intake: dict[str, Any]) -> str:
    """The clarifier's view of memory (plan S4): the profile slice (Full only) and the
    conversation summaries (Past conversations and Full), under
    `fallback.CLARIFIER_MEMORY_TOKENS`, oldest summaries dropped first. Empty at
    Off and at "This conversation"."""
    from app.services.chatbot.lanes import fallback as fallback_mod

    lines: list[str] = []
    facts = memory_intake.get("profile_facts") or []
    if facts:
        lines.append(
            "About this contact: "
            + "; ".join(f"{f.get('key', '').replace('_', ' ')} {f.get('value')}" for f in facts if isinstance(f, dict))
        )
    summaries = list(memory_intake.get("summaries") or [])
    while True:
        block = lines + ([f"Recent conversations: {' | '.join(summaries)}"] if summaries else [])
        text_value = "\n".join(block)
        if context_mod.est_tokens(text_value) <= fallback_mod.CLARIFIER_MEMORY_TOKENS or not (summaries or lines):
            return text_value
        if summaries:
            summaries.pop()  # newest-first, so the oldest goes first
        else:
            lines = []


def _fallback_context(
    db: Session,
    *,
    verdict: dict[str, Any],
    memory_intake: dict[str, Any],
    contact_respond_id: str,
    dry_run: bool,
    turn_id: str,
    policy: Any,
    profile: Any = None,
) -> Any:
    """Everything the graceful fallback reply needs from the database, read while the
    routing session is still open (S4, plan 7.1 and 7.2). Each memory field is filled
    only when the contact's level grants its layer (plan 6.0): the open conversation at
    "This conversation" and above, closed conversations at "Past conversations" and
    above, profile facts (usual products and site, saved language) at Full only. The
    CRM link (customer, the order team) is live data, read at every level."""
    from app.models.access import RespondContact
    from app.services.chatbot import copy as copy_mod
    from app.services.chatbot.lanes import fallback as fallback_mod
    from app.services.chatbot.tail.outcome import pretty_team

    level = memory_intake.get("effective_level") or "off"
    message_type = verdict.get("message_type")
    if message_type == "history_question" and level != "off":
        kind = "history"
    elif message_type == "unknown":
        kind = "unknown"
    else:
        kind = "small_talk"

    history = memory_mod.history_items(
        db,
        contact_respond_id=contact_respond_id,
        is_test=dry_run,
        current_turn_id=turn_id,
        level=level,
    )
    facts = memory_intake.get("profile_facts") if level == "full" else None
    usual_sites = _as_list(_fact_value(facts, "usual_sites"))

    contact_pk = memory_intake.get("contact_pk")
    customer, _salesperson = _live_customer(db, contact_pk)
    first_name = None
    if contact_pk:
        first_name = db.query(RespondContact.first_name).filter(RespondContact.id == contact_pk).scalar()
    order_domain = policy.domain("order") if policy is not None else None
    # ESCALATION-CONTROL: no team to "pass this to" for a barred contact, so the fallback
    # takes its ordinary offer instead of `fallback_offer_customer`.
    team_code = (
        None
        if turn_state.escalation_barred(profile)
        else getattr(order_domain, "escalation_team_code", None)
    )

    statements = verdict.get("profile_statements")
    noted = [
        s
        for s in (statements[:3] if isinstance(statements, list) else [])
        if isinstance(s, dict) and s.get("key") in profile_facts_mod.VOCABULARY
    ]
    saved_language = _fact_value(facts, "language")

    return fallback_mod.FallbackContext(
        kind=kind,
        level=level,
        history=history if kind == "history" else [],
        last_time=history[0] if history else None,
        usual_products=_as_list(_fact_value(facts, "usual_products")),
        usual_site=usual_sites[0] if usual_sites else None,
        customer=customer,
        team=pretty_team(team_code).title() if team_code else None,
        noted=noted,
        saved_language=saved_language if isinstance(saved_language, str) else None,
        first_name=(first_name or "").strip() or None,
        memory_slice=_clarifier_memory_slice(memory_intake) if level in ("episodes", "full") else "",
        copy=copy_mod.resolve(db),
    )


def _carried_line(
    db: Session,
    *,
    verdict: dict[str, Any],
    memory_intake: dict[str, Any],
    contact_respond_id: str,
    dry_run: bool,
) -> str | None:
    """AC-MEM083: a business answer whose subject the parser carried from memory (every
    entity `current_message: false`, none in the live conversation) opens with one line
    naming what was carried - from a closed conversation ("Carrying on from Tue 23 Sep:
    outstanding DO for CC001 Chin Chun Trading."), else from the usual products ("Your
    usual: SRTWB1455, M486-75-BL."). None when nothing came from memory."""
    from app.models.conversation_frame import ConversationFrame
    from app.services.chatbot import copy as copy_mod

    level = memory_intake.get("effective_level") or "off"
    if level not in ("episodes", "full"):
        return None
    entities = [e for e in (verdict.get("entities") or []) if isinstance(e, dict)]
    if not entities or any(e.get("current_message") is not False for e in entities):
        return None
    codes = [str(e.get("canonical_code") or e.get("raw") or "").strip() for e in entities]
    codes = [c for c in codes if c]
    if not codes:
        return None
    earlier = " ".join(str(m.get("text") or "") for m in memory_intake.get("earlier_messages") or []).upper()
    if all(c.upper() in earlier for c in codes):
        return None  # carried from THIS conversation, not from memory

    facts = memory_intake.get("profile_facts") if level == "full" else None
    language = _fact_value(facts, "language")
    canned = copy_mod.resolve(db)

    # "The usual" first: a code set the profile names as usual is carried from there
    # even when an older conversation also named it.
    usual = [p.upper() for p in _as_list(_fact_value(facts, "usual_products"))]
    if usual and all(c.upper() in usual for c in codes):
        return canned.render_in("carried_usual", language, products=", ".join(codes))
    frames = (
        db.query(ConversationFrame)
        .filter(
            ConversationFrame.contact_respond_id == contact_respond_id,
            ConversationFrame.is_test.is_(dry_run),
            ConversationFrame.status == "closed",
        )
        .order_by(ConversationFrame.last_activity_at.desc())
        .limit(3)
        .all()
    )
    for frame in frames:
        # Matched on the frame's own entity codes (fix round 6: the summary is prose
        # now, not a clause chain to split), with the summary text as the fallback.
        named = " ".join(
            str(v) for values in (frame.entities or {}).values() if isinstance(values, list) for v in values
        )
        haystack = f"{named} {frame.summary or ''}".upper()
        if not all(c.upper() in haystack for c in codes):
            continue
        noun = episode_digest_mod.domain_noun(frame.domain)
        listed = episode_digest_mod.join_words([episode_digest_mod.display_code(c) for c in codes])
        subject = f"{noun} for {listed}" if noun else listed
        day = episode_digest_mod.day_label(frame.last_activity_at) if frame.last_activity_at else None
        return canned.render_in("carried_episode", language, day=day or "earlier", subject=subject)

    return None


def _handover_context(
    db: Session,
    *,
    verdict: dict[str, Any],
    memory_intake: dict[str, Any],
    contact_respond_id: str,
    dry_run: bool,
    turn_id: str,
) -> dict[str, Any]:
    """What a handover adds to the escalation lane's own actions (AC-MEM087/088): the
    linked salesperson for a commercial ask (the parser's `intent_hint:
    commercial_request`), and the live conversation's summary line for the person who
    picks it up (at "This conversation" and above)."""
    level = memory_intake.get("effective_level") or "off"
    salesperson = None
    if verdict.get("intent_hint") == "commercial_request":
        _customer, salesperson = _live_customer(db, memory_intake.get("contact_pk"))
    summary = None
    if level != "off":
        summary = memory_mod.open_summary(
            db, contact_respond_id=contact_respond_id, is_test=dry_run, current_turn_id=turn_id
        )
    facts = memory_intake.get("profile_facts") if level == "full" else None
    from app.services.chatbot import copy as copy_mod

    return {
        "salesperson": salesperson,
        "summary": summary,
        "language": _fact_value(facts, "language"),
        "copy": copy_mod.resolve(db) if salesperson else None,
    }


def _profile_snapshot(contact_row: Any) -> dict[str, Any] | None:
    """What the `memory` event shows of the contact's profile: the saved fact count,
    tier and language, off one `respond_contacts` row."""
    if contact_row is None:
        return None
    profile = contact_row.chatbot_profile or {}
    return {
        "facts_count": len(profile.get("facts") or []),
        "tier": profile.get("tier"),
        "language": profile.get("language"),
    }


def _run_stages(envelope: Envelope, *, session_factory: SessionFactory, **kwargs: Any) -> TurnResult:
    """`_run_stages_body` with the office's customer groups held for the whole turn.

    Every place that joins ledgers into one company (`ledger_family_key` / `_label`, the
    gate's `_cust_base`) reads the groups from a ContextVar, so the ONE load happens here, on
    the turn's scoped session, before any stage runs. Read fresh each turn: a rename or a
    regroup shows on the next message (CUSTOMER-GROUP AC-15)."""
    with _session(session_factory) as db:
        groups = family_overrides(db)
    with customer_groups(groups):
        return _run_stages_body(envelope, session_factory=session_factory, **kwargs)


def _run_stages_body(  # noqa: PLR0915
    envelope: Envelope,
    *,
    session_factory: SessionFactory,
    turn_trace: trace_mod.TurnTrace,
    turn_id: str,
    contact_respond_id: str,
    contact_scope: frozenset,
    dry_run: bool,
    actions: list[dict[str, Any]],
    stage: list[str],
    switches: _TurnSwitches,
    media_box: dict[str, Any],
) -> TurnResult:
    """received -> understood -> access -> routed. Wrapped by `run_turn`.

    `switches` is the settings snapshot `run_turn` already read (AC-810): this function
    does not read the singleton itself, because S7 mode is needed before the ticket, which
    is before this runs. `contact_scope` is the SAME frozenset `run_turn` already resolved
    to build the scoped `session_factory` - threaded through rather than re-queried, for
    the roster-plan re-validation below (security SF-1, hand pass 11 final).

    `media_box` is `stage`'s own trick, one more mutable single-key box: this function
    stashes the media-intake outcome onto it (chatbot media-into-turn, S2) so `run_turn`
    can read it back AFTER this returns and prepend the "I read .../I heard ..." prefix
    in the ONE place every answering arm's reply passes through, rather than at each of
    this function's own dozen return sites.
    """
    media_detected = None
    with _session(session_factory) as db:
        # AC-108: today's `set-human-intervened` path. The turn CONTINUES; the caller
        # clears the flag on the contact.
        if _is_human_intervened(envelope):
            actions.append(
                {
                    "kind": "update_contact_fields",
                    "fields": {"is_human_intervened": False},
                    "dry_run": dry_run,
                }
            )

        # Chatbot media-into-turn, S2/S3: an image or voice attachment is intaked
        # INSIDE the turn - decided below, in the no-DB-session window the parser
        # call already uses. A document, a video, or a sticker is not this step's
        # concern (AC-1804): it falls through unchanged, on its caption text.
        # `patched_upstream` is the SEPARATE transition-window signal (AC-1805): when
        # n8n's own pipeline already decided this one, no intake runs here at all.
        patched_modality = media_intake.patched_upstream(envelope)
        media_detected = None if patched_modality else media_intake.detect(_inner_message(envelope))

        session_block = _read_session_vars(
            db,
            respond_io_id=contact_respond_id,
            reply_to_id=_reply_to_message_id(envelope),
        )
        # O2 / AC-112: honoured on a DRY RUN, ignored on a live envelope. The ignored list
        # is recorded below rather than dropped, because a harness envelope that reached a
        # real customer would otherwise answer them from a mock in silence.
        harness_present = _harness_keys_present(envelope)
        harness_ignored: list[str] = [] if dry_run else harness_present
        if dry_run:
            session_block = _inject_harness_session(session_block, envelope)
        latest_user_message = build_latest_user_message(envelope, session_block)
        # -- stage A's three shelves (PLAN "State: three shelves, one writer each") --- #
        policy = load_policy(db)
        # Roster cap (PLAN-chatbot-answer-half-reattach.md, owner ruling 20 Sep 2026):
        # `{entity kind: chatbot_entity_kinds.roster_cap}`, read off the SAME policy
        # object every other per-kind fact (`did_you_mean`, `default_narrowing`) comes
        # from, and handed down to `resolve_kinds` -> `resolve_gate.run` -> `gate.run_gate`
        # - the one place a roster is actually cut.
        roster_caps = {row.kind: row.roster_cap for row in policy.kinds}
        # `chatbot_recall_enabled` is no longer read (contract section 2: the recall
        # re-parse is deleted; the column stays, untouched, per Q1 - no data change).
        profile, _recall_enabled = turn_runtime.load_profile(db, contact_respond_id)
        known_phone = turn_runtime.contact_phone(db, contact_respond_id)
        turn_no = turn_runtime.turn_number(db, contact_respond_id)
        state_in = turn_runtime.load_state(session_block, profile=profile, turn_no=turn_no)
        remembered_before = session_state.five_keys(session_block)
        # The parser's `Previous response:` line. Read here, at `received`, with the
        # other contact facts and off the SAME session: it is what the bot last said to
        # this contact, and the parser is the one component allowed to read prose.
        previous_reply = turn_runtime.previous_reply_text(
            db,
            contact_respond_id=contact_respond_id,
            ingress=envelope.ingress,
            is_test=bool(dry_run),
        )
        # Chatbot memory lane A (contract section 3/6.6): the last 3 closed-frame
        # summaries, the live episode's earlier messages and the profile slice -
        # everything `context.assemble` needs beyond the profile row already read
        # above, off the SAME session, timed as `memory_ms`.
        memory_intake = _memory_intake(
            db,
            contact_respond_id=contact_respond_id,
            dry_run=dry_run,
            turn_id=turn_id,
            console=envelope.ingress == "console",
        )
        # Security review 26 Sep 2026 (B1): the ONE contact this turn resolved to,
        # already workspace-scoped and lock-safe - every later fact write in this
        # turn (tier, tally, `profile_statement`) reads this instead of re-deriving
        # an ambiguous lookup by `respond_io_id`. `None` when intake degraded.
        remembered_before["_contact_pk"] = memory_intake.get("contact_pk")
        remembered_before["_profile_before"] = memory_intake.get("profile_snapshot")
        # PR #1247 round 8: the last three exchanges, so a short reply is read against
        # what was asked. Same rows, same scopes, same session as the line above.
        recent = turn_runtime.recent_exchanges(
            db,
            contact_respond_id=contact_respond_id,
            ingress=envelope.ingress,
            is_test=bool(dry_run),
        )
        # `parser_config` is resolved AFTER media intake, not here: AC-1810's "no
        # parser call" means no parser SETUP either - a media-denied turn (no API
        # key required to check a gate/quota/burst decision) must not fail because
        # nothing configured a provider for a parser this turn will never reach.

    turn_trace.record(
        "received",
        summary="Received the message and loaded what the bot remembered.",
        why="Every turn starts from the contact's stored conversation state.",
        facts={
            "memory_ms": memory_intake["ms"],
            "ingress": envelope.ingress,
            "remembered_keys": len([k for k, v in remembered_before.items() if v]),
            "quoted_a_message": _reply_to_message_id(envelope) is not None,
            "dry_run": dry_run,
            "turn_no": turn_no,
            # ALWAYS present, empty list included: a reader must never have to tell
            # "no harness keys" from "this build does not report them".
            "harness_keys_ignored": harness_ignored,
            # Issue #1262: what this turn answered ahead of itself and what it left to
            # their own deliveries (and which bound left them).
            **media_box.get("send_order", {}),
            # ... or, on a row answered ahead, the message whose response carried it.
            **(
                {"answered_ahead_by_message": media_box["answered_ahead_by"].get("message_id")}
                if media_box.get("answered_ahead_by")
                else {}
            ),
        },
        # The carrying turn's id goes in `raw`, never `facts`: facts print verbatim on
        # the trace screen, and a UUID never reaches the UI.
        raw={
            "session_vars": session_block,
            **(
                {"answered_ahead_by": media_box["answered_ahead_by"].get("turn_id")}
                if media_box.get("answered_ahead_by")
                else {}
            ),
        },
    )

    # -- MEDIA INTAKE (NO DB SESSION IS OPEN HERE, same window as the parser) --- #
    if patched_modality is not None:
        # AC-1805 (review round S3 + security item 4): n8n's own pipeline already
        # decided, metered and recorded this one upstream - no intake runs here.
        turn_trace.record(
            "media_intake",
            status="ok",
            summary="Read the photo." if patched_modality == "image" else "Heard the voice note.",
            why="n8n's own media pipeline already decided this one before /chat/turn ran.",
            facts={"skipped": "patched_upstream", "modality": patched_modality},
            raw=None,
        )
        media_box["patched_modality"] = patched_modality
    elif media_detected is not None:
        modality, attachment = media_detected
        if not jsc.truthy(jsc.get(attachment, "url")):
            # AC-107/H5, restated (captain ruling 23 Sep 2026): an attachment whose
            # own `url` is falsy is unreadable, not a plain-text fallthrough. Never
            # reaches `run()`'s decide/meter/enqueue pipeline - nothing to fetch, so
            # no ledger row, no job.
            outcome = media_intake.no_url_outcome(modality)
        else:
            caption = jsc.js_string(jsc.get(attachment, "description")) or None
            outcome = media_intake.run(
                respond_io_id=contact_respond_id,
                message_id=_message_id(envelope),
                modality=modality,
                attachment=attachment,
                caption=caption,
                turn_id=turn_id,
                session_factory=session_factory,
                # note (a): the console already stored these bytes itself
                # (`console_service._upload_console_media`, under `chatbot-console/`) -
                # `_store_media_bytes` reads this back off the job to skip a second copy.
                source="console" if envelope.ingress == "console" else "chat-turn",
            )
        media_box["outcome"] = outcome
        turn_trace.record(
            "media_intake",
            status="failed" if outcome.stops_here else "ok",
            summary=(
                "Read the photo." if modality == "image" else "Heard the voice note."
            ) if not outcome.stops_here else (
                "Could not read the photo." if modality == "image" else "Could not hear the voice note."
            ),
            why="The customer sent media; this is what the intake pipeline decided and read.",
            facts=_media_intake_facts(outcome),
            error=outcome.reply_text if outcome.stops_here and outcome.turn_status == "failed" else None,
            # Review round S5: `job_id`/`attachment_id`/the full `result` live here,
            # never in `facts` - `TurnPanel`'s generic StageRow prints every `facts`
            # value verbatim (`String(value)`), so a bare id there is a UUID on
            # screen (cursor rule) and the nested `result` object prints as
            # "[object Object]". `console_service`/`chatbot.py`'s own readers merge
            # `facts` and `raw` back together, so nothing downstream of the trace
            # itself lost a field.
            raw={"job_id": outcome.job_id, "attachment_id": outcome.attachment_id, "result": outcome.result},
        )
        if outcome.stops_here:
            close_stage = "sent" if outcome.turn_status == "done" else "media_intake"
            reply_text = outcome.reply_text or ""
            lane_actions = list(actions)
            if reply_text:
                lane_actions = [
                    *actions,
                    {
                        "kind": "send_message",
                        "text": reply_text,
                        "quick_replies": None,
                        "dry_run": dry_run,
                    },
                ]
            # A media-denied turn never reaches `_run_answer`/`_record_memory_trace`
            # (there is no `Answer` object - it closed before APPLY even ran), so those
            # two stage records are written directly here, in the same minimal shape,
            # so the trace still reads received -> media_intake -> replied -> remembered
            # like every other declared branch kind (AC-007, captain ruling 23 Sep 2026).
            turn_trace.record(
                "replied",
                summary=f"Replied: {reply_text}" if reply_text else "Sent no reply; the burst repeat stayed silent.",
                why="The reply is the media intake's own denial text, never the customer's words.",
                facts={"lane": "media_denied", "sections": 0, "asking": None, "files": 0},
                raw={"reply": {"text": reply_text}},
            )
            turn_trace.record(
                "remembered",
                summary=(
                    "Nothing was written: this is a test turn (D14)."
                    if dry_run
                    else "Nothing changed in what the bot remembered."
                ),
                why="A media-denied turn never reached APPLY, so there is no state to write.",
                facts={"written": False, "dry_run": dry_run},
                raw=None,
            )
            with _session(session_factory) as close_db:
                _close_turn(
                    close_db,
                    turn_id,
                    status=outcome.turn_status,
                    stage=close_stage,
                    branch_kind="media_denied",
                    error=None if outcome.turn_status == "done" else reply_text,
                    records=turn_trace.persisted(),
                    response={"actions": lane_actions, "reply": {"text": reply_text}},
                )
            return TurnResult(
                turn_id=turn_id,
                is_test=dry_run,
                branch_kind="media_denied",
                delegate=None,
                reply={"text": reply_text, "quick_replies": None},
                actions=lane_actions,
                status=outcome.turn_status,
                stage=close_stage,
            )
        # AC-1807/AC-1808/AC-1809: the parser reads the intake's OWN rendered text -
        # the caption plus entity raws, or the entity raws alone with no caption
        # (`needs_clarification` no longer nulls it - service.py's own change), or
        # the transcript verbatim - never the url, never the envelope's own text.
        latest_user_message = outcome.rendered_text or ""

    # Resolved HERE, not inside the `received` session block above: a media-denied
    # turn returned before this line and never needed a provider configured for a
    # parser it will not call (AC-1810's "no parser call" reading extended to setup).
    with _session(session_factory) as db:
        parser_config = parser.resolve_config(
            db,
            current_date=_current_date_directive(),
            override_version_id=_prompt_override(envelope, parser.PROMPT_KEY, dry_run=dry_run),
        )
        # #1262 slice 9 (F1a): the live brand list for the `Known brands:` line,
        # read in the SAME session as the config above (already scoped to this
        # contact's companies, `_scoped_factory` at the top of this function) -
        # no cache, so an operator's table edit reaches the very next turn.
        brands = turn_runtime.active_brands(db)

    # -- B PARSER (NO DB SESSION IS OPEN HERE) ------------------------------ #
    # One call, one schema. What comes back IS the verdict - a plain dict, validated once
    # by the parser's own schema and never re-modelled (PLAN "APPLY contract"). The
    # post-processor that used to sit here, and the thirteen places inside it that
    # overrode the parser's answer, are gone: APPLY is the one place a verdict becomes a
    # decision now.
    stage[0] = "understood"
    pending_options = _pending_option_labels(state_in.pending)
    # PR #1247 rounds 8 and 9: the ONE question on the table, as a structured object the
    # parser answers in `open_question_answer` - the open pick or offer when there is
    # one (it is what the message answers), else the stock question (issue #1293).
    open_question = turn_question.open_question(state_in.pending, state_in.focus.tasks)
    effective_level = memory_intake["effective_level"]
    subject_full = parser.current_subject_line(state_in.focus)
    subject_prefix = "Current subject: "
    current_subject = (
        subject_full[len(subject_prefix) :] if subject_full and subject_full.startswith(subject_prefix) else subject_full
    )
    context_layers = context_mod.ContextLayers(
        level=effective_level,
        profile_facts=memory_intake["profile_facts"],
        summaries=memory_intake["summaries"],
        earlier_messages=memory_intake["earlier_messages"],
        previous_response=previous_reply,
        current_subject=current_subject,
        pending_kind=state_in.pending.kind if state_in.pending is not None else None,
        pending_options=pending_options,
        settings_profile_line=memory_mod.profile_block(state_in.profile),
        current_message=latest_user_message,
        reply_to=None,
        media_line=None,
        task_lines=parser.open_task_lines(state_in.focus, open_question),
        open_question_line=parser.open_question_line(open_question),
        brands=brands,
        recent_exchanges=recent,
    )
    user_block, context_report = context_mod.assemble(context_layers)
    # AC-MEM052/056 (round 3): where the level itself came from, alongside what it
    # rendered - added here rather than inside `context.assemble` since that
    # module renders layers, it does not resolve levels.
    context_report["level_source"] = memory_intake["level_source"]
    # G6: a dry run may supply the emission instead of paying for it.
    parser_bypassed = dry_run and "mock_reformulator_output" in harness_present
    parse_started = time.perf_counter()
    try:
        if parser_bypassed:
            parser_raw = _harness_value(envelope, "mock_reformulator_output")
        else:
            parser_raw = parser.parse(parser_config, user_block)
        # Empty on a bypassed parse: no call, no spend to record.
        parser_usage = getattr(parser_raw, "usage", {}) or {}
        if not isinstance(parser_raw, dict) or not parser_raw:
            raise parser.ParserError("parser returned no usable emission")
        if parser_bypassed:
            # G6's own half of R5 / H44. A PROVIDER's answer was held to the declared
            # keys inside `parser.parse`; the harness value never went near it, so
            # `{"nope": true}` (the 5 Sep 2026 production case) routed a whole turn off
            # tolerant defaults and finished `done`. Same rule, same wording, named here
            # because this is the seam that skipped it.
            parser.assert_emission(parser_raw)
        verdict: dict[str, Any] = dict(parser_raw)
        # CHATBOT-EMPTY-ROUTE-PICK: a declared answer to a question that was never
        # asked (no `Open question:` line went to the parser) is dropped before any
        # reader, and the `understood` record below shows the drop as `derived`.
        verdict, phantom_answer = turn_question.without_phantom_answer(
            verdict,
            open_question=open_question,
            positions_read_elsewhere=_positions_read_elsewhere(state_in),
            focus_has_product=bool(_focus_products(state_in)),
        )
    except parser.ParserError as exc:
        # R5 / H44: no soft default and no default routing. A failed understanding is a
        # failed turn with today's error reply.
        message = str(exc)
        turn_trace.record(
            "understood",
            status="failed",
            summary="Could not understand the message.",
            why="The parser did not return a usable answer, so the turn was not routed.",
            facts={"prompt_version": parser_config.prompt_version, "model": parser_config.model},
            error=message,
            raw={"user_block": user_block},
        )
        with _session(session_factory) as db:
            # The provider bills a truncated or non-JSON emission too, so a failed parse
            # is a spend the usage table has to carry.
            _record_parser_usage(
                db,
                usage=getattr(exc, "usage", {}) or {},
                started=parse_started,
                contact_respond_id=contact_respond_id,
                dry_run=dry_run,
                answered=False,
                turn_id=turn_id,
            )
            _close_turn(
                db,
                turn_id,
                status="failed",
                stage="understood",
                branch_kind=None,
                error=message,
                records=turn_trace.persisted(),
            )
        return _failed_result(
            turn_id,
            "understood",
            message,
            actions,
            dry_run,
            reply_text=(
                llm_call.RATE_LIMITED_REPLY
                if getattr(exc, "rate_limited", False)
                else GENERIC_ERROR_REPLY
            ),
        )

    # Chatbot memory lane A (contract section 3, Q5 ruling): the recall re-parse
    # is gone - one parser call per turn, always. The frames `context.assemble`
    # already folded into THIS call's user block (L4, level `past`/`full`) are
    # what `episodes.read` on the `memory` trace event reports below.
    recalled: list[dict[str, Any]] = memory_intake["read_frames"]

    turn_trace.record(
        "understood",
        summary=(
            "Parser bypassed by harness."
            if parser_bypassed
            else trace_mod.understood_summary(verdict)
        ),
        why=(
            "A test envelope supplied the parser's answer, so no model was asked; "
            "everything after this point ran normally."
            if parser_bypassed
            else "The parser is the only step that reads the customer's words; everything after it works on structured state."
        ),
        facts={
            "message_type": verdict.get("message_type"),
            "domain": verdict.get("domain_hint"),
            "intent": verdict.get("intent_hint"),
            "entities": len(verdict.get("entities") or []),
            "prompt_version": parser_config.prompt_version,
            "tokens": int(parser_usage.get("total_tokens") or 0),
            # Chatbot memory lane A (contract section 6): the provider's own split,
            # alongside the total the trace already carried.
            "prompt_tokens": int(parser_usage.get("prompt_tokens") or 0),
            "completion_tokens": int(parser_usage.get("completion_tokens") or 0),
            "parser_bypassed": parser_bypassed,
            "recalled_frames": len(recalled),
            # D17: WHICH options the parser was shown, on the record.
            "open_question_options": pending_options,
        },
        raw={"parser_raw": parser_raw, "derived": verdict},
    )
    turn_trace.add("prompt_text", {"text": user_block})
    if phantom_answer:
        raw_positions = parser_raw.get("reference_positions")
        positions_kept = bool(raw_positions) and verdict.get("reference_positions") == raw_positions
        turn_trace.add(
            "phantom_answer",
            {
                "open_question_answer": parser_raw.get("open_question_answer"),
                "reference_positions": parser_raw.get("reference_positions"),
                "why": (
                    "No question was open, so the parser's declared answer was dropped; "
                    "the positions stay because they still have a reader (a question "
                    "answered by a position elsewhere, or the product in focus)."
                    if positions_kept
                    else "No question was open, so the parser's declared answer and positions were dropped."
                ),
            },
        )
    # Chatbot memory lane A (contract section 6): once per successful parse, never
    # on a failed one - `TurnTrace.persisted()` places every `.add()` event AFTER
    # every `.record()` stage, so an event added before a parse failure would
    # itself become the trace's last entry instead of the failed `understood`
    # stage (AC-105/R5/H44's own "no routing" shape).
    turn_trace.add("context", context_report)

    # The routing default lands ONCE, here, after the last parse and before the access
    # read (finding 2b): every reader downstream - access, the lanes, the trace - sees
    # the same `suggested_agent`. SRTSC07 (prod transcript, 22 Sep 2026): `pending=` lets
    # a null parser agent on the ACCEPTANCE turn carry the offer's own agent forward, the
    # same way `lane_parse_output` already carries the team - so the access check just
    # below is made against the CARRIED agent, not the default. No `session=` (reviewer
    # round 1, SHOULD-4): no writer ever produces a prior-turn agent nest to read.
    verdict = turn_runtime.with_routing_agent_default(verdict, pending=state_in.pending)
    # #865 round 5 (R1): an escalate word plus a named team, in the parser's own reading
    # of the message, is a help request whatever product words follow.
    verdict = turn_runtime.with_named_team_escalation(verdict)
    # #865 round 6 (R3): a reply naming one of the companies an open escalation offer
    # showed ("mocha", "srt", "yes please escalate to sorento team") picks it - n8n's
    # deterministic company-pick tier, with the parser's own pick as the validated fallback.
    verdict = turn_runtime.with_company_pick(
        verdict, pending=state_in.pending, message=latest_user_message
    )
    # Reviewer S1 on PR #833: the answer to "how many should I show?" must not rest on
    # the parser filling `top_n` for a bare "10" - its prompt has no example of one, and
    # its positional rule pulls a bare number toward `reference_positions`.
    # Round 4 R5 on PR #833: the answer to an open clarify re-runs the ask it was about.
    verdict = turn_runtime.with_clarify_answer(
        verdict, latest_user_message, carried=state_in.focus.set_clarify
    )
    verdict = _with_account_answer(verdict, state_in.focus.set_clarify)
    verdict = turn_runtime.with_set_count_from_text(
        verdict, latest_user_message, carried=state_in.focus.set_page
    )
    # Round 7 on PR #833 (owner hand test, item 6): a brand the last set answer offered
    # narrows that set.
    verdict = turn_runtime.with_brand_from_offer(
        verdict, latest_user_message, carried=state_in.focus.set_page
    )
    # Round 3 W3 on PR #833: a class word of this message's own starts a new set.
    verdict = turn_runtime.with_new_set_words(verdict)
    # Fix round 8 on PR #833 (owner retest of round 7): every descriptor the parser
    # emitted is grounded against the specification registry, after the clarify answer
    # is substituted (so "p trap" answering "t trap" is read as the choice it is) and
    # before APPLY reads the verdict: the category keeps what the thing IS, a colour or
    # a size becomes a `specification` entity, and nothing is a document type unless it
    # is on the list.
    with _session(session_factory) as grounding_db:
        verdict, grounding_notes = grounding.ground(
            grounding_db, verdict, message=latest_user_message.split("\n", 1)[0] if isinstance(latest_user_message, str) else None
        )
    if grounding_notes:
        turn_trace.add("grounding", {"changes": grounding_notes})
    # Fix round 12 on PR #833 (owner ruling 28 Sep 2026): once grounding has read the ask
    # as a described product set, the set's leg (stock, incoming, promotion, a document)
    # is the turn's domain, whatever verb carried it; APPLY plans from that.
    from app.services.chatbot.lanes.business.predicate import with_set_leg

    leg_verdict = with_set_leg(verdict, message_text=latest_user_message if isinstance(latest_user_message, str) else None)
    if leg_verdict is not verdict:
        turn_trace.add(
            "set_leg",
            {"from": [verdict.get("domain_hint"), verdict.get("intent_hint")], "to": [leg_verdict.get("domain_hint"), leg_verdict.get("intent_hint")]},
        )
        verdict = leg_verdict

    # -- access, C APPLY, D ROUTE ------------------------------------------- #
    stage[0] = "access"
    hard_failure: TurnResult | None = None
    with _session(session_factory) as db:
        _record_parser_usage(
            db,
            usage=parser_usage,
            started=parse_started,
            contact_respond_id=contact_respond_id,
            dry_run=dry_run,
            answered=True,
            turn_id=turn_id,
        )
        suggested_agent = jsc.get(verdict.get("routing"), "suggested_agent")
        access = check_access(
            db,
            agent_code=suggested_agent,
            contact_id=contact_respond_id,
            space_id=default_space_id(db),
        )
        turn_trace.record(
            "access",
            summary=(
                f"Access allowed for {access.get('agent_name') or suggested_agent}."
                if access.get("allowed")
                else f"Access refused: {access.get('decision')}."
            ),
            why="The contact must be granted the agent this turn would use before anything is looked up.",
            facts={
                "agent": suggested_agent,
                "allowed": bool(access.get("allowed")),
                "decision": access.get("decision"),
            },
            raw=access,
        )

        stage[0] = "routed"
        settings_row = switches
        stock_denial_enabled = _stock_denial_enabled(db, settings_row)
        enabled_lanes = _enabled_lanes(db, settings_row)
        s7_mode = _s7_mode(db, settings_row)
        space_id_for_turn = business_services.fetch_space_id(db)

        # LOWSTOCK-FILTER-ASK: an ask that left a required field open (the slot is one
        # turn long, consumed here) reads this message as the answer unless it is plainly
        # another ask; a fresh low stock ask takes its category / brand words off the
        # entity list for the lane. Read before every other seam below.
        from app.services.chatbot import required_fields
        from app.services.chatbot.lanes.business import low_stock_ask

        _message_text = jsc.js_string(jsc.get(_inner_message(envelope), "text") or "")
        # Security S1: these keys are the ENGINE's own; one the parser emitted (the
        # Anthropic path does not enforce the schema's additionalProperties) is dropped.
        verdict = {k: v for k, v in verdict.items() if k not in required_fields.ENGINE_KEYS}
        open_ask = state_in.focus.required_ask
        state_in.focus.required_ask = None
        verdict, required_rule = required_fields.reply_verdict(verdict, open_ask, _message_text)
        if required_rule:
            turn_trace.add("required_ask", {"verdict_rule": required_rule, "ask": (open_ask or {}).get("ask")})
        if required_rule == "required_ask_answer":
            state_in = dataclasses_replace(state_in, pending=None)
        verdict = low_stock_ask.take_words(verdict, _message_text)

        # Owner retest of top selling round 4 (27 Sep 2026): inside a ranking, the message
        # is read against the question the bot asked before anything routes it.
        # R2 (round 7): read before the verdict below can change the conversation.
        in_ranking_conversation = _in_ranking_conversation(state_in.focus)
        verdict, state_in, top_selling_rule = _top_selling_verdict(
            db, verdict, state_in, jsc.js_string(jsc.get(_inner_message(envelope), "text") or "")
        )
        if in_ranking_conversation and _asks_for_a_person(verdict):
            in_ranking_conversation = False
        if top_selling_rule:
            turn_trace.add("top_selling", {"verdict_rule": top_selling_rule})
        if state_in.focus.status == "top_selling" or state_in.focus.top_selling or (
            jsc.js_string(verdict.get("order_status") or "").strip() == "top_selling"
        ):
            # R8: inside a ranking no picker lists more than five options.
            roster_caps = {
                kind: min(cap, turn_compose.MAX_MENU_OPTIONS)
                if isinstance(cap, int) and cap > 0
                else turn_compose.MAX_MENU_OPTIONS
                for kind, cap in roster_caps.items()
            }

        # #1262 fix lane round 7 (R3 to R5): inside an order list the message is read
        # against the list before the parser's reading can send it anywhere else.
        from app.services.chatbot import order_list as order_list_mod

        order_list_was_open = order_list_mod.is_open_order_list(state_in.focus)
        verdict, state_in, order_list_rule = order_list_mod.order_list_verdict(
            db, verdict, state_in, jsc.js_string(jsc.get(_inner_message(envelope), "text") or "")
        )
        if order_list_rule:
            turn_trace.add("order_list", {"verdict_rule": order_list_rule})
        # SO-NUMBER-ASK: "all my sales orders" is the SO list whatever the parser read (the
        # tester measured "okay how about all my sales order?" as an SO answer in 1 of 3
        # runs): the words decide the document, as they decide the order list's brand above.
        from app.services.chatbot import so_status as so_status_mod

        # Cloud pass on #1435 (live parser): the SO numbers the message typed are its SO
        # entities, never one copied off the previous card.
        verdict, typed_so_rule = so_status_mod.typed_so_numbers_verdict(
            verdict, jsc.js_string(jsc.get(_inner_message(envelope), "text") or "")
        )
        if typed_so_rule:
            turn_trace.add("so_status", {"verdict_rule": typed_so_rule})
        # Same pass: the report status and document the parser carried onto "1" under the
        # outstanding offer, or onto a period answering the SO list, are not the message's.
        if not (in_ranking_conversation or top_selling_rule or state_in.focus.status == "top_selling"):
            verdict, carried_rule = so_status_mod.carried_status_verdict(
                verdict,
                jsc.js_string(jsc.get(_inner_message(envelope), "text") or ""),
                focus_document=state_in.focus.document,
                focus_status=state_in.focus.status,
                pending_kind=getattr(state_in.pending, "kind", None),
            )
            if carried_rule:
                turn_trace.add("so_list", {"verdict_rule": carried_rule})
        # Never inside a top selling ranking: there "by sales order" / "SO basis" switch the
        # ranking's basis (`_top_selling_verdict` above, round 7 R3), and the list must not
        # take the ranking's question or its status away.
        so_list_rule = None
        if not (in_ranking_conversation or top_selling_rule or state_in.focus.status == "top_selling"):
            verdict, so_list_rule = so_status_mod.so_list_verdict(
                verdict, jsc.js_string(jsc.get(_inner_message(envelope), "text") or "")
            )
        if so_list_rule:
            turn_trace.add("so_list", {"verdict_rule": so_list_rule})
            # Owner hand test, 3 Oct 2026: over an open outstanding summary, "find all my
            # sales order" was read as re-scoping THAT report (its detail offer and the
            # carried `focus.status` both survived) and printed it again. An SO list ask
            # starts its own question: the outstanding offer and status go.
            import dataclasses as _dc

            open_kind = getattr(state_in.pending, "kind", None)
            state_in = _dc.replace(
                state_in,
                focus=_dc.replace(state_in.focus, status=None),
                pending=None if open_kind in ("outstanding_detail", "outstanding_scope") else state_in.pending,
            )
        if not (in_ranking_conversation or top_selling_rule or state_in.focus.status == "top_selling"):
            # The month and the "outstanding" an SO ask types, when the parser dropped them.
            from app.services.chatbot import do_ask as _do_ask

            verdict, typed_rule = so_status_mod.typed_words_verdict(
                verdict, jsc.js_string(jsc.get(_inner_message(envelope), "text") or ""), _do_ask.today_myt()
            )
            if typed_rule:
                turn_trace.add("so_list", {"verdict_rule": typed_rule})

        # PR #1353 fix round 3: a bare position over an open roster is read by the
        # engine, and its positions win over the parser's (turn 3f56a40d: "2" read as 1).
        # Not while a stock quantity asked AFTER the roster is the current question:
        # there the bare number is that quantity (`apply._stock_task_is_the_current_question`).
        from app.services.chatbot.turn.apply import (
            _stock_task_is_the_current_question,
            _stock_task_owed_a_number,
        )

        if getattr(getattr(state_in, "profile", None), "stock_availability_only", False):
            typed = jsc.js_string(jsc.get(_inner_message(envelope), "text") or "")
            stock_ask = _as_a_stock_ask_by_its_own_words(verdict, typed)
            if stock_ask is not verdict:
                turn_trace.add("stock_ask_by_its_own_words", {"parser_intent": verdict.get("intent_hint")})
                verdict = stock_ask
            as_codes = _unsure_capture_as_codes(verdict)
            if as_codes is not verdict:
                turn_trace.add("unsure_capture_as_codes", {"entities": [e.get("raw") for e in as_codes["entities"] if isinstance(e, dict)]})
                verdict = as_codes
            summed = _with_repeated_codes_summed(
                verdict, jsc.js_string(jsc.get(_inner_message(envelope), "text") or "")
            )
            if summed is not verdict:
                turn_trace.add("repeated_code_summed", {"entities": summed.get("entities")})
                verdict = summed

        owed_task = _stock_task_owed_a_number(state_in.focus)
        if owed_task is None or not _stock_task_is_the_current_question(state_in, owed_task, verdict):
            engine_verdict = _with_the_engine_pick(
                verdict, state_in.pending, jsc.js_string(jsc.get(_inner_message(envelope), "text") or "")
            )
            if engine_verdict is not verdict:
                turn_trace.add(
                    "engine_pick",
                    {
                        "roster": state_in.pending.kind,
                        "positions": engine_verdict.get("reference_positions"),
                        "parser_positions": verdict.get("reference_positions"),
                    },
                )
                verdict = engine_verdict

        # PR #1353 fix round 2: a bare pick answered in the roster's domain takes the
        # roster's axis from the option picked, never from a value the parser carried.
        roster_picks = _picks_in_the_roster_domain(state_in.pending, verdict)
        if roster_picks:
            picked_verdict = _with_the_picked_axis(verdict, state_in.pending, roster_picks)
            if picked_verdict is not verdict:
                turn_trace.add(
                    "picked_axis",
                    {
                        "roster": state_in.pending.kind,
                        "positions": roster_picks,
                        "access_levels": [verdict.get("access_levels"), picked_verdict.get("access_levels")],
                        "entities_dropped": len(verdict.get("entities") or []) - len(picked_verdict.get("entities") or []),
                    },
                )
                verdict = picked_verdict

        # C APPLY, first pass: state and plan from the verdict alone.
        state_out, plan = turn_apply(state_in, verdict, policy)

        # The resolver seam, and the ONE re-entry of APPLY it feeds (PLAN "Turn order":
        # "Reconciliation lives in E because it needs the resolver, but its RULE is
        # declared in C's policy and its outcome is written back into state' before F
        # runs"). Asked only when the turn named something to resolve.
        parsed_output = turn_runtime.lane_parse_output(
            verdict,
            focus=state_out.focus,
            pending=state_in.pending,
            # An accepted escalation offer routes by the team the customer just
            # picked (contract 108); a multi-team offer's own `pending.team` is
            # null until one of its options is chosen.
            accepted_team=plan.trace.team,
            accepted_assignee=plan.trace.assignee,
            # The company a numbered pick over the company clarify named - written onto
            # `escalation.company_pick`, the one key `escalation_context` validates a
            # named company through (hand pass 11, blocker 2).
            accepted_company=plan.trace.company,
            declined_offer_copy=plan.trace.lane == "offer_declined",
            prior_session=session_block,
            # R6 (22 Sep 2026): a null `routing.suggested_team` falls back to the
            # QUESTION's own domain team before the flat "customer_service" literal.
            policy=policy,
        )
        # Security N-3/S2 (hand pass 11 security review): an accepted offer whose options
        # carry a COMPANY needs that company - and above all its `company_id` - to reach
        # `escalation_context`, whose two company arms both read this one legacy field.
        # See `turn_runtime.escalation_roster_plan`'s own docstring.
        # A copy of `session_block` for `ctx` only; `lane_parse_output` above already
        # read the UNPATCHED one, so this cannot change what this turn's own team
        # resolved to.
        ctx_session = session_block
        roster_plan = turn_runtime.escalation_roster_plan(
            state_in.pending,
            accepted_lane=plan.trace.lane,
            accepted_rules=plan.trace.rules_fired,
        )
        if plan.trace.lane == "offer_hold":
            # #865 round 6 (R5): the held offer's own pool is what the re-asked clarify
            # names (`lanes/canned.fragments_for` reads it off the same field).
            roster_plan = [
                {"plan_idx": i, **row} for i, row in enumerate(turn_pending.offered_companies(state_in.pending))
            ] or None
        # Security SF-1 (hand pass 11 final): the plan is minted from a PERSISTED offer,
        # which can be turns old, so a row's company must still be in the contact's
        # CURRENT scope before it drives routing - a revoked membership must not keep
        # routing to the company it lost. Filtered here, at the one injection point, so
        # the printed clarify pool and the routed id can never disagree. An emptied plan
        # degrades to `None`, which is the contact-derived company in
        # `resolve_routing_company` - the pre-feature behaviour.
        if roster_plan:
            roster_plan = [
                row
                for row in roster_plan
                if row.get("company_id") and str(row["company_id"]) in contact_scope
            ] or None
        if roster_plan:
            session_vars = session_block.get("session_vars") if isinstance(session_block, dict) else None
            prior_variables = (session_vars or {}).get("variables") or {}
            ctx_session = {
                **session_block,
                "session_vars": {
                    **(session_vars if isinstance(session_vars, dict) else {}),
                    "variables": {
                        **prior_variables,
                        "routing_roster_plan": roster_plan,
                        # #865 round 6 (R4): whether the offer showed a member picker,
                        # the key n8n's clarify copy branches on ("reply a number, a name,
                        # or the company" only when numbers were shown), and the picker
                        # itself, which a clarify keeps open (n8n re-persists the offer) so
                        # a member number still picks after it.
                        **(
                            {
                                "selection_context": "member_offer",
                                "held_offer": turn_pending.to_wire(state_in.pending),
                            }
                            if state_in.pending is not None and state_in.pending.kind == "member_offer"
                            else {}
                        ),
                        # MERGE, never replace (reviewer N-c): `variables.routing` is a
                        # whole routing block on a session n8n wrote, and this needs
                        # exactly one key of it - the team, so `escalation_context`'s own
                        # `same_team` equality check holds for the offer we just accepted.
                        "routing": {
                            **(
                                prior_variables.get("routing")
                                if isinstance(prior_variables.get("routing"), dict)
                                else {}
                            ),
                            "suggested_team": plan.trace.team,
                        },
                    },
                },
            }
        ctx = build_ctx(
            contact=_contact_block(envelope, known_phone),
            text=_tf_message(envelope),
            session=ctx_session,
            parse={"output": parsed_output, "_parser_raw": verdict},
            access=access,
            media=getattr(envelope, "media", None),
        )[0]["json"]["ctx"]
        if in_ranking_conversation:
            # Read by the tail: no escalate offer and no routing picker (round 7).
            ctx["top_selling_no_offer"] = True
        # PLAN-chatbot-customer-scope-29sep.md D3: the contact's customer scope, read once
        # per turn; the gate below and (through `make_tool_runner`) the lane read it.
        # Lazily: only a turn on a domain a customer scope matters to (the order domain
        # owns every customer-scoped tool; purchase_order is screened for customer words)
        # or one saying "my" pays for the lookup.
        customer_scope = (
            business_services.customer_scope(db, contact_respond_id, space_id_for_turn)
            if verdict.get("self_reference") is True
            or {"order", "purchase_order"} & {*(plan.domains or ()), *(spec.domain for spec in plan.fetch)}
            else None
        )

        # Grant before roster (SF-1, PLAN-chatbot-answer-half-reattach.md slice R2):
        # an ungranted contact's sales-report ask is refused HERE, before the resolver
        # ever runs. Read off `parsed_output` (the PROJECTED `order_status` -
        # `lane_parse_output` derives it from `focus.status` when this turn's own
        # verdict names none, exactly the carried-offer/carried-focus shape SF-2's
        # own tests pin), never the raw verdict alone: a position pick answering a
        # `sales_report_detail` offer, or a plain message under a carried
        # `focus.status == "sales_report"`, names no `order_status` of its own, and
        # checking the raw verdict here let the R4 bridge's own pre-fetch miss arm
        # (a resolver `not_found` exit needs no fetch to answer) compose a reply
        # before `lanes.business.run_fetch`'s own second-line check ever ran -
        # measured, `test_sales_report_grant_security.py::TestSF2...` (SF-2(i)/(ii)).
        # Mirrors main's own R-S3 check (`lanes.business.run_until_exit`'s bypass) for
        # the same reason it exists there: the ambiguous-customer picker is an
        # interactive, multi-choice prompt naming real customer matches, and showing
        # it before refusing leaks that enumeration for nothing. `lanes.business.
        # run_fetch`'s own `_SALES_REPORT_GRANT` check stays as the second line of
        # defence, for a re-entry path that calls it directly.
        from app.services.chatbot.lanes.business import _SALES_REPORT_GRANT

        from app.services.chatbot.contracts import SALES_FIGURE_STATUSES

        # PLAN-chatbot-top-x-hot-selling-24sep.md S4 point 5: the top selling ranking is
        # the same money under the same key, refused at the same seam.
        sales_report_grant_refused = (
            jsc.js_string(parsed_output.get("order_status") or "").strip() in SALES_FIGURE_STATUSES
            and _SALES_REPORT_GRANT not in set(access.get("attributes") or [])
        )
        if sales_report_grant_refused:
            # Same event shape `lanes.business.run_fetch`'s own sales-report grant
            # check emits (`__init__.py:1216`), so an operator reading the trace sees
            # one denial shape regardless of which seam refused it.
            turn_trace.add(
                "domain_grant",
                {
                    "domain": verdict.get("domain_hint"),
                    "skipped": "not_granted",
                    "needs": _SALES_REPORT_GRANT,
                },
            )

        top_selling_updates: dict[str, Any] = {}
        customer_scope_refused = False
        account_question: str | None = None
        resolved_kinds: dict[str, dict[str, int]] = {}
        compatible_entities: list[dict[str, Any]] = []
        predicate: dict[str, Any] | None = None
        resolved_candidates: dict[str, list[dict[str, Any]]] = {}
        unplaced_tokens: dict[str, str] = {}
        spec_tier = False
        # R2 (PLAN-chatbot-answer-half-reattach.md): the raw `resolve_gate.run` payload
        # (`resolved`, `gate`, `aggregate`, `tier_gate`, `_exit_kind`), carried through
        # `ResolveOutcome.payload` - `None` until the resolver actually runs. Nothing
        # consumes it yet beyond handing the real gate to `make_tool_runner` below.
        resolver_payload: dict[str, Any] | None = None
        # AC-1701/AC-1702: the parse output the ANSWER composers read. `complete_answer`
        # reads the SAME `ctx["parse"]["output"]` the resolver ran on, so the bridge does
        # too - see `turn_runtime.answer_parse_output`. The bare verdict is the fallback
        # for a turn that never reaches the resolver at all.
        answer_parse_output: dict[str, Any] = (ctx.get("parse") or {}).get("output") or {}
        # SF-1: a refused sales-report ask never reaches the resolver at all - no
        # `resolve_gate.run` call, no roster built from what it would have found.
        # W4 (owner hand test round 2): a page of a carried set is answered off the carry
        # alone (`turn_runtime.page_the_set`). Re-resolving the carried class word without
        # the set's predicate placed nothing ("wash basin" is no product code), and that
        # resolver's own not-found exit turned the page into "Couldn't find: wash basin".
        set_page_turn = bool(plan.fetch) and isinstance(plan.fetch[0].filters.get("set_page"), dict)
        if not sales_report_grant_refused and not set_page_turn and (plan.fetch or plan.ask is not None):
            # The REAL branch this plan belongs to, the SAME function "D ROUTE" below
            # calls on the (possibly reconciled) plan - not a literal "business_query"
            # for every turn, so a promotion ask reaches `resolve_gate.run` at
            # "access_check" instead of the generic "resolve" entry
            # (`lanes.business.ENTRY_BY_BRANCH_KIND`). One rule, one place: called twice
            # on two plan snapshots, never duplicated.
            provisional_branch_kind = turn_route(plan)
            # The RESOLVER's own ctx: a roster has to list things that exist, with the
            # stamps the picker probe measures ("SRTWC286-SH-NEW-P - has incoming"), and
            # a turn that named no product of its own ("incoming", after a stock answer
            # about ten variants) gave the resolver nothing to look up. The carried
            # subject is handed over for that question.
            #
            # A FETCH turn hands over the UNSETTLED carry only. A settled carry is
            # already in the plan and re-resolving one is how a stale subject gets back
            # into an answer; a carry that is still only a TOKEN is in the plan as a word
            # no `*_ids` param can be built from, so the fetch runs about nothing. That
            # is browser pass 6's promo defect exactly (turn 0bd47e62): the tier pick
            # settled the tier, and the ruling "a pick settles only its kind" leaves the
            # product on the fetch - but it was never resolved, so the promotion tool was
            # called with no product at all.
            # AC-1708 (reviewer S7): and the domain the plan is actually asking about.
            # `gate.py:357` reads `parser.domain_hint` and nothing else - it is the key
            # into `ALLOWED`, so a verdict that names none leaves `gate_debug` as
            # `{"domain": null}` with no `allowed_lookup`, the gate raises no picker at
            # all (`gate_passed: True`, `gate_clarification: ""`), and the ambiguous
            # customer falls through to `turn/compose.py`'s generic "Which one do you
            # mean?". The pre-rearch head had no such turn - one verdict named one
            # domain - so `domain_hint` was never absent there; contract 122's fan-out
            # (`asks: [{"domain": "order"}, {"domain": "incoming"}]`) is what introduced
            # it. WHICH domain is `_fanout_domain_hint`'s own rule (reviewer MB-1): the
            # first one whose `ALLOWED` row takes the kinds this turn's entities carry,
            # and no hint at all when none does - message order alone decided it before,
            # so "incoming and orders for hanlim" handed the gate a row with no
            # `customer` and lost the ambiguous customer entirely.
            resolver_parse_output = turn_runtime.with_carried_entities(
                (ctx.get("parse") or {}).get("output") or {},
                state_out.focus,
                unsettled_only=plan.ask is None,
            )
            resolver_parse_output = _without_carried_domain_on_a_roster_pick(
                resolver_parse_output, plan.trace.rules_fired
            )
            resolver_parse_output = _without_the_drill_pick_words(
                resolver_parse_output, state_in.pending, plan.trace
            )
            # PLAN-chatbot-top-x-hot-selling-24sep.md S4 point 4 (AC-1954): under `order`
            # the generic resolver re-types a category token as a customer
            # (`entity_resolver._DOMAIN_HINT_EXPANSIONS["order"]["category"]`), which
            # would filter the ranking by a customer nobody named or open a customer
            # picker. A top selling ask resolves its category words itself, against
            # `product_categories` (`lanes/business._top_selling_category_ids`), so the
            # resolver is never asked about them.
            # Owner retest (27 Sep 2026): read off the FOCUS, not the verdict, so a
            # message that only narrows the ranking ("sold by fanny", no ask word of
            # its own) is kept away from the resolver too.
            if state_out.focus.status == "top_selling":
                dealer = (
                    business_services.top_selling_dealer_ledgers(db, contact_respond_id, space_id_for_turn)
                    is not None
                )
                resolver_parse_output, top_selling_updates = _top_selling_narrowing(
                    db, resolver_parse_output, state_out.focus, dealer=dealer
                )
                resolver_parse_output = _top_selling_dealer_scope(
                    db, resolver_parse_output, state_out.focus, contact_respond_id, space_id_for_turn
                )
            elif isinstance(state_out.focus.top_selling, dict) and state_out.focus.top_selling.get("hop"):
                resolver_parse_output = _without_carried_words(resolver_parse_output)
            gate_refusal: str | None = None
            staff_level_refused = False
            unnamed_account = _unnamed_account(verdict, resolver_parse_output)
            account_unnamed_n = unnamed_account
            if unnamed_account is not None:
                resolver_parse_output = {
                    **resolver_parse_output,
                    "entities": [
                        e
                        for e in resolver_parse_output.get("entities") or []
                        if not (isinstance(e, dict) and (e.get("hint") == "customer" or e.get("entity_type") == "customer"))
                    ],
                }
                scope_ids, scope_refused, gate_refusal = None, True, None
                account_question = f"Which customer is Account {unnamed_account} for?"
            else:
                resolver_parse_output, scope_ids, scope_refused, gate_refusal = _customer_scope_gate(
                    customer_scope, verdict, state_out.focus, resolver_parse_output, plan.domains
                )
            scope_ids = _drill_offer_subject(scope_ids, state_out.focus, plan.trace)
            if (
                len(plan.domains) > 1
                and resolver_parse_output.get("entities")
                and not jsc.truthy(resolver_parse_output.get("domain_hint"))
            ):
                fanout_domain = _fanout_domain_hint(
                    plan.domains, resolver_parse_output.get("entities")
                )
                if fanout_domain is not None:
                    resolver_parse_output = {
                        **resolver_parse_output,
                        "domain_hint": fanout_domain,
                    }
            resolver_ctx = {
                **ctx,
                "parse": {**(ctx.get("parse") or {}), "output": resolver_parse_output},
            }
            resolve_outcome = (
                turn_runtime.resolve_kinds(
                    db,
                    ctx=resolver_ctx,
                    branch_kind=provisional_branch_kind,
                    space_id=space_id_for_turn,
                    dry_run=dry_run,
                    # The roster about to be printed is an INCOMING one: it carries the
                    # has/no-incoming stamp whether the customer named the family this
                    # turn or the conversation carried it (browser pass 3, turn 2).
                    stamp_incoming=plan.ask is not None and "incoming" in plan.domains,
                    # Item 2: the customer roster carries has DO / no DO, the way the
                    # product roster carries has/no incoming. Read off the CUSTOMER TOKEN
                    # this turn named, not off `plan.ask`: on this pass the plan is the
                    # FIRST one, taken before the resolver ran, and the roster it is about
                    # to ask for does not exist yet (the same reason `stamp_incoming`
                    # reads the domain rather than the ask). R20's carve-out stands and is
                    # decided on the ask's OWN status: the probe measures DELIVERED
                    # orders, the opposite population from the outstanding report's DO
                    # block, so an outstanding ask is stamped with nothing rather than
                    # with a claim its own answer contradicts two turns later.
                    stamp_customer=(
                        any(
                            jsc.nullish_str(e.get("hint")).strip().lower() == "customer"
                            for e in (verdict.get("entities") or [])
                            if isinstance(e, dict)
                        )
                        and not _asks_outstanding(verdict)
                    ),
                    # Rows 1 and 7: the promotion and purchase-order product rosters carry
                    # has promo / no promo and has PO / no PO, read the same way
                    # `stamp_incoming` is - off the DOMAIN the turn is about to ask under,
                    # because the roster itself does not exist yet on this first plan.
                    stamp_promotion=plan.ask is not None and "promotion" in plan.domains,
                    stamp_purchase_order=(
                        plan.ask is not None and "purchase_order" in plan.domains
                    ),
                    roster_caps=roster_caps,
                    enforced_scope=bool((customer_scope or {}).get("enforced")),
                )
            )
            resolved_kinds = resolve_outcome.resolved_kinds
            compatible_entities = resolve_outcome.compatible_entities
            predicate = resolve_outcome.predicate
            resolved_candidates = resolve_outcome.resolved_candidates
            unplaced_tokens = resolve_outcome.unplaced_tokens
            spec_tier = resolve_outcome.spec_tier
            resolver_payload = resolve_outcome.payload
            # Every turn of availability access, not only a stock / ETA one: a code the
            # dealer names on an order or outstanding turn is carried into the next one, and
            # a bare "eta" after "srtw2000" asked its whole prefix family (tester re-run on
            # a5ba9dc9f, fix 4).
            if getattr(getattr(state_out, "profile", None), "stock_availability_only", False):
                exact = _exact_codes_only(
                    resolver_parse_output, resolved_kinds, resolved_candidates, compatible_entities, unplaced_tokens
                )
                if exact is not None:
                    resolved_kinds, resolved_candidates, compatible_entities, unplaced_tokens = exact
                    turn_trace.add("exact_codes_only", {"kept": [e.get("code") for e in compatible_entities]})
            # ACCOUNT-LEDGER Q4: staff asked a level the typed name lacks.
            staff_account_refusal = ((resolver_payload or {}).get("resolved") or {}).get("account_refusal")
            if staff_account_refusal and not (customer_scope or {}).get("enforced"):
                account_question, scope_refused, staff_level_refused = str(staff_account_refusal), True, True
            elif gate_refusal:
                account_question = gate_refusal
            # CHATBOT-SELFREF-SCOPE R4: every scope decision is on the trace, with its
            # reason and the ids it dropped, so a refusal explains itself.
            screened_refused = False
            screened_dropped: list[str] = []
            if customer_scope and customer_scope.get("enforced"):
                screened_refused, compatible_entities, resolved_candidates, screened_dropped = (
                    _screen_resolver_for_scope(
                        customer_scope, resolver_payload, compatible_entities, resolved_candidates
                    )
                )
                scope_refused = scope_refused or screened_refused
            if scope_refused:
                # ONE refusal for the whole turn (a fan-out says it once): nothing below
                # fetches or asks; the fixed line is the answer.
                customer_scope_refused = True
                resolved_kinds, resolved_candidates, unplaced_tokens, predicate = {}, {}, {}, None
                turn_trace.add(
                    "customer_scope",
                    {
                        "refused": "customer_not_permitted",
                        "reason": (
                            "account_unnamed"
                            if account_unnamed_n is not None
                            else "account_level_absent"
                            if staff_level_refused
                            else "resolver_matched_only_other_customers"
                            if screened_refused
                            else "typed_customer_word_outside_links"
                        ),
                        "dropped": screened_dropped,
                        "self_reference": verdict.get("self_reference") is True,
                    },
                )
                resolver_payload = _pass_scope_gate(resolver_payload, [], force=True)
            elif scope_ids is not None:
                # D3: the linked customers are this turn's customers.
                compatible_entities = _scoped_compatible(customer_scope, scope_ids, compatible_entities)
                resolver_payload = _pass_scope_gate(resolver_payload, compatible_entities, force=False)
                turn_trace.add(
                    "customer_scope",
                    {
                        "decision": "scoped_to_links",
                        "ids": list(scope_ids),
                        "self_reference": verdict.get("self_reference") is True,
                        **({"dropped": screened_dropped} if screened_dropped else {}),
                    },
                )
            answer_parse_output = turn_runtime.answer_parse_output(
                resolver_ctx["parse"]["output"],
                gate=(resolver_payload or {}).get("gate"),
                domains=plan.domains,
                db=db,
            )
            # SO-NUMBER-ASK: a plan that ASKED before the resolver ran ("Which one do you
            # mean? 1. SO421624 2. SO999998 3. SO422057") was built without the unplaced
            # words, so `narrow.decide`'s own "a roster never offers a token the resolver
            # could not place" rule never saw them. Re-entering with them lets it fire.
            # Order plans only, the lane this was measured on.
            if resolved_kinds or resolved_candidates or (
                unplaced_tokens and plan.ask is not None and "order" in plan.domains
            ):
                # The ONE re-entry the plan allows: what the resolver found goes back
                # into APPLY, so the narrower asks about things that exist and a
                # reconciled kind lands before anything is fetched.
                state_out, plan = turn_apply(
                    state_in,
                    verdict,
                    policy,
                    resolved_kinds,
                    resolved_candidates,
                    frozenset(unplaced_tokens),
                )
            if (
                scope_ids is not None
                and plan.ask is not None
                and plan.ask.kind == "customer_pick"
                and any(
                    isinstance(e, dict) and (e.get("hint") == "customer" or e.get("entity_type") == "customer") and _account_of(e)
                    for e in verdict.get("entities") or []
                )
            ):
                # Two typed customer words armed a pick between the WORDS; the linked
                # customers they matched are the turn's customers, so nothing is asked:
                # the plan is made again without the words.
                state_out, plan = turn_apply(
                    state_in,
                    {
                        **verdict,
                        "entities": [
                            e
                            for e in verdict.get("entities") or []
                            if not (isinstance(e, dict) and (e.get("hint") == "customer" or e.get("entity_type") == "customer"))
                        ],
                    },
                    policy,
                    resolved_kinds,
                    resolved_candidates,
                    frozenset(unplaced_tokens),
                )
            # Round 4 R5: a clarify this turn is about to ask keeps the ask, so its answer
            # re-runs it (`turn_runtime.with_clarify_answer`).
            clarify = turn_runtime.set_clarify_carry(
                verdict, predicate, (resolver_payload or {}).get("resolved")
            )
            if clarify is not None:
                state_out.focus.set_clarify = clarify
            _apply_top_selling_updates(state_out.focus, top_selling_updates)
            if scope_refused:
                # A refusal must not lock the next turns: nothing the refused message
                # named (the foreign word, unresolved) stays on the focus or the plan.
                state_out.focus.customers = []
                if account_unnamed_n is not None:
                    # S-3: the answer to the bot's own question keeps this Account number
                    # for that one turn (apply clears `set_clarify` on every other turn).
                    state_out.focus.set_clarify = {"account": account_unnamed_n}
                for spec in plan.fetch:
                    spec.entities = [e for e in spec.entities if e.get("hint") != "customer"]
            elif scope_ids is not None:
                # D3: the linked customers ARE the turn's customers: the header names
                # them and a follow-up carries them. The plan's own customer rows (the
                # typed word, never resolved) go, or `_entities_for` would drop the
                # linked rows for not matching that word.
                for spec in plan.fetch:
                    spec.entities = [e for e in spec.entities if e.get("hint") != "customer"]
                state_out.focus.customers = [
                    {
                        "uuid": cid, "hint": "customer", "canonical_code": code or name, "raw": name,
                        "name": name, "current_message": False,
                    }
                    for cid, name, code in customer_scope["linked"]
                    if cid in scope_ids
                ]

        # D ROUTE. Two facts outrank the plan and neither is IN one: a refused access
        # agent (contract 58, fail closed) and the stock-denial switch, which is decided
        # from the CONTACT's own record (contract 61, 62).
        if access.get("allowed") is not True:
            branch_kind = "access_denied"
        elif stock_denial_enabled and _stock_check_denied(db, envelope, verdict):
            branch_kind = "demand_qty" if _demand_qty_missing(verdict) else "stock_denied"
        else:
            branch_kind = turn_route(plan)

        # S4 graceful fallback (plan 7.1 and 7.2): every `low_signal` reply is
        # `ack + memory_line + offer`, and a history question is one of them (routed
        # here by `apply._lane`). Everything the reply reads from the database is read
        # now, while this session is open; the clarifier call runs without one.
        fallback_ctx: Any = None
        if branch_kind == "low_signal":
            try:
                fallback_ctx = _fallback_context(
                    db,
                    verdict=verdict,
                    memory_intake=memory_intake,
                    contact_respond_id=contact_respond_id,
                    dry_run=dry_run,
                    turn_id=turn_id,
                    policy=policy,
                    profile=getattr(state_out, "profile", None),
                )
            except Exception:  # noqa: BLE001 - the clarifier still answers, memory-less
                logger.warning("chatbot: the fallback context did not build", exc_info=True)
        # AC-MEM083: a business answer carried from memory names what it carried.
        if branch_kind in ("business_query", "check_promotion"):
            try:
                remembered_before["_carried_line"] = _carried_line(
                    db,
                    verdict=verdict,
                    memory_intake=memory_intake,
                    contact_respond_id=contact_respond_id,
                    dry_run=dry_run,
                )
            except Exception:  # noqa: BLE001 - the answer stands without the line
                logger.warning("chatbot: the carried line did not build", exc_info=True)
        # AC-MEM087/088: who a handover names, and what it carries to them.
        if branch_kind == "out_of_scope":
            try:
                remembered_before["_handover"] = _handover_context(
                    db,
                    verdict=verdict,
                    memory_intake=memory_intake,
                    contact_respond_id=contact_respond_id,
                    dry_run=dry_run,
                    turn_id=turn_id,
                )
            except Exception:  # noqa: BLE001 - the lane's own handover stands
                logger.warning("chatbot: the handover context did not build", exc_info=True)
        item = _stamp_item(access, branch_kind, {})

        # Owner ruling, hand pass 10 (21 Sep 2026, `test_rearch_r10_handpass10_
        # replay.py::TestHandPass10PromotionAskRepeatsAfterATierPick`): a fresh
        # promotion ask that names no access level of its own must RE-OPEN the
        # tier roster, never settle on the last picked tier. AC-MEM037 (chat-side
        # tier persistence, "a tier pick writes `chatbot_profile.tier` so the next
        # turn does not ask again") conflicts with that older, binding ruling -
        # security review 26 Sep 2026 removed the write entirely rather than
        # special-case it: `profile_facts.set_tier` and this call site are gone,
        # a resolved tier is never persisted onto the contact row, and every ask
        # still resolves fresh off `narrow.py`'s own turn-scoped state.
        contact_pk = remembered_before.get("_contact_pk")

        # AC-1546 / chatbot memory lane A (contract section 3): the episode belongs to
        # the topic that just CLOSED, and a topic closes because the customer changed
        # subject - not because this turn's lane went on to answer. Written HERE, where
        # the reset is decided, so a turn whose fetch failed or whose lane refused
        # still remembers the topic it ended. Once per turn, never mid-topic.
        #
        # Human intervention closes nothing (Q2 ruling): the topic is whatever it was
        # when the bot resumes. D14's third named exception (Q15) is the one write a
        # dry run may make: a CONSOLE turn writes an `is_test=true` frame of its own
        # world; every other dry run (clone/replay/harness) writes nothing.
        is_console_dry_run = dry_run and envelope.ingress == "console"
        written_frame = None
        # Fix lane round 3 (R1, owner hand test 28 Sep 2026): the parser's
        # `topic_reset` alone never closed "check stock X" then "incoming X" (same
        # product, so it reads as a follow-up). A turn whose plan names a different
        # domain from the open conversation's newest one is the other switch
        # (`episode_digest.close_trigger`, the backfill's same rule).
        this_domain = str(plan.domains[0]) if plan.domains else None
        may_write_episode = (not dry_run or is_console_dry_run) and not _is_human_intervened(envelope)
        open_domain = None
        if may_write_episode and this_domain and verdict.get("topic_reset") is not True:
            try:
                open_domain = memory_mod.open_topic_domain(
                    db,
                    contact_respond_id=contact_respond_id,
                    is_test=dry_run,
                    exclude_turn_id=turn_id,
                )
            except Exception:  # noqa: BLE001 - a lost episode is never a lost turn
                logger.warning("chatbot: the open topic read did not run", exc_info=True)
        close_trigger = episode_digest_mod.close_trigger(verdict, this_domain, open_domain)
        if close_trigger is not None and may_write_episode:
            try:
                written_frame = memory_mod.write_episode_for_reset(
                    db,
                    contact_respond_id=contact_respond_id,
                    is_test=dry_run,
                    resetting_turn_id=turn_id,
                )
            except Exception:  # noqa: BLE001 - a lost episode is never a lost turn
                logger.warning("chatbot: the episode write did not run", exc_info=True)
        # Stashed on the shared `before` box (the same object `_run_answer` and
        # `_run_entities_only_arm` already receive as `remembered_before`) so
        # `_record_memory_trace`, several call frames later, can report what THIS turn
        # closed without a new parameter threaded through every arm between here and
        # there.
        remembered_before["_episode_written"] = (
            {
                "id": written_frame.id,
                "turn_count": len(written_frame.turn_ids or []),
                "close_reason": written_frame.close_reason,
                "summary": written_frame.summary,
                "trigger": close_trigger,
                "domain": getattr(written_frame, "domain", None),
            }
            if written_frame is not None
            else None
        )

        # AC-MEM032: the tally runs after the episode write above has committed -
        # never before, since it reads the newest CLOSED frames and a frame this
        # turn just wrote is one of them. Round 3 (PLAN 6.0, AC-MEM049): facts are
        # learned and saved at EVERY context level, Off included - the level decides
        # only what the bot READS, never what it LEARNS. Episodes themselves are
        # ALSO written whatever the level, staff still see everything either way; the
        # one gate that still applies is B2's own (never on a dry run).
        # Stashed on `before` the same way `_episode_written` is, so `_run_answer`'s
        # tail can fold it into the turn's own `facts_saved` trace line.
        if written_frame is not None and not dry_run and contact_pk is not None:
            try:
                tallied = profile_facts_mod.tally(
                    db, contact_respond_id, is_test=dry_run, contact_pk=contact_pk
                )
                remembered_before["_facts_tallied"] = [
                    {"key": f["key"], "source": f["source"]} for f in tallied
                ]
            except Exception:  # noqa: BLE001 - a lost tally is never a lost turn
                logger.warning("chatbot: the profile tally did not run", exc_info=True)

        turn_trace.add(
            "apply",
            {
                "verdict": verdict,
                # What APPLY read this message AS, before any rule acted on it
                # (`turn/decide.py`): ANSWER, REFINE, NEW_ASK or CARRY, plus the one
                # rule that decided it.
                "decision": dict(plan.trace.decision) if plan.trace.decision else None,
                "state_diff": turn_runtime.focus_diff(state_in.focus, state_out.focus),
                "narrowing": list(plan.trace.narrowing),
                "reconciled": [list(r) for r in plan.trace.reconciled],
                "rules_fired": list(plan.trace.rules_fired),
                "plan": {
                    "domains": list(plan.domains),
                    "fetch": [spec.domain for spec in plan.fetch],
                    "denied": list(plan.denied),
                    "ask": plan.ask.kind if plan.ask is not None else None,
                    "lane": plan.trace.lane,
                },
            },
        )
        turn_trace.record(
            "routed",
            summary=f"Routed to {trace_mod.lane_words(branch_kind, verdict.get('domain_hint'))}.",
            why=trace_mod.routed_why(
                branch_kind, verdict, bool(access.get("allowed")), lane=plan.trace.lane
            ),
            facts={
                "lane": branch_kind,
                "domains": list(plan.domains),
                "asking": plan.ask.kind if plan.ask is not None else None,
                "stock_denial_enabled": stock_denial_enabled,
                "lane_completed_by_crm": branch_kind in enabled_lanes,
            },
            raw={"item": item},
        )

        # S3 (chatbot media-into-turn): bare entities, no domain, no carried focus -
        # resolved and answered deterministically here, never through the generic
        # fetch/ask machinery below (there is nothing for it to fetch: `plan.fetch`
        # and `plan.ask` are both empty for this lane) and never through the `casual`
        # lane's LLM clarifier.
        if plan.trace.lane == "entities_only":
            return _run_entities_only_arm(
                db,
                turn_id=turn_id,
                ctx=ctx,
                item=item,
                verdict=verdict,
                state=state_out,
                actions=actions,
                dry_run=dry_run,
                session_factory=session_factory,
                turn_trace=turn_trace,
                stage=stage,
                contact_respond_id=contact_respond_id,
                space_id=space_id_for_turn,
                remembered_before=remembered_before,
                recalled=recalled,
                from_photo=_media_source_modality(media_box) == "image",
                # Review round nit: a LIVE media outcome (image or voice) already got
                # its own "I read .../I heard ..." line from `_apply_media_reply_prefix`
                # - this arm's own lead would double it (most visibly on voice: "I
                # heard: X" followed by "I have X."). `None` on a patched-upstream
                # turn (no outcome ran here at all - S3), which still needs this arm's
                # own lead.
                media_prefixed=bool(media_box.get("outcome")),
            )

        # D9: no engine switch. The re-architected turn IS the engine, so a lane the
        # CODE can complete is completed here - `system_settings.chatbot_completed_lanes`
        # no longer gates it, because there is no second implementation to fall back to
        # (the n8n lanes it used to hand back to read the retired head's state). Rollback
        # is a blue/green redeploy, which is what the plan's own D9 says it is.
        # `delegate` stays on the response for the kinds no lane here can finish, so the
        # outer loop's arm Switch reads exactly what it always has (AC-1507).
        completes_here = branch_kind in CRM_COMPLETED_BRANCH_KINDS
        delegate = None if completes_here else branch_kind

        # S4: the low_signal lane finishes INSIDE the CRM, and its model call must not
        # run with a session open. Everything it needs from the database is read here.
        clarifier_prompt: dict[str, Any] | None = None
        clarifier_config: Any = None
        clarifier_setup_error: str | None = None
        if branch_kind == "low_signal" and completes_here:
            try:
                resolved_for_prompt = casual.resolve_for_prompt(db, ctx=ctx)
                clarifier_prompt = casual.construct_user_prompt(ctx, resolved_for_prompt)
                clarifier_config = casual.resolve_clarifier_config(
                    db,
                    override_version_id=_prompt_override(
                        envelope, casual.PROMPT_KEY, dry_run=dry_run
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - AC-403: the lane's own failure shape
                logger.warning(
                    "chatbot turn %s: low_signal lane setup failed", turn_id, exc_info=True
                )
                clarifier_setup_error = str(exc)

        # -- BRIDGE: a single-domain resolver exit answers via production's own
        #    composers, before any fetch runs (PLAN-chatbot-answer-half-reattach.md
        #    slice R3, AC-1683). `access_ask` (the resolver's own exit for a contact
        #    with no access rows at all) and `offer` (the gate's own ambiguous
        #    customer/product picker) are both decided by `resolve_gate.run` itself,
        #    so nothing here needs a fetch to answer them. `not_found` (R4, AC-1699 to
        #    AC-1705) is the SAME early exit - "nothing resolved at all" (H11's
        #    zero-tool case) - answered by the bridge's own miss arm instead.
        #    Precedence: where this and `narrow.decide`'s own roster arms would both
        #    ask, the bridge wins for a single-domain plan - `plan.ask` is left
        #    standing (R6 deletes the now-shadowed `narrow` arms) but never reaches
        #    `turn_compose.compose_question` while `answer` is already set here.
        answer: Any = None
        # Set the moment the bridge itself answers (either arm) - the FETCH section
        # below always assigns `answer` too (even `turn_compose.compose([])`'s own
        # empty Answer, for a plan with nothing to fetch), so `answer is None` alone
        # cannot tell "the bridge already answered" from "nothing has answered yet" by
        # the time the ASK section runs.
        bridge_answered = False
        lane_error_text: str | None = None
        # Chatbot stock ask v2 S4: the answered `stock_availability` entries of this
        # turn's fetch, acted on by `_run_answer` once the turn row is closed.
        stock_ask_entries: list[dict[str, Any]] = []
        # The fetch's envelopes, empty on a turn that fetched nothing (REFER-SALESMAN reads
        # them below the fetch section, on every dealer refer reply).
        envelopes: list[dict[str, Any]] = []

        # Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not merged, owner
        # ruling 24 Sep 2026) for chatbot-stock-ask-v2 S3. -- the OPEN TASK's own
        # re-ask: nothing to fetch, nothing to roster -- #
        # A task RESUMED with nothing new ("back to the stock check") asks only what is
        # still owed and calls no tool at all; so does a bare number the task could not
        # attribute to one of its slots. Composed the same way the stock refusal below
        # is - a text Answer, the whole reply, taking the same tail every composed
        # answer takes.
        if plan.trace.task_question and completes_here:
            stage[0] = "replied"
            answer = turn_compose.Answer(text=plan.trace.task_question)

        if (
            answer is None
            and branch_kind in ("business_query", "check_promotion")
            and completes_here
            and not sales_report_grant_refused
            and not customer_scope_refused
            # AC-1708 (captain's ruling, 20 Sep 2026): an `offer` / `access_ask` exit is
            # the SAME question whether the message named one domain or two - "which
            # customer do you mean?" has one answer, and asking it twice in two wordings
            # is the defect. The domain count gated this until now, so a two-domain
            # ambiguous-customer ask printed `turn/compose.py`'s generic header while the
            # one-domain one printed `gate.py`'s. HIT and MISS composition for a
            # multi-domain plan is untouched and still `turn/compose.py`'s: neither is an
            # `offer`/`access_ask` exit, and `question_for` answers nothing else.
            and isinstance(resolver_payload, dict)
            and (
                resolver_payload.get("_exit_kind") in ("access_ask", "offer")
                # R5: a `not_found` exit (the product never resolved) still needing a
                # tier pick - `entry == "access_check"` already ran the tier gate
                # before resolve-entity even tried the product, so its own
                # `tier_ask: True` outranks the product's own absence. Same gate
                # `answer_bridge.question_for` itself re-checks, kept in lockstep so
                # neither can decide alone that the other agrees.
                or (
                    isinstance(resolver_payload.get("tier_gate"), dict)
                    and resolver_payload["tier_gate"].get("tier_ask") is True
                    and resolver_payload.get("_exit_kind") == "not_found"
                )
            )
        ):
            from app.services.chatbot import answer_bridge
            from app.services.chatbot import copy as copy_mod

            answer = answer_bridge.question_for(
                resolver_payload,
                parser=answer_parse_output,
                ctx=ctx,
                canned=copy_mod.resolve(db),
                db=db,
                asked_at_turn=turn_no,
            )
            bridge_answered = True

        # A resolver `not_found` exit is answered by the SAME `answer_bridge.answer_for`
        # call the FETCH section below already makes post-fetch (`via_resolver_exit`
        # inside `answer_for` itself reads `payload.get("_exit_kind") == "not_found"`,
        # independent of what the fetch envelope carries) - there is no separate
        # PRE-fetch trigger here on purpose (R5, captain's ruling 20 Sep 2026, reverting
        # coder 28's R4 shortcut). `turn_runtime.make_tool_runner`'s `runner()` calls the
        # KEPT `lanes.business.run_fetch` unconditionally for every `FetchSpec`, whether
        # or not anything resolved (it is what decides "needs scope" / "not found" /
        # picker text for the unresolved shapes today, and `_answered_unfiltered`
        # relabels its own fragment `outcome: "not_found"` when nothing filtered it) - so
        # skipping straight to a customer-facing miss BEFORE that call ran means a
        # genuine infrastructure failure inside it is never seen: measured live,
        # `test_engine_failure_paths.py::TestTheBusinessLaneOnFetchFailure::
        # test_an_mcp_failure_that_raises_is_a_failed_turn_not_a_not_found` and
        # `test_s3_switch_and_complete_by_body.py::TestSendActionShape::
        # test_every_completed_lane_pins_quick_replies_string_or_null_never_a_list`
        # both mock `lanes.business.run_fetch` to raise and expect `status == "failed"`;
        # the pre-fetch shortcut answered "not found" instead because the mocked raise
        # was never reached. Same reply text either way (`not_found_error_message` and
        # `miss_suggest.run_miss_lane` read `resolved`/`gate`/`parser`, never the fetch
        # fragment itself), so removing the shortcut costs nothing observable on a live
        # not_found turn and buys back the failure-not-silence guarantee.

        # R3's second access_ask trigger (`fetch_arm == "tier-ask"`) is discovered only
        # once `lanes.business.run_fetch` has actually run the per-tier promotion probe -
        # but `narrow.decide`'s own `narrow_by_tier` policy (untouched this slice) treats
        # an unsettled tier as something to ASK about BEFORE fetching, so `plan.fetch` is
        # empty for exactly the turn that needs this arm (measured live: a two-tier
        # promotion ask). The SAME override also covers the coordinator's add-on
        # (`tier_proceed is True`): exactly ONE entitled tier needs no ask at all -
        # `needs_tier_ask` is false, `run_fetch`'s tool-selection path (not its tier_ask
        # arm) reads `tier_gate.access_levels_recomposed` and fetches straight away
        # (`lanes/business/__init__.py:637-649`) - so the SAME forced fetch, with no
        # bridge Answer at all, naturally wins over `narrow_by_tier`'s stale
        # entitlement-blind ask via `bridge_answered` alone. The bridge wins here too
        # (same precedence as access_ask/offer above): the resolver's OWN `tier_gate`
        # already says whether a pick is needed or a tier settles automatically, so this
        # fetch runs from a ONE-SPEC plan built for the occasion rather than waiting on
        # `narrow.py`'s now-shadowed ask.
        fetch_plan = plan
        if (
            answer is None
            and branch_kind in ("business_query", "check_promotion")
            and completes_here
            and not sales_report_grant_refused
            and not customer_scope_refused
            and not plan.fetch
            and len(plan.domains) == 1
            and isinstance(resolver_payload, dict)
            and resolver_payload.get("_exit_kind") == "continue"
            and isinstance(resolver_payload.get("tier_gate"), dict)
            and (
                resolver_payload["tier_gate"].get("tier_ask") is True
                or resolver_payload["tier_gate"].get("tier_proceed") is True
            )
        ):
            import dataclasses

            from app.services.chatbot.turn.plan import FetchSpec as _FetchSpec

            bridge_answered = True

            fetch_plan = dataclasses.replace(
                plan,
                fetch=[_FetchSpec(domain=plan.domains[0], entities=[], filters={}, date_window=None)],
            )

        # -- E FETCH + F COMPOSE, for the turn that has something to look up --- #
        if (
            answer is None
            and branch_kind in ("business_query", "check_promotion")
            and completes_here
            and not sales_report_grant_refused
            and not customer_scope_refused
        ):
            stage[0] = "looked_up"
            turn_ctx = turn_runtime.TurnContext(
                db=db,
                contact_respond_id=contact_respond_id,
                trace=turn_trace,
                policy=policy,
                profile=state_out.profile,
                tool_runner=turn_runtime.make_tool_runner(
                    db,
                    ctx=ctx,
                    verdict=verdict,
                    focus=state_out.focus,
                    compatible_entities=compatible_entities,
                    predicate=predicate,
                    unplaced=unplaced_tokens,
                    space_id=space_id_for_turn,
                    dry_run=dry_run,
                    turn_trace=turn_trace,
                    counted_set=spec_tier and bool(turn_runtime.class_scope_terms(verdict)),
                    resolver_gate=(
                        resolver_payload.get("gate")
                        if isinstance(resolver_payload, dict)
                        else None
                    ),
                    resolver_tier_gate=(
                        resolver_payload.get("tier_gate")
                        if isinstance(resolver_payload, dict)
                        else None
                    ),
                    # R4 (owner ruling 5): the record-key rerun gate asks the RESOLVER
                    # what this message's token is, not the parser's hint.
                    resolved_kinds=resolved_kinds,
                    customer_scope=customer_scope,
                    # R6 (fix round 2): so a null `routing.suggested_team` inside the
                    # per-domain fetch context gets the same domain-aware fill this
                    # turn's own `ctx.parse.output` already got above.
                    policy=policy,
                ),
                granted_reveals=access.get("attributes"),
                access_levels=list(verdict.get("access_levels") or []),
                contains_flyer=bool(verdict.get("contains_flyer")),
                # SRTSC07 (prod transcript, 22 Sep 2026): the SAME `routing.
                # suggested_agent` `with_routing_agent_default` already resolved at
                # L1399, before `compose()` mints a fresh `team_pick` off it.
                suggested_agent=jsc.get(verdict.get("routing"), "suggested_agent"),
                # Round 4 (owner-approved, 22 Sep 2026): this turn's own resolved
                # brand, off the SAME gate dict `resolver_gate=` two lines up already
                # reads - `lanes/business/gate.py::run_gate`'s own `routing_brand`.
                # `jsc.get` never raises on a non-dict `gate`, same contract as every
                # other reader of this payload.
                routing_brand=jsc.get(
                    resolver_payload.get("gate") if isinstance(resolver_payload, dict) else None,
                    "routing_brand",
                ),
                # No resolver ran at all (the focus product is settled, so it is not
                # re-resolved): `_team_pick_question` still takes the focus product's
                # brand (#865 sibling), the same read `_focus_brand_payload` makes. A
                # thunk, so the read runs only when compose mints (fix round 2, N2).
                focus_brand=(
                    (lambda: _focus_brand(db, state_out.focus))
                    if not isinstance(resolver_payload, dict)
                    else None
                ),
            )
            # Will `answer_bridge.answer_for` (R4/R5) answer this turn's miss? ONE
            # rule, computed once, read TWICE below: it gates that call, and it is
            # what tells `run_fetch` the bridge owns the cross-domain ladder for this
            # turn (reviewer S1 - the bridge's miss arm walks production's own
            # `answer.run_crossdomain`, so a rung `turn/fetch.py::_climb` also climbs
            # is fetched and then discarded, since the composer reads `envelopes[0]`
            # alone). The two facts that cannot exist until the fetch has run
            # (`answer`, `envelopes`) stay on the call site.
            #
            # A genuine absence, discovered once the fetch has actually run - either
            # the resolver's OWN exit was already "not_found" (`answer_for`'s
            # `via_resolver_exit`, R5: this is now the ONLY place that trigger is read,
            # so a mocked/broken fetch lane is always given the chance to raise first)
            # or `make_tool_runner.runner`'s own "answered unfiltered" fallback marked
            # the fragment `outcome: "not_found"`, carried through untouched as
            # `raw_fragment` (`via_error_fragment`). Single-domain only, same
            # precedence as every other bridge arm above.
            #
            # An outstanding report's own REFINE re-run (`turn/apply.py::
            # _answer_outstanding`) already built a real `FetchSpec` in the FIRST apply
            # pass, independent of whatever the resolver says about a location/status
            # WORD this turn named - that word's own "not_found" is not a verdict on
            # the report itself. `_answered_unfiltered`'s own docstring names the
            # report as the one exception to "unfiltered means nothing to narrow by",
            # and the SAME carve-out has to hold here or the bridge answers a generic
            # escalate offer over a report that never even ran. Measured live,
            # `test_outstanding_lane.py::TestDateNarrowingUnderAnOpenOffer` and
            # siblings.
            #
            # The SAME carve-out, for the FIRST ask rather than a REFINE re-run: an
            # outstanding-shaped order ask (`order_status` in the
            # `_DOCUMENT_STATUS_TO_ORDER_STATUS` family, every value containing
            # "outstanding") whose only named subject is a customer that did not
            # resolve is deferred per the 16 Sep 2026 ruling (`turn/narrow.py::
            # _narrow`'s own docstring: "a single un-uuid'd candidate... passes
            # through, deferred, rather than an ask manufactured from a name alone") -
            # never a manufactured escalate offer either. Measured live:
            # `lanes/business/__init__.py::run_fetch`'s own
            # `ENTITY_FILTER_REQUIRED_TOOLS` early return (the outstanding report needs
            # a filter, none could be built from an unresolved customer name) already
            # answers `_error_fragment(..., outcome="not_found")` WITHOUT ever calling
            # the report tool - R4's own `raw_fragment` carry-through then let this
            # bridge route that SAME fragment through the rich miss composer, which
            # mints an escalate `team_pick` `run_miss_lane` never used to reach for
            # this domain (`test_rearch_s3_journey_chain.py::TestJourneyChain::
            # test_step_4_outstanding_do_customer_must_narrow_one_no_document_question`,
            # green on R3, before R4's `raw_fragment` existed at all). A genuinely
            # RESOLVED customer/product on the SAME ask reaches the real report tool
            # instead (`kind: "result"`, no `outcome` key), so this exclusion never
            # hides an actual zero-row report answer.
            # Hand pass 9, D4: dropped its own `and isinstance(resolver_payload, dict)`
            # clause. `resolve_kinds` returns `payload=None` on two of its own early
            # returns - the resolver raised, OR (the one that bit here) this turn named
            # NO entities to resolve at all, which is exactly what an after-a-PICK turn
            # looks like: `turn/apply.py::_answer_pending` already assembled a fully
            # SETTLED entity (code + uuid) directly from the roster option, so
            # `with_carried_entities(unsettled_only=True)` finds nothing left to hand
            # the resolver. Requiring a dict here sent that turn's miss to `run_fetch`'s
            # OLD `_climb` rung (`bridge_owns_ladder=False`) instead of the bridge's own
            # AC-1705 ladder - live turn 139f5282-4968-4802-bd11-501de55bca50's "Stock
            # details found for the requested products." raw MCP-presenter intro,
            # never the bridge's "But no incoming matched these." sentence. The call
            # site below already hands `answer_bridge.answer_for` `resolver_payload or
            # {}` (the SAME "no opinion" fallback `answer_parse_output`'s own `gate=`
            # read uses two lines above `resolve_outcome` is unpacked), so a None here
            # degrades to an empty payload rather than skipping the bridge outright -
            # `answer_for`'s own `via_fetched_empty` trigger reads the FETCH envelope's
            # `raw_fragment`, not `resolver_payload`, so it still fires correctly.
            bridge_answers_a_miss = (
                len(fetch_plan.fetch) == 1
                and plan.ask is None
                and not any(spec.filters.get("outstanding") for spec in fetch_plan.fetch)
                and "outstanding" not in str(parsed_output.get("order_status") or "")
            )
            try:
                envelopes = run_fetch_mod.run_fetch(
                    fetch_plan, turn_ctx, bridge_owns_ladder=bridge_answers_a_miss
                )
                # BRIDGE (R3): the tier-ask arm is discovered only once the fetch has
                # actually run the per-tier promotion probe (`lanes.business.run_fetch`'s
                # own tier_ask arm) - `envelope_of` carries its fetch fragment through as
                # `tier_ask_fetch`, `None` on every other fetch. Single-domain only, same
                # precedence as the access_ask/offer bridge above.
                tier_fetch = (
                    envelopes[0].get("tier_ask_fetch")
                    if len(fetch_plan.fetch) == 1 and envelopes
                    else None
                )
                if tier_fetch is not None:
                    from app.services.chatbot import answer_bridge
                    from app.services.chatbot import copy as copy_mod

                    answer = answer_bridge.question_for(
                        {"_exit_kind": "continue"},
                        fetch=tier_fetch,
                        parser=answer_parse_output,
                        ctx=ctx,
                        canned=copy_mod.resolve(db),
                        db=db,
                        asked_at_turn=turn_no,
                    )
                    bridge_answered = True
                # BRIDGE (R4/R5): a genuine absence, discovered once the fetch has
                # actually run - either the resolver's OWN exit was already "not_found"
                # (`answer_bridge.answer_for`'s `via_resolver_exit`, R5: this is now the
                # ONLY place that trigger is read, so a mocked/broken fetch lane is
                # always given the chance to raise first) or `make_tool_runner.runner`'s
                # own "answered unfiltered" fallback marked the fragment
                # `outcome: "not_found"`, carried through untouched as `raw_fragment`
                # (`via_error_fragment`). Single-domain only, same precedence as every
                # other bridge arm above.
                if answer is None and envelopes and bridge_answers_a_miss:
                    from app.services.chatbot import answer_bridge
                    from app.services.chatbot import copy as copy_mod

                    answer = answer_bridge.answer_for(
                        # No resolver ran (the focus product is settled, so "eta" after
                        # a product answer re-resolves nothing): the miss still mints its
                        # offer with the focus product's brand (#865 sibling).
                        # Read only when the bridge will answer a miss, so a hit never
                        # pays for it (fix round 2, N2).
                        resolver_payload
                        or (
                            _focus_brand_payload(db, state_out.focus)
                            if answer_bridge.answers_a_miss({}, envelopes[0])
                            else {}
                        ),
                        envelope=envelopes[0],
                        parser=answer_parse_output,
                        ctx=ctx,
                        canned=copy_mod.resolve(db),
                        services=business_services.production_answer_services(db),
                        db=db,
                        asked_at_turn=turn_no,
                        roster_caps=roster_caps,
                        # The four `complete_answer` hands its own miss half and
                        # cross-domain call (reviewer B3/N3/N4): the configured ladder
                        # - without it `answer._next_crossdomain_rung` returns None for
                        # a non-dict and the SECOND rung, the PO rung from migration
                        # `491_chatbot_ladder_incoming_po` (owner ruling 8 Sep 2026),
                        # never ran - plus the turn id, the turn's own trace and the
                        # lane's real dry-run flag.
                        crossdomain_ladder=_crossdomain_ladder(switches),
                        turn_id=turn_id,
                        trace=turn_trace,
                        dry_run=dry_run,
                        # Hand pass 9 D2: the roster `apply()` carried into this turn
                        # (a still-open pick still answerable), so a bare escalate
                        # offer over ANOTHER miss patches onto it (contract 36) rather
                        # than replacing it - the same rule `turn_compose.compose`
                        # already applies on its own miss arm below.
                        carried_pending=state_out.pending,
                        # #1262 slice 11 (F8): same ladder-rung audience gate as the
                        # HIT arm below.
                        profile=state_out.profile,
                        dealer_stock_ask=_dealer_stock_ask(state_out, plan),
                        dealer_incoming_ask=_dealer_incoming_ask(state_out, plan),
                    )
                    if answer is not None:
                        bridge_answered = True
                if answer is None:
                    answer = turn_compose.compose(envelopes, state_out, policy, turn_ctx)
                    # BRIDGE (R5): the HIT arm, AC-1694 to AC-1696 - a single-domain
                    # ORDER fetch that genuinely found rows opens with the
                    # Customer/Product/Dates scope block. A no-op for every other
                    # domain (`answer_bridge.apply_scope_block`'s own
                    # `search_scope_header` returns `None` outside "order") and for
                    # a miss (`envelope_missed`), so the miss composer's own text is
                    # never touched here. `resolver_payload` is `None` on a bare
                    # positional pick (no entity of its own, so `resolve_kinds`
                    # never ran) - the gate/resolved axes fall back to the FOCUS
                    # carry (`apply_scope_block`'s own `focus_customers`/
                    # `focus_products`), which is where a customer picker's own
                    # pick and the original ask's product both already live.
                    # `own_header` (`envelope_of`'s own flag, true for the
                    # outstanding report) is excluded the SAME way `turn/compose.py`'s
                    # own `date_line` decoration already is: the outstanding report
                    # prints its OWN four-line Customer/Product/Location/Order-date
                    # header (`lanes/business/__init__.py::
                    # _outstanding_scope_filter_lines`), and stacking this one on top
                    # of it said the same thing twice - measured live,
                    # `test_outstanding_lane.py`'s own
                    # `test_report_skips_search_scope_header`.
                    # `not envelope_missed(...)` alone is not "this turn found rows":
                    # `turn/fetch.py::envelope_missed` returns False for a DENIED
                    # envelope too (a whole-domain grant refusal, `envelope_of`'s own
                    # `outcome == "access_denied"` arm), so a refusal was getting the
                    # Customer/Product/Dates block prepended - and this header's third
                    # fallback prints the gate row's own DB title, a name the contact
                    # never typed, over a turn they were refused.
                    if (
                        len(fetch_plan.fetch) == 1
                        and envelopes
                        and not envelopes[0].get("denied")
                        and not run_fetch_mod.envelope_missed(envelopes[0])
                        and not envelopes[0].get("own_header")
                    ):
                        from app.services.chatbot import answer_bridge
                        from app.services.chatbot.tail import scope_block as scope_block_mod

                        # Hand pass 12 round 3, Group F (owner ruling): a bare positional
                        # pick runs no resolver of its own (`resolver_payload is None`),
                        # so this header falls to the FOCUS carry - and a multi-uuid
                        # customer option's own focus rows carry NO name at all
                        # (`turn/apply.py::_answer_pending`'s own Group F rule: a name is
                        # only stamped when the option covers ONE identity), only the
                        # picked option's rollup `canonical_code`. Filled here the SAME
                        # way `turn_runtime.py::make_tool_runner.runner` already fills a
                        # FETCHED row's `display_name` - a local copy, `entity_type`
                        # stamped so the shared filler recognises them (a focus row's own
                        # key is `hint`, never `entity_type`), never written back onto
                        # `state_out.focus` itself.
                        focus_customers_named = [
                            {**row, "entity_type": "customer"}
                            for row in state_out.focus.customers
                        ]
                        turn_runtime.fill_customer_names(db, focus_customers_named)

                        answer = answer_bridge.apply_scope_block(
                            answer,
                            domain=fetch_plan.fetch[0].domain,
                            # The window the fetch ran over (`_spec_window`), so the
                            # header's Dates line never says "all dates" above rows a
                            # carried window narrowed (owner retest of round 4, R5).
                            qf=turn_runtime._spec_window(
                                (ctx.get("parse") or {}).get("output") or {}, fetch_plan.fetch[0]
                            ),
                            gate_json=scope_block_mod.with_brand_names(
                                (
                                    resolver_payload.get("gate")
                                    if isinstance(resolver_payload, dict)
                                    else None
                                ),
                                envelopes[0],
                            ),
                            resolver_json=(
                                resolver_payload.get("resolved")
                                if isinstance(resolver_payload, dict)
                                else None
                            ),
                            focus_customers=focus_customers_named,
                            focus_products=state_out.focus.products,
                        )
                        # BRIDGE (hand pass 11, defect 1): a zero-stock HIT climbs the
                        # SAME cross-domain ladder a miss does. `apply_crossdomain_hit`
                        # is a no-op off `crossdomain_zeroset`'s own gate on the
                        # PARSER's `domain_hint` for every hint but inventory/incoming,
                        # and off its own probeable-product gate when there is nothing
                        # zero to climb for - so calling it unconditionally here costs
                        # nothing on every other single-domain HIT.
                        aggregate = (
                            resolver_payload.get("aggregate")
                            if isinstance(resolver_payload, dict)
                            and isinstance(resolver_payload.get("aggregate"), dict)
                            else None
                        )
                        answer = answer_bridge.apply_crossdomain_hit(
                            answer,
                            envelope=envelopes[0],
                            parser=answer_parse_output,
                            resolved=(
                                resolver_payload.get("resolved")
                                if isinstance(resolver_payload, dict)
                                else None
                            ),
                            entities_names=aggregate.get("name") if aggregate is not None else [],
                            crossdomain_ladder=_crossdomain_ladder(switches),
                            ctx=ctx,
                            services=business_services.production_answer_services(db),
                            contact_id=(ctx.get("contact") or {}).get("id"),
                            space_id=space_id_for_turn,
                            trace=turn_trace,
                            dry_run=dry_run,
                            asked_at_turn=turn_no,
                            turn_id=turn_id,
                            # A page of a carried set answers about its own ids only.
                            # The focus still holds the LAST answer's rows, and reading
                            # them as this turn's ask closed a Cabana page with "No stock
                            # and no incoming for" the Sorento rows before it (round 7
                            # on PR #833, owner hand test item 6).
                            focus_products=None if set_page_turn else state_out.focus.products,
                            # #1262 slice 11 (F8): the ladder rung's own audience
                            # gate reads the SAME profile the composer's arm does.
                            profile=state_out.profile,
                        )
                        # BRIDGE (hand pass 11, defect 3): a HIT in one of several
                        # searched companies still offers the SILENT company's own
                        # team. No-op off its own `lookup_companies` gate for every
                        # single-company turn.
                        answer = answer_bridge.apply_silent_company_offer(
                            answer,
                            profile=state_out.profile,
                            envelope=envelopes[0],
                            parser=answer_parse_output,
                            # For the silent company's own `brand_code`, off the gate's
                            # per-company routing axis - the SAME read `apply_scope_block`
                            # above already makes of this payload.
                            gate=(
                                resolver_payload.get("gate")
                                if isinstance(resolver_payload, dict)
                                else None
                            ),
                            asked_at_turn=turn_no,
                            turn_id=turn_id,
                            # #865 round 6 (R1): the miss company's CS roster read.
                            db=db,
                            ctx=ctx,
                        )
            except Exception as fetch_error:  # noqa: BLE001 - a lane failure, not a crash
                logger.exception("chatbot turn %s: fetch or compose failed", turn_id)
                lane_error_text = f"{type(fetch_error).__name__}: {fetch_error}"
                turn_trace.record(
                    "looked_up",
                    status="failed",
                    summary="Could not look an answer up.",
                    why="The read the answer needs did not come back.",
                    facts={"lane": "business", "domains": [s.domain for s in fetch_plan.fetch]},
                    error=lane_error_text,
                    raw=None,
                )
            else:
                # No paging (owner ruling, 26 Sep 2026). A counted set is remembered only
                # when it was too long to list (more than `answer.SET_LIST_MAX`, no count
                # named): the reply asked "how many should I show?", and the answer to
                # that question lists that many of the same set (`turn/apply.py`'s
                # `set_count_answered`). Every other turn leaves nothing carried.
                #
                # Written only for a SPEC-tier answer: the counted set is how that tier of
                # the ONE product ladder renders, and a code-tier answer is a list.
                # `set_page_carry` refuses a set with no scope term of its own on top of
                # that, so the recount can never read the whole catalogue (turns 92d565a5
                # / b383d402 / 2e7ca929, 17 Sep 2026).
                #
                # Security B2 (re-check round): the carry records the ENTITLEMENT this
                # answer counted under, off the envelope's own `access_levels_used` (the
                # recomposed list the tool call actually carried). The recount uses that,
                # never the parser's list, which a bare count leaves empty and which reads
                # downstream as "no tier filter".
                from app.services.chatbot.lanes.business import answer as business_answer

                class_terms = turn_runtime.class_scope_terms(verdict)
                qualifying = int((predicate or {}).get("qualifying_total") or 0)
                asked = named_count(verdict.get("top_n"))
                withheld = (
                    predicate is not None
                    and plan.fetch
                    and spec_tier
                    and qualifying > business_answer.SET_LIST_MAX
                    and asked is None
                    and bool((predicate or {}).get("breakdown"))
                )
                # Fix round 9 on PR #833: a long set nothing splits lists what fits, and
                # is carried like a named count so "another N" continues it.
                listed_to_the_limit = (
                    predicate is not None
                    and plan.fetch
                    and spec_tier
                    and qualifying > business_answer.SET_LIST_MAX
                    and asked is None
                    and not withheld
                )
                # W4 (owner hand test round 2): a set listed only in part (the customer
                # named fewer than qualify) is carried too, so their own "another N"
                # continues it. The reply never offers that.
                partly_listed = (
                    predicate is not None
                    and plan.fetch
                    and spec_tier
                    and asked is not None
                    and min(asked, business_answer.SET_LIST_MAX) < qualifying
                )
                paged = bool(plan.fetch) and isinstance(plan.fetch[0].filters.get("set_page"), dict)
                if paged:
                    # A page of a carried set: the runner hands back where it stopped.
                    state_out.focus.set_page = envelopes[0].get("set_carry") if envelopes else None
                elif withheld or partly_listed or listed_to_the_limit:
                    state_out.focus.set_page = turn_runtime.set_page_carry(
                        predicate,
                        plan.fetch[0],
                        class_terms,
                        access_levels=(
                            envelopes[0].get("access_levels_used") if envelopes else None
                        ),
                        shown=(
                            0
                            if withheld
                            else business_answer.SET_LIST_MAX
                            if listed_to_the_limit
                            else min(asked, business_answer.SET_LIST_MAX)
                        ),
                    )
                elif predicate is not None and plan.fetch and spec_tier and (
                    predicate.get("other_brands") or predicate.get("set_brands")
                ):
                    # Round 7 item 6: the reply closed with "Other brands ... Name one to
                    # see them.", so the brand named next narrows this set.
                    state_out.focus.set_page = turn_runtime.set_page_carry(
                        predicate,
                        plan.fetch[0],
                        class_terms,
                        access_levels=(
                            envelopes[0].get("access_levels_used") if envelopes else None
                        ),
                        shown=qualifying,
                        offer_only=True,
                    )
                else:
                    state_out.focus.set_page = None
                # Ported from PR #1118 (not merged), D25: the open stock task is
                # whatever the REPLY says is still owed - opened, updated and closed
                # by one rule, read off the backend's own `needs_quantity` per
                # product. The engine never decides who must state a quantity; it
                # reads what the reply stated about it. Owner hand test 26 Sep, slice
                # 2: the same read narrows to an exact code and turns a family into a
                # which-one pick (`_stock_ask_reply`).
                answer = _stock_ask_reply(
                    answer,
                    state_out,
                    envelopes,
                    fetch_plan,
                    verdict,
                    turn_no=turn_no,
                )
                # #1262 fix lane round 7, R6: inside an order list no escalate offer and
                # no routing picker; an empty list says so in one line.
                answer = order_list_mod.list_reply(
                    answer,
                    was_open=order_list_was_open,
                    fetch_plan=fetch_plan,
                    envelopes=envelopes,
                    order_status=parsed_output.get("order_status"),
                )
                # Chatbot stock ask v2 S3, AC-SA314: an `incoming` entry answered
                # with its own packing list attaches it to THIS reply. `answer.files`
                # is the same seam every other domain's attachment already flows
                # through (`turn_runtime.envelope_of`'s own "files" -> here -> the
                # existing `send_attachments` action, `_send_actions`) - reused
                # rather than a new action kind, so B3 needs nothing new from the
                # executor.
                answer.files.extend(_stock_ask_packing_list_files(envelopes))
                stock_ask_entries = _stock_ask_answered_entries(envelopes)
                record_top_selling_asked(state_out.focus, envelopes)
                # LOWSTOCK-FILTER-ASK: a lane that asked for a required field hands its
                # slot back; it stays open for the next message only.
                state_out.focus.required_ask = next(
                    (e["required_ask"] for e in envelopes or [] if isinstance(e, dict) and isinstance(e.get("required_ask"), dict)),
                    None,
                )
                if (
                    (state_out.focus.top_selling or {}).get("asked")
                    and state_out.pending is not None
                    and state_out.pending.kind == "top_selling_pick"
                ):
                    # Owner retest (27 Sep 2026): the lane asked a question over a
                    # ranking still on screen, so the "2" that answers it must not
                    # pick row 2 of the old list. The question closes the list.
                    state_out = dataclasses_replace(state_out, pending=None)
                if (
                    state_out.pending is not None
                    and state_out.pending.kind in ("outstanding_detail", "outstanding_scope")
                    and _answered_by_so_list(envelopes)
                ):
                    # crew-tester re-run on PR #1435 (3 Oct 2026): "1" under the summary got
                    # the SO list's period question, and the sticky detail offer then read
                    # "september" as narrowing the summary. The SO list answered: it closes.
                    state_out = dataclasses_replace(state_out, pending=None)
                turn_trace.record(
                    "looked_up",
                    summary="Looked the answer up.",
                    why=(
                        "One tool is read per domain in the plan, and the answer is "
                        "rendered from what they returned."
                    ),
                    facts={
                        "domains": [s.domain for s in fetch_plan.fetch],
                        "sections": len(answer.sections),
                        "missed": [s.domain for s in answer.sections if s.miss and not s.figures],
                    },
                    raw={"envelopes": envelopes},
                )
            stage[0] = "routed"

        # -- the ASK: the composer's question IS the answer on this turn ------ #
        # `not bridge_answered`, never `answer is None`: the FETCH section above always
        # assigns `answer` (even `turn_compose.compose([])`'s own empty Answer, for a
        # plan with nothing to fetch), so `answer is None` cannot tell "the bridge
        # already answered" from "nothing has answered yet" - `bridge_answered` is the
        # one flag that means the former (R3).
        # `not sales_report_grant_refused` is SF-1's own rule, and this was the one
        # section that did not carry it - unlike the resolver block, the bridge, the
        # forced tier fetch and the FETCH/COMPOSE entry. An ungranted sales-report ask
        # naming TWO customer words builds `narrow.decide`'s `must_narrow_one` roster
        # from the contact's OWN typed words (the resolver never ran, so no DB names
        # and no stamps), which set `answer` here and swallowed the refusal composed
        # below entirely: the contact read "Which one do you mean?" over a question
        # they were never entitled to ask. The grant comes before ANY question.
        if (
            not bridge_answered
            and not sales_report_grant_refused
            and not customer_scope_refused
            and plan.ask is not None
            and completes_here
            and branch_kind in _ASK_BRANCH_KINDS
        ):
            # Hand pass 12 Phase 3 finding P1: a fresh roster over a DIFFERENT axis
            # (e.g. a product pick, once a multi-ledger `customer_pick` already
            # settled) must still name every carried customer, not just print the
            # option's shared rollup code - `turn/apply.py::_answer_pending`'s own
            # multi-uuid rows carry no `name` at all. Filled the SAME way the HIT-arm
            # scope block above already does (~2158-2166): a LOCAL copy, `entity_type`
            # stamped for the shared filler, never written back onto `state_out.focus`
            # itself.
            subject_state = state_out
            if state_out.focus.customers:
                focus_customers_named = [
                    {**row, "entity_type": "customer"} for row in state_out.focus.customers
                ]
                turn_runtime.fill_customer_names(db, focus_customers_named)
                subject_state = dataclasses_replace(
                    state_out,
                    focus=dataclasses_replace(state_out.focus, customers=focus_customers_named),
                )
            answer = turn_compose.compose_question(plan.ask, subject_state)

        # -- the REFUSAL: a denied stock check is an answer, not silence ------- #
        # `stock_denied` is one of the three business branch kinds, so it is outside
        # `canned_lanes.COMPLETED_BRANCH_KINDS` (that set is what the canned composer
        # knows how to build) and outside the fetch gate above - deliberately, because a
        # contact who is not allowed stock must not be shown the rows. Nothing else
        # composed it, so before this the turn closed `done` with no reply and no action
        # at all. The refusal is the whole reply and it takes the SAME tail every other
        # composed answer takes, so the turn is remembered and the caller is handed a
        # `send_message` to send (AC-105/AC-107: even a failed turn hands back a reply).
        if answer is None and branch_kind == "stock_denied" and completes_here:
            from app.services.chatbot import copy as copy_mod

            stage[0] = "replied"
            answer = turn_compose.Answer(
                text=canned_lanes.stock_denied_text(copy_mod.resolve(db))
            )

        # -- the REFUSAL: SF-1's grant-before-roster, same idiom as stock_denied ---- #
        # No new branch kind: the resolver never ran (gated above), so `plan` is
        # still the first, unreconciled pass and `branch_kind` reads as an ordinary
        # `business_query` - the fetch gate above already excludes this turn, so
        # nothing else composed it, exactly the shape `stock_denied` handles the
        # same way just above.
        if answer is None and sales_report_grant_refused and completes_here:
            from app.services.chatbot.lanes.business import SALES_REPORT_NOT_ENABLED_MESSAGE

            stage[0] = "replied"
            answer = turn_compose.Answer(text=SALES_REPORT_NOT_ENABLED_MESSAGE)

        # -- the REFUSAL: a customer word outside a scoped contact's links -------- #
        # Once for the whole turn, however many domains the plan fans out to; no picker,
        # no offer, nothing armed (`_customer_scope_gate`).
        if answer is None and customer_scope_refused and completes_here:
            stage[0] = "replied"
            answer = turn_compose.Answer(
                text=account_question or str((customer_scope or {}).get("refusal") or "")
            )

    if answer is not None and lane_error_text is None:
        if _dealer_stock_ask(state_out, plan) or _dealer_incoming_ask(state_out, plan):
            # Owner ruling 26 Sep 2026 (hand test F1): whatever composed this stock
            # reply, a dealer is referred to their salesman, never offered a team.
            # PR #1329 (ETA policy): a dealer's incoming reply is the same, so an
            # incoming miss no longer offers the purchasing team.
            answer = _dealer_refers_to_salesman(answer)
            if _dealer_incoming_ask(state_out, plan):
                answer = _dealer_names_the_miss(answer, envelopes)
        elif in_ranking_conversation:
            answer = _without_escalation_offer(answer)
        return _run_answer(
            turn_id=turn_id,
            ctx=ctx,
            item=item,
            branch_kind=branch_kind,
            actions=actions,
            answer=answer,
            state=state_out,
            remembered_before=remembered_before,
            dry_run=dry_run,
            session_factory=session_factory,
            turn_trace=turn_trace,
            stage=stage,
            contact_respond_id=contact_respond_id,
            verdict=verdict,
            recalled=recalled,
            stock_ask_entries=stock_ask_entries,
            # REFER-SALESMAN / CUSTOMER-ASKS-REFER-ONLY: what `refer_asks` names a refer
            # reply's rows by, read once the reply is final (`_run_answer`).
            refer_context={
                "envelopes": envelopes,
                "plan": plan,
                "pending_before": state_in.pending,
                "message_text": latest_user_message,
            },
            chat_console=_chat_console(envelope),
        )

    with _session(session_factory) as db:
        if branch_kind in canned_lanes.COMPLETED_BRANCH_KINDS and completes_here:
            stage[0] = "looked_up" if branch_kind == "ideate" else "replied"
            reply, session_patch, extra_actions = _complete_canned_lane(
                db,
                branch_kind=branch_kind,
                ctx=ctx,
                item=item,
                turn_id=turn_id,
                dry_run=dry_run,
                contact_respond_id=contact_respond_id,
                turn_trace=turn_trace,
                state=state_out,
            )
            actions = [*actions, *extra_actions]
            _close_turn(
                db,
                turn_id,
                status="done",
                stage="sent",
                branch_kind=branch_kind,
                error=None,
                records=turn_trace.persisted(),
                response={"ctx": ctx, "item": item, "actions": actions, "reply": reply},
            )
            return TurnResult(
                turn_id=turn_id,
                ctx=ctx,
                item=item,
                branch_kind=branch_kind,
                delegate=None,
                actions=actions,
                reply=reply,
                session_patch=session_patch if dry_run else None,
                status="done",
                stage="sent",
            )

        if lane_error_text is not None and completes_here:
            # The CRM owns this turn and cannot answer it. Recorded `failed` at the stage
            # it stopped, with the reply the caller sends, so the trace screen's Retry
            # (R4: manual, never automatic) has something to retry.
            hard_failure = _failed_result(
                turn_id,
                "looked_up",
                lane_error_text,
                actions,
                dry_run,
                ctx=ctx,
                item=item,
                branch_kind=branch_kind,
            )
            _close_turn(
                db,
                turn_id,
                status="failed",
                stage="looked_up",
                branch_kind=branch_kind,
                error=lane_error_text,
                records=turn_trace.persisted(),
                response={
                    "ctx": ctx,
                    "item": item,
                    "actions": hard_failure.actions,
                    "reply": hard_failure.reply,
                    "delegate_error": lane_error_text,
                },
            )
        elif delegate is not None and s7_mode and not dry_run:
            # S7 mode retires the n8n tail: a turn that still delegates has NOBODY to
            # finish it, so it is closed here rather than left as a ghost.
            orphan_error = (
                f"S7 mode is on (system_settings.chatbot_ordering_enabled), so the CRM "
                f"owns the tail and /complete is gone, but the {delegate!r} lane is not "
                f"completed in the CRM. Add {branch_kind!r} to "
                f"system_settings.chatbot_completed_lanes on a build that can complete "
                f"it, or turn S7 mode off."
            )
            logger.error("chatbot turn %s: %s", turn_id, orphan_error)
            turn_trace.record(
                "routed",
                status="failed",
                summary="The turn was routed to a lane the CRM cannot finish.",
                why=(
                    "S7 mode retires the n8n tail, so a lane that still delegates has "
                    "nobody left to complete it."
                ),
                facts={"lane": delegate, "s7_mode": True, "lane_completed_by_crm": False},
                error=orphan_error,
                raw={"item": item},
            )
            orphan_failure = _failed_result(
                turn_id,
                "routed",
                orphan_error,
                actions,
                dry_run,
                ctx=ctx,
                item=item,
                branch_kind=branch_kind,
            )
            _close_turn(
                db,
                turn_id,
                status="failed",
                stage="routed",
                branch_kind=branch_kind,
                error=orphan_error,
                records=turn_trace.persisted(),
                response={
                    "ctx": ctx,
                    "item": item,
                    "actions": orphan_failure.actions,
                    "reply": orphan_failure.reply,
                    "delegate_error": orphan_error,
                },
            )
            return orphan_failure
        elif not (branch_kind in _CRM_FINISHED_HERE and completes_here):
            _close_turn(
                db,
                turn_id,
                status="delegated" if delegate else "done",
                stage="routed",
                branch_kind=branch_kind,
                error=None,
                records=turn_trace.persisted(),
                response={"ctx": ctx, "item": item, "actions": actions},
            )

    if hard_failure is not None:
        return hard_failure

    if (
        branch_kind == "out_of_scope"
        and completes_here
        and turn_state.escalation_barred(getattr(state_out, "profile", None))
    ):
        # ESCALATION-CONTROL (owner, 30 Sep 2026): a barred contact asking for a person,
        # or answering an old offer, gets the salesperson referral and no hand-off.
        # `apply()` already routed it here as "escalation_barred"; this is the guard in
        # front of `_run_escalation_arm` whichever way the turn arrived.
        from app.services.chatbot import escalation_control

        turn_trace.add("escalation_barred", {"branch_kind": branch_kind})
        return _run_answer(
            turn_id=turn_id,
            ctx=ctx,
            item=item,
            branch_kind=branch_kind,
            actions=actions,
            answer=turn_compose.Answer(text=escalation_control.barred_reply(state_out.profile)),
            state=state_out,
            remembered_before=remembered_before,
            dry_run=dry_run,
            session_factory=session_factory,
            turn_trace=turn_trace,
            stage=stage,
            contact_respond_id=contact_respond_id,
            verdict=verdict,
            recalled=recalled,
            chat_console=_chat_console(envelope),
        )

    if branch_kind == "out_of_scope" and completes_here:
        # Owner correction, 21 Sep (hand pass 12 round 3): reverts hand pass 12 round
        # 2's `_run_member_offer_arm` detour (commit 8e4ecdac7). A "yes" accepting a
        # customer_service escalation offer with no member preference assigns by
        # ROUND ROBIN IMMEDIATELY, exactly as production - the CS roster does not
        # show on a SEPARATE later turn. Where the roster belongs instead: riding IN
        # THE SAME REPLY as the offer itself (see `tail/outcome.py::cs_offer_gate`
        # and `answer_bridge.py::_miss_question`, Group B2, hand pass 12 round 3).
        return _run_escalation_arm(
            turn_id=turn_id,
            ctx=ctx,
            item=item,
            actions=actions,
            dry_run=dry_run,
            session_factory=session_factory,
            turn_trace=turn_trace,
            stage=stage,
            state=state_out,
            verdict=verdict,
            remembered_before=remembered_before,
            contact_respond_id=contact_respond_id,
            recalled=recalled,
        )

    if branch_kind == "low_signal" and completes_here:
        return _run_casual_lane(
            turn_id=turn_id,
            ctx=ctx,
            item=item,
            actions=actions,
            dry_run=dry_run,
            session_factory=session_factory,
            turn_trace=turn_trace,
            stage=stage,
            clarifier_prompt=clarifier_prompt,
            clarifier_config=clarifier_config,
            setup_error=clarifier_setup_error,
            state=state_out,
            verdict=verdict,
            remembered_before=remembered_before,
            contact_respond_id=contact_respond_id,
            recalled=recalled,
            fallback=fallback_ctx,
            chat_console=_chat_console(envelope),
        )

    return TurnResult(
        turn_id=turn_id,
        is_test=dry_run,
        ctx=ctx,
        item=item,
        branch_kind=branch_kind,
        delegate=delegate,
        delegate_payload=None,
        actions=actions,
        session_patch=None,
        status="delegated" if delegate else "done",
        stage="routed",
    )


def _contact_block(envelope: Envelope, known_phone: str | None) -> dict[str, Any]:
    """respond.io's contact record, with the one gap the CRM can close filled in.

    Verbatim but for `phone`: see `turn_runtime.contact_phone` for why a missing one is
    the difference between a handover and a 400.
    """
    contact = dict(envelope.contact or {})
    if not jsc.truthy(contact.get("phone")) and known_phone:
        contact["phone"] = known_phone
    return contact


def _dealer_stock_ask(state_out: Any, plan: Any) -> bool:
    """Is this turn a stock ask by a dealer (an availability-only contact, hand test F1)?"""
    return _dealer_asks(state_out, plan, "inventory")


def _dealer_incoming_ask(state_out: Any, plan: Any) -> bool:
    """Is this turn an incoming ask by a dealer? The backend gives the same contact the
    dealer view on every incoming route (`eta_policy.is_dealer`: the same availability
    policy), so the reply is the ETA and the salesperson, never our stock or a team."""
    return _dealer_asks(state_out, plan, "incoming")


def _dealer_asks(state_out: Any, plan: Any, domain: str) -> bool:
    profile = getattr(state_out, "profile", None)
    if not getattr(profile, "stock_availability_only", False):
        return False
    domains = list(getattr(plan, "domains", None) or []) or list(
        getattr(state_out.focus, "domains", None) or []
    )
    return domain in domains


def _in_ranking_conversation(focus: Any) -> bool:
    """Is the conversation inside a ranking, or a report a ranking handed over to?"""
    slot = focus.top_selling if isinstance(focus.top_selling, dict) else None
    return focus.status == "top_selling" or bool(slot and slot.get("hop"))


def _asks_for_a_person(verdict: dict[str, Any]) -> bool:
    escalation = verdict.get("escalation") if isinstance(verdict.get("escalation"), dict) else {}
    return verdict.get("message_type") == "request_for_help" or escalation.get("is_escalation_confirmation") is True


def _without_escalation_offer(answer: Any) -> Any:
    """PR #1273 round 7 (round 5 R6, restated): inside a ranking or the report it handed
    over to, a reply offers no escalation and no routing picker unless the customer asked
    for a person. The sentence, the offer and an escalation question all go; a pick the
    reply also asked stays, without the offer attached to it."""
    from app.services.chatbot import dealer_stock as dealer_mod
    from app.services.chatbot.turn.pending import ESCALATION_OFFER_KINDS

    text, _had = dealer_mod.refers_to_salesman(getattr(answer, "text", "") or "")
    question = getattr(answer, "question", None)
    if question is not None:
        if question.kind in ESCALATION_OFFER_KINDS:
            question = None
        elif (question.payload or {}).get("escalate_offered") is True:
            question = dataclasses_replace(question, payload={**question.payload, "escalate_offered": False})
    return dataclasses_replace(answer, text=text, question=question, offer=None)


def _dealer_refers_to_salesman(answer: Any) -> Any:
    from app.services.chatbot import dealer_stock as dealer_mod

    if getattr(answer, "question", None) is not None and (
        (answer.question.payload or {}).get("stock_pick") is True
    ):
        return answer
    text, question = dealer_mod.without_escalation(
        getattr(answer, "text", "") or "", getattr(answer, "question", None)
    )
    if text == (getattr(answer, "text", "") or "") and question is getattr(answer, "question", None):
        return answer
    return dataclasses_replace(answer, text=text, question=question)


def _stock_ask_reply(
    answer: Any,
    state_out: Any,
    envelopes: list[dict[str, Any]],
    fetch_plan: Any,
    verdict: dict[str, Any],
    *,
    turn_no: int,
) -> Any:
    """The stock task after the tool's reply, and the reply itself when it is a
    question (owner hand test 26 Sep, slice 2).

    `turn/task.py::after_reply` owns the rule; this writes its tasks onto the focus and,
    for a single-domain stock turn whose reply still needs a quantity, says the task's
    own named question (or the family pick) instead of the presenter's bare "How many
    units do you need?". The pick is minted as the turn's open question, so the tail
    persists it and the next turn's `decide()` reads a typed code against its options.
    """
    reply = turn_task.after_reply(
        tuple(state_out.focus.tasks or ()),
        envelopes,
        turn_no=turn_no,
        # SEC-S2: did this ask name a product at all? A bare "what stock do you have?"
        # fetches a page of the catalogue, and a task must not be opened to collect a
        # quantity for every row of it. Scoped to the INVENTORY spec only (review
        # round 2): a multi-domain ask like "promo for X, and what stock do we have?"
        # names X on the promotion spec, not on the inventory one.
        named_products=any(
            spec.entities for spec in fetch_plan.fetch if spec.domain == "inventory"
        ),
        asked=[
            e
            for e in (verdict.get("entities") or [])
            if isinstance(e, dict) and e.get("current_message") is True
        ],
        demand_qty=verdict.get("demand_qty"),
    )
    state_out.focus.tasks = reply.tasks
    if [spec.domain for spec in fetch_plan.fetch] != ["inventory"]:
        return answer
    block_seen = any(
        isinstance(envelope, dict) and envelope.get("stock_availability")
        for envelope in envelopes or []
    )
    if not block_seen:
        return answer
    # AVAIL-MODE-REPLIES rule 5 (owner, 2 Oct 2026): ONE combined reply - the answered
    # lines in the order asked, then the codes found nowhere, then at most one question
    # (this reply's own, else the lists of the pick just answered that are still open).
    text, pick = reply.text, reply.pick
    queued = verdict.get(turn_task.NEXT_PICKS)
    owed = [e for e in verdict.get(turn_task.OWED) or [] if isinstance(e, dict)]
    if not pick and isinstance(queued, dict) and queued.get("groups"):
        # The lists of the last pick the dealer has not answered, numbers kept. Pickers
        # come first (reviewer S1): a code owed a quantity, including the one just
        # picked, rides on them and is asked once no list is left open.
        carried = [*owed, *turn_task.owed_of(tuple(state_out.focus.tasks or ()))]
        state_out.focus.tasks = tuple(t for t in state_out.focus.tasks or () if t.kind != "stock_qty")
        text = turn_task.picks_question(queued["options"], queued["groups"])
        pick = turn_task.stock_pick(queued["options"], queued["groups"], carried)
    elif not pick and owed:
        # Reviewer B4: the pick is answered, so the codes owed a quantity beside it are
        # asked now, in one question with any the picked code owes itself.
        state_out.focus.tasks, text = turn_task.with_owed(
            tuple(state_out.focus.tasks or ()), owed, turn_no=turn_no
        )
    misses = _unplaced_tokens(envelopes)
    if not text and not misses:
        return answer
    parts = _stock_answer_lines(envelopes)
    if misses:
        parts.append(f"Couldn't find: {', '.join(misses)}.")
    if text:
        parts.append(text)
    question = (
        turn_pending.ask("product_pick", pick["options"], asked_at_turn=turn_no, payload=pick["payload"])
        if pick
        else None
    )
    return dataclasses_replace(answer, text="\n\n".join(parts), question=question, sections=[])


#: `turn/compose.py`'s once-per-turn line for a token nothing placed.
_COMPOSE_MISS = re.compile(r"\n*I could not find [^\n]*\.[ \t]*$")


def _dealer_names_the_miss(answer: Any, envelopes: list[dict[str, Any]]) -> Any:
    """AVAIL-MODE-REPLIES rule 5 for a dealer's ETA ask: a code found nowhere is named as
    "Couldn't find: X, Y." after the ETA lines and before the refer line, the same
    sentence the stock mix uses (compose prints "I could not find X." after the refer
    line). A reply without that line (a total miss, already named) is left as it is."""
    text = getattr(answer, "text", "") or ""
    misses = _unplaced_tokens(envelopes)
    if not misses or not _COMPOSE_MISS.search(text):
        return answer
    from app.services.chatbot.turn import refer
    from app.services.chatbot.turn.task import REFER_TO_SALESMAN

    blocks = _COMPOSE_MISS.sub("", text).rstrip().split("\n\n")
    line = f"Couldn't find: {', '.join(misses)}."
    if blocks and blocks[-1].strip() == REFER_TO_SALESMAN:
        # The refer line stays last, printed through its one helper.
        head = "\n\n".join(b for b in blocks[:-1] if b.strip())
        return dataclasses_replace(answer, text=refer.after("\n\n".join(p for p in (head, line) if p)))
    return dataclasses_replace(answer, text="\n\n".join(p for p in (*blocks, line) if p.strip()))


def _stock_answer_lines(envelopes: list[dict[str, Any]]) -> list[str]:
    """The answered lines the presenter printed ("<code> x <Q>: ..."), in the order asked."""
    lines: list[str] = []
    for envelope in envelopes or []:
        if not isinstance(envelope, dict) or not envelope.get("stock_availability"):
            continue
        for item in envelope.get("figures") or envelope.get("items") or envelope.get("answers") or []:
            flags = item.get("flags") if isinstance(item, dict) else None
            title = item.get("title") if isinstance(item, dict) else None
            if isinstance(flags, dict) and flags.get("branch") and not flags.get("needs_quantity") and title:
                lines.append(str(title))
    return lines


def _unplaced_tokens(envelopes: list[dict[str, Any]]) -> list[str]:
    """The tokens this turn's fetch could place on no product, once each, as typed."""
    out: list[str] = []
    for envelope in envelopes or []:
        for token in (envelope.get("unresolved") if isinstance(envelope, dict) else None) or []:
            if isinstance(token, str) and token and token not in out:
                out.append(token)
    return out


def _run_answer(
    *,
    turn_id: str,
    ctx: dict[str, Any],
    item: dict[str, Any],
    branch_kind: str,
    actions: list[dict[str, Any]],
    answer: Any,
    state: Any,
    remembered_before: dict[str, Any],
    dry_run: bool,
    session_factory: SessionFactory,
    turn_trace: Any,
    stage: list[str],
    contact_respond_id: str,
    verdict: dict[str, Any],
    recalled: list[dict[str, Any]],
    stock_ask_entries: list[dict[str, Any]] | None = None,
    chat_console: bool = False,
    refer_context: dict[str, Any] | None = None,
) -> TurnResult:
    """G TAIL for a turn the composer answered: persist, record, hand the actions back.

    One tail for the two arms that reach it (a fetched answer and an ask), because the
    turn's memory must not depend on which of them ran - the `Answer` carries the text,
    the actions and the question, and this writes exactly that.
    """
    # ESCALATION-CONTROL: the backstop behind every offer site's own gate - whatever
    # composed this reply, a barred contact is offered no hand-off.
    if turn_state.escalation_barred(getattr(state, "profile", None)):
        from app.services.chatbot import escalation_control

        answer = escalation_control.strip_offers(answer, state.profile)
    # AC-MEM083 (S4): an answer whose subject was carried from memory opens with the
    # one line naming what was carried, built in `_carried_line` before the fetch.
    carried = remembered_before.get("_carried_line")
    if carried and (getattr(answer, "text", "") or "").strip():
        answer = dataclasses_replace(answer, text=f"{carried}\n\n{answer.text}")
    stage[0] = "replied"
    reply = {
        **_reply_of(answer),
        "attachments_src": answer.files or None,
    }
    turn_trace.record(
        "replied",
        summary=trace_mod.replied_summary(reply, branch_kind),
        why="The reply is composed from what the plan fetched, never from the customer's words.",
        facts={
            "lane": branch_kind,
            "sections": len(answer.sections),
            "asking": answer.question.kind if answer.question is not None else None,
            "files": len(answer.files),
        },
        raw={"reply": reply},
    )

    stage[0] = "remembered"
    written = False
    with _session(session_factory) as db:
        tail_ctx = turn_runtime.TurnContext(
            db=db,
            contact_respond_id=contact_respond_id,
            access_levels=list(verdict.get("access_levels") or []),
            contains_flyer=bool(verdict.get("contains_flyer")),
            ideation=remembered_before.get("ideation"),
        )
        # ONE payload for both kinds of turn: a live turn writes it, a dry run hands it
        # back as `session_patch` and writes nothing (D14). Same rule `run_tail` applies
        # for the older lanes, so every asking lane carries its question to the next
        # console or replay turn the same way (finding 2a).
        session_payload = turn_tail.session_payload(state, answer, tail_ctx)
        if not dry_run:
            turn_tail.persist(
                state, answer, tail_ctx, base=remembered_before, turn_id=turn_id
            )
            _log_session_write(db, turn_id=turn_id, contact_respond_id=contact_respond_id)
            written = True

        facts_saved = _apply_profile_statements(
            db,
            turn_trace,
            verdict=verdict,
            remembered_before=remembered_before,
            contact_respond_id=contact_respond_id,
            turn_id=turn_id,
            dry_run=dry_run,
        )

        _record_memory_trace(
            db,
            turn_trace,
            contact_respond_id=contact_respond_id,
            before=remembered_before,
            state=state,
            answer=answer,
            recalled=recalled,
            dry_run=dry_run,
            written=written,
            facts_saved=facts_saved,
        )
        lane_actions = [*actions, *_answer_actions(answer, dry_run=dry_run)]
        turn_trace.record(
            "sent",
            summary="Handed the reply to the caller to send.",
            why="The CRM never sends on the turn path; n8n owns respond.io egress (D9).",
            facts={"lane": branch_kind, "actions": len(lane_actions), "dry_run": dry_run},
            raw={"actions": lane_actions},
        )
        _close_turn(
            db,
            turn_id,
            status="done",
            stage="sent",
            branch_kind=branch_kind,
            error=None,
            records=turn_trace.persisted(),
            response={"ctx": ctx, "item": item, "actions": lane_actions, "reply": reply},
        )

    _record_customer_asks(
        session_factory,
        turn_id=turn_id,
        contact_respond_id=contact_respond_id,
        state=state,
        stock_entries=stock_ask_entries or [],
        reply_text=reply.get("text") or "",
        refer_context=refer_context,
        ctx=ctx,
        dry_run=dry_run,
        chat_console=chat_console,
    )

    return TurnResult(
        turn_id=turn_id,
        is_test=dry_run,
        ctx=ctx,
        item=item,
        branch_kind=branch_kind,
        delegate=None,
        actions=lane_actions,
        reply=reply,
        session_patch=session_payload if dry_run else None,
        status="done",
        stage="sent",
    )


def _apply_profile_statements(
    db: Session,
    turn_trace: Any,
    *,
    verdict: dict[str, Any],
    remembered_before: dict[str, Any],
    contact_respond_id: str,
    turn_id: str,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Apply the verdict's `profile_statements` and return the facts this turn saved
    (the tally's, stashed at the topic reset, plus the stated ones)."""
    # AC-MEM033/AC-MEM069 (round 3): `profile_statements` (a LIST, up to 3) the
    # parser read off THIS message is applied in the tail of EVERY live arm - the
    # answer, the casual lane and the escalation arm (reviewer pass at d89110c0, B1: a
    # statement-only message has no domain and routes to `low_signal`) - never
    # earlier, since a statement is only worth learning once the turn itself is being
    # persisted (a dry run persists nothing, so it learns nothing either).
    # Round 3 also drops the OLD level gate here: facts are learned and saved at
    # EVERY context level, Off included (PLAN 6.0, AC-MEM049) - the level decides
    # only what the bot READS. `apply_statement` owns every validation and
    # precedence rule; `None` back means it was rejected (an invalid value, or a
    # staff fact already owns the key) and nothing is saved.
    facts_saved: list[dict[str, Any]] = list(remembered_before.get("_facts_tallied") or [])
    statements_raw = verdict.get("profile_statements")
    # "up to 3" is a POSITION cap on the verdict's own list, applied before
    # validation - a verdict naming 4 (however it got past the strict schema)
    # writes only the first three, whether or not each of those three is itself
    # valid (AC-MEM033/069's own "keep first 3" test).
    statements = statements_raw[:3] if isinstance(statements_raw, list) else []
    if not dry_run:
        # Security review 26 Sep 2026 (B1): lock by the primary key intake
        # already resolved for this turn, never re-derive by the ambiguous
        # `respond_io_id`. An intake that degraded (no pk resolved) writes
        # nothing, for every statement alike.
        tail_contact_pk = remembered_before.get("_contact_pk")
        for statement in statements:
            if not isinstance(statement, dict) or not statement.get("key"):
                continue
            stmt_key = statement.get("key")
            stmt_spec = profile_facts_mod.VOCABULARY.get(stmt_key)
            if stmt_spec is None or not stmt_spec.allow_stated:
                # A statement naming a key outside the six-key stated
                # vocabulary (contract section 6.5) - traced regardless of the
                # contact's own memory level, since this is the PARSER's
                # emission being out of bounds, not a settings decision.
                turn_trace.add(
                    "profile_statement_dropped",
                    {"key": stmt_key, "reason": "not a stated-vocabulary key"},
                )
                continue
            if tail_contact_pk is None:
                turn_trace.add(
                    "profile_statement_dropped",
                    {"key": stmt_key, "reason": "contact resolution degraded"},
                )
                continue
            try:
                entry = profile_facts_mod.apply_statement(
                    db,
                    contact_respond_id,
                    stmt_key,
                    statement.get("value"),
                    turn_id=turn_id,
                    contact_pk=tail_contact_pk,
                )
            except Exception:  # noqa: BLE001 - a lost statement is never a lost turn
                logger.warning("chatbot: profile_statement apply did not run", exc_info=True)
                turn_trace.add("profile_statement_dropped", {"key": stmt_key, "reason": "apply error"})
                continue
            if entry is not None:
                facts_saved.append({"key": entry["key"], "source": entry["source"]})
            else:
                turn_trace.add(
                    "profile_statement_dropped",
                    {"key": stmt_key, "reason": _dropped_reason(db, stmt_key, statement.get("value"))},
                )
    return facts_saved


def _dropped_reason(db: Session, key: str, value: Any) -> str:
    """Why `apply_statement` saved nothing: the value failed the key's validation, or a
    fact that outranks a statement (staff) already owns the key."""
    spec = profile_facts_mod.VOCABULARY[key]
    if profile_facts_mod._normalize_for_stated(key, spec, value, db) is None:
        return "invalid value"
    return "outranked"


def _run_entities_only_arm(
    db: Session,
    *,
    turn_id: str,
    ctx: dict[str, Any],
    item: dict[str, Any],
    verdict: dict[str, Any],
    state: Any,
    actions: list[dict[str, Any]],
    dry_run: bool,
    session_factory: SessionFactory,
    turn_trace: Any,
    stage: list[str],
    contact_respond_id: str,
    space_id: str | None,
    remembered_before: dict[str, Any],
    recalled: list[dict[str, Any]],
    from_photo: bool,
    media_prefixed: bool = False,
) -> TurnResult:
    """S3 (PLAN-chatbot-media-into-turn.md): bare entities, no domain, no carried
    focus (AC-1822 to AC-1832). Resolved directly against the SAME resolver seam
    the fetch would use (`business_services.production_services(db).resolve_entity`),
    never through the business lane's fetch/ask machinery - there is no domain for
    it to fetch against - and never through the `casual` lane's LLM clarifier.

    Placed tokens settle onto `focus.products` WITH a uuid (`focus_settles_product`,
    AC-1823); `state.focus.products` already carries an entry per token (`apply()`'s
    own `_focus_rules`, run before this arm), unresolved raw and all, so this only
    needs to ENRICH the matching rows rather than build the list from nothing.
    """
    stage[0] = "looked_up"
    # Review round B1(b): filtered the SAME way the lane gate was (`turn/apply.py::
    # is_product_shaped_entity`) - a customer/order/brand token riding alongside a
    # real code in one message must never be resolved as a "missed" product and
    # reported back as "Couldn't find Hanlim".
    entities = [
        e
        for e in (verdict.get("entities") or [])
        if isinstance(e, dict)
        and e.get("current_message") is True
        and e.get("raw")
        and is_product_shaped_entity(e)
    ]
    raws = [e["raw"] for e in entities]
    placed: list[str] = []
    unplaced: list[str] = []
    if raws:
        from app.api.v1.system.references import ResolveReferenceRequest

        # Security fix B2 (browser pass, reproduced 2/2): `resolve_reference_post`
        # is a ROUTE function, called in-process rather than over HTTP, so the
        # naive worry was that the router dependency which would normally stamp
        # company scope onto the request session (`apply_company_scope`) never
        # runs for it. MEASURED (hot-fix follow-up) rather than assumed: an
        # explicit `set_company_scope(db, contact_scope)` re-stamp right here was
        # tried and is REDUNDANT - `db` already carries the correct scope by the
        # time this line runs, because `run_turn` wraps `session_factory` itself
        # (`_scoped_factory`, H56, top of this file) before `_run_stages` opens
        # ANY session, including this arm's own `db`. Proved for a NON-default
        # company too (`test_the_explicit_restamp_is_load_bearing_for_a_non_
        # default_company`, deliberately named for the hypothesis it disproved):
        # a contact mapped to Mocha, with a same-code decoy product under
        # Sorento, still places the Mocha row with the line deleted. The actual
        # bug this fix's other half caught (`resolutions` keyed on the wrong
        # field, see below) is what made every code look unplaced regardless of
        # scope - not a missing re-stamp.
        services = business_services.production_services(db, space_id=space_id)
        body = {"tokens": raws, "allowed_entity_types": ["product"]}
        ResolveReferenceRequest(**body)  # validated the same way every other caller is
        result = services.resolve_entity(body)
        # B2 fix, second half (browser pass): `resolve_reference_post`'s own
        # resolutions carry `token`, never `raw` - keying on `raw` here silently
        # collapsed every resolution to `None` and every code came back unplaced
        # regardless of company scope. Masked in every OTHER test in this file by
        # `_resolve_services`'s stub, which (correctly, for the real API) sets
        # BOTH keys on each entry.
        resolutions = {
            r.get("token"): r for r in jsc.array(jsc.get(result, "resolutions")) if isinstance(r, dict)
        }
        by_raw = {row.get("raw"): row for row in state.focus.products if isinstance(row, dict)}
        # Live browser pass finding: the ONE `apply` trace event every turn gets is
        # recorded BEFORE this arm ever runs (`_run_stages`, right after parsing) -
        # the normal business-fetch path re-enters `turn_apply` with the resolver's
        # own output and so its OWN `apply` event (still the only one) already
        # reflects resolved entities; this arm never re-enters `turn_apply` at all,
        # so the drawer's Apply tab stayed on the pre-resolution snapshot forever,
        # reading as "the code never resolved" even on a turn that resolved it
        # correctly (proven by the SAME turn's Memory tab / `focus.products` itself).
        # A snapshot BEFORE the loop mutates these dicts in place, so the second
        # `apply` event below can show a real before/after rather than identical
        # dicts.
        before_products = copy.deepcopy(by_raw)
        for raw in raws:
            resolution = resolutions.get(raw)
            matches = jsc.array(jsc.get(resolution, "matches")) if resolution else []
            row = by_raw.get(raw)
            if matches and row is not None:
                match = matches[0]
                row["uuid"] = jsc.get(match, "uuid")
                row["entity_type"] = jsc.get(match, "entity_type")
                row["canonical_code"] = jsc.get(match, "canonical_code")
                placed.append(raw)
            else:
                unplaced.append(raw)
        turn_trace.add(
            "apply",
            {
                "verdict": verdict,
                "decision": None,
                "state_diff": {
                    "products": {
                        "before": [before_products.get(raw) for raw in raws],
                        "after": [by_raw.get(raw) for raw in raws],
                    }
                },
                "narrowing": [],
                "reconciled": [],
                "rules_fired": ["entities_only_resolved"],
                "plan": {"domains": [], "fetch": [], "denied": [], "ask": None, "lane": "entities_only"},
            },
        )

    turn_trace.record(
        "looked_up",
        summary="Read the codes and looked each one up.",
        why="The entities-only arm resolves bare codes directly; there is no domain to fetch against yet.",
        facts={"placed": len(placed), "unplaced": len(unplaced)},
        raw={"placed": placed, "unplaced": unplaced},
    )

    text = turn_compose.entities_only_reply(
        placed, unplaced, from_photo=from_photo, media_prefixed=media_prefixed
    )
    answer = turn_compose.Answer(text=text)
    # `_run_answer` opens and commits its OWN session for the tail (persist, close);
    # this caller's own `db` (the "access, apply, route" session, still open) must
    # have nothing pending on it first - every OTHER caller of `_run_answer` reaches
    # it after a write of its own already committed `db` along the way (the resolver
    # route call among them); this arm's own `resolve_entity` call is read-only, so
    # nothing does that here, and leaving `db` mid-transaction across the tail's own
    # nested session left the tail's commit invisible once THIS session's later
    # close rolled its own (empty but still savepoint-scoped) transaction back -
    # measured: `focus.products` persisted correctly inside `_run_answer`, then read
    # back as the pre-turn seed the moment `_run_stages`'s outer session closed.
    db.commit()
    return _run_answer(
        turn_id=turn_id,
        ctx=ctx,
        item=item,
        branch_kind="business_query",
        actions=actions,
        answer=answer,
        state=state,
        remembered_before=remembered_before,
        dry_run=dry_run,
        session_factory=session_factory,
        turn_trace=turn_trace,
        stage=stage,
        contact_respond_id=contact_respond_id,
        verdict=verdict,
        recalled=recalled,
    )


def _answer_actions(answer: Any, *, dry_run: bool) -> list[dict[str, Any]]:
    """What the caller executes: the message, then any files, in that order."""
    built: list[dict[str, Any]] = []
    words = getattr(answer, "text", "") or ""
    if words.strip():
        built.append(
            {
                "kind": "send_message",
                **_reply_of(answer),
                "dry_run": dry_run,
            }
        )
    if answer.files:
        built.append(
            {"kind": "send_attachments", "attachments_src": answer.files, "dry_run": dry_run}
        )
    return built


def _quick_replies_of(answer: Any) -> str | None:
    """n8n's own shape: a comma-joined string or null, never a list (AC-507).

    Owner ruling 23 Sep 2026 (AC-1866): a `member_offer`'s own numbered text list is
    the offer - `turn_pending.quick_replies_suppressed` withholds ITS options from
    this string; `result_set` (the roster a numbered reply resolves through) is a
    separate read and is untouched here.
    """
    if answer.question is None:
        return None
    labels = [str(o.get("label")) for o in answer.question.options if o.get("label")]
    return turn_pending.quick_replies(getattr(answer.question, "kind", None), labels)


def _reply_of(answer: Any) -> dict[str, Any]:
    """The three fields every `Answer`-shaped reply derives the same way: the text,
    the quick replies (through `_quick_replies_of`, so a suppressed kind stays
    suppressed at every caller), and the roster a numbered follow-up resolves
    against. Shared by `_run_answer`'s persisted reply and `_answer_actions`'s own
    `send_message` action, which each add their own remaining key(s) on top
    (`attachments_src` vs `kind`/`dry_run`) - one seam, so a fix here reaches both.
    """
    return {
        "text": getattr(answer, "text", "") or None,
        "quick_replies": _quick_replies_of(answer),
        "result_set": list(answer.question.options) if answer.question is not None else [],
    }


def _pending_option_labels(pending: Any) -> list[str] | None:
    """D17: the numbered options the parser is shown, so a worded answer can resolve."""
    if pending is None or not pending.options:
        return None
    return [str(o.get("label")) for o in pending.options if o.get("label")] or None


def _apply_media_reply_prefix(result: TurnResult, outcome: Any) -> None:
    """AC-1817 to AC-1821: the "I read .../I heard ..." line, prepended exactly
    once, wherever the answering arm's reply landed.

    One wrapping step over the WHOLE result rather than a change inside each of
    `_run_stages`'s dozen answering arms (fetch, ask, roster, escalation offer) -
    every one of them already funnels its text onto `result.reply["text"]` and
    its own `send_message` action by the time this runs. `outcome` is `None` on
    a plain text turn (AC-1820) and skipped when the intake itself stopped the
    turn (AC-1810 to AC-1814 already composed their own reply with no prefix).
    """
    if outcome is None or outcome.stops_here:
        return
    prefix = media_intake.reply_prefix(outcome)
    notices = media_intake.notice_texts(outcome)
    reply = result.reply or {}
    body = reply.get("text") or ""
    lines = [prefix, *([body] if body else []), *notices]
    full_text = "\n".join(lines)
    result.reply = {**reply, "text": full_text}
    for action in result.actions or []:
        if isinstance(action, dict) and action.get("kind") == "send_message" and action.get("text") == body:
            action["text"] = full_text


def _repersist_media_prefixed_reply(
    session_factory: SessionFactory, turn_id: str, result: TurnResult, dry_run: bool
) -> None:
    """Review round S1: `_apply_media_reply_prefix` mutates the in-memory `TurnResult`
    handed back to the caller, but by the time it runs, the answering arm's own tail
    (`_run_answer`/`_run_entities_only_arm`/etc, plural rather than one shared
    `_finish_turn`) already persisted the row's `response` and its trace's `sent`/
    `replied` record with the UN-prefixed text - so `chatbot.turns.response` and a
    `_duplicate_result` replay disagreed with the words the customer actually got.
    Patched back onto the row here, in place, on its own short session - a no-op
    whenever `_apply_media_reply_prefix` itself was a no-op (a plain turn, or a
    media-denied turn that never reaches an answering arm at all).
    """
    if dry_run:
        return
    new_text = (result.reply or {}).get("text")
    db = session_factory()
    try:
        row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
        if row is None or not isinstance(row.response, dict):
            return
        response = row.response
        reply = response.get("reply") if isinstance(response.get("reply"), dict) else {}
        if reply.get("text") == new_text:
            return  # nothing to fix - either no prefix applied, or already persisted right
        response = {**response, "reply": {**reply, "text": new_text}}
        if isinstance(response.get("actions"), list):
            response["actions"] = result.actions
        row.response = response
        trace = [dict(r) if isinstance(r, dict) else r for r in (row.trace or [])]
        for record in trace:
            if not isinstance(record, dict) or record.get("stage") not in ("sent", "replied"):
                continue
            raw = record.get("raw")
            if not isinstance(raw, dict):
                continue
            if isinstance(raw.get("reply"), dict):
                raw["reply"] = {**raw["reply"], "text": new_text}
            if isinstance(raw.get("actions"), list):
                raw["actions"] = result.actions
        row.trace = trace
        db.commit()
    finally:
        db.close()


def _media_source_modality(media_box: dict[str, Any]) -> str | None:
    """This turn's media source, whichever of the two intake paths produced it - a
    live outcome this module ran itself, or a modality n8n's own pipeline already
    decided upstream (`patched_upstream`, review round S3). `None` on a plain text
    turn. Used for `from_photo` (the entities-only arm's wording), which must read
    the same either way."""
    outcome = media_box.get("outcome")
    if outcome is not None:
        return outcome.modality
    return media_box.get("patched_modality")


def _attributes_summary(attributes: list[Any]) -> str | None:
    """"2 quantity, 1 size" - one count per `MediaAttribute.kind` (review round S5).
    `None` on an empty list, so a text turn's facts carry no `attributes` key at
    all rather than a printed empty string."""
    from collections import Counter

    counts = Counter(
        a.get("kind") or "attribute" for a in attributes if isinstance(a, dict)
    )
    if not counts:
        return None
    return ", ".join(f"{n} {kind}" for kind, n in counts.items())


def _media_intake_facts(outcome: media_intake.MediaIntakeOutcome) -> dict[str, Any]:
    """The `media_intake` trace record's facts (AC-1800/AC-1801, AC-1836's own
    `attachment_error`) - FLATTENED to exactly what `TurnPanel`'s generic StageRow
    prints (review round S5): every value here is a plain string/number/bool the
    component renders with `String(value)` - never a nested object (which would
    print as "[object Object]") and never a bare id (no UUIDs on screen, the
    cursor rule) - `job_id`/`attachment_id`/the full `result` live in the trace
    record's `raw` instead (never printed; see the `turn_trace.record` call site).
    """
    entities = [e for e in (outcome.result.get("entities") or []) if isinstance(e, dict)]
    raws = [e.get("raw") for e in entities if e.get("raw")]
    facts: dict[str, Any] = {
        "modality": outcome.modality,
        "decision": outcome.decision,
        "status": outcome.status,
        "elapsed_ms": outcome.elapsed_ms,
    }
    if raws:
        facts["entities"] = ", ".join(raws)
    attributes_summary = _attributes_summary(outcome.result.get("attributes") or [])
    if attributes_summary:
        facts["attributes"] = attributes_summary
    if outcome.result.get("notes"):
        facts["notes"] = outcome.result["notes"]
    if outcome.result.get("truncated"):
        facts["truncated"] = True
    if outcome.attachment_error:
        facts["attachment_error"] = outcome.attachment_error
    if outcome.extraction_error:
        facts["extraction_error"] = outcome.extraction_error
    return facts


def _stock_check_denied(db: Session, envelope: Envelope, verdict: dict[str, Any]) -> bool:
    """Contract 61: the contact is not allowed stock and this turn asked for it.

    Read off the CONTACT's own record, which is why it cannot be a plan fact: the plan
    knows what was asked, not who is asking. S6 (owner ruling, 16 Sep 2026): the record
    is the CRM's `respond_contacts.chatbot_stock_allowed`, default ON, read through
    `turn_runtime.load_profile` - the one seam for the contact's facts. The envelope's
    respond.io custom field (formerly `is_allowed_stock`) is not read at all: a console
    turn that borrowed an envelope with an empty `custom_fields` read it as "not
    allowed" and answered a stock ask with the demand-quantity question. No row at all
    fails open, like the profile.
    """
    profile, _recall = turn_runtime.load_profile(db, _contact_respond_id(envelope))
    return (
        profile.stock_allowed is not True
        and verdict.get("intent_hint") == "check_stock"
        and not jsc.is_empty(verdict.get("entities"))
    )


def _demand_qty_missing(verdict: dict[str, Any]) -> bool:
    """Contract 62: a denied stock check with no quantity asks for one first."""
    return jsc.is_empty(verdict.get("demand_qty")) or verdict.get("demand_qty") == 0


def _record_memory_trace(
    db: Session,
    turn_trace: Any,
    *,
    contact_respond_id: str,
    before: dict[str, Any],
    state: Any,
    answer: Any,
    recalled: list[dict[str, Any]],
    dry_run: bool,
    written: bool,
    facts_saved: list[dict[str, Any]] | None = None,
    record_remembered: bool = True,
) -> None:
    """The `memory` trace record (chatbot memory lane A, contract section 6): the
    contact's context level, the three shelves before/after with their writer, what
    THIS turn closed (`episodes.written`, stashed on `before` by the topic-reset write
    a few call frames back) and what it fed the parser (`episodes.read`), and the
    facts it saved this turn (tallied after a reset, stated from the tail, or both -
    the caller has already applied both writers by the time this runs)."""
    from app.models.access import RespondContact
    from app.models.user import SystemSetting
    from app.services.chatbot.turn.pending import to_wire
    from app.services.chatbot.turn.state import focus_to_wire

    # The contact intake resolved (workspace-scoped, by primary key), never a second
    # `respond_io_id` lookup; the profile BEFORE is intake's snapshot, the AFTER is
    # read now, once this turn's writes are in (reviewer pass at d89110c0, S22).
    contact_pk = before.get("_contact_pk")
    contact_row = (
        db.query(RespondContact).filter(RespondContact.id == contact_pk).first()
        if contact_pk is not None
        else None
    )
    own_level = contact_row.chatbot_memory_level if contact_row is not None else None
    system_memory = db.query(SystemSetting.chatbot_memory).scalar()
    effective = memory_mod.resolve_level(own_level, system_memory)
    profile_after = _profile_snapshot(contact_row)
    if profile_after is not None and state is not None:
        profile_after = {**profile_after, "tier": state.profile.tier, "language": state.profile.language}

    turn_trace.add(
        "memory",
        {
            "level": {"own": own_level, "effective": effective},
            "focus": {
                "before": before.get("focus") or {},
                "after": focus_to_wire(state.focus) if state is not None else None,
                "writer": "apply",
            },
            "profile": {
                "before": before.get("_profile_before"),
                "after": profile_after,
                "writer": "contact",
            },
            "episodes": {
                "read": [f.get("id") for f in recalled],
                "written": before.get("_episode_written"),
                "writer": "tail",
            },
            "facts_saved": list(facts_saved or []),
            "open_question": {
                "before": before.get("open_question"),
                "after": to_wire(answer.question) if answer is not None else None,
                "writer": "apply",
            },
            "written": written,
            "dry_run": dry_run,
        },
    )
    if not record_remembered:
        # The casual and escalation arms hand the session write to `complete_turn`,
        # whose own tail records `remembered`.
        return
    turn_trace.record(
        "remembered",
        summary=(
            "Nothing was written: this is a test turn (D14)."
            if dry_run
            else "Wrote the conversation state."
        ),
        why="The CRM is the only writer of the conversation state on the turn path (D2).",
        facts={"written": written, "dry_run": dry_run},
        raw=None,
    )


def _remember_on_delegated_arm(
    db: Session,
    turn_trace: Any,
    *,
    turn_id: str,
    verdict: dict[str, Any] | None,
    remembered_before: dict[str, Any] | None,
    contact_respond_id: str | None,
    recalled: list[dict[str, Any]] | None,
    state: Any,
    dry_run: bool,
) -> None:
    """The memory half of `_run_answer`'s tail for the two arms that hand the session
    write to `complete_turn` (casual, escalation): apply the verdict's statements and
    record the `memory` event, so a statement-only turn is remembered and traced like
    any other (reviewer pass at d89110c0, B1 and S22)."""
    if verdict is None or remembered_before is None or contact_respond_id is None:
        return
    facts_saved = _apply_profile_statements(
        db,
        turn_trace,
        verdict=verdict,
        remembered_before=remembered_before,
        contact_respond_id=contact_respond_id,
        turn_id=turn_id,
        dry_run=dry_run,
    )
    _record_memory_trace(
        db,
        turn_trace,
        contact_respond_id=contact_respond_id,
        before=remembered_before,
        state=state,
        answer=None,
        recalled=list(recalled or []),
        dry_run=dry_run,
        written=not dry_run,
        facts_saved=facts_saved,
        record_remembered=False,
    )


def _run_casual_lane(
    *,
    turn_id: str,
    ctx: dict[str, Any],
    item: dict[str, Any],
    actions: list[dict[str, Any]],
    dry_run: bool,
    session_factory: SessionFactory,
    turn_trace: Any,
    stage: list[str],
    clarifier_prompt: dict[str, Any] | None,
    clarifier_config: Any,
    setup_error: str | None = None,
    state: Any = None,
    verdict: dict[str, Any] | None = None,
    remembered_before: dict[str, Any] | None = None,
    contact_respond_id: str | None = None,
    recalled: list[dict[str, Any]] | None = None,
    fallback: Any = None,
    chat_console: bool = False,
) -> TurnResult:
    """The `low_signal` lane, from the model call to the closed turn (AC-401, AC-403).

    `fallback` (S4, plan 7.2) is the graceful fallback's context, read off the database
    before this runs: the clarifier writes the ack only, and the reply is `ack +
    memory_line + offer`. A clarifier that still answers in the older `{"response"}`
    shape is the whole reply, as before. A history question whose clarifier failed
    still gets its list, behind the canned ack: the list needs no model.

    Split out of `_run_stages` so the "no DB session across LLM I/O" rule is visible in the
    signature rather than in a comment: this function takes a `session_factory`, never a
    `Session`, and opens one only after the provider has answered.

    `ClarifierError` is caught HERE and not by `run_turn`'s outer handler. Routing already
    succeeded, so the turn keeps `branch_kind = "low_signal"` and fails at
    `stage = "casual_llm"`; the outer handler would null the branch kind and send the
    generic parser-error reply, which is a different lane's text (AC-403).
    """
    stage[0] = "casual_llm"
    user_message = (
        casual.render_user_message(clarifier_prompt) if clarifier_prompt is not None else ""
    )
    if fallback is not None and user_message:
        user_message = f"{user_message}\n{fallback_mod.clarifier_tail(fallback)}"

    # -- NO DB SESSION IS OPEN HERE ---------------------------------------- #
    # Every failure string here is TYPE-PREFIXED, and every test against it is
    # `is not None`. `str(exc)` alone is EMPTY for `ValueError("")` or a bare
    # `ClarifierError()`, and an empty string is falsy: `if failed` would read the turn as
    # a success, close the row `done`, and leave `error` as "" - a turn that failed,
    # recorded as fine, with nothing on the trace screen to say otherwise.
    failed: str | None = setup_error
    answer_shape: str | None = None
    ack_replaced = False
    reply_language: str | None = None
    if failed is not None:
        # SETUP failure (the resolver, the registry, the AI config, the API key). The
        # customer gets a FIXED sentence, never `str(exc)`: these messages carry provider
        # detail and configuration names, and none of that belongs in a WhatsApp reply.
        # The real reason is on the row and on the trace, which is where an operator looks.
        text = casual.CLARIFIER_UNAVAILABLE_REPLY
    else:
        try:
            raw = casual.call_clarifier(clarifier_config, user_message)
            said = fallback_mod.read_clarifier(casual.central_exchange({"text": raw}))
            if said is None:
                raise casual.ClarifierAnswerEmpty("the clarifier returned nothing to say")
            answer_shape = said.shape
            text = said.text
            if fallback is not None and (said.shape == "ack" or fallback.kind == "history"):
                noted_language = next(
                    (s.get("value") for s in fallback.noted if s.get("key") == "language"), None
                )
                reply_language = fallback_mod.pick_language(
                    fallback.saved_language, noted_language, said.language
                )
                ack = said.text.strip()
                # AC-MEM081: an ack stating a figure, code, price or date its own
                # input never had is replaced by the canned one for the language.
                if not fallback_mod.ack_is_safe(ack, user_message):
                    ack = fallback.copy.render_in("fallback_ack", reply_language)
                    ack_replaced = True
                text = fallback_mod.compose(ack, fallback, fallback.copy, reply_language)
        except casual.ClarifierRateLimited as exc:
            # PR #1247 round 6, ruling 3: a rate limit that never cleared is one plain
            # sentence, never the provider's text. The row keeps the real reason.
            failed = f"{type(exc).__name__}: {exc}"
            text = llm_call.RATE_LIMITED_REPLY
        except Exception as exc:  # noqa: BLE001 - a provider error or an unreadable answer
            # AC-MEM086 (owner ruling Q10): the dealer never reads exception text. The
            # row and the trace keep the real reason; the dealer gets the turn's own
            # apology. n8n's `sub-error-logger` interpolation is retired here.
            failed = f"{type(exc).__name__}: {exc}"
            text = GENERIC_ERROR_REPLY
    if failed is not None and fallback is not None and fallback.kind == "history" and fallback.copy is not None:
        # The history list needs no model: behind the canned ack it still answers.
        reply_language = fallback_mod.pick_language(fallback.saved_language)
        text = fallback_mod.compose(
            fallback.copy.render_in("fallback_ack", reply_language), fallback, fallback.copy, reply_language
        )
        answer_shape = "canned"
        failed = None
    if turn_state.escalation_barred(getattr(state, "profile", None)):
        # ESCALATION-CONTROL: this lane's send action is built HERE, before the tail runs,
        # so the backstop runs here too (a clarifier that wrote an escalate sentence).
        from app.services.chatbot import escalation_control

        stripped, _question, offered = escalation_control.strip_text(text, None, state.profile)
        if offered:
            text = stripped
    actions = [
        *actions,
        # AC-507: `quick_replies` is n8n's comma-joined string or null, never a list -
        # this lane offers none, so it is null, matching every other hand-built action.
        {"kind": "send_message", "text": text, "quick_replies": None, "dry_run": dry_run},
    ]

    # The clarifier IS this lane's lookup: it is where the turn's answer comes from, the
    # way `sub-answer` is for the business lane. Recorded at `looked_up` rather than
    # `replied` so it does not collide with the `replied` record the tail writes below.
    turn_trace.record(
        "looked_up",
        status="failed" if failed is not None else "ok",
        summary=(
            _casual_failure_summary(failed, setup_error)
            if failed is not None
            else "The history question was answered from memory."
            if fallback is not None and fallback.kind == "history"
            else "The clarifier wrote the acknowledgement; the reply adds what memory holds and an offer."
            if answer_shape == "ack"
            else "The clarifier wrote small talk or one clarifying question."
        ),
        why=(
            "A history question with memory on is answered from the contact's open and "
            "closed conversations."
            if fallback is not None and fallback.kind == "history"
            else "The turn carried no business question to look up, so the clarifier writes "
            "the reply."
        ),
        facts={
            "message_type": (clarifier_prompt or {}).get("message_type"),
            "model": getattr(clarifier_config, "model", None),
            "prompt_version": getattr(clarifier_config, "prompt_version", None),
            "resolved_entities": len((clarifier_prompt or {}).get("entities") or []),
            # S4: which shape answered, the fallback kind and level, the reply's
            # language, and whether the guard replaced the ack (AC-MEM081).
            "answer_shape": answer_shape,
            "fallback_kind": getattr(fallback, "kind", None),
            "memory_level": getattr(fallback, "level", None),
            "language": reply_language,
            "ack_replaced": ack_replaced,
        },
        error=failed,
        raw={"user_prompt": user_message},
    )

    if failed is not None:
        # AC-403: a failed clarifier is a FAILED turn, and the tail does not run. No
        # session is written (the customer's memory must not record an answer that was
        # never composed), and `branch_kind` stays `low_signal` because routing succeeded.
        # AC-507: null, not `[]` - the same string-or-null contract every other reply
        # carries (a failed clarifier never composed one).
        reply = {"text": text, "quick_replies": None}
        with _session(session_factory) as db:
            _close_turn(
                db,
                turn_id,
                status="failed",
                stage="casual_llm",
                branch_kind="low_signal",
                error=failed,
                records=turn_trace.persisted(),
                response={"ctx": ctx, "item": item, "actions": actions, "reply": reply},
            )
        return TurnResult(
            turn_id=turn_id,
            ctx=ctx,
            item=item,
            branch_kind="low_signal",
            delegate=None,
            reply=reply,
            actions=actions,
            session_patch=None,
            status="failed",
            stage="casual_llm",
        )

    # -- the tail, exactly the one every other lane will use ---------------- #
    # The row is closed `delegated` at `routed` FIRST, which is not bookkeeping: it is the
    # state the turn is genuinely in (a lane produced a result and the tail has not folded
    # it in yet), it is the state `complete_turn` refuses to run without, and it is what
    # the trace screen should show if this process dies between the two. It also puts the
    # `send_message` action on the row before the tail reads `prior_actions` off it, so a
    # duplicate delivery replays the action as well as the reply (D15).
    #
    # `answer` is `sub-answer`'s own return on this arm, reproduced exactly: its
    # `answer-result` node emits `{...central-exchange's output, outcome_fragment}`, and
    # the ITEM that reaches `sub-output` is that object - NOT `route-turn`'s item.
    #
    # The difference decides the reply. `build-outcome` reads `central-exchange` out of
    # `item.outcome_fragment`, and `complete_turn`'s entry gate runs `escalate-catalog`
    # only when the item carries a `branch_kind`. `sub-answer`'s output has none, so the
    # catalog is skipped and the compile-state ladder falls through to `central-exchange`.
    # Hand it `route-turn`'s item instead and the catalog runs, produces an empty
    # `response` for a kind it has no case for, and wins the ladder - the reply comes out
    # blank. The turn ROW keeps `low_signal` either way; `branch_kind` is read off the row.
    central = {"response": text}
    answer = {
        **central,
        "outcome_fragment": {
            "central-exchange": central,
            "build-miss-member-offer": None,
            "dym-annotate-partial": None,
        },
    }

    with _session(session_factory) as db:
        _remember_on_delegated_arm(
            db,
            turn_trace,
            turn_id=turn_id,
            verdict=verdict,
            remembered_before=remembered_before,
            contact_respond_id=contact_respond_id,
            recalled=recalled,
            state=state,
            dry_run=dry_run,
        )
        _close_turn(
            db,
            turn_id,
            status="delegated",
            stage="routed",
            branch_kind="low_signal",
            error=None,
            records=turn_trace.persisted(),
            response={"ctx": ctx, "item": item, "actions": actions},
        )

    completed = complete_turn(
        turn_id,
        {"item": answer, "ctx": ctx, "answer": answer},
        session_factory=session_factory,
        # The clarifier asks nothing of its own, so whatever question was open before
        # this greeting is still open after it (contract 36 / 56, cluster 4's carry).
        state=state,
        chat_console=chat_console,
    )

    return TurnResult(
        turn_id=turn_id,
        ctx=ctx,
        item=item,
        branch_kind="low_signal",
        delegate=None,
        reply=completed.reply,
        actions=completed.actions,
        # D14: on a dry run the tail wrote nothing and hands back what it WOULD have
        # written, so a console turn can be inspected. On a live turn it is already saved.
        session_patch=completed.session_patch,
        status=completed.status,
        stage=completed.stage,
    )


def _with_handover_context(
    actions: list[dict[str, Any]], handover: dict[str, Any] | None, ctx: dict[str, Any]
) -> list[dict[str, Any]]:
    """S4 (AC-MEM087/088) over the escalation lane's own actions, which it leaves in
    place: routing, the assignment, the SLA row and the comment's own lines are the
    lane's. What this adds:

    * the comment gains `Salesperson: <name>` for a commercial ask and `Conversation so
      far: <summary>` when the live conversation named something, so the person picking
      it up sees who looks after the account and what was being discussed;
    * for a commercial ask with a linked salesperson, the dealer's "routed to the
      respective person-in-charge" line (the send after the comment) names the
      salesperson instead.
    """
    if not handover:
        return actions
    salesperson = handover.get("salesperson")
    summary = handover.get("summary")
    if not (salesperson or summary):
        return actions
    extra = []
    if salesperson:
        extra.append(f"Salesperson: {salesperson}")
    if summary:
        extra.append(f"Conversation so far: {summary}")
    out: list[dict[str, Any]] = []
    seen_comment = False
    team = None
    for action in actions:
        if action.get("kind") == "add_comment" and not seen_comment:
            seen_comment = True
            body = str(action.get("text") or "")
            first = body.split("\n", 1)[0]
            if first.startswith("Team: "):
                team = first[len("Team: ") :].strip()
            out.append({**action, "text": "\n".join([body, *extra])})
            continue
        if salesperson and seen_comment and action.get("kind") == "send_message" and team:
            from app.services.chatbot import copy as copy_mod
            from app.services.chatbot.tail.outcome import pretty_team

            line = (handover.get("copy") or copy_mod.fallback_copy()).render_in(
                "handover_salesperson", handover.get("language"), salesperson=salesperson, team=pretty_team(team)
            )
            out.append({**action, "text": line})
            salesperson = None  # once
            continue
        out.append(action)
    return out


def _run_escalation_arm(
    *,
    turn_id: str,
    ctx: dict[str, Any],
    item: dict[str, Any],
    actions: list[dict[str, Any]],
    dry_run: bool,
    session_factory: SessionFactory,
    turn_trace: Any,
    stage: list[str],
    state: Any = None,
    verdict: dict[str, Any] | None = None,
    remembered_before: dict[str, Any] | None = None,
    contact_respond_id: str | None = None,
    recalled: list[dict[str, Any]] | None = None,
) -> TurnResult:
    """The `out_of_scope` lane, from the lane call to the closed turn (AC-501 to AC-505).

    `run_escalation_lane` is a module-level name so a test can replace it; the real one is
    `lanes.escalation.run`, whose `services` default builds the production bundle. The lane
    owns its own unit of work: the round-robin cursor and the SLA row must not roll back
    with the turn's routing transaction, because a person has already been told.

    A lane failure is a FAILED turn at `looked_up` with today's generic reply and NO
    partial assignment - the lane returns its whole action list or raises before returning
    any of it, so "assigned but no SLA row" is not a state this can produce.
    """
    stage[0] = "looked_up"
    # #865: the product this escalation is about, from THIS turn's applied focus (this
    # turn's named product, else the one the previous turn left in focus; a topic reset
    # or a newer product already replaced it). The lane reads its brand off the product
    # row. On the lane's item only: the row keeps the route-turn item it always had.
    lane_item = {**item, "focus_products": _focus_products(state)}
    try:
        # The lane opens its OWN session (its writes are a unit of work of their own), and
        # it opens it off THIS factory rather than `SessionLocal`, so the contact's company
        # scope travels into it (H56). Defence in depth: `post_next_assignee` pins its own
        # scope before it reads `Team` / `AgentTeam`, so the draw was not failing; the
        # pre-pin reads and the lane's unit of work were the unscoped half.
        fragment = run_escalation_lane(
            ctx, lane_item, dry_run=dry_run, session_factory=session_factory
        )
    except Exception as exc:  # noqa: BLE001 - a failed lane is recorded, never dropped
        message = f"{type(exc).__name__}: {exc}"
        logger.exception("chatbot turn %s: escalation lane failed", turn_id)
        turn_trace.record(
            "looked_up",
            status="failed",
            summary="Could not hand the conversation to a person.",
            why="The assignment the escalation lane depends on did not complete.",
            facts={"lane": "out_of_scope", "dry_run": dry_run},
            error=message,
            raw=None,
        )
        with _session(session_factory) as db:
            _close_turn(
                db,
                turn_id,
                status="failed",
                stage="looked_up",
                branch_kind="out_of_scope",
                error=message,
                records=turn_trace.persisted(),
                response={"ctx": ctx, "item": item, "actions": actions},
            )
        return _failed_result(turn_id, "looked_up", message, actions, dry_run)

    arm = fragment.get("arm")
    clarify = fragment.get("clarify")
    lane_actions = _with_handover_context(
        list(fragment.get("actions") or []), (remembered_before or {}).get("_handover"), ctx
    )
    pending = fragment.get("pending")

    # Only `looked_up` is recorded here. `replied` and `remembered` are the TAIL's, and
    # recording a `replied` of our own would put two of them on the trace.
    routed_to = escalation_routing_line(fragment.get("routing"))
    turn_trace.record(
        "looked_up",
        summary=(
            "Asked which product the escalation is about."
            if arm == "clarify" and jsc.truthy(jsc.get(clarify, "clarify_product"))
            else "Asked which company should take it."
            if arm == "clarify"
            else " ".join(filter(None, ("Handed the conversation to a person.", routed_to)))
        ),
        why=(
            "More than one company was offered and nobody picked one, so assigning would "
            "have round-robined a pool the customer never chose."
            if arm == "clarify"
            else "The turn asked for a human, so the lane assigns one and starts the SLA clock."
        ),
        facts={
            "lane": "out_of_scope",
            "arm": arm,
            "actions": [a.get("kind") for a in lane_actions],
            "dry_run": dry_run,
            # #865 observability: the next-assignee body's routing axes, the rung that
            # chose the brand, and the round-robin cursor key the draw used. Before this
            # the brand a lane sent was visible only by joining the SLA row by time.
            "routing": fragment.get("routing"),
        },
        raw={"clarify": clarify, "pending": pending},
    )

    # -- the tail, the same one every completed lane runs -------------------- #
    # n8n sends this arm through `tag-out-of-scope` -> `sub-output`, so the session IS
    # written today: the routing axes, and `escalate-catalog`'s `includeResponse: false`
    # state text ("Informed the user that request is out of scope..."). Skipping the tail
    # would have quietly dropped both the moment the lane was switched on.
    #
    # The item handed over is `tag-out-of-scope`'s, `{branch_kind: "out_of_scope"}` and
    # nothing else - NOT `route-turn`'s item. That is what makes `complete_turn`'s entry
    # gate run `escalate-catalog` for this arm, which is where the acknowledgement text
    # comes from. It stays a tail concern and is deliberately not one of the actions.
    #
    # The row is closed `delegated` at `routed` first: it is the state the turn is really
    # in (a lane produced a result, the tail has not folded it in), it is what
    # `complete_turn` refuses to run without, and it puts the actions on the row before the
    # tail reads `prior_actions` off it, so a duplicate replays them too (D15).
    all_actions = [*actions, *lane_actions]
    with _session(session_factory) as db:
        _remember_on_delegated_arm(
            db,
            turn_trace,
            turn_id=turn_id,
            verdict=verdict,
            remembered_before=remembered_before,
            contact_respond_id=contact_respond_id,
            recalled=recalled,
            state=state,
            dry_run=dry_run,
        )
        _close_turn(
            db,
            turn_id,
            status="delegated",
            stage="routed",
            branch_kind="out_of_scope",
            error=None,
            records=turn_trace.persisted(),
            response={"ctx": ctx, "item": item, "actions": all_actions, "pending": pending},
        )

    completed = complete_turn(
        turn_id,
        {
            "item": {"branch_kind": "out_of_scope"},
            "ctx": ctx,
            "clarify": clarify,
            # What the LANE already decided the customer can tap. The tail composes no
            # quick reply on this arm, so without this the reply it persists disagrees
            # with the action it hands the executor.
            "lane_quick_replies": next(
                (
                    a["quick_replies"]
                    for a in all_actions
                    if a.get("kind") == "send_message" and jsc.truthy(a.get("quick_replies"))
                ),
                None,
            ),
        },
        session_factory=session_factory,
        # An accepted offer was CONSUMED by APPLY, so this carries nothing; a clarify
        # arm asks its own question and `_question_offered` reads it back.
        state=state,
    )

    # -- seal the send actions with what the tail composed -------------------- #
    # `quick_replies` and `result_set` are the SEALED reply's, and they do not exist until
    # the tail has run - the lane declares the keys empty and they are filled here, so the
    # executor sees one `send_message` shape whoever built it. `attachments_src` is the
    # same story one step further: when the tail produced one, the send_attachments action
    # goes LAST, after both messages, which is the order `send-attachments` runs in today.
    sealed = completed.reply or {}
    final_actions = [_seal_send(a, sealed) for a in (completed.actions or [])]
    if jsc.truthy(sealed.get("attachments_src")):
        final_actions.append(
            {
                "kind": "send_attachments",
                "attachments_src": sealed.get("attachments_src"),
                "reply": sealed,
                "dry_run": dry_run,
            }
        )
    if final_actions != (completed.actions or []):
        # The row has to carry what the caller was handed, or a duplicate delivery replays
        # a different action list than the first turn produced (D15).
        with _session(session_factory) as db:
            row = db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()
            if row is not None and isinstance(row.response, dict):
                row.response = {**row.response, "actions": final_actions}
                db.commit()

    return TurnResult(
        turn_id=turn_id,
        ctx=ctx,
        item=item,
        branch_kind="out_of_scope",
        delegate=None,
        reply=completed.reply,
        actions=final_actions,
        # D14: on a dry run the tail wrote nothing and hands back what it WOULD have
        # written; on a live turn it is already saved.
        session_patch=completed.session_patch,
        status=completed.status,
        stage=completed.stage,
    )


def _seal_send(action: dict[str, Any], sealed: dict[str, Any]) -> dict[str, Any]:
    """Fill a `send_message`'s sealed halves from the tail's reply. Others pass through."""
    if action.get("kind") != "send_message":
        return action
    # AC-507/D9: n8n's `quick_reply` is a comma-joined STRING or null, never a list -
    # `or []` here would hand the sender a type its `quick_reply` input has never taken.
    # The sealed value wins when the tail composed one, and only then: a lane that built
    # its OWN quick replies (the escalation clarifies name the teams so the answer is a
    # tap) must not have them erased by a turn whose tail composed none. Same rule for
    # `result_set`, for the same reason.
    sealed_replies = sealed.get("quick_replies")
    sealed_set = sealed.get("result_set")
    return {
        **action,
        "quick_replies": sealed_replies if sealed_replies else action.get("quick_replies"),
        "result_set": sealed_set if sealed_set else action.get("result_set"),
    }


def _casual_failure_summary(failed: str, setup_error: str | None) -> str:
    """One sentence for the trace screen, in the operator's words not the provider's.

    "Could not reach the clarifier" is wrong for the commonest setup failure by far - a
    missing API key or an unset AI-assistant config never reached anything, and telling an
    operator the model was unreachable sends them to look at the wrong system.
    """
    if setup_error is None:
        return "Could not reach the clarifier."
    lowered = failed.lower()
    if "api key" in lowered:
        return "The clarifier is not configured: no API key for its provider."
    if "configuration is not set" in lowered:
        return "The clarifier is not configured: the AI assistant settings are empty."
    return "Could not prepare the clarifier call."


def _focus_products(state: Any) -> list[dict[str, Any]]:
    """The product entries of a turn's applied focus, or `[]` (#865)."""
    focus = getattr(state, "focus", None)
    products = getattr(focus, "products", None)
    return [p for p in products if isinstance(p, dict)] if isinstance(products, list) else []


def _focus_brand(db: Session, focus: Any) -> str | None:
    """The brand of the focus product, off the product row, or None (#865).

    `escalation_services.focus_product_brand` is the one read, shared with the escalation
    lane's `product_brand` seam so the offer and the draw cannot disagree about a brand.
    Fails soft: a brand nobody could read leaves the offer exactly as it was before.
    """
    products = getattr(focus, "products", None)
    if not products:
        return None
    from app.services.chatbot.lanes.escalation_services import focus_product_brand

    try:
        return focus_product_brand(db, products)
    except Exception:  # noqa: BLE001 - a missing brand is not a failed turn
        logger.warning("chatbot: the focus product's brand could not be read", exc_info=True)
        return None


def _focus_brand_payload(db: Session, focus: Any) -> dict[str, Any]:
    """A resolver-shaped payload carrying only the focus product's brand, or `{}` (#865).

    For a turn whose resolver never ran: the bridge's miss arm reads the brand off
    `payload["gate"]["routing_brand"]` like every other mint site, and a gate holding
    nothing else reads as `{}` for every other key.
    """
    brand = _focus_brand(db, focus)
    if not brand:
        return {}
    return {"gate": {"routing_brand": brand, "routing_brand_source": "focus_product"}}


def _stamp_item(access: dict, branch_kind: str, tier_stamp: dict) -> dict[str, Any]:
    """`route-turn`'s output item, byte-equal to today (AC-101).

    `stock_denied` also carries `not_allowed_check_stock: true` - n8n stamps it with a
    one-field `Edit Fields2` Set between the route Switch and `tag-entry-resolve`, and
    `sub-main-processing` reads it by that exact name. The CRM stamps it here rather than
    keeping a node-shaped Set of its own, so the item `sub-main-processing` receives is
    unchanged while that lane still delegates (S6 owns it).
    """
    from app.services.chatbot.contracts import TAG_ONLY_BRANCH_KINDS

    if branch_kind in TAG_ONLY_BRANCH_KINDS:
        return {"branch_kind": branch_kind}
    item = {**access, "branch_kind": branch_kind, **tier_stamp}
    if branch_kind == "stock_denied":
        item["not_allowed_check_stock"] = True
    return item


def _failed_result(
    turn_id: str,
    stage: str,
    error: str,
    actions: list[dict[str, Any]],
    dry_run: bool,
    *,
    ctx: dict[str, Any] | None = None,
    item: dict[str, Any] | None = None,
    branch_kind: str | None = None,
    reply_text: str = GENERIC_ERROR_REPLY,
) -> TurnResult:
    """A failed turn still hands the caller today's error reply to send (AC-105, AC-107).

    `reply_text` is that reply unless the caller knows better: a parser call refused by
    a rate limit on every attempt says `llm_call.RATE_LIMITED_REPLY` (PR #1247 round 6).

    `quick_replies` is null, never `[]`: AC-507's contract is `quick_reply` is n8n's
    comma-joined string or null, and a failed turn offered none.

    `ctx` / `item` / `branch_kind` default to None because most failures happen before
    they exist. A LANE failure has all three, and keeps them: the turn was routed, so
    nulling them would lose where it got to (S4's rule for the clarifier, and what D15's
    duplicate replay hands n8n's by-name re-emitters).
    """
    return TurnResult(
        turn_id=turn_id,
        is_test=dry_run,
        ctx=ctx,
        item=item,
        branch_kind=branch_kind,
        delegate=None,
        reply={"text": reply_text, "quick_replies": None},
        actions=[
            *actions,
            {
                "kind": "send_message",
                "text": reply_text,
                "quick_replies": None,
                "dry_run": dry_run,
            },
        ],
        status="failed",
        stage=stage,
        error=error,
    )


def _s7_mode(db: Session, row: Any = _UNSET) -> bool:
    """`system_settings.chatbot_ordering_enabled` - the CRM owns the whole turn.

    Ordering and tail-ownership are the same promote (the thin spine posts every message to
    `/turn` and the CRM answers it), so they are one switch; `app/api/v1/external/chat.py`
    reads the same column for the other half, the 410 on `/complete`. Its precondition is
    on the settings screen next to it: every lane the owner has switched on has to be one
    this build can complete before this goes on.

    A settings COLUMN since AC-810, read per turn off the row the turn has already read
    (`row`), because the owner flips it while watching live turns and an environment
    variable makes that a deploy. Same `db`-first signature as `_stock_denial_enabled`
    below, for the same reason: a caller with no row in hand still gets a correct answer.
    """
    if row is _UNSET:
        row = _settings_row(db)
    return bool(getattr(row, "chatbot_ordering_enabled", False)) if row is not None else False


def _business_lane_enabled(db: Session, row: Any = _UNSET) -> bool:
    """`system_settings.chatbot_business_lane_enabled`, default FALSE.

    Off, the head behaves exactly as it did in S1: the three business arms delegate by
    name and carry no payload. On, they run the ported `sub-resolve-and-gate` in process
    and hand n8n its output item. It stays independent of `chatbot_completed_lanes` (which
    says an arm may ANSWER) so deploy, compare, switch on and cut n8n remain four
    separately reversible steps.
    """
    if row is _UNSET:
        row = _settings_row(db)
    return (
        bool(getattr(row, "chatbot_business_lane_enabled", False)) if row is not None else False
    )


def _settings_row(db: Session) -> Any:
    """The `system_settings` singleton, read ONCE per turn.

    Every chatbot switch lives on it, and each predicate below takes the ROW rather than a
    session so the turn makes one query for all of them instead of one each.
    """
    from app.models.user import SystemSetting

    return db.query(SystemSetting).first()


@dataclass(frozen=True)
class _TurnSwitches:
    """The settings values one turn reads, snapshotted off the row at the first session.

    A SNAPSHOT rather than the ORM row, because the turn needs S7 mode before the ticket
    (in the dedup session) and the other four while routing (in a later one), and the row
    would be detached by then. Field names match the columns on purpose: every predicate
    below reads them with `getattr`, so the snapshot and the row are interchangeable and a
    caller with only a session still gets a correct answer.

    Read once means the switches cannot change halfway through a turn, which is the more
    important half: a turn that ordered itself under S7 mode and then routed as though it
    were off would have taken a ticket nobody releases.
    """

    chatbot_stock_denial_enabled: bool = False
    chatbot_unsupported_domains: Any = None
    chatbot_completed_lanes: Any = None
    chatbot_business_lane_enabled: bool = False
    chatbot_ordering_enabled: bool = False
    # A7 (chatbot-growth-r1). MISSING until 8 Sep 2026, and that single omission is why
    # Foundre's rule never fired for a customer: `_crossdomain_ladder` reads this snapshot,
    # `getattr` found no attribute, returned None, and `_next_crossdomain_rung` reads None
    # as "no ladder configured = the pre-A7 single probe". Every unit test passed the
    # ladder in by hand, so nothing saw it. This is the "a new DB column must reach every
    # manual builder" lesson one builder further along than the two it usually names.
    chatbot_crossdomain_ladder: Any = None


def _read_switches(db: Session) -> _TurnSwitches:
    """One query for every switch the turn will consult. No row = every default."""
    row = _settings_row(db)
    if row is None:
        return _TurnSwitches()
    return _TurnSwitches(
        chatbot_stock_denial_enabled=bool(
            getattr(row, "chatbot_stock_denial_enabled", False)
        ),
        chatbot_unsupported_domains=getattr(row, "chatbot_unsupported_domains", None),
        chatbot_completed_lanes=getattr(row, "chatbot_completed_lanes", None),
        chatbot_business_lane_enabled=bool(
            getattr(row, "chatbot_business_lane_enabled", False)
        ),
        chatbot_ordering_enabled=bool(getattr(row, "chatbot_ordering_enabled", False)),
        chatbot_crossdomain_ladder=getattr(row, "chatbot_crossdomain_ladder", None),
    )


def _stock_denial_enabled(db: Session, row: Any = _UNSET) -> bool:
    """R1: `system_settings.chatbot_stock_denial_enabled`, default false.

    Off, `isStockCheckDenied` is never evaluated and no turn can reach `stock_denied` or
    `demand_qty` - which is exactly as dead as those two lanes are today, by typo.

    Keeps its name and its leading `db` parameter because `test_engine_failure_paths.py`
    patches it by name with a one-argument lambda. `row` is how the caller passes the
    singleton it has already read; omit it and this reads its own, which is what that
    patched call site and any future caller get for free.
    """
    if row is _UNSET:
        row = _settings_row(db)
    return bool(getattr(row, "chatbot_stock_denial_enabled", False)) if row is not None else False


def _unsupported_domains(row: Any) -> tuple[str, ...] | None:
    """`system_settings.chatbot_unsupported_domains` (AC-304), or None for the default.

    None rather than the default list, so `route.decide` owns the fallback in ONE place -
    returning the two literals here would put them in two files. Takes the row the turn
    has already read rather than querying again.
    """
    configured = getattr(row, "chatbot_unsupported_domains", None) if row else None
    return tuple(str(x) for x in configured) if isinstance(configured, list) else None


def _crossdomain_ladder(row: Any) -> dict[str, list[str]] | None:
    """`system_settings.chatbot_crossdomain_ladder` (A7), or None for the default.

    Same shape as `_unsupported_domains`: takes the row the turn has already read,
    returns None when unset so `run_crossdomain` owns the fallback in one place.
    """
    configured = getattr(row, "chatbot_crossdomain_ladder", None) if row else None
    if not isinstance(configured, dict):
        if row is None:
            return None
        # D7: a settings row with no usable ladder (a schema built from the models, never
        # migrated) gets the shipped default; no row at all stays None (H52).
        from app.services.chatbot.lanes.business.answer import DEFAULT_CROSSDOMAIN_LADDER

        return {k: list(v) for k, v in DEFAULT_CROSSDOMAIN_LADDER.items()}
    return {
        str(k): [str(v) for v in vs]
        for k, vs in configured.items()
        if isinstance(vs, list)
    }


def _enabled_lanes(db: Session, row: Any = _UNSET) -> frozenset[str]:
    """`system_settings.chatbot_completed_lanes`: which lanes the CRM may FINISH.

    Empty by default, so a newly deployed lane delegates to n8n until the owner turns it
    on, and an absent settings row means "none" rather than "all" - the safe direction.
    """
    if row is _UNSET:
        row = _settings_row(db)
    return enabled_lanes_from(getattr(row, "chatbot_completed_lanes", None)) if row else frozenset()


# Luxon's `cccc, dd MMMM yyyy` is English regardless of where the process runs. Python's
# `%A` / `%B` follow LC_TIME, so a container with a non-English locale would hand the
# parser a date it has never been shown a single example of. Named, not formatted.
_WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def _current_date_directive() -> str:
    """`{{ $now.toUTC(8*60).format('cccc, dd MMMM yyyy') }}` - Malaysia time, same format."""
    from datetime import timedelta

    now_myt = datetime.now(timezone.utc) + timedelta(hours=8)
    return (
        f"{_WEEKDAYS[now_myt.weekday()]}, {now_myt.day:02d} "
        f"{_MONTHS[now_myt.month - 1]} {now_myt.year}"
    )


# --------------------------------------------------------------------------- #
# The tail (S2). `complete_turn` is the second half of a delegated turn: the lane
# ran in n8n, and everything from "what did it build" to "what do we remember"
# happens here (AC-201).
# --------------------------------------------------------------------------- #

# What `sub-output`'s trigger declares, minus `item` and `ctx`. Every one is nullable and
# every one is a producer's whole output, verbatim, so the tail's by-name reads become
# named arguments (D1: the sub-workflow boundary was transport).
def close_turn_for_tail(
    turn_id: str,
    *,
    session_factory: SessionFactory,
    branch_kind: str,
    ctx: dict[str, Any],
    item: dict[str, Any],
    actions: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> None:
    """Close the row `delegated` at `routed`, the state `complete_turn` refuses to run without.

    Extracted so a lane that finishes in process (S4's `low_signal`, S6c's three business
    arms) can hand over to the tail without importing `_close_turn`'s private shape or
    opening its own session in the middle of a lane. Not bookkeeping: it is the state the
    turn is genuinely in while the tail has not folded the lane's result in yet, it is what
    the trace screen should show if the process dies between the two, and it puts this
    turn's actions on the row before the tail reads `prior_actions` off it (D15).
    """
    with _session(session_factory) as db:
        _close_turn(
            db,
            turn_id,
            status="delegated",
            stage="routed",
            branch_kind=branch_kind,
            error=None,
            records=records,
            response={"ctx": ctx, "item": item, "actions": actions},
        )


FRAGMENT_FIELDS: tuple[str, ...] = (
    "result",
    "resolved",
    "gate",
    "offer_hold",
    "suggest_offer",
    "not_found",
    "incoming_picker",
    "access_choice",
    "crossdomain_render",
    "answer",
    "clarify",
)


class CompleteResult:
    """What the `/complete` endpoint serialises."""

    __slots__ = (
        "turn_id",
        "reply",
        "actions",
        "session_patch",
        "status",
        "stage",
        "error",
        "is_test",
    )

    def __init__(self, **kwargs: Any) -> None:
        for slot in self.__slots__:
            setattr(self, slot, kwargs.get(slot))
        if self.actions is None:
            self.actions = []
        self.is_test = bool(self.is_test)

    def as_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "reply": self.reply,
            "actions": split_send_actions(self.actions),
            "session_patch": self.session_patch,
            # The ROW's `is_test`, so the caller's test-guard reads one field here instead
            # of remembering what `/turn` said about this turn two calls ago.
            "is_test": self.is_test,
        }


def _load_turn(db: Session, turn_id: str) -> ChatbotTurn | None:
    return db.query(ChatbotTurn).filter(ChatbotTurn.id == turn_id).first()


def _stock_ask_packing_list_files(envelopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chatbot stock ask v2 S3, AC-SA314: one file per `incoming` entry that carries
    a `packing_list` (gated server-side, `StockService._apply_stock_visibility` only
    ever sets it for a contact whose `packing_list_allowed` is on - this reads that
    decision, it does not re-make it). The canonical file shape every other domain's
    attachment already carries into `answer.files` (`url`/`filename`/`mimeType`,
    `sorento_crm_mcp.presenters._Builder.attach`'s own normalisation, unreachable
    from this package so re-stated here rather than imported across the process
    boundary)."""
    files: list[dict[str, Any]] = []
    for envelope in envelopes or []:
        if not isinstance(envelope, dict):
            continue
        for entry in envelope.get("stock_availability") or []:
            if not isinstance(entry, dict) or entry.get("branch") != "incoming":
                continue
            packing_list = entry.get("packing_list")
            if not isinstance(packing_list, dict):
                continue
            url = packing_list.get("file_path")
            if not url:
                continue
            files.append(
                {
                    "url": url,
                    "filename": packing_list.get("filename"),
                    "mimeType": packing_list.get("mime_type"),
                }
            )
    return files


def _answered_by_so_list(envelopes: list[dict[str, Any]]) -> bool:
    """Did the SO list (`lanes/business._so_list_reply`) answer this turn?"""
    for env in envelopes:
        raw = env.get("raw_fragment") if isinstance(env, dict) else None
        fetched = raw.get("fetch") if isinstance(raw, dict) else None
        if isinstance(fetched, dict) and fetched.get("so_list") is True:
            return True
    return False


def _stock_ask_answered_entries(envelopes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chatbot stock ask v2 S4: every `stock_availability` entry of this turn's fetch that
    carries an answer (a branch and the dealer's quantity), in the order asked.

    An entry still owing its quantity is not answered yet and becomes an ask on the turn
    that answers it, once (review, PR #1333); since AVAIL-MODE-REPLIES the entries beside
    it that are answered are recorded on this turn, the turn their lines were sent."""
    from app.services import stock_ask_service

    entries: list[dict[str, Any]] = []
    for envelope in envelopes or []:
        if not isinstance(envelope, dict):
            continue
        block = [e for e in envelope.get("stock_availability") or [] if isinstance(e, dict)]
        # AVAIL-MODE-REPLIES rule 5: an answered line is printed (and so recorded) even
        # while another product owes its quantity; that product leaves the task answered
        # by a later turn, so each entry is still recorded once. `answered_entries` keeps
        # only the entries that carry a branch and the dealer's quantity.
        entries.extend(stock_ask_service.answered_entries(block))
    return entries


def _ctx_message_text(ctx: Any) -> str:
    """What the customer typed this turn (`ctx.text.message.message.text`)."""
    inner = jsc.get(jsc.get(jsc.get(ctx, "text"), "message"), "message")
    value = jsc.get(inner, "text")
    return jsc.js_string(value) if jsc.truthy(value) else ""


def _record_customer_asks(
    session_factory: SessionFactory,
    *,
    turn_id: str,
    contact_respond_id: str,
    state: Any,
    stock_entries: list[dict[str, Any]],
    reply_text: str,
    refer_context: dict[str, Any] | None,
    ctx: Any,
    dry_run: bool,
    chat_console: bool,
) -> None:
    """CUSTOMER-ASKS-REFER-ONLY (owner ruling 1 Oct 2026): Customer asks logs every reply
    that referred the customer to their salesman, and only those. Run by both tails
    (`_run_answer`, `complete_turn`) once the turn row is closed.

    Whether the reply referred is never read off its text: a stock ask line carries the
    presenter's `refers_to_salesman`, and every other refer line was printed through
    `turn/refer.py`, which marked this turn (`refer.consume`, so a second tail in the same
    turn cannot log it twice). `refer_asks` names the rows: the incoming ETA lines, the
    misses, a declined did-you-mean, the plan's products, else what the customer typed.

    Chatbot stock ask v2 S4 / S5 (AC-SA401, AC-SA402, AC-SA501): a live turn, and (owner
    ruling 28 Sep 2026) a CHAT CONSOLE turn, write the rows and enqueue the real salesman
    job; every other dry run does neither (D14). The send is a queued job, so Respond never
    holds the reply up."""
    from app.services.chatbot import refer_asks
    from app.services.chatbot.turn import refer

    marked = refer.consume()
    if dry_run and not chat_console:
        return
    try:
        context = refer_context or {}
        referred = marked or any(
            isinstance(e, dict) and e.get("refers_to_salesman") is True for e in stock_entries
        )
        entries = [
            *stock_entries,
            *refer_asks.referred_entries(
                referred=referred,
                reply_text=reply_text,
                envelopes=context.get("envelopes") or [],
                plan=context.get("plan"),
                pending_before=context.get("pending_before"),
                message_text=context["message_text"] if "message_text" in context else _ctx_message_text(ctx),
                answered=stock_entries,
            ),
        ]
    except Exception:  # noqa: BLE001 - a post-commit side effect: the reply is already recorded
        logger.exception("chatbot turn %s: building the Customer asks rows failed", turn_id)
        return
    if not entries:
        return
    _after_stock_ask_turn(
        session_factory,
        turn_id=turn_id,
        contact_respond_id=contact_respond_id,
        state=state,
        entries=entries,
        reply_text=reply_text,
        source="console" if dry_run else "live",
    )


def _after_stock_ask_turn(
    session_factory: SessionFactory,
    *,
    turn_id: str,
    contact_respond_id: str,
    state: Any,
    entries: list[dict[str, Any]],
    reply_text: str,
    source: str = "live",
) -> None:
    """Hand the answered stock ask to `stock_ask_service` once the turn is closed. Never
    raises: the turn is answered and recorded, and a failure here must not turn it into
    an error reply."""
    from app.services import stock_ask_service
    from app.services.field_access import resolve_contact_with_null_workspace_fallback

    profile = getattr(state, "profile", None)
    try:
        with _session(session_factory) as db:
            contact_id = resolve_contact_with_null_workspace_fallback(
                db, contact_id=contact_respond_id, space_id=default_space_id(db)
            )
            stock_ask_service.after_answered_turn(
                db,
                turn_id=turn_id,
                contact_id=contact_id,
                notify_salesman=bool(getattr(profile, "notify_salesman", False)),
                entries=entries,
                reply_text=reply_text,
                source=source,
            )
    except Exception:  # noqa: BLE001 - the dealer's answer is already recorded
        logger.exception("chatbot turn %s: stock ask follow-up failed", turn_id)


def _attachments_src(answer: Any) -> Any:
    """`send-attachments`'s own frozen expression, as a value.

    n8n reads `$("Call 'sub-answer'").first().json.outcome_fragment['central-exchange']`
    by name, which is why the attachment lane survived every rewiring. The CRM hands the
    same value back on `reply.attachments_src` so the node reads one field instead.
    """
    fragment = jsc.get(answer, "outcome_fragment")
    source = jsc.get(fragment, "central-exchange") if isinstance(fragment, dict) else None
    return _clean_attachments(source)


def _clean_attachments(source: Any) -> Any:
    """One send per FILE, and a filename that says WHICH file it is.

    `source` is `central-exchange`'s own item - the ANSWER ENVELOPE - and the list the
    executor sends from is `envelope.attachments`, which is what `sub-send-attachments`'
    own `central-exchange` stub reads (`const a = n.first().json.attachments; return
    Array.isArray(a) ? a : []`). Every other shape passes through untouched, including the
    casual lane's `{response}` and a bare list.

    An attachment entry is `{url, filename, mimeType, attachmentType[, uploadedAt]}` and
    NOTHING else - measured over 7402 entries in the capture corpus, zero of which carry an
    id or a company. So:

    * **Identity is the `url`**, which is also the key the executor's own `Remove
      Duplicates` node uses, so the two halves cannot disagree about what a duplicate is.
    * **The company comes from the ROW that produced the file**, because the entry does not
      carry one. The envelope's rows do: each carries `company_name` and the values that
      name the thing the file belongs to. A row CLAIMS a file when one of its values is the
      filename, or is contained in it (a packing list is named after its container). When
      the claims line up one-for-one with the colliding files, they pair off in row order -
      the presenter appends attachments as it walks rows - and each file is qualified with
      its own company.

    When the claims do NOT line up, the files are left exactly as they arrived: an absence
    of evidence is not a licence to label one of them "Mocha". Measured on the corpus: of 91
    filename collisions between different urls, 56 attribute cleanly to two different
    companies and 35 carry no rows to attribute from.
    """
    if isinstance(source, dict):
        files = source.get("attachments")
        if not isinstance(files, list) or not files:
            return source
        return {**source, "attachments": _label_attachments(files, _company_rows(source))}
    if isinstance(source, list):
        return _label_attachments(source, [])
    return source


def _company_rows(envelope: Any) -> list[tuple[Any, list[str]]]:
    """`[(company_name, [values that identify this row])]`, in the envelope's own order."""
    items = envelope.get("items") if isinstance(envelope, dict) else None
    if not isinstance(items, list):
        items = envelope.get("answers") if isinstance(envelope, dict) else None
    rows: list[tuple[Any, list[str]]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        company: Any = None
        values: list[str] = []
        for field in item.get("fields") or []:
            if not isinstance(field, dict):
                continue
            key = jsc.nullish_str(field.get("key")).lower()
            value = field.get("value")
            if key == "company_name" or field.get("label") == "Company":
                company = value
            elif isinstance(value, str) and value.strip():
                values.append(value.strip())
        rows.append((company, values))
    return rows


def _claims(name: str, values: list[str]) -> bool:
    """Does a row carrying these values own the file called `name`?

    Equality first (a promotion row's value IS the flyer's filename), then containment for
    values long enough to be an identifier (a packing list is "<container> - WH.xlsx"). Six
    characters is the floor so a short code or a status word cannot claim a file by
    accident; nothing shorter appears as a container, shipment or product code.
    """
    return any(v == name for v in values) or any(len(v) >= 6 and v in name for v in values)


def _with_company(filename: str, company: Any) -> str:
    """"x.xlsx" + "Mocha" -> "x (Mocha).xlsx". BEFORE the suffix, never after it.

    WhatsApp picks the viewer off the extension, so a name ending in "(Mocha)" arrives as a
    file the phone does not know how to open.
    """
    stem, dot, suffix = filename.rpartition(".")
    label = f" ({jsc.js_string(company)})"
    if dot and stem and 1 <= len(suffix) <= 8 and " " not in suffix:
        return f"{stem}{label}.{suffix}"
    return f"{filename}{label}"


def _label_attachments(files: list, rows: list[tuple[Any, list[str]]]) -> list:
    """Dedupe on `url`, then qualify a surviving filename collision with its company."""
    kept: list[Any] = []
    seen_urls: set[Any] = set()
    for entry in files:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        url = entry.get("url")
        if not jsc.truthy(url):
            # D9 (8 Sep 2026): a file with no link is listed in the answer (the presenter
            # says "(file link unavailable right now)") but never sent - a send with no
            # url is a dead action, not a delivery.
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        kept.append(entry)

    groups: dict[str, list[int]] = {}
    for index, entry in enumerate(kept):
        if isinstance(entry, dict) and jsc.truthy(entry.get("filename")):
            groups.setdefault(jsc.js_string(entry.get("filename")), []).append(index)

    labelled = list(kept)
    for name, indexes in groups.items():
        if len(indexes) < 2:
            continue
        claims = [company for company, values in rows if _claims(name, values)]
        if len(claims) != len(indexes) or not all(jsc.truthy(c) for c in claims):
            continue  # nothing established: send them exactly as they arrived
        if len({jsc.js_string(c) for c in claims}) < 2:
            continue  # one company owns both: the suffix would be noise
        for index, company in zip(indexes, claims):
            entry = labelled[index]
            labelled[index] = {**entry, "filename": _with_company(name, company)}
    return labelled


def run_tail(
    db: Session,
    *,
    turn_id: str,
    ctx: Mapping[str, Any],
    item: Mapping[str, Any],
    values: Mapping[str, Any],
    canned: Any,
    branch_kind: str | None,
    dry_run: bool,
    contact_respond_id: str,
    turn_trace: trace_mod.TurnTrace,
    write_session: bool = True,
    lane_quick_replies: Any = None,
    state: Any = None,
    question: Any = None,
    remembered_before: Mapping[str, Any] | None = None,
    profile: Any = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """outcome -> CS member offer -> reply ladder -> persist the five keys.

    ONE tail, TWO callers. `complete_turn` runs it for a lane that ran in n8n, and
    `run_turn` runs it for a lane the CRM finished itself - and they must be the same
    code, because the whole claim of the port is that a turn's memory does not depend on
    which half of the migration answered it.

    What the re-architecture changed is the SECOND half. The reply is still composed from
    the producers this branch built (`tail/reply_ladder.py`, the surviving half of the
    retired compiler); the MEMORY is no longer re-derived from that reply. The five keys
    are written from the `State` APPLY computed and the question the composer asked, so
    there is one writer, one shape, and no ladder of markers to keep in step.

    `state` is APPLY's own `state'` when the caller has one (every lane that runs inside
    `run_turn`): its focus is what gets written, and its `pending` is what an answering
    lane carried.

    `write_session` is the ONE thing a caller may vary, and it is not a dry-run flag:
    `access_denied` answers WITHOUT the tail's write on a live turn, and a contact refused
    the agent must not have the turn written into their memory.
    """
    # ESCALATION-CONTROL (security review S1): the contact's profile for the block checks
    # below. A caller with APPLY's state carries it there; n8n's `/complete` has none and
    # hands the contact's own profile in (`complete_turn`).
    if profile is None:
        profile = getattr(state, "profile", None)
    from app.services.chatbot.contracts import SessionVars
    from app.services.chatbot.tail import member_offer as member_mod
    from app.services.chatbot.tail import outcome as outcome_mod
    from app.services.chatbot.tail import reply_ladder

    # -- what this branch built ---------------------------------------- #
    producers: dict[str, Any] = {}
    for name, field in outcome_mod.CARRIER_FIELDS.items():
        if values.get(field) is not None:
            producers[name] = values[field]

    outcome_input: dict[str, Any] = dict(item)
    # `entry-gate`: the escalate catalog runs only when the lane stamped a branch
    # kind on the item. Everything else goes straight to the outcome hub.
    if jsc.js_string(jsc.get(item, "branch_kind") or "") != "":
        catalog = outcome_mod.escalate_catalog(
            item,
            ctx,
            canned,
            not_found=values["not_found"],
            incoming_picker=values["incoming_picker"],
            access_choice=values["access_choice"],
            suggest_offer=values["suggest_offer"],
            gate=values["gate"],
            offer_hold=values["offer_hold"],
        )
        if ctx.get("top_selling_no_offer") and catalog.get("is_escalate_offer") is True:
            # PR #1273 round 7: inside a ranking, or the report it handed over to, no
            # escalate offer and no routing picker unless the customer asked for a person.
            from app.services.chatbot import dealer_stock as dealer_mod

            catalog = {
                **catalog,
                "response": dealer_mod.refers_to_salesman(jsc.js_string(catalog.get("response") or ""))[0],
                "is_escalate_offer": False,
            }
        producers["escalate-catalog"] = catalog
        outcome_input = catalog
        # ESCALATION-CONTROL: no CS roster is built for a barred contact at all (its rows
        # would ride on `result_set` even after the text is stripped).
        if not turn_state.escalation_barred(profile) and outcome_mod.cs_offer_gate(
            catalog, ctx, values["gate"]
        ):
            plan = member_mod.cs_roster_plan(values["gate"])
            rosters = member_mod.fetch_rosters(db, plan, ctx)
            offer = member_mod.build_cs_member_offer(catalog, plan, rosters)
            producers["cs-roster-plan"] = plan
            producers["build-cs-member-offer"] = offer
            outcome_input = offer

    outcome_items = outcome_mod.build_outcome([{"json": outcome_input}], producers)
    outcome = jsc.get(outcome_items[0]["json"], "outcome") or {}

    # -- what to say ----------------------------------------------------- #
    composed = reply_ladder.compose_reply(outcome)
    if question is None:
        question = _question_offered(ctx, values, outcome, composed)
    quick_replies = composed.get("quick_replies")
    if jsc.truthy(lane_quick_replies) and not jsc.truthy(quick_replies):
        # A lane may have composed quick replies of its own before the tail ran: the
        # escalation clarifies name the teams so the answer is a tap.
        quick_replies = lane_quick_replies
    barred_profile = profile
    if turn_state.escalation_barred(barred_profile):
        # ESCALATION-CONTROL: the same backstop `_run_answer` runs, for the lanes this
        # tail composes (canned, escalation). The casual lane builds its send action
        # before the tail and runs the same strip in `_run_casual_lane`.
        from app.services.chatbot import escalation_control

        stripped_text, question, offered = escalation_control.strip_text(
            jsc.js_string(composed.get("text") or ""), question, barred_profile
        )
        if offered:
            composed = {**composed, "text": stripped_text}
            # Security review N2: the offer's team buttons go with it.
            quick_replies = None
    reply = {
        "text": composed.get("text"),
        "quick_replies": quick_replies,
        "result_set": composed.get("result_set") or [],
        "attachments_src": _attachments_src(values["answer"]),
    }

    turn_trace.record(
        "replied",
        summary=trace_mod.replied_summary(reply, branch_kind),
        why="The reply is composed from what the lane built, never from the customer's words.",
        facts={
            "lane": branch_kind,
            "quick_replies": bool(reply["quick_replies"]),
            "rows_offered": len(reply["result_set"]),
        },
        raw={"reply": reply},
    )

    # -- what to remember ------------------------------------------------ #
    # The five keys, from the state this turn computed (AC-1504, AC-1532). The allowlist
    # check `LegacyVariables` used to run is now the shape itself: `SessionVars` forbids
    # every key outside the five, so a producer cannot leak one into a real contact's
    # memory.
    # What APPLY decided this turn, when the caller ran one. `/complete` is n8n's own
    # entry and has no APPLY state at all, so it re-reads the session this turn STARTED
    # with - which is exactly why the carry below reads `applied` and not `state`: that
    # reloaded pending is the question as it stood BEFORE the turn, and writing it back
    # would re-open a question the lane has just answered.
    applied = state
    if state is None:
        state = turn_runtime.load_state(
            {"session_vars": jsc.get(jsc.get(ctx, "session"), "session_vars")},
            profile=profile if profile is not None else turn_state.Profile(),
            turn_no=0,
        )
    before = dict(remembered_before or session_state.five_keys(jsc.get(ctx, "session")))
    payload = {
        "focus": turn_state.focus_to_wire(state.focus),
        # The lane's OWN question, else the one APPLY carried - the same rule the
        # composed arms run through `turn/tail.py::session_payload` ("no new question"
        # is not "no question", AC-1532). A lane that answers nothing and clears nothing
        # must leave the open question exactly where it found it: hand-pass 2 sent
        # "hello" while an escalate offer was open, the casual lane wrote a patch with
        # no question in it, and the "1" that followed had nothing left to answer
        # (browser pass 3, turns 8 and 9).
        "open_question": turn_pending.to_wire(
            question if question is not None else (applied.pending if applied else None)
        ),
        # The ideate lane's own pointer wins over the turn-start value (owner console
        # walk 25 Sep 2026: three turns minted three drafts because this always read
        # `before`). Only `lanes/ideate.py::build_reply` sets `ideation` on the tail
        # item, `None` clearing it on a terminal status - pre-re-architecture
        # `tail/compile_state.py:773` wrote `ideate.ideation if ideate else prev.ideation`
        # and commit 0a335146e dropped that arm.
        "ideation": item.get("ideation") if "ideation" in item else before.get("ideation"),
        "access_levels": list(before.get("access_levels") or []),
        "contains_flyer": bool(before.get("contains_flyer")),
    }
    SessionVars(**payload)
    reply_ladder.sanitize_em_dash(payload)

    written = (not dry_run) and write_session
    if written:
        turn_tail.write_merged(
            db,
            respond_io_id=contact_respond_id,
            payload=payload,
            base=before,
            turn_id=turn_id,
        )
        _log_session_write(db, turn_id=turn_id, contact_respond_id=contact_respond_id)
    turn_trace.record(
        "remembered",
        summary=(
            "Nothing was written: this is a test turn (D14)."
            if dry_run
            else "Nothing was written: a refused turn is not remembered."
            if not write_session
            else "Wrote the conversation state."
        ),
        why=(
            "The CRM is the only writer of the conversation state on the turn path (D2)."
        ),
        facts={
            "asking": payload["open_question"]["kind"] if payload["open_question"] else None,
            "dry_run": dry_run,
            "written": written,
        },
        raw={"session_patch": payload},
    )

    return reply, payload


def _question_offered(
    ctx: Mapping[str, Any], values: Mapping[str, Any], outcome: Mapping[str, Any], composed: Mapping[str, Any]
) -> Any:
    """The one open question a KEPT lane's own producers just put on screen.

    The re-architected arms hand `run_tail` their composer's `question` directly. These
    four are the lanes that still build their ask as copy (the team clarify, the company
    clarify, the CS member roster and the escalate offer), so the pending they leave open
    is read back off what they offered - one place, rather than the four markers the
    retired compiler wrote independently.
    """
    def _options(rows: Any, kind: str) -> list[dict[str, Any]]:
        built: list[dict[str, Any]] = []
        for index, row in enumerate(jsc.array(rows)):
            if not jsc.truthy(row):
                continue
            label = jsc.get(row, "label") or jsc.get(row, "company_name") or jsc.get(row, "name")
            # Security N-2 (hand pass 11 final): for `kind == "company"` this `value` is a
            # COMPANY id, not an entity uuid. It is safe here only because
            # `ESCALATION_OFFER_KINDS` routes `company_pick` to `turn/apply.py::
            # _answer_offer` BEFORE the generic roster path, so it never reaches
            # `focus.uuids` or a tool call - removing `company_pick` from that set would
            # change that.
            value = jsc.get(row, "team") or jsc.get(row, "uuid") or jsc.get(row, "company_id")
            built.append(
                {
                    "position": jsc.get(row, "idx") or index + 1,
                    "label": label,
                    "uuid": value,
                    "uuids": [value] if value else [],
                    "entity_type": kind,
                    "payload": _option_payload(row, kind),
                }
            )
        return built

    def _option_payload(row: Any, kind: str) -> dict[str, Any]:
        """What the ANSWER to this option carries into the next turn.

        A team option names its team; a COMPANY option names its company and, above all,
        its `company_id` - the only field routing reads (`lanes/escalation.py::
        _next_assignee_body`), and what makes the tapped number and the typed company
        name reach `escalation_context` through the same seam (hand pass 11, blocker 2).

        SRTSC07 review round 1, SHOULD-2: a TEAM option also carries THIS turn's
        `routing.suggested_agent` beside its own `team` - read straight off `ctx`
        (the enclosing function's own parameter) rather than off a locally-assigned
        variable, so this stays correct regardless of which branch calls it. Round 4
        (owner-approved, 22 Sep 2026): `brand_code` is the SAME idiom, one axis over,
        read off `values["gate"]` (the enclosing function's own parameter too -
        `lanes/business/gate.py::run_gate`'s own `routing_brand`).
        """
        if kind == "team":
            routing = jsc.get(jsc.get(jsc.get(ctx, "parse"), "output") or {}, "routing") or {}
            return {
                "team": jsc.get(row, "team"),
                "agent": jsc.get(routing, "suggested_agent"),
                "brand_code": jsc.get(values.get("gate"), "routing_brand"),
            }
        if kind == "company":
            return {
                "company": jsc.get(row, "company_name") or jsc.get(row, "label"),
                "company_id": jsc.get(row, "company_id") or None,
                "brand_code": jsc.get(row, "brand_code") or None,
            }
        return {}

    clarify = values.get("clarify")
    if jsc.truthy(clarify):
        if jsc.truthy(jsc.get(clarify, "clarify_product")):
            # #865 round 5: the escalation's own product did-you-mean. Each option is a
            # product and the team the escalation goes to, so the number the customer
            # replies with is an ACCEPTANCE (`turn/apply.py::_answer_offer`) that settles
            # that product onto the focus. `uuid` stays empty: an option is not a person
            # (`preferred_assignee_id`) and not a row to fetch with.
            routing = jsc.get(jsc.get(jsc.get(ctx, "parse"), "output") or {}, "routing") or {}
            return turn_pending.ask(
                "team_pick",
                [
                    {
                        "position": jsc.get(row, "position") or index + 1,
                        "label": jsc.get(row, "label"),
                        "uuid": None,
                        "uuids": [],
                        "entity_type": "product",
                        "payload": {
                            "team": jsc.get(row, "team"),
                            "product_code": jsc.get(row, "product_code"),
                            "agent": jsc.get(routing, "suggested_agent"),
                        },
                    }
                    for index, row in enumerate(jsc.array(jsc.get(clarify, "clarify_product_options")))
                    if jsc.truthy(row)
                ],
                team=jsc.get(clarify, "team"),
                expects="pick",
            )
        if jsc.truthy(jsc.get(clarify, "clarify_team")):
            return turn_pending.ask(
                "team_pick", _options(jsc.get(clarify, "clarify_team_options"), "team"), expects="pick"
            )
        if jsc.truthy(jsc.get(clarify, "clarify_text")):
            held = turn_pending.from_wire(
                jsc.get(jsc.get(jsc.get(jsc.get(ctx, "session"), "session_vars"), "variables"), "held_offer")
            )
            if held is not None and turn_pending.offered_companies(held):
                # #865 round 6 (R4): a bare "yes" over the member picker asked which
                # company; the picker stays the open question (n8n re-persists the
                # offer), so a member number, a member name or a company word answers it.
                return held
            # The clarify's OWN pool first (`escalation.clarify_company_reply`'s
            # `clarify_company_options`): the companies it just printed, carrying the ids
            # that route. `result_set` stays the fallback for a clarify composed by a
            # producer that filled one - the member roster the n8n arm clarifies over.
            routing = jsc.get(jsc.get(jsc.get(ctx, "parse"), "output") or {}, "routing") or {}
            rows = jsc.get(clarify, "clarify_company_options") or composed.get("result_set")
            return turn_pending.ask(
                "company_pick",
                _options(rows, "company"),
                # The team the offer was made for: an acceptance names none of its own
                # (contract 108), and a clarify that dropped it sent the answering turn
                # to whatever team the fresh parse happened to suggest.
                team=jsc.get(clarify, "team") or jsc.get(routing, "suggested_team"),
                expects="pick",
                # SRTSC07 review round 1, SHOULD-2: same reasoning as `team` above,
                # one axis over - on the pending's own top-level payload, since a
                # company clarify's own bare "yes" answers it without a position.
                # `brand_code` (round 4) is the SAME idiom, off `values["gate"]`.
                payload={
                    "agent": jsc.get(routing, "suggested_agent"),
                    "brand_code": jsc.get(values.get("gate"), "routing_brand"),
                },
            )

    member = outcome.get("build-cs-member-offer")
    if jsc.truthy(member):
        from app.services.chatbot import answer_bridge

        routing = jsc.get(jsc.get(jsc.get(ctx, "parse"), "output") or {}, "routing") or {}
        return turn_pending.ask(
            "member_offer",
            [
                option
                for option in (
                    answer_bridge.member_option(row, i + 1)
                    for i, row in enumerate(jsc.array(jsc.get(member, "cs_last_result_set")))
                )
                if option
            ],
            # #865 round 6: the team and the companies the picker was built for
            # (`answer_bridge._miss_question`'s own member arm, the same shape).
            team=jsc.get(routing, "suggested_team"),
            expects="pick",
            # SRTSC07 review round 1, SHOULD-2: picking a member option IS an
            # escalation acceptance (`turn/apply.py:546`). `brand_code` (round 4) is
            # the SAME idiom, off `values["gate"]`.
            payload={
                "agent": jsc.get(routing, "suggested_agent"),
                "brand_code": jsc.get(values.get("gate"), "routing_brand"),
                "roster_plan": answer_bridge.roster_plan_of(member),
            },
        )

    catalog = outcome.get("escalate-catalog")
    if jsc.truthy(catalog) and jsc.get(catalog, "is_escalate_offer") is True:
        routing = jsc.get(jsc.get(jsc.get(ctx, "parse"), "output") or {}, "routing") or {}
        return turn_pending.ask(
            "team_pick",
            [{"position": 1, "label": "Yes", "entity_type": "team", "payload": {}}],
            team=jsc.get(routing, "suggested_team"),
            expects="yes_no",
            # SRTSC07 review round 1, SHOULD-2: the escalate-catalog twin of
            # `answer_bridge.py::_miss_question`'s own bare-"Yes" arm. `brand_code`
            # (round 4) is the SAME idiom, off `values["gate"]`.
            payload={
                "agent": jsc.get(routing, "suggested_agent"),
                "brand_code": jsc.get(values.get("gate"), "routing_brand"),
            },
        )
    return None


def _send_actions(
    reply: Mapping[str, Any], *, dry_run: bool, preview: bool = False
) -> list[dict[str, Any]]:
    """The actions the CALLER executes for a finished turn, in order (D9).

    Shape agreed with the n8n executor, and it is the SEALED reply's own values verbatim,
    not a normalised copy of them:

    * `quick_replies` is `compile-current-state`'s `quick_reply` as it stands - n8n's
      comma-joined string, or null when the turn offered none. Coercing it to a list
      would hand `sub-sendmsg` a type its `quick_reply` input has never been given, and
      the sender is the half of this that did NOT move into the CRM.
    * `result_set` is `variables.last_result_set`, which is what the send node passes on
      so a numbered reply's rows travel with the message that numbered them.
    * `send_attachments` is a SECOND action and only when there is something to send.
      It carries the whole `reply` because `sub-send-attachments` reads more than one
      field off it, and it comes AFTER the message for the same reason n8n wires it that
      way: the text explains the files.

    `preview` adds AC-507's second flag, and only a lane that stood a seam value in sets
    it: the key is ABSENT on a live action and on a dry run whose text is real, exactly as
    the escalation lane emits it, so a reader never has to tell `preview: false` from
    "this build does not report it".
    """
    send: dict[str, Any] = {
        "kind": "send_message",
        "text": reply.get("text"),
        "quick_replies": reply.get("quick_replies"),
        "result_set": reply.get("result_set"),
        "dry_run": dry_run,
    }
    if preview:
        send["preview"] = True
    actions = [send]
    attachments = reply.get("attachments_src")
    if attachments is not None:
        attach: dict[str, Any] = {
            "kind": "send_attachments",
            "attachments_src": attachments,
            "reply": dict(reply),
            "dry_run": dry_run,
        }
        if preview:
            attach["preview"] = True
        actions.append(attach)
    return actions


def _complete_canned_lane(
    db: Session,
    *,
    branch_kind: str,
    ctx: Mapping[str, Any],
    item: Mapping[str, Any],
    turn_id: str,
    dry_run: bool,
    contact_respond_id: str,
    turn_trace: trace_mod.TurnTrace,
    state: Any = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, list[dict[str, Any]]]:
    """One of S3's eight lanes, answered inside the turn. `(reply, patch, actions)`.

    Two shapes, and the split is n8n's own graph rather than a convenience:

    * **`access_denied`** is answered by the SEND NODE's own expression - the `route`
      Switch's `access_denied` output goes STRAIGHT to
      `sorento-sub-respond-sendmsg-respond5` (there is no tag node on that arm) and never
      reaches `compile-current-state`. So the CRM composes that one string and runs
      no tail: a contact who is not allowed the agent must not have the turn written into
      their memory, and running the tail would write it.
    * **everything else** builds the `sub-output` fragments its lane would have handed the
      tail, and the S2 tail runs UNCHANGED. `ideate` supplies its reply through
      `item.outcome_fragment` - RS-6.1c's own mechanism and the exact key `build-outcome`
      reads - so the tail needs no ideate arm.

    The `sent` stage is recorded here and it is not a fiction: the CRM never sends (D9),
    it hands the caller a `send_message` action, and the trace records that hand-off so
    the operator screen shows where the turn ended.
    """
    from app.services.chatbot import copy as copy_mod
    from app.services.chatbot.lanes import ideate as ideate_mod

    canned = copy_mod.resolve(db)
    reply_extras: dict[str, Any] = {}

    if branch_kind in canned_lanes.NO_SESSION_WRITE_BRANCH_KINDS:
        text = canned_lanes.access_denied_text(db, ctx, canned)
        reply = {"text": text, "quick_replies": None, "result_set": [], "attachments_src": None}
        session_patch: dict[str, Any] | None = {}
        turn_trace.record(
            "replied",
            summary=f"Refused: {trace_mod.lane_words(branch_kind).lower()}.",
            why="The contact is not granted the agent this turn would have used.",
            facts={"lane": branch_kind},
            raw={"reply": reply},
        )
        turn_trace.record(
            "remembered",
            summary="Nothing was remembered.",
            why="A refused turn is not written into the contact's memory.",
            facts={"lane": branch_kind, "written": False, "dry_run": dry_run},
            raw=None,
        )
    else:
        prev_variables = jsc.get(
            jsc.get(jsc.get(ctx, "session"), "session_vars"), "variables"
        ) or {}
        if branch_kind == "ideate":
            # `dry_run` goes INTO the lane, not around it: the lane's seam is an MCP write
            # tool, and on a dry run it is called as a TEST turn (`is_test`) rather than
            # skipped (#1179), so the reply is the tool's own words on both kinds of turn.
            lane = ideate_mod.run(ctx, item, dry_run=dry_run)
            tail_item = lane["item"]
            reply_extras = lane["reply_extras"]
            fragments: dict[str, Any] = {"item": tail_item}
        else:
            fragments = canned_lanes.fragments_for(branch_kind, item, ctx, prev_variables, canned)
            tail_item = fragments["item"]
        values = {name: fragments.get(name) for name in FRAGMENT_FIELDS}
        reply, session_patch = run_tail(
            db,
            turn_id=turn_id,
            ctx=ctx,
            item=tail_item,
            values=values,
            canned=canned,
            branch_kind=branch_kind,
            dry_run=dry_run,
            contact_respond_id=contact_respond_id,
            turn_trace=turn_trace,
            state=state,
        )
        reply = {**reply, **reply_extras}

    actions = _send_actions(reply, dry_run=dry_run)
    turn_trace.record(
        "sent",
        summary="Handed the reply to the caller to send.",
        why="The CRM never sends on the turn path; n8n owns respond.io egress (D9).",
        facts={"lane": branch_kind, "actions": len(actions), "dry_run": dry_run},
        raw={"actions": actions},
    )
    return reply, session_patch, actions


@_refer_tracked
def complete_turn(  # noqa: PLR0915 - one linear pipeline, and the order IS the contract
    turn_id: str,
    fragments: dict[str, Any],
    *,
    session_factory: SessionFactory,
    compose_send_action: bool = False,
    lane_trace: Any = None,
    state: Any = None,
    chat_console: bool = False,
) -> CompleteResult:
    """Run the tail of one turn: outcome -> member offer -> state -> compose -> persist.

    `fragments` is the `sub-output` trigger contract: `item` plus the eleven nullable
    producer outputs, plus an optional `ctx` override. `ctx` normally comes off the turn
    row, which is where `/turn` persisted it - one less thing for the caller to keep
    consistent, and the only thing that makes a retry from the trace screen possible.

    **Dry run writes NOTHING (D14, AC-702's shape).** `is_test` was decided on the
    envelope at `/turn` and is read off the row here, so a console or clone turn cannot
    become a live write by calling a different URL. The response carries the would-be
    `session_patch` instead.

    **The session write is validated BEFORE it happens.** `LegacyVariables(extra="forbid")`
    is what stops a harness key leaking into a customer's session (H15, AC-203), and it
    has to raise before `overwrite_for_contact`, not after.

    `state` is APPLY's `state'`, handed over by the lanes that run inside `run_turn`, so
    the session this writes carries the focus and the still-open question THIS turn
    computed rather than a re-read of the one it started with. `/complete` is n8n's path
    and has none.

    `compose_send_action` is for a lane that finishes IN the CRM and only learns its own
    words here (S6c's business arms): it puts the `send_message` on the row before the row
    is closed, so the caller has something to send and a duplicate delivery replays it.
    Default FALSE, because `/complete` is n8n's path and n8n composes its own send.
    """
    from app.services.chatbot import copy as copy_mod

    item = fragments.get("item") or {}
    values = {name: fragments.get(name) for name in FRAGMENT_FIELDS}

    with _session(session_factory) as db:
        row = _load_turn(db, turn_id)
        if row is None:
            raise LookupError(f"chatbot turn {turn_id} not found")
        if row.status == "done" and isinstance(row.response, dict):
            # Idempotent, the same shape D15 gives a duplicate delivery: the tail already
            # ran and the caller already has an answer it must not send twice.
            stored = row.response
            return CompleteResult(
                turn_id=turn_id,
                reply=stored.get("reply"),
                actions=stored.get("actions") or [],
                session_patch=None,
                status=row.status,
                stage=row.stage,
                is_test=bool(row.is_test),
            )
        if row.status != "delegated":
            # ONLY a delegated turn has a tail to run, and the guard is not tidiness.
            # A `failed` turn has no `ctx` and no lane, so running the tail on it would
            # compose an answer out of whatever `item.branch_kind` the CALLER's fragments
            # happened to carry - an answer decoupled from the real failure - WRITE it to
            # the customer's session, and overwrite `status` / `error` with `done` / null,
            # erasing the R4 / H32 record the trace screen exists to show. A `processing`
            # turn is the same hazard one moment earlier. Refused BEFORE the tail runs, so
            # nothing is composed and nothing is written.
            raise AppException(
                status_code=409,
                message="This turn cannot be completed.",
                detail=(
                    f"chatbot turn {turn_id} is {row.status!r} at stage {row.stage!r}, not "
                    "'delegated', so it has no lane result to fold in. A failed turn is "
                    "retried from the trace screen, never completed."
                ),
                code="CHATBOT_TURN_NOT_DELEGATED",
            )
        contact_respond_id = row.contact_respond_id
        # H56, the tail's half. `/complete` is n8n's own entry, so this session comes
        # straight off `SessionLocal` and nothing has stamped a company scope on it -
        # which would empty the CS roster read below (`fetch_rosters` -> `list_team_roster`
        # walks `Team` / `AgentTeam`, both owned models) exactly the way it emptied the
        # resolver. The row's contact is the only identity a `/complete` call carries, and
        # it is the same one the head scoped by.
        #
        # UNCONDITIONAL, and the earlier "only when the session carries no scope yet"
        # version was wrong twice over: it cost an in-process caller nothing, but it made
        # the property untestable (`tests/conftest.py` defaults every new session to
        # Sorento, so the guard never fired under test) and it would silently skip a
        # session some other listener had stamped with a scope that is not this contact's.
        # Two indexed reads on a path that already makes an LLM call is not a cost worth
        # a conditional.
        set_company_scope(
            db, _contact_company_scope(session_factory, str(contact_respond_id or ""))
        )
        dry_run = bool(row.is_test)
        stored_response = row.response if isinstance(row.response, dict) else {}
        ctx = fragments.get("ctx") or stored_response.get("ctx") or {}
        branch_kind = row.branch_kind
        prior_actions = list(stored_response.get("actions") or [])
        turn_trace = trace_mod.TurnTrace.resume(row.trace)
        # The events the LANE recorded after the head closed the row (A9): `run_fetch`
        # runs in the head and its `tool` event is already on `row.trace`, but
        # `run_crossdomain` runs inside `complete_answer` - AFTER the head wrote the row -
        # so resuming from the row alone dropped every `crossdomain` and `reveals` event
        # on the floor. The turn-detail screen then showed an empty cross-domain section
        # for every business turn, and the console check could not tell a rung that never
        # ran from one whose evidence was discarded. Carried explicitly rather than by
        # sharing the object, because `resume` deliberately rebuilds from the persisted
        # array and that is what makes the head/tail split one timeline.
        if lane_trace is not None:
            seen = {json.dumps(e, sort_keys=True, default=str) for e in turn_trace.events}
            for event in getattr(lane_trace, "events", []) or []:
                if json.dumps(event, sort_keys=True, default=str) not in seen:
                    turn_trace.events.append(event)
        canned = copy_mod.resolve(db)

        # EVERY failure in the tail closes the turn, the way R4 promises for every
        # other failure path: `failed` at `remembered`, with the reason on the row and
        # on the trace. Left unwrapped, an allowlist raise (AC-203) or a malformed
        # fragment leaves the row exactly as the HEAD wrote it - `delegated` at
        # `routed` - which reads on the trace screen as a turn still waiting for a lane
        # that finished minutes ago, and is the dropped turn H32 is about.
        try:
            reply, session_patch = run_tail(
                db,
                turn_id=turn_id,
                ctx=ctx,
                item=item,
                values=values,
                canned=canned,
                branch_kind=branch_kind,
                dry_run=dry_run,
                contact_respond_id=contact_respond_id,
                turn_trace=turn_trace,
                state=state,
                # NOT a `FRAGMENT_FIELD`: those are the lane CARRIERS the tail composes
                # from, and this is one value the tail could not have composed for itself
                # - what the lane already decided the customer can tap.
                lane_quick_replies=fragments.get("lane_quick_replies"),
                # ESCALATION-CONTROL (security review S1): n8n's `/complete` hands over no
                # APPLY state, so the contact's own profile is read here, or a blocked
                # contact would be offered the team and the CS roster on this path.
                profile=(
                    getattr(state, "profile", None)
                    if state is not None
                    else turn_runtime.load_profile(db, str(contact_respond_id or ""))[0]
                ),
            )
            # D9: the caller SENDS; the engine hands it the action to send. Only a lane
            # that ASKED for it gets one: the `/complete` path is n8n's, and n8n composes
            # its own send (the world replays assert exactly that - a tail-composed action
            # there would be a second message). A lane that already knows its text builds
            # the action before the tail instead (S4's clarifier), which is why the
            # `send_message` guard below leaves it alone: one send per turn either way.
            # Built through `_send_actions`, the same builder `_complete_canned_lane` uses,
            # so a CRM-completed business answer carries `result_set` and its
            # `send_attachments` follow-up exactly as an n8n-completed one does.
            if (
                compose_send_action
                and isinstance(reply["text"], str)
                and reply["text"]
                and not any(a.get("kind") == "send_message" for a in prior_actions)
            ):
                prior_actions = [*prior_actions, *_send_actions(reply, dry_run=dry_run)]
            _close_turn(
                db,
                turn_id,
                status="done",
                stage="remembered",
                branch_kind=branch_kind,
                error=None,
                records=turn_trace.persisted(),
                response={
                    **stored_response,
                    "reply": reply,
                    # D15: the duplicate replay must hand back the SAME action list the
                    # first delivery got, so the send rides on the row too.
                    "actions": prior_actions,
                },
            )
        except AppException:
            # The status guard above and anything else that has already NAMED its own
            # HTTP answer. Re-raised untouched: it did not run the tail, so there is
            # nothing to close and the row must keep the state it was refused in.
            raise
        except Exception as exc:  # noqa: BLE001 - a failed tail is recorded, never dropped
            message = f"{type(exc).__name__}: {exc}"
            logger.exception("chatbot turn %s failed in the tail", turn_id)
            turn_trace.record(
                "remembered",
                status="failed",
                summary="The answer could not be finished.",
                why="Something the tail depends on did not produce a usable result.",
                facts={"lane": branch_kind, "dry_run": dry_run},
                error=message,
                raw=None,
            )
            # The TAIL'S OWN session, rolled back first, not a fresh one. `rollback` is
            # what makes it usable again when the failure was a DB error, and is a no-op
            # when it was not (the allowlist raise is pure Python). A fresh session would
            # look tidier and be wrong: under the test fixture every session nests on one
            # connection, so a nested commit is discarded the moment the outer session
            # closes - the close would be reported and then silently undone.
            db.rollback()
            _close_turn(
                db,
                turn_id,
                status="failed",
                stage="remembered",
                branch_kind=branch_kind,
                error=message,
                records=turn_trace.persisted(),
            )
            raise

    # CUSTOMER-ASKS-REFER-ONLY: a reply this tail composed (the casual, canned, escalation
    # and business lanes) that referred the customer is a Customer asks row, the same rule
    # `_run_answer` applies. The row is closed first, as there.
    _record_customer_asks(
        session_factory,
        turn_id=turn_id,
        contact_respond_id=str(contact_respond_id or ""),
        state=state,
        stock_entries=[],
        reply_text=jsc.js_string(reply.get("text") or ""),
        refer_context=None,
        ctx=ctx,
        dry_run=dry_run,
        chat_console=chat_console,
    )

    return CompleteResult(
        turn_id=turn_id,
        reply=reply,
        actions=prior_actions,
        # D14: the would-be patch, so a console or clone turn can be inspected without
        # anything having been written.
        session_patch=session_patch if dry_run else None,
        status="done",
        stage="remembered",
        is_test=dry_run,
    )


def _log_session_write(db: Session, *, turn_id: str, contact_respond_id: str) -> None:
    """AC-206: the session write is logged where n8n's PUT used to be logged.

    Best-effort by the layering rule - a post-commit side effect never raises, because
    the write it describes has already happened and failing here would report a turn that
    did not answer when it did.
    """
    try:
        from app.schemas.integration import IntegrationLogCreate
        from app.services.integration_service import IntegrationLogService

        IntegrationLogService(db).create_integration_log(
            IntegrationLogCreate(
                integration_channel="n8n",
                business_table="respond_contacts.session_vars",
                business_id=turn_id,
                external_reference=contact_respond_id,
                direction="inbound",
                endpoint=f"/api/v1/external/chat/turn/{turn_id}/complete",
                http_method="POST",
                status_code=200,
                status="success",
            )
        )
    except Exception as log_error:  # noqa: BLE001
        logger.warning(
            "Failed to log the chatbot session write for turn %s: %s", turn_id, log_error
        )
