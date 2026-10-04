# PLAN: AutoCount DO Apply job shows live progress and counts (DO-APPLY-PROGRESS)

Status: in review, PR #1414 (small fix track: no migration, no auth/RBAC change, no new ingest surface)

A failed apply zeroes successful/failed/skipped (the batch rolled back, so published tallies
would overstate it). Browser evidence: `evidence/do-apply-progress/README.md`.

## Journey

The owner confirms a Delivery Orders pull. The Apply job (dev, 6,487 docs, ~2m12s) sat on
STARTED with Total 0 / Processed 0 / Successful 0 the whole run. They want to see it move,
and see the final counts once it finishes.

## Premises (verified on main 757b41da)

- Preview publishes progress through its own fresh session:
  `sorento_crm_backend/app/tasks/autocount_pull_tasks.py:92-107` (`_publish_preview_progress`),
  used by the DO preview at `:766` and `:771` (`on_progress`).
- Apply never touches `total_rows` / `processed_rows`: `apply_autocount_pull` `:191-237`,
  `_apply_delivery_orders` `:792-818` calls `ingest.ingest(...)` with no `on_progress`.
- The batch commits once at the end, `:803`; per-record SAVEPOINTs inside
  `AutocountDocIngestService.ingest` (`app/services/autocount_doc_ingest_service.py:493-540`).
- `on_progress` fires every 100 records plus once at the end, with `(processed, total)` only.

## Fix

1. Backend: `_apply_delivery_orders` publishes `total_rows` before the ingest and
   processed/successful/failed/skipped during it, through the same fresh-session publisher
   preview uses (a separate short transaction, never the apply's own session, so the
   single-commit semantics are unchanged). The running tallies come from the records the
   ingest has produced so far: `ingest` exposes its in-flight `result.records` list as
   `ingest.live_records` (one attribute, no callback signature change, so the preview's
   two-argument callbacks are untouched), and the task's callback tallies it. Mapping: successful = created + updated + adopted, failed = failed + retryable,
   skipped = unchanged. After the final commit the job row gets the same final numbers.
2. Frontend: the import-job page Results section, for a finished `autocount_apply` job of
   entity `delivery_orders`, shows created / adopted / updated / unchanged / failed /
   retryable / lines deleted from `metadata.autocount_apply.counts`.

## Out of scope

Products and stock apply progress (same gap, not reported; trigger: an owner report).
