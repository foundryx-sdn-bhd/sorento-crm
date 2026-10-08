# BORROW-LOCATION-ONLY: agent-browser evidence run (1280 and 375)

Head under test: 891e1e716. Sandbox only: DB `sorento_browse`, backend :8000, `npm run dev` :3000,
headless Chromium 153 driven through `agent-browser@0.27.0` (own session, closed at the end). All
names are fake (`ZZT-*` fixtures, "Browse Admin"). Every navigation started at `/` and went through
the sidebar (Supply Chain > Project Demand > Fulfilment Planning, Procurement > Order Inquiries,
Users & Access > Settings). The only in-app link followed outside the sidebar was the board's own
"Order Inquiries" footer link (not used in the end; see step 3).

## Seed (scratch scripts, not committed)

Reused `_borrow_world` and `_own_bin_world` chains from the red tests, committed for real.

| World | Asker core SO | Shape |
| --- | --- | --- |
| A | ZZT-SO-ASKB3DC7 | no stock in own group; a decided later donor order holds 30 on hand at a MWH bin (step 2) |
| B | ZZT-SO-ASKD0B33 | own bin holds 30 pinned by a later decided order at the SAME bin |
| C | ZZT-SO-ASKD607E | like A, fresh, for the 375 pass |
| D | ZZT-SO-ASKD2B16 | like A, fresh, for the switch-ON pass |

Fresh-DB fixups needed so the board can see the data (not product defects): company_id stamped on
rows inserted with no company scope, SO numbers upper-cased (the board route matches upper-case),
`projects.sales_orders.so_id` linked to the core SO, system settings row seeded with a name and
support email, all modules installed for the default tenant.

## Results

| Step | Result | Evidence |
| --- | --- | --- |
| 1. Settings > General shows "Borrow from another order", OFF; toggle on, save, persists; toggle off, save | PASS. `POST /api/v1/user-management/settings/general` 200 each time; DB column `t` then `f`. Also at 375 (on, saved, `t`). | 01, 02, 15, 16 |
| 2a. Strip reads "Borrow other location" / row reads "Borrow (other) 30 (ZZTMWH-IBc13)", not "Borrow (order)" | PASS (A, B, C). "Borrow from another order" tile shows Suggested 0. | 03, 10, 12 |
| 2b. Reason text | PASS. Exact text: `Borrow 30 on hand at ZZTMWH-IBc13; ordered back to ZZTMWH-IBc13 on this line.` No donor SO, no "debt lands in". | 05 |
| 2c. Decide menu has no "Borrow from another order" | PASS. Items: As suggested, Use own location, Borrow other location, Use BRW, Buy, Transfer to Cancel SO. Same at 375. | 04, 13 |
| 2d. Add a borrow dialog shows no "SO... line N" under any location | PARTIAL, see Observations 1 and 2. No "SO... line N" appears and no donor SO in the reason or labels; but the location list has a duplicate row tagged with a project code, and expanding a location row lists the SO document that holds stock there. | 06, 07, 14 |
| 3. Confirm A; OI shows the asker's line, SO Qty, date, location; remark; donor untouched | PASS. confirm-all 200. OI-2610-0001 line No. 1, SO Qty 30, delivery 07/11/2026 (asker's), location ZZTMWH-IBc13, ORDER BACK, To Buy. DB row: `Order-back: ZZTMWH-IBc13 lent 30 to ZZT-PSO-ALXKG line 1`, on the asker line, 2026-11-07. Donor decision still `active`, no supersede. The remark text is not shown on the OI detail Lines tab (Observation 3), verified in the DB. | 08, 09 |
| 4. Same for own-location borrow (B) | PASS. Strip `Borrow (other) 30 (ZZTBRW-BB4995)` (the asker's own bin). Confirm: OI-2610-0002, row on the asker line at the own bin, note `Order-back: ZZTBRW-BB4995 lent 30 to ZZT-PSO-AMCZG line 1`, donor decision `active`. | 10, 11 |
| 5. Switch ON, fresh asker D | PASS. Strip `Borrow (order) 30 (ZZTMWH-IB4a6)`; reason `Borrow 30 on hand at ZZTMWH-IB4a6 from ZZT-SO-DONORAD72 line 2 (due 11 Dec 2026); its debt lands in Dec 2026.` Confirm: ORDER_BACK on the DONOR line 2 at the donor date 2026-12-11, note names the donor, donor decision `superseded` ("Borrowed by SO... line 1"). See Observation 4 about the Decide menu. | 17, 19, 20 |
| 6. No stuck UI, no UUIDs on screen, nothing clipped at 375 | PASS with notes. No page errors (`errors` empty) and no console errors. No UUID is rendered in the UI (OI detail URL carries an id, as before). 375: `scrollWidth` 360 to 375 on every page checked, no horizontal page overflow; strip tiles truncate with an ellipsis (title attribute carries the full word); tables scroll inside their own container; the Decide menu sits flush against the left edge of the viewport (13). | 12 to 16 |

## Observations and defects

1. **Add a borrow lists the borrowed location twice (OFF).** Two rows for `ZZTMWH-IBc13`, the second
   tagged `PRJ-000001` (the donor's project code), "Where: Group" on both. Repro: switch OFF, board
   for ZZT-SO-ASKB3DC7, Change decision, Add a borrow (06). The donor SO is not named, but the donor's
   project is still exposed and the second row is a duplicate location as far as a planner can tell.
   Likely `donor_project_ref` is not blanked with the other donor fields.
2. **Expanding a location row in Add a borrow lists the SO document at that bin (OFF).** The ledger
   under the second row shows `S/O ZZT-SO-DONORA483, qty 30` (07). This is the location's own stock
   ledger, not a donor claim, so it may be by design; flagged because the brief says no donor SO
   should be named anywhere.
3. **Remark not visible on the OI detail Lines tab.** The `Order-back: ... lent 30 to ... line 1`
   note is in the database but I could not find it rendered on OI-2610-0001 (no tooltip on the
   instruction badge either). Not checked on the OI worklist "Lines" view.
4. **Decide menu never offers "Borrow from another order", even with the switch ON.** With ON the
   proposal names the donor (19), but the board payload's `borrow_candidates` for that line carry no
   `donor_so_number` (`rung` null, two location candidates), and the new menu rule keys on those. So
   under ON a planner cannot pick "Borrow from another order" from Decide. If the board never carried
   donor-named candidates, the old always-on item opened an empty picker, so this may only expose an
   existing gap. Worth a decision before ON is used.
5. **Process notes.** The seeded plan board for a project registered by another user returned no
   lines until the fixups above (fresh-DB artefact). A first Confirm failed with a raw SQL unique
   violation (`uq_projects_so_line_core_line`) shown verbatim in a toast, because the seeded project SO
   was not linked to its core SO; after linking, Confirm succeeded. The raw SQL toast is a UX leak
   but unrelated to this lane.

## Network and console

`POST /api/v1/user-management/settings/general` 200; `GET /api/v1/project-sales/fulfilment-planning/board`
200; `POST /api/v1/project-sales/fulfilment-planning/confirm-all` 200. `errors` and `console` clean
after each interaction (one-off Chromium restart lost the login once; re-login done).
