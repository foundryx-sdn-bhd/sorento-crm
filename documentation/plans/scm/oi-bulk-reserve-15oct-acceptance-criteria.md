# UAC: one-button bulk Decide on the OI Lines tab (OI-BULK-RESERVE)

Plan: `PLAN-oi-bulk-reserve-15oct.md`. Card: `oi-bulk-reserve-behaviour-card.md` (final rules v3).

- AC-BR-01 With the reserve grant and lines ticked, the Lines tab selection strip shows one button
  "Decide (N)" at its right end; N counts only ticked lines with an open (unanswered) request row;
  disabled at 0; not shown without the reserve grant.
- AC-BR-02 Its menu has exactly "Reserve as requested" and "Reject...".
- AC-BR-03 "Reserve as requested" stages each counted line exactly as the line's tick does
  (min(qty requested, remaining) at the request row's location; chip "Reserve <qty> @ <loc>"),
  replaces an existing staged entry, clears the ticks, opens no dialog.
- AC-BR-04 "Reject..." opens "Reject N lines" with one Reason; "Stage N lines" is disabled until a
  reason is typed; staging writes Reserve 0 + that reason on each counted line (chip "Reserve 0")
  and clears the ticks. Cancel stages nothing.
- AC-BR-05 Ticked lines without an open request (answered or never requested) are neither counted
  nor staged; their per-line pen (Edit / Amend reserve) still works.
- AC-BR-06 The header "Reserve (N)" shows the staged count and opens the #1684 confirmation listing
  every staged line (rejected lines with Reserve 0 and their reason); confirming commits all in one
  call with `park_notice` per the tickbox; a rejected line ends "declined" and purchasing gets the
  same Reserve 0 notice as a single-line Reserve 0 (sent or parked).
- AC-BR-07 Single-line tick / pen / Undo behave as before.
- AC-BR-09 A requested line shows an x "Reject" button between the tick and the pen (reserve grant
  only); it opens "Reject 1 line" with a required Reason; Stage stages Reserve 0 + reason on that line
  (chip "Reserve 0"); Cancel stages nothing. Answered lines show no x (pen = Amend only).
- AC-BR-08 Usable at 375 and 1280 (strip wraps, Decide button stays reachable at the right).

## Part 2: cross-OI Lines page (mock v4)

- AC-BR-20 `GET /order-inquiries?reserve=<parked|needs_reserve|notice_parked|reserved>` keeps a
  row when one of its own request rows is in that status (same predicate as the Documents view);
  the summary honours it; an unknown value is 422.
- AC-BR-21 The Lines view's Filters popover has "Reserve" with the four Documents-view labels,
  clearable, shown as a chip, carried in the URL as `reserve=`.
- AC-BR-22 A reserve-grant holder can tick lines on the Lines view; "Decide (N)" sits at the
  toolbar's right end, N = ticked lines that still need a reserve, disabled at 0.
- AC-BR-23 "Reserve as requested" / "Reject..." (one required reason, "Next") open ONE
  confirmation: "Reserve N lines on M order inquiries", one row per OI with Reserve and Reject
  counts, and "Park notice first".
- AC-BR-24 Confirming sends one reserve-commit per OI (requested qty at the request's location,
  or 0 + reason); each OI saves all or nothing; the dialog shows a result per OI with the
  server's message for a failed one and ends on Close; failed OIs' lines stay ticked, saved
  ones untick; a toast sums it up.
- AC-BR-25 "Send all parked reserves to purchasing (N)" is in the Lines view's Actions menu,
  same count and send as the Documents view.
- AC-BR-26 Usable at 375 and 1280.
