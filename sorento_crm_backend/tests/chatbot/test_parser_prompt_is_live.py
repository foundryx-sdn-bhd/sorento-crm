"""The parser prompt fallback IS the live n8n system message, not the export (AC-104).

S1 originally vendored the working-tree EXPORT of `sub-semantic-parser`. That export is
flagged `locally_edited` in its own MANIFEST and carries an UNPROMOTED lane change
(B-TEAM-1'), so it is 49,318 characters where the LIVE body is 46,942. A CRM turn must
parse the way a live n8n turn parses, so the live body is the only correct source.

Nothing here reaches n8n. The guard is a sha256 recorded beside the constant plus a set of
properties that only hold for the live text, so a future edit that reintroduces the export
(or quietly rewrites the fallback) fails here rather than in production.

The full live file is not vendored into this repo: it is 46 KB of prompt that already
exists once, as the constant. What IS pinned is its hash and the two mechanical edits that
derive the constant from it, so the derivation is reproducible by anyone holding the file.
A third, content-only edit (migration `487_chatbot_warehouse_cue`) sits inside the
requested-attributes block and is deliberately excluded from that reproducibility check -
see `test_the_constant_is_reproducible_from_the_live_file`'s own docstring.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from app.services.chatbot_parser_prompt import (
    ESCALATION_CONFIRMATION_ADDENDUM,
    SELF_REFERENCE_ADDENDUM,
    GROWTH_R1_ADDENDUM,
    KNOWN_BRANDS_ADDENDUM,
    LAST_COST_ADDENDUM,
    LIVE_SYSTEM_MESSAGE_SHA256,
    LOW_STOCK_ADDENDUM,
    ACCOUNT_LEDGER_ADDENDUM,
    MEMORY_ADDENDUM,
    PO_SPO_WAREHOUSE_ADDENDUM,
    QUANTITY_ADDENDUM,
    SALES_ANALYSIS_ADDENDUM,
    SALES_REPORT_ADDENDUM,
    SPECIFICATION_ADDENDUM,
    STOCK_TASK_ADDENDUM,
    TOP_SELLING_ADDENDUM,
    SEMANTIC_PARSER_PROMPT,
)

# The one n8n expression the registry replaces. Everything else is byte-identical.
DATE_EXPR = "{{ $now.toUTC(8*60).format('cccc, dd MMMM yyyy') }}"

LIVE_CHARS = 46942  # the fetched file, leading `=` included
# 46906 after dropping `=` and swapping the date expression; +63 chars from migration
# 487_chatbot_warehouse_cue (owner report, 7 Sep 2026), which gave `warehouse_arrival_date`
# its own warehouse/CJK/Malay cue and narrowed `estimated_arrival_date` so it no longer owns
# the bare word "arrival"; +513 chars from a follow-up to the same migration (7 Sep 2026),
# which added a WORKED EXAMPLES paragraph because the cue alone did not resolve zh/ms
# phrasings against the FULL TIMELINE sentinel; +262 chars from a review pass on the same
# migration (F1, 7 Sep 2026), which rewrote that paragraph so a bare container/shipment ask
# ("incoming TIIU6323920") reads as the full timeline and an explicit ETA ask about a
# container ("ETA of X") still resolves to `estimated_arrival_date` alone. All four edits
# are intentional content, not drift from this guard.
#
# Growth r1 (migration 490) adds a fifth: `GROWTH_R1_ADDENDUM`, appended WHOLE at the end
# rather than woven in, so it is subtracted rather than sliced - see
# `_without_growth_r1_addendum`. CONSTANT_CHARS is the body BEFORE that append, which is
# what keeps this number a guard against drift in the live-derived text rather than a
# number that moves every time growth r1's vocabulary is edited.
# +367 chars (8 Sep 2026, owner turn 2d903c96 "delivery to hanlim"): one sentence appended to
# the request_for_help definition inside the MESSAGE TYPE block - a request_for_help is
# ONLY a request for a human; a customer or product beside a delivery word is an order ask.
# Intentional content, woven in where the definition lives, not drift.
# +197 chars (item 8, 8 Sep 2026): one line in REQUESTED ATTRIBUTES - the customer's
# attribute phrase is emitted whole, and a base property is a requested attribute too.
# +262 chars (D6, 8 Sep 2026): the requested_attributes line scoped to that key, one
# attachment_type canonical_code sentence in ATTACHMENT TYPE EXTRACTION, and "image" removed
# from the canonical enum (it is "photo"). Intentional content.
# +123 chars (D10, 8 Sep 2026, turn 69d9900e): one sentence in the incoming domain
# description, in both bodies - the code in an incoming ask is the product, and only a
# 4-letter + 7-digit token (a real container number) is a container. Intentional content.
# +99 chars (D12, 8 Sep 2026, turn 8f4a8526): one sentence in REQUESTED ATTRIBUTES, both
# bodies - "details" / "product details" / "info" / "tell me about X" name no property.
# +204 chars (E2, 8 Sep 2026, turn b377b18e "Catalog Sorento"): one sentence in the
# resource_attachment domain description, both bodies - a document-class word plus a
# brand or company name is get_resource_attachment with the attachment entity, never
# promotion.
# +99 chars (12 Sep 2026, PLAN-chatbot-last-purchase-cost.md item 9): "| purchase_cost"
# woven into the `domain_hint = ONE of:` literal and one "- check_po_cost - ..." bullet
# woven into the intent_hint list, both inside the OUTPUT/INTENT & DOMAIN section, ahead
# of where `LAST_COST_ADDENDUM` stacks. Intentional content, not drift.
# +206 chars (16 Sep 2026, S4 ruling, PLAN-chatbot-turn-rearch.md): the fallback
# prompt declares the four v3 schema keys the coder's S4 slice adds
# (`document`/`status`/`anaphora`/`continuation` - the parser verdict fields the
# turn re-architecture's APPLY stage reads). RED until that declaration lands;
# written ahead of the coder's change (test-first) rather than after.
# +1423 chars (16 Sep 2026, S6 cluster 4 ruling, `chatbot-turn-rearch-acceptance-
# criteria.md` "S6 rulings"): the fallback prompt documents `answers_open_question`
# (`{resolved, picks, answer}`) - the parser now says whether THIS message answers a
# still-open question rather than the engine inferring it. Measured against the coder's
# landed change (`72eabc6a0`), not derived.
# +1589 chars (17 Sep 2026, hand pass 2 item 12, `71109d8d3`): a `== DOCUMENT ==` section
# - the closed set (SO/DO/PO/SPO/GRN), current message only, default `[]`, worked examples
# including turn c45e2929 - plus an OUTPUT line describing the key. Owner report: the
# `document` key was declared in the schema and explained nowhere, so the model had the
# field and no rule for filling it; `focus.document` and `outstanding_scope_ask_candidate`
# already read it correctly (no engine change, prompt-only fix). Measured against the
# coder's landed change, not derived.
# 52313 -> 52139 (17 Sep 2026, hand pass 3, `ddd6dc970`/`0353b9138`): the OPEN QUESTION
# section and the `answers_open_question` OUTPUT key line came OUT (the key is retired),
# and a `Current subject` block went IN (the focus, printed every turn, so refinements and
# domain switches are judged with context). Net -174. Measured against the coder's landed
# change.
# 52139 -> 53753 (17 Sep 2026, coder 14's last item, `4427bb6bb`): `asks` (an array of
# {domain, intent} objects, replacing the bare-code two-domain reading) and `topic_reset`
# join `DECLARED_KEYS`, each with its own `== ASKS ==` / `== TOPIC RESET ==` prompt
# section. Net +1614. Measured against the coder's landed change, not derived.
# 53753 -> 54793 (16 Sep 2026, `944452a8b`, owner's 16 Sep turns): a grammar particle is
# never an entity, a bare family code is always a product token wherever it sits, "PO"
# alone is purchase_order (only COST words make a message purchase_cost, and domain_hint
# is the first ask). Net +1040. Measured against the coder's landed change, not derived.
# 54793 -> 55227 (17 Sep 2026, `3cdf6ba21`, journey chain
# `hanlim-outstanding-detail-then-so-switch` step 3): a message that is ONLY a paper word
# ("Sales order" over an open detail offer) is that document and never casual - the
# DOCUMENT section's worked examples were all full sentences, none a bare two-word
# document name. Net +434. Measured against the coder's landed change, not derived.
# 55227 -> 59247 (17 Sep 2026, coder 20's S4-adjacent slice, `8f1ac903c` and descendants):
# four prompt edits - the status invariant, `domain_in_message` joining the schema (the
# `domain_in_message`/entities discriminator table, `turn/decide.py::_subject_reading`),
# `broaden_to` joining the schema, and `broaden_to`'s own worked examples. Net +4020.
# Measured against the coder's landed change (`3c19a8533`), not derived. `scope_exclusive`
# stays DECLARED in both the schema and the prompt per the same ruling (only the engine
# read is retired), so it does not move this number by itself.
# 59247 -> 58853 (17 Sep 2026, coder 21's item 2, `3fc38c409`): `scope_exclusive` comes
# OUT of the schema and the prompt entirely (the engine read was already retired by
# coder 20; this is the follow-up that drops the declaration too, per the same 17 Sep
# ruling). Net -394. Measured against the coder's landed change, not derived.
# 58853 -> 61284 (17 Sep 2026, coder 23's hand pass 6 R-a/R-b/R-c + defect 7, `1a065da1c`):
# four additions to PRONOUN REFERENCE / DOCUMENT / POSITIONAL REFERENCES / ASKS - R-b's
# prompt half (a pronoun pointing outside the message sets `anaphora.backward_reference`
# instead of naming an entity), a worked example for "can show both?" over a detail offer
# (defect 7a), the bare-number-no-larger-than-the-roster rule plus "reference_positions is
# only set when a numbered list is open" (defect 7b / defect 4's prompt half), and the
# "also"/"as well"/"too" union rule (defect 7c). Net +2431. Measured against the coder's
# landed change (`str.replace` count-verified edits on the raw source), not derived.
# 61284 -> 62981 (20 Sep 2026, hand pass 7 B2, coders 24/25, `c338cd525`): a general
# READING THE CURRENT MESSAGE IN CONTEXT rule - the current subject's own domain is the
# weakest signal there is, a continuing subject does not carry a continuing domain -
# plus coder 24's earlier typo-tolerance bullets for a garbled "outstanding"/"quantity"
# spelling. Measured via `_without_growth_r1_addendum(SEMANTIC_PARSER_PROMPT)`, not
# derived; full prompt (all four addenda included) is 84160 chars.
# 62981 -> 63657 (21 Sep 2026, hand pass 12 round 2, C7(ii), coder 44, `e58f3bea9`):
# `entity_op`'s OUTPUT SCHEMA enum gains `"replace"` (was `"clear|replace_combine|
# modify|reuse"` only) plus one new bullet beside `replace_combine` in the entity_op
# explanation list - "only X"/"just X" sets entity_op "replace" when X is not the
# whole subject already, explicitly distinguished from `replace_combine` (which keeps
# every OTHER carried axis; `replace` drops the whole scope to this message's own
# entities alone). Net +676. Measured via `_without_growth_r1_addendum
# (SEMANTIC_PARSER_PROMPT)` against the coder's landed change, not derived.
# #1262 slice 5 (F4, 26 Sep 2026): `QUANTITY_ADDENDUM` stacks AFTER
# `SALES_REPORT_ADDENDUM`, the newest outermost suffix - stripped first in
# `_without_growth_r1_addendum`, below. It does not move CONSTANT_CHARS (which
# measures the body BEFORE every later addendum, growth r1's own included).
# 63657 -> 63711 (#1262 slice 9, F1a, 26 Sep 2026): an IN-BODY edit, not an
# addendum - the ENTITY OPERATIONS list's `brand -> Sorento, Mocha, or Cabana`
# bullet is replaced (not appended around) with `brand -> a name from the Known
# brands: line ...`, because AC-S9-2's own test asserts the hard-coded phrase is
# GONE from the published template, which an addendum stacked outside the body
# cannot make true (the phrase would still be present, just superseded in
# meaning). `KNOWN_BRANDS_ADDENDUM` (stacked after `QUANTITY_ADDENDUM`) is the
# separate, ADDITIVE piece - it teaches the new `Known brands:` line itself, the
# same shape every other addendum teaches a new key. Net +54, this one bullet
# only; nothing else in the body moved. NOT reproducible from a live re-fetch by
# `test_the_constant_is_reproducible_from_the_live_file` (that test's own
# `_requested_attributes_block` exemption covers a different span) - that test
# stays skipped without `CHATBOT_LIVE_SYSTEM_MESSAGE` set, which is every CI run;
# flagged for whoever next re-derives the constant from a live fetch.
# -602 chars (PR #1247 round 8, owner ruling 26 Sep 2026 ~09:50Z, "that one can
# remove"): the n8n `{{ (() => ...)() }}` expression after "Companies OFFERED" is cut. The
# CRM registry never evaluated it, so it reached the model as literal JavaScript. See
# `COMPANIES_OFFERED_CUT` for the same edit applied to the live file in the derivation.
# 63055 -> 62668 (27 Sep 2026, chatbot memory lane A S3, merged over PR #1247, migration
# `mem_0002_parser_memory`): two cuts go with `MEMORY_ADDENDUM` (contract section
# 6.4) - the `previous_conversation_state` input-description bullet (never sent, -127),
# (the "Companies OFFERED" IIFE cut the lane also made is main's round 8 cut above, so
# it is counted once, there), and the `CURRENT DATE` section moved out of the base body entirely,
# to the end of `MEMORY_ADDENDUM` (AC-MEM071, -260). `MEMORY_ADDENDUM` itself now
# stacks AFTER `SALES_REPORT_ADDENDUM` (`_without_growth_r1_addendum` peels it first),
# so it is not part of this number. Net -387. Measured via `_without_growth_r1_
# addendum(SEMANTIC_PARSER_PROMPT)`, not derived.
#
# Merge of main b299bf6e (#1301) into chatbot memory lane A (integration round 5, 28 Sep
# 2026): main's +54 (the brand bullet now points at the Known brands line, 63055 -> 63109)
# and this lane's -387 above touch different spans of the body and both stand:
# 62668 + 54 = 62722. Measured, not derived.
# Merge of main into the #1262 lane (fix lane round 5, 27 Sep 2026): main's -602
# (63657 -> 63055) and this lane's +54 (63657 -> 63711) touch different spans of the
# body and both stand: 63055 + 54 = 63109.
# 63109 -> 63358 (issue #1352, 29 Sep 2026, owner ruling "the parser reports, the engine
# judges"): DOMAIN IN MESSAGE gains the consistency rule "domain_in_message true means
# domain_hint is never null" with its one example (+249). The open numbered question
# block and the stock task block also changed, but both sit in addenda this measure
# subtracts. Measured via `_without_growth_r1_addendum(SEMANTIC_PARSER_PROMPT)`.
# Merge of main bc75eb96 (#1353) into chatbot memory lane A (fix round 7, 29 Sep 2026):
# main's +249 and this lane's -387 touch different spans and both stand:
# 62722 + 249 = 62971.
# 62971 -> 62977 (29 Sep 2026, PLAN-po-spo-warehouse-29sep S2): the OUTSTANDING block's
# "SPO" -> incoming becomes "SPO" -> spo_allocation, an in-body edit (+6).
# 62977 -> 63051 (29 Sep 2026, PLAN-po-spo-warehouse-29sep S2, review round): the DOCUMENT
# section's `"SPO", "shipment", "container" -> ["SPO"]` becomes `"SPO", "SPO allocation" ->
# ["SPO"]` with "shipment"/"container" naming no paper (they are incoming's own words, and
# the SPO document now routes to spo_allocation), an in-body edit (+74).
CONSTANT_CHARS = 63051

#: The line the round 8 cut rewrote, as the live file carries it and as the constant does.
COMPANIES_OFFERED_LIVE = (
    'Companies OFFERED in the pending offer (from state; "(none)" when no offer is '
    "pending): " + "{{ (() => { const st = $('When Executed by Another Workflow').first().json.previous_conversation_state || {}; const rp = Array.isArray(st.routing_roster_plan) ? st.routing_roster_plan : []; const src = rp.length ? rp : (Array.isArray(st.routing_companies) ? st.routing_companies : []); return src.map(c => (c && typeof c.company_name === 'string') ? c.company_name : '').filter((n, i, a) => n && a.indexOf(n) === i).map(n => { const k = n.toLowerCase(); const code = k === 'sorento' ? 'SRT' : (k === 'mocha' ? 'MCH' : (k === 'cabana' ? 'CBN' : '')); return code ? (n + ' (code ' + code + ')') : n; }).join(' / ') || '(none)'; })() }}"
)
COMPANIES_OFFERED_CUT = (
    "Companies OFFERED in the pending offer: the companies the Previous response "
    'offered; "(none)" when no offer is pending.'
)


def _without_growth_r1_addendum(text: str) -> str:
    """The body as it was before growth r1 appended its vocabulary block (migration 490).

    `LAST_COST_ADDENDUM` (migration 511, 12 Sep 2026), then `LOW_STOCK_ADDENDUM`
    (PLAN-low-stock-report.md S7, 14 Sep 2026), then `SALES_REPORT_ADDENDUM`
    (PLAN-chatbot-sales-report.md S4 wiring point 1, migration
    `519_chatbot_sales_report_vocab`), then `QUANTITY_ADDENDUM` (issue #1262 slice 5,
    26 Sep 2026), then `KNOWN_BRANDS_ADDENDUM` (issue #1262 slice 9, 26 Sep 2026),
    then `ESCALATION_CONFIRMATION_ADDENDUM` (#1323), then `MEMORY_ADDENDUM` (chatbot memory lane A,
    migration `mem_0002_parser_memory`, which also folds the moved CURRENT DATE
    section into this same trailing block - AC-MEM071) now stack AFTER
    `GROWTH_R1_ADDENDUM` on both bodies, the same way this one stacked after the live
    text - so they come off FIRST, newest outermost, before the `removesuffix` this
    function has always done. Each addendum is an APPENDED block, so both come off by
    suffix rather than by the index slice the warehouse-arrival edit needs (that one
    sits INSIDE the requested-attributes section). The assertion that
    `GROWTH_R1_ADDENDUM` really is the tail once `LAST_COST_ADDENDUM` is off lives in
    `test_parser_growth_r1_reachability.py::test_the_addendum_is_appended_to_both_bodies`.
    """
    # MEMORY_ADDENDUM (chatbot memory lane A) is the newest addendum and the prompt's
    # tail, so it is peeled first; ESCALATION_CONFIRMATION_ADDENDUM (#1323) sits beneath
    # it, then `TOP_SELLING_ADDENDUM` (PLAN-chatbot-top-x-hot-selling-24sep.md S4), then
    # the #1262 pair (KNOWN_BRANDS_ADDENDUM, QUANTITY_ADDENDUM), then
    # SPECIFICATION_ADDENDUM (fix round 8 on PR #833), then SALES_ANALYSIS_ADDENDUM
    # (#1267 S1), then STOCK_TASK_ADDENDUM (ported from PR #1118, not merged,
    # chatbot-stock-ask-v2 S3).
    if text.endswith(MEMORY_ADDENDUM):
        text = text[: -len(MEMORY_ADDENDUM)]
    if text.endswith(ACCOUNT_LEDGER_ADDENDUM):
        text = text[: -len(ACCOUNT_LEDGER_ADDENDUM)]
    if text.endswith(PO_SPO_WAREHOUSE_ADDENDUM):
        text = text[: -len(PO_SPO_WAREHOUSE_ADDENDUM)]
    if text.endswith(ESCALATION_CONFIRMATION_ADDENDUM):
        text = text[: -len(ESCALATION_CONFIRMATION_ADDENDUM)]
    if text.endswith(SELF_REFERENCE_ADDENDUM):
        text = text[: -len(SELF_REFERENCE_ADDENDUM)]
    if text.endswith(TOP_SELLING_ADDENDUM):
        text = text[: -len(TOP_SELLING_ADDENDUM)]
    if text.endswith(KNOWN_BRANDS_ADDENDUM):
        text = text[: -len(KNOWN_BRANDS_ADDENDUM)]
    if text.endswith(QUANTITY_ADDENDUM):
        text = text[: -len(QUANTITY_ADDENDUM)]
    if text.endswith(SPECIFICATION_ADDENDUM):
        text = text[: -len(SPECIFICATION_ADDENDUM)]
    if text.endswith(SALES_ANALYSIS_ADDENDUM):
        text = text[: -len(SALES_ANALYSIS_ADDENDUM)]
    if text.endswith(STOCK_TASK_ADDENDUM):
        text = text[: -len(STOCK_TASK_ADDENDUM)]
    if text.endswith(SALES_REPORT_ADDENDUM):
        text = text[: -len(SALES_REPORT_ADDENDUM)]
    if text.endswith(LOW_STOCK_ADDENDUM):
        text = text[: -len(LOW_STOCK_ADDENDUM)]
    if text.endswith(LAST_COST_ADDENDUM):
        text = text[: -len(LAST_COST_ADDENDUM)]
    assert text.endswith(GROWTH_R1_ADDENDUM), (
        "GROWTH_R1_ADDENDUM is no longer the tail of the prompt (once any LATER addendum "
        "is removed). It is appended rather than woven in on purpose (the FULL body has "
        "to stay a mechanical derivation of the live n8n message); moving it into the "
        "body means this file needs a second slice-out, not a bigger character count."
    )
    return text[: -len(GROWTH_R1_ADDENDUM)]


def test_the_constant_has_the_live_size_not_the_export_size() -> None:
    """The export is 49,318 characters. Anything near that is the wrong body."""
    body = _without_growth_r1_addendum(SEMANTIC_PARSER_PROMPT)
    assert len(body) == CONSTANT_CHARS, (
        f"parser prompt is {len(body)} chars before the growth r1 addendum, expected "
        f"{CONSTANT_CHARS}. "
        "The live body is 46,942 chars before the two mechanical edits; the working-tree "
        "export is 49,318 and carries the unpromoted B-TEAM-1' lane change."
    )


def test_the_constant_never_asks_for_team_source() -> None:
    """`team_source` is B-TEAM-1'. The live message does not mention it, and not one of
    the 488 captured raw emissions carries it."""
    assert "team_source" not in SEMANTIC_PARSER_PROMPT


def test_the_date_expression_became_the_registry_variable() -> None:
    assert SEMANTIC_PARSER_PROMPT.count("{{current_date}}") == 1
    assert DATE_EXPR not in SEMANTIC_PARSER_PROMPT
    assert not SEMANTIC_PARSER_PROMPT.startswith("=")


def test_the_output_block_declares_exactly_the_schema_keys() -> None:
    """The prompt and `parser.PARSE_OUTPUT_JSON_SCHEMA` must agree on the wire shape."""
    from app.services.chatbot.head.parser import DECLARED_KEYS

    missing = sorted(k for k in DECLARED_KEYS if f'"{k}"' not in SEMANTIC_PARSER_PROMPT)
    assert not missing, f"prompt does not declare: {', '.join(missing)}"


def _live_file() -> Path | None:
    raw = os.environ.get("CHATBOT_LIVE_SYSTEM_MESSAGE")
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_file() else None


def _requested_attributes_block(text: str) -> tuple[int, int]:
    """The span from the `estimated_arrival_date` vocabulary line through the end of the
    WORKED EXAMPLES paragraph.

    F5 (review, 7 Sep 2026): migration `487_chatbot_warehouse_cue` put NEW prompt content
    inside this span - it is not a transform of the live text, so it is not byte-for-byte
    reproducible from it. `test_the_constant_is_reproducible_from_the_live_file` slices this
    one block out of both sides before comparing; everything outside it must still match.
    """
    start = text.find('"estimated_arrival_date"')
    assert start != -1, '"estimated_arrival_date" not found in prompt'
    worked = text.find("WORKED EXAMPLES", start)
    assert worked != -1, "WORKED EXAMPLES paragraph not found after estimated_arrival_date"
    end = text.find("FULL TIMELINE", worked)
    assert end != -1, "FULL TIMELINE paragraph not found after WORKED EXAMPLES"
    return start, end


def test_the_constant_is_reproducible_from_the_live_file() -> None:
    """The derivation itself, when the fetched file is available.

    Opt-in via ``CHATBOT_LIVE_SYSTEM_MESSAGE`` because the file is not committed. The
    tests above hold everywhere; this one proves the two MECHANICAL edits and nothing
    more, and it is what to run after any re-fetch.

    F5: the constant also carries a THIRD, content-only edit inside the REQUESTED
    ATTRIBUTES block (the warehouse-arrival vocabulary + its WORKED EXAMPLES paragraph),
    which by construction cannot come out of the live file by a mechanical transform - it
    is new text the owner asked for, published as its own prompt version rather than
    promoted straight to production. So this test derives the constant from the live file
    the same two-edit way as before, then asserts it against `SEMANTIC_PARSER_PROMPT` with
    that one block cut out of BOTH sides, rather than expecting the two to be byte-equal
    end to end.
    """
    path = _live_file()
    if path is None:
        pytest.skip(
            "set CHATBOT_LIVE_SYSTEM_MESSAGE to the fetched "
            "sub-semantic-parser.systemMessage.live.txt to verify the derivation"
        )
    raw = path.read_text(encoding="utf-8")
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == LIVE_SYSTEM_MESSAGE_SHA256, (
        "the file at CHATBOT_LIVE_SYSTEM_MESSAGE is not the body this constant was taken "
        "from; re-derive the constant rather than editing the hash"
    )
    assert len(raw) == LIVE_CHARS
    assert raw.startswith("=")
    assert raw.count(DATE_EXPR) == 1
    derived = raw[1:].replace(DATE_EXPR, "{{current_date}}")
    assert derived.count(COMPANIES_OFFERED_LIVE) == 1
    derived = derived.replace(COMPANIES_OFFERED_LIVE, COMPANIES_OFFERED_CUT)

    body = _without_growth_r1_addendum(SEMANTIC_PARSER_PROMPT)
    d_start, d_end = _requested_attributes_block(derived)
    c_start, c_end = _requested_attributes_block(body)
    assert derived[:d_start] == body[:c_start], (
        "everything BEFORE the requested-attributes block must still be a pure mechanical "
        "derivation of the live file"
    )
    assert derived[d_end:] == body[c_end:], (
        "everything AFTER the requested-attributes block, and before the growth r1 addendum, "
        "must still be a pure mechanical derivation of the live file"
    )
