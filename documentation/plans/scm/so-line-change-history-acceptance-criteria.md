# UAC: SO line change history shows the Was and the Now (SO-LINE-CHANGE-HISTORY)

Status: F1 (AC-1 to AC-6, SO detail dialog) RULED and building, red-first (card a67320022,
7 Oct 2026: Q1 = a, Q4 = a). F2 (AC-7 to AC-13, History Before row) HELD until the owner
answers Q2 and Q3; a different ruling rewrites the AC it touches before any test is written.
Q5 (backfill) is not in this lane either way.
Plan: `PLAN-so-line-change-history.md` alongside.

## Journey

CS opens sales order SO314593 from the Sales orders list and clicks the Lines tab. Line 16
(SRTWCX8605-S-RL-PJ, 280, required 01/03/2027) carries the amber triangle in the Decided
column because the September book moved it. CS clicks the triangle. The "What changed" dialog
must answer the one question CS has: what did the book change on this line. Today it says the
book moved the line without changing anything, which is false (the batch row holds 182 @
01/06/2026 to 280 @ 01/03/2027). After this lane it shows the same Was | Now table the
fulfilment board shows: Qty 182 | 280, Date 01/06/2026 | 01/03/2027.

CS then opens the order inquiry OI-2609-0011 from the Order inquiries list, Lines tab, and
clicks History on the same product. The Rows tab must tell the line's story in rows: the row as
it stands now (280, 01/03/2027) and, above "No earlier rows", the row as it stood before the
change (182, 01/06/2026). Today it prints "Was 182." in the Why column and hides the date.

Nothing else changes: no new screen, no new log, no import change, no data update.

## Phase 2 (single phase: the dialogs exist, only their data and rows change)

### SO detail "What changed" dialog

- AC-1 [BE] Given a sales order whose newest planning-change batch holds a row for a core line
  with `from_json` {qty 182, required_date 2026-06-01} and `to_json` {qty 280, required_date
  2027-03-01}, When `GET /api/v1/scm/sales-orders/{id}` is read, Then that line's
  `planning_change` carries `from` = {qty "182", required_date "2026-06-01", status, item_code}
  and `to` = {qty "280", required_date "2027-03-01", status, item_code} (the same
  `PlanningChangeFromTo` shape the board feed uses), alongside today's id, kind, applied_state,
  result.
- AC-2 [BE] Given a planning-change row whose `from_json` and `to_json` are NULL or {}, When the
  order is read, Then `planning_change.from` and `planning_change.to` are `null` (no invented
  side), and id, kind, applied_state, result are unchanged.
- AC-3 [FE] Given a line whose `planning_change` carries `from` and `to` as in AC-1, When the
  Decided-column triangle is clicked, Then the dialog titled "What changed, SO314593
  (SRTWCX8605-S-RL-PJ)" shows the Was | Now table with a Qty row "182" | "280" and a Date row
  "01/06/2026" | "01/03/2027", and does NOT show the sentence "The book moved this line without
  changing its quantity, its date or its decision."
- AC-4 [FE] Given `from`/`to` where only the date differs (SO387800 SRTWHBWP: 96 -> 96,
  19/11/2026 -> 15/12/2026), When the dialog opens, Then only the Date row is printed (the
  board's `onlyMovedFields` rule), no Qty row.
- AC-5 [FE] Given a line whose `planning_change` has `from`/`to` null or absent, When the dialog
  opens, Then it prints today's sentence "The book moved this line without changing its quantity,
  its date or its decision." exactly as before (no regression for rows without sides).
- AC-6 [FE] Given a `planning_change` of kind `cancelled`, When the dialog opens, Then it still
  reads "Cancelled" on the Now side (today's `closed` path), with the Was side from `from`.

### Order inquiry History dialog, Rows tab

- AC-7 [FE] Given a line whose Now row has `previous_qty` "182" and `previous_delivery_date`
  "2026-06-01" (OI-2609-0011 row e9fac76d), When History > Rows opens, Then the grid lists the
  Now row first and ONE extra row labelled "Before" after the live rows and before any retired
  row, with Qty 182, Delivery date 01/06/2026, Document "-", and Why "Was 182 on 01/06/2026,
  before OI-2609-0011 changed it", and the text "No earlier rows for this line." is NOT shown.
- AC-8 [FE] Given the same line, When the Rows grid renders, Then it has a "Delivery date" column
  (between Qty and What) and the Now row prints "01/03/2027" there, formatted by
  `formatDateInMalaysia` like every other date in the dialog.
- AC-9 [FE] Given a Now row with `previous_qty` set and `previous_delivery_date` null, When
  History opens, Then the Before row prints Qty = previous_qty, Delivery date "-", Why
  "Was <qty>, before <OI number> changed it".
- AC-10 [FE] Given a Now row with neither `previous_qty` nor `previous_delivery_date`, When
  History opens, Then no Before row is added and, if no retired rows exist, "No earlier rows for
  this line." still shows (AC-ND-16 preserved).
- AC-11 [FE] Given a Now row where `previous_qty` equals `qty` and only
  `previous_delivery_date` differs from `delivery_date`, When History opens, Then the Before row
  IS listed (the date moved).
- AC-12 [FE] Given two live Now rows on one line each carrying previous_*, When History opens,
  Then each gets its own Before row directly after the Now rows, in the same order as the Now
  rows, each with a stable grid row id distinct from the real rows.
- AC-13 [T] The Now row's own Why text keeps today's wording ("Was 182." + note), so the existing
  AC-ND-15 assertions keep passing except where a Before row changes the row count.

### Not in this lane (recorded so the DoD gate can tick them)

- AC-14 [T] No change to `project_order_inquiry_import_service.py` (Q4 a: the importer creates
  no sales_order_lines; nothing to set).
- AC-15 [T] No UPDATE of `sales_order_lines.required_date` (Q5 b). The read-only SELECT for the 11
  candidate rows is in the plan.
- AC-16 [T] No new table, column, migration, permission or endpoint. `security-reviewer` not run:
  diff outside its surface (no auth, RBAC, ingest, upload, company scoping).

### UX

- AC-17 [UX] Both dialogs stay usable at 375px and 1280px: the History grid's own scroller is the
  single horizontal scroll surface (AC-ND-18 preserved with the sixth column); the Was | Now
  table is unchanged in width.
- AC-18 [UX] No new motion. Nothing animates beyond the dialogs' existing open/close presets.
