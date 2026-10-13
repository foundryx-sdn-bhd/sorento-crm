# PLAN PV2-RESOLVE (13 Oct night)

Status: in progress (Build). Track: full lane (size L, carries a data migration `pv2_0002_alias_seed`).
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
3. Spec registry: `app/api/v1/master_data/spec_registry.py` create (`:827`), patch (`:911`), add value (`:1195`);
   alias source `merged_synonyms(row)` (`app/services/product_spec_registry.py:1623`).

Neither category nor attachment-type schemas expose `search_synonyms` today, so (1) and (2) gain an optional
`search_synonyms` field on their Create/Update schemas (additive, backend only) and the check runs when it is sent.

## Decisions taken per contract intent (noted in the PR)
- Lanes meet via the contract types; until PV2-CORE's `schema.py` and PV2-CARRY's `state.py` land, `resolve.py`
  imports them when present and the tests carry a local copy of the contract shapes (`tests/chatbot/pv2/_contract_types.py`),
  deleted at integration.
- Spec attribute null + a number: real data ranges come from `product_specifications` for the held class.
