# UAC: order sheet fixes, OI worksheet for a plan run, async OI export

Plan: `PLAN-order-sheet-oi-reports-22sep.md`. Status: DRAFT, rulings R1-R4 applied 22 Sep.

## Lane A - order sheet cells

- AC-A1 BRW incoming qty prints the total, then one `<container> - <qty>` line per open SPO
  line; a line with no container prints `<qty>`; no SPO number anywhere in the cell. Same
  on the low stock report's two sheets.
- AC-A2 Last in qty prints `<container> - <qty>` (or `<qty>` with no container); no SPO
  number. Last in date unchanged.
- AC-A3 BRW PO qty is unchanged (total, then `<PO number> - <qty>` lines).
- AC-A4 Project / customer prints `project_customer_label(customer, coalesce(project
  title, SO project label))`, one line per label with its qty. An adopted AutoCount SO with
  a project label prints `CUSTOMER / LABEL`, never the customer alone.
- AC-A5 Project qty, Delivery and Project / customer are built from the OI rows the
  engine buys for, in the run's scope only: verb ORDER or ORDER_BACK, state raised or
  partly_linked, ack acknowledged or changed, not redirected, owed (qty minus linked) > 0
  and printed as owed; SO in the run's picked orders when set, product in the run's
  products when set, delivery inside the run's window (undated included). Supply
  decisions play no part. A placed, actioned, awaiting-ack, rejected, redirected or fully
  linked row is absent.
- AC-A6 On a Project run with buy-in-full, Project qty equals Suggested qty on every row;
  a row of the same product due outside the window, or on an un-picked SO, is absent from
  all three cells.
- AC-A6b Suggestion prints one part per line, `Stock: 438`, `PO: 11`, `Buy: 67`, in that
  order, omitting zero parts; `Nothing` when there is no buy. On the PDF the cell renders
  the line breaks (pre-line class). The plan grid is unchanged.
- AC-A8b A test seeds a PO header in a currency other than MYR with a NULL-currency line
  and asserts that currency prints (a literal default cannot pass).
- AC-A5b A `write_rows`-level test pins the window: a picked-SO row dated after the run's
  `plan_horizon_date` is absent from the frozen `project_customers`.
- AC-A10 Company isolation: a second company's PO line and OI row are excluded from Last
  cost and Project qty respectively; the order sheet worker fails closed (no rows, no
  costs) for a run row carrying no company.
- AC-A7 New column "Last cost" sits immediately right of Supplier on the XLSX and the PDF;
  every later column keeps its own width and wrap/number rule.
- AC-A8 Last cost = the newest non-cancelled PO line's unit cost with its currency, the
  line's own when set else the PO header's; never a literal default. Blank when the
  product has no PO line with a cost. Looked up at export time. Test: a line with NULL
  currency on a MYR header prints `x.xx MYR`.
- AC-A9 Supplier still names the last-purchase supplier (regression pin).

## Lane B - async OI export

- AC-B1 Export Excel on the OI detail returns at once with a toast; a `user_downloads` row
  exists with kind `order_inquiry_xlsx`, source `order_inquiry` + the OI id, filename
  `<OI number>.xlsx`.
- AC-B2 The worker renders the same workbook the sync export produced (same sheets, same
  rows, same headings) and the row goes ready; My Downloads lists it.
- AC-B3 A second click while one is queued or processing starts nothing (toast says so).
- AC-B4 Enqueue failure marks the row failed with the reason; the drawer shows it.
- AC-B5 The OI detail gear has "Download history"; it lists only this OI's downloads,
  newest first, and opens the ready file.
- AC-B6 The OI list page's Export Excel also queues a download (kind
  `order_inquiry_worklist_xlsx`, no source entity, current filters honoured) and toasts;
  the file matches the old sync GET for the same filters.
- AC-B7 Permission: the same VIEW permission the sync export required.

## Lane C - OI worksheet for a plan run

- AC-C1 The plan view's export menu shows "OI worksheet Excel" beside the order sheet and
  low stock items; all four disable while any export is pending.
- AC-C2 Clicking queues a download tied to the run (`reorder_run`, run id), kind
  `oi_worksheet_xlsx`, filename `oi-worksheet-<ddmmyyyy>.xlsx`, and toasts.
