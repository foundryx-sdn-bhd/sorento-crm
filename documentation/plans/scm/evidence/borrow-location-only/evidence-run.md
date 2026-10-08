# BORROW-LOCATION-ONLY: agent-browser evidence run (1280 and 375)

Head under test: aab68212f (includes fix round 1, BR1 and BR2). Rerun of the first pass, which was
on 891e1e716. Sandbox only: DB `sorento_browse`, backend :8000, `npm run dev` :3000, headless
Chromium 153 through `agent-browser@0.27.0` (own session, closed at the end). All names are fake
(`ZZT-*` fixtures, "Browse Admin"). Every UI navigation started at `/` and went through the sidebar
(Supply Chain > Project Demand > Fulfilment Planning, Procurement > Supply Chain > Order Inquiries,
Users & Access > Settings).

## Seed (scratch scripts, not committed)

Fresh askers per scenario, from the red tests' `_borrow_world` / `_own_bin_world` chains, committed
for real. Worlds E, F, G, H at 1280; I, J at 375.

| World | Asker core SO | Shape |
| --- | --- | --- |
| E | ZZT-SO-ASKC1C57 | no own-group stock; a decided later order holds 30 at a MWH bin (step 2), switch OFF |
| F | ZZT-SO-ASK22E3C | own bin holds 30 pinned by a later decided order at the SAME bin, OFF |
| G | ZZT-SO-ASKEDE06 | need 6; two later-order lines of 4 + 4 at one bin, OFF (S6) |
| H | ZZT-SO-ASK72F94 | like E, switch ON |
| I, J | ZZT-SO-ASKC2F15, ZZT-SO-ASK17A94 | like E; I for the OFF pass at 375, J for the ON pass at 375 |

Fresh-DB fixups (not product defects): company_id stamped on rows inserted with no company scope; SO
numbers upper-cased (the board route matches upper-case); `projects.sales_orders.so_id` linked to the
core SO; two columns added for newly merged main migrations (`report_ask_rate_*`).

## Results

