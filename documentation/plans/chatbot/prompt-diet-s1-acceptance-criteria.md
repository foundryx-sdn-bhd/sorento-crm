# UAC: PROMPT-DIET stage 1 (fix, delete dead text, port #1574)

Plan: `PLAN-prompt-diet-audit-16oct.md` (Stage 1 section). Base text: dev v94 (= prod v63 text), stored verbatim
as `sorento_crm_backend/alembic/data/chatbot_semantic_parser.v94.txt` and never edited. Output:
`sorento_crm_backend/alembic/data/chatbot_semantic_parser.diet-s1-20261016.txt`, built by
`scripts/build_parser_diet.py`. Section numbers are the audit page's (v94 line numbers in brackets).

## Build

- AC1 The build is reproducible: building from the stored v94 text gives exactly the committed output file.
- AC2 Every edit is an exact (id, old, new) replacement whose `old` occurs exactly once at the moment it is
  applied; a missing or repeated anchor raises with the edit id.
- AC3 #1574 port: every cut in `scripts/build_parser_ablation.CUTS` applies to v94 except the 18 ids that no
  longer match (C05 C23 C25 C34 C39a C39b C42a C42b C43a C43c C43d C44 C45 C47c C47d C53a C53b C57). A
  non-skipped cut that fails to match raises; a skipped cut that starts matching raises too (drift is loud).
  The cut list is imported, not copied.

## Text (the built output)

- AC4 CURRENT DATE: "before calling the MCP tool" is gone and `{{current_date}}` appears exactly once. The
  relative-date sentence itself is removed by #1574 cut D03 and is NOT re-added (amended 16 Oct: stage 1 never
  undoes a #1574 cut).
- AC5 FLYER FLAG (16): no instruction to set `contains_flyer` true remains. One line keeps the routing cue: a
  flyer or brochure ask is a promotion ask (`domain_hint` promotion, `intent_hint` check_promotion) and flyer is
  never an entity. The OUTPUT object still lists `"contains_flyer": false`.
- AC6 CORRECTION / DOUBT (25): the section is gone; the OUTPUT object still lists `"correction": false`. Other
  mentions of correction in later sections are untouched.
- AC7 REFERENCE TARGET (29): the section is gone; the text "suggestions active" appears nowhere; the OUTPUT line
  reads `"reference_target": null`; no "see REFERENCE TARGET" cross-reference remains (v94:216).
- AC8 Section 43 contradiction: "Never emit BOTH an entity and a reference_position for the same message."
  (v94:900) is gone. In its place: a BARE number reply (no code or name typed) gives reference_positions only and
  never also copies the picked option into entities. The typed-code rule (v94:891-893, position AND entity)
  is unchanged.
- AC9 OUTPUT object: a comma follows `"requested_attributes": []` (v94:765). Every key a banner announced "IN
  ADDITION to the OUTPUT object" is listed inside the OUTPUT object with the same values: group_by (plus month,
  year), top_n, sales_channel, proceed_anyway, sales_basis, sales_company, rank_by, basis, rank_group,
  rank_direction, self_reference. The entity sub-keys quantity, spec_key and spec_value are stated once, right
  after the OUTPUT object. The phrase "IN ADDITION to the OUTPUT object" appears nowhere.
- AC10 Banner boxes that only carried a title or that key announcement are gone: TWO MORE OUTPUT KEYS, LAST
  PURCHASE COST, SALES REPORT, THE OPEN TASK, SALES ANALYSIS, SPECIFICATIONS, QUANTITY, KNOWN BRANDS, TOP
  SELLING, SELF REFERENCE. The CURRENT DATE, LOW STOCK REPORT FILTERS and ESCALATION CONFIRMATION banners stay
  (they carry rules). Every `== X ==` section heading under a removed banner stays, except a heading that an
  applied #1574 cut deletes (D01 removes the duplicate "A DELIVERY WORD PLUS A NAME IS AN ORDER ASK"; amended 16 Oct).
- AC11 `{{find_filters}}` appears exactly once, and the text from `<<<CHATBOT POLICY BLOCKS>>>` to
  `<<<END CHATBOT POLICY BLOCKS>>>` is byte-identical to v94's.
- AC12 No key lost: every required top-level key of `PARSE_OUTPUT_JSON_SCHEMA`
  (`app/services/chatbot/head/parser.py`) that v94 quotes as `"key"` is still quoted in the output. (Amended 16 Oct:
  10 required keys, e.g. sort_by, sub_asks, find, are never quoted in v94; they reach the model only through the
  JSON schema, so they are out of scope here.)
- AC13 The output is at least 7% shorter than v94 in characters.
- AC14 No em-dash or en-dash is added by any stage-1 edit's `new` text.

## Publish

- AC15 Migration `pdiet_s1_0001` publishes the output text as the next `chatbot_semantic_parser` version with
  variables `["current_date", "find_filters"]`, commit message naming PROMPT-DIET stage 1; it never touches
  `ai_prompt_labels`; running it twice publishes once (an identical template already present is a no-op);
  downgrade removes only the row it published.

## Gate (not a test; recorded in the PR)

- G1 parser-gold TRAIN x2 on the output version shows no drop against v94 on the same day (crew scores).
- G2 crew held-out after G1. No label move without the owner's PASS.
