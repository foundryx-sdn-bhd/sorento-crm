# PLAN: SO line "What changed" and History show the Was and the Now (SO-LINE-CHANGE-HISTORY)

Status: F1 building, red-first (feature track M, lead pattern: tester red tests, coder green,
reviewer + kill test, agent-browser 1280/375; no mock, owner exception for wording and rows
inside existing dialogs). F2 HELD pending owner answers to Q2/Q3 of card a67320022.
Plan created: 2026-10-07T18:30:00+08:00
Lane: SO-LINE-CHANGE-HISTORY, branch `crew/so-line-change-history`, base origin/main 292a366ee
Domain: scm (SO detail Lines tab) + project-sales (order inquiry History dialog)
UAC: `so-line-change-history-acceptance-criteria.md` alongside

## Problem (owner, 7 Oct 2026)

"in SO right, do we store the change of the SO ah? can we visualize? we got this but it doesn't
show anything somehow ... i expect to see the change from 182 to 280, delivery date from 1/6 to
1/3/2027 in order inquiry also, not sure why when we import the 1/6/2026 is not captured"

Case: SO314593 / SRTWCX8605-S-RL-PJ, prod snapshot sorento_ai_automation_20261006.

## Measured (origin/main 292a366ee)

- The change IS stored. `projects.planning_change_rows` f2b6b20d: `from_json` {qty "182",
  required_date "2026-06-01", status open} -> `to_json` {qty "280", required_date "2027-03-01"},
  kind delayed, decision amend, applied. `projects.order_inquiry_rows` e9fac76d (OI-2609-0011):
  previous_qty 182, previous_delivery_date 2026-06-01, qty 280, delivery_date 2027-03-01.
- SO detail never sends the sides: `app/services/scm/sales_order_service.py:729-734` builds
  `{id, kind, applied_state, result}` only; schema `SalesOrderLinePlanningChange`
  (`app/schemas/scm_orders.py:110`) declares those four, and `GET /api/v1/scm/sales-orders/{id}`
  (`app/api/v1/scm/sales_orders.py:176`, `response_model=SalesOrder`) drops anything undeclared.
- FE hardcodes the sides to null: `salesOrderLineChange.ts:39-40` (`scm/sales-orders/[id]/components/`),
  so `BoardChangeSummary` (`fulfilment-planning/components/BoardChangeTable.tsx:171-173`) always
  prints "The book moved this line without changing its quantity, its date or its decision."
- The board's own feed already carries the sides as `PlanningChangeFromTo`
  (`app/schemas/planning_change.py:51`: required_date, qty, status, item_code, all Optional[str])
  via `row_out` (`planning_change_service.py:2359-2360`, `row.from_json or {}`), and
  `annotationOf` (`_shared/lib/boardChangeAnnotations.ts:396-403`) maps `from.qty` -> was.qty,
  `from.required_date` -> was.date, `to.*` -> now (null when `closed`).
- History dialog (`order-inquiries/[id]/components/OrderInquiryLineHistoryDialog.tsx`) folds rows
  per `sol:<mirror line id>` (`orderInquiryLineFold.ts:93-98`), which is the OPEN AutoCount line;
  the cancelled 182 sheet line has no inquiry rows, so "No earlier rows" (:153). `lineHistoryEntries`
  (:279-291) prints "Was <previous_qty>." and ignores `previous_delivery_date`.
- The importer creates no sales_order_lines any more (`project_order_inquiry_import_service.py:124-128`),
  so "set required_date going forward" has no code path. Sheet-origin lines: 1738, all cancelled,
  46 with required_date NULL, 11 of those with a dated OI row (Q5, not in lane).
- Only 11 of the 1738 sheet lines lack a date AND have a dated OI row:
  `select cl.id, s.so_number, pr.product_code, cl.qty_ordered, r.delivery_date from projects.order_inquiry_rows r join projects.sales_order_lines psl on psl.id=r.so_line_id join sales_order_lines cl on cl.id=psl.core_sales_order_line_id join sales_orders s on s.id=cl.sales_order_id join products pr on pr.id=cl.product_id where cl.required_date is null and r.delivery_date is not null;`

## Rulings (card a67320022, 7 Oct 2026)

- Q1 = (a): backend adds from/to, FE maps them, board Was | Now table reused.
- Q4 = (a): importer change not applicable.
- Q2, Q3 (History Before row, cancelled sibling): with the owner. F2 held.
- Q5 (backfill): not in this lane either way.

## Design (simplest thing that works)

### F1 SO detail "What changed" shows the sides (AC-1 to AC-6)