| Step | Result | Evidence |
| --- | --- | --- |
| 1. Settings > General shows "Borrow from another order", OFF; toggle on, save, persists; toggle off, save | PASS at 1280 and 375. Saved ON (DB `t`), the page showed it ON after a fresh sidebar visit, saved OFF (DB `f`). | 01, 02, 15, 16 |
| 2a. OFF: strip and row read "Borrow other location" / "Borrow (other) 30 (ZZTMWH-IB730)", not "Borrow (order)" | PASS (E, F, I, G) | 03, 10, 12 |
| 2b. OFF reason text | PASS: `Borrow 30 on hand at ZZTMWH-IB730; ordered back to ZZTMWH-IB730 on this line.` No donor SO, no "debt lands in". | 05 |
| 2c / BR1 OFF. Decide menu has no "Borrow from another order" | PASS. Menu: As suggested, Use own location, Borrow other location, Use BRW, Buy, Transfer to Cancel SO. Same at 375. | 04, 13 |
| BR1 ON. Decide menu offers "Borrow from another order" | PASS at 1280 (H) and 375 (J). Seven items, the new one between Use own location and Borrow other location. The donor-order picker opens. | 18, 24 |
| 2d / BR2. Add a borrow lists each bin once, no project code tag, no "An unnamed sales order" | PASS. One row `ZZTMWH-IB730` (was two rows, the second tagged `PRJ-000001`). Dialog text has no `PRJ-` and no "unnamed sales order". Also usable at 375. | 06, 14 |
| 3. Confirm A (E); OI shows the asker's line, SO Qty, date, location; remark; donor untouched | PASS. confirm-all 200. OI-2610-0005: line No. 1, SO Qty 30, delivery 07/11/2026 (the asker's), location ZZTMWH-IB730, ORDER BACK, To Buy. DB: `Order-back: ZZTMWH-IB730 lent 30 to ZZT-PSO-AHUKG line 1` on the asker's line, 2026-11-07. Donor decision still `active`. The remark text is not shown on the OI detail Lines tab (Observation 3). | 08, 09 |
| 4. Own-location borrow (F) | PASS. Strip `Borrow (other) 30 (ZZTBRW-BB76a0)` (the asker's own bin). Confirm: one ORDER_BACK on the asker's line at the own bin, note `Order-back: ZZTBRW-BB76a0 lent 30 to ZZT-PSO-AHUKN line 1`, donor decision untouched. | 10, 11 |
| 5. Switch ON, fresh asker (H) | PASS. Strip `Borrow (order) 30`; reason `Borrow 30 on hand at ZZTMWH-IB436 from ZZT-SO-DONOR7578 line 2 (due 11 Dec 2026); its debt lands in Dec 2026.` Confirm: ORDER_BACK on the DONOR's line 2 at the donor date 2026-12-11, note names the donor, donor decision `superseded` ("Borrowed by SO... line 1"). | 17, 19, 20 |
| S2/B1. OFF refusal names only a location | PASS, reached through the API (no screen path): a confirm for J with qty 500 and a stale payload carrying `donor_so_number: ZZT-STALE` returned 422 `supply_lines_failed`: "The components add up to 500 and the line is open for 30." and "Later-order stock at ZZTMWH-IB768 now holds 30, and 500 was asked for." Only the location is named, the stale SO is not echoed, nothing written. | none |
| S6. OFF, Decide "Borrow other location" covers need 6 from lines 4 + 4 at one bin | **FAIL in the Decide dialog**, PASS through the proposal. See Defect A. | 21, 22, 23 |
| 6. No stuck UI, no UUIDs on screen, nothing clipped at 375 | PASS with notes. `errors` empty, no console errors. No UUID rendered in the UI (the OI detail URL carries an id, as before). At 375: `scrollWidth` 360 for 375 on every page checked; strip tiles truncate with an ellipsis; tables scroll in their own container; the Decide menu sits flush to the left edge. | 12 to 16, 24 |

## Defects and observations

**A. S6 fails in the Decide dialog (OFF).** Repro: switch OFF, board for ZZT-SO-ASKEDE06 (need 6, two
later-order lines of 4 and 4 at ZZTMWH-IB3f2), tick the line, Decide, Borrow other location, pick
ZZTMWH-IB3f2, type a reason. The dialog says "1 line will be skipped: only 4 free at ZZTMWH-IB3f2" and
Save stays at "Save 0 lines" (screenshot 21). The proposal itself is right: saving "As suggested"
shows two borrow rows 4 + 2 (screenshot 22), Confirm succeeds, and one ORDER_BACK of 6 is written at
the bin on the asker's line (screenshot 23). Likely cause, from the board payload: before the confirm
the line's `borrow_candidates` were three rows at that bin (free 4, 4, 8) and all three had
`donor_core_line_id` null. `decideComposition` keys its running claim on
`donor_core_line_id ?? warehouse_id`, so the three rows share one key, the first draw of 4 uses it up,
and the second row reads 0 left. The unit tests give the rows distinct donor ids, so they do not see it.

**B. Remark not visible on the OI detail Lines tab.** The `Order-back: ...` note is in the database but
is not rendered on the OI detail Lines tab, and the instruction badge has no tooltip. Not checked on
the OI worklist "Lines" view.

**C. Expanding the bin row in Add a borrow lists the SO document at that bin (OFF).** The ledger shows
`S/O ZZT-SO-DONOR85ED, qty 30` (screenshot 07). This is the location's own stock ledger, so it may be
by design; the project code tag and the duplicate row from the first pass are gone (BR2).

**D. Add a borrow quantity at 375.** In the 375 run the dialog opened with quantity 60 for a 30 unit
line ("After borrowing 60"), after I had pressed "Add a borrow" twice (the first press showed no dialog
to my script). Not reproduced on purpose; flagged in case the default quantity does not subtract a
borrow row already added to the line.

**E. Process notes.** Chromium crashed three times while navigating and the login was lost each time
(re-login done). Playbook asks for at most two committed screenshots per lane; the brief asked for the
folder, so 24 are committed, each under 200 KB.

## Network and console

`POST /api/v1/user-management/settings/general` 200; `GET /api/v1/project-sales/fulfilment-planning/board`
200; `POST /api/v1/project-sales/fulfilment-planning/confirm-all` 200. `errors` and `console` clean.
