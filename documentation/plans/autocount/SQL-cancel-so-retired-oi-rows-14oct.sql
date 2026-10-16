-- CANCEL-STOP-AT-SO, owner data repair, step 1 of 2: READ ONLY. Changes nothing.
--
-- Lists every order inquiry row retired with cause `cancel_so` (audit event OI_ROW_CANCELLED)
-- since 14 Oct 2026 00:00 MYT, and tells apart the two writers of that cause:
--   * "AutoCount Cancel SO": the cancel ingest hook (run_cancel_hooks) the owner's ruling of
--     14 Oct removes. Its stamp names a Cancel SO document the CRM holds for that company (or
--     the hook's "AutoCount" fallback) and CS never transferred the line (`cancelled_at` IS
--     NULL). Not keyed on `cancelled_by_cancel_doc`: a cancel lowered, voided or re-decided
--     since clears that flag and reopens the line, but its rows stayed retired.
--   * "CS board Cancel SO": CS pressed Transfer to Cancel SO on the board (`cancelled_at` set
--     in the same transaction as the retire, never cleared). Intended, NOT to be restored.
-- `restore` says what step 2 (SQL-cancel-so-restore-oi-rows-14oct.sql) does with the row.
--
-- What the audit row holds: only cause, qty, item_code and SO line no (`_record_cancel`).
-- `order_inquiry_rows` and `order_inquiry_links` are not audited, so the row's state before the
-- retirement is derived from the note the retirement appended to:
--   "...; Transferred to Cancel SO: <XS no>"        the retirement's own stamp, always last
--   "...; Unlinked from <doc>; Transferred to ..."  the retirement also removed a link: the row
--                                                    was partly linked / placed and that link
--                                                    is gone (link qty is not recorded anywhere)
-- Run: psql "$DATABASE_URL" -f SQL-cancel-so-retired-oi-rows-14oct.sql

BEGIN TRANSACTION READ ONLY;

-- 1. The retired rows.
WITH cutoff AS (
    -- audit_logs.changed_at is clock_timestamp() stored without a zone on a session the app
    -- opens with timezone=utc (app/database.py), i.e. naive UTC.
    SELECT (timestamptz '2026-10-14 00:00:00+08' AT TIME ZONE 'UTC') AS at
), retired AS (
    SELECT DISTINCT ON (a.entity_id)
           a.entity_id, a.changed_at AS retired_at, a.user_id, a.actor_type, a.integration_id,
           a.new_values
    FROM public.audit_logs a, cutoff
    WHERE a.entity_type = 'project_order_inquiry_rows'
      AND a.event = 'OI_ROW_CANCELLED'
      AND a.new_values ->> 'cause' = 'cancel_so'
      AND a.changed_at >= cutoff.at
    ORDER BY a.entity_id, a.changed_at DESC
)
SELECT r.retired_at,
       so.so_number,
       pl.line_no                                   AS project_line_no,
       oir.item_code,
       r.new_values ->> 'qty'                       AS qty,
       oir.verb,
       oir.state                                    AS state_now,
       oir.ack_state,
       CASE
           WHEN core.id IS NULL THEN 'unknown (no core line)'
           WHEN core.cancelled_at IS NOT NULL THEN 'CS board Cancel SO'
           WHEN ac.held THEN 'AutoCount Cancel SO'
           ELSE 'unknown'
       END                                          AS source,
       core.line_status                             AS core_line_status_now,
       substring(oir.note FROM 'Transferred to Cancel SO: ([^;]*)$') AS cancel_doc,
       oir.note ~ '(^|; )Unlinked( from [^;]*)?; Transferred to Cancel SO: [^;]*$' AS lost_links,
       oir.po_line_id IS NOT NULL                   AS po_tagged,
       CASE
           WHEN core.id IS NULL OR core.cancelled_at IS NOT NULL OR NOT ac.held
               THEN 'leave (not AutoCount)'
           WHEN oir.state <> 'cancelled'
               THEN 'leave (row changed since)'
           WHEN oir.note IS NULL OR oir.note !~ '(^|; )Transferred to Cancel SO: [^;]*$'
               THEN 'leave (stamp not last, row changed since)'
           WHEN oir.note ~ '(^|; )Unlinked( from [^;]*)?; Transferred to Cancel SO: [^;]*$'
                OR oir.po_line_id IS NOT NULL
               THEN 'MANUAL (was linked / placed, re-link by hand)'
           ELSE 'restore to raised'
       END                                          AS restore,
       oir.note,
       oir.id                                       AS row_id
FROM retired r
JOIN projects.order_inquiry_rows oir ON oir.id::text = r.entity_id
LEFT JOIN projects.sales_order_lines pl ON pl.id = oir.so_line_id
LEFT JOIN public.sales_order_lines core ON core.id = pl.core_sales_order_line_id
LEFT JOIN public.sales_orders so ON so.id = core.sales_order_id
CROSS JOIN LATERAL (
    SELECT substring(oir.note FROM 'Transferred to Cancel SO: ([^;]*)$') = 'AutoCount'
           OR EXISTS (SELECT 1 FROM public.autocount_cancel_docs d
                      WHERE d.kind = 'cancel_so' AND d.company_id = oir.company_id
                        AND d.doc_no = substring(oir.note FROM 'Transferred to Cancel SO: ([^;]*)$'))
           AS held
) ac
ORDER BY r.retired_at, so.so_number, pl.line_no;

-- 2. Rows the same hook sent back to purchasing's To confirm (`flag_rows_for_cancelled_lines`:
--    acknowledged, not cancelled -> ack_state 'changed'). Not audited either; these are the
--    candidates on lines an AutoCount Cancel SO still cancels. Step 2 does NOT touch them: the earlier
--    changed_at is not recorded, so this is the owner's call.
WITH cutoff AS (
    -- changed_at is written as datetime.utcnow() (naive UTC).
    SELECT (timestamptz '2026-10-14 00:00:00+08' AT TIME ZONE 'UTC') AS at_utc
)
SELECT so.so_number, pl.line_no AS project_line_no, oir.item_code, oir.verb, oir.state,
       oir.ack_state, oir.changed_at, oir.id AS row_id
FROM projects.order_inquiry_rows oir
JOIN projects.sales_order_lines pl ON pl.id = oir.so_line_id
JOIN public.sales_order_lines core ON core.id = pl.core_sales_order_line_id
JOIN public.sales_orders so ON so.id = core.sales_order_id
CROSS JOIN cutoff
WHERE core.cancelled_by_cancel_doc
  AND core.cancelled_at IS NULL
  AND oir.state <> 'cancelled'
  AND oir.ack_state = 'changed'
  AND oir.changed_at >= cutoff.at_utc
ORDER BY so.so_number, pl.line_no;

-- 3. Purchasing emails written since the cutoff that carry a CANCEL ALL line (cannot be
--    unsent; listed so purchasing can be told which ones to ignore).
SELECT e.created_at, e.status, e.sent_at, e.recipient_email, e.subject
FROM public.email_outbox e
WHERE e.created_at >= (timestamptz '2026-10-14 00:00:00+08' AT TIME ZONE 'UTC')  -- naive UTC
  AND (e.subject ILIKE '%CANCEL ALL%' OR e.body_text ILIKE '%CANCEL ALL%')
ORDER BY e.created_at;

ROLLBACK;
