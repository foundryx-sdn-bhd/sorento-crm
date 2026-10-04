# PLAN: product ref collision (ItemKey vs ItemCode refs)

Status: PR #1459 READY, review + security clean after round 1, awaiting CI + owner merge (L steps minus migration; prod cleanup SQL runs after deploy). Track: L pipeline (external ingest), no migration expected.

## Problem
Document lines link products under `BOOK:<ItemAutoKey>` (shared-service `presets.py:250/410/480`);
the product feed sends `BOOK:<ItemCode>` (`presets.py:805`). For numeric item codes the two collide.
`MasterIngestService._apply_scoped` trusts a ref hit before the code (`master_ingest_service.py:1213-1229`)
and `_update` writes every sent column including `product_code` (`:1635-1657`). Result on PROD 3 Oct:
push of Mocha ItemCode 2001/2002/2003 tries to rename MKT4524SS-DIY / MKT4524SS-GM-DIY /
MKT4528ASS-DIY and fails on `uq_products_company_product_code`. When no product owns the code yet
the same path renames the wrong product silently.

Line side: `MasterRefResolver._resolve_master` returns a ref hit without checking the sent code
(`master_ref_resolver.py:168-169`).

## Design (owner rulings 1 + 2, 3 Oct: product code is identity, product refs unused)
1. Product feed (`master_ingest_service._apply_scoped`): products never read or write
   `integration_references`. Match by company + normalised code (existing adopt/create paths); no
   `_link`, no origin guard (preload (b)/(c) skipped for products).
2. Line ladder (`master_ref_resolver._resolve_master`, Product): code only; no code or unknown code
   -> existing unknown-product verdict (SO/PO line dropped D9, SPO retryable); never link a product
   ref (`_link_product_ref`, generic rung :218).
3. Billing `_product`: code only; miss -> `product_unresolved` + NULL.
4. Snapshot preload (`document_ingest_service` ~:713): code only.
5. Product deletions (`deletion_service._delete_one` :381): code only (shared service sends `codes`
   for products, `sinks_sorento.codes_from_refs`). Today a delete of ItemCode 2001 hits the ItemKey
   ref and would remove MKT4524SS-DIY.
6. DO/GRN, stock balances already code only.
7. Read-back (`MasterReadService` products, document/SPO/billing line `product_ref`) left as is: shared
   service never calls `read_back` (`sinks_sorento.py:786`, no callers on ss main 6fe2b8d4).
8. No migration, no name matching. Prod cleanup after deploy:
   `sorento_crm_backend/scripts/cleanup_product_integration_references.sql` (owner runs).

Rename (open owner question, built as (a)): a renamed AutoCount item becomes a new product; the old
one is kept untouched.

## Files
- `sorento_crm_backend/app/services/master_ingest_service.py`
- `sorento_crm_backend/app/services/master_ref_resolver.py`
- `sorento_crm_backend/app/services/finance/billing_document_ingest_service.py`
- `sorento_crm_backend/app/services/document_ingest_service.py`
- `sorento_crm_backend/app/services/deletion_service.py`
- `sorento_crm_backend/scripts/cleanup_product_integration_references.sql`
- tests: `tests/test_product_ref_collision.py`, `tests/test_product_ref_collision_billing.py`

## Pipeline
tester (red, from UAC) -> `test(red):` commit + red-proof -> coder -> kill-proof -> reviewer +
security-reviewer (external ingest) -> CI. No UI, no browser pass, no hand test unless owner asks.
