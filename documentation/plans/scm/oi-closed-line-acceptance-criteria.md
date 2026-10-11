# OI-CLOSED-LINE acceptance criteria

Source: behaviour card v2 (`oi-closed-line-behaviour-card.md`), owner approval 11 Oct 2026
(Q1 (a) skip the free-stock recheck for the delivered part; Q2 (a) clean-up cancels only the CKSW015
CANCEL_BALANCE row). Owner model: AutoCount delivered qty and the plan / OI are decoupled; the OI changes only
through CS decisions confirmed in Fulfilment Planning; Confirm sends the purchasing email.

## Batch apply on a closed line

- AC-1 A pending planning-change row (decision confirm or amend, kind not `cancelled`) whose core SO line
  became `closed` (fully delivered) after the batch was built applies like an open line: `applied_state` is
  `applied`, and no row carries the reason "the sales-order line is closed".
- AC-2 That apply, with a composition of Reserve at BRW and buy 0, cancels the line's raised ORDER row
  (purchasing is no longer asked to buy) and the cancelled row is queued to the handover email of that same
  commit (kind `cancelled`).
- AC-3 Same as AC-2 for a Borrow composition (other location, buy 0).
- AC-4 Other lines of the same batch still apply as before (no regression of AC-S1-3's "rest of the order
  applies").
- AC-5 A pending row whose core line became `cancelled` (Transfer to Cancel SO or the book) between compose and
  apply is still superseded, reason "the sales-order line is cancelled". `cancelled`-kind rows keep their own path.

## Free-stock recheck at Confirm (Q1)

- AC-6 Confirm of a Reserve (or Borrow) on a line whose core `qty_delivered` covers the reserved qty succeeds
  even when the reserve location now has 0 free stock: the delivered part of the line needs no free stock.
- AC-7 The exemption is the delivered part only: on a line with ordered 10, delivered 4, a Reserve of 10 at a
  location with 0 free stock is refused as today (6 undelivered units need stock); with 6 free it succeeds.
- AC-8 A line with nothing delivered is rechecked exactly as today (over-reserve refused).
- AC-9 No stock hold is written for delivered qty (the hold stays capped at what is still owed).

## AutoCount ingest (regression lock)

- AC-10 An ingest that sets a line `closed` because delivered >= ordered changes no OI row (state, qty, note,
  ack_state) and queues no handover email.

## One-off clean-up script (owner runs on prod)

- AC-11 `python -m scripts.oneoff.oi_closed_line_cleanup --inquiry <OI no>` without `--apply` writes nothing
  and prints each row it would change (item code, verb, qty, ordered, delivered, reason).
- AC-12 With `--apply` it cancels the OI's raised ORDER / ORDER_BACK rows whose SO line has a planning-change row
  superseded "the sales-order line is closed", or whose core line is `cancelled`; note names the dropped
  decision; each is queued to one handover email. Placed, partly linked and actioned rows are never touched.
- AC-13 `--cancel-balance <item code>` additionally cancels that item's raised CANCEL_BALANCE row(s) on the OI
  (CKSW015); the placed ORDER row of that item stays as it is.
- AC-14 Re-running with `--apply` after a successful run changes nothing (idempotent).
