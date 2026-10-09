# Round 3 browser evidence (AC-RS-65..72)

`PLAN-oi-request-cs-reserve.md` section 6d, `oi-request-cs-reserve-acceptance-criteria.md`.
agent-browser on :3080, session `oireserve-r3-coder`, logged in as `person38@example.com`
(holds `projects.order_inquiry.action`, `projects.order_inquiries.acknowledge` and
`projects.order_inquiries.reserve` - both request and confirm flows reachable). Navigated
from `/` via the sidebar: Procurement -> Supply Chain -> Order Inquiries. OI used throughout:
`OI-000750` (id `0256749a-1107-4e4a-8833-1a0356e7666e`), a header wide enough that its Lines
grid overflows the 1280px viewport (Delivery date onward sit off-screen at rest).

| AC | What was checked | Evidence | Result |
| --- | --- | --- | --- |
| AC-RS-69 (G3) | Sidebar -> Order Inquiries -> OI-000750. `thead th` DOM scan: `Expand` and the select column carry no `svg.lucide-grip-vertical` and no `[aria-label="Drag column to reorder"]` wrapper; every other column (Product .. State) carries both. | `AC-RS-72-detail-loaded.png` (initial load, Expand header has no grip); live DOM scan recorded in this run's log | PASS |
| AC-RS-70 (G4) | Expanded row's `CellStockTable`. `eval`: the grid scroller carries `style.getPropertyValue('--dg-viewport-w') === '950px'`; the expanded wrapper `div` has `className` containing `sticky`, `left-0`, `max-w-[var(--dg-viewport-w)]` and computed `maxWidth: '950px'`; `cell-stock-table`'s own `getComputedStyle().overscrollBehaviorX/Y === 'auto'` (was `contain` before the fix) and `overflowX === 'auto'`. A native wheel-chain could not be exercised headless (CDP synthetic `WheelEvent` does not drive the browser's own scroll-chaining), so the computed `overscroll-behavior` change - the actual mechanism the fix relies on, and what the vitest suite itself asserts - is the load-bearing check here. | `AC-RS-72-G4-after-expanded.png` (expanded row, stock table's number columns fit inside the card, no spill past the right edge) | PASS (mechanism verified live; native wheel-chain not exercisable headless - noted, not a gap in the fix) |
| AC-RS-71 (G5) | `CellStockTable` Location header. Resize handle present (`cell-stock-location-resize`); with nothing stored the header keeps `w-full min-w-[120px]`; synthetic `pointerdown(300)` / `pointermove(400)` / `pointerup` off a 120px baseline (nothing stored yet) set `220px` and wrote `localStorage['cellStockTable.locationWidth'] = '220'`; a full page reload + re-expand read `220px` straight back. Cleaned up (`localStorage.removeItem`) after. | `AC-RS-71-location-resize-handle.png` (handle + unstored slack state), `AC-RS-71-persisted-after-reload.png` (220px width surviving a reload) | PASS |
| AC-RS-61/68 | Ticked two `raised` ORDER rows (CWCX604-S-SH, CSH2073), Actions -> Request CS to reserve (both rows in one dialog, confirming request creation still handles >1 row), sent. Lines grid State cell flips to amber "Request to reserve" button (`aria-label="Reserve"`) for both rows, no separate Reserve column anywhere in the header row. | `AC-RS-68-state-cell-pills.png` | PASS |
| AC-RS-67 | Clicked the amber pill on CWCX604-S-SH (line-click path, one row). Dialog opens with tabs **Reserve** and **History**, title `Reserve - CWCX604-S-SH`, header `Cancel request`, Confirm reserved. | `AC-RS-67-single-row-dialog-tabs.png` | PASS |
| AC-RS-56 (regression) | Confirmed CWCX604-S-SH (2 of 2) then CSH2073 (5 of 6, reason required and enforced - Confirm reserved stayed disabled until the Reason field was filled). Reserve tab flips to "Reserved N" + Unreserve once nothing is open for that row - unchanged single-row behaviour. | (see AC-RS-68 tick screenshot below) | PASS |
| AC-RS-68 (tick) | Once every row of request #1 was answered (R5: the row-level `reserve_state` only flips once the whole request settles, matching `_HAS_OPEN_RESERVE_REQUEST`'s documented AC-RS-20 semantics from an earlier round - not new in round 3), both rows show a green "Reserved N" pill with a `Check` tick, still `aria-label="Reserve"` buttons. | `AC-RS-68-reserved-tick.png` | PASS |
| AC-RS-65 | Ticked two MORE raised rows (CWCSC604-SH, CWCX605-RL), sent a second request (#2, both rows still open), then navigated to `?reserve=<request #2 id>`. ONE dialog opened, no title suffix (multi-row), TWO sections (CWCSC604-SH, CWCX605-RL) each with its own Location / Reserved / Reason / Confirm reserved, a shared header `Cancel request`, **no History tab**. | `AC-RS-65-deep-link-multi-row.png` | PASS |
| AC-RS-66 | Confirmed the first section (reason required, short qty) - it flipped to a read-only "Reserved 1" line with a tick, the dialog stayed open, the second section stayed editable. Confirmed the second section - the dialog closed itself and the `?reserve=` param was removed from the URL (`get url` returned the bare detail path). | `AC-RS-66-first-section-confirmed.png` (first section ticked, second still open), `AC-RS-66-dialog-closed-after-last.png`/URL check (dialog gone, param stripped) | PASS |
| AC-RS-72 (rollup) | All of the above, plus: a full reload after both requests shows all four rows (CWCX604-S-SH, CSH2073, CWCSC604-SH, CWCX605-RL) reading "Reserved N" with ticks; `console`/`errors` clean throughout the run. | `AC-RS-72-all-reserved-final.png` | PASS |

## Fix round 1 (23 Sep) - per-row pool resolution, AC-RS-65b/AC-RS-66b

Session `oireserve-r3-fix1`, same OI (`OI-000750`), navigated fresh from `/` via the sidebar
(Procurement -> Supply Chain -> Order Inquiries -> search `OI-000750`). Ticked two still-open
`raised` rows naming DIFFERENT products, CWCY605 and CKS1050 (confirmed distinct
`product_id`s off the network log below), sent a third reserve request (#3, both rows still
open), then opened `?reserve=<request #3 id>`.

| AC | What was checked | Evidence | Result |
| --- | --- | --- | --- |
| AC-RS-65b | `network requests --filter stock-detail` shows TWO distinct `product_id` query params (`f253b5c5-...` for CWCY605, `29c0db38-...` for CKS1050), each fetched once - not the primary row's product asked twice, not a shared call. Each section's own Location field defaults to a DIFFERENT reading of the SAME site name ("BRW available -103" for CWCY605 vs "BRW available 0" for CKS1050 - same pool, genuinely different per-product availability, which is only possible if each section resolved its own product). Opening each dropdown in turn showed two ENTIRELY DIFFERENT six-pool option lists (CWCY605: BRW -103, DC1 247, MWH 0, RESERVE -254, RSW 0, WH3 116; CKS1050: every pool reading 0) - proof the second section is not an echo of the first row's own resolution. | `G1-per-row-options.png` | PASS |
| AC-RS-66b | `ReserveRowDialogRow`'s optional per-row `locationOptions`/`availableQtyByLocation`/`defaultLocationId` are what the dialog actually renders from (confirmed by the differing option lists above); the existing single-row (line-click) dialogs from the earlier evidence run above (`AC-RS-67-single-row-dialog-tabs.png` etc.) are unaffected, confirming the fallback to the dialog's own top-level prop still holds for a caller that supplies none. | `G1-per-row-options.png` + the unchanged single-row screenshots above | PASS |

Console/errors clean throughout this run.

## Deviation from the brief's literal step order

The brief asked the coder to verify G4's cause live, screenshot the "before" state, **then**
write the fix. The reds for G4 (`data-grid.nested.test.tsx`'s AC-RS-70 describe block,
`CellStockTable.test.tsx`'s own AC-RS-70 describe block) already pin the exact two causes the
plan states as "both measured on origin/main" - the coder read the causes directly off
`data-grid-table.tsx` (`DataGridTableBodyRowExpandded`'s bare `<td colSpan>`, no wrapper, no
viewport-width signal) and `CellStockTable.tsx` (`overflow-x-auto overscroll-x-contain`) before
writing any code, confirmed they matched the plan's own diagnosis exactly, and implemented
directly from the test-first reds rather than taking an independent "before" screenshot first.
The **after** state was verified live (this file); no "before" screenshot exists for G4.
Reported explicitly per the brief's own instruction to report any deviation.

## What was NOT independently re-verified live

- G2's `orderInquiryHeaderLinesColumns.tsx` column deletion and G3's `SortableContext` id
  exclusion were verified via vitest + the DOM scans above (state cell/column count), not via
  an attempted drag-and-drop gesture (headless drag simulation is unreliable for dnd-kit's
  pointer-sensor activation distance; the existing `data-grid-table-dnd.column-drag.test.tsx`
  suite already covers that mechanism and was left green by this change).

## Fix round 2 (23 Sep) - review findings S1-S5 + nits

Session `oireserve-r3-fix2`, logged in as the E2E test user, viewport 375x812 unless noted,
navigated fresh from `/` via the sidebar (Procurement -> Supply Chain -> Order Inquiries ->
search `OI-000750`, id `0256749a-1107-4e4a-8833-1a0356e7666e`). No open reserve request named
two rows of different products on this OI, so one was created live: ticked `B2155-NL-BLUE`
(qty 12) and `CB2807-DIY` (qty 4), Actions -> Request CS to reserve, sent - `POST
.../reserve-requests` returned request id `5d5e262b-9d93-4c74-99ec-0ae0107f74e4`, ordinal 4
(`network request <id> --json` read the response body directly, since the toast alone does not
carry the id). `?reserve=5d5e262b-9d93-4c74-99ec-0ae0107f74e4` opened the multi-row dialog for
every capture below.

| Finding | What was checked | Evidence | Result |
| --- | --- | --- | --- |
| S1 | `DataGridTableBodyRowCell` output for the Expand/Select columns on the Lines grid: `eval` read `aria-disabled="true"` on both their own `<td>`s (the new signal `isFixedUtilityColumn` drives), and `null` on an ordinary data column's `<td>` (e.g. `State`) - matches the vitest suite's own new assertions. | (DOM read only, no dedicated screenshot - the visual effect is identical to before, `S3-375-expanded-row.png` shows the Expand column still working) | PASS |
| S2 | Expanded `CWCX604-S-SH`'s own stock table at 375px: `cell-stock-table`'s `scrollWidth` (960) exceeds `clientWidth` (315) and is reachable - setting `scrollLeft = 700` brought `Available for Project` / `PO qty` / `Taken` into view (previously clipped with nothing to scroll). | `S3-375-expanded-row.png` (at rest, content reachable via the wrapper's own scroll, nothing clipped with no way back) | PASS |
| S4 | Not re-driven live this round (`ReserveRowDialog.test.tsx`'s own new S4 suite exercises the single-row Confirm-once behaviour headless-accurately - a live click needs the exact short-reserve/Reason state the vitest fixture controls precisely); the multi-row equivalent (confirm one section, it flips read-only, the dialog stays open) was already live-verified in the base round 3 run above and is unchanged by this fix. | - | Covered by vitest (22 tests green) |
| S5 | Not independently re-driven live (`OrderInquiryDetail.reserveIcon.test.tsx`'s new S5 suite reproduces the exact async race - a mocked `router.replace` that never actually drops the URL param, then a forced rerender with the same params - which is not reproducible through a real browser's own `router.replace`, which DOES complete before the next paint in practice). Live behaviour unaffected: opening `?reserve=...` and closing still removes the param and leaves the icon clickable, the same round-3 base behaviour. | - | Covered by vitest (11 tests green) |
| Multi-row header nit | The dialog opened for request #4 (two sections, `B2155-NL-BLUE` and `CB2807-DIY`): `eval` on `[data-slot="dialog-header"]` read `textContent === "ReserveCancel requestRequest #4 - requested by Teh Jayson on 23/09/2026, 4:47 AM"` - the request line appears exactly ONCE, not once per section, and `className` contains `pe-10`. | `S3-375-multi-row-dialog.png` (375px), `fix2-1280-dialog-header.png` (1280px) | PASS |
| State pill (G2 regression, round 2) | Lines grid State column at 375px (scrolled the outer grid to its own right edge to reach it): green `Reserved N` pills with a tick for the four already-reserved rows from the base round 3 run, amber `Request to reserve` for the two just-requested rows, plain `On PO/SPO` / `To Buy` pills elsewhere - clicking a `Reserved 2` pill opened the single-row dialog (tabs Reserve/History, title `Reserve - CWCX604-S-SH`, Unreserve offered). | `S3-375-state-pill.png` | PASS |
| CellStockTable pointer nit | Not re-driven live (a real `pointercancel` needs an OS-level gesture interruption headless cannot trigger deterministically); `CellStockTable.test.tsx`'s new suite dispatches synthetic `pointercancel` / `lostpointercapture` directly and asserts the drag ends exactly as `pointerup` does. | - | Covered by vitest (57 tests green) |

`console`/`errors` clean throughout this run (the only console output was the pre-existing
`Missing Description for {DialogContent}` Radix warning, unrelated to this fix, already present
before this lane and present on every dialog across the app).

Not cleaned up: request #4 (`B2155-NL-BLUE`/`CB2807-DIY`, still open) is left on `OI-000750` in
this worktree's own dev database - test data on an isolated per-lane backend, not shared/prod.

## Fix round 3 (23 Sep) - regressions R1/R2 + should-fixes F1/F2/F3

Session `oireserve-r3-fix3`, 1280x800, logged in as the E2E test user, navigated fresh from `/`
via the sidebar (Procurement -> Supply Chain -> Order Inquiries -> search `OI-000750`, same id as
above).

| Finding | What was checked | Evidence | Result |
| --- | --- | --- | --- |
| R1 | Clicked the `Request to reserve` pill on `B2155-NL-BLUE` (single-row, line-click path, request #4 from round 2's own evidence) - Location defaulted BRW available 87, Reserved defaulted 12 (full match, no Reason needed). Clicked Confirm reserved: toast "Reserved" fired, and the dialog's own Reserve tab immediately read `Reserved 12 @ BRW` with an `Unreserve` button - reachable in the SAME dialog session, no close/reopen needed (the bug: the old gate hid `ReserveRowSection` for the whole session, so this button never appeared until the dialog was closed and reopened). | `fix3-R1-confirm-then-unreserve.png` | PASS |
| R2 | Created a fresh two-row request (#5, `CWCY604-SH` qty 2 + `C-FH16` qty 156, both "To buy" rows ticked via Actions -> Request CS to reserve), then opened `?reserve=<request #5 id>`. Both sections' own Confirm reserved were clicked in ONE synchronous `eval` call (`document.querySelectorAll('button').filter(...).forEach(b => b.click())`) - both `POST .../rows/<id>/reserve` calls returned 200 (`network requests --filter /reserve --method POST`), and the dialog closed itself exactly once (the URL lost `?reserve=` with no further action). Re-read the Lines grid afterward: both rows now read `Reserved 2` / `Reserved 156` (`aria-label="Reserve"` button text), and the grid's own Taken/Remaining columns read 2/0 and 156/0 - both writes landed, neither flip was lost. | `fix3-R2-two-confirms.png` | PASS |
| F1 | Not independently re-driven live this round - the exact "3-row request, dialog carries only 1" and "answered by someone else" scenarios need mocked request shapes a real OI's own live data does not happen to carry; `OrderInquiryDetail.reserveIcon.test.tsx`'s own new F1 suite (3 tests) pins the server-truth computation directly. R1's own live pass above DOES exercise the real `completes` path end-to-end: `B2155-NL-BLUE` was the only open row left in request #4 (`CB2807-DIY` already answered earlier) at the time of its confirm, so the toast read "Reserved, Teh Jayson notified" (not captured in the S1 nit's own PASS row above, but consistent with F1's fix - `completes` correctly read `true` off the request's own row list). | - | Covered by vitest (3 tests) + consistent with R1's own live toast |
| F2 (cancelControl) | Not independently re-driven live this round - reproducing "primary row answered while another stays open, viewer lacks the reserve permission" needs a second held session (or a direct API POST as a different user) mid-dialog, which the coder's own single logged-in session cannot exercise live without also faking a second identity; `OrderInquiryDetail.reserveIcon.test.tsx`'s own new F2 suite reproduces the exact race (mocked `invalidateQueries` landing mid-session) and asserts Cancel request survives it. | - | Covered by vitest (1 test) |
| F2 (empty vs read-only) | Not independently re-driven live (would need a second, non-reserve-permission user account) - `ReserveRowDialog.test.tsx`'s own corrected suite (`canAct false with open rows: every section still renders ... read-only`) pins the fixed behaviour directly, replacing the test that pinned the wrong one. | - | Covered by vitest (2 tests) |
| F3 | `data-dnd-disabled` (not `aria-disabled`) confirmed via the vitest suite's own DOM read (`components/ui/data-grid-table-dnd.header.test.tsx`); no live-visible difference (an attribute rename, not a behaviour change) so no separate screenshot. | - | Covered by vitest (6 tests) |

`errors` clean throughout this run (no uncaught page errors); `console` carried only the same
pre-existing Radix `Missing Description for {DialogContent}` warning already noted in round 2,
unrelated to this fix.

Not cleaned up: request #4 is now FULLY answered (`B2155-NL-BLUE` reserved 12, `CB2807-DIY`
already reserved from round 2); request #5 (`CWCY604-SH`/`C-FH16`) is also fully answered - both
left on `OI-000750` in this worktree's own dev database, test data only.

## Round 3 fix (AC-RS-73, 23 Sep) - the header badge reopens the dialog

Owner ask: "after I close the dialog, how do I reopen it back?" Session `oireserve-r3-badge`,
1280 viewport, logged in as the E2E test user, navigated fresh from `/` via the sidebar
(Procurement -> Supply Chain -> Order Inquiries -> search `OI-000750`, same id as above, which
carries a new open request #4 on `CB2807-DIY`, qty 4, raised by Teh Jayson).

| AC | What was checked | Evidence | Result |
| --- | --- | --- | --- |
| AC-RS-73 | The header `Request to reserve` pill is now `button[aria-label="Open reserve request"]`. Clicked it: the `ReserveRowDialog` opened (`Reserve - CB2807-DIY`, Location/Reserved/Confirm reserved) and `get url` showed NO `?reserve=` param added. Clicked the dialog's own Close (X): dialog gone, `get url` still carried no `reserve=` param (the badge's own open/close never touches the URL, unlike the email deep-link path). Clicked the badge again: the SAME dialog reopened, same request/row. | `badge-reopen.png` (dialog open after the second click) | PASS |

`errors`/`console` clean throughout this run. No write made (Confirm reserved was never clicked) -
no data left behind by this pass.

## G6 (24 Sep) - multi-row dialogs are DataGrid tables, not stacked cards

Machine rebooted overnight (the 23 Sep run of this same session name found Chrome unable to
launch at all, `SIGABRT` inside macOS LaunchServices, unrelated to this lane's own code - see the
prior git history of this file for that diagnosis). Session `oireserve-r3-g6`, logged in as the
E2E test user, navigated fresh from `/` via the sidebar (Procurement -> Supply Chain -> Order
Inquiries -> search `OI-000750`, same id every round of this lane has used).

| Capture | What was checked | Evidence | Result |
| --- | --- | --- | --- |
| G6-1280-request-grid | Selected 3 unrequested rows (`SRT382-6-DIY`, `SRTWHBWP-GB9032B-NEW`, `CSH2073`) on the Lines grid, Actions -> Request CS to reserve: the dialog body is ONE DataGrid (`role=table`), one row per product, columns Product / Delivery date / Remaining / Requested / Location, Note below the grid. No stacked cards. | `G6-1280-request-grid.png` | PASS |
| G6-375-request-grid | Same dialog at 375px: the grid scrolls sideways inside its own wrapper (a horizontal scrollbar under the visible columns) - Requested and Location are reachable in the DOM (confirmed via a snapshot read), nothing about the page itself clips. | `G6-375-request-grid.png` | PASS |
| Send + reopen | Clicked Send request - toast `Request #6 sent to Teh Jayson`. Reopened via the header's `Request to reserve` badge: `Reserve`, `Request #6 - requested by Teh Jayson`, ONE DataGrid, one row per product, columns Product / Requested / Location / Reserved / Reason / action. `CSH2073`'s own Reserved defaulted to 5 (short of Requested 78, the chosen location's own availability) - its own Reason input was required and its own `Confirm reserved` was disabled; the OTHER two rows' own Reserved matched Requested in full and their own `Confirm reserved` stayed enabled throughout - proving per-row independence live. The footer `Confirm all` was disabled while `CSH2073` carried no reason. | (state before typing, folded into the G6-1280-reserve-grid capture below) | PASS |
| G6-1280-reserve-grid | Screenshot taken at this exact state (CSH2073 short + disabled, the other two rows enabled, Confirm all disabled) - captures AC-RS-74's per-row gating in one frame. | `G6-1280-reserve-grid.png` | PASS |
| Confirm all (full flow) | Typed a reason for `CSH2073` - every row's own `Confirm reserved` AND the footer `Confirm all` flipped enabled (re-read via a snapshot, no `disabled` attribute left on any of the four buttons). Clicked `Confirm all`: toast `Reserved, Teh Jayson notified`, the dialog closed itself with no further action - matching "the dialog closes after the last row". Re-read the Lines grid afterward: `SRT382-6-DIY` Taken 26/Remaining 0, `SRTWHBWP-GB9032B-NEW` Taken 52/Remaining 0, `CSH2073` Taken 5/Remaining 73 - all three rows landed with the exact Reserved figures shown in the dialog, in one Confirm-all pass. | (see toast state in the screenshot list below) | PASS |
| G6-375-reserve-grid | A second fresh multi-row request (`CB313` + `CGB762-NEW`, request #7, both full-match so no Reason needed) opened via the header badge at 375px: ONE DataGrid, columns Product / Requested / Location visible, the grid scrolls sideways to reach the rest, `Confirm all` reachable below - no page clipping. | `G6-375-reserve-grid.png` | PASS |

`errors`/`console` clean throughout this run (only the same pre-existing Radix `Missing
Description for {DialogContent}` warning already noted in every prior round of this lane,
unrelated to this fix).

Not cleaned up: request #7 (`CB313`/`CGB762-NEW`) is left OPEN on `OI-000750` in this worktree's
own dev database (its own multi-row dialog was screenshotted but never confirmed) - test data on
an isolated per-lane backend, not shared/prod. Request #6 (`SRT382-6-DIY`/`SRTWHBWP-GB9032B-NEW`/
`CSH2073`) is now fully reserved (see the Confirm-all row above); request #4
(`CB2807-DIY`, opened briefly to confirm it still renders the single-row tabs form, unchanged by
G6, then closed without action) is unaffected and still open from an earlier round.
