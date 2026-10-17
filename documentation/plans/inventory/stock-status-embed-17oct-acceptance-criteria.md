# UAC: Available stock status, embedded (STOCK-STATUS)

Plan: `PLAN-stock-status-embed-17oct.md` (rules R1-R12, Q1-Q7). Mock: `mockups/stock-status.html`
round 4, owner OK 17 Oct 07:55. AutoCount is the truth; the panel only reads.

## API: `GET /api/v1/inventory/stock-status?product_id=<uuid>&so_due_by=<YYYY-MM-DD>`

- AC-1 Rows. One row per warehouse of the caller's company holding any nonzero On hand, SO, PO or
  SPO for the product, active or not; each row says `is_active`. All-zero warehouses are absent.
- AC-2 On hand = `stock.quantity_on_hand` at that warehouse, inactive warehouses included.
- AC-3 SO = sum of `qty_ordered - qty_delivered` (floored at 0 per line) over lines with
  `line_status = 'open'` on orders with `status = 'open'`. A line CS marked `covered` counts. A line
  whose order has `is_transferable = false` does not count; NULL counts. `qty_required` is ignored.
- AC-4 PO = sum of `qty_ordered - qty_received` over open lines (`line_status = 'open'`) of purchase
  orders in `active / received / partial / closed`, bound to the warehouse.
- AC-5 SPO = sum of `allocated_quantity - quantity_received` over open SPO rows
  (`spo_supply.open_incoming_clauses`), bound to the warehouse.
- AC-6 Forecast per row = On hand - SO + PO + SPO, signed. Totals = column sums;
  `forecast_incl_pl` = total Forecast + Incoming PL.
- AC-7 Incoming PL (item level, never on a row): per not-arrived, non-draft shipment, the product's
  still-to-land qty (shipped - received, line not `received`) less the qty of this product's SPO rows
  linked to that shipment by `inbound_shipment_id` OR by `container_number` =
  `shipping_container_number`, floored at 0. Each shipment is listed with still-to-land, on-SPO and
  counted figures. A PL fully on an SPO counts 0.
- AC-8 Horizon. With `so_due_by`, SO counts only lines whose due date (line `required_date`, else the
  order's `requested_delivery_date`) is on or before it; a line with no date or dated 2030-01-01 always
  counts. Supply (PO, SPO, PL) is never cut.
- AC-9 Documents. SO rows per counted line: so number, sales order id, debtor code + name, project
  label, salesperson, doc date, due date (null when none or 2030-01-01), qty, warehouse. PO rows per
  counted line: po number, purchase order id, supplier code + name, issue date, expected date, qty,
  warehouse. SPO rows per counted row: spo number + line, supplier code + name, issue date, arrival,
  container number, qty, warehouse. The rows of a type sum to that type's total.
- AC-10 `as_of` = the newest stock write for the product (as the location-stock dialog reports).
- AC-11 Access: 200 for a holder of any of `inventory.stock.view`, `projects.projects.view`,
  `scm.reorder.run`, `scm.dashboard.view`; 403 otherwise; 404 for a product id that is not a uuid or
  not in the caller's company. Another company's warehouses never appear.
- AC-12 Read-only: no write of any kind.

## Panel (FE)

- AC-13 Columns On hand, SO (negative), PO, Incoming PL, SPO, Forecast; no status column; totals row;
  Incoming PL on the totals row only ("-" on rows); "Forecast incl. incoming PL" row.
- AC-14 Inactive warehouse rows carry an "Inactive in CRM" tag.
- AC-15 Location search (`ListSearchInput`): filters rows by code/name as you type; a "Shown, n of N"
  subtotal appears while filtered; the All locations totals stay.
- AC-16 Expanding a location lists its SO / PO / SPO rows sorted by due date; every document number
  links to its CRM page (SO `/scm/sales-orders/{id}`, PO `/scm/purchase-orders/{id}`, SPO
  `spoDetailHref`, PL `/procurement-management/packing-lists/{id}`).
- AC-17 SO due by toggle (All, this month, +1, +2, +3) refetches with `so_due_by` = month end.
- AC-18 SO totals tab: Group by Project (default) / Customer / Salesperson / Delivery month; rows =
  group values, columns = locations with SO, row and column totals; search narrows the columns with a
  Shown row and an All locations row.
- AC-19 No dot separators anywhere in the panel.
- AC-20 Embeds: OI detail line expand, OI worklist item code (chip + popover), fulfilment board List
  view product cell (chip + popover), loading plan product row dialog (Stock status tab), stand-alone
  page `/inventory-management/stock-status` in the Inventory menu.
- AC-21 Empty state: "No stock, orders or incoming for this item" when the API returns no rows and no
  incoming PL.

## Real-data check (crew, dev copy)

- AC-22 SRTWB1515: On hand 495 at active warehouses (499 with AutoCount's inactive ones at the
  screenshot moment), SO 10,390, PO 2,438, SPO 459, Incoming PL 227; BRW-BB 0 / -7,312 / 1,734 / 50 =
  -5,528.
