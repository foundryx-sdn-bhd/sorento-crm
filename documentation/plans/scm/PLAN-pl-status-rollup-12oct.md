# PLAN: packing-list header status rolls up from received lines

Status: in progress (small fix track: no migration, no auth/RBAC change, expected diff under ~300 lines)

## Problem

Owner report 12 Oct 2026: packing list TCLU8176397 (prod) has every shipment line Received
(529/529 each, 1587 total) but the Packing Lists list page still shows the header as In Transit.

## Scope

1. Root-cause the header status (stored vs derived, which receive paths touch it).
2. Roll the header up to Received when every line is received (red-first test).
3. Read-only count SQL + idempotent dry-run-by-default backfill for stale headers.
4. Detail page header shows the same status badge as the list page.

Findings and file:line citations follow in the crew-note on the PR.
