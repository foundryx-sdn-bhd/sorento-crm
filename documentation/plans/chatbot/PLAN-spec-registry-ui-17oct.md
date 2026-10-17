# PLAN: spec-registry-ui (owner edits chatbot closed lists from the front end)

Status: Plan (card + publish path answered 17 Oct, mock ready for the owner's OK). Track: full (size L, one migration).
crew-lane: SPEC-REGISTRY-UI

## Owner request (17 Oct)
The owner wants to correct chatbot spec matching from the front end (example: "300mm" read as trap length
should map to another dimension) by editing registry rows (field name, label, unit, synonyms / aliases,
description / examples the parser sees), spec values, attachment-type aliases and certification types.
The change takes effect on the next message, no deploy. Data only: no keyword rules or fuzzy matching in code.

## Discovery (17 Oct, main @ 63f1610f, #1755 head c6c71e8b)

Most of this ALREADY SHIPS. The owner's journey is a repair + small extension of existing screens, not a new screen.

### Where the lists live and who edits them today
| List | Table | Admin screen (exists) | Read into parser |
|---|---|---|---|
| Spec fields (key, label, unit, type, words) | `product_spec_registry` (`sorento_crm_backend/app/models/product_spec.py:41`) | Master Data > Product Specifications, record page tabs Details / Choices and words / Rules / Seen in products (`sorento_crm_frontend/app/(protected)/master-data-management/product-specifications/components/record/*`) | `specification_lines` (`app/services/chatbot_parser_prompt.py:1268`) via `{{specs}}` (`app/services/chatbot_prompt_vars.py` on main and #1755) |
| Spec values + words per value | same row: `allowed_values` + `user_values` - `suppressed_values`; `synonyms` + `user_synonyms` - `suppressed_synonyms` (`product_spec.py:57-131`, merged by `merged_synonyms` `app/services/product_spec_registry.py:1691`) | Choices and words tab | same |
| Words for the field itself | `synonyms["_self"]` (trap_length ships `_self: ["trap"]`, `product_spec_registry.py:1155`) | Details tab, "Other names for this specification" (`HeaderTab.tsx:128`) | rendered as `Words:` on the numeric line (`chatbot_parser_prompt.py:1309`) |
| File types | `attachment_types` (code, type_name, description) | Resources > Attachment Types | #1755 `render_attachment_types` / `closed_list_items` |
| File type aliases | lookup set `attachment_type_alias` (`lookup_sets` / `lookup_options` / `lookup_option_keywords`, `app/models/lookup.py`) | System > Lookup Sets | #1755 `_alias_words` |
| Certification types | `attachment_types WHERE is_certificate` + `certificates.scheme` + lookup set `certificate_scheme` | Attachment Types, Certificates, Lookup Sets | #1755 `render_certification_types` |

### How a change reaches the bot (no deploy)
`chatbot_prompt_vars` renders every `{{token}}` from its table on each turn; values are cached per process for
`CACHE_TTL_SECONDS = 30` and the cache is cleared by an `after_commit` hook when the session wrote one of
`REGISTRY_TABLES` (`product_spec_registry`, `attachment_types`, `certificates`, `lookup_options`,
`lookup_option_keywords`). The worker converges within 30 s. This holds ONLY if the active published
`chatbot_semantic_parser` text carries the `{{specs}}` / `{{attachment_types}}` / `{{certification_types}}` tokens
(the docstring at `chatbot_parser_prompt.py:1278` still says "at the next publish").
Crew data check (17 Oct): it does NOT. Production (dev v63) carries no `{{specs}}`, `{{attachment_types}}` or
`{{certification_types}}`; the spec lines are baked text (rendered by `render_prompt_blocks` at publish time). #1755's
unlabelled v162 adds the two list tokens but not `{{specs}}`. So today no registry edit reaches the production bot.

### Permission and audit (exist)
- Words / values: `master_data.spec_registry.edit` OR `master_data.products.edit`; any other field needs
  `master_data.spec_registry.edit` (`app/api/v1/master_data/spec_registry.py:911-960`). Add key `...add`, delete `...delete`.
- Audit: `product_spec_registry` and the lookup tables have no `__audit_skip__`, so every save is in the global
  audit log (`app/services/audit_service.py:1297`), visible on System > Audit Logs. No per-key History tab.

### Guard rails (exist)
- A shipped key cannot be deleted, only switched off (`delete_registry_key`, `product_spec_registry.py:1571`).
- Shipped values cannot be edited, only suppressed / added to (`spec_registry.py:1009`).
- A word cannot be a second value's word on the SAME key (`find_similar_value`, `product_spec_registry.py:2010`).
- #1755: an alias two file types share names neither (`closed_list_items`).

### Test a message (exists, twice)
- Product Specifications > Try a phrase (`TryPhraseDialog.tsx`) runs the search ranker's understanding model
  (`product_spec_understanding`), NOT the chatbot parser.
- System > Chatbot Console (`POST /api/v1/system/chatbot/console/turn`, `app/api/v1/system/chatbot.py:647`) runs a
  dry-run turn through the real parser.

### Gaps found (the actual work)
- G1 BUG: the Details tab lets you edit Unit (`HeaderTab.tsx:69`, sent by `useSpecKeyRecord.ts:152`) but
  `SpecKeyUpdate` has no `unit` (`spec_registry.py:173`, deliberate: a unit change invalidates stored values), so the
  edit is silently dropped.
- G2 BUG: Name (label) of a shipped key is editable but is seed-owned: the next migration that runs
  `seed_spec_registry` (`product_spec_registry.py:1278`, `_seed_values` includes `label`) reverts it.
- G3 MISSING: no per-field description / examples text the parser sees. The parser gets only
  `Specification trap_length ("Trap outlet length"): a number in mm. Words: trap.`
- G4 MISSING: a word may sit on two different spec fields (no cross-key guard), so "length" can mean two fields.
- G5 MISSING (optional): no parser-pick view on the spec page; the console shows it per turn.

## Behaviour card (crew answers 17 Oct, all (a))
1. One "What this means to the chatbot" text per field: new column `parser_hint` (text, max 200, one line), printed
   on that field's line in `{{specs}}` (`specification_lines`). Editable on shipped and own fields, never seed-repaired.
   Needs `master_data.spec_registry.edit`.
2. One word, one field: saving a word (per-value or `_self`) that is already ANOTHER field's word or name is refused
   `already a word for <Field>` (409-style AppException, code `spec_registry_word_on_other_key`). Only newly added
   words are judged; shipped overlaps are listed, not blocked.
3. An edited Name is the owner's: new `label_overridden` flag (or equivalent) so `seed_spec_registry` never reverts it.
4. Unit read-only on a saved field, short reason shown beside it; the FE stops sending `unit` on PATCH.
5. Today's permissions. "Try in chatbot" (record gear) runs the real parser dry-run (console turn path, no reply,
   no writes outside `chatbot.turns`) and shows the picked field / value / list item.
6. Gear links to Attachment Types and Lookup Sets. History tab mounts `AuditTrail` for the row.
7. Publish path (crew answer 17 Oct, (a)): `scripts/publish_specs_token_prompt.py`, same shape as #1755's
   `publish_closed_lists_prompt.py`. Base = production version or `--from-version N` (run on top of #1755's version so
   one publish carries both). Finds the ONE contiguous run of baked `Specification ...` lines plus the
   `Product types (...)` line, replaces it with `{{specs}}`, adds `specs` to `variables`, refuses on 0 or 2+ runs,
   publishes the next version UNLABELLED, never moves a label. Crew sequences the publishes; the label moves only after
   a bench run and the owner's OK.
8. Shipped cross-field overlaps (20 words on dev, 14 of them Product class vs Type) get an "Also on: <field>" note on
   the word row; only NEW words are refused.
9. Audit: 0 dev rows for `product_spec_registry` is expected (every dev edit, 14 to 21 Sep, predates default-on staff
   audit); History fills from the first save.

## Mock
`documentation/plans/chatbot/spec-registry-ui-17oct-mock.html` (seed rows; swapped for crew rows when they arrive).

## Build slices (after the owner's mock OK)
- S1 BE: migration `spec_hint_0001` adds `product_spec_registry.parser_hint text NULL` and `label_owned boolean NOT NULL
  DEFAULT false`. PATCH accepts `parser_hint` (spec_registry.edit), sets `label_owned` when the label changes;
  `seed_spec_registry` skips `label` on an owned row; `_serialise` returns both; `specification_lines` prints
  `Means: <hint>.`
- S2 BE: cross-field word guard on PATCH (`user_synonyms`, `user_values`) and POST `/{spec_key}/values`; GET returns
  `also_on` per word for the "Also on" note.
- S3 BE: `POST /api/v1/master-data/spec-registry/try-chatbot` {text<=500} -> `resolve_config` + `build_user_block` +
  `parse` (`app/services/chatbot/head/parser.py:750,856,1021`), returns entities `[{raw, kind, field_label, value}]`
  plus `prompt_version`; no DB write; ParserError -> 422 with a readable message. Needs spec_registry.view.
- S4 script: `scripts/publish_specs_token_prompt.py` (card item 7).
- S5 FE: HeaderTab meaning box + Unit read-only; WordsDataGrid "Also on" + refusal; History tab (AuditTrail);
  TryInChatbotDialog; gear links.

## Test list (red first)
BE (`tests/test_spec_registry_ui_17oct.py`, Postgres):
1. PATCH parser_hint stores trimmed one-line text; 201 chars -> 422; products.edit-only caller -> 403.
2. specification_lines prints `Means:` for numeric, enum and boolean rows; blank hint prints nothing.
3. prompt var `specs` re-renders with the new hint after commit (cache cleared).
4. seed_spec_registry keeps an owned label, repairs an unowned one, never writes parser_hint.
5. PATCH with `unit` does not change unit (and FE never sends it, vitest).
6. Adding a word that is another field's word / label / key -> 409 `spec_registry_word_on_other_key`, nothing stored;
   an existing overlap re-sent in the same PATCH saves; a suppressed word elsewhere does not block.
7. POST values with another field's word -> same 409.
8. GET row returns `also_on` for a shipped overlap.
9. try-chatbot with `parse` stubbed returns raw / field label / value; writes no row (count of chatbot.turns and
   audit_logs unchanged); ParserError -> 422; >500 chars -> 422; view permission enforced.
10. publish script: one run -> `{{specs}}`, `specs` in variables, unlabelled, idempotent; 0 or 2 runs -> refused;
    `--from-version` honoured; production label untouched.
FE (vitest): HeaderTab meaning box view/edit + counter, Unit read-only, payload has no `unit`; WordsDataGrid shows
the 409 message under the input and "Also on"; TryInChatbotDialog renders picks and the error line; record tabs include
History; gear has the two links.
