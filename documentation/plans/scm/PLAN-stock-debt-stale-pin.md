# PLAN: Stock Debt stale pins never count as cover (STOCK-DEBT-STALE-PIN)

Status: in review. Track: small fix (no migration, no auth/RBAC, no new ingest). UAC:
`stock-debt-stale-pin-acceptance-criteria.md`.

## Journey

Owner, 14 Oct 2026, prod: Stock Debt > CB6633-NL > Oct 26. SO424555 line 16 (BRW-IB,
outstanding 126) read "Covered by: On hand BRW-IB", status Pinned, Short 0, while the
product page showed BRW-IB on hand 0 and the Supply tab said "Nothing arrives here / No
stock is held or on the way for this column."

Prod check (owner, 14 Oct 12:20): the 'own' allocation (saved 336, held 126) was confirmed
on 8 Oct, two days AFTER the stock row went to 0 (choosing the own location is allowed
whatever the stock). Eight live pins sit above free stock, about 406 units of shortfall
read as covered: SRTSH1040 SO402757 BRW-IB 150 / free 2; CB6633-NL SO424555 BRW-IB 126 / 0;
SRTWCX7604-S-RL-NEW-WEPLS SO382618 DC1-BB 100 / 21; SRT357 SO382618 BRW-BB 39 / 0; CSH2073
SO419595 BRW 8 and SO419598 BRW 2 / 0; SRT382-6-DIY SO421287 BRW-IR 3 / 0; CB2829-DIY
SO419598 BRW 1 / 0.

## Cause (origin/main df3eb9146)

- A pin is a confirmed `so_line_allocations` row (`ProjectSupplyService._hold_query`); its
  quantity is capped at the owed quantity (`held_qty_expr`), never at stock.
- `StockDebtService._supply` drops a bin whose on hand less reserved is <= 0, so the pin's
  `on_hand:<bin>` event is absent and `assign()` takes the stand-in branch
  (`supply_assignment.py`, AC-S2-1b, meant for bins OUTSIDE the read's span), which builds
  the whole 126 out of the pin itself.
- Received-SPO placements already skipped an empty in-span bin (SPO-RECEIVED-PIN);
  allocation pins did not.

## Fix

1. `_holds`: allocation pins walk the same rule. Bin in the span with no on-hand event: the
   hold is dropped (line short, no "Covered by"). Bin with an event: `assign()` already caps
   the pin at what the bin has left.
2. Bins OUTSIDE the span (site pool, unflagged bin, another group under `group=`): the
   stand-in is capped at that bin's real free stock (`_stand_in_floors`, one read, on hand
   less reserved), shared by every on-hand hold on the bin (allocation and received-SPO
   placement alike), nearer line first (allocation rows now ordered by due date, SO, line).
3. `cell()`: the Supply tab lists every source a line of the column is pinned on (R43 did
   this for PO pins only), stand-ins included, Free 0 outside its home column.
4. Dialog: the Supply empty state says "What covers these lines is listed in an earlier
   column." when a demand row in the column shows cover, never "No stock is held".

## Fulfilment Planning board (shares `_holds` through `planning_assignments`)

- The line a stale pin sat on reads short in the shared assignment instead of pinned.
- Rung 1 (`_drawn_at_own_date`) no longer offers the phantom on hand as stock the unit
  already has, so the ladder moves on to its later steps (borrow, buy) for it.
- Borrow (`_donor_on_hand_total`, `_eligible_donor`): such a line is no longer a donor and
  offers no phantom on hand to borrow.
- A pin on a partly stocked bin covers only what the bin holds; the rest is short.

## Not in scope

Releasing or editing stale allocation rows (data stays; the read stops trusting it), and
refusing a confirm at an empty bin (owner rule allows it).
