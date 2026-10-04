# UAC: SO-TRANSFERABLE

- AC-TR-1 An SO push with `transferable: "F"` stores `sales_orders.is_transferable = false`; `"T"` stores true; JSON `true`/`false` work the same.
- AC-TR-2 A re-push that omits `transferable` (or sends null) leaves the stored value unchanged.
- AC-TR-3 A value that is not a boolean word (e.g. `"X"`) fails that record; nothing is written.
- AC-TR-4 `POST /external/read/sales_orders` returns `transferable` (true / false / null).
- AC-TR-5 Rows that never stated it read `is_transferable = null` (migration adds a nullable column with no default; nothing is backfilled).
- AC-TR-6 Stock Debt list: an open SO line on an F order is not demand; the same line on a T or unknown order is. A product whose only demand is F and has no stock/supply gets no row.
- AC-TR-7 Stock Debt cell drill: F order lines are not listed in `demand`, and `demand_total_qty` excludes them.
- AC-TR-8 The shared assignment (`assignments_for`, and the board ladder's `planning_assignments`) carries no F line, so no stock is reserved for it.
- AC-TR-9 `GET /scm/sales-orders/{id}` returns `is_transferable`; `PUT /scm/sales-orders/{id}` cannot change it.
- AC-TR-10 SO list shows a Transferable column (Yes / No / Not stated) and a Transferable filter; SO detail General tab shows a read-only Transferable field in view and edit, marked "From AutoCount" only on an AutoCount order; usable at 1280px and 375px.
