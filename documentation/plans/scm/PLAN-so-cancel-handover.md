# PLAN: Transfer to Cancel SO hands over to purchasing like cancel balance

Status: in review (PR #1530; owner card answered 8 Oct 2026 via crew: Q1a Q2a Q3a Q4a Q5a, no backfill; reviewer READY on c4859d403). Track: feature lane, size M (behaviour change on an
existing flow, no migration, no screen change), LEAD pattern (tester -> coder -> reviewer).
Domain: scm (fulfilment board confirm, order inquiries, handover email)
UAC: `so-cancel-handover-acceptance-criteria.md`
Lane: `crew/so-cancel-handover`, base `origin/main` a16a0c545.
Follows: `PLAN-so-cancel-line.md` (PR #1524), whose Q4a ("inquiry rows go back to To confirm") this replaces.

## Owner's words (8 Oct 2026, verbatim)

"actually for the cancel, I am expecting it go through same route like normal order inquiry confirm that handover to
purchasing to inform the line is cancelled (similar to cancel balance)"

## Measured (origin/main a16a0c545; prod snapshot sorento_ai_automation_20261006, read-only)

- `ProjectSupplyService.cancel_lines` (`app/services/project_supply_service.py:1213-1303`) cancels the core line and
  calls `flag_rows_for_cancelled_lines` (`app/services/project_order_inquiry_service.py:517-565`), which only sets
  acknowledged live rows to `ack_state = changed`. No handover line is queued, so no email and no in-app notice.
- Snapshot: SO373923 AC lines 384/400/416 (CB110, CB110-R, CB110LP) cancelled 7 Oct 14:51 on a test copy; their
  OI-2609-0724 rows read `state = raised`, `ack_state = changed`, qty 36, no links. The last
  `order_inquiry_handover` automation run that day is 09:49.
- Cancel balance route: `_settle_row_in_place` zero-need branch (`project_order_inquiry_service.py:1895-1927`) sets
  the row `cancelled`, removes its links, dispatches `order_inquiry_changed_with_links` when it had links, and calls
  `_record_handover(row, kind="cancelled", was={"qty": previous_qty})`. `handover_remark` prints
  `CANCEL BALANCE <qty> NOS` (`:433-435`), QTY CHANGE TO `0` (`:3598-3600`), headline key `CANCEL_BALANCE` (`:366`).
- `_fire_pending_handover` (`:11148`) drains every queued line once per ROOT commit into ONE
  `order_inquiry_handover` dispatch; the seeded automation "Order inquiry to purchasing" (enabled in the snapshot)
  sends the email and the in-app notice to its recipients.
- Real prod mail 6 Oct 05:28 (snapshot `email_outbox`): `OI: BRW-BB @ SO373923`, headline `CANCEL BALANCE`, line
  `27/10/2025 | SO373923 | BRW-BB | SRT384-6 | 78 | 0 | 02/10/2026 | CANCEL BALANCE 78 NOS`.
- The confirm route commits once per request (`app/api/v1/projects/fulfilment_planning.py`, `confirm_supply`), for a
  cancel-only press too (`:1042-1044`), so a queued handover line fires.

## Design (owner card: Q1a bare remark, Q2a Cancelled, Q3a cancel + unlink + changed-with-links, Q4a told even if never acknowledged, Q5a one email per commit on the existing switch)

One new method on `ProjectOrderInquiryService`, `retire_rows_for_cancelled_lines(core_line_ids, reason_by_core_line,
actor_user_id)`, called by `cancel_lines` in place of `flag_rows_for_cancelled_lines`:

1. For each named core line, the live rows of its mirror line(s) (`state` raised / partly_linked / placed, verb ORDER
   or ORDER_BACK, not `redirected_to_pool`).
2. Each such row: capture `was_qty = row.qty`; remove its links (`_remove_links`) and remember whether it had any;
   `state = cancelled`; note appended `Transferred to Cancel SO: <reason>`; `_drop_suggested_links`.
3. A row that had links: `_dispatch_changed_with_links(inquiry, row, had_link=True)`.
4. Every cancelled row: `_record_handover(row, kind="cancelled", was={"qty": was_qty}, actor_user_id=actor)`.
5. `actioned` rows, `CANCEL_BALANCE` exception rows and rows already `cancelled` are left as they are; an
   `actioned` row that purchasing had acknowledged still goes to `changed` (the old flag, kept for that row only).

`flag_rows_for_cancelled_lines` stays for its two AutoCount ingest callers (`SalesOrderService._upsert_lines`,
`document_ingest_service`), unchanged.

No migration, no FE change, no new event key, nothing pushed to AutoCount.

## Slices

1. Tester: red pytest from the UAC (`tests/test_so_cancel_handover.py`), Postgres fixture, fake data.
2. Coder: the method above + the `cancel_lines` call, until green; touched suites `-n 2`.
3. Reviewer; browser pass on the crew test copy (board Confirm of a cancel, then the queued `email_outbox` row and
   `notifications` row as proof, scheduler off).

## Not this plan

- Backfill of lines cancelled before this ships.
- AutoCount-side cancels (ingest path) keep today's flag behaviour.
