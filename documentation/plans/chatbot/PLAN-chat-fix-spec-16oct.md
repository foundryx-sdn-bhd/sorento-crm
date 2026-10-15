# PLAN: chat fix PR-A, spec matching (lane CHAT-ADVISOR-SPIKE, chat-fix LEAD)

Status: Plan. Track: L (registry seed migration + one new parser prompt version + code in grounding, search and
prompt rendering). Owner GO 16 Oct 00:55 ("Ok start"); chatbot freeze lifted for this plan only. Branch
`crew/chat-fix-spec` off origin/main ccb1ce9820. Source analysis: `ARCH-advisor-16oct.html` sections E, B and 2
(PR #1711).

## Goal

A spec ask is answered from the registry's meaning, never from a word rule in code, and an unknown feature is said
back instead of dropped. Owner ruling 16 Oct 00:10: "bathtub is not jacuzzi lah... Jacuzzi is like bathtub with
massage jet": a plain bathtub ask returns bathtubs; "bathtub with massage jet" / whirlpool means jacuzzi, via the
parser and the spec registry.

## Facts (origin/main, 16 Oct)

- The parser prompt v94 bakes the spec lines (v94:1796-1845); the live `{{specs}}` variable exists
  (`app/services/chatbot_prompt_vars.py:368-371` -> `chatbot_parser_prompt.specification_lines`) but v94 does not use
  it, so a registry edit reaches the parser only on republish.
- `specification_lines` skips the `class` key (`chatbot_parser_prompt.py:1265`) and prints product types as display
  names only (`:1288-1296`): 44 types with synonyms in the registry, none shown.
- Registry `class` row: 0 allowed values, `synonyms {}`; class words live in `product_categories.search_synonyms`
  (39 of 126 rows) and in the code list `CLASS_SYNONYMS` (`app/services/product_class_signal.py:66-92`), whose
  "Jacuzzi" words never apply (`:290-295`, no category is labelled Jacuzzi). 186 products are Jacuzzi class.
- Unknown spec words are dropped silently except on promotion asks (`app/services/chatbot/head/grounding.py:738-749`).
- Spec bugs from the walkthrough: name-matched ids OR-ed with the spec clause (`system/references.py:3429-3438` ->
  `product_predicate_service.py:1010-1020`, W04 count 220 vs 183); yes/no spec values dropped
  (`product_spec_search.py:1003-1005`, W10); a spec menu offering choices that have no promotion
  (`chatbot/turn_runtime.py:703-709`, W09); a class-level product list with no total (W12).
- Spec extraction rules that set a product's yes/no values from its catalogue description are code in the registry
  module (`product_spec_registry.py` `flag(...)`, 19 keys). They read the CATALOGUE, not the user's message.

## Changes (each its own red-first slice)

1. S1 `{{specs}}` complete: `specification_lines` also prints product type synonyms and class meanings (class
   choices with their synonyms from the registry). New parser prompt version = prod's chain (prod v63 = v52 + the
   `{{find_filters}}` line; dev copy v94) with the static spec block replaced by `{{specs}}`. Prod v52/v63 holds a
   static block, so PR-A takes `_with_specs_block` / `publish_specs_block` (`chatbot_prompt_vars.py`) and the
   `publish_parser_specs_block --from-version` script from #1582 (parked; only these pieces). Dev only; published on
   owner PASS only.
2. S2 class meanings as data: seed the registry `class` choices + synonyms (idempotent migration), moving every word
   from `CLASS_SYNONYMS` into data (nothing lost), Jacuzzi = "bathtub with massage jets", whirlpool, spa bath,
   按摩浴缸; then delete `CLASS_SYNONYMS` and read the registry instead.
3. S3 `has_massage` registry key (data row + catalogue flag rule like `is_frameless`, limited to tub classes), so
   jetted tubs outside the Jacuzzi class are found too.
4. S4 say back unknown spec words on every domain (`grounding.py:738-749` loses its promotion-only condition).
5. S5 category name rule: one prompt line like the brand rule (v94:1486-1499): a category is one of the listed
   types (W05 "GrabBar"). Same prompt version as S1.
6. S6 NEW-4 intersect name-matched ids with the spec clause instead of OR.
7. S7 NEW-7 keep yes/no spec values in the search join.
8. S8 NEW-6 a spec menu offers only choices that lead to rows for the asked domain; otherwise answer at class level.
9. S9 NEW-8 a class-level product list states its total ("Bathtubs (84)"), like the W04 count reply. The shower
   screen data clean-up is an owner-run SQL (UPDATE), not in the PR.

Out: P2-F3 (dropped, word rule), P2-F4 size branch (#1582, parked except the specs publisher above).

Overlap: #1582 edits the same `grounding.ground()` block (~714-741) and `specification_lines`; #1582 stays parked, so
PR-A owns these lines now. No other open PR touches `product_class_signal.py`.

## Gates (owner/crew 16 Oct, no exceptions)

- Red-first commit + kill test per slice (tester writes red tests from the UAC; coder makes them green).
- Parser gold TRAIN (crew/state/eval/parser-gold, TRAIN slice only): whole-parse must not drop, and must improve on
  items the fix was not written for. Crew runs the HELD-OUT set itself.
- Walkthrough rows not written for (W01-W10 except the spec rows) re-run on the PR branch: no regression.
- One new prompt version for the PR, published only on owner PASS; live check on the prod prompt before hand test.
- reviewer; security-reviewer not required (no auth/RBAC/ingest); global guards if a table changes
  (`tests/test_schema_uuid_id_principle.py`, `tests/test_list_pagination_limit.py`).
- Owner hand test before merge (Japan 17-25 Oct, console over Tailscale). Spend reported per PR.

## Measurement

- Walkthrough W11/W12/W10/W04/W05/W09 (written for) + the rest (not written for), staff + dealer, v94 vs the PR
  prompt version (`scripts/advisor_spike/walkthrough.py`, kept out of the PR).
- Parser gold TRAIN 137 items x 2 runs on the PR prompt version (~$1.6).
