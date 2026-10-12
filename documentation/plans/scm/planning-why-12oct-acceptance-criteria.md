# UAC: Planning "why" trail

Plan: `PLAN-planning-why-12oct.md`. Status: DRAFT, all questions answered (13 Oct 2026); read side follows mock v3 (admin-only), pending
owner approval of v3. Reason TEXT in AC-W1..W5 becomes a short reason code + badge per mock v2 section 7.

Paths: BE = `sorento_crm_backend/app`, FE = `sorento_crm_frontend/app/(protected)/project-sales`.

## R1 Confirm result names each line

- **AC-W1** A Confirm of an order with a planning change batch whose posted lines all carry a
  pending AutoCount change with no decision (`planning_change_rows.decision` NULL,
  `applied_state` pending) answers that order with `ok: false` and one `failing_lines` entry
  per such line: `so_line_no`, `item_code`, and a `reason` that reads, for a qty change,
  `AutoCount changed the quantity 25 -> 22 on 08/10/2026 and no one has decided it yet. Decide the line, then Confirm.`
  (product change: `the product SRTWT2201 -> SRTWT2219`; date change: `the required date
  <old> -> <new>`; new line: `added this line (qty N)`). The order-level `error` is no longer
  `Nothing on this planning change could be confirmed.`
- **AC-W2** A posted line whose change row is `superseded` ("Line changed again", "Replaced by a
  later change") answers `AutoCount changed this line again; this change is replaced. Reload the board.`
- **AC-W3** A posted line whose change was overtaken by a newer revision answers
  `Already settled by revision N (<confirmed_by name>, <dd/mm/yyyy h:mm am>). Reload the board.`
- **AC-W4** A posted line whose SO line is cancelled in the book answers
  `AutoCount cancelled this line; nothing to confirm.`
- **AC-W5** An order with nothing pending at all answers one order-level `error`
  `Every change on this order is already confirmed in revision N (<name>, <when>).` and no
  `failing_lines`.
- **AC-W6** The same per-line answer comes back for `preview: true` (the board's dry run), for
  `POST /sales-orders/{pso_id}/confirm` (in the 422 body's `failing_lines`), and for
  `POST /planning-changes/{batch_id}/apply`.

## R2 Every refused Confirm is recorded

- **AC-W7** When `POST /fulfilment-planning/confirm-all` (real or `preview: true`),
  `POST /sales-orders/{pso_id}/confirm` or `POST /planning-changes/{batch_id}/apply` refuses an
  order, exactly one `audit_logs` row per refused order is committed AFTER the order's rollback:
  `entity_type = project_sales_orders`, `entity_id` = the order, `action = CONFIRM_REFUSED`,
  `user_id` = the presser, `root_entity_*` = the order, `reason` = the order-level error,
  `new_values = {"preview": bool, "batch_id": str|null, "lines": [{"so_line_no", "item_code", "reason"}]}`.
  A successful Confirm writes no such row. The admin Simulate route
  (`POST /fulfilment-planning/simulate-confirm`) writes none.
- **AC-W8** A failure to write that audit row never changes the Confirm's own answer
  (logged, swallowed).

## R4 Cancelled OI row: who, when, why

- **AC-W9** Every path that sets `order_inquiry_rows.state = cancelled` (OIS:1031, 1355, 1937,
  4011, 4109, 4208, 4348/4360, 8936, 6061; PCS:3764, 3427) writes one `audit_logs` row in the
  same transaction: `entity_type = order_inquiry_rows`, `entity_id` = the row, `action = CANCEL`,
  `user_id` = the acting user (the confirmer, the marker, or NULL for a system path),
  `root_entity_*` = the order, `new_values = {"revision_no": int|null, "supply_decision_id": str|null,
  "planning_change_row_id": str|null, "cause": <code>, "cause_text": str}`. Cause codes:
  `undecided_change` (the line had a pending, undecided AutoCount change and the revision left
  it out), `book_left_nothing`, `product_changed`, `cancel_so`, `planning_change_retired`,
  `reserve_moved`, `manual`, `superseded` (re-issued in the same revision).

## Admin gate (mock v3)

- **AC-W0** Admin = role slug `superadmin` or `admin` (`UserPermissionService.get_user_role_slugs`,
  as `fulfilment_planning.py:573-581`). The per-line refusal rows (AC-W1..W5) are sent only to an
  admin; a non-admin's refusal is today's answer, unchanged (`error` = `Nothing on this planning
  change could be confirmed.`, no new `failing_lines`). The recording (AC-W7) happens for both.
  The history read (AC-W10) answers 403 for a non-admin.

## R3 / R4 read side (mock v3)

- **AC-W10** `GET /sales-orders/{pso_id}/history[?core_line_id=]` returns the order's events
  newest first: `revision_confirmed`, `autocount_update`, `confirm_refused`, `oi_cancelled`,
  `email_sent`; a cancel + re-issue of the same line, item and qty inside one revision is
  counted on the revision event (`reissued_unchanged: N`), not listed.
- **AC-W11** For a row cancelled before release (no CANCEL audit row): who/when come from the
  revision its note names ("Superseded by revision N" -> N's `confirmed_by`/`confirmed_at`);
  the cause is `undecided_change` only when a `planning_change_rows` row for that line existed
  with `created_at` before N's `confirmed_at`, was not applied by then, and had no decision;
  otherwise `cause not recorded`.
- **AC-W12** For an admin, the board gear menu shows "History" under "Simulate confirm"; it opens
  a right Sheet with clearable filters SO (the board's orders, all selected at first), product,
  line, event (multi), user, date range; results in `EventTimeline`. A non-admin sees no
  History item. No page, route or menu entry; line History icon, worklist, board header unchanged.
- **AC-W13** In the admin results block each refusal row is Line | Product | Change | reason badge
  | action (Go to line / Reload), plus a History link opening the Sheet on that order.
- **AC-W14** "Open email" shows the stored email (subject, sent time, status, recipients, text)
  to anyone who can see the board.
- Usable at 375 px and 1280 px. No UUIDs on screen.
