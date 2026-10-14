# UAC: park reserve requests and reserve notices, send them in bulk (OI-STAGED-NOTIFY)

Plan: `PLAN-oi-staged-notify-14oct.md`. Card: `oi-staged-notify-behaviour-card.md`.
Examples use the card's dev OIs (OI-2609-0612/0614/0592 raised by Eling, 0718/0727 by Niki);
tests seed their own equivalent chain (CI DB is empty).

## Request side (purchasing -> CS)

- AC-SN-01 `POST .../reserve-requests` with `park: true` creates the request (state `requested`,
  `parked_at` set) and queues NO `order_inquiry_reserve_requested` dispatch.
- AC-SN-02 `park` omitted or false: today's behaviour unchanged (one dispatch, `parked_at` null).
- AC-SN-03 A parked request is visible and answerable by CS like any open request (commit works).
- AC-SN-04 `GET /order-inquiries/reserve-parked/summary`: `requests_total` counts every parked
  request still `requested` with at least one unanswered row; `my_requests` only the caller's.
- AC-SN-05 Preview (`.../reserve-requests/parked/preview`) covers only the caller's parked
  requests, grouped one email per first non-actor recipient, and returns per email only
  `to`, `cc`, `inquiry_count`, `line_count` (no bodies). Dev config (no fixed CS user): Joey's
  17 parked (9 raised by Eling, 8 by Niki) -> 2 lines, To Eling Cc Joey 9, To Niki Cc Joey 8.
  With a fixed CS user configured: 1 line To that user, Cc Joey + Niki (deduped, Eling once).
- AC-SN-06 Preview rows are only rows CS has not answered (`qty_reserved IS NULL`), with
  current remaining; a request answered in full or cancelled is not listed.
- AC-SN-07 Send stamps exactly the previewed set's current members (`parked_at` null), queues one
  dispatch per email group in the same transaction, and returns `{emails_sent, items_sent,
  unresolved}`; nothing is dispatched if the transaction rolls back.
- AC-SN-08 A second send (same or other user, or concurrent) sends nothing for already-sent
  items: `emails_sent = 0`.
- AC-SN-09 A group whose recipients resolve to nobody (no fixed user, raiser and requester
  without email, or no enabled automation) is reported in `unresolved`, is not stamped and
  stays in the count.
- AC-SN-10 A colleague's parked request is listed by the Parked filter but is neither previewed
  nor sent by the caller.
- AC-SN-11 Cancelling a parked request clears `parked_at` and sends no email.
- AC-SN-12 A one-OI email renders today's subject and body unchanged. A multi-OI email renders
  subject "Reserve requests: N order inquiries (Joey)", heading "N order inquiries waiting for
  your reserve", the OI and line counts, the first 5 OIs by OI no (OI NO, S/O NO, CUSTOMER,
  LINES = requested lines), "and M more order inquiries." when M > 0, and ONE button "View the N
  order inquiries waiting for your reserve" linking to `/project-sales/order-inquiries?
  reserve_batch=<send batch id>`. It never lists more than 5 OIs.
- AC-SN-13 Each email a send produces gets one `order_inquiry_reserve_send_batches` row; its
  requests carry that `sent_batch_id`. `GET /order-inquiry-headers?reserve_batch=<id>` returns
  exactly those OIs whatever their state; `GET .../reserve-send-batches/{id}` returns kind,
  sender name, sent_at, inquiry count; a batch of another company or an unknown id is 404.

## Notice side (CS -> purchasing)

- AC-SN-20 `reserve-commit` with `park_notice: true` writes reserves/amendments as today, sets
  `notice_parked_at`/`notice_parked_by` on each touched request row, queues NO
  `order_inquiry_reserved` dispatch.
- AC-SN-21 `park_notice` omitted or false: today's dispatch, and the touched rows get
  `notified_qty = qty_reserved`, `notice_parked_*` cleared.
- AC-SN-22 Notice preview/send (`.../reserve-notices/parked/...`, RESERVE grant) carries only
  rows parked by the caller whose `qty_reserved` differs from `notified_qty` (null counts as
  different, so a parked Reserve 0 is sent); groups one email per first non-actor recipient
  (the requester); send stamps `notified_qty` and clears the park.
- AC-SN-23 Amend after sent (B2154-NL 200 sent, amended to 150 parked) -> next preview carries
  that row only, figure 150. Amend back to 200 before sending -> nothing parked for that row.
- AC-SN-24 Two parked commits on one row before sending -> one email row with the latest figure.
- AC-SN-25 Double send / concurrency / unresolved / colleague rules as AC-SN-08/09/10.
- AC-SN-26 Multi-OI notice email: subject "Reserved: N order inquiries (Eling)", the same short
  shape as AC-SN-12 (LINES = lines in the notice), button "View the N order inquiries CS
  reserved" -> `?notice_batch=<id>`; rows carry `notice_batch_id`; one-OI keeps today's email.

## List (Documents view)

- AC-SN-30 `GET /order-inquiry-headers?parked=requests|notices` returns only OIs with such
  parked items (any user); each row carries `parked_request_by_name` / `parked_notice_by_name`.
- AC-SN-31 Template update is skipped (left as is) when the stored subject/layout differs from
  the seeded value; applied when equal; downgrade restores the seeded value.

## Frontend

- AC-SN-40 Request lightbox shows "Park this first" UNTICKED on every open (owner: default
  send); unticked -> "Send request", `park: false`, today's toast; ticked -> button "Park
  request", payload `park: true`, toast "Request #n parked. Send it from Order inquiries."
- AC-SN-41 OI detail shows "Park notice" (unticked on load) beside Reserve (N) for RESERVE
  holders; Reserve sends `park_notice` matching the tickbox.
- AC-SN-42 Documents list: Parked filter with counts from the summary, Parked column, chip;
  picking a Parked value sets the state toggle to All.
- AC-SN-43 Toolbar: "Send parked requests to CS (N)" (ACKNOWLEDGE) / "Notify purchasing (N)"
  (RESERVE), N = `my_requests` / `my_notices`; disabled with reason when N = 0.
- AC-SN-44 Preview dialog: ONE summary table (To, Cc, Order inquiries, Lines; total row), a
  "No recipient, stays parked" line per unresolved group, footer "Send N emails"; success toast
  "N emails sent (M order inquiries)"; `emails_sent = 0` -> "Nothing left to send".
- AC-SN-46 Opening `?reserve_batch=<id>` (or `?notice_batch=`) shows the Documents list with
  the state toggle on All and a chip "From email: Joey's reserve requests, 14/10/2026 (9)"
  ("... reserve notices ..." for notices); its X clears the param and returns to the normal list.
- AC-SN-45 Usable at 375 and 1280 (lightbox grid and email frame scroll inside, toolbar wraps).
