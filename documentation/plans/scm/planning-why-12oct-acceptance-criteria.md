# UAC: Planning "why" trail

Plan: `PLAN-planning-why-12oct.md`. Status: FINAL (owner approved mock v3, 13 Oct 2026: "ok all good").
Mock: `documentation/mockups/PLANNING-WHY/index.html` (v3).

Paths: BE = `sorento_crm_backend/app`, FE = `sorento_crm_frontend/app/(protected)/project-sales`.
OIS = `BE/services/project_order_inquiry_service.py`, PCS = `BE/services/planning_change_service.py`.

## Admin gate

- **AC-W0** Admin = role slug `superadmin` or `admin` via
  `UserPermissionService(db).get_user_role_slugs(user_id)`, the check Simulate confirm uses
  (`BE/api/v1/projects/fulfilment_planning.py:573-581`). FE: `isSuperadminUser(session.user)`
  (`sorento_crm_frontend/lib/is-superadmin.ts:19`), like `canSimulate`
  (`FE/fulfilment-planning/components/FulfilmentBoardPanel.tsx:1265`). The server is the
  enforcement; the FE gate is UX only.

## R1 Confirm refusal names each line (admin only)

Today the "nothing confirmed" path raises `AppException(422, code="planning_change_not_confirmed")`
with message `Nothing on this planning change could be confirmed.` and no `failing_lines`
(`BE/api/v1/projects/fulfilment_planning.py:1482-1507`).

- **AC-W1** `SupplyFailingLine` (`BE/schemas/project_supply.py:283`) gains two optional fields:
  `reason_code: Optional[str]` and `change: Optional[str]`. Existing callers that do not set them
  are unchanged.
- **AC-W2** For an ADMIN presser, on that path, the order's result carries one `failing_lines`
  entry per pending or superseded planning change row of that order's batch (the rows that explain
  why nothing confirmed; tests reach the path with `lines: []`), with `so_line_no`, `item_code`, and:

  | situation | `reason_code` | `reason` | `change` example |
  |---|---|---|---|
  | the line's planning change row is `pending` with `decision` NULL | `not_decided` | `Not decided` | `Qty 25 -> 22`; `Product SRTWT2201 -> SRTWT2219`; `Date 09/11/2026 -> 16/11/2026`; `New, qty 2` |
  | the row's `applied_state` is `superseded` (line changed again / replaced) | `changed_again` | `Changed again` | as above, from the row |
  | the row was overtaken by a newer revision (PCS:4246-4257) | `already_confirmed` | `Already confirmed in revision N` | null |
  | the SO line is cancelled in the book (PCS:4285-4302) | `line_cancelled` | `Line cancelled` | null |

  `change` is built from the row's `from_json` / `to_json` (qty, then product, then date; first
  that differs; `added` rows read `New, qty N`). Dates `dd/mm/yyyy`.
  The order-level `error` stays `Nothing on this planning change could be confirmed.`
- **AC-W3** For an admin, an order with nothing pending at all (no line to name) answers
  `error` = `Every change on this order is already confirmed (revision N).` and no `failing_lines`.
- **AC-W4** For a NON-admin presser the answer is exactly today's: same `error`, no new
  `failing_lines`, no `reason_code`.
