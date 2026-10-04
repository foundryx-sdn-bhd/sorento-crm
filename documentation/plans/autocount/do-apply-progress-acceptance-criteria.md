# UAC: DO-APPLY-PROGRESS

- AC-1: When a Delivery Orders apply job starts its ingest, `import_jobs.total_rows` equals the
  number of snapshot documents and `processed_rows` is 0, readable from another session
  (the job page) before the batch commits.
- AC-2: During the ingest, every 100 documents, `processed_rows` advances and
  `successful_rows` (created + updated + adopted), `failed_rows` (failed + retryable) and
  `skipped_rows` (unchanged) reflect the documents processed so far, readable from another
  session while the apply's own transaction is still open.
- AC-3: Publishing progress never commits the apply's own session: a failure after the
  ingest (before the batch commit) still writes no delivery order.
- AC-4: A progress-publish failure never fails the apply.
- AC-5: When the apply finishes, total/processed/successful/failed/skipped hold the final
  numbers, and `metadata.autocount_apply.counts` keeps its existing keys.
- AC-6: The import-job page for a finished DO apply job shows a Results block with
  Created, Adopted, Updated, Unchanged, Failed, Retryable and Lines deleted from
  `metadata.autocount_apply.counts`. Usable at 375px and 1280px.
