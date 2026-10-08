# PLAN: borrow = location only (BORROW-LOCATION-ONLY)

Status: Plan (behaviour card in progress; size L, LEAD pattern, cloud lane)

## Owner's ask (8 Oct)

Sorento does not borrow a specific SO's stock, only a location's. Turn off "borrow order"
(donor SO line pick + order-back debt to the donor). The replenishment of what was borrowed stays
tied to the ORIGINAL SO line, so OI shows the original line (its SO qty, its date) and PO/SPO
linking finds it. Existing borrow-order records need a guarded fix script (dry-run first).

## Findings

(in progress)
