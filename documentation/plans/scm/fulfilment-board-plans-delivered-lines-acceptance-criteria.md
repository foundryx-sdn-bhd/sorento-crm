# UAC - Fulfilment board plans what nobody decided, delivered or not

Plan: `PLAN-fulfilment-board-plans-delivered-lines.md` (same folder).
Owner rulings, 14 Sep 2026 (chat, from SO421404): the board asks "has someone decided where
this line's stock comes from", not "is delivery still outstanding"; a delivered line nobody
decided is planned at its ordered quantity so a Buy raises the order-back inquiry; a line that
already carries a live order inquiry row (the migrated book, #875) counts as decided on the
buying side and is not planned again; the board is always shown for the selection, with decided
lines read-only, never hidden behind a "fully planned" gate; old delivered orders that used
stock and never bought are acceptable on the board because they are only there when selected.

## Journey

**CS, from a completed sales order.** Project Sales Admin > Sales Orders. SO421404 reads
Completed, three lines, 3 ordered, 3 delivered, 0 outstanding, Linked to `-`. The stock left
BRW and BRW-BB with no plan behind it, so nothing ever reached order inquiries and purchasing
was never told to buy back. CS ticks the order and chooses Plan selected. The board opens on
SO421404. Each line is there: SRT320-CR at BRW-BB 1, SRT320-GM at BRW 1, SRTWT5903 at BRW 1,
each showing ordered 1 and delivered 1. The ladder walks today's stock. Where the location still
holds a unit the suggestion is Use own location; where the delivery emptied it the suggestion is
Buy 1. CS saves and confirms. The Buy lines raise ORDER rows on the order's inquiry, purchasing
sees them in the inquiry list, and the sales order's Linked to column names the inquiry. The Use
own location lines hold nothing: the unit already shipped. Decisions CS makes: the same one per
line as today. Nothing new to fill in.

**CS, on an order the book already bought for.** The same board on an order whose lines carry a
raised or placed inquiry row from the migrated order inquiry sheet and no board decision. Those
lines are decided: read-only, naming the inquiry, no suggestion, not in the confirm set. Only
lines with neither a decision nor a live inquiry row are proposed for.

**CS, on a fully decided completed order.** The board still opens. Every line reads decided
(composition, or the inquiry). Nothing to confirm. No empty state, because the lines are there.

**Purchasing.** Nothing new. The ORDER row lands in the inquiry list the way a Buy on an open
order does today.

## Acceptance criteria

### S1 - Stock holds follow what is still owed [BE]

- **AC-S1-1 [BE]** Given a confirmed own-location allocation of 3 on a core line with 3 ordered
  and 1 delivered, when free stock is computed (`ProjectSupplyService._free_stock`), then the
  hold subtracted is 2, not 3.
- **AC-S1-2 [BE]** Given a confirmed own-location allocation on a core line fully delivered,
  when free stock is computed, then that allocation holds 0.
- **AC-S1-3 [BE]** Given a confirmed own-location allocation on a core line whose line_status is
  `cancelled` with a delivered quantity the book never reversed, then that allocation holds 0.
- **AC-S1-4 [BE]** Given the same allocations, when the stock debt screen lists holds
  (`stock_debt_service._holds`), then each hold's quantity is the same capped figure the free
  stock arithmetic used: one expression, two readers.
- **AC-S1-5 [BE]** Given an allocation on a core line with nothing delivered, then its hold is
  unchanged from today (the cap is a no-op above the delivered line).

### S2 - The board admits every undecided line and plans its ordered quantity [BE]

- **AC-S2-1 [BE]** Given a project sales order with status `closed` whose line is `closed`,
  3 ordered, 3 delivered, no active decision and no inquiry row, when the board is built for
  that order, then the line is a contribution with qty 3, qty_ordered 3, qty_delivered 3, and
  the ladder proposes for it.
- **AC-S2-2 [BE]** Given that line's location has free stock of 0 after the delivery, then the
  proposal is Buy 3 (verb ORDER on confirm).
- **AC-S2-3 [BE]** Given that line's location group has free stock of 5, then the ladder proposes
  a stock rung for 3 (whichever rung the ladder's own rules pick; the UAC does not prescribe the
  composition), and after confirm the reserve it writes holds 0 (AC-S1-2).
- **AC-S2-4 [BE]** Given an open line with 3 ordered, 1 delivered, no decision, then the line
  asks for 3 (not 2): 1 still to ship, 1 to put back. RESTATED 14 Oct 2026 (owner rule:
  delivered qty consumes the Reserve/Borrow first, then the Buy): after a confirmed Reserve 1 +
  Buy 2 on it, the delivered unit used the Reserve up, so the hold is 0 and free stock for
  other orders is untouched; the Buy 2 covers the 2 still owed. (Was: the hold counted
  `min(1, 3 - 1) = 1`.) The rung composition is the ladder's own business (rule 7 keeps the
  own bin out of Reserve; the UAC does not prescribe it).
