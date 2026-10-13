# PLAN PV2-RESOLVE (13 Oct night)

Status: reviewed READY (d229106aa), awaiting crew merge into crew/pv2. Track: full lane (size L, carries a data migration `pv2_0002_alias_seed`).
Contract: `documentation/plans/chatbot/CONTRACT-PV2-13oct.md` section 4 (this lane), sections 1, 3 (types it consumes).
UAC: the contract's section 4 rules are the acceptance criteria; one table test per rule in
`tests/chatbot/pv2/test_resolve_*.py`.

## Files this lane owns (contract section 9)
- `sorento_crm_backend/app/services/chatbot/pv2/resolve.py` - exact resolvers (entities, specs, attachment type),
  customer-name candidates, unknown-words log writer.
- `sorento_crm_backend/app/services/chatbot/pv2/aliases.py` - alias sources, precedence lookup, `check_conflicts`,
  `open_clashes` (what `publish_v2` refuses on).
- `sorento_crm_backend/app/services/chatbot/pv2/alias_seed.py` - the seed data (codes + aliases), imported by the migration.
- `sorento_crm_backend/alembic/versions/pv2_0002_alias_seed.py` - additive seed, `down_revision = pv2_0001_schema`.

## The 3 alias save paths this lane edits (named in commit 1, no other lane edits them)
1. Product category: `app/services/product_service.py` `ProductCategoryService.create_category` / `update_category`
   (called from `app/api/v1/master_data/categories.py:167,187`); alias column `product_categories.search_synonyms`.
2. Attachment type: `app/services/resources_service.py` `AttachmentTypeService.create_type` / `update_type`
   (called from `app/api/v1/resources/attachment_types.py:64,81`); alias column `attachment_types.search_synonyms`
   (new, added by PV2-CORE `pv2_0001_schema`).
3. Spec registry: `app/api/v1/master_data/spec_registry.py` create (`:827`) and patch (`:911`); alias source =
   the row's `_self` words in `merged_synonyms(row)` (`app/services/product_spec_registry.py:1623`). Add value
   (`:1195`) is NOT checked: it only adds enum value words, and the index prints no value words (PV2-CORE
   `pv2/index.py _spec_attributes`), so they are not aliases and cannot clash.

Neither category nor attachment-type schemas expose `search_synonyms` today, so (1) and (2) gain an optional
`search_synonyms` field on their Create/Update schemas (additive, backend only) and the check runs when it is sent.

## Decisions taken per contract intent (noted in the PR)
- Alias kinds and keys are the index's (PV2-CORE `pv2/index.py`), so one precedence row means the same thing
  to the index and to a save: `attachment_type` (code), `product_class` (slug of class_label; words = active
  searchable categories' `search_synonyms` + `CLASS_SYNONYMS`), `spec_attribute` (`_self` words).
- Entity keys: `brand` = brand_code, `product_class` = class slug, `product_type` = product_type value,
  `attachment_type` = code. `DbResolver.from_index_keys(db, IndexKeys)` builds the pinned set.
- Every product read that can show a row (closest rows, ranges, candidates) is a top-level `Product` ORM query:
  the company/brand scope listener does not reach into subqueries or aggregates (measured in a test).
- `resolve_report_spec` lives in `DbResolver` (REPORTS defines `ReportResolution`, no resolver): brand and
  category (product_class) keys -> ids, customer names exact or real-name candidates keyed by id, location
  -> warehouse code; sales agents stay unresolved for the report lane to say.
- Seeds: precedence rows for the `CLASS_SYNONYMS` words two classes share (bathtub, bath tub, tub -> bathtub;
  jacuzzi -> jacuzzi). Attachment aliases from recorded chat words + en/zh/ms (owner 13 Oct: seed them).
- Lanes meet via the contract types; until PV2-CORE's `schema.py` and PV2-CARRY's `state.py` land, `resolve.py`
  imports them when present and otherwise defines the contract shapes itself (a `try/except ImportError` stand-in,
  deleted at integration). Tests build every type with all fields, so they run against CORE's strict models too.
- Spec attribute null + a number: real data ranges come from `product_specifications` for the held class.
- Attachment-type aliases are DATA only (crew, 13 Oct eval prep): printed as "aka" they pushed the index to 8,460
  chars, over the 8,000 cap. The index now prints `key = name` for attachment types (header text unchanged; parsing
  never read the aka). Trade-off for the owner to confirm: the model no longer sees "gambar", "图纸", "sijil" etc.,
  so attachment-type turns rely on the model's own vocabulary or end in a clarify question; expect that in evals.
  Headroom is about 120 chars on the eval DB (about 7,877 of 8,000). Next trim if it is exceeded: the
  PRODUCT CLASSES aka lists (largest remaining alias block), never the cap.
