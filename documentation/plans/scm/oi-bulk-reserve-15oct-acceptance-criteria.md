# UAC: one-button bulk Decide on the OI Lines tab (OI-BULK-RESERVE)

Plan: `PLAN-oi-bulk-reserve-15oct.md`. Card: `oi-bulk-reserve-behaviour-card.md` (final rules v3).

- AC-BR-01 With the reserve grant and lines ticked, the Lines tab selection strip shows one button
  "Decide (N)" at its right end; N counts only ticked LINES (not rows) with an open (unanswered)
  request row; disabled at 0; not shown with nothing ticked or without the reserve grant.
- AC-BR-02 Its menu has exactly "Reserve as requested" and "Reject..." (owner 16 Oct 07:35: the
  bulk "Reserve..." tried after the 07:00 hand test is withdrawn).
- AC-BR-03 "Reserve as requested" stages each counted line exactly as the line's tick does
  (the requested qty at the request row's location, no cap at remaining; chip "Reserve <qty> @ <loc>"),
  replaces an existing staged entry, clears the ticks, opens no dialog.
- AC-BR-04 "Reject..." opens "Reject N lines" with one Reason; "Save N lines" is disabled until a
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
  (chip "Reserve 0") and leaves other ticked lines ticked; Cancel stages nothing. Answered lines show
  no x (pen = Amend only).
- AC-BR-08 Usable at 375 and 1280 (strip wraps, Decide button stays reachable at the right).

- AC-BR-10 (retired 16 Oct 07:35) The bulk "Reserve..." popup built after the 07:00 hand test is
  removed: owner "the first version is correct ... if they want to change the quantity, they can go
  line by line" - the experience stays Fulfilment Planning's (Decide saves, Reserve (N) confirms).
- AC-BR-11 (owner 16 Oct 07:00, kept at 07:35) The pen's Edit reserve prefills Reserved with the
  full request and never caps or recomputes it by the location's CRM stock (AutoCount may already
  hold the reservation while CRM stock lags); stock shows as information ("available N") on each
  location option; any qty 0..requested; Reason only when below the request. The backend accepts it
  (commit cap = min(qty_requested, requestable), no stock check).
- AC-BR-12 No "Stage" anywhere on the OI screens: the pen's button and the reject dialog read
  "Save" / "Save N lines", Fulfilment Planning's own word (`BoardDecideControl.tsx` "Save N lines",
  `BoardDecisionPill.tsx` "Saved").

## Part 2: cross-OI Lines page (mock v4, owner OK 16 Oct 09:08, final v3 shape)

Owner 16 Oct 09:08 "ok pls go ahead" on mock v4, built in the FINAL v3 shape (07:35): Decide saves
draft decisions like Fulfilment Planning, the existing Reserve (N) confirmation commits. This
replaces mock v4's "no staging, Decide goes straight to the confirmation".

- AC-BR-20 `GET /order-inquiries?reserve=<parked|needs_reserve|notice_parked|reserved>` keeps a
  row when one of its own request rows is in that status (same predicate as the Documents view);
  the summary honours it; an unknown value is 422.
- AC-BR-21 The Lines view's Filters popover has "Reserve" with the four Documents-view labels,
  clearable, shown as a chip, carried in the URL as `reserve=`.
- AC-BR-22 A reserve-grant holder can tick lines on the Lines view (across OIs); "Decide (N)" sits
  at the toolbar's right end while lines are ticked, N = ticked lines with an open request row,
  disabled at 0; its menu has exactly "Reserve as requested" and "Reject...". Without the grant:
  no Decide, no reserve controls.
- AC-BR-23 "Reserve as requested" saves a draft on each counted line (requested qty at the
  request row's location, chip "Reserve <qty> @ <loc>" with Undo), no dialog; "Reject..." opens
  "Reject N lines" with one required Reason and "Save N lines", saving Reserve 0 + reason (chip
  "Reserve 0"). Both clear the ticks and replace an earlier draft. Cancel saves nothing. No word
  "Stage" on screen.
- AC-BR-24 A line's pen (Edit reserve) on the Lines view is the same form as inside the OI: prefill
  = the full request, never capped by CRM stock ("available N" on each location option), any qty
  0..requested, Reason only below the request, button "Save"; it saves a draft like the above.
- AC-BR-25 While drafts exist, a "Reserve (N)" button (N = draft lines) opens the existing
  Reserve confirmation in its multi-OI mode: "Reserve N lines on M order inquiries", one row per OI
  (OI no, Reserve count, Reject count), and "Park notice first".
- AC-BR-26 Confirming sends one `reserve-commit` per OI (its drafts: qty, warehouse, reason;
  `park_notice` per the tickbox); each OI saves all or nothing; the dialog shows a result per OI
  (saved, or the server's message) and ends on Close; saved OIs' drafts clear and their lines
  refresh, failed OIs' drafts stay; a toast sums it up.
- AC-BR-27 "Send all parked reserves to purchasing (N)" is in the Lines view's Actions menu,
  same count and send as the Documents view.
- AC-BR-28 Usable at 375 and 1280.
