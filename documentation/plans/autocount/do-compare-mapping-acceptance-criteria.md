# UAC - DO compare mapping (DO-COMPARE-SIM)

Plan: `PLAN-do-compare-mapping.md` (same folder).

## Journey

1. The checker opens the delivery-orders pull (Import Jobs > the pull row) and goes to
   "Compare with my Excel".
2. Each dropzone names the workbook kind and the sheet it reads, from the saved mapping:
   "Order Listing (macro), sheet Master" / "Order Tracking (macro), sheet Master".
3. They drop the two macro workbooks as they are. The browser reads the configured sheet by
   name (never a `Template` sheet), the server maps each column through the saved mapping and
   compares inside the pulled DocDate window.
4. When a workbook changes shape (a renamed sheet, a moved column, a discount stored as a
   fraction), they click "Mapping" on the tab, see one table per workbook kind
   (Excel column | Transform | Sorento field) plus the sheet name, edit in place, Save, and
   drop the file again. Nothing else changes; Confirm still applies the AutoCount pull.

## Phase 2 - backend

- **AC-CMM-1 [BE]** Given a fresh migration, when `GET /autocount/pulls/compare-mappings` is
  called by a holder of `order_management.orders.autocount_pull`, then it returns two
  mappings, `order_listing` and `order_tracking`, each with `sheet_name = "Master"` and the
  default column rows (plan section 3). A user without that permission gets 403.
- **AC-CMM-2 [BE]** Given a holder, when `PUT /autocount/pulls/compare-mappings/order_listing`
  saves a sheet name and column rows, then GET returns exactly what was saved. A transform
  outside the fixed list, a Sorento field outside the kind's list, the same Sorento field
  mapped twice, a blank Excel header, or a missing required field (`doc_no`, `item_code` for
  order_listing; `doc_no` for order_tracking) answers 422 and stores nothing.
- **AC-CMM-3 [BE]** Given the lines mapping maps `Discount` with `percent_fraction`, when a
  sheet row carries `0.37` and the pull line carries `"37%"`, then no discount difference is
  reported; `1` against `"100%"` also matches. With `percent_text`, `"40%+5%"` against
  `"40%+5%"` matches.
- **AC-CMM-4 [BE]** Given a lines sheet in the Order Listing Master shape (document `Total`
  and line `Total_1` columns, a promotion-package line whose `Total (Ex)` cell is blank),
  when compared, then the blank line total is not compared and the document `Total` column is
  never read as the line total (no `total_ex` difference for that line).
- **AC-CMM-5 [BE]** Given the pull holds a cancelled DO (`Cancelled = "T"`) that the Order
  Listing does not list, when the LINES file is compared, then its lines are not reported as
  only in AutoCount. The HEADERS compare still reports a `cancel` difference when the
  tracking sheet says F.
- **AC-CMM-6 [BE]** Given a mapping that renames a column (e.g. `Doc Number` -> `doc_no`),
  when the compare runs, then rows are keyed by the renamed column; the window reads the
  column mapped to `doc_date`.
- **AC-CMM-7 [BE]** Existing compare contract unchanged: the stored summary, the
  `source`/`window`/`ignored_outside_window` response keys, and products/stock compare.

## Phase 2 - frontend

- **AC-CMM-10 [FE]** Given an `.xlsm` with sheets `Master` and `Template`, when dropped on a
  DO dropzone whose mapping says `Master`, then the rows come from `Master`.
- **AC-CMM-11 [FE]** Given the Order Tracking workbook (sheets Config, Raw Data, Master,
  Sheet1, Daily Tracking, Overall Tracking; no Template), when dropped, then it parses from
  `Master` with no error. A workbook without the configured sheet shows
  "Sheet 'X' not found (found sheets: ...)".
- **AC-CMM-12 [FE]** The dropzone titles show the configured sheet name.
- **AC-CMM-13 [FE]** "Mapping" opens a modal with the two workbook kinds as line tabs, a sheet
  name input and a DataGrid (Excel column | Transform | Sorento field) edited in place, add
  row, remove row, one Save; success toasts and invalidates the mapping query; a 422 shows
  the extracted message.
- **AC-CMM-14 [UX]** Usable at 375px and 1280px; selects are `SearchableSelect`.

## Evidence

- **AC-CMM-20 [T]** `scripts/simulate_do_compare.py` on the owner's two workbooks against the
  01-03 Sep local snapshot: before/after numbers in the PR, remaining rows explained by class.
