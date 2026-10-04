# UAC: several attachment types in one ask + no snake_case in customer replies

Plan: `PLAN-attachment-multi-type-2oct.md`. Owner answers 2 Oct 2026: Q1-Q5 all (a).
Tests: `sorento_crm_backend/tests/chatbot/test_attachment_multi_type.py` (pytest, blank
schema, real resolver + gate + miss lane + composer, only the MCP client stubbed).
Type names are dev's own: Product Photos, Technical Specifications (no code); there is no
"Technical Drawing" type on dev.

- AC-1 (R1) "photo and technical specifications for SRTWC286-SH" keeps BOTH types in the gate
  scope; one customer word that matched several document types is still narrowed to the one
  it named (container-status S1 guard).
- AC-2 (R2, Q1 a) An ambiguous product with two asked types stamps every line per type:
  `1. SRTWC286-SH - has Product Photos, no Technical Specifications`. Numbering and order are
  the gate's own. With no files: `- no Product Photos, no Technical Specifications`.
- AC-3 (R5, Q4 a) The stamp noun is `attachment_types.type_name`, never a slug `code`.
- AC-4 (R3, Q2 a) A found product missing one asked type: the files that exist are sent, plus
  `SRTWC286-SH has no Technical Specifications.`, with no escalate offer. A product with none
  of the asked types: `SRTWC286-SH-200 has no Product Photos or Technical Specifications.`
  A slug type is never named as a gap.
- AC-5 (R4, owner ruling 2 Oct) The miss sentence names the official types: `But no Product Photos or Technical Specifications matched these.`;
  the found bullet reads `• attachment type: Product Photos, Technical Specifications`.
- AC-5b (owner ruling 2 Oct, hand test "photo and cert for strwc286") A product that is not
  found and has no did-you-mean candidate replies only `Couldn't find "strwc286" (product).`
  plus the escalate offer: no "Here's what you want" types, no "no <types> matched these".
  A swapped-letter typo below the resolver's did-you-mean floor stays as is (owner, no fix).
- AC-6 (Q3 a) The require-specific picker (product attachments and incoming) asks
  `Which product do you mean? Please choose:`.
- AC-7 (Q5 a) None of the 11 swept leaks reaches a customer as snake_case: dropped-filter line,
  found bullet, could-not-find sentence, vague-token clarify (both halves), needs-a-filter
  reply, did-you-mean label fallback, kind pick option, not-allowed reply, access-level ask,
  picker header, stamp noun.
- AC-8 Existing suites stay green (`tests/chatbot`), single-type stamps unchanged
  (`test_product_attachment_picker_stamp.py`), node captures byte-equal (`test_replay.py`).
- AC-9 Hand test (owner, crew test copy): the `crew-handtest:` script on PR #1437.
