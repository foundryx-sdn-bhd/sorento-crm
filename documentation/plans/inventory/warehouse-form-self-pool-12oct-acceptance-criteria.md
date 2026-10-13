# UAC: warehouse form keeps a self-pool

1. Editing a warehouse whose `pool_warehouse_id` equals its own id shows "Draws stock from"
   as "This warehouse (own pool)".
2. Saving that edit after changing only another field (e.g. the description) sends no
   `pool_warehouse_id` key; the warehouse stays its own pool.
3. The user can pick "This warehouse (own pool)" for a warehouse that has no pool, and the
   save sends `pool_warehouse_id` = its own id.
4. Clearing the field and saving still sends `pool_warehouse_id: null` (clear still works).
5. Backend: an update whose payload omits `pool_warehouse_id` keeps a self-pool; an
   update setting it to the warehouse's own id persists; an explicit null clears it.
