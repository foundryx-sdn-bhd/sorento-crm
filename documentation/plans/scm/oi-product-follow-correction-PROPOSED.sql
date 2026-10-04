-- OI-PRODUCT-FOLLOW: one-off correction for OI rows already wrong on prod.
-- FOR THE OWNER TO RUN. Crew never runs this on prod.
--
-- Owner ruling R3 (2 Oct): fix ALL live OI rows whose item_code differs from their
-- AutoCount SO line's product, linked or not. Links are NOT touched (R2: the link
-- follows AutoCount and flows through).
--
-- Needs this lane's migration first (oipf_0001_prev_item_code adds
-- projects.order_inquiry_rows.previous_item_code). Step 0 refuses to run without it.
--
-- What this does NOT do (raw SQL skips the app):
--   * no handover email to purchasing and no audit row. Rows purchasing had confirmed go
--     back to To confirm, so tell purchasing by hand that these lines changed product;
--   * the scope is wider than what a Confirm moves from now on (live ORDER / ORDER BACK
--     rows with a catalogue code). The dry run below breaks the rows down by verb, by
--     used (redirected) rows, and by catalogue / non-catalogue / blank code, so you can
--     see exactly what "all" covers before committing.
--
-- How to run:
--   1. Run the read-only check (oi-product-follow-prod-readonly.sql, Q4/Q5) and note
--      rows_mismatched.
--   2. Run THIS file as is. It ends in ROLLBACK: nothing is kept. Compare the dry-run
--      count (step 1) and the after-count (step 4, must be 0) with what Q5 reported.
--   3. Only if both match, change the last line from ROLLBACK to COMMIT and run it again.

BEGIN;

-- Step 0: the migration is in.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'projects' AND table_name = 'order_inquiry_rows'
      AND column_name = 'previous_item_code'
  ) THEN
    RAISE EXCEPTION 'previous_item_code is missing: deploy migration oipf_0001_prev_item_code first';
  END IF;
END $$;

-- Step 1: DRY RUN. What will change, before anything does.
CREATE TEMP TABLE _oi_product_fix ON COMMIT DROP AS
SELECT r.id             AS row_id,
       r.item_code      AS old_code,
       cp.product_code  AS new_code,
       r.ack_state      AS old_ack_state,
       (SELECT count(*) FROM projects.order_inquiry_links l WHERE l.row_id = r.id) AS links
FROM projects.order_inquiry_rows r
JOIN projects.sales_order_lines pl ON pl.id = r.so_line_id
JOIN public.sales_order_lines sol  ON sol.id = pl.core_sales_order_line_id
JOIN public.products cp            ON cp.id = sol.product_id
WHERE r.state <> 'cancelled'
  AND r.item_code IS DISTINCT FROM cp.product_code;

SELECT count(*)                                 AS rows_to_fix,
       count(*) FILTER (WHERE links > 0)        AS rows_with_link_kept,
       count(*) FILTER (WHERE old_ack_state IN ('acknowledged', 'changed')) AS rows_back_to_to_confirm
FROM _oi_product_fix;
SELECT r.verb,
       r.redirected_to_pool                                  AS used_row,
       CASE WHEN f.old_code IS NULL THEN 'blank code'
            WHEN EXISTS (SELECT 1 FROM public.products p WHERE p.product_code = f.old_code)
                 THEN 'catalogue code'
            ELSE 'not a catalogue code' END                  AS old_code_kind,
       count(*)                                              AS rows
FROM _oi_product_fix f
JOIN projects.order_inquiry_rows r ON r.id = f.row_id
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3;
SELECT * FROM _oi_product_fix ORDER BY new_code LIMIT 500;

-- Mirror lines that step 2 will move (all orders, OI or not).
SELECT count(*) AS mirror_lines_to_move
FROM projects.sales_order_lines pl
JOIN public.sales_order_lines sol ON sol.id = pl.core_sales_order_line_id
WHERE pl.product_id IS DISTINCT FROM sol.product_id;

-- Step 2: mirror lines follow the AutoCount line's product (what every ESB push does
-- from this lane on, `_sync_mirror_line`).
UPDATE projects.sales_order_lines pl
SET product_id = sol.product_id
FROM public.sales_order_lines sol
WHERE sol.id = pl.core_sales_order_line_id
  AND pl.product_id IS DISTINCT FROM sol.product_id;

-- Step 3: the rows, the same three writes a Confirm makes: new code, old code kept as
-- "was", note, and a row purchasing already confirmed comes back to To confirm.
UPDATE projects.order_inquiry_rows r
SET previous_item_code = f.old_code,
    item_code          = f.new_code,
    -- the same "was" rule a Confirm uses: this change moved the product only
    previous_qty           = NULL,
    previous_delivery_date = NULL,
    note = CASE WHEN r.note IS NULL OR r.note = ''
                THEN 'Was item ' || coalesce(f.old_code, '-')
                ELSE r.note || '; Was item ' || coalesce(f.old_code, '-')
           END,
    ack_state  = CASE WHEN r.ack_state IN ('acknowledged', 'changed') THEN 'changed' ELSE r.ack_state END,
    changed_at = CASE WHEN r.ack_state IN ('acknowledged', 'changed') THEN now() ELSE r.changed_at END
FROM _oi_product_fix f
WHERE r.id = f.row_id;

-- Step 4: after-count, must read 0.
SELECT count(*) AS still_mismatched
FROM projects.order_inquiry_rows r
JOIN projects.sales_order_lines pl ON pl.id = r.so_line_id
JOIN public.sales_order_lines sol  ON sol.id = pl.core_sales_order_line_id
JOIN public.products cp            ON cp.id = sol.product_id
WHERE r.state <> 'cancelled'
  AND r.item_code IS DISTINCT FROM cp.product_code;

ROLLBACK;  -- owner: change to COMMIT only after the dry run above reads right
