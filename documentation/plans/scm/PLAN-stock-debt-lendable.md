# PLAN - far-dated landed pins become lendable to nearer sales orders (STOCK-DEBT-LENDABLE)

Status: in progress, 30 Sep 2026. Feature track (read model + the cell dialog's status and
Covered by wording; no write, no migration, no RBAC change, no new ingest surface). UAC:
`stock-debt-lendable-acceptance-criteria.md`. Mockup:
`documentation/mockups/stock-debt-lendable.html`. PR #1398.
Domain: SCM, Stock Debt view (`_assignments(view=True)`) and the shared supply assignment.

## The owner's words (30 Sep 2026)

Product SRTSS8710, book Project. SO381065 (due 29/03/2027) is Pinned 88 "On hand BRW-BB"
because its linked SPO was received. SO396071 (due 01/09/2026) reads Short 32 and the Sep 26
cell is -76, Oct -29, Dec -207, while 88 units sit on hand held for a March-2027 order.

Owner: "would like this stock to be allocated nearer like oct, nov, dec".

The owner first thought of a user-chosen cutoff date on the view, then rejected it himself:
"dangerous if the user wants to see the stock debt until say June, the stock will go to Mar
which gives a high stock urgency for near term".

## Why option B (today + lead time), not a cutoff date

A number that depends on how far the reader scrolled is not a number a planner can act on:
two people looking at the same product on the same morning would see two different debts, and
widening the range to see further would itself move stock to the far order and paint the near
months red. So lendability is anchored to a fact about the ORDER, never about the VIEW: the
pinned line's own required date against `as_of + product lead time + 14 days`. That is
`reserve_window_end`, the window the fulfilment board already uses to decide who is "a later
order that can wait" when it proposes a Borrow (`ProjectSupplyService._eligible_donor`). A
line due on or after that day can still be re-bought in time, so its landed goods may go to a
nearer line now and the far line gets an order-back; a line due inside it cannot, so its goods
stay with it exactly as R7 rules.

`date_from` / `date_to` / `group` / `book` keep their R14 meaning: they hide rows and never
move stock (R2).

## What main did (origin/main 55421aaf)

- `StockDebtService._landed_holds` (`app/services/scm/stock_debt_service.py`) pins
  `min(landed, open)` to the line's own bin as `Hold(landed=True)`; `assign()` step 1 spends
  it at any date. Landed comes from `_po_received_by_so_line_ref` (batched per product).
- `_eligible_donor` (`project_supply_service.py`) restated the window test inline.
- No unpin, no horizon: `date_to` filters demand rows before the walk (R14), which is exactly
  the "cutoff" effect the owner refused when he thought it through.

## The rule (R46, 30 Sep 2026, Stock Debt view only)

1. **A landed pin is lendable when its line can wait.** `later_order_can_wait(required_date,
   window=reserve_window_end(as_of, lead), tba_from)`: dated, before the TBA line, due on or
   after the window. One function (`front_planning_engine.later_order_can_wait`), read by
   `_eligible_donor` and by `_landed_holds`, so the view never lends a pin the board would not
   offer as a donor.
2. **The lendable quantity walks as free on hand at its bin.** Nearer lines of the same
   ownership group draw it first-come by required date (R40 still seals groups). The group's
   free stock, at ANY of its bins, is drawn before a lent part, so a nearer line that the
   group could cover otherwise lends nothing, whatever the bins are called (reviewer round).
   Claims are registered in the lines' own walk order, never the query's row order.
3. **The far line keeps its claim.** At its own step it takes what is left of its landed
   goods (a pinned, landed take, as today); what nearer lines took is LENT. It reads
   `order_back` for the lent quantity (a planned re-buy at its own date; its month books the
   shortfall, amber because it can still be bought for), and stays `pinned` for the rest.
   `short` outranks: a line short beyond what it lent reads `short`, the two halves said apart
   ("short 12 · order back 88"). The order-back figure is `min(lent, shortfall)`: what the
   line is still owed for the lend, never more than its own shortfall.
4. **The receiving line says whose stock it has**: Covered by "On hand BRW-BB (from
   SO381065)" beside plain "On hand BRW-BB" for any free part. The lending line lists
   "Lent to SO396071 (32)" per receiver.
