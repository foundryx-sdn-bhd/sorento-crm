# UAC: park reserve requests and reserve notices, send all parked (OI-STAGED-NOTIFY)

Plan: `PLAN-oi-staged-notify-14oct.md`. Card: `oi-staged-notify-behaviour-card.md`. Mock v6.
Examples use dev OIs (9 raised by Eling, e.g. OI-2609-0001/0002/0004/0590/0592/0612/0614/0730/
0731; 8 raised by Niki, e.g. OI-2609-0022/0641/0717/0718/0719/0727/0728/0733); tests seed their
own equivalent chain (CI DB is empty).

## Request side (purchasing -> CS)

- AC-SN-01 `POST .../reserve-requests` with `park: true` creates the request (state `requested`,
  `parked_at` set) and queues NO `order_inquiry_reserve_requested` dispatch.
- AC-SN-02 `park` omitted or false: today's behaviour unchanged (one dispatch, `parked_at` null).
- AC-SN-03 A parked request is visible and answerable by CS like any open request (commit works).
- AC-SN-04 Preview (`.../reserve-requests/parked/preview`) covers only the caller's parked
  requests, grouped one email per first non-actor recipient, and returns per email `to`, `cc`,
  `inquiry_count`, `line_count`, `subject` and `body_html` (rendered by the same template the
  send uses). Dev config (no fixed CS user): Joey's 17 parked (9 raised by Eling, 8 by Niki) -> 2
  emails, To Eling Cc Joey 9, To Niki Cc Joey 8. With a fixed CS user configured: 1 email To that
  user, Cc Joey + Niki (deduped, Eling once).
- AC-SN-05 Preview and send carry only rows CS has not answered (`qty_reserved IS NULL`), with
  current remaining; a request answered in full or cancelled is not included.
- AC-SN-06 Send stamps the caller's current parked requests (`parked_at` null), queues one
  dispatch per email group in the same transaction, returns `{emails_sent, items_sent,
  unresolved}`; nothing is dispatched if the transaction rolls back.
- AC-SN-07 A second send (same or other user, or concurrent) sends nothing for already-sent
  items: `emails_sent = 0`.
- AC-SN-08 A group whose recipients resolve to nobody (no fixed user, raiser and requester
  without email, or no enabled automation) is reported in `unresolved`, is not stamped and stays
  parked.
- AC-SN-09 A colleague's parked request is counted in the `parked` status but is neither
  previewed nor sent by the caller, and not in the caller's `my_parked_requests`.
- AC-SN-10 Cancelling a parked request clears `parked_at` and sends no email; a commit that
  completes a parked request clears `parked_at`.
