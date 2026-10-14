# PLAN: consignment goods are delivered at issue (CONSIGN-DELIVERED-FIX)

Status: in review (small fix track, URGENT prod data fix; PR #1686)

## Problem

ss#125 (14 Oct, about 21:30) projected about 786 AutoCount CGs (CG, SRT, from 2026-01-01) as
consignment sales_orders with qty_delivered = ConsignmentDTL.TransferedQty. In AutoCount,
TransferedQty on a CG line is the qty already RETURNED (Consignment Return, incl. 'CVT TO DO'):
27,773 of 27,836 live CG lines have TransferedQty equal to their returns, none has
TransferedQty > 0 without a return. CG headers read "STOCK TRANSFER - BRW (DISP/BB) TO CON" and
the lines sit at Location CON, so the goods left the warehouse when the CG was issued. 696 CGs
(4,065 units over 928 items) became open demand: Fulfilment Planning, Stock Debt, the
outstanding report and the reorder run.

## Owner rulings (14 Oct, 22:00)

1. A CG means the goods are already at the customer: delivered = full qty, TransferedQty =
   returned.
2. The Consignment page shows "At customer" = sent less returned, with status "At customer" /
   "Fully returned" instead of "Outstanding".
3. No stopgap on the reorder run; PR tonight, red-first, reviewer + security, re-projection of
   the stored CGs with a read-only before/after SQL.

## Fix

- `app/services/autocount_consignment.py` `consignment_payload`: every line `qty_delivered =
  Qty`; `_status` is `cancelled` for a void / vanished CG, else `closed`. TransferedQty is no
  longer sent to the SO ingest.
- `recompute_returned`: `qty_returned` = max(matched live Consignment Return lines, the CG's own
  TransferedQty from its stored record), capped at ordered. The two are one fact pushed twice;
  the larger is the one that has landed. Non-finite values are ignored.
- `AutocountCancelIngestService._project_consignment` runs `recompute_returned` over the
  order's lines after every projection.
- Every open-demand reader filters `sales_orders.status = 'open'` and ordered less delivered
  > 0 (board `project_fulfilment_board_service.py`, `group_netting.py`, Stock Debt, outstanding
  report, `scm.committed_v` and `horizon_committed_select_sql`), so a closed, fully delivered
  consignment drops out of all of them with no reader change.
- Frontend: `scm/lib/consignmentStatus.ts`; Consignment list Status reads At customer / Fully
  returned (Cancelled stays), new "At customer" column; the consignment detail header badge uses
  the same wording.
- Contract note for `consignments` updated (same wire shape).

## Re-projection (deploy step)

A script, not a migration: it reuses the ingest's own projection and post-commit hooks, which a
SQL migration would bypass. Crew runs it right after the deploy:

```
venv/bin/python scripts/reproject_consignments.py --company SRT          # dry run, counts only
venv/bin/python scripts/reproject_consignments.py --company SRT --apply
```

Idempotent: a second `--apply` reports `changed: 0`. Read-only before/after counts:
`documentation/plans/autocount/SQL-consign-delivered-fix-14oct.sql`.

## Other TransferedQty readers checked

Cancel SO / Cancel PO matching is scoped to `doc_type = 'sales_order'`
(`autocount_cancel_ingest_service.py`), the sales report and achievement already exclude
consignments, and no other code reads a consignment line's TransferedQty.

## Tests

`tests/test_consignment_delivered_at_issue.py` (projection, returned qty, board predicate,
Stock Debt, outstanding report, reorder committed figure, re-projection script);
`tests/test_ingest_autocount_consignments.py` updated to the ruling. Vitest:
`ConsignmentsList.test.tsx`, `SalesOrderDetail.consignment.test.tsx`.
