-- CONSIGN-DELIVERED-FIX: read-only before/after counts for the consignment re-projection.
-- Plan: documentation/plans/autocount/PLAN-consign-delivered-fix-14oct.md.
--
-- Run each block BEFORE `docker compose exec backend_<color> python scripts/reproject_consignments.py
-- --company SRT --apply` and again
-- AFTER it. Read-only: SELECTs only. Expected after: block 1 has no `open` row, blocks 2 and 3
-- read 0, block 4 reads 0.

-- 1. Consignment orders by status (before: about 696 open; after: closed + cancelled only).
SELECT so.status, count(*) AS orders
FROM sales_orders so
WHERE so.doc_type = 'consignment'
GROUP BY so.status
ORDER BY so.status;

-- 2. What consignments put into open demand: the shared predicate every reader uses
--    (header open, line open, not covered, ordered less delivered > 0).
--    Before: about 4,065 units over 928 items; after: 0 / 0 / 0.
SELECT count(DISTINCT so.id)          AS orders,
       count(DISTINCT sol.product_id) AS items,
       count(*)                       AS lines,
       COALESCE(sum(GREATEST(COALESCE(sol.qty_required, sol.qty_ordered)
                             - COALESCE(sol.qty_delivered, 0), 0)), 0) AS open_units
FROM sales_order_lines sol
JOIN sales_orders so ON so.id = sol.sales_order_id
WHERE so.doc_type = 'consignment'
  AND so.status = 'open'
  AND sol.line_status = 'open'
  AND sol.purchasing_status <> 'covered'
  AND GREATEST(COALESCE(sol.qty_required, sol.qty_ordered) - COALESCE(sol.qty_delivered, 0), 0) > 0;

-- 3. The same through the view the reorder run, dashboard and coverage read
--    (committed units on lines that belong to a consignment; after: 0).
SELECT COALESCE(sum(GREATEST(COALESCE(sol.qty_required, sol.qty_ordered)
                             - COALESCE(sol.qty_delivered, 0), 0)), 0) AS retail_leg_units
FROM sales_order_lines sol
JOIN sales_orders so ON so.id = sol.sales_order_id
WHERE so.doc_type = 'consignment'
  AND so.status = 'open'
  AND sol.line_status = 'open'
  AND so.demand_class IS DISTINCT FROM 'project';

-- 4. Live consignment lines whose delivered is not their ordered qty, or whose returned is
--    below the CG's own TransferedQty (after: 0 rows counted).
SELECT count(*) FILTER (WHERE sol.qty_delivered <> sol.qty_ordered)              AS delivered_not_full,
       count(*) FILTER (WHERE (dtl ->> 'TransferedQty') ~ '^-?[0-9]+(\.[0-9]+)?$'
                          AND sol.qty_returned < LEAST(CASE WHEN (dtl ->> 'TransferedQty') ~ '^-?[0-9]+(\.[0-9]+)?$'
                                                            THEN (dtl ->> 'TransferedQty')::numeric END,
                                                       sol.qty_ordered))         AS returned_below_transfered
FROM autocount_cancel_docs d
CROSS JOIN LATERAL jsonb_array_elements(COALESCE(d.source_record -> 'Details', '[]'::jsonb)) dtl
JOIN sales_order_lines sol
  ON sol.company_id = d.company_id
 AND sol.source_ref = d.source_book || ':CSGN:' || d.doc_key || ':' || CASE WHEN (dtl ->> 'DtlKey') ~ '^[0-9]+(\.0+)?$'
                                                         THEN ((dtl ->> 'DtlKey')::numeric)::bigint::text END
JOIN sales_orders so ON so.id = sol.sales_order_id AND so.doc_type = 'consignment'
WHERE d.kind = 'consignment'
  AND NOT d.is_void
  AND d.source_vanished_at IS NULL;

-- 5. For the Consignment page: units still at customers (ordered less returned) vs returned.
SELECT COALESCE(sum(GREATEST(sol.qty_ordered - sol.qty_returned, 0)), 0) AS at_customer_units,
       COALESCE(sum(sol.qty_returned), 0)                                AS returned_units
FROM sales_order_lines sol
JOIN sales_orders so ON so.id = sol.sales_order_id
WHERE so.doc_type = 'consignment'
  AND so.status <> 'cancelled';

-- 6. Anything built on a consignment line while it read as open demand (review nit 4):
--    project mirror lines adopted from a consignment line, and order inquiry rows on them.
--    Informational: the re-projection does not retire these; a non-zero count goes to the
--    owner before anything is touched.
SELECT count(DISTINCT psol.id) AS project_mirror_lines,
       count(DISTINCT oir.id)  AS order_inquiry_rows
FROM sales_order_lines sol
JOIN sales_orders so ON so.id = sol.sales_order_id AND so.doc_type = 'consignment'
JOIN projects.sales_order_lines psol ON psol.core_sales_order_line_id = sol.id
LEFT JOIN projects.order_inquiry_rows oir ON oir.so_line_id = psol.id;
