-- CANCEL-STOP-AT-SO, owner data repair, step 2 of 2: RESTORE. Owner only, in psql.
--
-- Puts back the order inquiry rows the AutoCount Cancel SO hook retired since 14 Oct 2026
-- 00:00 MYT (step 1, SQL-cancel-so-retired-oi-rows-14oct.sql, lists them; read it first).
-- Run ONLY after the CANCEL-STOP-AT-SO fix is deployed, or the next Cancel SO sync retires
-- them again.
--
-- What the retirement did to a row (ProjectOrderInquiryService.retire_rows_for_cancelled_lines):
--   state raised / partly_linked / placed -> cancelled; "; Transferred to Cancel SO: <XS no>"
--   appended to the note; any links removed (each appending "; Unlinked from <doc>" first);
--   suggested links dropped; one OI_ROW_CANCELLED audit row (cause cancel_so). Nothing else on
--   the row moved (qty, dates, verb, ack_state, po_ref / po_line_id untouched).
-- What this script restores, for a row it can restore exactly:
--   state -> raised (a row with no link is `raised`: INQUIRY_LINK_STATES), note -> the note
--   without the retirement's stamp (NULL when the stamp was the whole note).
-- What it does NOT restore (listed in 2, left for CS / purchasing by hand):
--   rows whose retirement removed links (were partly linked / placed: the link qty is not
--   recorded anywhere) and rows tagged to a PO (`po_line_id`, were placed).
-- Not restorable by any script: the "CANCEL ALL" emails already sent, and suggested links
-- (the next link suggestion pass proposes them again).
--
-- Shape: BEGIN, build the list, show it, update, verify, then STOP. Nothing is saved until the
-- owner reads the verify output and types COMMIT (or ROLLBACK). There is no COMMIT below.
-- Run it INTERACTIVELY (psql -f would end the session and roll the transaction back):
--   psql "$DATABASE_URL"
--   \i SQL-cancel-so-restore-oi-rows-14oct.sql
--   ... read the output, then type COMMIT; or ROLLBACK;

\set ON_ERROR_STOP on
BEGIN;

CREATE TEMP TABLE cancel_so_restore ON COMMIT DROP AS
WITH cutoff AS (
    SELECT (timestamptz '2026-10-14 00:00:00+08' AT TIME ZONE current_setting('TimeZone')) AS at
), retired AS (
    SELECT DISTINCT ON (a.entity_id) a.entity_id, a.changed_at AS retired_at
    FROM public.audit_logs a, cutoff
    WHERE a.entity_type = 'project_order_inquiry_rows'
      AND a.event = 'OI_ROW_CANCELLED'
      AND a.new_values ->> 'cause' = 'cancel_so'
      AND a.changed_at >= cutoff.at
    ORDER BY a.entity_id, a.changed_at DESC
)
SELECT oir.id                                                   AS row_id,
       oir.company_id,
       oi.project_sales_order_id,
       r.retired_at,
       so.so_number,
       pl.line_no                                               AS project_line_no,
       oir.item_code,
       oir.note                                                 AS note_now,
       substring(oir.note FROM 'Transferred to Cancel SO: ([^;]*)$') AS cancel_doc,
       NULLIF(regexp_replace(oir.note, '(^|; )Transferred to Cancel SO: [^;]*$', ''), '')
                                                                AS note_before,
       (oir.note ~ '(^|; )Unlinked( from [^;]*)?; Transferred to Cancel SO: [^;]*$'
        OR oir.po_line_id IS NOT NULL)                          AS manual
