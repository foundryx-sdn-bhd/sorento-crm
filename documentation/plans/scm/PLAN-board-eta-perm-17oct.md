# PLAN: fulfilment board ETA respects view_clearance

Status: implemented, in review (small fix track: backend only, no migration, no new permission)

## Journey

Owner ruling 17 Oct ("depends on the field allowed"): the delayed ETA (`eta_delay_date`)
shows only to a user who holds `procurement.packing_lists.view_clearance`. Everyone else
sees `estimated_arrival_date`. Before this lane only `/incoming-stock` enforced it; the
fulfilment board printed the effective ETA (`eta_delay_date or estimated_arrival_date`) to
everyone.

Crew ruling (b), 17 Oct: the engine plans on the real (delayed) ETA for EVERY viewer, so a
board suggestion is always what a Save commits. Only the dates PRINTED to a viewer without
`view_clearance` switch to the published ETA.

## Acceptance criteria

- AC1: a board viewer WITH `view_clearance` sees an incoming SPO line dated by
  `eta_delay_date` when the shipment carries one.
- AC2: a board viewer WITHOUT it sees the same line dated by `estimated_arrival_date`
  (falling back to the SPO line's `expected_date`): in incoming lists, the stock drawer,
  reason and trail sentences, and overdue counts. The delay date value appears nowhere in
  the board or stock-detail response.
- AC3: both viewers get the same proposal (the engine plans on the delay).
- AC4: no UI change beyond which date shows.

Accepted hints (ruling (b)): a verdict worked on the real date can show that something is
late without its date (a line bought although the printed ETA is before the required date;
incoming lists ordered by the real date; the drawer's counted/assumed flag).

## Mechanism

`spo_supply.MaskedEta`: compares as the delay, prints as the published ETA.
`ProjectSupplyService(reveal_eta_delay=False)` emits it; only the board and stock-detail
read routes pass False (`field_access.staff_may_see_clearance`). `FulfilmentBoardService`
unwraps it on the way out.

## Out of scope

The stock panel (PR #1752 STOCK-STATUS). Other effective-ETA call sites are listed in the PR.
