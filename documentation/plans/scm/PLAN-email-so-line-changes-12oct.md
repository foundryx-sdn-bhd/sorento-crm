# PLAN: Order inquiry email - remove REMARK, derive Was -> Now from the line change

Status: Build done, in review (lane EMAIL-SO-CHANGES, size M, standard track: the REMARK
removal is a template-document migration, `oihr_0006_no_remark_column`). Red test landed first.

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
`_settle_row_in_place` (`:1915`) calls `_redirect_row_if_received` and declines. In
`refresh_for_decision`,
`_stamp_date_move` finds no target (the redirected row is excluded), the netting raises a fresh
ORDER row and stamps `previous_qty` / `previous_delivery_date` off the redirected row
(`:1575-1580`, "Replaces 1 used"), joins the line to `settled_in_place` so no separate DELAY
row is raised (`:1611-1620`), and then records the fresh row as `kind="raised"` with `was`
holding only an item code (`:1679-1688`). The date move is on the row (the dialog shows it)
and never reaches the email. CWB242 took the ordinary `_settle_row_in_place` path, whose
`handover_was` carries the old date (`:2073-2082`).

Red test: `tests/scm/test_oi_email_so_line_changes.py::test_redirected_line_prints_its_date_move_as_was_now`.

## Behaviour (card answered 12 Oct, PR #1596)

- Q1 owner = (a): numbers + headline only. No REMARK column, no per-line action label, no
  per-action group row. The existing grey `S/O NO` row that opens each order's lines in a
  mail naming two or more orders stays (it is `oihr_0005`'s, not an action label).
- Q2 = (a): one `Attachments:` line under the table, by item code.
- Q3 = (a): the before values are the OI row's own (`previous_qty`, `previous_delivery_date`,
  `previous_item_code`), the same ones the Order Inquiries Was/Now dialog reads.
- Owner addition: the item code is a column pair like qty and date: ITEM CODE (before) +
  NEW ITEM CODE (after), the NEW column shown only when some line in the mail changed its
  product, the cell filled only on lines that changed.
- Line table: `LOCATION | ITEM CODE | NEW ITEM CODE* | QTY | NEW QTY* | DELIVERY | NEW DATE*`.
- A redirected line's date move also names DELAY/ADVANCE in the headline.
- Quantity moves whose rows hold only the difference (crew audit 12 Oct, answer (b)) print
  the SO line's before and after, from the structured values their writer already holds,
  never from note text and never from a new column (the Was/Now dialog is untouched):
  the confirm exception's `placed` / `need` (QTY 10, NEW QTY 6); an SO amendment's delta
  `from_value` / `to_value` (10 -> 6, 10 -> 14), also when a still-raised amendment row
  rides a later mail; a book change's batch row `from_json` / `to_json` qty (handed over by
  `_oi_demand_rows` as `from_qty` / `to_qty`). One email line per SO line: an increase the
  netting splits over two rows prints once, with both rows' verbs in the headline.
- `scripts/preview_oi_email.py` renders the email one SO line change would send, through
  the real apply inside the Simulate confirm dry run, so the owner sees it on the copy
  without SMTP (`laneboard/scripts/1596.md`).
- Known, accepted readings under Q1 (a), listed to the owner on the card: a CHANGE SO NO row
  no longer names the SO it moved to, and PRE-ORDERED / ALREADY INBOUND / ORDER BACK lines
  read like ORDER lines; the headline names those actions and Order Inquiries shows the row.
- A line with a redirected row AND another live row whose date `_stamp_date_move` stamps
  can print the same date move on two lines (the stamped row's and the fresh row's). Both
  are true, the in-app Was/Now shows the same two rows; not reproduced on real data.
- The `ALIGN-email-so-line-changes-12oct.html` renders predate the owner's Q1/item-code
  answers; the tests in `tests/test_oi_email_no_remark.py` are the current shape.

## Done

- `app/services/project_order_inquiry_service.py`: the fresh row raised in place of used
  supply records its `was` off its own before columns (`refresh_for_decision`, raised branch);
  `_record_handover` prints a raised CANCEL BALANCE row's before/after off its stamp;
  `_handover_verb_keys` adds the date verb for such a row.
- `alembic/versions/oihr_0006_no_remark_column.py`: the new document, guarded on the
  unedited `oihr_0005` state, reversible.
- Tests: `tests/scm/test_oi_email_so_line_changes.py` (SRTWB246 date and qty, headline,
  redirected product swap, no note parsing, batch row from/to), `tests/test_oi_email_qty_moves.py`
  (cut, increase, split, confirm exception, book change, still-raised amendment),
  `tests/test_oi_email_no_remark.py` (migration + renders), `tests/test_oi_email_real_shapes.py`,
  `tests/scm/test_preview_oi_email.py`.
- `scripts/preview_oi_email.py`, `laneboard/scripts/1596.md`.
- Guide: `documentation/user-guides/supply-chain/order-inquiry-handover-email.md`.
