-- DO-HISTORY Q3, option (a) (owner ruling 14 Oct 2026): the CRM writes nothing to the pre-July
-- open SO lines. The owner closes them in AutoCount; this is the list to work from.
--
-- READ-ONLY. The OWNER runs it on prod; no agent runs it there. It only SELECTs, inside a
-- READ ONLY transaction that is rolled back, so a mistyped edit fails instead of writing.
-- Export: run it in any SQL client and save the result grid as Excel / CSV, or in psql swap
-- the final SELECT for `\copy (<the SELECT>) to 'pre_july_open_so_lines.csv' csv header`.
--
-- Which lines:
--   * AutoCount sales orders only (`source_system = 'autocount'`): only those close in AutoCount.
--   * SO dated before 2026-07-01 (the Order Tracking cutover, PLAN-do-history-backfill-13oct.md 1.1).
--   * Order and line both open, and something still to transfer: qty ordered > qty transferred.
-- "Qty transferred" is AutoCount's own SODTL TransferedQty, which the SO sync stores as
-- `sales_order_lines.qty_delivered` (app/models/order.py:728-731). It already counts Cancel SO
-- quantities, so "open qty" is the same figure AutoCount's outstanding SO listing shows.
--
-- Dev check (shared dev DB sorento_cagent_stack, read-only, 14 Oct 2026; that copy runs to SO
-- date 2026-09-21 and holds the SRT book only): 10,565 lines, 1,075,160 open qty, earliest SO
-- dated 2023-09-05. The plan's 9,615 lines (section 1.1) is crew's count on the 12 Oct prod clone,
-- by its own definition; the owner's run of query 1 is the number that counts.

BEGIN TRANSACTION READ ONLY;

-- 1. Row count and open qty per book, to check the export is complete.
SELECT c.code                                   AS book,
       count(*)                                 AS open_lines,
       sum(sol.qty_ordered - sol.qty_delivered) AS open_qty
FROM sales_order_lines sol
JOIN sales_orders so ON so.id = sol.sales_order_id
JOIN companies c     ON c.id = so.company_id
WHERE so.source_system = 'autocount'
  AND so.order_date < DATE '2026-07-01'
  AND so.status = 'open'
  AND sol.line_status = 'open'
  AND sol.qty_ordered > sol.qty_delivered
GROUP BY c.code
ORDER BY c.code;

-- 2. The list, one row per open SO line.
SELECT c.code                                     AS book,
       so.so_number                               AS so_no,
       so.order_date                              AS so_date,
       so.debtor_code                             AS customer_code,
       coalesce(so.debtor_name, cu.customer_name) AS customer,
       p.product_code                             AS item,
       w.warehouse_code                           AS location,
       sol.qty_ordered                            AS qty_ordered,
       sol.qty_delivered                          AS qty_transferred,
       sol.qty_ordered - sol.qty_delivered        AS open_qty,
       sa.sales_agent                             AS agent
FROM sales_order_lines sol
JOIN sales_orders so      ON so.id = sol.sales_order_id
JOIN companies c          ON c.id = so.company_id
JOIN products p           ON p.id = sol.product_id
LEFT JOIN warehouses w    ON w.id = sol.warehouse_id
LEFT JOIN customers cu    ON cu.id = so.customer_id
LEFT JOIN sales_agents sa ON sa.id = so.sales_agent_id
WHERE so.source_system = 'autocount'
  AND so.order_date < DATE '2026-07-01'
  AND so.status = 'open'
  AND sol.line_status = 'open'
  AND sol.qty_ordered > sol.qty_delivered
ORDER BY c.code, so.order_date, so.so_number, sol.line_no NULLS LAST, p.product_code;

ROLLBACK;
