# PLAN: notify the OI raiser when purchasing rejects rows (OI-REJECT-NOTIFY)

Status: Ready, reviewer READY at d30ed42e (track: standard, additive seed migration; cloud lane, backend only)

UAC: `oi-reject-notify-13oct-acceptance-criteria.md` (alongside).

## Journey

Purchasing rejects one or more Order Inquiry rows (single reject, or batch reject with one
reason). The person who raised the OI gets ONE email and ONE in-app notification per OI per
reject action, listing every rejected line of that OI, the reason, who rejected and when, with
a link to the OI. Today nothing is sent (`reject_row` / `reject_rows`,
`project_order_inquiry_service.py:6958` / `:6990`).

## Behaviour card

Example (illustrative OI; the cloud sandbox holds no prod-copy data, so the numbers below are
made up in the shape a real OI carries).

Purchasing (Joey) batch-rejects 2 rows of `OI-2610-0007` (SO `SO-422517`, customer
`ABC Builders Sdn Bhd`, project `Residensi Yotu`) with reason "Supplier discontinued this
finish". The raiser (Eling) receives:

Email, subject: `OI rejected: OI-2610-0007 SO-422517`

> **Rejected: OI-2610-0007**
>
> | OI NO | S/O NO | CUSTOMER | PROJECT |
> |---|---|---|---|
> | OI-2610-0007 | SO-422517 | ABC Builders Sdn Bhd | Residensi Yotu |
>
> | ITEM CODE | QTY | DELIVERY DATE |
> |---|---|---|
> | WC-1234-WH | 10 | 25/10/2026 |
> | BS-5678-CH | 6 | 25/10/2026 |
>
> Reason: Supplier discontinued this finish
>
> Rejected by Joey (joey@...) on 13/10/2026 14:05.
>
> [Open in Order Inquiries](/project-sales/order-inquiries/<OI id>)

In-app notification (bell), one per OI:

- Title: `OI-2610-0007: 2 lines rejected by purchasing`
- Body: `SO-422517 ABC Builders Sdn Bhd. Reason: Supplier discontinued this finish`
- Click opens `/project-sales/order-inquiries/<OI id>`.

Same table/border styles and "Open in Order Inquiries" footer as the handover email
(`oihr_0003_location_column.py`). No ` · ` joins anywhere.

### Edge cases (recommended behaviour)

| Case | Behaviour |
|---|---|
| Raiser = rejecter | No email to the raiser and no in-app notice: they did it themselves. The automation still runs, so any extra recipient an admin adds still gets it. |
| Raiser inactive / trashed / no email | Treated as no raiser: no email to them, no in-app notice. |
| OI has no raiser (`raised_by` NULL) | Same as above: nothing to the raiser. |
| Rows of 2 OIs in one batch | Two emails, two in-app notices, each listing only its own OI's lines. |
| Reject then undo / reject again later | Each reject action is its own notice (no retraction email). A later reject of the same OI sends a fresh notice; dedup is per action, never per OI. |
| Reject rolled back (any error) | Nothing sent. |
| Single-row reject | Same email/notice with one line. |
| Handover email | `uncover_lines` re-confirms in the decider's name; reject must NOT also send an `order_inquiry_handover` email. Pinned by a test on a two-line order. |

## Design

Reuse, no parallel path:

- New event-driven trigger `order_inquiry_rejected` in `automation_triggers.py` (same shape as
  `order_inquiry_reserve_requested`).
- `ProjectOrderInquiryService._uncover_and_notify_rejection` builds one context per OI
  (`_rejection_notices`) BEFORE `uncover_lines` rewrites rows, queues it on `Session.info`, and
  drops any handover item the uncover's re-confirm queued.
- Post-commit drain: `after_commit` that ignores savepoint releases (no nested transaction open)
  plus the file's C2 tx_chain discard on rollback, fresh `SessionLocal()`, `AutomationService.dispatch_event` for the email and
  `NotificationService.create_in_app_only` for the bell.
- Recipient: seeded automation with `include_raiser: true`, `one_email: true`, no roles. The
  context's `raiser` is `None` when the raiser is the rejecter or not an active user.
- `create_in_app_only` gains an optional `data` argument (additive) so the notice carries
  `data.link`, which the bell already routes (`NotificationItem.tsx` `collectLinks`).
- Migration `oirj_0001_seed_reject_automation`: template + automation, idempotent by template
  code and `(trigger_type, name)`, copied from `undo_0002_seed_undone`.

## Test list (red first)

1. Trigger `order_inquiry_rejected` is in the catalog.
2. Single reject dispatches one `order_inquiry_rejected` after commit with OI no, SO no,
   customer, line (item code, qty, dd/mm/yyyy date), reason, actor, rejected-at, link to OI.
3. Batch reject of 2 rows of ONE OI: one dispatch listing both lines.
4. Batch reject spanning 2 OIs: two dispatches, each its own lines.
5. Raiser receives an in-app notification per OI with `data.link` to the OI.
6. Raiser = rejecter: context `raiser` is None and no in-app notice.
7. Raiser inactive: context `raiser` is None and no in-app notice.
8. Rejected request (422, e.g. already rejected) dispatches nothing.
9. Reject on a two-line order dispatches no `order_inquiry_handover`.
10. Seed migration idempotent; downgrade removes both rows; automation enabled with
    `include_raiser`.
11. Template renders the lines, reason and footer; no ` · `; blank not `None`.
