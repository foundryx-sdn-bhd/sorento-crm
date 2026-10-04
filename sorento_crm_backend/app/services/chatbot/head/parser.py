"""The semantic parser call (AC-104, AC-105, R5).

One LLM call per turn. It is the ONLY place in the engine that reads the customer's words
(D11); everything after it is deterministic over structured state.

R5 closes H44 at the source: the call passes a STRICT `json_schema` to the provider, so a
well-formed object is guaranteed whenever the provider answers at all. A provider error, a
timeout, or a response that still fails validation is a FAILED `understood` stage - never a
soft default, never `intent = unknown` routed as if it were a real answer.

**No DB session is held across this call.** The plan's capacity section is explicit about
it and the 96/100-connection incident is the evidence: `resolve_config` collects everything
the call needs, the caller closes its session, the call runs, the caller reopens. The
signature enforces the discipline - nothing here takes a `Session`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.services.ai_prompt_registry import agent_model, render
from app.services.chatbot.contracts import ParserOutputError  # noqa: F401 - re-export
from app.services.chatbot.turn import context as context_mod
from app.services.chatbot.turn import question as question_mod
from app.services.chatbot.turn import task as task_mod

logger = logging.getLogger(__name__)

PROMPT_KEY = "chatbot_semantic_parser"

# The parser's own error reply. ONE declaration, in `app/services/chatbot_reply_copy.py`
# with the rest of the bot's fixed sentences, because the ENDPOINT sends the same string
# when the TAIL fails and it cannot import this package (AC-002).
from app.services.chatbot_reply_copy import CHATBOT_TURN_ERROR_REPLY as PARSER_ERROR_REPLY

PARSER_MAX_TOKENS = 2048

# Bounded in `llm_call.chat`, not here (CHATBOT-QUEUE-FIX, prod turn a45f spent 230 s in
# this call): every attempt carries `llm_call.ATTEMPT_TIMEOUT_SECONDS` with the SDK's own
# retries off, the whole call stops at `llm_call.CALL_DEADLINE_SECONDS`, and a timeout is
# raised as `llm_call.TimedOut`, a `RateLimited`, so it lands in the `rate_limited` arm
# below and the dealer reads `RATE_LIMITED_REPLY`.


class ParserError(RuntimeError):
    """The parse could not be completed. The caller fails the `understood` stage.

    `usage` is what the provider billed for the attempt, when it got far enough to bill
    anything: a truncated or non-JSON emission still costs tokens, and a spend the table
    cannot see is the whole reason the usage row exists. Empty when the call never
    returned (transport failure).
    """

    usage: dict[str, Any]

    def __init__(
        self,
        *args: object,
        usage: dict[str, Any] | None = None,
        rate_limited: bool = False,
    ) -> None:
        super().__init__(*args)
        self.usage = usage or {}
        # PR #1247 round 6, ruling 3: every attempt was refused with a 429, so the
        # dealer is told `llm_call.RATE_LIMITED_REPLY`, not the generic parser error.
        self.rate_limited = rate_limited


@dataclass(frozen=True)
class ParserConfig:
    """Everything the LLM call needs, resolved BEFORE the session is released."""

    system_prompt: str
    prompt_version: int | None
    provider: str
    model: str
    api_key: str


def _build_json_schema() -> dict[str, Any]:
    """The strict 29-key `ParseOutput` schema the provider is held to (AC-105).

    26 top-level keys, and `routing` carries exactly two members, is what the LIVE
    parser emits: every one of the 488 captured raw emissions has that shape. Growth r1
    (AC-909 / AC-910) adds `group_by` and `top_n`, which no captured emission carries -
    see their own comment below, and `output_exchange._EXEMPT_FROM_REQUIRED` for how a
    pre-growth-r1 emission still post-processes. The sales report slice adds a 29th,
    `sales_channel`, exempted the SAME way and for the SAME reason - see its own
    comment below.

    Built from the prompt's own OUTPUT block. `additionalProperties: false` is what makes
    "exactly these keys, no others" a provider guarantee instead of an instruction, and
    every key is `required` so a silently missing one is a validation failure rather than
    a `None` that reads downstream as "the customer said nothing about it".

    Value types stay permissive (`type: [...]` unions rather than enums) on purpose: the
    parser legitimately emits values the enum does not cover yet, `output_exchange`
    normalises several of them (`"null"` to null, a compound access level to a tier
    token), and rejecting them at the provider would fail turns that work today. The
    vocabularies in `contracts.py` are what the code is written against; this schema is
    what the WIRE is held to.
    """
    string_or_null = {"type": ["string", "null"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "message_type": {"type": "string"},
            "intent_hint": string_or_null,
            "domain_hint": string_or_null,
            "scope_intent": string_or_null,
            "is_affirmative": {"type": ["boolean", "null"]},
            "user_goal": string_or_null,
            # AC-1317: true when the message asks for more of the set the LAST answer
            # counted ("more", "next", "lagi", ...). Declared and still emitted, but
            # nothing pages on it any more (owner ruling, 26 Sep 2026: no paging); the
            # answer to "how many should I show?" is `top_n`, read by
            # `turn/apply.py::_named_count`.
            "continuation": {"type": ["boolean", "null"]},
            "access_levels": {"type": "array", "items": {"type": "string"}},
            "broaden_axis": string_or_null,
            # HOW FAR that axis is widened (owner ruling, 17 Sep 2026): "family" widens
            # the picked variant to every variant of its family, "all" drops the axis
            # altogether, null is no widening asked. The axis alone could not tell "all
            # variants of 286" from "for all products", and the engine read it as
            # neither - "okay nvm for all products" was answered for the one variant the
            # question already carried (turns 6095ce66 / d8ab659e).
            "broaden_to": string_or_null,
            "date_mode": string_or_null,
            "date_filter_start": string_or_null,
            "date_filter_end": string_or_null,
            "match_mode": string_or_null,
            "demand_qty": {"type": ["number", "string", "null"]},
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "raw": string_or_null,
                        "hint": string_or_null,
                        "canonical_code": string_or_null,
                        "current_message": {"type": ["boolean", "null"]},
                        "confident": {"type": ["boolean", "null"]},
                        # Turn re-architecture (AC-1506): the entity KIND hint's own
                        # confidence, separate from `confident` above (the entity's
                        # identity). Reconciliation (S2) asks the resolver for the
                        # hinted kind FIRST only when this is true; a low-confidence
                        # kind hint goes straight to reconciliation instead.
                        "hint_confident": {"type": ["boolean", "null"]},
                        # #1262 slice 5 (F4), owner ruling 1: the parser OWNS quantity -
                        # a leading/trailing "xN"/"N pcs" beside a product is this
                        # entity's own count, never folded into `raw`/`canonical_code`
                        # and never regex-stripped back out of them downstream. `null`
                        # is "no quantity said" (every entity before this slice).
                        #
                        # Ported from PR #1118 (feat/chatbot-dealer-stock-verdict, not
                        # merged, owner ruling 24 Sep 2026) for chatbot-stock-ask-v2
                        # S3, D13: the quantity the message stated FOR THIS ENTITY.
                        # `demand_qty` below is one number for the whole turn and
                        # cannot answer "MWT5727SS-CR 5, MHS1028 60" at all, which is
                        # why an `availability` dealer was asked "how many units do
                        # you need?" and never answered. REQUIRED like every other key
                        # of this object: strict mode rejects a `properties` key
                        # absent from `required`, and a key the provider is never
                        # forced to reason about is a key it never fills.
                        "quantity": {"type": ["number", "null"]},
                        # Fix round 8 on PR #833 (owner retest of round 7): a
                        # `specification` entity names its registry key and the choice
                        # (or number) the words mean; null on every other kind. The
                        # vocabulary is the policy block's Specification lines, and
                        # `head/grounding.py` checks both against the registry after the
                        # parse. Required like every other key: strict mode.
                        "spec_key": string_or_null,
                        "spec_value": {"type": ["string", "number", "boolean", "null"]},
                        # ACCOUNT-LEDGER: the numbered account the message names for a
                        # customer ("Soon Heng account 1" -> 1); null otherwise. Required
                        # like every key here (strict mode).
                        "account": {"type": ["integer", "null"]},
                    },
                    "required": [
                        "raw",
                        "hint",
                        "canonical_code",
                        "current_message",
                        "confident",
                        "hint_confident",
                        "quantity",
                        "spec_key",
                        "spec_value",
                        "account",
                    ],
                },
            },
            "entity_op": string_or_null,
            # Does THIS message name a domain or a status word of its own (owner ruling,
            # 17 Sep 2026)? It is the discriminator between a NEW ASK and a REFINEMENT,
            # and it replaces `scope_exclusive` (item 2, captain ruling, 17 Sep 2026:
            # removed from the schema, the prompt and this parser entirely - it asked
            # the wrong question of the two turns it was written for: "outstanding DO
            # for 7445" and "for 7445" both name a product under an order subject, and
            # only the first is a new question).
            "domain_in_message": {"type": ["boolean", "null"]},
            "requested_attributes": {"type": "array", "items": {"type": "string"}},
            "contains_flyer": {"type": ["boolean", "null"]},
            "reference_positions": {"type": "array", "items": {"type": "number"}},
            "reference_target": string_or_null,
            "person_mention": string_or_null,
            "is_active": {"type": ["boolean", "string", "null"]},
            "order_status": string_or_null,
            # Growth r1 (AC-909 / AC-910). The two keys every list tool this plan touches
            # takes uniformly: which axis to break the answer down by, and how many rows
            # were asked for. ENUM rather than the permissive `string_or_null` the older
            # keys use, and that is safe HERE for the reason the docstring gives for the
            # others being permissive: those keys have live emissions the enum would have
            # to cover, and these two have none - no prompt version before
            # `490_chatbot_parser_growth_r1` asks for them, so the enum cannot reject a
            # value a working turn already produces. `output_exchange` exempts both from
            # its required-key check, so a captured emission that predates them still
            # post-processes with each reading as null.
            "group_by": {
                "type": ["string", "null"],
                "enum": [
                    "customer",
                    "transporter",
                    "date",
                    "product",
                    "warehouse",
                    "supplier",
                    # PLAN-retail-sales-reports-26sep S1: the sales analysis's axes.
                    "month",
                    "year",
                    None,
                ],
            },
            "top_n": {"type": ["integer", "null"]},
            # PLAN-chatbot-sales-report.md S4 wiring point 1 (captain ruling 4, corrected
            # 19 Sep): the sales report's channel filter. `additionalProperties: false`
            # means the provider is called in STRICT mode (`llm_provider.py`'s `strict:
            # True`), and strict mode rejects a `properties` key absent from `required` -
            # so this key MUST be in the `required` list below, exactly like `group_by` /
            # `top_n`, and is exempted from the post-processor's OWN required-key check
            # the same way: `output_exchange._EXEMPT_FROM_REQUIRED`. A published OLDER
            # prompt version that never emits this key still post-processes with it
            # reading as null; the field is genuinely conditional (emitted only on a
            # sales report ask) either way.
            "sales_channel": {"type": ["string", "null"], "enum": ["dealer", "project", None]},
            # PLAN-chatbot-top-x-hot-selling-24sep.md "Parser (S4)": the top selling ask's
            # three axes, each nullable and each an ENUM for the reason `group_by` gives
            # (no earlier prompt version emits them, so no working turn produces a value
            # outside the set). Required for strict mode, exempted from the recorded
            # emissions' check through `TOLERATED_ABSENT`, exactly like `sales_channel`.
            # `unclear` is the parser saying the message could mean either: the lane asks
            # (owner rulings 26 Sep 2026: never assume the basis or the grain). A null
            # `rank_by` is asked too (no default metric).
            "rank_by": {"type": ["string", "null"], "enum": ["quantity", "amount", None]},
            "basis": {"type": ["string", "null"], "enum": ["delivered", "ordered", "unclear", None]},
            "rank_group": {"type": ["string", "null"], "enum": ["item", "category", "unclear", None]},
            # Fix lane round 4 (owner retest, 27 Sep 2026): "cold selling", "least sold"
            # rank ascending. Same shape and same exemption as the three above.
            "rank_direction": {"type": ["string", "null"], "enum": ["top", "bottom", None]},
            # PLAN-po-spo-warehouse-29sep S4: the PO/SPO sort axis. Same shape and same
            # reason as `rank_direction`: an enum, required for strict mode, tolerated absent.
            "sort_by": {
                "type": ["string", "null"],
                "enum": [
                    "date",
                    "expected_date",
                    "quantity",
                    "outstanding",
                    "received_date",
                    "received_quantity",
                    "product",
                    "supplier",
                    None,
                ],
            },
            "sort_dir": {"type": ["string", "null"], "enum": ["asc", "desc", None]},
            # PLAN-retail-sales-reports-26sep S1: the sales analysis's basis and the
            # company named. Required for strict mode and tolerated absent, exactly as
            # `sales_channel` above (no recorded emission carries them).
            "sales_basis": {"type": ["string", "null"], "enum": ["ordered", "delivered", "invoiced", None]},
            "sales_company": string_or_null,
            "correction": {"type": ["boolean", "null"]},
            # PLAN-chatbot-customer-scope-29sep D2: "my" / "me" / "our" as the asker's own
            # account. Required for strict mode, tolerated absent (older prompt versions).
            "self_reference": {"type": "boolean"},
            "routing": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    # `string_or_null`, NOT an enum of `SUGGESTED_TEAMS`, and from
                    # 7 Sep 2026 that is a CONTRACT rather than the general permissiveness
                    # the docstring above describes (owner rules R-a / R-c, console pass
                    # 4). The prompt asks for an enum member when the customer's team word
                    # maps to exactly one, and for the customer's OWN word, verbatim and
                    # lowercased, when it maps to several ("marketing" - three teams) or to
                    # none ("sales"). `null` therefore means one thing only: the customer
                    # named no team.
                    #
                    # That is what lets `lanes/escalation._person_routing` tell "escalate
                    # to marketing" (ask which of the three) from "I want to talk to a
                    # human" (assign the default) without reading either message, which
                    # D11 forbids. Tightening this to an enum would delete the
                    # discriminator and take the H64 defect back.
                    "suggested_team": string_or_null,
                    "suggested_agent": string_or_null,
                },
                # NO `team_source`. It is not a live key: the live `sub-semantic-parser`
                # system message never asks for it and not one of the 488 captured
                # emissions carries it. It belongs to the UNPROMOTED B-TEAM-1' lane change
                # (plan, S1 "pending re-port"), and declaring it `required` here would make
                # the CRM the only deployment that forces the model to invent one.
                "required": ["suggested_team", "suggested_agent"],
            },
            "escalation": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "is_escalation_confirmation": {"type": ["boolean", "null"]},
                    "company_pick": string_or_null,
                },
                "required": ["is_escalation_confirmation", "company_pick"],
            },
            # Turn re-architecture (AC-1506): the v3 shape's three new top-level keys.
            # `document` is a LIST of document kinds ("DO", "SO") or null/empty for
            # "no document named" - never a third "both" value (PLAN's own framing).
            # The papers the message named, from a CLOSED set: `turn/apply.py::
            # DOMAIN_BY_DOCUMENT` and `turn_runtime._DOCUMENT_STATUS_TO_ORDER_STATUS`
            # both key off these exact five codes, so a free string here is a document
            # nothing downstream can read. Declared as an enum so the provider cannot
            # emit one (hand pass 2 item 12: "Outstsnding DO for 7445" came back with
            # `document: []` and the CRM asked which document the message had named).
            "document": {
                "type": ["array", "null"],
                "items": {"type": "string", "enum": ["SO", "DO", "PO", "SPO", "GRN"]},
            },
            # The delivery/order status axis - "outstanding", "delivered", or null.
            # Replaces the old flat `order_status` key on the OUTPUT side too, kept
            # above only because live emissions before this prompt version still carry
            # it (`output_exchange._EXEMPT_FROM_REQUIRED`-style tolerance).
            "status": string_or_null,
            # The SECOND domain a message names, and every one after it (AC-1522,
            # contract 122). Declared as a list of ASK OBJECTS rather than bare domain
            # codes because that is the shape every reader already speaks -
            # `turn/apply.py` reads `a["domain"]` at three sites and the committed S2/S3
            # fixtures build `[{"domain": ..., "intent": ...}]` - and one wire shape for
            # one fact is worth more than a shorter one nothing reads.
            #
            # `domain` is a free string for the same reason `domain_hint` is: the domain
            # set lives in `chatbot_domains`, the owner edits it through the Chatbot
            # Domains screen, and the closed set is taught by the rendered domain block in
            # the prompt body - an enum here would freeze the schema against that table.
            #
            # `null` (or []) for the ordinary one-domain message, which is nearly every
            # message: this key exists for "incoming and stock for 7445", where answering
            # one half is answering half the question.
            "asks": {
                "type": ["array", "null"],
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "domain": {"type": "string"},
                        "intent": string_or_null,
                    },
                    "required": ["domain", "intent"],
                },
            },
            # The customer changed subject (AC-1525, AC-1546): `turn/apply._focus_rules`
            # empties every focus axis but the contact's own tier and brand, and
            # `engine.run_turn` closes the conversation episode on it. Both readers shipped
            # with no way for the model to set it, so no live turn has ever reset a topic
            # or written an episode.
            "topic_reset": {"type": ["boolean", "null"]},
            # Ported from PR #1118 (not merged), D13/D15: "go ahead without answering
            # the open question" - said in any wording, in any language ("just
            # proceed", "never mind those, check what you have"). Generic, not
            # stock-specific: it is the one word that closes ANY open task with what
            # it already holds.
            "proceed_anyway": {"type": ["boolean", "null"]},
            # PR #1247 round 8 (owner console test of round 7, 26 Sep 2026): the
            # parser's own answer to the "Open question:" object the user block states,
            # so a reply to the stock quantity question ("10 / 20 / 30", the list pasted
            # back with blanks, "that's it", "3 for all of them") comes back as declared
            # slots instead of being guessed from its shape by one rule per phrasing.
            # Fixed keys and a list of fixed-shape items: strict-schema safe. Always an
            # object; `mode` null is "this message does not answer it". Read first by
            # `turn/apply.py::_open_question_answer`, the shape rules only as fallback.
            # Round 9 (issue #1293): the SAME object answers every kind of question the
            # bot asks (`turn/question.py`), so a pick, a yes or a no is declared here
            # too, with the positions picked and any quantity stated in the same breath
            # ("the first one, I need 2").
            "open_question_answer": {
                "type": "object",
                "additionalProperties": False,
                "description": (
                    "The answer to the user block's 'Open question:' object, else mode null."
                ),
                "properties": {
                    "mode": {
                        "type": ["string", "null"],
                        "enum": ["pick", "yes", "no", "fill", "all", "done", "cancel", None],
                        "description": (
                            "pick: the options in picked (and items by code), with any "
                            "quantity in items or qty_for_all; yes / no: a confirm "
                            "answered, no also for none of the options; fill: "
                            "quantities for some lines; done: these lines (or none) then "
                            "answer now, blanks skipped; all: qty_for_all for every line; "
                            "cancel: drop the question; null: not an answer to it."
                        ),
                    },
                    "picked": {"type": "array", "items": {"type": "integer"}},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "position": {"type": ["integer", "null"]},
                                "code": string_or_null,
                                "qty": {"type": ["number", "null"]},
                            },
                            "required": ["position", "code", "qty"],
                        },
                    },
                    "qty_for_all": {"type": ["number", "null"]},
                },
                "required": ["mode", "picked", "items", "qty_for_all"],
            },
            # LOWSTOCK-SEMANTIC (owner, 4 Oct 2026): everything the low stock report's
            # filters need, read by the parser and nowhere else - the lane only resolves
            # these words against the master data (`lanes/business/low_stock_ask.py`).
            # Always an object (strict mode), empty unless intent_hint is
            # `low_stock_report`. `group_by` is an ENUM: brand / warehouse are named so
            # the lane can ASK (the workbook splits only by supplier and category),
            # never so a value it cannot run is guessed into one it can.
            "low_stock": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "group_by": {
                        "type": ["string", "null"],
                        "enum": ["supplier", "category", "supplier_category", "brand", "warehouse", "none", None],
                    },
                    "categories": {"type": "array", "items": {"type": "string"}},
                    "all_categories": {"type": ["boolean", "null"]},
                    "brands": {"type": "array", "items": {"type": "string"}},
                    "suppliers": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["group_by", "categories", "all_categories", "brands", "suppliers"],
            },
            "anaphora": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    # True when the message refers BACKWARD to something outside this
                    # turn's own focus/pending (AC-1547) - the ONE signal that arms a
                    # recall re-parse behind the contact's `chatbot_recall_enabled` flag.
                    "backward_reference": {"type": ["boolean", "null"]},
                },
                "required": ["backward_reference"],
            },
            # Chatbot memory lane A (contract section 6.5, round 3): set only when the
            # dealer states something about THEMSELVES ("I'm the purchaser", "reply in
            # Malay") - `turn/apply.py` applies each entry as a `stated` fact through
            # `profile_facts.apply_statement`, in the tail. A LIST (round 3 renames the
            # singular `profile_statement`) because one message can state more than one
            # fact ("I'm in Penang and I prefer Malay"), capped at 3 (`maxItems`) so one
            # turn cannot flood the profile. ENUM'd `key` (unlike the free-string
            # `domain_hint` et al.) because this vocabulary is closed and small, and a
            # value outside it is dropped by APPLY with a trace line rather than read as
            # a fact (contract section 6.5's own six keys - `about` included, per the
            # vocabulary table in section 4).
            "profile_statements": {
                "type": ["array", "null"],
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "key": {
                            "type": "string",
                            "enum": ["language", "role", "usual_brands", "usual_sites", "project", "about"],
                        },
                        "value": {"type": ["string", "array"], "items": {"type": "string"}},
                    },
                    "required": ["key", "value"],
                },
            },
        },
        "required": [
            "message_type",
            "intent_hint",
            "domain_hint",
            "scope_intent",
            "is_affirmative",
            "user_goal",
            "continuation",
            "access_levels",
            "broaden_axis",
            "broaden_to",
            "date_mode",
            "date_filter_start",
            "date_filter_end",
            "match_mode",
            "demand_qty",
            "entities",
            "entity_op",
            "domain_in_message",
            "requested_attributes",
            "contains_flyer",
            "reference_positions",
            "reference_target",
            "person_mention",
            "is_active",
            "order_status",
            "group_by",
            "top_n",
            "sales_channel",
            "rank_by",
            "basis",
            "rank_group",
            "rank_direction",
            "sort_by",
            "sort_dir",
            "sales_basis",
            "sales_company",
            "correction",
            "self_reference",
            "routing",
            "escalation",
            "document",
            "status",
            "asks",
            "topic_reset",
            "proceed_anyway",
            "open_question_answer",
            "anaphora",
            "profile_statements",
            "low_stock",
        ],
    }


PARSE_OUTPUT_JSON_SCHEMA = _build_json_schema()
PARSE_OUTPUT_SCHEMA_NAME = "chatbot_parse_output"
# The keys the schema declares. Validation is "every required key present, nothing
# unknown kept" - unknown keys are IGNORED (the risk the plan names: a model that
# occasionally adds one must not fail a turn), missing ones are REJECTED.
DECLARED_KEYS: frozenset[str] = frozenset(PARSE_OUTPUT_JSON_SCHEMA["required"])

#: Declared keys a RECORDED emission may lack. The live parser always emits every
#: declared key (structured output with `additionalProperties: false` requires it), but
#: the replay corpus and the console cases were captured BEFORE these keys existed, and
#: a harness value is held to the same check a provider answer is (`assert_emission`).
#: Absent reads as null everywhere, so an old recording behaves exactly as it did.
#: A key leaves this set when the corpus has been re-recorded with it.
#: `sales_channel` joins them for the SAME reason and by the SAME rule the sales report
#: lane states for the retired `output_exchange._EXEMPT_FROM_REQUIRED` (the pre-rearch
#: home of this set): strict mode rejects a `properties` key absent from `required`, so
#: it HAS to be declared at the wire, and no prompt version before the sales report
#: addendum ever emits it - so every recorded emission and every `mock_reformulator_
#: output` a console case carries lacks it, and reads as null.
#: The top selling keys (`rank_by`, `basis`, `rank_group`, `rank_direction`) join for
#: the same reason.
#: `proceed_anyway` joins them by the SAME rule (ported from PR #1118, not merged,
#: D13): it has to be declared at the wire to exist at all, and no prompt version
#: before this lane's addendum emits it, so every recorded emission and every
#: `mock_reformulator_output` a console case carries lacks it and reads as null.
#: `entities[].quantity` needs no tolerance - this check is TOP-LEVEL keys only, and a
#: nested entity field is validated by the provider's own strict schema, never
#: against a replay emission.
#: `open_question_answer` joins them by the same rule (PR #1247 round 8): no recorded
#: emission and no console case carries it, and absent reads as mode null.
TOLERATED_ABSENT: frozenset[str] = frozenset(
    {
        "broaden_to",
        "domain_in_message",
        "sales_channel",
        "rank_by",
        "basis",
        "rank_group",
        "rank_direction",
        "sort_by",
        "sort_dir",
        "sales_basis",
        "sales_company",
        "proceed_anyway",
        "open_question_answer",
        "self_reference",
        "profile_statements",
        # LOWSTOCK-SEMANTIC: no recorded emission and no prompt version before
        # `lss_0001_parser_vocab` carries it; absent reads as "nothing placed".
        "low_stock",
    }
)


def resolve_config(
    db: Session, *, current_date: str, override_version_id: str | None = None
) -> ParserConfig:
    """Read the prompt, the per-key model override and the API key. Session-bound.

    Call this, then CLOSE the session, then call `parse`. Everything that needs the
    database happens here so nothing needs it while the provider is answering.

    `override_version_id` runs a specific prompt VERSION instead of the published one, and
    the name is `ai_prompt_registry.render`'s own so there is one word for one thing all
    the way down. It exists for the Prompts screen's "Run a turn" test (AC-807), which is
    dry-run only: the engine refuses to read it off a live envelope, so a customer can
    never be answered by an unpublished prompt.
    """
    from app.services.ai_assistant_service import AIAssistantConfigService
    from app.services.llm_provider import resolve_api_key

    system_prompt, prompt_version = render(
        db, PROMPT_KEY, current_date=current_date, override_version_id=override_version_id
    )
    provider_override, model_override = agent_model(db, PROMPT_KEY)

    config = AIAssistantConfigService(db).get()
    if config is None:
        raise ParserError("AI assistant configuration is not set")
    provider = provider_override or config.provider
    model = model_override or config.model
    api_key = resolve_api_key(config, provider)
    if not api_key:
        raise ParserError(f"no API key configured for provider {provider!r}")
    return ParserConfig(
        system_prompt=system_prompt,
        prompt_version=prompt_version,
        provider=provider,
        model=model,
        api_key=api_key,
    )


def _subject_names(rows: Any) -> list[str]:
    """The human names on one focus axis, in order, deduped.

    A focus row is either an entity dict (products, customers) or a bare code (document,
    tier). The NAME is what a customer would recognise, the raw token is what they typed,
    and the canonical code is the last resort: a subject line saying "300-H030" tells the
    model less about the conversation than "hanlim" does.
    """
    names: list[str] = []
    for row in rows or []:
        value = (
            (row.get("name") or row.get("raw") or row.get("canonical_code"))
            if isinstance(row, dict)
            else row
        )
        text = str(value).strip() if value is not None else ""
        if text and text not in names:
            names.append(text)
    return names


def current_subject_line(focus: Any) -> str | None:
    """"Current subject: ..." - what the conversation is about, on one line, or None.

    Owner ruling, hand pass 3 (17 Sep 2026): the parser judged a refinement ("For
    srtwc286 only") and a domain switch ("promo") against the previous REPLY alone, which
    says what was answered and not what it was answered about, so a message naming an
    entity of a kind the open roster was not about re-asked the roster. The focus is the
    one place that fact lives, so it is stated.

    Absent for an empty focus, which keeps every other turn's block byte-identical.
    """
    if focus is None:
        return None
    parts: list[str] = []
    for label, rows in (
        ("domain", getattr(focus, "domains", None)),
        ("customer", getattr(focus, "customers", None)),
        ("product", getattr(focus, "products", None)),
        ("document", getattr(focus, "document", None)),
    ):
        names = _subject_names(rows)
        if names:
            parts.append(f"{label} {', '.join(names)}")
    status = getattr(focus, "status", None)
    if isinstance(status, str) and status.strip():
        parts.append(f"status {status.strip()}")
    window = getattr(focus, "date_window", None)
    if isinstance(window, dict):
        bounds = [str(window.get(key)).strip() for key in ("start", "end") if window.get(key)]
        if bounds:
            parts.append("dates " + " to ".join(bounds))
    if not parts:
        return None
    return "Current subject: " + "; ".join(parts) + "."


def build_user_block(
    *,
    previous_response: Any,
    latest_user_message: Any,
    pending_kind: str | None,
    pending_options: list[str] | None = None,
    profile_block: str | None = None,
    episodes_block: str | None = None,
    focus: Any = None,
    brands: list[dict[str, Any]] | None = None,
    open_question: dict[str, Any] | None = None,
    recent_exchanges: list[tuple[str, str]] | None = None,
) -> str:
    """The user turn, in the same two lines the n8n `AI Agent` node sends.

    The ONE addition S1 makes (and the only prompt change allowed before S1b): the
    persisted `pending` marker, stated as a fact rather than left for the model to infer
    from the previous reply's wording (R3, D11). The legacy string is still present in
    `previous_response`, so a session written by n8n and one written by the CRM both
    parse the same way during the migration window.

    `pending_options` is the second (D17, 13 Sep 2026): the numbered options of an open
    question whose answer is a POSITION, so the parser can resolve a worded answer
    against what was actually offered. Omitted, and the block is unchanged.

    `focus` is the third (hand pass 3, 17 Sep 2026): the "Current subject" line, so a
    refinement and a domain switch are read against what the conversation is about rather
    than against the previous reply alone.

    `brands` (#1262 slice 9, F1a): the live `Brand` rows for the contact's own
    companies, read fresh by the caller every turn (no cache) - one `Known
    brands:` line, `name (code)` pairs, deduped by name across companies, dropped
    whole when the read comes back empty.

    The fourth is the OPEN TASK lines (ported from PR #1118, not merged, D21): one
    `Open task: ...` line per task the focus carries, most recently touched first, so
    the model can fill a quantity for a product the task named whatever the current
    subject is, and can read "add", "drop", "make B 80", "proceed" and "never mind
    the stock check" as instructions on that task.

    The fifth and sixth are PR #1247 round 8 (owner console test of round 7, 26 Sep
    2026: "is the parameter from the parser too less already, is the context too less
    already?"): the stock question as a structured `Open question:` object
    (`task.open_question`), which the parser answers in `open_question_answer`, and the
    last three exchanges, so a short reply is read against what was actually asked.
    Both omitted, and the block is unchanged.
    """
    import re

    previous = re.sub(
        r"^Previous turn \([a-z_]+\)", "Previous turn", str(previous_response or ""), flags=re.I
    )
    lines = [
        f"Previous response: {previous}",
        f"Current user message: {latest_user_message}",
    ]
    subject = current_subject_line(focus)
    if subject:
        # Hand pass 3, ruling 2: the subject the conversation already has, so a refinement
        # and a domain switch are judged against something. One line, omitted whole when
        # the focus is empty.
        lines.append(subject)
    lines.extend(open_task_lines(focus, open_question))
    question_line = open_question_line(open_question)
    if question_line:
        lines.append(question_line)
    if pending_kind:
        lines.append(f"Pending: the assistant is waiting for a {pending_kind} reply.")
    if pending_options:
        # D17 (owner design ruling, 13 Sep 2026): deterministic code never reads words.
        # A question whose answer is a POSITION against a stored roster states that
        # roster here, so the parser can map "the DO list" or "all" onto an option the
        # assistant actually offered - and so the head only ever has to map the number
        # back. Absent for every other turn, which keeps their block byte-identical.
        lines.append("Open question options: " + "; ".join(pending_options))
    if profile_block:
        # AC-1548: what the system already knows about this contact - tier, language,
        # default ledgers - stated on EVERY parse, so the model never asks for a fact the
        # profile already holds (journey A's own rule: nothing already known is re-asked).
        lines.append(profile_block)
    if episodes_block:
        # AC-1547: the recalled frames, on the SECOND parse of a turn that pointed
        # backwards. Absent on every other turn, which keeps their block unchanged.
        lines.append(episodes_block)
    brands_line = context_mod.known_brands_line(brands)
    if brands_line:
        # #1262 slice 9 (F1a): one line, deduped by name (`context.known_brands_line`,
        # shared with the memory lane's assembler so both print the same line).
        lines.append(brands_line)
    exchanges = context_mod.recent_exchange_lines(recent_exchanges, previous)
    if exchanges:
        lines.append("Recent exchanges, oldest first:")
        lines.extend(line for pair in exchanges for line in pair)
    return "\n".join(lines)


def open_task_lines(focus: Any, open_question: dict[str, Any] | None) -> list[str]:
    """The `Open task: ...` lines (ported from PR #1118, D21), most recently touched
    first. Shared by `build_user_block` and the memory lane's `context.assemble`
    caller, so both print the same lines."""
    return list(
        task_mod.hint_lines(
            getattr(focus, "tasks", None),
            # Round 9: a pick or an offer is not the stock question, so the task's own
            # line still prints under it.
            open_question_shown=question_mod.is_the_stock_question(open_question),
        )
    )


def open_question_line(open_question: dict[str, Any] | None) -> str | None:
    """PR #1247 round 8: the one question on the table as its `Open question: {...}`
    line, or None when there is none."""
    if not open_question:
        return None
    return "Open question: " + json.dumps(open_question, separators=(",", ":"), ensure_ascii=False)


#: Round 8's exchange cap and line helper now live in `turn/context.py` (the assembler
#: prints the same lines); re-exported under their old names for existing readers.
EXCHANGE_TEXT_CAP = context_mod.EXCHANGE_TEXT_CAP
_exchange_text = context_mod.exchange_text


class ParsedOutput(dict):
    """The parser's emission, with what the call COST hanging off it as `usage`.

    A dict, not a `(parsed, usage)` pair, and deliberately: `parse` is the seam half a
    dozen tests replace with a two-line stub returning a plain dict, and every reader of
    the emission reads it by key. Renaming the seam for one telemetry number would make
    the stubs lie about the shape. `getattr(out, "usage", {})` is the whole contract, and
    a stub that does not carry one is a legitimate answer: no call, no cost.
    """

    usage: dict[str, Any]

    def __init__(self, parsed: dict[str, Any], usage: dict[str, Any] | None = None) -> None:
        super().__init__(parsed)
        self.usage = usage or {}


def assert_emission(emission: dict) -> None:
    """Every key the schema declares is present, or this is not an emission.

    ONE rule, TWO callers, because a verdict reaches the engine two ways and both of them
    used to answer this question differently: `parse` below, for what a provider returned,
    and `engine.run_turn`'s harness bypass, for what an operator's
    `mock_reformulator_output` supplied. The bypass had no check at all beyond "is a
    non-empty dict", so `{"nope": true}` - the real 5 Sep 2026 production case - routed a
    whole turn off defaults and came back `done`, with entity resolution running on a
    token nobody typed. R5 / H44: a failed understanding is a FAILED TURN at `understood`,
    never a soft default.

    The message names every missing key at once (wording kept from `_assert_emission`, the
    fix this restores: a bare `KeyError: 'reference_positions'` read as a CRM fault and
    said nothing about what the model got wrong), so a bad mock or a prompt regression is
    fixed in one pass instead of one key per run.
    """
    missing = sorted(DECLARED_KEYS - TOLERATED_ABSENT - set(emission))
    if missing:
        raise ParserError(
            "parser emission missing " + ", ".join(repr(key) for key in missing)
        )


def parse(config: ParserConfig, user_block: str) -> ParsedOutput:
    """One structured-output call. Raises `ParserError`; never returns a default.

    The return is the parser's 26 keys; `.usage` on it carries provider, model and token
    counts. Both have a reader: the `understood` trace facts (so an operator can see what
    a turn cost without leaving the screen) and `ai_assistant_usage_logs` (so the
    chatbot's spend lands in the same table every other LLM call here reports to).

    NOTHING here touches the database. That is the rule the capacity section states and
    the reason `ParserConfig` exists.
    """
    from app.services.chatbot import llm_call

    messages = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": user_block},
    ]
    try:
        # A rate limit is waited out inside the call (PR #1247 round 6, ruling 3).
        result = llm_call.chat(
            config.provider,
            config.api_key,
            config.model,
            messages,
            temperature=0.0,
            max_tokens=PARSER_MAX_TOKENS,
            json_schema=PARSE_OUTPUT_JSON_SCHEMA,
            json_schema_name=PARSE_OUTPUT_SCHEMA_NAME,
        )
    except llm_call.RateLimited as exc:
        raise ParserError(f"parser provider call failed: {exc}", rate_limited=True) from exc
    except Exception as exc:  # noqa: BLE001 - provider/transport failure is a failed stage
        raise ParserError(f"parser provider call failed: {exc}") from exc

    usage = {
        "provider": config.provider,
        "model": config.model,
        "prompt_tokens": int(getattr(result, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(result, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(result, "total_tokens", 0) or 0),
    }
    try:
        content = (result.content or "").strip()
        if not content:
            # An empty structured emission (e.g. an Anthropic max_tokens truncation with no
            # tool_use) would validate as `{}` and route confidently on nothing.
            raise ParserError("parser returned empty content")
        try:
            parsed = json.loads(content)
        except Exception as exc:  # noqa: BLE001
            raise ParserError(f"parser returned non-JSON content: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ParserError("parser returned a non-object")
        assert_emission(parsed)
    except ParserError as exc:
        # The provider answered, so it billed. The turn fails either way; the spend is
        # still real and still has to reach `ai_assistant_usage_logs`.
        exc.usage = usage
        raise
    return ParsedOutput(parsed, usage)
