# UAC: board Simulate confirm (admin dry run)

Plan: `PLAN-board-simulate-confirm-12oct.md`. Recommendations Q1-Q5 assumed until the owner answers.

## Contract

`POST /api/v1/project-sales/fulfilment-planning/simulate-confirm`, body = `ConfirmManyBody` (the
same body Confirm posts; `preview` ignored, always simulated). Response = `ConfirmManyResult`:
one `ConfirmManyOrderResult` per order, each with `preview: true`, plus per result:
- `simulated_errors: [{line_no, item_code, project_line_id, error}] | null` - set only when the
  order failed with no `failing_lines` and the per-line probe ran.

## Acceptance criteria

- AC-S1 Gate: a user without role slug `superadmin`/`admin` gets 403 (code `simulate_admin_only`),
  even with `projects.projects.edit`. An admin gets 200. An X-API-Key caller is refused (route uses
  `require_permission`, not the API-key variant).
- AC-S2 Nothing persists: after a simulated press that WOULD confirm (revision, inquiry rows, a
  borrow/pool transfer, a saved draft deleted), every one of these is unchanged: count of
  `so_supply_decisions` for the order, `order_inquiry_rows` for its lines, `stock_transfers` for
  the order, the saved `so_supply_decision_drafts` row still present, `undo_journal` rows,
  `audit_logs` rows written since the press.
- AC-S3 Batch path: an order answering a pending planning-change batch is simulated through the
  batch apply; afterwards every batch row is still `pending`, `decision`/`composition_json`
  unchanged, `applied_at` null; `NotificationService.create_with_channel_preferences` never called.
- AC-S4 Tripwires: while simulating, a root `Session.commit()` raises inside the order's write and
  is reported as that order's `ok: false` error (nothing committed); `queue_service.enqueue_job`
  raises instead of enqueueing. Outside a simulation both behave normally (no regression).
- AC-S5 Echo: an ok order answers `ok: true`, `preview: true`, `decision_revision` (the number it
  would write), `lines_confirmed`, `inquiry_rows`, `transfers` (same fields as preview today).
- AC-S6 Per-line probe: an order with two posted lines where one line raises a non-AppException
  (e.g. `TypeError("Object of type date is not JSON serializable")`) answers `ok: false`, `error`
  = the raw text, and `simulated_errors` naming ONLY the bad line (line_no, item_code, error text
  starting with the exception class name). The good line is not listed.
- AC-S7 Per-line probe skipped when the refusal already names lines (`failing_lines` present):
  `simulated_errors` is null.
- AC-S8 Error text cap: `error` and each `simulated_errors[].error` are at most 1,000 chars.
- AC-S9 FE gear: "Simulate confirm" appears in the board gear menu only for an admin session
  (`isSuperadminUser`), and never adopts an order (no adopt call); an un-adopted order is listed as
  "is not being planned yet; Confirm would adopt it first, so it was not simulated."
- AC-S10 FE results: the existing results block shows "Simulation, nothing was saved: X of Y
  orders would confirm", "would confirm as revision N", a "Would write" list of inquiry rows and
  transfers per ok order, and each `simulated_errors` line as `line N ITEM: <error>`. The board's
  drafts and Confirm count are unchanged after a simulation.
