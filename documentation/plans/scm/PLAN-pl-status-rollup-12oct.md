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

## Existing stale rows (owner approval required, nothing runs on prod from this lane)

- Read-only counts: `documentation/plans/scm/pl-status-rollup-readonly.sql`
  (Q1 as asked, Q2 the TCLU shape, Q3 the TCLU8176397 diagnosis).
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
