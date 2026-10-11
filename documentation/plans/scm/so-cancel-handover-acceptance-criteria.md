# UAC: Transfer to Cancel SO hands over to purchasing like cancel balance

Plan: `PLAN-so-cancel-handover.md`. Status: accepted 8 Oct 2026 (owner card via crew: Q1a Q2a Q3a Q4a Q5a, no
backfill). Fake ids and names only in tests.

Fixture shape (as `tests/test_so_cancel_line.py`): a project SO, a core line ordered 36 / delivered 36 / `closed`
with no active decision covering it, a live ORDER inquiry row of 36 on its mirror line, plus at least one other line
that a press can confirm.

## Journey

CS saves "Transfer to Cancel SO" with a reason on a line and presses Confirm (alone, or beside other decided lines,
or via confirm-all). The line's inquiry row becomes Cancelled; purchasing receives one "Order inquiry to purchasing"
email and in-app notice whose line reads `CANCEL BALANCE 36 NOS`, QTY CHANGE TO 0, exactly as a cancel balance does.

## Backend

- AC-H1 A cancel-only Confirm (`cancelled_lines` only) of a line whose live ORDER row is `raised` and `acknowledged`
  sets the row `state = cancelled` and appends `Transferred to Cancel SO: <reason>` to its note (existing note kept).
- AC-H2 The same press queues exactly one `order_inquiry_handover` dispatch after commit, holding one line for that
  row: `qty` = the row's qty before the cancel (36), QTY CHANGE TO `0`, `remark` = `CANCEL BALANCE 36 NOS` (no
  reason in the remark), headline / verb key `CANCEL_BALANCE`, SO number, item code, delivery date and location of
  the row.
- AC-H3 A row purchasing never acknowledged (`ack_state = awaiting`) is cancelled and handed over the same way.
- AC-H4 A row with links (`partly_linked` or `placed`): its links are removed (the document is free again), the row
  is cancelled, one `order_inquiry_changed_with_links` dispatch is queued for it, and it is handed over as in AC-H2.
- AC-H5 A `placed` row with no link is cancelled and handed over; no changed-with-links dispatch.
- AC-H6 An `actioned` row on the cancelled line is not cancelled, not unlinked and not handed over.
- AC-H7 A row already `cancelled`, and a `CANCEL_BALANCE` exception row, on the cancelled line are left as they are
  and are not handed over.
- AC-H8 A cancelled line with no live inquiry row queues no handover dispatch and the press still succeeds.
- AC-H9 Three lines cancelled in one press produce ONE handover dispatch carrying three lines.
- AC-H10 A mixed press (cancel line A, confirm line B) produces ONE handover dispatch carrying A's
  `CANCEL BALANCE` line together with whatever B's confirm hands over.
- AC-H11 confirm-all with `cancelled_lines` behaves as AC-H1/AC-H2.
- AC-H12 A press that is refused (any 4xx from the existing cancel guards, or a rolled-back batch) cancels no row and
  queues no handover dispatch.
- AC-H13 Nothing is written for AutoCount (no outbound push); the AutoCount ingest callers of
  `flag_rows_for_cancelled_lines` keep today's behaviour.
- AC-H14 `tests/test_so_cancel_line.py::test_c7_*` is updated to the new rule (acknowledged rows are cancelled,
  not flipped to changed); every other test in that file stays green unchanged.

## Out of scope

- Backfill of lines cancelled before this ships. No FE change. No migration. No new event key.