FROM retired r
JOIN projects.order_inquiry_rows oir ON oir.id::text = r.entity_id
JOIN projects.order_inquiries oi ON oi.id = oir.order_inquiry_id
JOIN projects.sales_order_lines pl ON pl.id = oir.so_line_id
JOIN public.sales_order_lines core ON core.id = pl.core_sales_order_line_id
JOIN public.sales_orders so ON so.id = core.sales_order_id
WHERE core.cancelled_by_cancel_doc          -- an AutoCount Cancel SO cancelled the line
  AND core.cancelled_at IS NULL             -- and CS never transferred it on the board
  AND oir.state = 'cancelled'               -- nothing revived the row since
  AND oir.note ~ '(^|; )Transferred to Cancel SO: [^;]*$';  -- the stamp is still the last word

-- The stamp must name a Cancel SO document the CRM holds (or the hook's "AutoCount" fallback).
DELETE FROM cancel_so_restore c
WHERE c.cancel_doc <> 'AutoCount'
  AND NOT EXISTS (SELECT 1 FROM public.autocount_cancel_docs d
                  WHERE d.kind = 'cancel_so' AND d.doc_no = c.cancel_doc);

-- 1. Rows this script restores to raised (expected: the step 1 rows marked "restore to raised").
SELECT so_number, project_line_no, item_code, cancel_doc, retired_at, note_now, note_before, row_id
FROM cancel_so_restore WHERE NOT manual ORDER BY so_number, project_line_no;

-- 2. Rows left for manual handling (were linked / placed). Not touched.
SELECT so_number, project_line_no, item_code, cancel_doc, note_now, row_id
FROM cancel_so_restore WHERE manual ORDER BY so_number, project_line_no;

-- 3. Restore. The note guard skips a row that moved after the list above was built.
UPDATE projects.order_inquiry_rows o
SET state = 'raised',
    note = c.note_before
FROM cancel_so_restore c
WHERE o.id = c.row_id
  AND NOT c.manual
  AND o.state = 'cancelled'
  AND o.note = c.note_now;

-- 4. The trail: one audit row per restored row, next to the OI_ROW_CANCELLED it reverses.
INSERT INTO public.audit_logs (id, entity_type, entity_id, action, event, new_values,
                               description, company_id, root_entity_type, root_entity_id)
SELECT gen_random_uuid(), 'project_order_inquiry_rows', c.row_id::text, 'EVENT',
       'OI_ROW_RESTORED',
       jsonb_build_object('cause', 'cancel_so_restore', 'cancel_doc', c.cancel_doc,
                          'state', 'raised'),
       'CANCEL-STOP-AT-SO owner repair: AutoCount Cancel SO retirement reversed',
       c.company_id, 'project_sales_orders', c.project_sales_order_id::text
FROM cancel_so_restore c
JOIN projects.order_inquiry_rows o ON o.id = c.row_id
WHERE NOT c.manual AND o.state = 'raised';

-- 5. VERIFY before COMMIT. Expect: restored = rows in 1; still_cancelled = 0;
--    note_mismatch = 0; audit_rows = restored.
SELECT
  (SELECT count(*) FROM cancel_so_restore WHERE NOT manual)                    AS listed,
  (SELECT count(*) FROM cancel_so_restore c JOIN projects.order_inquiry_rows o ON o.id = c.row_id
    WHERE NOT c.manual AND o.state = 'raised')                                 AS restored,
  (SELECT count(*) FROM cancel_so_restore c JOIN projects.order_inquiry_rows o ON o.id = c.row_id
    WHERE NOT c.manual AND o.state = 'cancelled')                              AS still_cancelled,
  (SELECT count(*) FROM cancel_so_restore c JOIN projects.order_inquiry_rows o ON o.id = c.row_id
    WHERE NOT c.manual AND o.note IS DISTINCT FROM c.note_before)              AS note_mismatch,
  (SELECT count(*) FROM public.audit_logs a JOIN cancel_so_restore c ON a.entity_id = c.row_id::text
    WHERE a.event = 'OI_ROW_RESTORED')                                         AS audit_rows;

-- STOP. Read 1, 2 and 5. Then type   COMMIT;   to keep it, or   ROLLBACK;   to undo it.