5. **The board and the ladder never lend** (`assignments_for` passes no `view`). There the far
   line stays `pinned` and is a Borrow DONOR: the same window, turned into a decision by the
   board's own Confirm, which raises the order-back.
6. **The page stays read-only.** Owner, on the mockup (30 Sep 2026, verbatim): "don't need
   rebalance step, this is a dashboard view only". The Rebalance button, its Preview and
   Confirm, the preview route and its permission gate were built, proven end to end and then
   removed on that ruling (commit history of PR #1398 has them). A planner who wants the lend
   made real opens the receiving order on the fulfilment board, where the far line is offered
   as a Borrow donor and the order-back is raised by that Confirm.

## Design (simplest thing that works)

- `supply_assignment.py`: `Hold.lendable`; a lendable hold registers a CLAIM in step 1
  (capped like a pin: need, floor, earlier claims) instead of spending the event; `_walk`
  takes the claim at the line's own step ahead of the pile draw; `_attribute_lends` charges
  the draws off a claimed floor to free stock first, then to the claimants in hold order,
  splitting a receiving take where it straddles; `LineResult.lent` / `lent_qty`,
  `Assigned.lent_from_line_key` / `lent_from_so`, `STATUS_ORDER_BACK`.
- `front_planning_engine.later_order_can_wait`; `_eligible_donor` calls it.
- `stock_debt_service.py`: `_assignments(view=True)` decides ONCE which lines can wait
  (`later_order_can_wait` off the batched `lead_times` read, R7: no extra query) and hands
  that set to BOTH readers of a line's landed goods: `_holds`' received-SPO placement branch
  (SPO-RECEIVED-PIN) and `_landed_holds` (R7's own-purchase read). `cell()` adds `lent_qty`
  and the two Covered by shapes.
- Owner hand test 1 (30 Sep 2026) FAILED on the dev copy: "no lend, Sep 26 still -76,
  SO381065 still pinned 88". Crew's diagnosis: SO381065's 88 reach the assignment through
  the AUTO placement on its received SPO-2026/05-0001 (`order_inquiry_links`), which
  `_holds` pinned as a plain on-hand hold; `_landed_holds` then netted its own read to
  nothing, so no claim was ever registered and nearly every real received SPO (all have an
  auto link) lent nothing. Fixed by marking that placement pin lendable too;
  `test_the_far_lines_placement_on_its_received_spo_lends_too` reproduces the shape. A
  confirmed Reserve (`so_line_allocations`) is still never lent: it is a decision, not
  landed goods.
- `schemas/stock_debt.py`: `order_back` status, `lent_from_so_number` on the on-hand entry,
  the `lent` entry, `lent_qty`.
- No route change, no write.
- FE: status pill `order back N` (violet; "short N · order back M" when short outranks),
  Covered by wording, the lent entry linking the receiving order. Nothing else on the page.

## Decisions taken in the lane (recorded for the owner)

- R5 (Rebalance) dropped by the owner on the mockup: see rule 6. The two questions the lane
  put back with the mock (whole-product preview; Borrow + Buy split) are moot with it.
- With `date_to` hiding the lender's row, the receiving line still gets the same quantity and
  the same status; only the lender's NAME is not printed, because its row (and its pin) are
  off the page. That is R14's own semantics, unchanged by ruling ("do not change `date_to`").
- Observed while R5 was still in: the board confirm's existing auto-link pass placed the
  ORDER_BACK row on the far line's own already-received PO line (`from_so_line_ref` match),
  and R43 then reads that placement as fulfilling. Pre-existing auto-link behaviour, outside
  this lane; reported on the PR as a crew-note.

## Tests

- `tests/scm/test_stock_debt_lendable.py` (pure walk, 15): AC-1 to AC-11.
- `tests/scm/test_stock_debt_lendable_routes.py` (Postgres, 8): the window off the lead
  read, TBA/undated, R2 with and without `date_to`, `date_from`, board parity, the cell wire
  by name.
- Vitest: `StockDebtCellDialog.lendable.test.tsx` (pill, Covered by).

## Out of scope

- The board and the ladder's own walk.
- Any write from this page (owner ruling, rule 6).
- The auto-link pass's choice of document for an order-back row.
- Any change to `date_to` / `date_from` semantics.
