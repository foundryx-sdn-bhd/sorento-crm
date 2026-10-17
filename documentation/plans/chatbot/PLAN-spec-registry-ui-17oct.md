# PLAN: spec-registry-ui (owner edits chatbot closed lists from the front end)

Status: Plan (discovery in progress). Track: TBD after discovery (expected full track: permission + audit).
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
(the docstring at `chatbot_parser_prompt.py:1278` still says "at the next publish"): needs a crew data check.

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

## Behaviour card
(pending crew ask)

## Mock
(pending)
