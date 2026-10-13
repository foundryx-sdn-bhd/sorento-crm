# PLAN: index embedding_chunks.document_id so scope_seeds does not stall a release (MIGRATION-INDEX 13 Oct)

Status: IN PROGRESS 2026-10-13. Small fix track by size, but it carries a migration, so it gets
the migration evidence below. No auth/RBAC, no ingest surface change.

## Journey

The owner dispatches a production release of main d01ca05cb. Release run 38328542953 fails: the
green backend is not healthy after 240 s while alembic runs
`oihr_0006_no_remark_column -> scope_seeds_debtor_company`. The release must go out, and a
migration that cannot finish must fail fast instead of hanging past the health window.

## Cause (crew analysis, read from code, not confirmed on prod)

- `scope_seeds_debtor_company.py:153-154` deletes the legacy company-less debtor seeds (655 rows
  on prod per the crew analysis) from `embedding_chunks`, then from `embedding_documents`.
- `embedding_chunks.document_id` references `embedding_documents.id` ON DELETE CASCADE
  (`app/models/embeddings.py:91-94`, `alembic/versions/142_pgvector_embedding_pipeline.py:93`)
  but has no index (`embeddings.py:116-128`, `142_...:96-118`). Postgres runs the cascade's RI
  query `DELETE FROM embedding_chunks WHERE document_id = $1` once per deleted document, so each
  of the 655 rows is a sequential scan of the vector table. Steps 1b
  (`JOIN embedding_chunks c ON c.document_id = od.id`, `NOT EXISTS ... x.document_id = nd.id`)
  are the same shape.
- `scripts/blue_green_deploy.sh:24` waits 120 x 2 s = 240 s, then exits 1 without stopping
  green (`:69-72`), so the migration runs on unattended.

## Ordering decision

Alembic runs the whole upgrade in ONE transaction (`alembic/env.py`, no
`transaction_per_migration`), so a killed run rolls everything back, but green is not stopped on
timeout, so the stuck run may have finished and committed `scope_seeds_debtor_company` on prod.
A revision placed BEFORE scope_seeds would then never run there (alembic only reads the head).
So the index is built on both paths:

1. `scope_seeds_debtor_company.upgrade()` builds `ix_embedding_chunks_document_id` first
   (CONCURRENTLY, IF NOT EXISTS, inside `autocommit_block()`), before any DELETE. Prod still at
   `oihr_0006` gets the index before the slow statements.
2. A new revision `mig_idx_chunks_document_id` AFTER scope_seeds (the new single head) builds the
   same index the same way. Prod already at scope_seeds gets it from here; everywhere else it is a
   no-op. Both drop an INVALID leftover first (a failed CONCURRENTLY build leaves one that
   IF NOT EXISTS would otherwise keep forever), the `aud_0001` / `identity_0001` pattern.
3. `index=True` on the model column, so `create_all` (scratch schemas, the shared dev DB) has it.
4. `scope_seeds_debtor_company` sets `lock_timeout = 5s`, `statement_timeout = 120s` for its
   data statements (after the autocommit block, since SET LOCAL ends with the transaction it was
   set in) and restores the previous values at the end, so later revisions in the same run do not
   inherit them.

## Optional, separate commit, flagged for the owner

`blue_green_deploy.sh` stops the NEW colour's containers on a health timeout, so a hung
migration is ended rather than left running. Timeouts unchanged.

## UAC

See `migration-index-acceptance-criteria.md`.
