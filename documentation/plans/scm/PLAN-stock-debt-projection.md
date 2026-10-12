# PLAN: Stock Debt projected coverage (sales orders only) (STOCK-DEBT-PROJECTION)

Status: built; reviewer READY after two fix rounds, browser pass PASS (12 Oct 2026); waiting on owner hand test. Owner answered card v2: Q1-Q4 (a); locations "only
consider pool, those project locations are matched already". Small fix track (no migration, no
auth/RBAC change, no new ingest surface).

## Implemented rule (owner, 12 Oct 2026)

```
month(M)      = the page's own figure for M, unchanged (project bins matched as today)
added(M)      = the month figure of the site pools OUTSIDE the span (active, segment <> 'project',
                not flagged for planning), from the same walk over those pools alone: their
                supply (PO Delivery date, SPO ETA, today for on hand and past due) less their
                own sales orders, less what a hold in the page's walk took of that supply
pool_free(M)  = every pool's spare dated in M, in the span or outside it
carried_in(M) = carry(M-1), 0 before the current month
projected(M)  = month(M) + added(M) + carried_in(M)
carry(M)      = clamp(projected(M), 0, carried_in(M) + pool_free(M))
```

Only pool spare carries; project spare and debt never carry (R37). A pool reads the same
whether the span holds it or not (review B1): its own orders are netted either way, and pool
spare already in a month figure only caps the carry, it is never added again. Under the switch
the walk ignores `date_from` (demand before it still takes its share); the axis still starts at
its month. Row total = today's total plus `added` on the axis. "Only products in debt" and the
sort read the projected months. UAC: `stock-debt-projection-acceptance-criteria.md`.

Known limits (review rounds 1-2):
- Outside the span a pool is walked on its own; inside it, every pool shares one pile, so a pool
  order can also draw another pool's on-hand stock. A month can differ by that draw; the carry
  over the row ends the same.
- Placements that together exceed their document (more placed than the PO holds) can read the
  outside case slightly more optimistic in the order's month; the walk inside the span caps
  them, the projection does not re-walk them.
