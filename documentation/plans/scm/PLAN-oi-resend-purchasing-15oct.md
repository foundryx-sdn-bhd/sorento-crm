# PLAN: OI-RESEND-PURCHASING - "Send to purchasing" for an Order Inquiry (15 Oct)

Status: Built, review fix round 1 done, Q1 (OI Excel) built (PR #1714). Track: LEAD, cloud lane. Behaviour
card answered by crew 15 Oct (Q2-Q4 as recommended). Q1 owner ruling 16 Oct 00:10: "should attach the OI
Excel just like a normal confirm action ... It should be the same function." (rule 9).

## Problem
The "Order inquiry to purchasing" automation (trigger `order_inquiry_handover`) was disabled on prod
~14 Oct 19:45 to ~15 Oct 15:51 MYT. Handovers confirmed in that window sent no email and left no
automation_run / notification row, and there was no way to send the handover again by hand.

## Rules (behaviour card, crew-approved)
1. Content: the OI's open rows (`raised` / `partly_linked`; never cancelled, actioned or placed), each
   built by `ProjectOrderInquiryService.handover_item` (the same builder the automatic email queues
   through `_record_handover`) and aggregated by `_build_handover_context` (same template, same
   per-line SO files). Subject gets " (Resent)", the headline "RESENT: ". A `partly_linked` row
   prints only its open remainder (`qty` minus what its links carry), so purchasing never re-orders
   the linked part (review S2).
2. Recipients and template: the handover automation's own (enabled one first, else the oldest), via
   `park_send.resolve_ordered` / `render_preview`. The `actor` stays the OI's raiser, so "Raised by"
   is true and the raiser is on Cc exactly as on the automatic email; the presser is recorded as
   `sent_by` only (review S1; the card's first draft had the presser as actor).
3. Automation switched off: still sends (explicit send); the preview says so in red.
4. Single: OI detail page options menu "Send to purchasing" (`projects.order_inquiry.action`).
5. Bulk: OI Documents list tick column (action holders only) -> bulk strip "Send to purchasing" ->
   one preview, one email per OI -> one Send. Max 50 OIs per call.
6. Duplicate guard (warn only): each email shows "Last sent to purchasing: <when> (automatic | resent
   by X)" from the newest `notifications` row with `trigger_type=order_inquiry_handover` and
   `source_id=<OI id>`, or "No handover email on record".
7. Audit: each OI is one `AutomationService.send_match` run -> `automation_runs` + `notifications` +
   `notification_deliveries` (source_kind `order_inquiry_handover_resend`, `sent_by` in data).
8. Never stuck: a preview error toasts and closes; a send with a per-OI failure toasts and keeps the
   dialog open on the OIs that did not go.
9. OI Excel (owner, 16 Oct): ONE build, `build_handover_email` (project_order_inquiry_service), serves
   BOTH the automatic send after a confirm and Send to purchasing. It attaches each named OI's own
   workbook (`OrderInquiryWorklistService.export_xlsx(inquiry_id=...)`, the Export Excel workbook,
   file `<OI no>.xlsx`), stored under `exports/order-inquiry/handover/`, as an optional extra
   attachment ahead of the SO-line files. Before this the automatic email did NOT carry the Excel
   (SO-line files only); now it does. A workbook that cannot be rendered or stored is logged and left
   out; the email still goes. The preview names the file ("Attached: ...") and stores nothing. The
   dispatch differs only where it must: a confirm fires enabled automations (`dispatch_event`); Send
   to purchasing runs the handover automation even when it is switched off (`send_match`).
10. Pick the lines (owner hand test, 16 Oct 00:33: "the first 2 lines is sent in the previous
    confirm, and then the third line is sent in the morning confirm ... I actually want to just
    resend for the third line only"): a SINGLE-OI preview lists every open line with a tick, all
    ticked to start. The email AND its Excel (`export_xlsx(row_ids=...)`) carry only the ticked
    lines; Send waits for at least one. A line that an earlier handover email of this OI names
    (its item code in that email's body) reads "sent DD/MM": a hint, still tickable. A bulk send
    takes every open line. `row_ids` with more than one OI, an empty list, or a line that is not
    an open line of that OI is 422. Crew ruling (b), 16 Oct: the resend's workbook ALWAYS holds
    exactly the open lines its email names (all ticked, some ticked, or a bulk send), never a
    cancelled or placed row; the automatic confirm email keeps the whole-OI workbook. The tick
    list shows a partly linked line's open remainder, the quantity the email asks for.
11. Review hardening (Excel slice): the workbook render runs in a SAVEPOINT so a DB error in it
    never poisons the session the email goes on (B1); a confirm stores the workbook only when an
    enabled automation will send it, and a resend only for an OI that has recipients (S2).

## Contract
- `POST /api/v1/project-sales/order-inquiries/handover-resend/preview` `{inquiry_ids}` ->
  `{emails[{inquiry_id, inquiry_no, to, cc, subject, body_html, inquiry_count, line_count, last_sent}],
  inquiry_total, unresolved, skipped[{inquiry_id, inquiry_no, reason}], automation_enabled}`.
- `POST .../handover-resend/send` `{inquiry_ids, row_ids?}` -> `{emails_sent, items_sent, skipped}`; preview takes the
  same body and each single-OI email adds `lines[{row_id, item_code, qty, location, delivery_date,
  selected, sent_at}]` and `attachments` (workbook names).
- skip reasons: `nothing_open`, `no_recipient`, `failed`. Unknown OI 404; empty / bad id / >50 is 422.

## Known limits
- "Last sent" only finds an automatic email whose `source_id` is this OI; a write that touched several
  OIs files its one email under the first OI.
- The workbook is rendered and stored inside a confirm's post-commit drain (one per OI the write
  touched), so a confirm takes that much longer; there is no worker hop.

## Evidence
- Backend: `tests/test_oi_handover_resend.py` (12; 9 red first in cea18b69, 3 added later) and
  `tests/test_oi_handover_excel.py` (2, red first in 6f301737), plus the handover suites
  (164 passed) after the `_record_handover` split.
- Frontend: `OrderInquiryHeadersList.handoverResend.test.tsx` (6); order-inquiries + _shared hooks +
  ParkedSendDialog vitest green.
- Cloud browser pass on e5132429: PASS 10/10 (PR comment).
