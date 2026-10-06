# PLAN: AVAIL-ONHAND-BASIS - chatbot availability counts on hand only, outstanding SO ignored

Status: built, PR #1510 open (small fix track, size S; owner ruling 6 Oct 2026 21:25, no further alignment needed). Move this plan to `_archive/chatbot/` once #1510 merges.

## Ruling

Owner, verbatim intent: "our basis should be the on hand, without considering the outstanding,
no wording change, just the calculation behind the stock change, need to ignore the outstanding."

## Prod case

Availability-only contact, warehouse scope MOCHA-WH, asked "MWC7632-SH-P" then "10".

- Before: `MWC7632-SH-P x 10: ❌ No stock and no incoming. Please refer to your salesman.`
- Staff stock summary at the same data timestamp: Total 6 (O/S 13), MOCHA-WH 6 (O/S 13).
- After: `MWC7632-SH-P x 10: ✅ Got stock, 6 available.` (asked 5: `✅ Got stock.`).

## Where it was

`sorento_crm_backend/app/services/inventory_service.py`, `_apply_stock_visibility`, availability
mode: `net_available = on hand over the policy-allowed active warehouses - open SO` where open SO
was the sum of `qty_ordered - qty_delivered` over open lines in the same warehouse scope (its own
`_supply_scope`). 6 - 13 = -7, and `stock_ask_branch.branch()` reads anything below 1 as no
stock.

## Change

- The availability figure is `on_hand_total` alone. The inline open-SO read, `_supply_scope` and
  the subtraction are deleted from that path. Warehouse scope unchanged. Wording unchanged
  (`sorento_crm_mcp/sorento_crm_mcp/presenters.py`, `label_catalog.py` untouched).
- Every other reader of open SO is a different helper and keeps its rule:
  `StockService.open_so_qty_by_product` / `open_so_qty_by_product_warehouse` feed the staff
  stock summary's O/S and sellable (`app/api/v1/inventory/stock.py::_with_sellable`); the
  fulfilment / confirm flows and the SCM planning reads query `SalesOrderLine` themselves.
  None of them called the deleted code (it was a local query inside the availability block).

## Tests

- `tests/test_avail_onhand_basis.py` (red first on main, 6 of 9 failed): the prod case at
  ask 10 (`in_stock`, `available_qty` 6) and ask 5 (`in_stock`, no count); 0 on hand / 0 open
  SO stays `no_incoming`; the figure does not move with the open SO; the route carries it; the
  staff summary's O/S and sellable are unchanged on the detailed rows and the compact summary.
- Retired assertions: `test_avail_mode_partial_stock.py` (open SO no longer subtracted before
  the count; open SO at or over on hand is still `in_stock`), `test_stock_availability_block.py`
  AC-SA303 (not subtracted; the short count is the on-hand count).
- Spec: `chatbot-stock-ask-v2-24sep-acceptance-criteria.md` truth-table row 8 rewritten to the
  on-hand basis with the MWC7632-SH-P case; AC-SA303 re-ruled.
  `tests/chatbot/AVAIL-MODE-SCENARIOS.md` definition of Available updated.
