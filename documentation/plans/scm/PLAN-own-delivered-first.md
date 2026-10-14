# PLAN - delivered qty consumes Reserve/Borrow before Buy (OWN-DELIVERED-FIRST)

Status: in progress, 14 Oct 2026, PR #1667 (lane STOCK-DEBT-DECOUPLE, small fix track: no
migration, no RBAC change, no new ingest surface). Owner confirmed the one rule 14 Oct 2026:
"delivered qty consumes own/borrow first, then buy. Keep it to that rule only (write + read)".

## The case

SO424555 line 16, CB6633-NL (prod). Decision Own 336 at BRW-IB + Buy 126 (plan 462), 336
delivered, 126 outstanding. `delivered_credits` (`project_supply_service.py`) credited the
delivered units to the Buy first, so 210 of the Own counted as delivered and 126 stayed held;
the read `held_qty_expr` = least(alloc qty 336, still owed 126) = 126, and Stock Debt showed
"Covered by On hand BRW-IB 126" at a bin the goods had left. Owner: the goods left from
BRW-IB, so the Own 336 is used up and the Buy covers the 126.

## The rule

Delivered qty (`delivered_qty_of`: delivered less AutoCount-cancelled) consumes the line's
Reserve and Borrow components first, in payload order, and only the rest reaches the Buy and
the timely SPO.

- **Write** (`delivered_credits`, read by the recheck, the R14 guard and
  `_write_allocations`): the credit is `min(delivered, plan qty)` spent across Reserve then
  Borrow, no longer net of the Buy.
- **Read** (`held_qty_expr`, behind every `_hold_query` reader: free stock on the board,
  the confirm guard, Stock Debt's pins): a line's stock holds together hold its ACTIVE
  decision's Reserve + Borrow (from `line_snapshots`, document Borrows left out) less
  delivered, never more than still owed, handed out across its rows in write order. Read off
  the decision, not the row, because a row written after a delivery is already net of it.
  A line with no active decision stating its components keeps `least(qty, still owed)`.

So the existing prod row (qty 336) reads 0 with no data change.

## Restated ACs

- AC-S2-4 (`fulfilment-board-plans-delivered-lines-acceptance-criteria.md`): Reserve 1 + Buy 2
  with 1 delivered holds 0 (was 1).
- OI-CLOSED-LINE AC-9 (`oi-closed-line-acceptance-criteria.md`): note added.

## Tests

`tests/scm/test_own_delivered_first.py` (credit order, write after delivery, the CB6633-NL
existing row on board free stock and Stock Debt pins, partial delivery credited once,
Reserve + Borrow rows together, the no-decision guard) and the restated AC-S2-4 test in
`tests/test_fulfilment_board.py`.