- A PO at an out-of-span pool whose AutoCount S/O names an order in the span is not
  pinned to that order (the page's own book-S/O pin reads in-span supply only); it counts as that
  pool's free supply.

Code: `app/services/scm/stock_debt_projection.py`, `StockDebtService._pool_projection` /
`_project` / `_projected_on_axis`; tests `tests/scm/test_stock_debt_projection*.py`,
`StockDebtClient.projection.test.tsx`, `stockDebtService.real.test.ts`.

The sections below are the behaviour card as it was put to the owner.

## The owner's words (12 Oct 2026)

The Stock Debt page (Procurement > Supply Chain > Stock Debt) should VISUALISE how open POs cover
sales-order demand, based on sales orders only. Order inquiries are removed as a factor (OI
linkage is not stable yet). Display only: the page stays a dashboard, nothing is allocated or
written.

Prod case SRTWT2215: BRW On Hand 191, BRW PO Qty 5,000 (PO 202610-S0042, warehouse HQ, AutoCount
PO, delivery 2026-10-08, no S/O link). Dealer O/S -135. Months: Oct +138, Nov -17, Dec -1,436,
Jan -1,613, Feb -480, Mar -797. The owner expects the 5,000 to read as covering Dec to Mar.

## Why the page cannot show it today (measured, main 4ee7a71bd)

Three independent reasons, each enough on its own:

1. **Months do not carry** (R37, `supply_assignment.py:7-15`, `_months` `:1167-1219`). A month
   is "free supply dated in M less what lines due in M were short at their own date". A PO
   landing in October can only ever make October green; it never appears in Dec to Mar.
2. **POs and SPOs are pin-only in the view** (R45, `stock_debt_service.py:1142`, `:1175`,
   `supply_assignment.py:224-232`). An unlinked PO clears no shortfall; its quantity is only
   "free" in its own month. Links that DO pin it come from OI placements
   (`_holds`, `stock_debt_service.py:1374-1466`) and the book's S/O (`_book_so_holds`).
3. **Span and pool seal.** `_warehouses` (`stock_debt_service.py:699-724`) reads project bins
   (`fulfilment_planning_predicate`) plus site pools defined as "some warehouse's
   `pool_warehouse_id`" (`project_supply_service.py:2422-2446`). The aux column "BRW PO Qty"
   uses the other pool rule, the segment rule (`site_pool_supply.py:69-109`). The pool seal
   (`supply_assignment.py:147-170`) keeps pool supply off project lines.

   **Correction after the data request (12 Oct):** on the dev copy every dealer-segment
   warehouse is its own `pool_warehouse_id`, HQ included, so HQ IS in the span there. The first
   draft's "HQ is nobody's pool" does not hold on dev and is unverified on prod. If prod is the
   same, the 5,000 is already credited to October as free supply, and October's +138 is what
   remains after the past-due and October shortfalls (past-due lines land in the current month).
   A running projection would then carry +138 forward and read red from December. The owner is
   asked to check the October drill on prod before the rule is fixed (card, check 1).

## Behaviour card (proposed rule, answers pending)

### What the user sees

A switch in the existing filter popover, beside "Show stock and PO columns":
**"Projected balance"** (off by default). On, every month cell shows the RUNNING projected
balance for that product instead of the month's own balance. No new column, no new row, so no
layout change and no Lavish mock. Same colours (red below 0, amber/green as `tone_for` today),
same TBA column, Total reads the last month's projected balance. A filter chip "Projected
balance" shows while it is on (same pattern as "Including covered products"). The export follows
the switch.

### The projection rule (one product, display only)

```
opening          = on hand now (qty_on_hand - qty_reserved) at every counted location
projected(M)     = projected(M-1) + supply dated in M - SO demand due in M
projected(first) = opening + supply dated in the current month - SO demand due in the current month
```

Supply counted, at every counted location:

| Source | Quantity | Dated on |
| --- | --- | --- |
| On hand | `quantity_on_hand - quantity_reserved` (same as `_supply`) | today |
| Open PO line | `qty_ordered - qty_received`, `crm_spo` shipping legs excluded (same as `_po_rows`) | Delivery date (`expected_date`), else `issue_date + lead` (R42) |
| Open SPO line | outstanding (`incoming_by_location`) | its arrival (ETA) |

A document past its date lands today (R44, the page's own overdue rule). An undated document is
not counted and is not silently dropped: the drill still lists it.

Demand counted: every open sales-order line the page counts today (`_demand`:
`is_open_demand`, `is_transferable_order`, SO status open, the date filter), project AND dealer,
by `required_date`; past-due lines land in the current month. TBA, undated and unlocated lines
keep their own columns and are NOT subtracted from the running row (question 3).

Counted locations: the page's own span, unchanged (project bins plus the FK site pools, which
on dev includes HQ). `book` and `group=` narrow it exactly as today.

### How order inquiries leave the calculation

The projection is a sum of quantities by date, product-wide. It reads no hold at all: not
`_holds` (decisions, OI placements), not `_landed_holds`, not `_book_so_holds`, and it does not
call `assign()`. An OI placement moves quantity between lines, never in or out of the product's
total, so it cannot change a projected number. The existing month view (switch off) is unchanged.

### R45 (no auto-assign) stays intact

Nothing is assigned to any line. No line's status changes, no "Covered by" text names the PO,
no placement or allocation is written. The switch only changes what the month cells print.
The drill keeps listing the lines as today (short stays short); in projection mode its header
adds one line: "Opening x, in y, out z, projected w".

### Examples

Worked on the dev copy by crew's data request (12 Oct). The figures are on the lane PR's card
comment, not in this repo. Shapes covered: SRTWT2215 (on dev it has no 5,000 PO; the PO is
prod only), an unlinked AutoCount PO at a pool covering later months, an overdue SPO carrying
later months green, a product that stays red, and a product with OI placements pinning PO
quantity to far lines. Dev has no open SPO with a future ETA, so that shape is seeded in tests.

### Questions (max 5, each with a recommendation)

1. **Where it shows.** (a) a switch "Projected balance" that swaps the month cells to the running
   figure, (b) a second "Projected" sub-row under every product (layout change, Lavish mock).
   Recommend (a): no layout change, one screen state at a time, the R45 month view stays one
   click away.
2. **Pool seal.** (a) one product-wide running number, pool and project together, so HQ/BRW
   supply covers project lines in the projection, (b) one running number per ownership group.
   Recommend (a): it is the only shape that shows the 5,000 covering Dec to Mar, and a
   projection is not an allocation, so crossing the seal decides nothing.
3. **TBA / undated / unlocated demand.** (a) keep their own columns, not subtracted from the
   running row, (b) subtract them after the last month. Recommend (a): they have no month to
   land in, and the TBA column already shows them beside the row.
4. **Drill and "Only products in debt" while projecting.** (a) a cell opens the existing drill
   with the Opening / in / out / projected line added; "in debt" means any projected month
   below 0, (b) cells are not clickable while projecting; "in debt" keeps today's meaning.
   Recommend (a): the drill is how a planner checks a number, and a product whose projection
   never goes negative is not in debt in that view.

## Design sketch (after answers; simplest thing that works)

- Backend: `StockDebtService.list(..., projected: bool = False)`. When set, a new
  `_projection(product_ids, warehouses)` reads the SAME three supply readers `_supply` uses
  and `_demand`, sums by month key, and runs the
  cumulative sum; it replaces `months[*].balance` and `total` on the row. No `assign()`, no
  holds. `_in_debt` reads the projected months when set. Same flag on the cell route (header
  line) and the export.
- Frontend: one `Switch` + chip in `StockDebtClient.tsx`, the flag through
  `stockDebtService.ts` / `useStockDebtQuery.ts`, and through `StockDebtExportPopover`.
- Tests first: pytest for the projection read (SRTWT2215 shape, OI placement does not move it,
  a pool PO counted against project lines, past-due PO lands today, TBA not subtracted); vitest for the switch and chip.

## Appendix A: data request (dev copy, read only)

```sql
-- Per product: on hand, open PO by delivery month, SO demand by required month.
WITH p AS (SELECT id, product_code FROM products WHERE product_code IN ('SRTWT2215')),
oh AS (
  SELECT s.product_id, w.warehouse_code, w.segment, w.is_active,
         SUM(COALESCE(s.quantity_on_hand,0) - COALESCE(s.quantity_reserved,0)) AS qty
  FROM stock s JOIN warehouses w ON w.id = s.warehouse_id
  WHERE s.product_id IN (SELECT id FROM p) GROUP BY 1,2,3,4),
po AS (
  SELECT pol.product_id, w.warehouse_code, po.po_number,
         COALESCE(pol.expected_date, po.issue_date) AS dated,
         pol.qty_ordered - COALESCE(pol.qty_received,0) AS qty
  FROM purchase_order_lines pol
  JOIN purchase_orders po ON po.id = pol.purchase_order_id
  LEFT JOIN warehouses w ON w.id = pol.warehouse_id
  WHERE pol.product_id IN (SELECT id FROM p) AND pol.line_status = 'open'
    AND pol.qty_ordered > COALESCE(pol.qty_received,0)
    AND COALESCE(po.source_system,'') <> 'crm_spo'),
so AS (
  SELECT sol.product_id, w.warehouse_code, to_char(sol.required_date,'YYYY-MM') AS month,
         SUM(GREATEST(COALESCE(sol.qty_required, sol.qty_ordered) - COALESCE(sol.qty_delivered,0),0)) AS qty
  FROM sales_order_lines sol JOIN sales_orders so ON so.id = sol.sales_order_id
  LEFT JOIN warehouses w ON w.id = sol.warehouse_id
  WHERE sol.product_id IN (SELECT id FROM p) AND so.status = 'open' AND sol.line_status = 'open'
  GROUP BY 1,2,3)
SELECT 'on_hand' AS kind, product_id, warehouse_code, NULL AS ref, NULL AS month, qty FROM oh
UNION ALL SELECT 'po', product_id, warehouse_code, po_number, to_char(dated,'YYYY-MM'), qty FROM po
UNION ALL SELECT 'so', product_id, warehouse_code, NULL, month, qty FROM so
ORDER BY 2, 1, 5;
```

Plus the pool membership of every warehouse in the result:
`SELECT warehouse_code, segment, is_active, id IN (SELECT pool_warehouse_id FROM warehouses WHERE
pool_warehouse_id IS NOT NULL) AS is_pool_fk FROM warehouses WHERE warehouse_code IN (...)`.
