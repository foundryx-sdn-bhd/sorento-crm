# Evidence round 2: board Simulate confirm

Head: af1fb56b1 (branch crew/simulate-confirm, includes #1580). agent-browser 0.27.0, sessions `simr2` / `simr2p`,
backend `SORENTO_ENV_FILE=.env.ci-tests uvicorn app.main:app --port 8000`, FE `npm run dev` (HMR, no build), db sorento_test.

| # | Item | Result |
|---|------|--------|
| 1 | Admin results, whole quantities, "Would confirm: line ..." | PASS: "pool 50 SIM-BRW-HXGE -> SIM-OWN-HXGD" (no 50.0000), "Would confirm: line 10 ZZT-HXGC · line 20 ZZT-HXGC" |
| 2 | Stock-short Reserve (pool 30, reserve 50) | PASS: "would confirm as revision 1" + "Line 10, ZZT-EWXC: Reserve 50 at SIM-BRW-HXGE confirmed as decided; only 30 free at SIM-BRW-HXGE when confirmed, 20 short". Counts unchanged |
| 3 | Cancel SO wording | PASS: "ZZT-CORE-EWXI: would transfer 1 line to Cancel SO" and "Would transfer to Cancel SO: line 10 ZZT-EWXH" (saved draft verdict `cancelled` via PUT .../lines/{key}/draft) |
| 4 | Error-line format (`line N ITEM: error`) | NOT RUN: no realistic browser route to an order failing without naming a line, and app code may not be edited. Covered by pytest `test_the_probe_names_only_the_line_that_fails_alone` / test_r5 |
| 5 | Planner | PASS: gear menu shows only "Undo all", "Back to sales orders"; direct POST with planner token: 403 `simulate_admin_only` |
| 6 | 375px stock-short block | PASS: block rect 16..344 of 375, no descendant wider than its box, document scrollWidth 360 (no sideways scroll) |

## Commands / observations
- Seed: scratchpad `seed2.py` (two more project SOs, one product each, pool 30 and 1000), drafts saved with PUT
  `/api/v1/project-sales/fulfilment-planning/lines/{key}/draft`: amended reserve 50 from the 30-unit pool; `cancelled` with reason.
- Walk: sign in, sidebar Supply Chain > Project Demand > Fulfilment Planning, select all rows, Plan together, Board actions >
  Simulate confirm. POST /simulate-confirm 200. `errors` empty.
- Counts (decisions / drafts / inquiry_rows / transfers / audit_logs), measured idle around a press: 0/4/0/0/58 before and after,
  audit rows identical by entity and action. Reload-free: Confirm count stayed "Confirm (4)".
- My first stock-short draft omitted `warehouse_id` on the reserve entry; the press answered 422 (FE shows no results block).
  Seed error, fixed by re-saving the draft with warehouse_id. Worth a thought: the draft endpoint accepts a reserve entry the
  confirm body then rejects.
- Minor: a cancel-only order prints an empty "Would write" heading above its "Would transfer to Cancel SO" line.

Screenshots: admin-results-1280-r2.png, admin-results-375-r2.png, planner-menu-1280-r2.png
