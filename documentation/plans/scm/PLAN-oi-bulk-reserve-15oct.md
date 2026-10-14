# PLAN: one-button bulk reserve on the OI Lines tab (OI-BULK-RESERVE)

Status: Plan (card final 15 Oct; build after #1684 merges, on this branch with main merged in).
Size M per crew, frontend only (expected diff small, no migration, no auth/RBAC change, no new
endpoint). Lead pattern: tester red tests, coder, reviewer, browser pass on the changed screen.

UAC: `oi-bulk-reserve-15oct-acceptance-criteria.md` (alongside). Card:
`oi-bulk-reserve-behaviour-card.md` (final rules at the bottom). Mock v2 (superseded by the owner's
"just one reserve button"): `documentation/mockups/OI-BULK-RESERVE/index.html`.

## Journey

Owner 15 Oct: CS reserves line by line today (each line's "Reserve" / "Edit reserve" fills one
staged entry, then the header "Reserve (N)" commits). Owner wants: tick many lines, press one
Reserve button, confirm.

## Rules (card final)

1. Selection strip on the Lines tab: ONE button "Reserve (N)" at its right end, for holders of the
   reserve grant. N = ticked lines with an open request row (not yet answered). Greyed at 0.
2. Press: stage each of those lines at min(qty requested, what is left) and the request row's
   location (same entry the line's own "Reserve" button writes), replace any staged entry for it,
   clear the ticks, and open the #1684 confirmation (`ReserveConfirmDialog`) covering EVERY staged
   line (these plus any staged singly before). Cancel keeps the staged chips.
3. Partial reserve = single-line "Edit reserve"; amend = single-line "Amend reserve" (unchanged).
4. Header "Reserve (N)" unchanged (single-line stages, same confirmation).

## Code

- `app/(protected)/project-sales/order-inquiries/[id]/components/OrderInquiryLinesTab.tsx`: the
  toolbar's selection strip gets the right-aligned "Reserve (N)" button (via
  `DataGridListToolbar`'s `bulkActionsSlot`), shown only with the reserve grant; it calls a new
  `onBulkReserve(lineIds)` prop.
- `[id]/components/OrderInquiryDetail.tsx`: `bulkStage(lineIds)` builds the staged entries from the
  same source the per-line "Reserve" button uses (`openRequestForRow`, request row qty/location,
  remaining cap), merges them into `stagedByRowId`, clears `rowSelection`, opens the confirmation.
  The eligible count comes from the same helper so the label and the stage never disagree.
- No backend change: `commitOrderInquiryReserve` already takes many lines; recipients read already
  takes the staged row ids (#1684).

## Tests (tester first, red)

Frontend vitest beside the touched components (new `OrderInquiryDetail.bulkReserve.test.tsx`),
reusing the harness of `OrderInquiryDetail.reserveConfirm.test.tsx`. List in the UAC.

## Out of scope

- Bulk partial reserve or bulk amend (owner: single-line Edit / Amend).
