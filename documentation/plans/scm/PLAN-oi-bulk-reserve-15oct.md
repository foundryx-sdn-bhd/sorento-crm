# PLAN: one-button bulk reserve on the OI Lines tab (OI-BULK-RESERVE)

Status: Plan (owner OK on mock v3 15 Oct; crew added the line-level x; build after #1684 merges, on this branch with main merged in).
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
