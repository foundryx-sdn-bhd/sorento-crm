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
