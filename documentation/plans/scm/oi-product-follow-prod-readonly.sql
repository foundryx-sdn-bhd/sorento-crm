-- OI-PRODUCT-FOLLOW: READ-ONLY prod diagnostics (owner OK'd read-only, 2 Oct 2026).
-- Every block runs inside BEGIN READ ONLY ... ROLLBACK, so nothing can be written.
-- Run with: psql "$PROD_URL" -f oi-product-follow-prod-readonly.sql
-- Path the worklist itself reads (order_inquiry_worklist_service.py:1172-1181):
--   projects.order_inquiry_rows.so_line_id -> projects.sales_order_lines (mirror)
--   .core_sales_order_line_id -> public.sales_order_lines (the AutoCount line)

BEGIN READ ONLY;

-- Q1. SO423414: every AutoCount line, its product, its status, and the mirror line
--     the board and the OI read (mirror product vs AutoCount product side by side).
SELECT sol.line_no,
       sol.source_ref,
       cp.product_code            AS so_product,
       sol.qty_ordered,
       sol.qty_delivered,
       sol.line_status,
       sol.required_date,
       pl.id                      AS mirror_line_id,
       mp.product_code            AS mirror_product,
       pl.qty                     AS mirror_qty
FROM public.sales_orders so
JOIN public.sales_order_lines sol ON sol.sales_order_id = so.id
LEFT JOIN public.products cp      ON cp.id = sol.product_id
LEFT JOIN projects.sales_order_lines pl ON pl.core_sales_order_line_id = sol.id
LEFT JOIN public.products mp      ON mp.id = pl.product_id
WHERE so.so_number = 'SO423414'
ORDER BY sol.line_no NULLS LAST, cp.product_code;

-- Q2. OI-2609-0776: every row, what it says, and what its SO line says now.
--     so_line_id NULL or mirror_core_line NULL = the row is not tied to an AutoCount line,
--     which is why a cancel on that line can never reach it.
SELECT r.id                       AS row_id,
       r.item_code                AS oi_item_code,
       r.qty, r.delivery_date, r.verb, r.state, r.ack_state,
       r.previous_qty, r.previous_delivery_date,
       r.so_line_id,
       pl.core_sales_order_line_id AS mirror_core_line,
       mp.product_code            AS mirror_product,
       cp.product_code            AS so_product,
       sol.line_no, sol.qty_ordered, sol.line_status
FROM projects.order_inquiries oi
JOIN projects.order_inquiry_rows r   ON r.order_inquiry_id = oi.id
LEFT JOIN projects.sales_order_lines pl ON pl.id = r.so_line_id
LEFT JOIN public.products mp         ON mp.id = pl.product_id
LEFT JOIN public.sales_order_lines sol ON sol.id = pl.core_sales_order_line_id
LEFT JOIN public.products cp         ON cp.id = sol.product_id
WHERE oi.inquiry_no = 'OI-2609-0776'
ORDER BY r.created_at, r.item_code;

-- Q3. SO423414 on the planning board: every change row the ESB pushes raised
--     (product_changed / added / cancelled ...) and whether a planner applied it.
--     A PENDING `added` row for WESERP10B = the line is waiting on the board, which is
--     the only path that raises an OI row for it.
SELECT b.created_at AS batch_at, b.applied_at,
       cr.kind, cr.line_no, cr.item_code, cr.applied_state, cr.decision, cr.applied_reason,
       cr.from_json ->> 'item_code' AS from_item, cr.to_json ->> 'item_code' AS to_item,
       cr.from_json ->> 'qty' AS from_qty, cr.to_json ->> 'qty' AS to_qty,
       cr.project_line_id, cr.core_line_id
FROM projects.sales_orders pso
JOIN projects.planning_change_rows cr  ON cr.project_sales_order_id = pso.id
JOIN projects.planning_change_batches b ON b.id = cr.batch_id
WHERE pso.autocount_doc_no = 'SO423414'
   OR pso.so_id IN (SELECT id FROM public.sales_orders WHERE so_number = 'SO423414')
ORDER BY b.created_at, cr.line_no;

-- Q4. FLEET: live OI rows whose item_code differs from their AutoCount line's product.
--     "live" = not cancelled. Split by whether the mirror line followed (it does not today:
--     document_ingest_service._sync_mirror_line copies only date + qty).
SELECT oi.inquiry_no,
       so.so_number,
       sol.line_no,
       r.id                AS row_id,
       r.item_code         AS oi_item_code,
       mp.product_code     AS mirror_product,
       cp.product_code     AS so_product,
       r.qty, r.state, r.ack_state, sol.line_status,
       (SELECT count(*) FROM projects.order_inquiry_links l WHERE l.row_id = r.id) AS links,
       EXISTS (SELECT 1 FROM projects.planning_change_rows cr
               WHERE cr.core_line_id = sol.id AND cr.kind = 'product_changed'
                 AND cr.applied_state = 'pending') AS pending_product_change
FROM projects.order_inquiry_rows r
JOIN projects.order_inquiries oi       ON oi.id = r.order_inquiry_id
JOIN projects.sales_order_lines pl     ON pl.id = r.so_line_id
JOIN public.sales_order_lines sol      ON sol.id = pl.core_sales_order_line_id
JOIN public.sales_orders so            ON so.id = sol.sales_order_id
JOIN public.products cp                ON cp.id = sol.product_id
LEFT JOIN public.products mp           ON mp.id = pl.product_id
WHERE r.state <> 'cancelled'
  AND r.item_code IS DISTINCT FROM cp.product_code
ORDER BY so.so_number, sol.line_no, r.created_at;

-- Q5. FLEET totals for the card: how many rows / lines / orders, and how many hold a link.
SELECT count(*)                         AS rows_mismatched,
       count(DISTINCT sol.id)           AS lines,
       count(DISTINCT so.id)            AS orders,
       count(*) FILTER (WHERE EXISTS (SELECT 1 FROM projects.order_inquiry_links l
                                      WHERE l.row_id = r.id)) AS rows_with_link
FROM projects.order_inquiry_rows r
JOIN projects.sales_order_lines pl ON pl.id = r.so_line_id
JOIN public.sales_order_lines sol  ON sol.id = pl.core_sales_order_line_id
JOIN public.sales_orders so        ON so.id = sol.sales_order_id
JOIN public.products cp            ON cp.id = sol.product_id
WHERE r.state <> 'cancelled'
  AND r.item_code IS DISTINCT FROM cp.product_code;

-- Q6. Mirror lines whose product drifted from the AutoCount line (all orders, OI or not).
SELECT count(*) AS mirror_lines_drifted
FROM projects.sales_order_lines pl
JOIN public.sales_order_lines sol ON sol.id = pl.core_sales_order_line_id
WHERE pl.product_id IS DISTINCT FROM sol.product_id;

ROLLBACK;
