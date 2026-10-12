# PLAN: packing-list header status rolls up from received lines

Status: in progress, small fix track (no migration, no auth/RBAC change, diff under ~300 lines)

## Problem

Owner report 12 Oct 2026: packing list TCLU8176397 (prod) shows every shipment line
Received (529/529 each, 1587 total) on the detail page, but the Packing Lists page still
shows the header as In Transit.

## Root cause

- The header is a stored column (`inbound_shipments.shipment_status`), rolled up from the
  STORED `inbound_shipment_lines.line_status` by `refresh_shipment_line_statuses` (every
  receipt path ends there) and re-derived from the same stored statuses by
  `list_shipments` on read.
- The detail page does not show the stored line status: `get_packing_list` recomputes it
  from the VISIBLE SPO allocation total (retired and never received allocations left out,
  `spo_supply.visible_line_clauses`).
- The stored status came from `compute_shipment_line_figures`, which summed every
  allocation on the shipment, retired ones included. One retired allocation AutoCount
  stopped naming doubles the product's allocated figure, so the stored line says
  `partially_received`, the header never rolls up and the list keeps In Transit.

## Fix

1. `compute_shipment_line_figures` judges the stored line status on the visible
   allocation total. `spo_allocated_quantity` stays the unfiltered figure its purchasing
   readers use (round 5, AC-H17 note in `packing_lists.get_packing_list`).
2. `rollup_shipment_status`, shared by the refresh write and the list read: every line
   received -> `fully_received`; any receipt -> `partially_received` (already in the
   `inbound_shipments_shipment_status_check` vocabulary); no receipt -> header kept, a
   stale receipt state falls back to `in_transit`; `closed` and `draft` never rolled.
3. Detail page header shows the list's status pill (`Badge status=...` +
   `formatStatusLabel`, `components/ui/badge.tsx`, `lib/status-badge.ts`).

## Readers of what changes (PLAN-hide-retired-spo-lines sections 11-12 asked for this)

The stored line STATUS now follows the visible allocation total; `received` keeps its
existing meaning ("the allocated part has arrived", `compute_inbound_shipment_line_status`),
applied to the allocation the document still names. The stored `spo_allocated_quantity`
is unchanged (unfiltered).

| Reader | Reads | Effect, and why it is the figure it wants |
|---|---|---|
| `refresh_shipment_line_statuses` header rollup, `list_shipments` read-time rollup, list Received/Open filters | line_status | The fix itself: header and list agree with the page the operator reads. |
| `open_shipment_ids` (nightly sweep, `--all-open`) | line_status | A line received against its visible allocation stops being re-swept; the sweep's job is lines whose stored figures can still be behind a receipt. |
| `coverage_service._CLOSED_SHIPMENT_LINE_STATUSES` / `_RECEIVED_SHIPMENT_STATUSES` (reorder cover) | line_status, header | A received line and a `fully_received` header stop counting as cover. Wanted for the TCLU shape (shipped equals visible allocation: nothing is left on the water). |
| `container_request_service.PL_NOT_ARRIVED_SQL`, `container_request_drill` | header | `partially_received` still counts as not arrived (as before); `fully_received` is reached when every line is received on the visible total. |
| `incoming_stock_service._still_incoming_filter` (chatbot / n8n incoming) | line_status | As coverage. |
| `PL_UNALLOCATED_SQL`, allocation suggestion | spo_allocated_quantity | Unchanged. |

Accepted edge (reviewer finding 2): a line shipped ABOVE its visible allocation whose
only other allocation is retired and never received reads `received` once the visible
allocation is received, so the unallocated rest leaves coverage and the incoming answer.
That is already how such a line behaves today when no retired allocation exists (shipped
1058, allocated 529, received 529 reads `received`). Q4 in the read-only SQL counts the
lines in scope (`over_shipped_in_scope` is this edge); the backfill dry run lists the
exact flips per line before anything is written.

Other notes from review:
- A header set to `arrived_at_port` / `at_warehouse` becomes `partially_received` once a
  receipt lands, and if that receipt is reversed it falls back to `in_transit`, not the
  earlier logistics state. No app code writes those two values today.
- The list API's `shipment_status` filter for values other than open/received/closed
  still matches the stored header, which the list may display rolled up (same drift that
  already existed for fully received). The FE has no such filter.
- The detail response still judges a line on the product's whole visible total, not the
  per-line apportioned share; a product on two lines can still differ. Follow-up.

## Existing stale rows (owner approval required, nothing runs on prod from this lane)

- Read-only counts: `documentation/plans/scm/pl-status-rollup-readonly.sql`
  (Q1 as asked, Q2 the TCLU shape, Q3 the TCLU8176397 diagnosis, Q4 the scope of the
  line-status change).
- Backfill, dry-run by default, idempotent:
  `python scripts/oneoff/refresh_container_received.py --company SRT --stale-headers`
  then the same with `--apply` once approved. A rerun after `--apply` reports
  `lines to change 0, headers to change 0`.
- Note: once deployed, the existing nightly open-container sweep
  (`shipping_order_rules.py`, `open_shipment_ids`) recomputes containers that still have a
  stored line not `received` with the new rule, so the TCLU shape heals on its own the
  first night. Containers whose stored lines are all `received` (Q1) are not in that
  sweep and need the backfill.

## Tests

`tests/test_packing_list_status_rollup.py` (owner case, partial, guards, backfill, the
owner SQL), `packing-list-record.test.tsx` (status pill).
