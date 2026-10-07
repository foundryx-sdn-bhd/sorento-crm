# UAC: Fulfilment board "Transfer to Cancel SO" on a sales order line

Plan: `PLAN-so-cancel-line.md`. Status: draft, pending owner answers to the behaviour card (7 Oct 2026).
Fixture shape: a project SO with one core line ordered 36 / delivered 36 / `closed`, a raised ORDER inquiry row of 36
on it with no links, and at least one other open line with a saved decision. Fake ids and names only.

## Journey

CS opens the fulfilment board for the order, sees the line planned at 36 with "Amended to nothing, 36 short",
presses "Transfer to Cancel SO", gives the reason, the button counts down 5 s with Cancel, the line turns grey
"Transferred to Cancel SO by <name>, <reason>", Confirm no longer counts it, the rest of the order confirms, and
purchasing finds the inquiry rows in To confirm, greyed and tagged.

## Backend

- AC-C1 Committing the parked record action `sales_order_line.cancel` with `{entity_id, reason}` as a user holding `projects.projects.edit`
  sets the core line `line_status = cancelled`, `cancelled_at` (now), `cancelled_by_id` (actor), `cancel_reason`
  (the text), and returns 200 with those four fields.
- AC-C2 Without `projects.projects.edit` parking the action is 403 and nothing changes.
- AC-C3 A blank or whitespace reason is 422 and nothing changes.
- AC-C4 A line covered by an active supply decision is 409 with a message that says to reject it first.
- AC-C5 A line already `cancelled` is 409; `cancelled_at` and `cancel_reason` are not rewritten.
- AC-C6 After AC-C1 the line's saved draft is gone.
- AC-C7 After AC-C1 every live inquiry row on the line that was `acknowledged` is `changed` with `changed_at` set;
  rows already `awaiting`/`changed`/`cancelled` are untouched; rows of other lines are untouched.
- AC-C8 Board read after AC-C1: the contribution is `cancelled: true` and carries `cancelled_at`, `cancelled_by`
  (display name, no UUID) and `cancel_reason`.
- AC-C9 Confirm of the order after AC-C1 with the other line's decision in the body writes the revision for the
  other line and skips the cancelled line; the result lists no failing line.
- AC-C10 An AutoCount re-push of the same document with the line present (ordered 36, delivered 36, header open)
  leaves `line_status = cancelled`, `cancelled_at` and `cancel_reason` as they were, while quantities/prices follow
  the push.
- AC-C11 A push whose header status is cancelled still cancels every line (existing behaviour unchanged).
- AC-C12 A migration adds the three nullable columns; `alembic heads` shows one head.

## Frontend

- AC-F1 The decision panel of an uncovered, not-cancelled line shows "Transfer to Cancel SO" beside Reject.
- AC-F2 On a covered line the button is disabled with a tooltip saying to reject first.
- AC-F3 Pressing it opens a reason box prefilled with the saved amend reason when one exists, empty otherwise; the
  submit is disabled while the reason is blank.
- AC-F4 Submitting starts a 5 s countdown with Cancel; pressing Cancel within the window posts nothing; Escape does
  not cancel it; when the window lapses the server commits the parked action once.
- AC-F5 After success the board and order-inquiry queries are invalidated, a non-sticky toast confirms, and the row
  renders in the existing cancelled style with "Transferred to Cancel SO" + who + reason; no tick box, not counted
  in "Confirm (N)".
- AC-F6 On error the toast shows the server message (`extractApiError`).
- AC-F7 Usable and non-clipped at 1280 and 375 px (browser pass table).

## Order inquiry worklist (existing C1-C4, verified not regressed)

- AC-O1 The three rows appear under To confirm, greyed, tagged as a cancelled line, qty kept, out of Buy, links kept.
