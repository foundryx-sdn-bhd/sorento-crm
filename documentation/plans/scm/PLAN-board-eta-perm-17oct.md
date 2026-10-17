# PLAN: fulfilment board ETA respects view_clearance

Status: in progress (small fix track: backend only, no migration, no new permission)

## Journey

Owner ruling 17 Oct ("depends on the field allowed"): the delayed ETA (`eta_delay_date`)
shows only to a user who holds `procurement.packing_lists.view_clearance`. Everyone else
sees `estimated_arrival_date`. Today only `/incoming-stock` enforces it; the fulfilment
board prints the effective ETA (`eta_delay_date or estimated_arrival_date`) to everyone.

## Acceptance criteria

- AC1: a board viewer WITH `view_clearance` sees an incoming SPO line dated by
  `eta_delay_date` when the shipment carries one.
- AC2: a board viewer WITHOUT it sees the same line dated by `estimated_arrival_date`
  (falling back to the SPO line's `expected_date`), and the delay date appears nowhere
  in the board response.
- AC3: no UI change beyond which date shows.

## Out of scope

The stock panel (PR #1752 STOCK-STATUS). Other effective-ETA call sites are listed in the PR.
