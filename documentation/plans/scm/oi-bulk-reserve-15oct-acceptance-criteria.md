# UAC: one-button bulk reserve on the OI Lines tab (OI-BULK-RESERVE)

Plan: `PLAN-oi-bulk-reserve-15oct.md`. Card: `oi-bulk-reserve-behaviour-card.md` (final rules).

- AC-BR-01 With the reserve grant and lines ticked, the Lines tab selection strip shows one button
  "Reserve (N)" at its right end; N counts only ticked lines that have an open (unanswered) request
  row. With none of those ticked it is disabled. Without the reserve grant it is not shown.
- AC-BR-02 Pressing it stages each counted line at min(qty requested, remaining) and the request
  row's location, the same entry the line's own "Reserve" button writes (chip "Staged <qty> @
  <location>"); a line already staged is replaced, not doubled.
- AC-BR-03 The same press clears the ticks and opens the reserve confirmation listing EVERY staged
  line: the lines just staged plus any staged singly before (e.g. 3 ticked + 1 staged by Edit
  reserve = "Reserve 4 lines on <OI>").
- AC-BR-04 Cancel on that confirmation commits nothing and keeps all staged chips; the header
  "Reserve (N)" shows the staged count.
- AC-BR-05 Confirming commits all staged lines in one call with `park_notice` per the tickbox
  (unchanged #1684 behaviour and toasts).
- AC-BR-06 Ticked lines without an open request (answered or never requested) are neither counted
  nor staged; their single-line "Amend reserve" / "Edit reserve" still work.
- AC-BR-07 Header "Reserve (N)" for single-line stages works as before.
- AC-BR-08 Usable at 375 and 1280 (strip wraps, button stays reachable).