- **AC-S2-5 [BE]** Given a line with `purchasing_status = covered`, then it is not admitted (a
  person already ruled "no purchase needed").
- **AC-S2-6 [BE]** Given a line with `line_status = cancelled`, then it is not admitted, and a
  sales order with status `cancelled` contributes no line.
- **AC-S2-7 [BE]** Given an open line with a live inquiry row (state not `cancelled`, ack_state
  not `rejected`) and no active decision, then the row reads `covered` true, `decision` null,
  `order_inquiry` naming the inquiry, `proposal` empty, and it is excluded from
  `confirmLinesFor`. Its OWN share fields are null (it stands in no queue), but the line is
  NOT removed from other lines' competing demand: it holds no stock, so `_decided_elsewhere`
  is untouched - see PLAN note 5 under "What changes in the planning engine".
- **AC-S2-8 [BE]** Given a line whose only inquiry row is `cancelled`, or whose row purchasing
  rejected and CS has not answered, then it is admitted as undecided and proposed for.
- **AC-S2-9 [BE]** Given the confirm endpoint receives components for an admitted delivered line,
  then the "components must sum to the quantity" check compares against the ordered (or
  required) quantity, not the still-owed one, and the refusal wording names that figure.
- **AC-S2-10 [BE]** Given an active decision frozen before this change on a line with nothing
  delivered, then the drift check (`_carry_snapshot_has_drifted`) does not flag it: frozen
  `open_qty` equals the plan quantity. (Measured 14 Sep on the 0907 copy: 97 decided lines, 0
  delivered in part or in full.)
- **AC-S2-11 [BE]** Given the netting engine, the reorder plan and the planning worklist, then
  `is_open_demand()` and `demand_qty()` are unchanged and a delivered line is still not demand
  there (existing tests stay green untouched).
- **AC-S2-12 [BE]** `test_only_open_demand_of_an_open_project_order_reaches_the_board` is
  rewritten to the new rule: the delivered line IS demand (qty 10), the covered and cancelled
  lines are not, the closed-by-delivery line is.
- **AC-S2-13 [BE]** Given a board whose selection has only cancelled or covered lines, then
  `line_count` is 0 and `cells` is empty.
