# PLAN: Order inquiry email - remove REMARK, derive Was -> Now from the line change

Status: Plan, behaviour card asked on PR #1596 (lane EMAIL-SO-CHANGES, size M, standard track:
the REMARK removal is a template-document migration). Red test landed first.

## Owner rules (12 Oct 2026)

1. Remove the REMARK column from the order inquiry handover email entirely.
2. QTY and DELIVERY moves always print Was -> Now, derived from the actual change on the line
   (the same source the in-app Was/Now dialog reads), not from the action label.

## Where things live

- Email document: `email_templates` row `order_inquiry_handover_default`, last written by
  `alembic/versions/oihr_0005_print_one_page.py` (`_PRINT_LAYOUT`, `_PRINT_BODY_TEXT`). REMARK
  is a column there; attachments print inside the REMARK cell.
- Line builder: `ProjectOrderInquiryService._record_handover`
  (`app/services/project_order_inquiry_service.py:3578`), `remark` from `handover_remark`
  (`:390`), `was` from the caller (`_format_handover_was`, `:468`).
- In-app Was/Now: Order Inquiries `OrderInquiryQtyAnnotationDialog.tsx` via
  `previousValueOf` (`_shared/lib/orderInquiryAck.ts:92`), which reads the row's own
  `previous_qty` / `previous_delivery_date`. The fulfilment board's "What changed"
  (`BoardChangeTable.tsx`) reads `planning_change_rows.from_json/to_json`.

## Root cause, SO400236 / SRTWB246

The line's one row was linked in full to SPO-2026/05-0037, already received.
`_settle_row_in_place` (`:1915`) calls `_redirect_row_if_received` and declines. In `_write`,
`_stamp_date_move` finds no target (the redirected row is excluded), the netting raises a fresh
ORDER row and stamps `previous_qty` / `previous_delivery_date` off the redirected row
(`:1575-1580`, "Replaces 1 used"), joins the line to `settled_in_place` so no separate DELAY
row is raised (`:1611-1620`), and then records the fresh row as `kind="raised"` with `was`
holding only an item code (`:1679-1688`). The date move is on the row (the dialog shows it)
and never reaches the email. CWB242 took the ordinary `_settle_row_in_place` path, whose
`handover_was` carries the old date (`:2073-2082`).

Red test: `tests/scm/test_oi_email_so_line_changes.py::test_redirected_line_prints_its_date_move_as_was_now`.

## Behaviour (pending the card answers)

See PR #1596 card and `ALIGN-email-so-line-changes-12oct.html` (before/after renders).