Backend, no schema migration:
1. `SalesOrderLinePlanningChange` (`app/schemas/scm_orders.py:110`) gains
   `from_: Optional[PlanningChangeFromTo] = Field(default=None, alias="from")` and
   `to: Optional[PlanningChangeFromTo] = None`, importing `PlanningChangeFromTo` from
   `app.schemas.planning_change` (same shape as the board; `populate_by_name` as the
   `PlanningChangeRow` schema already does at :179). Wire key is `from`, like the board.
2. `_line_planning_changes` (`sales_order_service.py:729-734`) adds
   `"from": row.from_json or None, "to": row.to_json or None`. Empty dict or NULL -> None
   (AC-2), never an invented side. Everything else in that method is untouched (newest batch,
   newest row per core line, any applied_state).

Frontend:
3. `scm.types.ts:369-376` `planning_change` gains `from?: {qty?, required_date?, status?,
   item_code?} | null; to?: ... | null`.
4. `salesOrderLineChange.ts:39-40` maps `was = {qty: from?.qty ?? null, date: from?.required_date
   ?? null, decision: null}` and `now = {qty: closed ? null : to?.qty ?? null, date: closed ? null :
   to?.required_date ?? null, decision: null}`, the same rule `annotationOf` applies
   (`boardChangeAnnotations.ts:396-403`). Docstring :19-24 is rewritten: the line now carries the
   sides. Nothing else changes: `BoardChangeSummary` already prints `BoardChangeWasNowTable` with
   `onlyMovedFields` when `changedFieldsOf` finds a moved field (AC-3, AC-4) and keeps the
   "moved without changing" sentence when it finds none (AC-5); `closed` keeps "Cancelled" (AC-6).
5. `productChangedFrom` stays null on this screen (out of scope; the board's rule needs kind
   product_changed and the from item_code, not asked for here).

Testing seams:
- pytest: a fresh world via `tests/test_planning_change_apply_on_board.py` helpers (`_form_three`,
  `_apply_from_board`, see :1280-1300 for the SO detail read) or a direct `PlanningChangeRow`
  insert with `from_json`/`to_json`, then `GET /api/v1/scm/sales-orders/{id}` and assert the line's
  `planning_change["from"]`/`["to"]` on the wire (response_model survival is the point; lesson
  "response_model drops undeclared fields").
- vitest: `salesOrderLineChange.test.ts` (new) for the mapping; `SalesOrderDetail.test.tsx`
  (pattern at :1842-1877: `planned({planning_change: {...}})`, click
  `board-change-icon-<id>`, read `board-change-dialog`) for AC-3, AC-4, AC-5.

### F2 History Rows: Before row + Delivery date column (AC-7 to AC-13) - HELD

Frontend only, if Q2 = (a): `lineHistoryEntries` (`orderInquiryLineFold.ts:279-291`) appends,
after the Now entries, one `Before` entry per Now row with previous_qty or previous_delivery_date:
`{what: 'Before', qty: previous_qty, date: previous_delivery_date, documents: none, why: "Was
<qty> on <dd/mm/yyyy>, before <inquiry_no> changed it", when: changed_at ?? raised_at, id:
<row.id>:before}`. `LineHistoryEntry` gains the optional overrides the grid reads instead of
`entry.row.*` for qty/date/document; `LineHistoryWhat` gains `'Before'`; `WHAT_VARIANT` maps it
to `outline`. The dialog grid gains a `Delivery date` column (size 110, `formatDateInMalaysia`)
between Qty and What. Existing AC-ND-15/16 tests adjust row counts where the fixture carries
previous_qty. If Q3 = (b) a second ticket adds a BE sibling-line lookup; not planned here.

## Out of scope (recorded)

- Per-line change log (`SalesOrderLine.__audit_skip__`, `app/models/order.py:710`); AutoCount
  re-pull overwrite in place (`document_ingest_service.py:1486-1534`).
- Older batches on the SO detail (only the newest batch's row per line, :722).
- Q5 backfill UPDATE (owner-gated, separate step, SELECT above).

## Slices and tickets

- F1 (this PR): BE schema + service, FE type + mapping, tests. ~60 lines.
- F2 (same PR, after Q2/Q3): FE fold + dialog column, tests. ~80 lines.

## Verification

- Red-first: `test(red):` commit before code, `crew-note: red-proof <sha>`; kill proof after green.
- Reviewer (opus) + kill test on AC-1, AC-3, AC-5. security-reviewer: not run, diff outside its surface.
- agent-browser on the lane copy with SO314593 (sidebar: Sales orders -> SO314593 -> Lines ->
  triangle) at 1280 and 375; hand test script `laneboard/scripts/<PR>.md`.