- **AC-S2-14 [BE]** Given a project sales order with status `closed` whose lines are delivered
  and undecided, when it is adopted (`ProjectSOAdoptionService.adopt`, the list's Start), then
  adoption succeeds and mirrors those lines (a `project_line_id` exists for each), so confirm can
  name them. A `cancelled` order is still refused.
- **AC-S2-15 [BE]** Given an open order with one still-owed line and one delivered undecided
  line, when adopted, then BOTH are mirrored: the mirror predicate is `is_undecided_demand()`,
  the board's, not `is_open_demand()`. A covered line and a cancelled line are not mirrored.
- **AC-S2-16 [BE]** Given a closed order's delivered undecided line, when a suggestion is saved
  or deleted on it (`project_line_draft_service._resolve_core_line`, behind
  `PUT`/`DELETE /fulfilment-planning/lines/{contribution_key}/draft`), then the key resolves and
  the draft is written and removed. The draft service's line set is the board's `_demand_rows`
  set - `SalesOrder.status IN ('open','closed')` and `is_undecided_demand()` - because the key it
  resolves is the one the board just handed out, and a resolver narrower than the board refuses
  a line the planner is looking at. A cancelled line is still a 422: the board never offered it.
  (Found on the lane's own browser walk, 14 Sep: three `Save all suggested` PUTs 422'd on a
  Completed order whose board had proposed Buy on every line, which blocks the whole of
  AC-E2E-1 after the board renders.)
- **AC-S2-17 [BE]** Given a saved suggestion on a line, then it reads `stale` when THE ASK
  changed, and the ask is the plan quantity: a change to `qty_ordered` (or `qty_required`)
  makes it stale, and a part delivery does not - the planner was looking at the plan quantity
  and a delivery does not move it. The snapshot `project_line_draft_service._line_snapshot`
  writes and its reader `sales_order_service._saved_is_stale` both read the plan quantity, and
  they move together: one side alone and every existing draft reads stale on the next board
  build. `required_date` is unchanged as the second half of the comparison.

### S3 - The board reads right [FE]

- **AC-S3-1 [FE]** Given a board response with `line_count` 0, then the empty state reads
  "No lines to plan on these sales orders" with the sub-line "Every line is cancelled or marked
  no purchase needed." The old "Nothing is outstanding on these sales orders that can be
  planned" wording is gone from component and tests.
- **AC-S3-2 [FE]** Given a contribution with `covered` true and `decision` null and an
  `order_inquiry`, then the cell renders it the way a decided line renders today (no suggestion,
  no confirm checkbox), with the inquiry number visible where the decision composition would be.
- **AC-S3-3 [FE]** Given a contribution with qty_delivered equal to qty_ordered, then the
  contribution shows ordered and delivered as today; no new "delivered" badge or explanation is
  added (no on-screen feature explanation).
- **AC-S3-4 [FE]** The day-window empty state ("Nothing is outstanding in these dates") is
  reworded to "No lines in these dates" with the same sub-line.

### S4 - "Planned" on the Sales Orders list and detail header [BE] [FE]

- **AC-S4-1 [BE]** Given the sales orders list endpoint, then every row carries integer
  `planned_lines` and `plannable_lines`, and the detail endpoint carries the same two (asserted
  in a test, since `response_model` drops undeclared fields).
- **AC-S4-2 [BE]** Given an order with 3 admitted lines, 1 covered by an active decision, 1 with
  a live inquiry row, 1 with neither, then `planned_lines` is 2 and `plannable_lines` is 3.
- **AC-S4-3 [BE]** Given an order whose lines are all cancelled or `purchasing_status =
  covered`, then `plannable_lines` is 0.
- **AC-S4-4 [BE]** Given a closed order with every line delivered and no decision or inquiry
  row (SO421404's shape), then `planned_lines` is 0 and `plannable_lines` is 3.
- **AC-S4-5 [BE]** Given a page of 25 orders, then the counts come from one grouped query for
  the page, not one per row (query count asserted).
- **AC-S4-6 [FE]** Given list rows with the two counts, then the Planned column renders a
  Badge: 3/3 "Planned" (success), 2/3 "Partly 2/3" (warning), 0/3 "Not planned"
  (destructive), 0/0 "-" (muted). Column has an explicit `size` and sits after Status.
- **AC-S4-7 [FE]** Given the sales order detail page, then the same Badge renders in the header
  beside Status, identical in view and edit mode.
- **AC-S4-8 [FE]** No sort, no filter, no tooltip explanation on the pill.

### E2E

- **AC-E2E-3 [E2E]** After AC-E2E-1's confirm, back on the Sales Orders list the order's
  Planned pill reads "Planned"; before it, "Not planned".
- **AC-E2E-1 [E2E]** agent-browser, from `/` by sidebar: Sales Orders > tick a Completed order
  seeded with a delivered undecided line whose location is empty > Plan selected > board shows
  the line with Buy > confirm > Order Inquiries lists the ORDER row on that order > Sales Order
  detail Lines tab shows Linked to naming the inquiry.
- **AC-E2E-2 [E2E]** Same walk on an order whose lines all carry raised inquiry rows: the board
  opens, every line reads decided, Confirm (0).

### No motion

Nothing new animates. The board's existing cell motion is unchanged.
