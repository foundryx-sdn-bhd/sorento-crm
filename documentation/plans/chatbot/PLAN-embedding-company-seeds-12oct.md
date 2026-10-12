# PLAN: per-company debtor seeds + certificate embedding company backfill (SCOPE-SEEDS)

Status: in progress (normal track: carries a data migration; security-reviewer joins because the
diff touches multi-company scoping).

Follow-up (a) of `PLAN-scope-leak-fix-12oct.md` (#1605).

## Problem

1. Synthetic `debtor:<code>` customer seeds (`embedding_backfill_service.py`, the "customer"
   source once the master is exhausted) are written with `company_id` NULL
   (`embedding_worker._company_id_for_source` returns None for them; migration 304 left them
   NULL). Since #1605 every company scope excludes them (`embedding_service._embedding_company_filter`,
   `entity_resolver._embedding_scope_sql`), so a contact's master-less debtor no longer resolves
   by vector similarity, even inside its own company. The seed itself is also built from the
   first order with that code in ANY company, and the backfill dedupes codes against the customer
   master of every company.
2. Migration 304 backfilled `company_id` for 15 owned source types but not `certificate` (or
   `product_spec`, keyed on a product id). Chunks embedded before 304 for those types are NULL,
   which reads as shared knowledge on `/external/rag/search` and in the resolver.

## Fix (no new mechanism)

- Seed id becomes `debtor:<company_id>:<code>`; `source_key` stays `debtor:<code>` (the resolver
  strips the prefix to get the `Order.debtor_code` value). One parse helper
  `parse_debtor_seed_id` in `embedding_worker.py`; legacy two-part ids parse as company None.
- Backfill emits one seed per (company, debtor_code) whose company has no customer master row
  with that code; worker reads the debtor name from that company's orders only and stamps
  `company_id` from the id. A legacy two-part id is refused by the worker (it would otherwise
  recreate a company-less row).
- `fuzzy_resolver` resolves a seed's debtor name within the seed's company.
- Migration `scope_seeds_debtor_company`: logs dry-run counts first, then (idempotent)
  copies each legacy seed document + current chunks to one per-company row per company whose
  live orders carry that code (vector reused, no re-embed needed), deletes the legacy two-part
  seed rows (derived data; staff lose nothing, the per-company copies replace them), and
  backfills `company_id` for `certificate` and `product_spec` rows from their source rows.
  NULL-company certificates are shared by design and stay NULL.
- Remove the #1605 `debtor:` exclusions in `_embedding_company_filter` and `_embedding_scope_sql`.

## Tests (red first, two companies)

- Backfill seeds: same debtor code in A and B yields two ids, each with its company; a code with
  an A customer master only seeds B.
- Worker: seed id stamps company_id and reads the name from that company's orders; legacy id fails.
- Search: an A-scoped `search_current` and tier-3/RAG SQL return A's seed, never B's.
- Migration: legacy seed copied per company, legacy rows removed, certificate chunk stamped,
  shared certificate stays NULL, second run is a no-op.