- AC-C3 The workbook is the worklist export's layout: title ORDER INQUIRY, headings SO DATE
  / S/O NO / ITEM CODE / QTY / TOTAL QTY / DELIVERY DATE / PROJECT/CUSTOMER / SUPPLIER / PO
  NO / LOCATION (no ACKNOWLEDGED), one sheet per delivery month, NO DATE sheet for undated
  rows, rows ordered SUPPLIER then ITEM CODE, TOTAL QTY on the last row of an item.
  Superseded in part (owner, 16 Oct 2026, PLAN-oi-excel-one-sheet-16oct.md): the OI Excel is
  ONE sheet, never split by month; no NO DATE sheet.
- AC-C4 Row set = every live OI Buy row inside the run's Start Plan scope: the run's SO
  numbers when set, its product ids when set, and delivery date inside the run's window
  (undated included). A row outside the window, for an SO not in the list, or for a
  product not in the list, is absent. A row whose product the engine did NOT put on the
  sheet is still present when it is in scope.
- AC-C4a The workbook's rows are exactly the rows Project qty counted on the same run (one helper).
- AC-C4b The count of OI headers and rows in the workbook equals what the Start Plan
  picker showed for the same selection (raised date, delivery window, orders, products).
- AC-C5 A run with no rows produces a one-sheet workbook with the headings only.
- AC-C6 The cap follows the worklist export's own cap; over it the route refuses with
  "Narrow the plan first" before creating a download row.

## Lane D - plan All with picked project SOs

- AC-D1 Start Plan with Demand = All shows the Orders picker (the shared
  `SearchableMultiSelect`), pre-ticked with every candidate order that has rows in the
  range; the buyer can untick. The closed trigger is ONE line: the first two SO numbers
  then `+x` for the rest (`SO418869, SO419517 +12`); menu rows are one line each. Same
  trigger under Demand = Project. Not clipped at 375px.
- AC-D1b On an All run WITH picked orders the From / To range narrows only the project OI
  rows; a retail SO line due after the To date is still planned. On an All run with no
  picked orders, and on a Dealer or Project run, the range behaves as today (retail leg
  windowed); `test_reorder_window_start.py` stays green unchanged.
- AC-D2 The run request carries `so_numbers` with no `demand_class`; the run row stores
  both as sent.
- AC-D3 On that run, a product whose only project demand sits on an un-picked SO carries no
  project need; a picked SO's OI rows in window are bought in full on top of retail sizing.
- AC-D4 A retail-only product below its level is still admitted and sized as on today's
  All run; Dealer o/s on the sheet is unchanged.
- AC-D5 Project qty on the sheet and the OI worksheet both equal the picked SOs' rows in
  window for that run.
- AC-D6 Demand = Dealer shows no picker and behaves as today; Demand = Project behaves as
  PR #1122 left it.

## Lane E - discontinued product with confirmed OI demand

- AC-E1 A discontinued (active, not excluded) product with a confirmed OI Buy line inside the
  run's scope gets a plan line whose Project need equals the line's owed qty.
- AC-E2 The same product with its only OI line outside the window, on an un-picked SO, or
  awaiting ack gets no line.
- AC-E3 A discontinued product below its reorder level with no OI line gets no line on an
  All or Dealer run (leg 2 never admits it).
- AC-E4 On an All run the admitted discontinued product's Suggested qty is the project need
  only, even when it sits below its level; Retail contributes nothing.
- AC-E5 A Dealer run never admits a discontinued product.
- AC-E6 `is_active = false` or `exclude_from_planning = true` still keeps a product out,
  confirmed OI line or not.
- AC-E7 The product's line appears on the order sheet (Project qty) and the OI worksheet for
  that run.
## Lane F - confirmed OI need bought in full on an All run

- AC-F1 All run, product with on hand greater than its confirmed OI qty, retail trigger
  off: Suggested qty equals the OI owed qty; the row is visible (not covered).
- AC-F2 All run, same product with the retail trigger on: Suggested qty equals retail
  sizing plus the full OI qty.
- AC-F3 ORDER BACK on a closed, fully delivered SO line, confirmed: bought in full on an
  All run and on a Project run.
- AC-F4 A confirmed Reserve decision of R units on that row reduces the bought qty by R,
  on every sizing path (single-member, pool, product-grain) and on a Project run too.
- AC-F5 Retail-only products and Dealer runs size exactly as before.
- AC-F6 The demand drill's Project figure, the order sheet's Project qty and the OI
  worksheet agree with the bought project qty on that run.
- AC-F7 Pool, single-member and product-grain paths all satisfy AC-F1.
