# UAC: MIGRATION-INDEX

- AC-1: After `scope_seeds_debtor_company.upgrade()` on a schema without it, a VALID
  `ix_embedding_chunks_document_id` on `embedding_chunks (document_id)` exists.
- AC-2: After `mig_idx_chunks_document_id.upgrade()` on a schema without it (prod already at
  scope_seeds), the same index exists; on a schema that has it, the upgrade is a no-op.
- AC-3: An INVALID leftover index of that name is replaced by a valid one.
- AC-4: scope_seeds' data statements run under `lock_timeout = 5s` and `statement_timeout = 120s`;
  the previous values are back after the upgrade.
- AC-5: `alembic heads` prints exactly one head; `alembic upgrade head` then `downgrade -1` and
  `upgrade head` again succeed on a scratch DB.
- AC-6: `EXPLAIN` of the cascade's RI query (`DELETE FROM embedding_chunks WHERE document_id =
  <id>`, what Postgres runs per deleted document; plain EXPLAIN of the parent DELETE does not
  show trigger plans) is an index scan on `ix_embedding_chunks_document_id`, on a sandbox DB.
