# PLAN: answering the bot's attachment-type question keeps the product it asked about (CARRY-ATTACH)

Status: in progress (T3 and T2 fixed, tests green, in review); track: small fix (backend only, under 300 changed lines, no migration, no auth/RBAC change, no new ingest surface)

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

## T2 (owner ruling, 13 Oct 2026)

Owner: "why is it same as before? we shouldn't need any ruling for this right? the parser should
be able to understand". No special wording, no clarify-policy row: the parser decides.

The parser already says it. "Srt8313" after "Srt8308 drawing" keeps the domain with no domain word
of its own (`domain_in_message: false`, the prompt's BARE ENTITY CONTINUATION rule: the same
question about a new value). `decide` reads that as REFINE, so the carried Technical
Specifications stays on the focus and the fetch already ran under it. Only the gate's
required-type check (`lanes/business/gate.py`, `have_types`) overrode it, because
`_with_carried_document_types` handed the resolver only uuid-less carried types.

Fix: `_with_carried_document_types` also hands over a SETTLED carried type, with its uuid. A
message that starts a new ask ("attachments for Srt8313", `domain_in_message: true`) has already
dropped the carried type in `apply._focus_rules` (`with_carried_entities` reads the post-apply
focus), so nothing is handed over and the bot still asks which type. No prompt change was needed:
the fields v52 already emits carry the reading. The `continuation` key is not read (#1570
redefines it as false whenever a code is typed); the tests pass with it true or false.

CLARIFY-POLICY (#1570): merges cleanly with this branch, and on the merged tree (seed policy, so
`carry_combine` = ask) all of `test_carry_attach.py` passes, with T2's `continuation` true and
false. #1570 keeps `attachment_type` an ask-only row for the case with no carried type; it does
not re-introduce the ask here.

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