- **AC-W5** AC-W2..W4 hold for `POST /fulfilment-planning/confirm-all` with `preview: true` (the
  board's dry run, `FulfilmentBoardPanel.tsx:1639`) and without, and for
  `POST /sales-orders/{pso_id}/confirm` (its 422 body). `POST /planning-changes/{batch_id}/apply`
  is out of scope (no FE caller).

## R2 Every refused Confirm is recorded

- **AC-W6** When `confirm-all` (real or `preview: true`) or `POST /sales-orders/{pso_id}/confirm`
  refuses an order (`ok: false` / 4xx), exactly ONE `audit_logs` row per refused order is
  committed after the order's rollback, on its own session:
  `entity_type = "project_sales_orders"`, `entity_id` = the order (planning record id),
  `action = "CONFIRM_REFUSED"`, `user_id` = the presser, `root_entity_type/id` = the same order,
  `reason` = the order-level error text,
  `new_values = {"preview": bool, "batch_id": str|null, "lines": [{"so_line_no", "item_code", "reason_code", "reason", "change"}]}`
  (`lines` is filled for admin AND non-admin pressers; the gate only limits what the response shows).
- **AC-W7** A successful order writes no `CONFIRM_REFUSED` row. `POST /fulfilment-planning/simulate-confirm`
  writes none. A failure to write the row is logged and never changes the Confirm's answer.

## R4 Cancel cause recorded

- **AC-W8** Every code path that sets `order_inquiry_rows.state` to cancelled writes, in the same
  transaction, one `audit_logs` row: `entity_type` = the model's pinned audit type (`project_order_inquiry_rows`), `entity_id` = the row,
  `action = "CANCEL"`, `user_id` = acting user or NULL, `root_entity_type = "project_sales_orders"`,
  `root_entity_id` = the row's order (planning record id) when resolvable,
  `new_values = {"revision_no": int|null, "supply_decision_id": str|null, "cause": <code>, "qty": number, "item_code": str, "so_line_no": int|null}`.
  Cause codes: `undecided_change` (the line had a pending, undecided, non-`cancelled` planning
  change row when the revision left it out; same rule as #1599's
  `_lines_with_undecided_planning_change`), `superseded` (any other revision supersede),
  `book_left_nothing` (OIS:1937), `cancel_so` (OIS:4208), `planning_change_retired` (PCS:3764),
  `reserve_moved` (PCS:3427), `manual` (`mark_rows`, OIS:6061), `borrow_retired` (OIS:8936, 4109).
  Known paths: OIS:1031, 1355, 1937, 4011, 4109, 4208, 4348/4360, 6061, 8936; PCS:3427, 3764.
- **AC-W9** A guard test fails if any assignment of the cancelled state to an OI row in
  `BE/services/` has no `_record_cancel` (or the agreed helper) call in the same function.

## R3 History (admin only)

- **AC-W10** `GET /project-sales/fulfilment-planning/history?pso_ids=<id>[,<id>...]` (max 50),
  `require_permission(VIEW)` + AC-W0 admin check (403 `history_admin_only` otherwise). Answers
  `{"events": [...]}` newest first, each event:
  `{"id", "kind", "at" (ISO), "actor" (name|null), "pso_id", "so_no", "lines": [...], ...kind fields}`.
  No UUID is meant for display.
  - `confirm_refused`: from AC-W6 rows; `lines` = their `lines`; `preview`.
  - `revision_confirmed`: one per `so_supply_decisions` row; `revision_no`, `actor` =
    `confirmed_by` name, `at` = `confirmed_at`; `lines` = `[{"so_line_no", "item_code", "action": "ordered"|"cancelled"|"reduced"|"order_back", "qty", "previous_qty", "cause", "change"}]`;
    `reissued_unchanged: int`. Ordered = OI rows created in the revision's window
    (`created_at` between the decision's `created_at` - 5 s and `confirmed_at` + 5 s); cancelled
    = rows whose CANCEL audit row names this `revision_no`, or (rows before release) whose note is
    `Superseded by revision N`; a cancelled row matched by an ordered row with the same
    `so_line_id`, `item_code` and `qty` counts in `reissued_unchanged` and is listed in neither.
  - `autocount_update`: one per (planning change batch, order); `at` = batch `created_at`,
    actor = `created_by` name; `counts` by kind (`qty_down`, `product_changed`, `added`, ...)
    and by state now, flat keys in one `counts` dict (`confirmed` = applied, `not_decided` = pending + decision NULL,
    `replaced` = superseded); `lines` = `[{"so_line_no", "item_code", "kind", "change", "state"}]`.
  - `email_sent`: `email_outbox` rows whose `metadata_json.notification_id` names a
    `notifications` row with `data.source_kind = "order_inquiry_handover"` and `data.source_id`
    one of the order's inquiry ids; `subject`, `status`, `sent_at`, `recipients` (addresses),
    `body_text`; `lines` = `[{"item_code", "remark"}]` parsed from the body's `|` table rows
    (remark = last cell); `counts` = `{"cancel", "order", "order_back"}` from the remarks.
  - `oi_cancelled`: a CANCEL audit row that names no revision (manual, cancel SO, planning
    change retire, reserve moved); `lines` = that one row.
- **AC-W11** Cause for a revision cancel with no CANCEL audit row (before release): who/when
  from revision N named by the note; cause `undecided_change` only when a `planning_change_rows`
  row for that line had `created_at` before N's `confirmed_at`, `decision` NULL and
  `applied_state` pending (or applied after N); otherwise `not_recorded`.
- **AC-W12** FE: for an admin, the board gear menu shows "History" under "Simulate confirm"
  (`FulfilmentBoardPanel.tsx:2310-2326`); it opens a right `Sheet` titled "History" with
  clearable filters SO (the board's orders), product, line, event (multi: Confirm refused,
  Revision confirmed, Order cancelled, AutoCount update, Email), user, date range; results in
  `EventTimeline` grouped by day; each event shows count badges and its lines in a small table
  on click; filters by product/line narrow each event to matching lines and hide events with
  none. A non-admin sees no History item. No page, route or menu entry.
- **AC-W13** FE: in the board results block, an admin's refused order renders its
  `failing_lines` that carry `reason_code` as a table: Line | Product | Change | reason `Badge` |
  action (`Go to line` for `not_decided` / `line_cancelled`, reusing the left-out banner link,
  `FulfilmentBoardPanel.tsx:343-349`; `Reload` for `changed_again` / `already_confirmed`), plus a
  "History" link opening the Sheet on that order. Lines without `reason_code` render as today
  (`failingLineText`). A non-admin sees today's block.
- **AC-W14** "Open email" opens a `Dialog` with subject, sent time, status, recipients and the
  stored text; rows naming the filtered product are highlighted.
- **AC-W15** Usable at 375 px and 1280 px. No UUIDs on screen.

## Out of scope

The wrong CANCEL ALL / under-order fixes (lane OI-CANCEL-BUGS, #1599). The line History icon,
Order Inquiries worklist and board header are unchanged.
