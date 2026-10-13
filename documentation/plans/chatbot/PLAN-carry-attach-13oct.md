# PLAN: answering the bot's attachment-type question keeps the product it asked about (CARRY-ATTACH)

Status: in progress (T3 fixed, tests green; T2 switch built, off, awaiting owner ruling); track: small fix (backend only, under 300 changed lines, no migration, no auth/RBAC change, no new ingest surface)

UAC: `carry-attach-acceptance-criteria.md` (alongside), golden set included.

## Report

Prod, 13 Oct 2026 12:35-12:37, parser prompt v52, turns 171-174, an internal General Enquiries
contact with no customer link.

- T1 "Srt8308 drawing": file sent. Focus after: products [SRT8308 uuid], extra.attachment_type
  [Technical Specifications uuid].
- T2 "Srt8313": parser [Srt8313 product, typed], `continuation: true`, `domain_in_message:
  false`. The gate asked "Please provide the attachment type for product Srt8313" although the
  fetch had found SRT8313's file under the carried type. No open_question was left.
- T3 "Drawing": parser [Srt8313 product `current_message: false`, Drawing attachment_type
  canonical "technical drawing"], `entity_op: reuse`, `domain_in_message: true`. The reply was
  "Here's what you want: / • attachment type: Technical Specifications / But no Technical
  Specifications matched these. Would you like me to escalate to marketing product team?" and
  T4 "Yes" escalated. BUG.

## Root cause (T3), reproduced on 2dfb83d4

1. `engine.py` calls `turn_runtime.without_restated_rows`: the turn types only a document word,
   so the parser's echo of the settled SRT8313 is dropped (ATTACH-SET-BRAND-FOLLOWUP round 2).
2. `turn/decide._subject_reading`: `domain_in_message: true` with entities is the table's
   NEW_ASK row ("domain_in_message"), and the only entity left is the document word.
3. `turn/apply._focus_rules`: a NEW_ASK evicts every `KIND_FIELD_MAP` kind the message did not
   name, so SRT8313 leaves `focus.products` (`new_ask_drops_product`).
4. `with_carried_entities` then finds no settled product (no `subject_settled`), the resolver
   is asked about "technical drawing" alone, the fetch runs with no product
   (`tool_filter has_product: false`), and the miss arm writes the described-set miss.

Staff vs dealer and the missing open_question were not factors: the repro with a dealer
contact and the prod verdict keys gives the prod reply word for word. The first repro passed
only because its T3 verdict lacked `domain_in_message`.

## Fix

The echo is the parser saying the subject is still the settled row. `without_restated_rows`
now records the kinds of the echoes it drops (`restated_kinds`, `turn/decide.RESTATED_KINDS_KEY`)
and the NEW_ASK eviction keeps those kinds, neither dropping nor replacing them. Every other
carried kind is still dropped. Parser fields only; no word rule.

## T2 (owner ruling pending)

`turn_runtime.CARRY_SETTLED_DOCUMENT_TYPE`:

- False (option b, today, the default): a settled carried attachment_type is not handed to the
  resolver, the gate asks for the type.
- True (option a): `_with_carried_document_types` hands it over with its uuid, the gate counts
  it, and the turn answers SRT8313 under Technical Specifications.

Option (a) as the owner phrased it also wants the reply to say "(technical drawing, same as
before)". That copy is not built: it needs the owner's wording ruling, and it belongs in the
attachment reply's header, one line.

CLARIFY-POLICY (#1570) seeds `attachment_type` as an ask-only row and carries only
product/customer subjects. Once it lands the constant becomes that row's mode (ask / default)
with `default` allowed, and the default note is the "same as before" line. No textual overlap:
#1570 edits `_answer_pending` and `_apply`, this lane edits the NEW_ASK eviction inside
`_focus_rules` and `_with_carried_document_types`.

## Not changed, noted for the owner

The T3 miss reply's shape "Here's what you want: / • attachment type: Technical
Specifications / But no Technical Specifications matched these." (`answer.what_you_want_reply`
via `described_members_reply`) prints the document type as a bullet list of one under "Here's
what you want", which reads as a list of products. Copy is unchanged pending an owner ruling.
After this fix T3 no longer reaches it.

## Tests

`sorento_crm_backend/tests/chatbot/test_carry_attach.py`: the 3-turn conversation through
`engine.run_turn` with the real resolver (red on 2dfb83d4, green here), both switch positions,
and the pure seams (stamp, no stamp, NEW_ASK keeps the restated kind and drops the rest).
