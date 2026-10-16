# PLAN: RELEASE-FIX-3 - release blockers (2 stale tests + #1719 promotion over-block)

Status: in progress (small fix track: test-only for 1-2, one service helper for 3; no migration,
no RBAC change; security-reviewer runs because item 3 is the SEC-PROMO-ACCESS hotfix's scope)

UAC: `release-fix-3-acceptance-criteria.md` beside this file.

Release run 38838139634 (main 05f030e15) failed three things, all reproduced locally on a
throwaway DB (`sorento_rf3_ci`, bootstrap_env) before any change.

## 1. Serial: frc_0001 fails after fin_0001 / fin_0002 (order-dependent)

`InvalidRequestError: schema translate map which previously did not have None present as a key
now has None present`. Cause: the fin migration tests translate `{"finance": scratch}` on a
connection of `app.database.engine`. `blank_schema_engine()` (`tests/_pg_fixture.py`) is an
option engine of that same engine, so it shares the compiled cache; the cache key records only
that a map is present, not its keys. A statement first compiled under the fin map (no `None`
placeholders) is then reused under the blank map (which carries `None`) and SQLAlchemy refuses.

Fix: every migration test's translate map carries `None: None` (default schema unchanged), so its
compiled statements have the `None` placeholders: fin_0001:133, fin_0002:72, and the seven
sales_000x maps (same latent defect, not yet tripped only because of file order).

## 2. Serial: sodv_0001 asserts a hard-coded alembic head

`test_the_migration_chain_has_a_single_head_that_is_ours` pins `["sodv_0001_ol_from_doc_idx"]`;
main's head moved to `sds_0001_stock_debt_snapshots`. Fix: assert exactly one head, no literal
(`tests/test_alembic_revision_ids.py:66` already guards the single head).

## 3. Shard 4: brand_scope_round2 promotion_products, both contacts see nothing

#1719 added `ContactAccessTypeService.translate_names_to_codes_for_caller`: `contact_id` present
and `access_levels` absent returns `[]` (nothing). The MCP drops an empty list, so the route
cannot tell "the gate emptied the list" from "the caller sent none". Every contact read without
`access_levels` therefore returns nothing, including n8n callers that never pass it.

Rule (brief): absent `access_levels` on a contact read derives the contact's OWN access level.
Fix in the one helper (covers all six routes #1719 wired to it):
- `contact_id` + absent/blank names: resolve the contact (`resolve_contact_id`, internal or
  Respond.io id) and return its assigned ACTIVE access codes; unknown contact or none held: `[]`.
- names given: unchanged (`translate_names_to_codes`; zero matches is `[]`).
- no `contact_id`: unchanged (`None`, no filter, staff reads).

Never wider than the contact's own levels, so the Cabana case (Sorento Dealer contact) still
cannot reach a Cabana OFFICE USE file.

Routes on the helper (all fixed by the one change):
`app/api/v1/marketing/promotion_products.py:306`, `app/api/v1/marketing/promotions.py:189`,
`:293`, `:585`, `app/api/v1/marketing/promotion_attachments.py:114`, `:270`.

Test seed: `tests/_brand_scope_seed.py` contacts held a random office code while the seeded
promotion carries `["dealer"]`; round2's promotion case only passed through the leak. The world
now gives both contacts one shared access type and seeds the promotion at it, isolating the brand
axis (the same move #1719 made for `test_rearch_r12_handpass12_c.py`).

## Proof

red-first commit (tests only), then fix, kill test, reviewer + security-reviewer, crew labels ci.
