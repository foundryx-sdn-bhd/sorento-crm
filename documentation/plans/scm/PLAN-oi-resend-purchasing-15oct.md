# PLAN: OI-RESEND-PURCHASING - "Send to purchasing" for an Order Inquiry (15 Oct)

Status: Built, review fix round 1 done (PR #1714). Track: LEAD, cloud lane. Behaviour card answered by crew 15 Oct
(Q2-Q4 as recommended); Q1 (attach the OI Excel) is with the owner, built as (a) no attachment.

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

## Contract
- `POST /api/v1/project-sales/order-inquiries/handover-resend/preview` `{inquiry_ids}` ->
  `{emails[{inquiry_id, inquiry_no, to, cc, subject, body_html, inquiry_count, line_count, last_sent}],
  inquiry_total, unresolved, skipped[{inquiry_id, inquiry_no, reason}], automation_enabled}`.
- `POST .../handover-resend/send` `{inquiry_ids}` -> `{emails_sent, items_sent, skipped}`.
- skip reasons: `nothing_open`, `no_recipient`, `failed`. Unknown OI 404; empty / bad id / >50 is 422.

## Known limits
- "Last sent" only finds an automatic email whose `source_id` is this OI; a write that touched several
  OIs files its one email under the first OI.
- Q1 pending: attaching the OI Excel would render the workbook inside the send (follow-up switch).

## Evidence
- Backend: `tests/test_oi_handover_resend.py` (11; 9 red first in cea18b69, 2 added in review), plus 7 handover suites
  (164 passed) after the `_record_handover` split.
- Frontend: `OrderInquiryHeadersList.handoverResend.test.tsx` (6); order-inquiries + _shared hooks +
  ParkedSendDialog vitest green.
- Cloud browser pass on e5132429: PASS 10/10 (PR comment).
