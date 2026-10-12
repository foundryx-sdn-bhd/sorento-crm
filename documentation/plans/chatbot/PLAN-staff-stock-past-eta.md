# PLAN: staff stock reply shows past ETAs and a duplicate PO block

Status: in progress (small fix track: expected diff under 300 lines, no migration, no auth/RBAC change)

## Problem

Crew live check on main (d01ca05cb): a STAFF (office) contact asking "stock SRTWC286" through
`crm_inventory_stock_balance_list` is told ETAs already in the past (2026-09-08, 2026-09-20,
delay 2026-10-05) and the SRTWC286-SH-PP purchase-order block is printed twice.

#1601 added a past-ETA rule only on the dealer incoming read
(`StockService._shipments_from_today`, `incoming_stock_service.earliest_packing_list_shipment`
`not_before`): the revised `eta_delay_date` first, never a past date.

## Fix

1. Apply the same rule to the staff stock reply, reusing the same helper (no second rule).
2. Fix the duplicate PO block.
3. Evidence on whether both pre-date #1601.

Owner rules: truthful, never a past date shown as future, one fact per line.

## Tests (red first)

To be listed once the staff path is traced.