- AC-SN-11 A one-OI email renders today's subject and body unchanged. A multi-OI email renders
  subject "Reserve requests: N order inquiries (Joey)", heading "N order inquiries need your
  reserve", the OI and line counts, the first 5 OIs by OI no (OI NO, S/O NO, CUSTOMER, LINES =
  requested lines), "and M more order inquiries." when M > 0, and ONE button "View order
  inquiries that need your reserve" linking to `/project-sales/order-inquiries?
  reserve=needs_reserve`. It never lists more than 5 OIs.

## Notice side (CS -> purchasing)

- AC-SN-20 `reserve-commit` with `park_notice: true` writes reserves/amendments as today, sets
  `notice_parked_at`/`notice_parked_by` on each touched request row, queues NO
  `order_inquiry_reserved` dispatch.
- AC-SN-21 `park_notice` omitted or false: today's dispatch, and the touched rows get
  `notified_qty = qty_reserved`, `notice_parked_*` cleared.
- AC-SN-22 Notice preview/send (`.../reserve-notices/parked/...`, RESERVE grant) carries only
  rows parked by the caller whose `qty_reserved` differs from `notified_qty` (null counts as
  different, so a parked Reserve 0 is sent), grouped one email per first non-actor recipient (the
  requester); send stamps `notified_qty` and clears the park.
- AC-SN-23 Amend after sent (B2154-NL 200 sent, amended to 150 parked) -> next preview carries
  that row only, figure 150. Amend back to 200 before sending -> nothing parked for that row.
- AC-SN-24 Two parked commits on one row before sending -> one email row with the latest figure.
- AC-SN-25 Double send / concurrency / unresolved / colleague rules as AC-SN-07/08/09.
- AC-SN-27 `GET /order-inquiries/{inquiry_id}/reserve-notices/recipients?row_ids=<ids>` (RESERVE)
  resolves each staged row like commit does (open request row, else latest answered) and returns
  `{to: {name}, cc: [{name}]}` = what the enabled `order_inquiry_reserved` automation would
  resolve for this OI's request (requester To, OI raiser Cc, the caller moved to Cc); `to` null
  when nobody resolves. Unknown or foreign inquiry 404.
- AC-SN-26 Multi-OI notice email: subject "Reserved: N order inquiries (Eling)", the short shape
  of AC-SN-11 (heading "CS answered your reserve requests on N order inquiries", LINES = lines in
  the notice), button "View reserved order inquiries" -> `?reserve=reserved`; one-OI keeps today's.

## Status (Documents view)

- AC-SN-30 `GET /order-inquiry-headers?reserve=<status>` returns OIs with at least one request row
  in that status: `parked` "Parked request to reserve" (unanswered, request parked),
  `needs_reserve` "Needs reserve" (unanswered, request sent, state requested), `notice_parked`
  "Parked reserve" (answered, notice parked), `reserved` "Reserved" (answered, nothing parked). Each row carries `reserve_status` = its earliest status in that order (null when the
  OI has no request rows) and `parked_by_name` (the requester or notice parker when parked).
- AC-SN-31 `GET .../reserve-status/summary` returns the OI count per status (everyone's) and the
  caller's own `my_parked_requests` / `my_parked_notices` item counts.
- AC-SN-33 (review round 1) The bulk or one-OI shape follows DISTINCT order inquiries: a group
  whose requests all belong to one OI sends each request with today's one-OI email (never
  "1 order inquiries"); `items_sent` counts distinct OIs. The migration backfills `notified_qty =
  qty_reserved` on rows answered before it, so "amend back to the sent figure" also holds for them.
  The plain-text bulk body says "and 1 more order inquiry." in the singular.
- AC-SN-32 Template update is skipped (left as is) when the stored subject/layout differs from
  the seeded value; applied when equal; downgrade restores the seeded value.

## Frontend

- AC-SN-40 Request lightbox shows "Park this first" UNTICKED on every open; unticked -> "Send
  request", `park: false`, today's toast; ticked -> "Park request", `park: true`, toast "Request #n
  parked. Send it from Order inquiries > Actions."
- AC-SN-41 OI detail: Reserve (N) opens a confirmation "Reserve N lines on <OI no>" listing the
  staged lines (Product, Requested, Reserve, Balance to buy, Location, Reason) and "Notice to
  purchasing: To <name>, Cc <names>", with the request lightbox's footer: "Park notice first"
  tickbox (unticked on open), Cancel, one primary button. Unticked: "Reserve and send to <name>"
  commits with `park_notice: false` (today's toast). Ticked: the button reads "Reserve and park
  notice", commits with `park_notice: true` and toasts "Reserved. Notice to <name> parked; send
  it from Order inquiries > Actions." Cancel or Esc commits nothing and keeps the staged lines.
- AC-SN-42 Documents list: Filters has "Reserve" (Parked request to reserve / Needs reserve /
  Parked reserve / Reserved, counts from the summary, clearable) synced to `?reserve=`, with a chip
  "Reserve: <status>"; picking a value sets the state toggle to All; opening `?reserve=
  needs_reserve` from the email lands with it set. A Reserve column shows the status pill, plus
  the parker's name for Parked request to reserve.
- AC-SN-43 Toolbar Actions menu (never a top-bar button, even with one item): "Send all parked
  requests to CS (N)" (ACKNOWLEDGE) / "Send all parked reserves to purchasing (N)" (RESERVE), N =
  `my_parked_requests` / `my_parked_notices`; disabled with reason when N = 0.
- AC-SN-44 `ParkedSendDialog` (shared, `components/common`): recipient table (To, Cc, Order
  inquiries, Lines; total row), a "No recipient, stays parked" line per unresolved group, one line
  tab per email showing its subject, To/Cc and rendered body (clicking a table line selects its
  tab), footer "Send N emails"; success toast "N emails sent (M order inquiries)"; `emails_sent =
  0` -> "Nothing left to send".
- AC-SN-45 Usable at 375 and 1280 (lightbox grid and email frame scroll inside, toolbar wraps).
