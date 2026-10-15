# PLAN: one-button bulk reserve on the OI Lines tab (OI-BULK-RESERVE)

Status: Build. Part 1 (v3, inside one OI) built and green on `crew/oi-bulk-reserve` (PR #1713), in review + cloud browser pass. Part 2 (v4, cross-OI Lines page) mock committed, crew decisions in, waiting on the owner's OK on the mock; backend red test committed. Track: standard (M), no migration, no auth/RBAC change.
Size M per crew, frontend only (expected diff small, no migration, no auth/RBAC change, no new
endpoint). Lead pattern: tester red tests, coder, reviewer, browser pass on the changed screen.

UAC: `oi-bulk-reserve-15oct-acceptance-criteria.md` (alongside). Card:
`oi-bulk-reserve-behaviour-card.md` (final rules at the bottom). Mock v2 (superseded by the owner's
"just one reserve button"): `documentation/mockups/OI-BULK-RESERVE/index.html`.

## Journey

Owner 15 Oct: CS reserves line by line today (each line's "Reserve" / "Edit reserve" fills one
staged entry, then the header "Reserve (N)" commits). Owner wants: tick many lines, press one
Reserve button, confirm.

## Rules (card final v3)

1. Selection strip: ONE "Decide (N)" button at its right end, reserve grant only; N = ticked lines
   with an open request row; greyed at 0.
2. Menu "Reserve as requested": stage each counted line exactly as its tick does (min(requested,
   remaining) at the request row's location), replace any staged entry, clear the ticks.
3. Menu "Reject...": lightbox "Reject N lines", one required Reason, "Stage N lines" stages
   Reserve 0 + reason on each counted line, clears the ticks.
4. A requested line gets an x "Reject" beside tick and pen: same one-reason prompt, stages Reserve 0 +
   reason (crew decision, mirrors bulk Reject).
5. Amend and partial reserve stay per line (pen). Header "Reserve (N)" + #1684 confirmation commit
   as today. Purchasing hears a reject as today's Reserve 0 notice (sent or parked).

## Code

- `app/(protected)/project-sales/order-inquiries/[id]/components/OrderInquiryLinesTab.tsx`: the
  toolbar's selection strip gets the right-aligned "Decide (N)" button + DropdownMenu (via
  `DataGridListToolbar`'s `bulkActionsSlot`, pattern `BoardDecideControl.tsx`), reserve grant only;
  it calls new props `onBulkReserveAsRequested(lineIds)` / `onBulkReject(lineIds, reason)`.
- New `[id]/components/BulkRejectReserveDialog.tsx`: "Reject N lines" (N = 1 from the line x), one
  required Reason; used by both the strip menu and the line x.
- `[id]/components/orderInquiryHeaderLinesColumns.tsx` `ReserveActionsCell` (~253-281): add the x
  "Reject" button (`onRejectReserve(row)`) between the tick and the pen for `reserve_state ===
  'requested'`.
- `[id]/components/OrderInquiryDetail.tsx`: `bulkStage(lineIds, mode)` builds the staged entries
  from the same source the per-line tick uses (`onTickReserve` path: open request row, qty capped at
  remaining, location) or Reserve 0 + reason, merges into `stagedByRowId`, clears `rowSelection`.
  The eligible count comes from the same helper so the label and the stage never disagree.
- No backend change: `commitOrderInquiryReserve` already takes many lines; recipients read already
  takes the staged row ids (#1684).

## Tests (tester first, red)

Frontend vitest beside the touched components (new `OrderInquiryDetail.bulkReserve.test.tsx`),
reusing the harness of `OrderInquiryDetail.reserveConfirm.test.tsx`. List in the UAC.

## Out of scope

- Bulk partial reserve or bulk amend (owner: single-line pen).

## Part 2: reserve from the cross-OI Lines page (mock v4)

Owner 15 Oct night: the stock person reserves ~100 OIs from Order inquiries > Lines without
opening each one. Mock: `documentation/mockups/OI-BULK-RESERVE/v4-lines-page.html`.

Crew decisions (15 Oct): (1) commit = one existing `POST /order-inquiries/{id}/reserve-commit`
per OI, a few at a time, a result row per OI, never a stuck dialog (the route is one transaction
per OI, `order_inquiries.py` 1300-1310; omitting `warehouse_id` defaults to the request row's
location, service 858). (2) Reserve filter = the Documents view's four statuses and predicate
(`orderInquiryHeaderStatus.ts` 25-41, `reserve_status_predicates()` service 272-283), applied
per ROW. (3) No staging on the Lines page: Decide goes straight to one confirmation listing OIs
with Reserve / Reject counts and "Park notice first". (4) "Send all parked reserves to purchasing
(N)" also in the Lines view's Actions menu. (5) The confirmation is a multi-OI mode INSIDE
`ReserveConfirmDialog`, no new component file; Decide and Reject reuse part 1's
`ReserveDecideControl` / `BulkRejectReserveDialog`.

Code: backend `reserve` query param on `GET /order-inquiries` and its summary
(`_worklist_filters`, `OrderInquiryWorklistService._base`); frontend `OrderInquiriesClient.tsx`
(filter in the Filters popover, ticking for the reserve holder at ~929, Decide as the toolbar's
primary beside Confirm, Send all parked in Actions), `ReserveConfirmDialog.tsx` multi-OI mode.
Tests: `tests/test_oi_bulk_reserve.py` (red, committed), vitest beside `OrderInquiriesClient`.
