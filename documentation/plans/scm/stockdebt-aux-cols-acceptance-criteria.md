# UAC: Stock Debt auxiliary columns (STOCKDEBT-AUX-COLS)

Plan: `PLAN-stockdebt-aux-cols.md`. Answers to the behaviour card questions are marked
PROVISIONAL until crew relays the owner's answers; a changed answer edits the AC it names.

## Columns

- **AC-1** Every Stock Debt row carries four numbers: `pool_on_hand`, `pool_po_qty`,
  `pool_incoming_qty`, `dealer_outstanding`. The screen labels them "BRW On Hand Qty",
  "BRW PO Qty", "BRW Incoming Qty", "Dealer O/standing Qty", in that order, between Product
  and the first month column.
- **AC-2** `pool_on_hand` equals `summary_order_service._pool_on_hand_map` for the product
  (SUM `quantity_on_hand`, active non-project warehouses with `counts_as_available`). A
  product with no such stock row reads 0. Stock in a project bin or a non-available
  warehouse is not counted.
- **AC-3** `pool_po_qty` equals `site_pool_supply.open_po_by_product(...)["qty"]`: open PO
  lines at pool warehouses only; a PO line at a project bin or with no warehouse is not
  counted; an over-received line counts 0.
- **AC-4** `pool_incoming_qty` equals `site_pool_supply.open_spo_by_product(...)["qty"]`:
  open, not-received SPO allocations at pool warehouses only.
- **AC-5** `dealer_outstanding` equals `summary_order_service._demand_aggregates(...,
  horizon=<page end date>, horizon_start=None)["dealer_qty"]`: open retail-class SO lines,
  `purchasing_status != 'covered'`, outstanding = COALESCE(qty_required, qty_ordered) -
  delivered. A line due AFTER the end date is excluded; a line due before the START date is
  still counted (Q4, PROVISIONAL); a line with no required date is always counted; with no
  end date every open retail line counts. Project-class and unclassified lines never count.
- **AC-6** The four values do not change with the Book (all/project/retail) or Supplier
  filter; those filters only decide which rows show.
- **AC-7** No new per-product query: each of the four reads runs once per request over the
  whole filtered product set.

## Toggle

- **AC-8** A Switch "Show stock and PO columns" (existing `components/ui/switch.tsx`) in the
  Stock Debt filter popover shows/hides the four columns. Default ON (Q2, PROVISIONAL).
- **AC-9** The choice is remembered per browser (localStorage, wrapped in try/catch) and
  restored on reload (Q3, PROVISIONAL). Hiding the columns does not count as an active
  filter chip.

## Totals

- **AC-10** The screen's totals footer sums each of the four columns over the WHOLE filtered
  set (not the page), like the month totals (Q5, PROVISIONAL).

## Excel export

- **AC-11** With the toggle ON, the workbook carries the four columns after Supplier and
  before the first month, same labels, numeric cells; each sheet's Total row sums them.
  With the toggle OFF the workbook is exactly today's (Q1, PROVISIONAL: export follows the
  toggle). Existing columns are not reordered. Split-by-supplier/category sheets carry them
  too.
- **AC-12** The export request body gains `show_aux: bool` (default false so an old client
  gets today's workbook).

## Layout

- **AC-13** Usable at 1280px and 375px; Product column stays pinned; aux columns have
  explicit `size`, right-aligned numbers, same number format as month cells.
