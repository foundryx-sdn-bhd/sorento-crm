# PLAN: stock reply Total O/S respects warehouse visibility (STOCK-TOTAL-OS-SCOPE)

Status: in progress (small fix track: no migration, no RBAC change, no new ingest surface)

Owner decision 2 Oct 2026: option (a), revised the same day to option (b) (no Unassigned line).

## Problem

The chatbot stock reply's Total line prints `(O/S: n)` from `open_so_qty_by_product`
(`app/services/inventory_service.py`), which sums every open SO line of the product with no
warehouse filter. A contact whose policy hides a warehouse is therefore told that warehouse's open
demand inside the Total (prod: MWC7624-RL-S10 `Total 54 (O/S: 531)`, `BRW 0 (O/S 0)`,
`MWH 54 (O/S 0)`).

## Rule

- Total O/S = sum of open SO qty on warehouses the contact may see: the same
  `warehouse_criterion(policy, ...)`, active-warehouse filter and question warehouse
  narrowing (`warehouse_ids` / `warehouse_id`) the location lines use.
- Open SO lines with no warehouse are not shown anywhere in this reply: not in Total O/S and
  not on a line of their own (option b).
- Open SO on a hidden warehouse is never shown or hinted.
- No `contact_id` (staff grid, no policy): unchanged; the summary keeps the product total.
- Company scope: the open-SO aggregates are already scoped by the session `do_orm_execute`
  listener (`SalesOrderLine` is `CompanyScopedMixin`; the compiled SQL carries
  `sales_order_lines.company_id IN (...)`). Guarded by a test, no code change.
- Detailed mode under a policy: the per-product summary (`total_on_hand`, `open_so_qty`) is
  narrowed the same way; it was summing hidden warehouses. The presenter does not render that
  summary, so the reply text is unchanged there.

## UAC

1. Policy includes only W1; open SO 10 on W1, 7 on hidden W2, 3 with no warehouse:
   Total O/S 10, nothing reads 3, 7, 13, 17 or 20.
2. No `Unassigned O/S` line in any reply.
3. Staff call (no contact): Total O/S unchanged (all lines).
4. Open SO of another company is not counted when the request is company scoped.
5. A warehouse named in the question narrows Total O/S the same way it narrows the lines.
