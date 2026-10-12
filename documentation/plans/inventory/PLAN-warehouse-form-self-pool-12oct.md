# PLAN: warehouse form keeps a self-pool

Status: in progress (small fix track: FE form + BE pin tests, no migration, no auth change)

## Defect

Inventory > Warehouses > edit, field "Draws stock from" (`pool_warehouse_id`):

- The options exclude the warehouse being edited (`WarehouseForm.tsx` `poolOptions`).
- A value equal to the warehouse's own id is reset to empty on load.
- Every save sends the whole form, so `pool_warehouse_id: null` goes out, and
  `WarehouseService.update_warehouse` writes every key it is sent.

A warehouse that is its own pool (BRW, HQ, seeded by `311_scm_purchasing_base.py`) loses
its pool status on any edit and save, dropping out of the Stock Debt span
(`procurement_service._planning_span_map`) and the pool chain.

## Fix

- FE: offer "This warehouse (own pool)" (value = own id) in edit mode, shown selected when
  `pool_warehouse_id == id`.
- FE: an edit sends only the fields the user changed (react-hook-form dirty fields), so an
  untouched field is never sent, null or otherwise.
- BE: `update_warehouse` already applies `model_dump(exclude_unset=True)`, so an omitted
  `pool_warehouse_id` is kept and only an explicit `null` clears it. Pinned by tests rather
  than rebuilt.

## Tests

See `warehouse-form-self-pool-12oct-acceptance-criteria.md`.
